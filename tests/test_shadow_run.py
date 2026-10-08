"""The shadow-run entrypoint: a watchable full-loop rehearsal that cannot spend.

`core_brain.shadow_run` reuses `core_brain.trader_loop.run` through the same
`VenueSeam` the live loop uses. It changes three things and nothing else: the
client cannot sign, the store is not the production registry, and the run stops
on a wall-clock deadline.

The tests that matter here are the ones about what shadow mode must NOT do.
"""
from __future__ import annotations

import pytest

from core_brain.trader_loop import VenueSeam, run
from core_brain.quotes import QuoteIntent


class FakeMarket:
    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False

    def t_remaining(self, now=None):
        return 14400.0


def _books(clob_host, token):
    return {"token_id": token, "best_bid": 0.47, "best_ask": 0.49,
            "bids": {0.47: 100}, "asks": {0.49: 100}}


def _load_cfg():
    from core_brain.config import load
    return load()


def _filled_by_token(registry, condition_id: str) -> dict:
    """Shares actually bought per token, straight off the fills ledger.

    Inventory is the wrong instrument for "did this pair get completed":
    inventory is fills MINUS closes, and a shadow rotation merges the pair it
    completes, so a completed-and-merged pair reads flat there. The fills
    ledger only ever records what was bought.
    """
    out: dict[str, float] = {}
    by_local = {o["id"]: o for o in registry.get_all_orders()}
    for f in registry.get_all_fills():
        row = by_local.get(f.get("order_uuid"))
        if row is None or row["condition_id"] != condition_id:
            continue
        tok = row["token_id"]
        out[tok] = out.get(tok, 0.0) + float(f.get("size") or 0.0)
    return out


def _seam(**overrides):
    base = dict(
        client=object(),
        fetch_market=lambda cid: FakeMarket(cid),
        fetch_books=_books,
        decide=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
        submit_fn=lambda *a, **k: 0,
        cancel_fn=lambda *a, **k: 0,
        reconcile_fn=lambda *a, **k: None,
        sweep_fn=lambda: None,
    )
    base.update(overrides)
    return VenueSeam(**base)


class TestDeadline:
    """The time box.

    `trader_loop.run` has no deadline and must not grow one -- editing the live
    loop to serve a rehearsal is how a rehearsal feature reaches live. The stop
    is driven entirely from the injected `sleep_fn`.
    """

    def test_the_loop_stops_once_the_deadline_passes(self):
        from core_brain.shadow_run import make_deadline_sleep

        now = [1000.0]
        slept = []

        def clock():
            return now[0]

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        sleep_fn = make_deadline_sleep(
            deadline_ts=1000.0 + 12.0, clock=clock, sleep=sleep)

        results = run(
            _seam(), interval=5.0, once=False, live=False,
            markets=[FakeMarket("0xabc")], sleep_fn=sleep_fn,
        )

        # 5 + 5 sleeps land at t=1010; the third check is past t=1012 only after
        # the clamped final sleep, so the loop stops without overshooting.
        assert sum(slept) <= 12.0, f"slept past the deadline: {slept}"
        assert results, "the loop must return the last rotation, not nothing"

    def test_the_deadline_returns_results_rather_than_escaping_as_an_error(self):
        """Why `_Deadline` subclasses KeyboardInterrupt.

        `trader_loop.py` wraps the sleep call in `except KeyboardInterrupt:
        break` and nothing else. A plain Exception would propagate out of `run`
        uncaught and lose the rotation's results; a bare BaseException subclass
        would not be caught by that handler at all and would escape the same
        way. Subclassing KeyboardInterrupt walks through the loop's own
        designed clean exit.
        """
        from core_brain.shadow_run import make_deadline_sleep

        sleep_fn = make_deadline_sleep(
            deadline_ts=0.0, clock=lambda: 1.0, sleep=lambda s: None)

        results = run(
            _seam(), interval=5.0, once=False, live=False,
            markets=[FakeMarket("0xabc")], sleep_fn=sleep_fn,
        )

        # Returned, not raised: the rotation's results survive the deadline.
        # DECLINED because the fake `decide` returns no intents -- what matters
        # here is that `run` handed results back at all.
        assert [r.status for r in results] == ["DECLINED"]

    def test_the_deadline_signal_survives_a_blanket_except_exception(self):
        from core_brain.shadow_run import _Deadline

        with pytest.raises(_Deadline):
            try:
                raise _Deadline()
            except Exception:  # noqa: BLE001 - the point of the test
                pytest.fail("a blanket except Exception swallowed the deadline")

    def test_a_sleep_before_the_deadline_is_clamped_to_the_remaining_time(self):
        """A 5s interval with 2s left must not overshoot the time box by 3s."""
        from core_brain.shadow_run import make_deadline_sleep

        slept = []
        sleep_fn = make_deadline_sleep(
            deadline_ts=102.0, clock=lambda: 100.0, sleep=slept.append)

        sleep_fn(5.0)

        assert slept == [2.0]


class TestRunShadow:
    """The session: read-only client, own store, live config, nothing sent."""

    def _markets(self, n=1):
        return lambda max_markets=None: [FakeMarket(f"0x{i}") for i in range(n)]

    def test_the_production_registry_is_refused_as_the_store(self, tmp_path):
        """AGENTS.md: `data/orders.db` is read, never rewritten.

        A shadow run fabricates fills. Those rows in the real registry would be
        corruption that nothing downstream flags.
        """
        from core_brain.order_registry import DEFAULT_DB_PATH
        from core_brain.shadow_guard import ShadowSafetyViolation
        from core_brain.shadow_run import run_shadow

        with pytest.raises(ShadowSafetyViolation, match="production registry"):
            run_shadow(
                minutes=0.0, db_path=DEFAULT_DB_PATH,
                markets_fn=self._markets(), client_fn=lambda: object(),
            )

    def test_paired_run_audits_selected_markets_it_never_visited(
            self, tmp_path, monkeypatch):
        """A selection the visit cap or an error kept unvisited must still show
        up in the paired audit: an unvisited market cannot be allowed to vanish
        from the coverage the report reads."""
        import json
        import sqlite3

        from core_brain.paired_shadow import ensure_paired_shadow_tables
        from core_brain.shadow_run import run_shadow

        db = tmp_path / "paired.db"
        ensure_paired_shadow_tables(db)

        # A minimal valid paired bundle: the run's paired gate reads its feed
        # from --markets-path and refuses a paired run without one.
        feed = tmp_path / "paired_markets.json"
        feed.write_text(json.dumps({
            "format": "spread_hunter.paired-depth.v1",
            "snapshot_id": "snapshot-audit",
            "control_depth_usd": 500,
            "treatment_depth_usd": 250,
            "control": [],
            "treatment": [{
                "cid": "0xfeed-row",
                "paired_depth_arm": "treatment",
                "paired_depth_cutoff_usd": 250,
                "paired_depth_snapshot_id": "snapshot-audit",
                "event_id": "event-feed",
            }],
        }), encoding="utf-8")

        # The market that IS visited fails resolution (no tokens), so no
        # admission row is written for it either; the other market in the
        # feed is never reached. Both must appear in the audit event.
        class BrokenMarket:
            condition_id = "0xvisited"
            up_token = ""
            down_token = ""
            market_slug = "broken"
            tick_size = 0.01
            neg_risk = False

            def t_remaining(self, now=None):
                return 14400.0

        markets = [BrokenMarket(), FakeMarket("0xnever-visited")]
        monkeypatch.setattr(
            "core_brain.trader_loop._fetch_market",
            lambda cid: (_ for _ in ()).throw(LookupError("no tradeable market")))

        run_shadow(
            minutes=0.01, db_path=db,
            markets_fn=lambda max_markets=None: markets,
            markets_path=str(feed),
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
            fetch_books=_books,
            paired_depth_arm="treatment",
            paired_depth_cutoff_usd=250.0,
            starting_bankroll_usd=100.0,
        )

        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT detail FROM shadow_paired_feed_events "
                "WHERE kind = 'unvisited_selected_markets'").fetchall()
        assert rows, "unvisited selected markets were not audited"
        detail = rows[0][0]
        assert "0xnever-visited" in detail

    def test_decided_intents_are_recorded_and_never_submitted(self, tmp_path):
        """The submission boundary. The loop decides; nothing leaves the process."""
        from core_brain.shadow_run import run_shadow

        intent = QuoteIntent(side="UP", token_id="tok-up", price=0.48, size=2,
                             mid=0.49, edge_vs_mid=0.01)

        result = run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(),
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([intent], ""),
        )

        assert len(result.intents) == 1
        recorded = result.intents[0]
        assert recorded.side == "UP"
        assert recorded.price == 0.48
        assert recorded.size == 2
        assert recorded.condition_id == "0x0"

    def test_the_client_is_the_denying_proxy_by_default(self, tmp_path,
                                                         monkeypatch):
        """No injected client means the real one -- which cannot sign."""
        import core_brain.shadow_guard as sg
        from core_brain.shadow_guard import ReadOnlyVenue
        from core_brain.shadow_run import build_shadow_seam

        monkeypatch.setattr(
            sg, "_build_unauthenticated_client", lambda host: object())

        seam = build_shadow_seam(
            db_path=tmp_path / "shadow.db",
            intents_sink=[],
        )

        assert isinstance(seam.client, ReadOnlyVenue)

    def test_an_injected_raw_client_is_wrapped_in_the_denying_proxy(
            self, tmp_path):
        """client_fn is an injection seam, not a way to hand shadow mode a
        raw signer. Whatever arrives unwrapped leaves through the proxy, so a
        future wiring change cannot put a signing-capable object on the seam.
        """
        from core_brain.shadow_guard import ReadOnlyVenue, ShadowSafetyViolation
        from core_brain.shadow_run import build_shadow_seam

        class RawClient:
            def post_order(self, *a, **k):
                return {"orderID": "should-never-happen"}

        seam = build_shadow_seam(
            db_path=tmp_path / "shadow.db",
            client_fn=lambda: RawClient(),
            intents_sink=[],
        )

        assert isinstance(seam.client, ReadOnlyVenue)
        with pytest.raises(ShadowSafetyViolation, match="post_order"):
            seam.client.post_order({"price": 0.48})

    def test_live_caps_are_used_unchanged(self, tmp_path):
        """Same config as live, or the rehearsal rehearses something we do not ship."""
        from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD
        from core_brain.shadow_run import run_shadow

        seen = {}

        def decide(cfg, up, dn, inv, t_rem, wf):
            seen["max_order_usd"] = cfg.max_order_usd
            seen["max_total_usd"] = cfg.max_total_usd
            return [], "declined"

        run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(), client_fn=lambda: object(),
            decide_fn=decide,
        )

        assert seen["max_order_usd"] == MAX_ORDER_USD
        assert seen["max_total_usd"] == MAX_TOTAL_USD

    def test_reconcile_is_skipped_not_silently_passed(self, tmp_path):
        """Reconcile compares against venue positions a shadow run does not
        have, so it stays a no-op and the result must say so rather than
        reporting it as having run clean.

        `sweep` is deliberately NOT asserted here any more: `run_shadow` now
        wires `shadow_sweep`, which runs the single-buy pairs pass against
        the shadow store every rotation (see `TestPairsSweep` below).
        Reporting a stage that ran as "skipped" would be the same lie in the
        other direction.
        """
        from core_brain.shadow_run import run_shadow

        result = run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(), client_fn=lambda: object(),
        )

        assert result.skipped_stages == ("reconcile",)


