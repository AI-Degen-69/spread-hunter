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

    def test_a_naked_pair_is_completed_by_the_sweep(self, tmp_path):
        """A single-buy pair seeded before the run is completed by the pairs
        pass `shadow_sweep` runs each rotation -- not by the quoting loop,
        which this run starves of intents (empty market list, so nothing is
        visited) to isolate the sweep as the only thing that could act.

        The completion is read off the fills ledger, not off the inventory.
        `shadow_sweep` merges every balanced pair in the same rotation that
        completes it, and a merge takes the shares back out of inventory --
        which is the point of a merge. This test previously asserted
        `up_shares == down_shares == 20` AFTER that merge, which passed only
        because the merge was invisible to `inventory_from_registry`; it
        pinned the defect. What a completion actually means is that both legs
        bought 20 shares, and that is what is asserted now.
        """
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

        assert _filled_by_token(OrderRegistry(db_path=db), "0xabc") == {
            "tok-up": pytest.approx(20.0), "tok-dn": pytest.approx(20.0)}

        # And the pair it completed was merged, so the position is flat again.
        inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
        assert inv.up_shares == pytest.approx(0.0)
        assert inv.down_shares == pytest.approx(0.0)

    def test_the_sweep_logs_the_completion(self, tmp_path, caplog):
        """`shadow_sweep` logs every non-hold/balanced outcome, mirroring the
        production U35 pass so an operator watching the run sees it act.
        """
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

        assert any("completed" in r.message for r in caplog.records)

    def test_one_fetch_books_serves_both_quoting_and_the_pairs_sweep(
            self, tmp_path):
        """The regression pin for the get_order_book book-shape bug.

        ONE canonical `fetch_books` -- `markets.parse_book`'s exact shape --
        drives BOTH consumers in a SINGLE run: `queue_ahead_at`, which reads
        the canonical price-keyed dict as the quoting path rests a fresh
        order, and `single_buy_saver._book_levels`, which reads a list of
        levels, as the sweep completes a naked pair on the same tokens off
        the same book source. The other two tests in this class isolate the
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
        # pre-seeded naked pair on condition 0xabc was completed. Read from
        # the fills ledger -- the same rotation merges the pair it completes,
        # which is exactly what takes the shares back out of inventory.
        assert _filled_by_token(reg, "0xabc") == {
            "tok-up": pytest.approx(20.0), "tok-dn": pytest.approx(20.0)}
        inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
        assert inv.up_shares == pytest.approx(0.0)


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


class TestMain:
    """The command line: `python -m core_brain.shadow_run --minutes N`."""

    def test_argument_defaults_match_the_plan(self):
        from pathlib import Path

        from core_brain.shadow_run import _parse_args

        a = _parse_args([])

        assert a.minutes == 5.0
        assert a.interval == 5.0
        assert a.db is None
        assert a.max_markets is None
        assert a.markets_path is None

    def test_markets_path_parses(self):
        from core_brain.shadow_run import _parse_args

        a = _parse_args(["--markets-path", "runtime/trials/shadow-03/markets.json"])

        assert a.markets_path == "runtime/trials/shadow-03/markets.json"

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

        monkeypatch.setattr(shadow_mod, "_default_markets_fn", no_default)
        monkeypatch.setattr(loop_mod, "_fetch_market",
                            lambda cid: FakeMarket(cid))

        quoted = []

        def decide(cfg, up, dn, inv, t_rem, wf):
            quoted.append(getattr(up, "condition_id", "?"))
            return ([], "declined")

        with caplog.at_level(logging.WARNING, logger="shadow_run"):
            rc = main(
                ["--minutes", "0", "--db", str(tmp_path / "shadow.db"),
                 "--markets-path", str(feed)],
                client_fn=lambda: object(),
                decide_fn=decide,
                fetch_books=_books,
            )

        assert rc == 0
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
        real_specs = loop_mod._market_specs

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

    def test_the_superseded_order_stops_resting(self, tmp_path, monkeypatch):
        """An order the loop decided to replace must not keep collecting
        simulated fills at a price the live loop would have cancelled.

        The price steps here are 4c apart, OUTSIDE `requote_dead_band` (3c):
        a step inside the band is now deliberately KEPT -- that is the dead
        band working, not a supersession bug -- so this test's replacement
        scenario needs moves big enough to actually trigger a re-quote.
        """
        from core_brain.order_registry import OrderRegistry

        self._run_cycles(tmp_path, monkeypatch, cycles=3,
                         prices=[0.47, 0.52, 0.57], tape=lambda n: {})

        reg = OrderRegistry(db_path=tmp_path / "shadow.db")
        rows = [o for o in reg.get_all_orders() if o["token_id"] == "tok-up"]
        by_price = {round(float(o["price"]), 2): o["status"] for o in rows}

        assert by_price[0.47] == "cancelled"
        assert by_price[0.52] == "cancelled"
        assert by_price[0.57] == "open"
        assert [o for o in reg.get_active_orders()
                if o.token_id == "tok-up" and o.status in ("open", "partial")
                ] != [], "nothing rests at the current price"

    def test_inventory_reflects_what_settled_before_the_requote(
            self, tmp_path, monkeypatch):
        """The tape credits 5 shares against the order resting at 0.47 on the
        second cycle, and the rest of the loop acts on exactly those shares.

        Full chain across the rotation: the fill is credited to the order that
        was resting when it happened (not to the replacement), the position it
        leaves is a naked one-sided leg, and the pairs pass rescues it in the
        same rotation -- so the inventory the next decision reads is flat, and
        the close accounts for the same 5 shares that settled. Reading only
        `up_shares` here would pass on a store where nothing was credited at
        all, which is why the fill is asserted from the ledger too.
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

        closes = reg.get_all_closes()
        assert [c["shares"] for c in closes] == [pytest.approx(5.0)]
        inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
        assert inv.up_shares == pytest.approx(0.0)


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
