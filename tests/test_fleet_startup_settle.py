"""U11 -- the startup settle pass.

`MarketState.__init__` rebuilds each market's inventory from the fills ledger,
and the fills ledger never learns about resolutions. A market that resolved
while the fleet was down therefore restarted holding phantom shares that
counted as committed capital until its first `visit` -- and if the ranker
dropped the market before that turn, the re-rank retention rule ("still
holding inventory") kept it in `states` forever on exactly the phantom
position. `_settle_startup_resolved` clears that at startup, before the first
visit.
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


def test_startup_settle_zeroes_phantom_inventory(monkeypatch, tmp_path):
    """THE BUG, STATED AS A TEST. A market with a `resolutions` row restarts
    holding shares that `_inventory_from_db` rebuilt from fills; the pass must
    zero them (and their cost) so committed capital is freed immediately."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settle.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    _seed_fill(store, side="UP", price=0.44, size=100.0)
    store.record_resolution("cond-1", "TOK-UP")

    st = fleet.MarketState(_spec(), load_cfg())
    assert st.inv.up_shares == 100.0          # phantom, rebuilt from fills
    assert st.inv.up_cost == 44.0

    settled, freed = sweep._settle_startup_resolved(
        [st], frozenset({"cond-1"}), now=2000.0)

    assert settled == 1
    assert freed == 44.0
    assert st.inv.up_shares == 0.0
    assert st.inv.down_shares == 0.0
    assert st.inv.up_cost == 0.0
    assert st.inv.down_cost == 0.0
    live = st.spec["_live"]
    assert live["naked_sh"] == 0.0
    assert live["naked_cost"] == 0.0
    assert live["err"] == ""
    assert live["ts"] == 2000.0


def test_startup_settle_leaves_unresolved_markets_alone(monkeypatch, tmp_path):
    """A market with fills but NO resolution row is still genuinely open --
    its inventory must survive the pass untouched."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settle.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    _seed_fill(store, side="UP", price=0.44, size=100.0)

    # No `record_resolution` was called, so `store.resolved_cids()` is empty
    # and the pass receives that empty set -- the market must survive.
    st = fleet.MarketState(_spec(), load_cfg())
    settled, freed = sweep._settle_startup_resolved(
        [st], frozenset(), now=2000.0)

    assert settled == 0
    assert freed == 0.0
    assert st.inv.up_shares == 100.0
    assert st.inv.up_cost == 44.0


def test_startup_settle_only_affects_resolved_members(monkeypatch, tmp_path):
    """A mixed fleet: one market resolved, one still open. Only the resolved
    one loses its inventory."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settle.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    _seed_fill(store, cond="cond-res", price=0.50, size=50.0)
    store.record_resolution("cond-res", "TOK-UP")
    _seed_fill(store, cond="cond-live", price=0.40, size=30.0)

    st_res = fleet.MarketState(_spec(cid="cond-res"), load_cfg())
    st_live = fleet.MarketState(_spec(cid="cond-live"), load_cfg())
    settled, freed = sweep._settle_startup_resolved(
        [st_res, st_live], frozenset({"cond-res"}), now=2000.0)

    assert settled == 1
    assert abs(freed - 25.0) < 1e-9
    assert st_res.inv.up_shares == 0.0
    assert st_live.inv.up_shares == 30.0
    assert st_live.inv.up_cost == 12.0


def test_startup_settle_is_idempotent_with_visit(monkeypatch, tmp_path):
    """After the startup pass clears a resolved market, the next `visit` (which
    also checks resolved_cids) must be a no-op -- no double counting, no error,
    inventory stays zero."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settle.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    _seed_fill(store, side="DOWN", price=0.30, size=40.0)
    store.record_resolution("cond-1", "TOK-UP")

    st = fleet.MarketState(_spec(), load_cfg())
    sweep._settle_startup_resolved([st], frozenset({"cond-1"}), now=1000.0)

    fleet.visit(st, bot_cfg=None, now=3000.0,
                resolved_cids=frozenset({"cond-1"}))

    assert st.inv.up_shares == 0.0
    assert st.inv.down_shares == 0.0
    assert st.inv.up_cost == 0.0
    assert st.inv.down_cost == 0.0
    live = st.spec["_live"]
    assert live["naked_sh"] == 0.0
    assert live["err"] == ""


def test_startup_settle_resolved_market_with_no_inventory_is_not_counted(
        monkeypatch, tmp_path):
    """A resolved market holding nothing has no phantom capital to free -- the
    pass still normalises its live payload but must not report it as settled."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settle.db"))
    from strategy.config import load as load_cfg
    from strategy import fleet, store, sweep

    store.record_resolution("cond-1", "TOK-UP")

    st = fleet.MarketState(_spec(), load_cfg())
    settled, freed = sweep._settle_startup_resolved(
        [st], frozenset({"cond-1"}), now=2000.0)

    assert settled == 0
    assert freed == 0.0
    assert st.inv.up_shares == 0.0
    assert st.spec["_live"]["ts"] == 2000.0


def test_startup_settle_empty_fleet_is_a_noop():
    """The pass must be safe on the empty-universe startup too -- nothing to
    settle, no error."""
    from strategy import fleet, sweep

    settled, freed = sweep._settle_startup_resolved(
        [], frozenset({"cond-1"}), now=2000.0)

    assert settled == 0
    assert freed == 0.0