class TestPairsSweep:
    """`shadow_sweep`: the single-buy pairs pass, wired to run once per
    rotation via `sweep_fn` -- the same seam port `trader_loop.run` already
    calls every cycle, previously a no-op in shadow mode.
    """

    def _seed_single_buy(self, db_path) -> None:
        """A naked pair in the shadow store: tok-up filled 20 at 0.47,
        tok-dn still resting -- the exact fixture the unit test in
        `tests/test_shadow_exec.py` uses, seeded here directly against the
        store rather than through a market visit.
        """
        from core_brain.order_registry import OrderRegistry, init_db
        from core_brain.quotes import QuoteIntent
        from core_brain.shadow_exec import (
            ensure_shadow_tables, record_submit, settle_market,
        )
        from core_brain.config import load

        init_db(db_path)
        reg = OrderRegistry(db_path=db_path)
        ensure_shadow_tables(db_path)
        intents = [
            QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                       mid=0.5, edge_vs_mid=0.0),
            QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=20,
                       mid=0.5, edge_vs_mid=0.0),
        ]
        record_submit(object(), reg, FakeMarket("0xabc"), intents, load(),
                      db_path=db_path, book_fn=lambda h, t: {"bids": {}})
        settle_market(reg, FakeMarket("0xabc"), db_path=db_path, seen=set(),
                      traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    @staticmethod
    def _canonical_book(_clob_host, token_id):
        """`markets.parse_book`'s exact shape: bids/asks as PRICE-KEYED
        DICTS. This is what `fetch_books` actually returns in production
        (`_default_fetch_books` -> `markets.full_book` -> `markets.parse_book`)
        -- never the list-of-levels shape `single_buy_saver._book_levels`
        wants. `ShadowExecutionClient.get_order_book` is responsible for that
        adaptation; a test fixture that hands over the already-adapted shape
        would not exercise it.
        """
        return {"token_id": token_id, "bids": {0.50: 500.0},
                "asks": {0.51: 500.0}, "best_bid": 0.50, "best_ask": 0.51,
                "malformed": 0}

    def test_a_naked_pair_waits_for_its_resting_hedge(self, tmp_path):
        """The shadow sweep records PATIENT_WAIT without taker completion."""
        from core_brain.order_registry import OrderRegistry, inventory_from_registry
        from core_brain.shadow_run import run_shadow

        db = tmp_path / "shadow.db"
        self._seed_single_buy(db)

        run_shadow(
            minutes=0.0, db_path=db,
            markets_fn=lambda max_markets=None: [],
            client_fn=lambda: object(),
            fetch_books=self._canonical_book,
        )

        registry = OrderRegistry(db_path=db)
        assert _filled_by_token(registry, "0xabc") == {
            "tok-up": pytest.approx(20.0)}
        pair_ids = {order["pair_id"] for order in registry.get_all_orders()}
        assert len(pair_ids) == 1
        state = registry.get_lifecycle_state(pair_ids.pop())
        assert state is not None
        assert state.state == "PATIENT_WAIT"

        inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
        assert inv.up_shares == pytest.approx(20.0)
        assert inv.down_shares == pytest.approx(0.0)

    def test_the_sweep_does_not_log_an_ordinary_wait_as_a_completion(
            self, tmp_path, caplog):
        """A patient wait is quiet and cannot be mistaken for a completed pair."""
        import logging

        from core_brain.shadow_run import run_shadow

        db = tmp_path / "shadow.db"
        self._seed_single_buy(db)

        with caplog.at_level(logging.INFO, logger="shadow_run"):
            run_shadow(
                minutes=0.0, db_path=db,
                markets_fn=lambda max_markets=None: [],
                client_fn=lambda: object(),
                fetch_books=self._canonical_book,
            )

        assert not any("completed" in r.message for r in caplog.records)

    def test_one_fetch_books_serves_quoting_and_the_lifecycle_sweep(
            self, tmp_path):
        """The regression pin for the get_order_book book-shape bug.

        ONE canonical `fetch_books` -- `markets.parse_book`'s exact shape --
        drives BOTH consumers in a SINGLE run: `queue_ahead_at`, which reads
        the canonical price-keyed dict as the quoting path rests a fresh
        order, and `single_buy_saver._book_levels`, which reads a list of
        levels, as the lifecycle sweep observes a naked pair from the same
        book source. The other two tests in this class isolate the
        sweep with an empty market list -- rigorous for what they check, but
        that isolation is also what would hide a shape conflict between the
        two consumers, which is exactly what broke before this fix (see
        Critical 1/2 in the task-5 review). Both must work off one
        `fetch_books` in one run for this to mean anything.
        """
        from core_brain.order_registry import OrderRegistry, inventory_from_registry
        from core_brain.quotes import QuoteIntent
        from core_brain.shadow_run import run_shadow

        db = tmp_path / "shadow.db"
        self._seed_single_buy(db)

        intent = QuoteIntent(side="UP", token_id="tok-up", price=0.48,
                             size=2, mid=0.5, edge_vs_mid=0.02)

        run_shadow(
            minutes=0.0, db_path=db,
            markets_fn=lambda max_markets=None: [FakeMarket("0x0")],
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([intent], ""),
            fetch_books=self._canonical_book,
        )

        # queue_ahead_at consumed the canonical dict without error: the
        # freshly-quoted market's intent rests.
        reg = OrderRegistry(db_path=db)
        fresh = [o for o in reg.get_active_orders()
                if o.condition_id == "0x0" and o.token_id == "tok-up"]
        assert fresh, "the quoting path never rested its intent"

        # ShadowExecutionClient.get_order_book adapted the same canonical
        # dict into levels single_buy_saver._book_levels can read: the
        # lifecycle sweep observed the pre-seeded naked pair on 0xabc without
        # crossing the book.
        assert _filled_by_token(reg, "0xabc") == {
            "tok-up": pytest.approx(20.0)}
        pair_ids = {o["pair_id"] for o in reg.get_all_orders()
                    if o["condition_id"] == "0xabc"}
        assert len(pair_ids) == 1
        assert reg.get_lifecycle_state(pair_ids.pop()).state == "PATIENT_WAIT"
        inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
        assert inv.up_shares == pytest.approx(20.0)
        assert inv.down_shares == pytest.approx(0.0)


class TestSettleWiring:
    """Rest, then fill, then decide -- in that order, inside one visit."""

    def test_the_seam_settles_before_it_reports_inventory(self, tmp_path):
        from core_brain.shadow_run import build_shadow_seam

        seen_markets = []
        seam = build_shadow_seam(
            db_path=tmp_path / "shadow.db",
            client_fn=lambda: object(),
            traded_fn=lambda cid, seen: seen_markets.append(cid) or {},
        )
        seam.inventory_fn(FakeMarket("0xabc"))

        assert seen_markets == ["0xabc"]

    def test_submitted_intents_become_rows_in_the_shadow_store(self, tmp_path):
        from core_brain.order_registry import OrderRegistry
        from core_brain.shadow_run import build_shadow_seam

        db = tmp_path / "shadow.db"
        seam = build_shadow_seam(
            db_path=db, client_fn=lambda: object(),
            fetch_books=lambda h, t: {"bids": {0.47: 10.0}},
        )
        placed = seam.submit_fn(
            seam.client, seam.registry, FakeMarket("0xabc"),
            [QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                        mid=0.5, edge_vs_mid=0.0)],
            seam.base_cfg,
        )

        assert placed == 1
        assert len(OrderRegistry(db_path=db).get_active_orders()) == 1

    def test_the_production_registry_is_still_refused(self):
        """The new writers must not have moved the guard off the front door."""
        from core_brain.order_registry import DEFAULT_DB_PATH
        from core_brain.shadow_guard import ShadowSafetyViolation
        from core_brain.shadow_run import build_shadow_seam

        with pytest.raises(ShadowSafetyViolation):
            build_shadow_seam(db_path=DEFAULT_DB_PATH)


class TestLookupFetchMarket:
    def test_lookup_falls_back_to_fetch_market_when_tokens_missing(self, monkeypatch):
        from core_brain.shadow_run import _lookup_fetch_market

        fetched = []
        def mock_fetch_market(cid):
            fetched.append(cid)
            return FakeMarket(cid)

        monkeypatch.setattr("core_brain.trader_loop._fetch_market", mock_fetch_market)

        # Incomplete market spec lacking up_token / down_token
        spec_dict = {"cid": "0x123", "title": "Incomplete"}
        resolver = _lookup_fetch_market([spec_dict])

        res = resolver("0x123")
        assert res.condition_id == "0x123"
        assert res.up_token == "tok-up"
        assert fetched == ["0x123"]

    def test_lookup_uses_cached_market_when_tokens_present(self, monkeypatch):
        from core_brain.shadow_run import _lookup_fetch_market

        fetched = []
        monkeypatch.setattr("core_brain.trader_loop._fetch_market", lambda cid: fetched.append(cid))

        complete_market = FakeMarket("0x456")
        resolver = _lookup_fetch_market([complete_market])

        res = resolver("0x456")
        assert res == complete_market
        assert fetched == []

    def test_lookup_resolves_refreshed_markets_dynamically(self, monkeypatch):
        from core_brain.shadow_run import _lookup_fetch_market

        fetched = []
        monkeypatch.setattr("core_brain.trader_loop._fetch_market", lambda cid: fetched.append(cid))

        current_box = [[FakeMarket("0x1")]]
        resolver = _lookup_fetch_market(lambda: current_box[0])

        assert resolver("0x1").condition_id == "0x1"

        # Dynamically refresh to cycle 2 markets
        current_box[0] = [FakeMarket("0x2")]
        assert resolver("0x2").condition_id == "0x2"
        assert fetched == []


