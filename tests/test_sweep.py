"""The sweep module's settle-and-cancel step (issue #11).

The per-market sweep in `strategy.fleet.visit` used to own settle/cancel
inline; the step now lives behind `strategy.sweep`'s interface so it can be
driven directly: feed a market state and a now, assert the outcome. The
startup-settle pass itself is covered in `test_fleet_startup_settle` through
the same interface; these tests cover the parts the startup pass does not
exercise: the mid-life settle (`settle_resolved`), the bare cancel
(`cancel_live_orders`) and the event dedup (`record_event`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _spec(cid="cond-1"):
    return {"cid": cid, "title": "Test Market", "slug": "test-mkt",
            "daily": 0.0, "min_size": 5, "max_spread": 4.5, "tick": 0.01,
            "shares": 120, "volume_24h": 100_000.0, "days_to_resolve": 1.0,
            "est_income": 0.0, "est_capital": 120.0, "return_pct_day": 0.0,
            "their_score": 100.0, "spread": 0.01}


def _seed_fill(store, cond="cond-1", slug="test-mkt", side="UP",
               price=0.44, size=100.0):
    store.log_fill(market_slug=slug, condition_id=cond, token_id="TOK-UP",
                   side=side, price=price, size=size)


def _events(store) -> int:
    with store.db() as c:
        return c.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]


def test_settle_resolved_zeroes_inventory_and_refreshes_the_payload(
        monkeypatch, tmp_path):
    """Settling a resolved market releases BOTH legs: shares, cost, and the
    dashboard payload all read zero, and the payload is FRESH (stale=False,
    ts=now) -- measured now, not stamped as a failure.
    """
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "sweep.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    _seed_fill(store, side="UP", price=0.44, size=100.0)
    _seed_fill(store, side="DOWN", price=0.50, size=100.0)
    st = fleet.MarketState(_spec(), load_cfg())
    # A resting order the settle must cancel on its way out.
    st.engine.post("TOK-UP", "UP", 0.40, 10, {}, 1000.0)
    assert st.inv.up_shares == 100.0
    assert st.inv.down_shares == 100.0

    freed = sweep.settle_resolved(st, now=2000.0)

    assert freed == 94.0, "the released cost (44 + 50) is the outcome"
    assert st.inv.up_shares == 0.0 and st.inv.down_shares == 0.0
    assert st.inv.up_cost == 0.0 and st.inv.down_cost == 0.0
    assert st.engine.open_orders() == [], "resting quotes must be cancelled"
    live = st.spec["_live"]
    assert live["stale"] is False, "settled figures are fresh, not stale"
    assert live["ts"] == 2000.0
    assert live["naked_sh"] == 0.0
    assert live["err"] == ""


def test_settle_resolved_records_a_resolved_event(monkeypatch, tmp_path):
    """A market holding inventory records the RESOLVED event; the zero case
    does not pretend shares were released."""
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "sweep.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    _seed_fill(store, side="UP", price=0.44, size=100.0)
    st = fleet.MarketState(_spec(), load_cfg())
    freed = sweep.settle_resolved(st, now=1000.0)
    assert _events(store) == 1
    assert freed == 44.0

    # No inventory: the settle normalises the payload, records nothing and
    # reports zero released.
    st2 = fleet.MarketState(_spec(cid="cond-2"), load_cfg())
    assert sweep.settle_resolved(st2, now=1001.0) == 0.0
    assert _events(store) == 1


def test_cancel_live_orders_blank_the_payload_and_persist(monkeypatch,
                                                          tmp_path):
    """A market losing eligibility cancels its resting offers, blanks the
    dashboard's quote fields and marks the historical rows cancelled."""
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "sweep.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    st = fleet.MarketState(_spec(), load_cfg())
    o = st.engine.post("TOK-UP", "UP", 0.40, 10, {}, 1000.0)
    o.quote_id = store.log_quote(
        market_slug="test-mkt", condition_id="cond-1", token_id="TOK-UP",
        side="UP", price=0.40, size=10, queue_ahead=0.0, mid=0.45,
        edge_vs_mid=0.05, t_remaining=1.0)
    st.spec["_live"] = {"quotes": [1], "capital": 12.0, "stale": False,
                        "up_bid": 0.44, "up_ask": 0.46, "dn_bid": 0.50,
                        "dn_ask": 0.52, "mid_up": 0.45, "our_up": 10.0,
                        "our_dn_as_up": 0.0, "dn_bid_as_up": 0.0,
                        "pair_cost": 0.95}

    sweep.cancel_live_orders(st)

    assert st.engine.open_orders() == []
    live = st.spec["_live"]
    assert live["quotes"] == []
    assert live["capital"] == 0.0
    assert live["stale"] is True
    for field in ("up_bid", "up_ask", "dn_bid", "dn_ask", "mid_up",
                  "our_up", "our_dn_as_up", "dn_bid_as_up", "pair_cost"):
        assert live[field] is None, f"{field} must be blanked"
    with store.db() as c:
        cancelled = c.execute(
            "SELECT cancelled FROM quotes WHERE id = ?",
            (o.quote_id,)).fetchone()[0]
    assert cancelled == 1, "cancelled rows must be persisted"


def test_record_event_collapses_routine_repeats_and_honours_force(
        monkeypatch, tmp_path):
    """A routine repeat inside the dedup window writes once; force writes
    through; a DIFFERENT event is not collapsed."""
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "sweep.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    st = fleet.MarketState(_spec(), load_cfg())
    sweep.record_event(st, 1000.0, "QUOTING", "resting 2 limit orders")
    n1 = _events(store)
    assert n1 == 1

    sweep.record_event(st, 1001.0, "QUOTING", "resting 2 limit orders")
    assert _events(store) == n1, "a routine repeat inside 30s must collapse"

    sweep.record_event(st, 1001.0, "QUOTING", "resting 2 limit orders",
                       force=True)
    assert _events(store) == n1 + 1, "force must write through the dedup"

    sweep.record_event(st, 1002.0, "WAITING", "no eligible quote intent")
    assert _events(store) == n1 + 2, "a different event must not collapse"
