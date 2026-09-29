"""The aged-out rescue (Issue #311): a one-sided leg past the rescue window.

`pairs_exit_window_sec` is a discovery filter, not a deadline. A naked leg older
than it fell out of `auto_manage_pairs` and nothing else closed it, so it sat
unmanaged until settlement. These tests pin the decision that replaces that gap:
a market-end-aware deadline, computed by one pure function, failing closed on
anything it cannot read.

No network, no venue, no clock: the verdict is a pure function of its inputs,
and the store-driven pass gets its own tests further down.
"""
from pathlib import Path
import time
import uuid
import pytest

from core_brain.config import MakerConfig
from core_brain import single_buy_saver as lp
from core_brain.market_resolution import MarketEndState
from core_brain.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, CloseRecord, QuoteRecord, init_db,
)
from core_brain.single_buy_saver import rescue_aged_out_legs

WINDOW_MS = 900_000          # pairs_exit_window_sec = 900.0
LEAD_SEC = 900.0             # aged_out_rescue_lead_sec
NOW_S = 1_800_000_000.0      # an arbitrary, fixed instant
WINDOW_SEC = WINDOW_MS / 1000.0


def _verdict(*, age_sec: float = 3600.0, end_in_sec: float = 7200.0,
             closed=False, accepting=True, lead_sec: float = LEAD_SEC,
             last_fill_ms: int | None = None):
    """The verdict for a leg `age_sec` old, on a market ending in `end_in_sec`."""
    if last_fill_ms is None:
        last_fill_ms = int((NOW_S - age_sec) * 1000.0)
    end_ts = None if end_in_sec is None else NOW_S + end_in_sec
    return lp.aged_out_verdict(
        last_fill_ms=last_fill_ms, window_ms=WINDOW_MS, now_s=NOW_S,
        end_ts=end_ts, lead_sec=lead_sec,
        venue_closed=closed, venue_accepting=accepting,
    )


# --- 1. the window keeps its meaning --------------------------------------

def test_an_undated_fill_is_left_alone():
    # Arrange / Act - no venue_ts, so the fill cannot be placed in time.
    verdict, reason = _verdict(last_fill_ms=0)

    # Assert
    assert verdict == "not_aged_out"
    assert "undated" in reason


def test_a_fill_inside_the_window_keeps_the_rescue_route():
    # Arrange / Act - 300s old against a 900s window.
    verdict, reason = _verdict(age_sec=300.0)

    # Assert - the in-window route order owns this leg, not the new arm.
    assert verdict == "not_aged_out"
    assert "inside window" in reason


def test_a_fill_exactly_at_the_window_edge_is_not_aged_out():
    # Arrange / Act - the existing discovery filter compares with `>`.
    verdict, _ = _verdict(age_sec=WINDOW_SEC)

    # Assert
    assert verdict == "not_aged_out"


def test_a_fill_past_the_window_is_aged_out():
    # Arrange / Act
    verdict, reason = _verdict(age_sec=WINDOW_SEC + 1.0)

    # Assert - aged out, but not yet due: the market ends in two hours.
    assert verdict == "awaiting_lead"
    assert f"{WINDOW_SEC:.0f}s" in reason


# --- 2. fail closed on anything unreadable --------------------------------

def test_an_unreadable_venue_state_never_invents_a_deadline():
    # Arrange / Act / Assert - a missing boolean is not "false", never live.
    for closed, accepting in ((None, True), (False, None), (None, None)):
        verdict, reason = _verdict(closed=closed, accepting=accepting)
        assert verdict == "end_unknown", (closed, accepting)
        assert "unreadable" in reason


def test_an_unreadable_end_time_leaves_the_leg_naked():
    # Arrange / Act - the venue is open but reports no end to measure against.
    verdict, reason = _verdict(end_in_sec=None)

    # Assert
    assert verdict == "end_unknown"
    assert "unreadable" in reason


def test_a_market_the_venue_already_closed_is_left_to_settlement():
    # Arrange / Act / Assert - selling into a closed market is not available,
    # and the resolution path owns the position.
    for closed, accepting in ((True, True), (True, False), (False, False)):
        verdict, reason = _verdict(closed=closed, accepting=accepting)
        assert verdict == "venue_closed", (closed, accepting)
        assert "closed" in reason


# --- 3. the deadline is market-end aware ----------------------------------