class TestProgressLog:
    """What the operator sees while a rehearsal runs.

    The point of a shadow run is watching the decision, so the decision has to
    reach the terminal. Before this, a wired-up run printed the banner and then
    nothing for the whole time box -- the per-cycle detail existed only in the
    ring file and the store.
    """

    def _emit(self, tmp_path):
        from core_brain.shadow_run import _make_logging_emit
        return _make_logging_emit(tmp_path / "shadow.db")

    def test_a_decision_with_intents_logs_the_market_and_the_count(
            self, tmp_path, caplog):
        import logging

        emit = self._emit(tmp_path)
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            emit(7, "quoting", "decide", market_slug="dota-2026",
                 reason="pair cost 0.985", extra={"intent_count": 2})

        line = caplog.text
        assert "cycle=7" in line
        assert "dota-2026" in line
        assert "intents=2" in line
        assert "pair cost 0.985" in line

    def test_a_declined_market_logs_the_reason_it_was_skipped(
            self, tmp_path, caplog):
        import logging

        emit = self._emit(tmp_path)
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            emit(7, "quoting", "decide", market_slug="lol-2026",
                 reason="UP: 0.905 outside band 0.10-0.90",
                 extra={"intent_count": 0})

        assert "intents=0" in caplog.text
        assert "outside band" in caplog.text

    def test_a_malformed_intent_count_logs_unknown_and_does_not_raise(
            self, tmp_path, caplog):
        """Telemetry must never be what stops the loop.

        `cycle_stream.emit` protects itself from a malformed event and
        returns. This wrapper called it first and then repeated the same
        conversion outside that protection, so a decision event carrying a
        non-numeric `intent_count` raised after the telemetry had already
        been handled -- ending the rehearsal on a logging detail. The count
        is unknown, and an unknown count is worth saying out loud, not worth
        a crash.
        """
        import logging

        emit = self._emit(tmp_path)
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            emit(7, "quoting", "decide", market_slug="dota-2026",
                 reason="pair cost 0.985", extra={"intent_count": "two"})

        assert "cycle=7" in caplog.text
        assert "dota-2026" in caplog.text
        assert "intents=?" in caplog.text

    def test_phases_that_are_not_a_decision_stay_quiet(self, tmp_path, caplog):
        """One line per market visit. A rotation that logged every phase would
        bury the decisions it exists to show."""
        import logging

        emit = self._emit(tmp_path)
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            emit(7, "settling", "pairs_completed", market_slug="dota-2026")

        assert caplog.text.strip() == ""

    def test_the_ring_and_store_still_receive_every_event(self, tmp_path):
        """The log is added beside the telemetry, never instead of it."""
        from core_brain.order_registry import init_db
        from core_brain.shadow_run import _make_logging_emit

        db = tmp_path / "shadow.db"
        init_db(db)
        emit = _make_logging_emit(db)
        emit(7, "quoting", "decide", market_slug="dota-2026",
             reason="pair cost 0.985", extra={"intent_count": 2})

        import sqlite3
        con = sqlite3.connect(db)
        rows = con.execute(
            "select cycle, market_slug, intent_count from cycle_intent").fetchall()
        con.close()
        assert rows == [(7, "dota-2026", 2)]

    def test_a_discard_logs_cycle_market_status_reason_and_count(
            self, tmp_path, caplog):
        """A UMA-flagged visit names itself in one readable line (#408)."""
        import logging

        emit = self._emit(tmp_path)
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            emit(7, "quoting", "discard", market_slug="cs2-tu-xdm",
                 reason="uma_resolution_proposed",
                 extra={"condition_id": "0xuma", "uma_status": "proposed",
                        "cancelled": 2, "failed": 0})

        line = caplog.text
        assert "cycle=7" in line
        assert "cs2-tu-xdm" in line
        assert "proposed" in line
        assert "uma_resolution_proposed" in line
        assert "cancelled=2" in line


class TestUmaGateShadowBuilder:
    """Both UMA ports are set on the shadow seam (#408)."""

    def test_builder_wires_both_ports_with_record_cancel_intact(
            self, tmp_path):
        from core_brain.order_registry import init_db, OrderRegistry
        from core_brain.shadow_run import build_shadow_seam

        db = tmp_path / "shadow.db"
        init_db(db)
        registry = OrderRegistry(db_path=db, run_id="shadow-test")

        def fake_uma(cid):
            from core_brain.market_resolution import UmaResolutionStatus
            return UmaResolutionStatus(condition_id=cid)

        seen = []

        def fake_record(record):
            seen.append(record)

        seam = build_shadow_seam(
            db_path=db, registry=registry,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=_books,
            fetch_uma_status=fake_uma,
            record_market_event=fake_record,
        )
        assert seam.fetch_uma_status is fake_uma
        assert seam.record_market_event is not None
        # record_cancel stays the cancel adapter: marking still rests rows.
        assert callable(seam.cancel_fn)
        # Absent port = skip the write: a default-built seam still wires the
        # record adapter without a caller-supplied sink.
        seam2 = build_shadow_seam(
            db_path=db, registry=registry,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=_books,
        )
        assert seam2.fetch_uma_status is None
        assert callable(seam2.record_market_event)

    def test_run_shadow_entrypoint_passes_through_uma_reader_to_market_visit(
            self, tmp_path):
        from core_brain.market_resolution import UmaResolutionStatus
        from core_brain.shadow_run import ShadowResult, run_shadow

        db = tmp_path / "shadow.db"
        visited_cids = []

        def fake_uma(cid):
            visited_cids.append(cid)
            return UmaResolutionStatus(condition_id=cid)

        spec = {
            "cid": "0xuma_entry",
            "question": "Will CS2 match end?",
            "tokens": [{"token_id": "tok-up", "outcome": "Yes"},
                       {"token_id": "tok-dn", "outcome": "No"}],
            "rewards": {"rates": []},
            "accepting_orders": True,
            "closed": False,
        }

        def one_shot_sleep(_s):
            raise KeyboardInterrupt

        res = run_shadow(
            db_path=db,
            minutes=0,
            interval=0.1,
            markets_fn=lambda **kw: [spec],
            fetch_books=_books,
            fetch_uma_status=fake_uma,
            sleep_fn=one_shot_sleep,
        )
        assert "0xuma_entry" in visited_cids
        assert isinstance(res, ShadowResult)



class TestUmaFlipRehearsal:
    """Clean admit, Gamma flips to proposed, next visit cancels both rows (#408)."""

    def test_flip_to_proposed_cancels_both_rows_writes_blocked_places_nothing(
            self, tmp_path, caplog):
        import logging
        import sqlite3

        from core_brain.market_resolution import UmaResolutionStatus
        from core_brain.order_registry import init_db, OrderRegistry
        from core_brain.quotes import QuoteIntent
        from core_brain.shadow_run import build_shadow_seam
        from core_brain.trader_loop import _visit_one

        db = tmp_path / "shadow.db"
        init_db(db)
        registry = OrderRegistry(db_path=db, run_id="shadow-flip")
        mode = {"uma": "clean"}

        def fake_uma(cid):
            if mode["uma"] == "proposed":
                return UmaResolutionStatus(condition_id=cid, status="proposed")
            return UmaResolutionStatus(condition_id=cid)

        seam = build_shadow_seam(
            db_path=db, registry=registry, cfg=_load_cfg(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=_books,
            fetch_uma_status=fake_uma,
        )
        assert seam.fetch_uma_status is fake_uma

        pair = [QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=5,
                            mid=0.5, edge_vs_mid=0.0),
                QuoteIntent(side="DOWN", token_id="tok-dn", price=0.47,
                            size=5, mid=0.5, edge_vs_mid=0.0)]
        seam.decide = lambda cfg, up, dn, inv, t_rem, wf: (list(pair), "")
        res1 = _visit_one(seam, {"cid": "0xflip"}, cycle=1, live=True)
        assert res1.status in ("QUOTED", "DECLINED", "DRY_RUN") or res1.submitted >= 0
        resting = [o for o in registry.get_active_orders()
                   if o.condition_id == "0xflip" and o.status == "open"]
        assert len(resting) == 2, "both legs must rest before the flip"

        # Flip the fake Gamma reader; the feed refresh returns [] — the
        # empty-feed path keeps the previous universe, so the market is
        # still visited and the UMA gate fires regardless of feed state.
        mode["uma"] = "proposed"
        submitted = []
        orig_submit = seam.submit_fn
        seam.submit_fn = lambda *a, **k: submitted.append(1) or 0
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            res2 = _visit_one(seam, {"cid": "0xflip"}, cycle=2, live=True)
        assert res2.status == "CANCELLED"
        assert res2.why == "uma_resolution_proposed"
        assert submitted == [], "zero new orders placed on the flagged visit"
        still = [o for o in registry.get_active_orders()
                 if o.condition_id == "0xflip" and o.status in ("open", "partial")]
        assert still == []
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT kind, reason_code, reason FROM market_events "
                "WHERE condition_id = '0xflip'").fetchall()
        blocked = [r for r in rows if r[0] == "BLOCKED"
                   and r[1] == "uma_resolution_proposed"]
        assert len(blocked) == 1
        assert "proposed" in (blocked[0][2] or "")
        assert "UMA_DISCARD" in caplog.text
        assert "uma_resolution_proposed" in caplog.text


