"""Default-off fill-quality entry gate (#761). Station III Task 4.

The gate cannot change live behavior by accident: off by default, empty block
list, and the consult only filters the intent-visible edge dimension.
"""

from core_brain import fill_quality_report as fqr
from core_brain.config import MakerConfig
from core_brain.quotes import QuoteIntent
from core_brain.trader_loop import (
    CANCEL_NOT_QUOTED, VenueSeam, _visit_one, plan_orders,
)


def _intent(edge: float, token: str = "tok-up", side: str = "UP") -> QuoteIntent:
    return QuoteIntent(side=side, token_id=token, price=0.60, size=10,
                       mid=0.60 + edge, edge_vs_mid=edge, pair_id="pair-1")


def test_gate_is_off_and_empty_by_default():
    cfg = MakerConfig()
    assert cfg.bot_entry_fill_quality_gate_on is False
    assert tuple(cfg.bot_entry_fill_quality_blocked_edge) == ()


def test_gate_env_override(monkeypatch):
    monkeypatch.setenv("BOT_ENTRY_FILL_QUALITY_GATE_ON", "1")
    monkeypatch.setenv("BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE", "0.02-0.03, 0.03-0.04")
    cfg = MakerConfig.from_env()
    assert cfg.bot_entry_fill_quality_gate_on is True
    assert tuple(cfg.bot_entry_fill_quality_blocked_edge) == ("0.02-0.03", "0.03-0.04")


def test_evaluate_is_pure_and_empty_means_allow_all():
    allow, reason = fqr.evaluate_fill({"edge": "0.02-0.03"}, frozenset())
    assert allow is True
    allow, reason = fqr.evaluate_fill(
        {"edge": "0.02-0.03"}, frozenset({("edge", "0.02-0.03")}))
    assert allow is False and "0.02-0.03" in reason
    allow, _ = fqr.evaluate_fill(
        {"edge": "0.06+"}, frozenset({("edge", "0.02-0.03")}))
    assert allow is True


def test_plan_orders_ignores_gate_when_off():
    cfg = MakerConfig()  # off by default
    reasons: dict = {}
    _, to_submit = plan_orders([], [_intent(0.025)], cfg=cfg, reasons=reasons)
    assert len(to_submit) == 1


def test_plan_orders_filters_blocked_edge_only_when_on():
    cfg = MakerConfig(bot_entry_fill_quality_gate_on=True,
                      bot_entry_fill_quality_blocked_edge=("0.02-0.03",))
    reasons: dict = {}
    _, to_submit = plan_orders(
        [], [_intent(0.025, "tok-bad"), _intent(0.10, "tok-good")],
        cfg=cfg, reasons=reasons,
    )
    submitted = {i.token_id for i in to_submit}
    assert submitted == {"tok-good"}
    assert any("fill_quality_gate" in key for key in reasons)


def _cold_intent(token: str = "tok-cold") -> QuoteIntent:
    """An intent with no book quote behind it: the edge cannot be measured."""
    return QuoteIntent(side="UP", token_id=token, price=0.60, size=10,
                       mid=None, edge_vs_mid=None, pair_id="pair-1")


def test_unmeasurable_edge_is_refused_not_let_through():
    """#792: `unknown` names no price band, so the floor must refuse it.

    Fail closed by an explicit rule: an intent whose edge cannot be measured
    carries the least support an entry can have, and the bands cannot name it.
    """
    cfg = MakerConfig(bot_entry_fill_quality_gate_on=True,
                      bot_entry_fill_quality_blocked_edge=("0.02-0.03",))
    reasons: dict = {}
    _, to_submit = plan_orders([], [_cold_intent()], cfg=cfg, reasons=reasons)
    assert to_submit == []
    assert list(reasons.values()) == [fqr.UNMEASURABLE_EDGE]


def test_the_rule_is_the_gate_s_own_and_the_report_label_stays_unknown():
    """Refused here, still its own `unknown` bucket in the calibration."""
    blocked = frozenset({("edge", "0.02-0.03")})
    assert fqr.evaluate_edge(None, blocked) == (False, fqr.UNMEASURABLE_EDGE)
    assert fqr.evaluate_edge(0.10, blocked) == (True, "allowed")
    assert fqr.edge_bucket_for(None) == fqr.QUEUE_UNKNOWN


class _FakeMarket:
    """The attributes `_visit_one` reads for one market."""

    condition_id = "0xabc"
    up_token = "tok-up"
    down_token = "tok-dn"
    market_slug = "fake-market"
    tick_size = 0.01
    neg_risk = False

    def t_remaining(self, now=None):
        return 14400.0


def test_the_refusal_is_counted_on_the_decide_event():
    """Counted, never silent: the report's `unknown` bucket only sees fills."""
    cfg = MakerConfig(bot_entry_fill_quality_gate_on=True,
                      bot_entry_fill_quality_blocked_edge=("0.02-0.03",))
    emitted: list = []
    seam = VenueSeam(
        base_cfg=cfg,
        client=object(),
        registry=None,
        fetch_market=lambda cid: _FakeMarket(),
        fetch_books=lambda host, tok: {"token_id": tok, "best_bid": 0.46,
                                       "best_ask": 0.50, "bids": {}, "asks": {}},
        decide=lambda *a, **k: ([_cold_intent("tok-up")], ""),
        submit_fn=lambda *a, **k: 0,
        cancel_fn=lambda *a, **k: 0,
        open_orders_fn=lambda m: [],
    )
    _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=False,
               emit_fn=lambda *a, **k: emitted.append(k))
    decide = [e for e in emitted if e.get("action") == "decide"]
    assert decide and decide[-1]["extra"]["fill_quality_refused"] == 1


def test_blocking_one_leg_still_posts_the_other_and_cancels_the_resting_leg():
    """The gate is per intent, not per pair (#787 decision input).

    A raised edge floor therefore does not close a pair atomically: the leg
    below the floor is dropped while its opposite leg still posts, and the
    resting order on the dropped token is cancelled as not_quoted.
    """
    cfg = MakerConfig(bot_entry_fill_quality_gate_on=True,
                      bot_entry_fill_quality_blocked_edge=("0.02-0.03",))
    resting = {"id": "o-up", "token_id": "tok-up", "price": 0.60, "size": 10}
    reasons: dict = {}
    to_cancel, to_submit = plan_orders(
        [resting], [_intent(0.025, "tok-up"), _intent(0.10, "tok-down", "DOWN")],
        cfg=cfg, reasons=reasons,
    )
    assert [i.token_id for i in to_submit] == ["tok-down"]
    assert [o["id"] for o in to_cancel] == ["o-up"]
    assert reasons["o-up"] == CANCEL_NOT_QUOTED