def test_a_future_end_outside_the_lead_is_waited_out():
    # Arrange / Act - ends in 2h, lead is 15min, so 105 minutes remain.
    verdict, reason = _verdict(end_in_sec=7200.0)

    # Assert
    assert verdict == "awaiting_lead"
    assert "lead" in reason


def test_entering_the_lead_window_before_the_end_is_due():
    # Arrange / Act - 600s to the end, lead is 900s.
    verdict, reason = _verdict(end_in_sec=600.0)

    # Assert
    assert verdict == "due"
    assert "600s" in reason


def test_a_stated_end_already_past_while_the_venue_still_accepts_is_due():
    # Arrange - the sports case from #312: `endDate` is the kickoff, so a live
    # in-play market carries an end that has already passed while the venue is
    # still taking orders. There is no future end to wait for.
    verdict, reason = _verdict(end_in_sec=-3600.0)

    # Assert
    assert verdict == "due"
    assert "passed" in reason


def test_the_lead_window_is_measured_against_the_end_not_the_clock():
    # Arrange / Act - same leg and market, two different leads.
    soon, _ = _verdict(end_in_sec=1200.0, lead_sec=3600.0)
    later, _ = _verdict(end_in_sec=1200.0, lead_sec=900.0)

    # Assert
    assert soon == "due"
    assert later == "awaiting_lead"


def test_a_due_reason_names_the_age_the_window_and_the_lead():
    # Arrange / Act - the #312 legibility rule: a refusal or an action carries
    # the values behind it, never one identical string for every row.
    verdict, reason = _verdict(age_sec=10_452.0, end_in_sec=600.0)

    # Assert
    assert verdict == "due"
    assert "10452s" in reason
    assert f"{WINDOW_SEC:.0f}s" in reason
    assert f"{LEAD_SEC:.0f}s" in reason


# --- 4. the knobs ---------------------------------------------------------

def test_the_new_knobs_are_on_by_default():
    # Arrange / Act
    cfg = MakerConfig()

    # Assert
    assert cfg.enable_aged_out_rescue is True
    assert cfg.aged_out_rescue_lead_sec == 900.0


def test_no_existing_default_moved():
    # Arrange - the issue forbids touching any shipped default.
    cfg = MakerConfig()

    # Act / Assert
    assert cfg.pairs_exit_window_sec == 900.0
    assert cfg.max_pair_cost == 0.99
    assert cfg.single_buy_max_loss_pct == 0.10
    assert cfg.single_buy_max_loss_usd == 0.045
    assert cfg.single_buy_grace_sec == 0.0
    assert cfg.enable_pairs_rule is True


def test_the_lead_knob_has_an_env_override(monkeypatch):
    # Arrange - the operator can retune the deadline without a code change.
    monkeypatch.setenv("HUNTER_AGED_OUT_RESCUE_LEAD_SEC", "120")

    from core_brain.config import load

    # Act / Assert
    assert load().aged_out_rescue_lead_sec == 120.0


def test_a_negative_lead_override_is_refused(monkeypatch):
    # Arrange - a negative lead would fire the deadline after the market ended.
    monkeypatch.setenv("HUNTER_AGED_OUT_RESCUE_LEAD_SEC", "-5")

    from core_brain.config import load

    # Act / Assert
    with pytest.raises(ValueError):
        load()


# ===========================================================================
# The pass itself: a real tmp registry, a fake venue, a fake market read.
# ===========================================================================

COND = "0xcond-aged"
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
MAX_PAIR_COST = 0.995
# A fill two hours old: past the 900s window by a wide margin.
AGE_SEC = 7200.0
OLD_FILL_TS_MS = int((NOW_S - AGE_SEC) * 1000.0)


@pytest.fixture
def registry(tmp_path: Path):
    registry = OrderRegistry(db_path=tmp_path / "live.db")
    init_db(tmp_path / "live.db")
    return registry


def _cfg(**kw) -> MakerConfig:
    base = dict(enable_pairs_rule=True, pairs_exit_window_sec=900.0,
                enable_aged_out_rescue=True, aged_out_rescue_lead_sec=LEAD_SEC,
                max_pair_cost=MAX_PAIR_COST)
    base.update(kw)
    return MakerConfig(**base)