class TestMain:
    """The command line: `python -m core_brain.shadow_run --minutes N`."""

    def test_explicit_run_arguments_keep_their_defaults(self):
        from core_brain.shadow_run import _parse_args

        a = _parse_args([
            "--db", "data/04_shadow_test.db",
            "--run-id", "shadow-04",
        ])

        assert a.minutes == 5.0
        assert a.interval == 5.0
        assert a.db == "data/04_shadow_test.db"
        assert a.max_markets is None
        assert a.markets_path is None

    def test_main_reports_missing_db_as_a_cli_error(self, capsys):
        from core_brain.shadow_run import main

        with pytest.raises(SystemExit) as exc:
            main(["--minutes", "0"])

        assert exc.value.code == 2
        assert "--db" in capsys.readouterr().err

    def test_markets_path_and_paired_depth_arm_parse(self):
        from core_brain.shadow_run import _parse_args

        a = _parse_args([
            "--db", "data/04_shadow_test.db",
            "--run-id", "shadow-04",
            "--markets-path", "runtime/trials/paired/paired_markets.json",
            "--paired-depth-arm", "treatment",
        ])

        assert a.markets_path == "runtime/trials/paired/paired_markets.json"
        assert a.paired_depth_arm == "treatment"

    def test_main_refuses_the_production_registry_via__db(self):
        """The guard sits between argv and the registry, not inside a flag."""
        from core_brain.order_registry import DEFAULT_DB_PATH
        from core_brain.shadow_guard import ShadowSafetyViolation
        from core_brain.shadow_run import main

        with pytest.raises(ShadowSafetyViolation, match="production registry"):
            main(["--minutes", "0", "--db", str(DEFAULT_DB_PATH)])

    def test_main_logs_a_banner_naming_mode_store_timebox_and_no_signer(
            self, tmp_path, caplog):
        """An operator must be able to tell a shadow process from a live one
        in a shared terminal, before any output that looks like results."""
        import logging

        from core_brain.shadow_run import main

        db = tmp_path / "shadow.db"
        with caplog.at_level(logging.INFO, logger="shadow_run"):
            main(
                ["--minutes", "0", "--db", str(db)],
                markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
                client_fn=lambda: object(),
                decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
                fetch_books=_books,
            )
        text = caplog.text.lower()
        assert "shadow" in text
        assert "no signer" in text
        assert str(db).lower() in text
        assert "minutes=0" in text  # the time box is named too

    def test_main_returns_0_when_the_time_box_expires_with_results(
            self, tmp_path):
        from core_brain.shadow_run import main

        def markets(max_markets=None):
            return [FakeMarket("0xabc")]

        rc = main(
            ["--minutes", "0", "--db", str(tmp_path / "shadow.db")],
            markets_fn=markets,
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
            fetch_books=_books,
        )

        assert rc == 0

    def test_main_reads_the_universe_from_the_flag_feed(
            self, tmp_path, monkeypatch, caplog):
        """`--markets-path` reroutes the initial load and every refresh to the
        trial feed; the pathless default loader is never consulted."""
        import json
        import logging

        import core_brain.shadow_run as shadow_mod
        import core_brain.trader_loop as loop_mod
        from core_brain.shadow_run import main

        feed = tmp_path / "trial-markets.json"
        feed.write_text(json.dumps([{"cid": "0xtrial0", "title": "trial 0"},
                                    {"cid": "0xtrial1", "title": "trial 1"}]),
                        encoding="utf-8")

        def no_default():
            raise AssertionError("pathless default loader must not run")

        real_specs = loop_mod._market_specs
        calls = []

        def counting(max_markets=None, registry=None, path=None):
            calls.append(path)
            return real_specs(max_markets, registry=registry, path=path)

        monkeypatch.setattr(shadow_mod, "_default_markets_fn", no_default)
        monkeypatch.setattr(loop_mod, "_market_specs", counting)
        monkeypatch.setattr(loop_mod, "_fetch_market",
                            lambda cid: FakeMarket(cid))

        with caplog.at_level(logging.WARNING, logger="shadow_run"):
            rc = main(
                ["--minutes", "0", "--db", str(tmp_path / "shadow.db"),
                 "--markets-path", str(feed)],
                client_fn=lambda: object(),
                decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
                fetch_books=_books,
            )

        assert rc == 0
        assert len(calls) >= 1
        assert set(calls) == {str(feed)}
        assert str(feed).lower() in caplog.text.lower()

    def test_main_prefers_an_injected_markets_fn_over_the_flag(
            self, tmp_path, monkeypatch):
        """An injected `markets_fn` wins; the flag only builds the default."""
        import core_brain.trader_loop as loop_mod
        from core_brain.shadow_run import main

        seen = {}

        def injected(max_markets=None):
            seen["used"] = True
            return [FakeMarket("0xabc")]

        monkeypatch.setattr(loop_mod, "_fetch_market",
                            lambda cid: FakeMarket(cid))

        rc = main(
            ["--minutes", "0", "--db", str(tmp_path / "shadow.db"),
             "--markets-path", str(tmp_path / "unused.json")],
            markets_fn=injected,
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
            fetch_books=_books,
        )

        assert rc == 0
        assert seen == {"used": True}

    def test_main_without_the_flag_delegates_pathless(
            self, tmp_path, monkeypatch):
        """No flag: the default wrapper calls `_market_specs` with no path."""
        import core_brain.shadow_run as shadow_mod
        import core_brain.trader_loop as loop_mod
        from core_brain.shadow_run import main

        seen = {}

        def spy(max_markets=None, registry=None):
            seen["max_markets"] = max_markets
            return [FakeMarket("0xabc")]

        monkeypatch.setattr(loop_mod, "_market_specs", spy)
        monkeypatch.setattr(shadow_mod, "_default_markets_fn",
                            lambda: loop_mod._market_specs)
        monkeypatch.setattr(loop_mod, "_fetch_market",
                            lambda cid: FakeMarket(cid))

        rc = main(
            ["--minutes", "0", "--db", str(tmp_path / "shadow.db"),
             "--max-markets", "7"],
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
            fetch_books=_books,
        )

        assert rc == 0
        assert seen == {"max_markets": 7}

    def test_main_wires_the_real_book_source_when_none_is_injected(
            self, tmp_path, monkeypatch):
        """A rehearsal against empty books rehearses nothing.

        `/book` is a public endpoint -- no key, no API credentials, and not on
        the CLOB client the deny-by-default proxy guards -- so the entrypoint
        reads it exactly as the live loop does. Unwired, every market decides
        against `{"bids": {}, "asks": {}}` and declines with "no two-sided
        book", which reads on the dashboard as a quiet venue rather than as a
        seam nobody connected.
        """
        import core_brain.markets as markets_mod
        from core_brain.shadow_run import main

        calls = []

        def fake_full_book(clob_host, token_id):
            calls.append((clob_host, token_id))
            return _books(clob_host, token_id)

        monkeypatch.setattr(markets_mod, "full_book", fake_full_book)

        main(
            ["--minutes", "0", "--db", str(tmp_path / "shadow.db")],
            markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
        )

        assert {t for _, t in calls} == {"tok-up", "tok-dn"}
        assert all(h.startswith("http") for h, _ in calls)


class TestBoundaryGuard:
    def test_no_live_module_imports_the_shadow_model(self):
        """The live fill engine's invariant is that a fill comes only from the
        venue. The shadow model infers one. The two must never meet, and the
        cheap way to keep that true is to check that nobody imports across the
        line.

        Scanned across every directory that runs beside the live path, not
        just `core_brain/`: the dashboard reads and writes the production
        registry, `scoring/` feeds the market selection the live loop trades,
        and `scripts/` is what an operator actually launches. A shadow import
        reaching live through any of those is the same failure.
        """
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        shadow_own = {"shadow_run.py", "shadow_exec.py", "shadow_fills.py",
                      "shadow_guard.py"}
        live_modules = [
            p
            for d in ("core_brain", "dashboard", "scoring", "scripts")
            for p in (repo / d).rglob("*.py")
            if p.name not in shadow_own
        ]
        assert live_modules, "the guard scanned nothing"

        offenders = [
            str(p.relative_to(repo)) for p in live_modules
            if "shadow_fills" in p.read_text(encoding="utf-8")
            or "shadow_exec" in p.read_text(encoding="utf-8")
        ]

        assert offenders == []


class TestPositionInTheLog:
    """The decide line and the inventory behind it.

    `trader_loop.py` emits the `quoting/decide` event with only
    `intent_count` and `condition_id` in `extra` (see the emit call in
    `evaluate_market_quote`, `core_brain/trader_loop.py` around line 455) --
    it never carries share counts, and `trader_loop.py` must not be edited to
    add them. The seam already computes an `Inventory` for the market inside
    `settling_inventory_fn` (`build_shadow_seam`), immediately before the
    decision that uses it. The log line resolves the extra's `condition_id`
    back to that same inventory through a cache `build_shadow_seam` keeps in
    its own closure -- it is not handed shares directly.
    """

    def test_a_decision_is_logged_with_the_position_behind_it(
            self, tmp_path, caplog):
        import logging

        from core_brain.quotes import QuoteIntent
        from core_brain.shadow_run import build_shadow_seam

        db = tmp_path / "shadow.db"
        market = FakeMarket("0xabc")

        seam = build_shadow_seam(
            db_path=db,
            client_fn=lambda: object(),
            traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
        )

        intents = [QuoteIntent(side="UP", token_id="tok-up", price=0.47,
                               size=20, mid=0.5, edge_vs_mid=0.0)]
        seam.submit_fn(seam.client, seam.registry, market, intents,
                      seam.base_cfg)

        # Settle, exactly as a real visit does immediately before deciding --
        # this is what populates the cache the log line reads from.
        inv = seam.inventory_fn(market)
        assert inv.up_shares == pytest.approx(20.0)  # sanity: the fixture fired
        assert inv.down_shares == pytest.approx(0.0)

        with caplog.at_level(logging.INFO, logger="shadow_run"):
            seam.emit_fn(9, "quoting", "decide", market_slug="dota-2026",
                        reason="pair cost 0.98",
                        extra={"intent_count": 1, "condition_id": "0xabc"})

        assert "up=20" in caplog.text
        assert "down=0" in caplog.text

    def test_an_unknown_condition_id_logs_without_inventory(
            self, tmp_path, caplog):
        """Total lookup: a condition_id the seam never settled logs the
        decide line plain -- no KeyError, and no fabricated zeros for a
        position nothing measured."""
        import logging

        from core_brain.shadow_run import build_shadow_seam

        seam = build_shadow_seam(db_path=tmp_path / "shadow.db",
                                 client_fn=lambda: object())

        with caplog.at_level(logging.INFO, logger="shadow_run"):
            seam.emit_fn(3, "quoting", "decide", market_slug="never-visited",
                        reason="", extra={"intent_count": 0,
                                          "condition_id": "0xnope"})

        assert "cycle=3" in caplog.text
        assert "up=" not in caplog.text
        assert "down=" not in caplog.text


