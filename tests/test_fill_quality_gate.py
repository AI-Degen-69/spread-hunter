"""Default-off fill-quality entry gate (#761). Station III Task 4.

The gate cannot change live behavior by accident: off by default, empty block
list, and the consult only filters the intent-visible edge dimension.
"""

from core_brain import fill_quality_report as fqr
from core_brain.config import MakerConfig
from core_brain.quotes import QuoteIntent
from core_brain.trader_loop import plan_orders


def _intent(edge: float, token: str = "tok-up") -> QuoteIntent:
    return QuoteIntent(side="UP", token_id=token, price=0.60, size=10,
                       mid=0.60 + edge, edge_vs_mid=edge)


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
