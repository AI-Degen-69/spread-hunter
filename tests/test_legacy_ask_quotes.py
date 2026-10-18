"""The ask branch of `decide_quotes`: measured dead, reachable, and documented.

`core_brain/quotes.decide_quotes` returns early for `objective` in
`("spread_capture", "rewards")` -- both mid-priced objectives, and every shipped
run carries `spread_capture` -- so everything below that return is reached only
by a config with another `objective`. Four knobs are read down there and nowhere
else: `ticks_below_ask` and `tick_size` (the price), `quote_window_pct` and
`enforce_quote_window` (the clock). #571's open questions asked whether anything
on the live path reads them; this file is the answer in executable form, so the
next reader does not have to re-derive it.

Nothing here is new behaviour -- the branch is unchanged. It is pinned because
the comments added to `core_brain/config.py` now claim these four knobs are
unreachable in production, and a claim like that has to fail loudly if it stops
being true.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from core_brain.config import MakerConfig
from core_brain.quotes import Inventory, decide_quotes

# Objective that reaches the ask branch. It is the one the issue calls dead:
# pricing off the ask puts every quote about half a spread above mid, so the
# pair costs 1.00 + spread by construction.
ASK = "pair"


def _books(up_ask: float = 0.52, dn_ask: float = 0.47) -> tuple[dict, dict]:
    """Two-sided books a tick wide, with the asks the branch prices under."""
    return (
        {"token_id": "tok-up", "best_bid": up_ask - 0.01, "best_ask": up_ask},
        {"token_id": "tok-dn", "best_bid": dn_ask - 0.01, "best_ask": dn_ask},
    )


def _ask_cfg(**kw) -> MakerConfig:
    return replace(MakerConfig(), objective=ASK, **kw)


def _quote(cfg, window_pct, books=None):
    up, dn = books or _books()
    return decide_quotes(cfg, up, dn, Inventory(), 3600.0, window_pct)


# --- the price: one tick under the ask, off `tick_size` -----------------------


def test_the_ask_branch_rests_one_tick_under_the_ask():
    intents, why = _quote(_ask_cfg(), window_pct=None)

    assert why == ""
    assert {i.side: i.price for i in intents} == {"UP": 0.51, "DOWN": 0.46}
    assert all("1 tick under ask" in i.reason for i in intents)


def test_the_branch_reads_tick_size_not_the_live_price_grid():
    """`tick_size` and `price_tick` hold the same value and are not the same
    knob: the live grid floors quotes to `price_tick`, this branch steps down
    from the ask by `tick_size`."""
    intents, why = _quote(_ask_cfg(tick_size=0.02), window_pct=None)

    assert why == ""
    assert {i.side: i.price for i in intents} == {"UP": 0.50, "DOWN": 0.45}


def test_a_touch_pair_at_the_cap_is_sat_out_rather_than_quoted():
    """0.53/0.48 minus a tick is exactly the $0.99 cap, and the instrument pays
    $1.00 -- the fresh-market guard refuses rather than opening one leg."""
    intents, why = _quote(_ask_cfg(), window_pct=None,
                          books=_books(up_ask=0.53, dn_ask=0.48))

    assert intents == []
    assert "no fillable sub-$1.00 pair" in why


# --- the clock: `quote_window_pct` / `enforce_quote_window` -------------------


def test_the_window_gate_refuses_past_the_quoting_window():
    intents, why = _quote(_ask_cfg(), window_pct=0.41)

    assert intents == []
    assert "41% into window > 40% quoting window" in why


def test_the_window_gate_admits_exactly_at_the_quoting_window():
    """The rule is `window_pct > quote_window_pct`, so the boundary is inside."""
    intents, why = _quote(_ask_cfg(), window_pct=0.40)

    assert why == ""
    assert len(intents) == 2


def test_the_window_gate_is_switchable():
    intents, why = _quote(_ask_cfg(enforce_quote_window=False), window_pct=0.99)

    assert why == ""
    assert len(intents) == 2


def test_a_missing_clock_skips_the_window_rule_on_this_branch():
    """None means the caller could not work the window out; the rule is skipped
    rather than guessed, so a missing clock cannot gate every quote."""
    intents, why = _quote(_ask_cfg(), window_pct=None)

    assert why == ""
    assert len(intents) == 2


# --- and why none of the above runs in production -----------------------------


@pytest.mark.parametrize("objective", ["spread_capture", "rewards"])
def test_the_mid_priced_objectives_never_read_the_clock(objective):
    """Both mid-priced objectives take the from-mid path, which has no window
    rule: the same books quote identically at 9% and at 99% into the window."""
    cfg = replace(MakerConfig(), objective=objective)

    early, _ = _quote(cfg, window_pct=0.09)
    late_why = _quote(cfg, window_pct=0.99)

    assert [i.side for i in early] == [i.side for i in late_why[0]]
    assert early == late_why[0]
    assert "into window" not in late_why[1]


def test_the_ask_branch_is_the_only_place_the_window_bites():
    """The same books, one field apart: this is the difference #571 asked about."""
    window = 0.99

    assert _quote(_ask_cfg(), window_pct=window)[0] == []
    assert _quote(replace(MakerConfig(), objective="spread_capture"),
                  window_pct=window)[0] != []


def test_legacy_ask_branch_reads_canonical_target_balance():
    import warnings

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        intents, why = _quote(_ask_cfg(), window_pct=None)

    assert why == ""
    assert {i.side: i.price for i in intents} == {"UP": 0.51, "DOWN": 0.46}
    scoped_legacy = {"target_balance", "max_pair_cost"}
    for w in recorded:
        if "quotes.py" in str(w.filename):
            assert not any(name in str(w.message) for name in scoped_legacy), f"Unexpected warning: {w.message}"