class TestUnlimitedSession:
    """Negative minutes mean run until stopped; zero remains one rotation."""

    @staticmethod
    def _capture_sleep_fn(tmp_path, monkeypatch, minutes):
        from core_brain import shadow_run as sr
        from core_brain import trader_loop

        target = tmp_path / "runtime" / "shadow_run.json"
        monkeypatch.setattr(sr, "shadow_heartbeat_path",
                            lambda root=None, run_id="": target)
        sleeps = []
        monkeypatch.setattr(sr.time, "sleep", lambda seconds: sleeps.append(seconds))
        captured = {}

        def fake_loop_run(seam, **kwargs):
            captured["sleep_fn"] = kwargs["sleep_fn"]
            return []

        monkeypatch.setattr(trader_loop, "run", fake_loop_run)
        monkeypatch.setattr(sr, "build_shadow_seam",
                            lambda **kw: type("Seam", (), {})())
        sr.run_shadow(
            minutes=minutes, db_path=tmp_path / "shadow.db",
            markets_fn=lambda: [], client_fn=lambda: object(),
            decide_fn=lambda *args, **kwargs: [],
            fetch_books=lambda *args, **kwargs: {}, cfg=_load_cfg(),
            run_id="shadow-unlimited", sleep_fn=None,
        )
        return captured["sleep_fn"], sleeps

    def test_negative_minutes_install_no_deadline(self, tmp_path, monkeypatch):
        sleep_fn, sleeps = self._capture_sleep_fn(tmp_path, monkeypatch, -1.0)

        sleep_fn(3600.0)

        assert sleeps == [3600.0]

    def test_zero_minutes_still_expires_immediately(self, tmp_path, monkeypatch):
        from core_brain.shadow_run import _Deadline

        sleep_fn, _ = self._capture_sleep_fn(tmp_path, monkeypatch, 0.0)

        with pytest.raises(_Deadline):
            sleep_fn(3600.0)


class TestSecondRotation:
    """Three cycles of the real loop over one market, with the price moving.

    Every other `run_shadow` test in this file passes `minutes=0.0`, which is
    exactly one rotation, and the rest drive seam ports one at a time. That is
    why a defect that only appears the SECOND time a market is visited -- a
    re-quote, which needs a cancel first -- survived seven scoped reviews. This
    class exists to make the second and third visits real.

    The loop under test is `trader_loop.run` itself, reached through
    `run_shadow` so the whole wiring (seam, sweep, fleet state) is the one a
    session gets. Only the deadline sleep is replaced: counting rotations is
    hermetic where a wall clock is not.
    """

    @staticmethod
    def _canonical_book(_clob_host, token_id):
        return {"token_id": token_id, "bids": {0.46: 500.0},
                "asks": {0.52: 500.0}, "best_bid": 0.46, "best_ask": 0.52,
                "malformed": 0}

    def _run_cycles(self, tmp_path, monkeypatch, *, cycles, prices, tape):
        """Rotate one market `cycles` times, quoting `prices[i]` on cycle i."""
        import core_brain.markets as markets_mod
        from core_brain.shadow_run import _Deadline, run_shadow

        seen_cycles = {"n": 0}

        def counting_sleep(_seconds):
            seen_cycles["n"] += 1
            if seen_cycles["n"] >= cycles:
                raise _Deadline()

        monkeypatch.setattr("core_brain.shadow_run.make_deadline_sleep",
                            lambda *a, **k: counting_sleep)

        tape_calls = {"n": 0}

        def fake_recent_trades(condition_id, seen):
            tape_calls["n"] += 1
            return tape(tape_calls["n"])

        monkeypatch.setattr(markets_mod, "recent_trades", fake_recent_trades)

        decided = {"n": 0}

        def decide_fn(cfg, up, dn, inv, t_rem, wf):
            price = prices[min(decided["n"], len(prices) - 1)]
            decided["n"] += 1
            return ([QuoteIntent(side="UP", token_id="tok-up", price=price,
                                 size=20, mid=0.5, edge_vs_mid=0.0)],
                    f"cycle price {price}")

        result = run_shadow(
            minutes=1.0, db_path=tmp_path / "shadow.db",
            markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
            client_fn=lambda: object(),
            decide_fn=decide_fn,
            fetch_books=self._canonical_book,
            interval=0.0,
        )
        return result, seen_cycles["n"], decided["n"]

    def test_a_requote_does_not_park_the_market_in_error(
            self, tmp_path, monkeypatch):
        """Cycle 1 rests at 0.47; cycles 2 and 3 want 0.48 and 0.49, which
        means cancelling first. With a `cancel_fn` that reports 0 cancelled,
        `_still_resting` cannot verify anything (the shadow seam wires no
        `resting_order_ids_fn`), so it fails closed and the market goes ERROR
        on every re-quote -- the rehearsal stops rehearsing it.
        """
        result, cycles, decisions = self._run_cycles(
            tmp_path, monkeypatch, cycles=3, prices=[0.47, 0.48, 0.49],
            tape=lambda n: {})

        assert cycles == 3
        assert decisions == 3, "the market was not visited on every cycle"
        errors = [r for r in result.results if r.status == "ERROR"]
        assert errors == [], f"re-quote errored: {[r.error for r in errors]}"
        assert [r.status for r in result.results] == ["QUOTED"]

    def test_rotation_holds_the_resting_order(self, tmp_path, monkeypatch):
        """A rotation that only moves the quote price must not churn the book.

        #387: no drift check, no pair-cost re-check after placement. Cycle 1
        rests at 0.47; cycles 2 and 3 want 0.52 and 0.57, and the 0.47 order
        stays open -- the replacement prices are suppressed, never posted.
        """
        from core_brain.order_registry import OrderRegistry

        self._run_cycles(tmp_path, monkeypatch, cycles=3,
                         prices=[0.47, 0.52, 0.57], tape=lambda n: {})

        reg = OrderRegistry(db_path=tmp_path / "shadow.db")
        rows = [o for o in reg.get_all_orders() if o["token_id"] == "tok-up"]
        by_price = {round(float(o["price"]), 2): o["status"] for o in rows}

        assert by_price == {0.47: "open"}
        assert [o for o in reg.get_active_orders()
                if o.token_id == "tok-up" and o.status in ("open", "partial")
                ] != [], "nothing rests at the current price"

    def test_inventory_reflects_what_settled_before_the_requote(
            self, tmp_path, monkeypatch):
        """The tape credits 5 shares against the order resting at 0.47 on the
        second cycle, and the lifecycle waits on exactly those shares.

        Full chain across the rotation: the fill is credited to the order that
        was resting when it happened (not to the replacement), the position it
        leaves is a naked one-sided leg, and the lifecycle records patient
        wait without completing or selling it. Reading only `up_shares` here
        would pass on a store where nothing was credited at all, which is why
        the fill is asserted from the ledger too.
        """
        from core_brain.order_registry import OrderRegistry, inventory_from_registry

        self._run_cycles(
            tmp_path, monkeypatch, cycles=3, prices=[0.47, 0.48, 0.49],
            tape=lambda n: {"tok-up": {0.47: 5.0}} if n == 2 else {})

        db = tmp_path / "shadow.db"
        reg = OrderRegistry(db_path=db)
        fills = reg.get_all_fills()
        assert [(f["token_id"], f["size"], f["price"]) for f in fills] == [
            ("tok-up", 5.0, 0.47)]
        # Credited to the order that was resting when the tape printed, which
        # is the one the next cycle superseded -- not the replacement.
        filled_row = next(o for o in reg.get_all_orders()
                          if o["id"] == fills[0]["order_uuid"])
        assert round(float(filled_row["price"]), 2) == 0.47

        assert reg.get_all_closes() == []
        assert reg.get_lifecycle_state(filled_row["pair_id"]).state == "PATIENT_WAIT"
        inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
        assert inv.up_shares == pytest.approx(5.0)
        assert inv.down_shares == pytest.approx(0.0)


class TestPairsWindowWiring:
    """The shim and the pass read the same window.

    `auto_manage_pairs` discovers pairs by fill age against
    `cfg.pairs_exit_window_sec`; `ShadowExecutionClient` reconstructs which
    pair a completion belongs to by the same rule. If the session did not pass
    its configured window down, the two could disagree and a completion would
    land on a pair the pass never acted on.
    """

    def test_the_session_hands_its_configured_window_to_the_shim(
            self, tmp_path, monkeypatch):
        from dataclasses import replace as dc_replace

        import core_brain.shadow_exec as shadow_exec
        import core_brain.config as config_mod
        from core_brain.shadow_run import run_shadow

        seen: list[float] = []
        real_client = shadow_exec.ShadowExecutionClient

        class RecordingClient(real_client):
            def __init__(self, *a, **kw):
                seen.append(kw.get("window_sec"))
                super().__init__(*a, **kw)

        monkeypatch.setattr(shadow_exec, "ShadowExecutionClient",
                            RecordingClient)
        narrow_cfg = dc_replace(_load_cfg(), pairs_exit_window_sec=123.0)
        monkeypatch.setattr(config_mod, "load", lambda *a, **k: narrow_cfg)

        run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=lambda max_markets=None: [],
            client_fn=lambda: object(),
            fetch_books=lambda h, t: {"bids": {}, "asks": {}},
        )

        assert seen == [123.0]


class TestExplicitDb:
    def test_shadow_run_requires_explicit_db(self):
        from core_brain.shadow_run import run_shadow

        import pytest

        with pytest.raises((TypeError, ValueError), match="explicit|per-run"):
            run_shadow(minutes=0.0, db_path=None)

    def test_pipeline_sourced_dbs_excludes_shared(self):
        from pathlib import Path

        from core_brain.kpi import _pipeline_sourced_dbs

        assert Path("data/shadow.db").resolve() not in _pipeline_sourced_dbs()