def _naked_pair(registry, *, fill_price: float = 0.60,
                venue_ts: int = OLD_FILL_TS_MS, pair_id: str = "pair-aged",
                cond: str = COND) -> str:
    """A heavy UP leg filled `venue_ts`, a light DOWN leg still resting."""
    now = OLD_FILL_TS_MS

    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id=f"venue-heavy-{pair_id}",
        condition_id=cond, token_id=TOK_UP, side="BUY", price=fill_price,
        original_size=10.0, status="filled",
        posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(heavy)
    registry.record_fill(FillRecord(
        trade_id=f"trade-{pair_id}-h", order_uuid=heavy.id, size=10.0,
        price=fill_price, venue_ts=venue_ts,
    ))
    light = OrderRecord(
        id=str(uuid.uuid4()), order_id=f"venue-light-{pair_id}",
        condition_id=cond, token_id=TOK_DN, side="BUY", price=0.38,
        original_size=10.0, status="open",
        posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=cond, token_id=TOK_UP, side="UP",
        price=fill_price, size=10.0))
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=cond, token_id=TOK_DN, side="DOWN",
        price=0.38, size=10.0))
    return pair_id


class FakeClient:
    """The four venue calls the saver makes, recorded. No network."""

    def __init__(self, best_ask=0.45, best_bid=0.55):
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.calls: list[str] = []
        self.orders: list[dict] = []

    def get_order_book(self, token_id):
        self.calls.append(f"book:{token_id}")
        asks = ([] if self.best_ask is None
                else [{"price": str(self.best_ask), "size": "100"}])
        return {"asset_id": token_id,
                "bids": [{"price": str(self.best_bid), "size": "100"}],
                "asks": asks, "tick_size": "0.01"}

    def get_order(self, order_id):
        self.calls.append(f"get_order:{order_id}")
        return {"orderID": order_id, "size_matched": 0.0}

    def cancel_order(self, payload):
        self.calls.append(f"cancel:{getattr(payload, 'orderID', payload)}")
        return {"canceled": ["venue-light"]}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(
            f"{verb}:{order_args.token_id}:{order_args.side}")
        self.orders.append({"side": order_args.side,
                            "token_id": order_args.token_id})
        return {"success": True, "orderID": f"venue-{verb}"}


def _open_state(end_ts=NOW_S + 600.0, closed=False, accepting=True):
    return MarketEndState(condition_id=COND, closed=closed,
                          end_ts=end_ts, end_date_iso=None,
                          end_date_passed=False, accepting_orders=accepting,
                          resolved=False, unreachable=False)


def _states(state):
    """A `market_state_fn` returning a fixed state for every condition."""
    def fn(condition_id):
        return state
    return fn


# --- 5. the pass acts before the market ends ------------------------------

def test_an_aged_out_leg_inside_the_lead_is_sold_with_its_own_reason(registry):
    # Arrange - 2h old, market ends in 10min, the pair completes over the cap
    # (0.60 + 0.45 = 1.05), so the exit owns it.
    _naked_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.45, best_bid=0.55)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=_states(_open_state()))

    # Assert
    assert len(results) == 1
    assert results[0]["action"] == "aged_out_rescue"
    assert results[0]["route"] == "exited"
    assert any(c.startswith("sell:") for c in client.calls)
    closes = registry.get_all_closes()
    assert len(closes) == 1
    assert closes[0]["reason"] == "aged_out_rescue"
    assert closes[0]["method"] == "single_buy_exit"


def test_a_pair_that_still_completes_under_the_cap_is_completed_not_dumped(registry):
    # Arrange - 0.50 fill + 0.40 ask = 0.90, under the 0.995 cap: the strategy
    # would rather merge at parity than sell the leg into the bid.
    _naked_pair(registry, fill_price=0.50)
    client = FakeClient(best_ask=0.40, best_bid=0.55)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=_states(_open_state()))

    # Assert
    assert results[0]["route"] == "completed"
    assert any(c.startswith("buy:") for c in client.calls)
    assert not any(c.startswith("sell:") for c in client.calls)


def test_the_stated_end_already_passed_while_the_venue_accepts_is_due_now(registry):
    # Arrange - the sports case from #312: `endDate` is the kickoff.
    _naked_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.45)

    # Act
    results = rescue_aged_out_legs(
        client, registry, _cfg(), now=NOW_S,
        market_state_fn=_states(_open_state(end_ts=NOW_S - 3600.0)))

    # Assert
    assert results[0]["action"] == "aged_out_rescue"
    assert "passed" in results[0]["reason"]


# --- 6. fail closed -------------------------------------------------------

def test_an_unreadable_end_leaves_the_leg_naked_and_retries(registry):
    # Arrange - the venue's open listing does not carry the market.
    _naked_pair(registry, fill_price=0.60)
    client = FakeClient()

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=_states(None))

    # Assert - nothing sent, nothing recorded, and the read is retried next
    # rotation because the leg stays in the discovery set.
    assert results[0]["action"] == "end_unknown"
    assert client.calls == []
    assert registry.get_all_closes() == []


