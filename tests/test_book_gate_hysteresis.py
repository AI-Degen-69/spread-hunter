"""The book-readiness gates in `visit` must not act on a 1-2s venue blip.

A live match's depth regularly dips under the $1K bar for a single poll and
refills a second later. Before the confirmation window (BOOK_GATE_CONFIRM_SEC),
that dip made the fleet cancel its resting orders, blank the last-known bids
and stamp STALE/ERROR on the dashboard's next poll -- then re-quote and clear
it on the one after. The dashboard read the blanked bids as a $0 exit and
flashed a fake full-cost unrealized loss every few seconds (the LoL
SK Gaming vs Natus Vincere market did exactly this on a ~5s cycle, per the
incident report).

The window holds: the first failure records the time and returns early with
orders and marks untouched; only a failure persisting past the window gets the
full cancel+stamp treatment. A recovered book resets the clock.
"""
from strategy.config import load as load_cfg
from strategy.fleet import MarketState, visit
from strategy.sweep import BOOK_GATE_CONFIRM_SEC


def _spec(cid="cond-1"):
    return {"cid": cid, "title": "Test Market", "slug": "test-mkt",
            "daily": 0.0, "min_size": 5, "max_spread": 4.5, "tick": 0.01,
            "shares": 120, "volume_24h": 100_000.0, "days_to_resolve": 1.0,
            "est_income": 0.0, "est_capital": 120.0, "return_pct_day": 0.0,
            "their_score": 100.0, "spread": 0.01}


class _Market:
    """Minimal stand-in for fetch_pinned_market output: tokens + ids only."""

    def __init__(self, up_token="tok-up", dn_token="tok-dn"):
        self.up_token = up_token
        self.down_token = dn_token
        self.condition_id = "cond-1"
        self.market_slug = "test-mkt"


def _deep_book(token="tok-up"):
    """A side that clears the $1K top-3 depth bar and the spread bar."""
    return {"bids": {0.48: 5000.0, 0.47: 4000.0, 0.46: 3000.0},
            "asks": {0.50: 8000.0}, "best_bid": 0.48, "best_ask": 0.50,
            "token_id": token}


def _shallow_book(token="tok-dn"):
    """The incident shape: NO-side top-3 depth collapses to pocket change."""
    return {"bids": {0.50: 100.0}, "asks": {0.52: 100.0},
            "best_bid": 0.50, "best_ask": 0.52, "token_id": token}


class _BotCfg:
    """Just enough for `visit`'s book fetches: a clob host attribute."""

    clob_host = "http://clob.test"


def _make_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "hyst.db"))
    st = MarketState(_spec(), load_cfg())
    st.market = _Market()
    # Last-known-good payload: what a successful visit wrote before the blip.
    st.spec["_live"] = {"err": "", "ts": 90.0, "up_bid": 0.48, "up_ask": 0.50,
                        "dn_bid": 0.50, "dn_bid_as_up": 0.50, "stale": False,
                        "quotes": ["q-1"], "capital": 66.0}
    # A resting order the blip must not cancel.
    st.engine.post("tok-dn", "DOWN", 0.50, 107.0,
                   book_bids={0.50: 0.0}, ts=90.0)
    return st


def _books_yes_deep(host, token):
    return _deep_book("tok-up") if token == "tok-up" else _shallow_book("tok-dn")


def test_a_transient_depth_dip_does_not_cancel_or_stamp(monkeypatch, tmp_path):
    """THE BUG, STATED AS A TEST. A single failing visit within the window
    must leave orders resting, marks intact and no error stamped -- the
    dashboard then keeps showing live figures instead of flashing STALE/ERROR
    and a fake full-cost loss on every venue blip."""
    st = _make_state(monkeypatch, tmp_path)
    monkeypatch.setattr("strategy.sweep.full_book", _books_yes_deep)

    visit(st, bot_cfg=_BotCfg(), now=100.0)

    assert st.err == ""                       # no error stamped
    assert st.spec["_live"]["err"] == ""
    assert st.spec["_live"]["up_bid"] == 0.48  # last-known marks intact
    assert st.spec["_live"]["stale"] is False
    assert st.spec["_live"]["capital"] == 66.0
    assert st.book_gate_fail_since == 100.0   # clock started, not acted on
    orders = st.engine.open_orders()
    assert len(orders) == 1 and not orders[0].cancelled