def _admission_feed(path, arm_rows):
    import json

    path.write_text(json.dumps({
        "format": "spread_hunter.paired-admission.v1",
        "snapshot_id": "snap-a",
        "control": [],
        "treatment": arm_rows,
    }), encoding="utf-8")


def _admission_row(cid, arm="treatment", role="mainline", event="ev-1"):
    return {
        "cid": cid,
        "trial_arm": arm, "arm": arm,
        "trial_axis": "admission",
        "snapshot_id": "snap-a",
        "event_cluster_id": f"gamma-event:{event}",
        "family": "fam",
        "admission_role": role,
        "event_id": event, "event_slug": event,
    }


def test_admission_arm_reads_only_markets_path(tmp_path):
    import sqlite3

    from core_brain.shadow_run import run_shadow

    feed = tmp_path / "paired_admission_markets.json"
    _admission_feed(feed, [_admission_row("0xadm")])
    db = tmp_path / "adm.db"

    run_shadow(
        minutes=0.01, db_path=db,
        markets_fn=lambda max_markets=None: [],
        markets_path=str(feed),
        client_fn=lambda: object(),
        fetch_books=_books,
        paired_admission_arm="treatment",
        starting_bankroll_usd=100.0,
    )

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT arm, trial_axis, cutoff_usd FROM shadow_paired_runs"
        ).fetchone()
    assert row[0] == "treatment"
    assert row[1] == "admission"
    assert row[2] is None


def test_admission_arm_requires_markets_path_and_bankroll(tmp_path):
    from core_brain.shadow_run import run_shadow

    feed = tmp_path / "paired_admission_markets.json"
    _admission_feed(feed, [_admission_row("0xadm")])
    db = tmp_path / "adm.db"
    base = dict(minutes=0, db_path=db,
                markets_fn=lambda max_markets=None: [],
                client_fn=lambda: object(), fetch_books=_books)

    with pytest.raises(ValueError, match="markets-path"):
        run_shadow(**{**base, "paired_admission_arm": "treatment",
                      "starting_bankroll_usd": 100.0})
    with pytest.raises(ValueError, match="bankroll"):
        run_shadow(**{**base, "paired_admission_arm": "treatment",
                      "markets_path": str(feed)})
    with pytest.raises(ValueError, match="bankroll"):
        run_shadow(**{**base, "paired_admission_arm": "treatment",
                      "markets_path": str(feed),
                      "starting_bankroll_usd": 0.0})
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_shadow(**{**base, "paired_admission_arm": "treatment",
                      "paired_depth_arm": "treatment",
                      "markets_path": str(feed),
                      "starting_bankroll_usd": 100.0})


def test_admission_arm_needs_no_cutoff(tmp_path):
    from core_brain.shadow_run import build_shadow_seam

    db = tmp_path / "adm-seam.db"
    build_shadow_seam(db_path=db, paired_admission_arm="treatment")


def test_admission_arm_rejects_empty_mixed_or_unidentified_rows(tmp_path):
    from core_brain.market_feed import MarketFeedError
    from core_brain.shadow_run import run_shadow

    def run(feed_rows):
        feed = tmp_path / "paired_admission_markets.json"
        _admission_feed(feed, feed_rows)
        run_shadow(
            minutes=0.01, db_path=tmp_path / "adm.db",
            markets_fn=lambda max_markets=None: [],
            markets_path=str(feed),
            client_fn=lambda: object(), fetch_books=_books,
            paired_admission_arm="treatment",
            starting_bankroll_usd=100.0,
        )

    with pytest.raises(ValueError, match="empty"):
        run([])
    mixed = [_admission_row("0xa"), _admission_row("0xb")]
    mixed[1]["snapshot_id"] = "snap-other"
    with pytest.raises(MarketFeedError, match="inconsistent"):
        run(mixed)
    orphan = _admission_row("0xorphan")
    orphan.pop("event_cluster_id")
    orphan.pop("event_id")
    orphan.pop("event_slug")
    with pytest.raises(MarketFeedError, match="no event identity"):
        run([orphan])


def test_admission_cli_parses_and_rejects_depth_combo(tmp_path):
    from core_brain.shadow_run import _parse_args, main

    a = _parse_args([
        "--db", "data/04_shadow_test.db",
        "--run-id", "shadow-04",
        "--markets-path", "runtime/trials/adm/paired_admission_markets.json",
        "--paired-admission-arm", "control",
    ])
    assert a.paired_admission_arm == "control"

    with pytest.raises(SystemExit):
        main([
            "--minutes", "0",
            "--db", str(tmp_path / "combo.db"),
            "--run-id", "shadow-combo",
            "--markets-path", "runtime/trials/adm/paired_admission_markets.json",
            "--paired-admission-arm", "control",
            "--paired-depth-arm", "control",
        ])

    with pytest.raises(SystemExit) as exc:
        main([
            "--minutes", "0",
            "--db", str(tmp_path / "combo2.db"),
            "--run-id", "shadow-combo",
            "--markets-path", "runtime/trials/adm/paired_admission_markets.json",
            "--paired-admission-arm", "control",
            "--paired-depth-cutoff-usd", "500",
        ])
    assert "cutoff" in str(exc.value)


class TestRefusedHoldShadow:
    """#390 end to end: a refused cycle holds, a dead market cancels."""

    TRANSIENT_WHY = ("UP: 8.0c from mid > 4.5c reward window; "
                     "DOWN: 8.0c from mid > 4.5c reward window")

    def _held_pair(self):
        return [QuoteIntent(side="UP", token_id="tok-up", price=0.60, size=5,
                            mid=0.61, edge_vs_mid=0.01),
                QuoteIntent(side="DOWN", token_id="tok-dn", price=0.40, size=5,
                            mid=0.41, edge_vs_mid=0.01)]

    def _seam(self, decide, calls):
        def open_orders_fn(m):
            return [{"token_id": "tok-up", "price": 0.60, "order_id": "o-up",
                     "side": "BUY", "status": "open"},
                    {"token_id": "tok-dn", "price": 0.40, "order_id": "o-dn",
                     "side": "BUY", "status": "open"}]

        def submit_fn(client, registry, market, intents, cfg):
            calls["submitted"].append([i.token_id for i in intents])
            return len(intents)

        def cancel_fn(client, registry, orders):
            calls["cancelled"].append([o["order_id"] for o in orders])
            return len(orders)

        return _seam(decide=decide, submit_fn=submit_fn, cancel_fn=cancel_fn,
                     open_orders_fn=open_orders_fn,
                     reconcile_fn=lambda *a, **k: None,
                     sweep_fn=lambda: None)

    def _run_cycles(self, seam, n):
        sleeps = []

        def sleep_fn(s):
            sleeps.append(s)
            if len(sleeps) >= n:
                raise KeyboardInterrupt

        return run(seam, interval=0.0, once=False, live=True,
                   markets=[FakeMarket("0xabc")], sleep_fn=sleep_fn)

    def test_refuse_then_quote_holds_without_churn(self, caplog):
        import logging
        caplog.set_level(logging.INFO)
        calls = {"submitted": [], "cancelled": []}
        ncalls = []

        def decide(cfg, up, dn, inv, t_rem, wf):
            ncalls.append(1)
            if len(ncalls) == 1:
                return [], self.TRANSIENT_WHY
            return list(self._held_pair()), ""

        results = self._run_cycles(self._seam(decide, calls), 2)
        assert [r.status for r in results] == ["QUOTED"]
        assert calls["cancelled"] == []
        assert calls["submitted"] == []
        assert "[HOLDING]" in caplog.text

    def test_dead_market_cancels_past_grace(self):
        from core_brain.trader_loop import REFUSED_HOLD_GRACE_CYCLES as GRACE
        calls = {"submitted": [], "cancelled": []}
        seam = self._seam(
            lambda cfg, up, dn, inv, t_rem, wf: ([], self.TRANSIENT_WHY),
            calls)
        self._run_cycles(seam, GRACE)
        assert calls["submitted"] == []
        assert calls["cancelled"] == [["o-up", "o-dn"]]