def test_an_unreachable_read_leaves_the_leg_naked_and_retries(registry):
    # Arrange
    _naked_pair(registry, fill_price=0.60)
    client = FakeClient()
    unreachable = MarketEndState(condition_id=COND, unreachable=True)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=_states(unreachable))

    # Assert
    assert results[0]["action"] == "end_unknown"
    assert client.calls == []


def test_a_market_the_venue_closed_is_left_to_settlement(registry):
    # Arrange
    _naked_pair(registry, fill_price=0.60)
    client = FakeClient()
    closed = _open_state(closed=True, accepting=False)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=_states(closed))

    # Assert
    assert results[0]["action"] == "venue_closed"
    assert client.calls == []
    assert registry.get_all_closes() == []


def test_a_future_end_outside_the_lead_waits(registry):
    # Arrange - ends in 6h, lead is 15min.
    _naked_pair(registry, fill_price=0.60)
    client = FakeClient()

    # Act
    results = rescue_aged_out_legs(
        client, registry, _cfg(), now=NOW_S,
        market_state_fn=_states(_open_state(end_ts=NOW_S + 21_600.0)))

    # Assert
    assert results[0]["action"] == "awaiting_lead"
    assert client.calls == []


# --- 7. the window's own behaviour is untouched ---------------------------

def test_a_leg_inside_the_window_belongs_to_the_in_window_pass(registry):
    # Arrange - a 300s-old fill: still the U35 route's business.
    _naked_pair(registry, venue_ts=int((NOW_S - 300.0) * 1000.0))
    client = FakeClient(best_ask=0.45)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=_states(_open_state()))

    # Assert - the arm reports nothing for it and sends nothing.
    assert results == []
    assert client.calls == []


def test_a_leg_already_closed_after_its_fill_is_never_sold_again(registry):
    # Arrange - a close covers only the fills that predate it; this fill does.
    pid = _naked_pair(registry, fill_price=0.60)
    registry.log_close(CloseRecord(
        ts=NOW_S - 60.0, condition_id=COND, method="single_buy_exit",
        shares=10.0, cost_basis=6.0, proceeds=5.5, realized_pnl=-0.5,
        reason="grace_expired", run_id="run-a"))
    client = FakeClient(best_ask=0.45)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=_states(_open_state()))

    # Assert - no sell of a leg the ledger says is already gone.
    assert results == []
    assert not any(c.startswith("sell:") for c in client.calls)
    assert len(registry.get_all_closes()) == 1
    assert pid


def test_a_fresh_fill_after_a_close_is_new_exposure_and_is_managed(registry):
    # Arrange - the close predates the fill, so it covers nothing here.
    _naked_pair(registry, fill_price=0.60)
    registry.log_close(CloseRecord(
        ts=NOW_S - 10_000.0, condition_id=COND, method="merge", shares=10.0,
        cost_basis=6.0, proceeds=10.0, realized_pnl=4.0, run_id="run-a"))
    client = FakeClient(best_ask=0.45)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=_states(_open_state()))

    # Assert
    assert results[0]["action"] == "aged_out_rescue"


def test_the_knob_off_means_off_and_no_market_read(registry):
    # Arrange
    _naked_pair(registry, fill_price=0.60)
    client = FakeClient()
    reads: list[str] = []

    def fn(condition_id):
        reads.append(condition_id)
        return _open_state()

    # Act
    results = rescue_aged_out_legs(
        client, registry, _cfg(enable_aged_out_rescue=False), now=NOW_S,
        market_state_fn=fn)

    # Assert
    assert results == []
    assert reads == []
    assert client.calls == []


def test_a_dry_run_sends_nothing(registry):
    # Arrange
    _naked_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.45)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), live=False,
                                   now=NOW_S,
                                   market_state_fn=_states(_open_state()))

    # Assert
    assert results[0]["action"] == "would_exit"
    assert not any(c.startswith(("buy:", "sell:", "cancel:"))
                   for c in client.calls)
    assert registry.get_all_closes() == []