def test_a_persistent_depth_failure_past_the_window_cancels_and_stamps(
        monkeypatch, tmp_path):
    """A book that is genuinely gone must still get the full treatment -- the
    protection the depth gate exists for -- once the failure outlives the
    confirmation window."""
    st = _make_state(monkeypatch, tmp_path)
    monkeypatch.setattr("strategy.sweep.full_book", _books_yes_deep)

    visit(st, bot_cfg=_BotCfg(), now=100.0)
    assert st.err == ""

    visit(st, bot_cfg=_BotCfg(), now=100.0 + BOOK_GATE_CONFIRM_SEC + 0.01)

    assert "NO: top-3 bid depth" in st.err
    live = st.spec["_live"]
    assert live["err"] == st.err
    assert live["stale"] is True
    assert live["up_bid"] is None            # marks blanked: can't exit now
    assert live["capital"] == 0.0
    assert all(o.cancelled for o in st.engine.open_orders())


def test_a_recovered_book_resets_the_confirmation_clock(monkeypatch, tmp_path):
    """A blip that recovers must not accumulate toward a false confirmation:
    the next successful visit clears the clock and quotes again."""
    st = _make_state(monkeypatch, tmp_path)
    monkeypatch.setattr("strategy.sweep.recent_trades", lambda *a, **k: {})
    monkeypatch.setattr("strategy.sweep.full_book", _books_yes_deep)

    visit(st, bot_cfg=_BotCfg(), now=100.0)
    assert st.book_gate_fail_since == 100.0

    # Book recovers one rotation later: both sides deep now.
    monkeypatch.setattr("strategy.sweep.full_book",
                        lambda host, token: _deep_book(token))
    visit(st, bot_cfg=_BotCfg(), now=101.0)

    assert st.book_gate_fail_since is None
    assert st.err == ""
    assert st.spec["_live"]["err"] == ""
    assert st.spec["_live"]["up_bid"] == 0.48   # fresh marks written again


def test_a_transient_fetch_failure_also_holds_then_fires(monkeypatch,
                                                         tmp_path):
    """The same blip logic protects the book-fetch path: one venue timeout
    must not cancel a healthy market's quotes, but a persistent one must."""
    st = _make_state(monkeypatch, tmp_path)

    def _boom(host, token):
        raise ConnectionError("venue timeout")
    monkeypatch.setattr("strategy.sweep.full_book", _boom)

    visit(st, bot_cfg=_BotCfg(), now=100.0)
    assert st.err == ""
    assert st.spec["_live"]["up_bid"] == 0.48
    assert not st.engine.open_orders()[0].cancelled

    visit(st, bot_cfg=_BotCfg(), now=100.0 + BOOK_GATE_CONFIRM_SEC + 0.01)
    assert "book fetch" in st.err
    assert st.spec["_live"]["err"] == st.err
    assert st.spec["_live"]["up_bid"] is None
    assert all(o.cancelled for o in st.engine.open_orders())


def test_confirmed_failure_then_recovery_requotes(monkeypatch, tmp_path):
    """End to end: persistent failure fires the cancel+stamp; when the book
    comes back, the market requotes and clears its error."""
    st = _make_state(monkeypatch, tmp_path)
    monkeypatch.setattr("strategy.sweep.recent_trades", lambda *a, **k: {})
    monkeypatch.setattr("strategy.sweep.full_book", _books_yes_deep)

    visit(st, bot_cfg=_BotCfg(), now=100.0)
    visit(st, bot_cfg=_BotCfg(), now=100.0 + BOOK_GATE_CONFIRM_SEC + 0.01)
    assert "NO: top-3 bid depth" in st.err

    monkeypatch.setattr("strategy.sweep.full_book",
                        lambda host, token: _deep_book(token))
    visit(st, bot_cfg=_BotCfg(), now=200.0)

    assert st.err == ""
    assert st.spec["_live"]["err"] == ""
    assert st.spec["_live"]["stale"] is False
    assert st.book_gate_fail_since is None