class TestQueueGateInRehearsal:
    """The queue-clear gate (#393) inside the rehearsal.

    The gate was inert in the only place it can be watched without spending
    money: `build_shadow_seam` left `VenueSeam.flow_fn` unset, so every
    rehearsal decided each market as if nothing rested ahead of the bid, and the
    numbers the record-only default exists to gather were produced nowhere but
    unit tests.

    The port is now wired by the entrypoint (`main`, with the module's other
    live reads), injectable through `run_shadow(flow_fn=...)`, left inert -- and
    NAMED as inert -- for a caller that substituted its own sources, lazy at the
    gate, and fail-open when the tape cannot be read. Each is pinned here.
    """

    #: 100 shares rest at 0.47 in `_books`; 5 shares traded through there in the
    #: 30m window is 100 / (5/30) = 600 minutes to clear, against the 60-minute
    #: bar. Every number comes from fixtures this file already uses.
    DEEP_FRONT_WHY = "600 min to clear"

    @staticmethod
    def _flow(status="complete", shares=5.0):
        """A flow port that records its calls, so laziness is observable."""
        calls: list = []

        def flow_fn(condition_id, window_sec):
            from core_brain.markets import SellFlow

            calls.append((condition_id, window_sec))
            return SellFlow(status=status, window_sec=window_sec,
                            by_token={"tok-up": {0.47: shares}})

        flow_fn.calls = calls  # type: ignore[attr-defined]
        return flow_fn

    @staticmethod
    def _deep_front_decide(cfg, up, dn, inv, t_rem, wf):
        """One new passive UP bid at 0.47 -- 100 shares rest in front of it."""
        return ([QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                             mid=0.475, edge_vs_mid=0.005)], "")

    @staticmethod
    def _markets(n=1):
        return lambda max_markets=None: [FakeMarket(f"0x{i}") for i in range(n)]

    def test_the_entrypoint_wires_the_live_flow_reader(self, tmp_path,
                                                       monkeypatch, caplog):
        """The rehearsal an operator launches measures the real tape.

        `main` is where this module wires its live reads (`fetch_books` too),
        so it is where the gate's reader belongs. Pinned against the module the
        reader actually lives in, so a rename cannot leave the gate inert on the
        one surface it is meant to run on -- and pinned against the inert
        warning, because "wired the port" and "warned that it did not" are the
        two readings an operator must never have to guess between.
        """
        import logging

        import core_brain.markets as markets
        from core_brain.shadow_run import main, shadow_cfg

        seen: list = []

        def fake_reader(condition_id, window_sec):
            from core_brain.markets import SellFlow

            seen.append((condition_id, window_sec))
            return SellFlow("complete", window_sec, {})

        monkeypatch.setattr(markets, "recent_sell_flow", fake_reader)

        with caplog.at_level(logging.WARNING, logger="shadow_run"):
            rc = main(
                ["--minutes", "0", "--db", str(tmp_path / "shadow.db")],
                markets_fn=self._markets(), client_fn=lambda: object(),
                decide_fn=self._deep_front_decide, fetch_books=_books,
            )

        assert rc == 0
        assert seen == [("0x0", float(shadow_cfg().queue_flow_window_sec))]
        assert "queue-clear gate inert" not in caplog.text

    def test_a_seam_without_a_flow_port_names_the_inert_gate(self, tmp_path,
                                                             caplog):
        """A caller that substitutes its own sources gets no live read by
        surprise -- and must be told the gate stayed inert.

        `scripts/ladder_shadow_rehearsal.py` replays a recorded tape for books
        and fills and rotates at `--interval 0.01`; defaulting the live reader
        at the seam would put public requests per market per rotation into a
        recorded experiment and measure today's tape against a previous
        session's book. Silent inertness is the failure this pins shut.
        """
        import logging

        from core_brain.shadow_run import build_shadow_seam

        with caplog.at_level(logging.WARNING, logger="shadow_run"):
            seam = build_shadow_seam(db_path=tmp_path / "shadow.db",
                                     client_fn=lambda: object())

        assert seam.flow_fn is None
        assert "queue-clear gate inert" in caplog.text

    def test_an_injected_flow_port_is_what_the_loop_calls(self, tmp_path,
                                                          monkeypatch):
        """The injection seam a test or a trial run needs: no live read."""
        import core_brain.markets as markets
        from core_brain.shadow_run import run_shadow, shadow_cfg

        def forbidden(*_a, **_k):
            raise AssertionError("the live reader ran despite an injected port")

        monkeypatch.setattr(markets, "recent_sell_flow", forbidden)
        flow = self._flow()

        result = run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(), client_fn=lambda: object(),
            decide_fn=self._deep_front_decide, fetch_books=_books,
            flow_fn=flow,
        )

        assert flow.calls == [("0x0", float(shadow_cfg().queue_flow_window_sec))]
        assert "record-only" in result.results[0].queue_why

    def test_a_rehearsal_measures_the_queue_and_records_the_reason(
            self, tmp_path, caplog):
        """The shipped record-only default, exercised by a real rehearsal.

        The point of shipping record-only is the numbers it gathers; those
        numbers were produced only in unit tests, never by the rehearsal the
        threshold is supposed to be picked from.
        """
        import logging

        from core_brain.shadow_run import run_shadow, shadow_cfg

        flow = self._flow()
        window = float(shadow_cfg().queue_flow_window_sec)

        with caplog.at_level(logging.INFO, logger="main_spread_hunter_loop"):
            result = run_shadow(
                minutes=0.0, db_path=tmp_path / "shadow.db",
                markets_fn=self._markets(), client_fn=lambda: object(),
                decide_fn=self._deep_front_decide, fetch_books=_books,
                flow_fn=flow,
            )

        (visit,) = result.results
        assert flow.calls == [("0x0", window)]
        assert self.DEEP_FRONT_WHY in visit.queue_why
        assert "record-only" in visit.queue_why
        # Record-only: the rehearsal still placed it, having recorded why not.
        assert [i.side for i in result.intents] == ["UP"]
        assert "[QUEUE]" in caplog.text

    def test_enforcing_in_a_rehearsal_drops_the_placement(self, tmp_path):
        """A rehearsal rehearses what we ship -- including the refusal."""
        from dataclasses import replace

        from core_brain.shadow_run import run_shadow, shadow_cfg

        cfg = replace(shadow_cfg(), enforce_queue_clear_gate=True)

        result = run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(), client_fn=lambda: object(),
            decide_fn=self._deep_front_decide, fetch_books=_books, cfg=cfg,
            flow_fn=self._flow(),
        )

        (visit,) = result.results
        assert self.DEEP_FRONT_WHY in visit.queue_why
        assert "record-only" not in visit.queue_why
        assert result.intents == []

    def test_a_clear_front_costs_the_rehearsal_no_tape_read(self, tmp_path):
        """Lazy by design: nobody pays a round-trip for a number that cannot
        change the answer."""
        from core_brain.shadow_run import run_shadow

        def clear_front(cfg, up, dn, inv, t_rem, wf):
            # 0.45 while the whole bid side rests at 0.47: nothing ahead of us.
            return ([QuoteIntent(side="UP", token_id="tok-up", price=0.45,
                                 size=20, mid=0.475, edge_vs_mid=0.005)], "")

        flow = self._flow()

        run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(), client_fn=lambda: object(),
            decide_fn=clear_front, fetch_books=_books, flow_fn=flow,
        )

        assert flow.calls == []

    def test_an_unmeasurable_tape_keeps_the_rehearsal_running(self, tmp_path):
        """A rehearsal holding no tape measured nothing, so it must place as
        before and name the skip -- never refuse on a floor, and never refuse
        on a read that never happened.
        """
        from core_brain.shadow_run import run_shadow

        flow = self._flow(status="unavailable", shares=0.0)

        result = run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=self._markets(), client_fn=lambda: object(),
            decide_fn=self._deep_front_decide, fetch_books=_books,
            flow_fn=flow,
        )

        (visit,) = result.results
        assert "unavailable" in visit.queue_why
        assert "nothing was refused" in visit.queue_why
        assert [i.side for i in result.intents] == ["UP"]

    def test_the_summary_names_what_the_gate_reported(self, tmp_path, caplog):
        """The run's own report line: measured, or nothing measurable.

        A session that measured its markets and reported the queue must not
        read the same as one that never got a tape. Driven through `main`, so
        the line is pinned on the command an operator actually runs.
        """
        import logging

        from core_brain.shadow_run import main

        with caplog.at_level(logging.WARNING, logger="shadow_run"):
            rc = main(
                ["--minutes", "0", "--db", str(tmp_path / "shadow.db")],
                markets_fn=self._markets(), client_fn=lambda: object(),
                decide_fn=self._deep_front_decide, fetch_books=_books,
                flow_fn=self._flow(),
            )

        assert rc == 0
        assert "queue gate: reported=1 measured=1 unmeasurable=0" in caplog.text


class _FakeTapeResponse:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self._rows