def test_one_failing_pair_does_not_stop_the_others(registry):
    # Arrange - one healthy aged-out pair, one whose market read raises.
    _naked_pair(registry, fill_price=0.60, pair_id="pair-good")
    _naked_pair(registry, fill_price=0.60, pair_id="pair-bad", cond="0xcond-bad")
    client = FakeClient(best_ask=0.45)

    def fn(condition_id):
        if condition_id == "0xcond-bad":
            raise RuntimeError("gamma exploded")
        return _open_state()

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                   market_state_fn=fn)

    # Assert
    actions = {r["pair_id"]: r["action"] for r in results}
    assert actions["pair-good"] == "aged_out_rescue"
    assert actions["pair-bad"] == "error"


def test_a_positions_read_failure_fails_the_pass_closed(registry, monkeypatch):
    # Arrange - the same pre-flight `auto_manage_pairs` uses: never sell a size
    # the venue does not agree we hold.
    _naked_pair(registry, fill_price=0.60)

    def boom(funder):
        raise RuntimeError("data api down")

    monkeypatch.setattr(lp, "fetch_positions", boom)
    client = FakeClient(best_ask=0.45)

    # Act
    results = rescue_aged_out_legs(client, registry, _cfg(), live=True,
                                   funder="0xfunder", now=NOW_S,
                                   market_state_fn=_states(_open_state()))

    # Assert
    assert results and results[0]["action"] == "error"
    assert client.calls == []


# --- 8. the shadow sweep runs the arm -------------------------------------

def test_the_shadow_sweep_runs_the_aged_out_arm(tmp_path):
    """`shadow_sweep` must call the arm, or a rehearsal measures nothing.

    An old naked pair is seeded directly into a shadow store (a fresh fill
    would belong to the in-window pass), the quoting loop is starved of
    markets so nothing else can act, and the market state is injected so no
    test reaches the network. Completions are read off the fills ledger.
    """
    import sqlite3 as _sqlite3

    from core_brain.config import load
    from core_brain.order_registry import init_db as _init
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit, settle_market
    from core_brain.shadow_run import run_shadow
    from core_brain.quotes import QuoteIntent

    class _Market:
        condition_id = "0xaged-shadow"

    db = tmp_path / "shadow.db"
    _init(db)
    reg = OrderRegistry(db_path=db)
    ensure_shadow_tables(db)
    intents = [
        QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                    mid=0.5, edge_vs_mid=0.0),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=20,
                    mid=0.5, edge_vs_mid=0.0),
    ]
    record_submit(object(), reg, _Market(), intents, load(), db_path=db,
                  book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, _Market(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    # Age the fill past the rescue window, in the store only. Measured against
    # the real clock: the sweep runs on `time.time()`, so a fill dated with a
    # synthetic "now" in the future would read as fresh to the U35 pass.
    wall_now = time.time()
    with _sqlite3.connect(db) as conn:
        conn.execute("UPDATE fills SET venue_ts = ?",
                     (int((wall_now - AGE_SEC) * 1000.0),))

    def book(_host, token_id):
        return {"token_id": token_id, "bids": {0.50: 500.0},
                "asks": {0.51: 500.0}, "best_bid": 0.50, "best_ask": 0.51}

    run_shadow(
        minutes=0.0, db_path=db,
        markets_fn=lambda max_markets=None: [],
        client_fn=lambda: object(),
        fetch_books=book,
        market_state_fn=lambda cid: _open_state(end_ts=wall_now + 600.0),
    )

    # Assert - the light leg was bought, which only the aged-out arm can do
    # (the U35 pass skips a fill this old), and the pair was merged flat.
    fills = {}
    for row in reg.get_all_fills():
        order = [o for o in reg.get_all_orders() if o["id"] == row["order_uuid"]]
        if order:
            fills[order[0]["token_id"]] = fills.get(order[0]["token_id"], 0.0) \
                + float(row["size"] or 0.0)
    assert fills.get("tok-dn", 0.0) > 0.0, (
        "the shadow sweep did not run the aged-out arm")


def test_a_balanced_pair_is_not_aged_out(registry):
    # Arrange - both legs filled: nothing naked to rescue.
    pid = _naked_pair(registry, fill_price=0.50)
    light_token = TOK_DN
    light = [o for o in registry.get_orders_by_pair(pid)
             if o.token_id == light_token][0]
    registry.record_fill(FillRecord(
        trade_id="trade-light", order_uuid=light.id, size=10.0,
        price=0.38, venue_ts=OLD_FILL_TS_MS))
    client = FakeClient()

    # Act / Assert
    assert rescue_aged_out_legs(client, registry, _cfg(), now=NOW_S,
                                market_state_fn=_states(_open_state())) == []