class TestTapeMissReplay:
    """#401: prints at the level drain the queue or fill the paper order.

    Replays the incident shape through the REAL `_default_traded_fn` -- only
    `markets._SESSION.get` is stubbed, keyed by request params: taker-view
    SELL volume split across two pages (invisible to the old single-page
    reader) plus a maker-view-only resting-bid fill.
    """

    COND = "0xcond401"

    @staticmethod
    def _row(side, price, size, tx, ts=1791368500):
        return {"transactionHash": tx, "asset": "tok-dn", "timestamp": ts,
                "price": price, "size": size, "side": side}

    @staticmethod
    def _books(_clob_host, token_id):
        return {"token_id": token_id, "bids": {0.26: 744.0}, "asks": {},
                "best_bid": 0.26, "best_ask": None, "malformed": 0}

    def _serve(self, monkeypatch, holder):
        from core_brain import markets

        def _get(url, params=None, **kw):
            params = dict(params or {})
            lim = int(params.get("limit", 500)) or 500
            idx = int(params.get("offset", 0)) // lim
            if params.get("takerOnly", "default") is False:
                return _FakeTapeResponse(holder["maker"].get(idx, []))
            return _FakeTapeResponse(holder["taker"].get(idx, []))

        monkeypatch.setattr(markets._SESSION, "get", _get, raising=False)

    def _submit_down9(self, db):
        from core_brain.order_registry import OrderRegistry, init_db
        from core_brain.shadow_exec import ensure_shadow_tables, record_submit

        init_db(db)
        reg = OrderRegistry(db_path=db)
        ensure_shadow_tables(db)
        record_submit(object(), reg, FakeMarket(self.COND),
                      [QuoteIntent(side="DOWN", token_id="tok-dn", price=0.26,
                                   size=9, mid=0.5, edge_vs_mid=0.0)],
                      _load_cfg(), db_path=db, book_fn=self._books)
        return reg

    def _settle(self, reg, db, seen):
        from core_brain.shadow_exec import settle_market
        from core_brain.shadow_run import _default_traded_fn

        return settle_market(reg, FakeMarket(self.COND), db_path=db,
                             traded_fn=_default_traded_fn(), seen=seen,
                             book_fn=self._books)

    def _mark_traded(self, db):
        import sqlite3

        con = sqlite3.connect(db)
        row = con.execute(
            "SELECT traded FROM queue_marks WHERE token_id = 'tok-dn'"
            " AND price = 0.26 ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
        return row[0] if row else None

    def test_prints_across_two_pages_drain_the_queue_and_cap_the_fill(
            self, tmp_path, monkeypatch):
        from core_brain.shadow_exec import read_queue_ahead

        db = tmp_path / "shadow.db"
        holder = {"taker": {0: [self._row("SELL", 0.25, 10.0, "0xprime")]},
                  "maker": {0: []}}
        self._serve(monkeypatch, holder)
        seen: set = set()

        # Phase 1: prime the tape while no orders rest -- history is marked
        # seen and credits nothing.
        from core_brain.order_registry import OrderRegistry, init_db
        from core_brain.shadow_exec import ensure_shadow_tables
        init_db(db)
        reg = OrderRegistry(db_path=db)
        ensure_shadow_tables(db)
        assert self._settle(reg, db, seen) == []

        # Phase 2: DOWN 9 @ 0.26 rests behind a 744 queue.
        reg = self._submit_down9(db)
        (order_id,) = [o["id"] for o in reg.get_all_orders()
                       if o["token_id"] == "tok-dn"]
        assert read_queue_ahead(db, reg._run_id(), order_id) == 744.0

        # Phase 3: incident-shaped tape -- page 0 full with no 0.26 SELLs,
        # page 1 carries 900 SELLs, maker walk carries a 300 resting fill.
        filler = [self._row("SELL", 0.25, 1.0, f"0xfill{i}")
                  for i in range(498)]
        holder["taker"] = {
            0: filler + [self._row("BUY", 0.26, 50.0, "0xmint1"),
                         self._row("BUY", 0.26, 60.0, "0xmint2")],
            1: [self._row("SELL", 0.26, 400.0, "0xsellA"),
                self._row("SELL", 0.26, 500.0, "0xsellB")],
        }
        holder["maker"] = {0: [self._row("BUY", 0.26, 300.0, "0xmaker")]}

        fills = self._settle(reg, db, seen)

        # Assert -- 1200 at the level: 744 queue drains, fill caps at 9.
        assert [(f.local_id, f.size) for f in fills] == [(order_id, 9.0)]
        assert _filled_by_token(reg, self.COND) == {"tok-dn": 9.0}
        assert self._mark_traded(db) == 1200.0
        assert read_queue_ahead(db, reg._run_id(), order_id) == 0.0

        # Phase 4: repeat poll on the same tape adds no drain and no fill.
        assert self._settle(reg, db, seen) == []
        assert _filled_by_token(reg, self.COND) == {"tok-dn": 9.0}

    def test_sells_at_025_leave_the_026_level_untouched(
            self, tmp_path, monkeypatch):
        from core_brain.shadow_exec import read_queue_ahead

        db = tmp_path / "shadow.db"
        holder = {"taker": {0: [self._row("SELL", 0.25, 1000.0, "0xaway")]},
                  "maker": {0: []}}
        self._serve(monkeypatch, holder)

        reg = self._submit_down9(db)
        (order_id,) = [o["id"] for o in reg.get_all_orders()
                       if o["token_id"] == "tok-dn"]

        assert self._settle(reg, db, set()) == []
        assert read_queue_ahead(db, reg._run_id(), order_id) == 744.0
        assert self._mark_traded(db) == 0.0


class TestFullCycleRehearsal:
    """#402 T5: one market quoted, paper-filled, merged, and re-quoted."""

    COND = "0xfullcycle"

    @staticmethod
    def _books(_clob_host, token_id):
        return {"token_id": token_id, "best_bid": 0.47, "best_ask": 0.49,
                "bids": {0.47: 500.0}, "asks": {0.49: 500.0},
                "malformed": 0}

    def _serve(self, monkeypatch, holder):
        from core_brain import markets

        def _get(url, params=None, **kw):
            params = dict(params or {})
            lim = int(params.get("limit", 500)) or 500
            idx = int(params.get("offset", 0)) // lim
            if params.get("takerOnly", "default") is False:
                return _FakeTapeResponse(holder["maker"].get(idx, []))
            return _FakeTapeResponse(holder["taker"].get(idx, []))

        monkeypatch.setattr(markets._SESSION, "get", _get, raising=False)

    def test_quote_fill_merge_requote(self, tmp_path, monkeypatch):
        import time
        from core_brain.order_registry import OrderRegistry
        from core_brain.shadow_run import run_shadow

        db = tmp_path / "shadow.db"
        holder = {"taker": {0: []}, "maker": {0: []}}
        self._serve(monkeypatch, holder)
        rotations = [0]
        quoted: dict = {}

        def tape_row(token, price):
            return {"transactionHash": f"0xfill-{token}", "asset": token,
                    "timestamp": int(time.time()), "price": price,
                    "size": 100000.0, "side": "SELL"}

        def sleep_fn(seconds):
            rotations[0] += 1
            if rotations[0] == 1:
                # Rotation 1 quoted into an empty tape. Arm SELLs at the
                # exact resting prices and sizes the run chose.
                reg = OrderRegistry(db_path=db)
                for o in reg.get_all_orders():
                    quoted[o["token_id"]] = (o["price"], o["original_size"])
                assert len(quoted) == 2, f"rotation 1 must quote both legs: {quoted}"
                holder["taker"] = {
                    0: [tape_row(tok, px) for tok, (px, _sz) in quoted.items()]}
            if rotations[0] >= 4:
                raise KeyboardInterrupt

        result = run_shadow(
            minutes=5.0, db_path=db,
            markets_fn=lambda max_markets=None: [FakeMarket(self.COND)],
            client_fn=lambda: object(),
            fetch_books=self._books,
            sleep_fn=sleep_fn,
        )
        assert rotations[0] == 4

        reg = OrderRegistry(db_path=db)
        # Paper fills landed on both legs, straight off the fills ledger --
        # the full quoted size on each side.
        assert _filled_by_token(reg, self.COND) == {
            "tok-up": pytest.approx(quoted["tok-up"][1]),
            "tok-dn": pytest.approx(quoted["tok-dn"][1])}
        # One shadow_merge close removed both legs from inventory.
        merges = [c for c in reg.get_all_closes()
                  if c["method"] == "shadow_merge"]
        assert len(merges) == 1
        first_pair = merges[0]["tx_hash"]
        assert merges[0]["shares"] == pytest.approx(quoted["tok-up"][1])
        # A later rotation placed fresh orders for the same condition id:
        # exactly one resting pair, a different pair id, no duplicates.
        resting = [o for o in reg.get_all_orders() if o["status"] == "open"]
        assert len(resting) == 2
        assert {o["pair_id"] for o in resting} != {first_pair}
        assert len({o["pair_id"] for o in resting}) == 1
        assert len({(o["token_id"], o["price"]) for o in resting}) == 2
        # The run quoted for real -- intents were decided, not stubbed.
        assert result.intents, "the rehearsal must decide real intents"
        by_pair: dict[str, float] = {}
        for qi in result.intents:
            by_pair[qi.condition_id] = by_pair.get(qi.condition_id, 0.0) + 1
        assert by_pair.get(self.COND, 0) >= 2


class TestResolvedStaysQuietAcrossConsumers:
    """#402 T5: after a mid-run resolution, no consumer reads dead books."""

    DEAD = "0xdeadcyc"
    LIVE = "0xlivecyc"

    class _Mkt:
        def __init__(self, cid):
            self.condition_id = cid
            self.up_token = f"tok-up-{cid}"
            self.down_token = f"tok-dn-{cid}"
            self.market_slug = f"fake-{cid}"
            self.tick_size = 0.01
            self.neg_risk = False

        def t_remaining(self, now=None):
            return 14400.0

    def test_no_book_reads_for_resolved_tokens_after_recording(
            self, tmp_path, monkeypatch):
        import time
        from core_brain import markets as markets_mod
        from core_brain.order_registry import OrderRegistry, ResolutionRecord
        from core_brain.shadow_run import run_shadow

        db = tmp_path / "shadow.db"
        holder = {"taker": {0: []}, "maker": {0: []}}

        def _get(url, params=None, **kw):
            params = dict(params or {})
            lim = int(params.get("limit", 500)) or 500
            idx = int(params.get("offset", 0)) // lim
            if params.get("takerOnly", "default") is False:
                return _FakeTapeResponse(holder["maker"].get(idx, []))
            return _FakeTapeResponse(holder["taker"].get(idx, []))

        monkeypatch.setattr(markets_mod._SESSION, "get", _get, raising=False)

        book_calls: list[tuple[int, str]] = []
        rotations = [0]

        def books(host, token):
            book_calls.append((rotations[0], token))
            return {"token_id": token, "best_bid": 0.47, "best_ask": 0.49,
                    "bids": {0.47: 500.0}, "asks": {0.49: 500.0},
                    "malformed": 0}

        direct_reads: list[str] = []

        def forbidden_book(host, token):
            direct_reads.append(token)
            raise AssertionError(f"zero-book rule broken for {token}")

        monkeypatch.setattr(markets_mod, "full_book", forbidden_book)
        monkeypatch.setattr(
            markets_mod, "fetch_pinned_market",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("terminal-first must not fetch")))

        armed = [False]

        def sleep_fn(seconds):
            rotations[0] += 1
            if rotations[0] == 1:
                reg = OrderRegistry(db_path=db)
                rows = [{"transactionHash": f"0xfill-{o['token_id']}",
                         "asset": o["token_id"],
                         "timestamp": int(time.time()), "price": o["price"],
                         "size": 100000.0, "side": "SELL"}
                        for o in reg.get_all_orders()
                        if o["condition_id"] == self.DEAD]
                assert len(rows) == 2, "dead market must quote both legs first"
                holder["taker"] = {0: rows}
            if rotations[0] == 2 and not armed[0]:
                armed[0] = True
                reg = OrderRegistry(db_path=db)
                reg.log_resolution(ResolutionRecord(
                    condition_id=self.DEAD, winning_token="Up",
                    resolved_ts=time.time(), run_id="t5-test",
                    winning_token_id=f"tok-up-{self.DEAD}"))
            if rotations[0] >= 4:
                raise KeyboardInterrupt

        run_shadow(
            minutes=5.0, db_path=db,
            markets_fn=lambda max_markets=None: [
                self._Mkt(self.DEAD), self._Mkt(self.LIVE)],
            client_fn=lambda: object(),
            fetch_books=books,
            sleep_fn=sleep_fn,
        )
        assert rotations[0] == 4

        dead_toks = {f"tok-up-{self.DEAD}", f"tok-dn-{self.DEAD}"}
        live_toks = {f"tok-up-{self.LIVE}", f"tok-dn-{self.LIVE}"}
        # Dead tokens were read while live (rotations 0-1) and never after.
        assert any(r <= 1 and t in dead_toks for r, t in book_calls)
        assert not any(r >= 2 and t in dead_toks for r, t in book_calls)
        # The unrelated live market kept quoting on every rotation.
        live_rots = {r for r, t in book_calls if t in live_toks}
        assert live_rots == {0, 1, 2, 3}
        # No consumer reached past the seam for a direct read.
        assert direct_reads == []

        reg = OrderRegistry(db_path=db)
        assert len([r for r in reg.get_all_market_events()
                    if r["kind"] == "lifecycle_stop"
                    and r["reason_code"] == "resolved"]) >= 1
