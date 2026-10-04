from __future__ import annotations

from pathlib import Path

import pytest

from core_brain.order_registry import CloseRecord, OrderRecord, OrderRegistry, QuoteRecord
from core_brain.statistics_report import SINGLE_CYCLE_LINE, write_statistics_report


def test_reporter_api_exists():
    assert callable(write_statistics_report)


def test_mode_validation_rejects_invalid(tmp_path: Path):
    with pytest.raises(ValueError):
        write_statistics_report(tmp_path / "registry.db", "run-1", "paper")


def test_reporter_does_not_call_run_shadow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("run_shadow must not be called")

    import core_brain.shadow_run

    monkeypatch.setattr(core_brain.shadow_run, "run_shadow", fail_if_called)
    result = write_statistics_report(tmp_path / "registry.db", "run-1", "shadow", tmp_path)
    assert called is False
    assert result["mode"] == "shadow"


def test_shadow_vs_live_disclaimer(tmp_path: Path):
    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    reg.log_close(CloseRecord(
        ts=1.0,
        condition_id="condition",
        market_slug="market",
        method="shadow_merge",
        shares=1.0,
        cost_basis=0.95,
        proceeds=1.0,
        realized_pnl=0.05,
        run_id="run-1",
    ))

    shadow = write_statistics_report(db, "run-1", "shadow", tmp_path / "shadow-out")
    live = write_statistics_report(db, "run-1", "live", tmp_path / "live-out")

    shadow_text = Path(shadow["report_path"]).read_text(encoding="utf-8")
    live_text = Path(live["report_path"]).read_text(encoding="utf-8")
    assert "rehearsal, not results" in shadow_text
    assert "observational, read-only, live caveats" in live_text
    assert Path(shadow["report_path"]).name.endswith("_shadow_run-1_statistics_report.md")


def test_sample_size_gate_bites_on_config_target(tmp_path: Path):
    """Ad-hoc reports gate against the configured close target, not 'reported'.

    shadow-01 showed a profitable 38-close run still failing the sample-size
    gate at target 60. If the report writer passes target_closes=None the gate
    row reads 'reported' / n/a and that failure becomes invisible. Fails
    before the fix (target_closes=None in write_statistics_report).
    """
    from core_brain.config import load as load_cfg

    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    target = load_cfg().stat_gate_target_closes
    n = min(target - 1, 5)  # below target, small keeps the test fast
    for i in range(n):
        reg.log_close(CloseRecord(
            ts=1.0 + i,
            condition_id=f"condition-{i}",
            market_slug=f"market-{i}",
            method="shadow_merge",
            shares=1.0,
            cost_basis=0.95,
            proceeds=1.0,
            realized_pnl=0.05,
            run_id="run-1",
        ))

    result = write_statistics_report(db, "run-1", "shadow", tmp_path / "out")
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    sample_row = next(line for line in text.splitlines() if "`n_closes`" in line)
    assert f">= {target}" in sample_row, sample_row
    assert "FAIL" in sample_row, sample_row
    assert "Target sample" in text or "`target_closes`" in text or f"{target}" in text


def _seed_zero_fill_run(reg: OrderRegistry, run_id: str, *, cycles: int = 1) -> None:
    """Issue #351 fixture: 4 deep-queue quotes, 0 fills, cycle-1-only intents."""
    queues = (2524.0, 5092.0, 38706.0, 6113.0)
    sizes = (6.0, 6.0, 5.0, 5.0)
    for i, (ahead, size) in enumerate(zip(queues, sizes)):
        reg.log_quote(QuoteRecord(
            ts=1000.0 + i,
            condition_id=f"cond-{i // 2}",
            token_id=f"tok-{i}",
            side="BUY",
            price=0.47,
            size=size,
            market_slug=f"market-{i // 2}",
            queue_ahead=ahead,
            local_id=f"ord-{i}",
            run_id=run_id,
        ))
    with reg._conn() as conn:
        for visit in range(max(cycles, 1) * 2 if cycles else 0):
            conn.execute(
                "INSERT INTO cycle_intent "
                "(ts, cycle, market_slug, condition_id, intent_count, submitted, cancelled, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1000.0 + visit * 0.25, visit // 2 + 1 if cycles > 1 else 1,
                 f"market-{visit % 2}", f"cond-{visit % 2}", 1, 1, 0, run_id),
            )
        conn.commit()


def test_single_cycle_run_shows_multiples_and_limitation(tmp_path: Path):
    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    _seed_zero_fill_run(reg, "run-1", cycles=1)

    result = write_statistics_report(db, "run-1", "shadow", tmp_path / "out")
    text = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "## Queue depth (shadow)" in text
    assert SINGLE_CYCLE_LINE in text
    assert result["measured_quotes"] == 4
    assert result["unmeasured_quotes"] == 0
    assert result["max_queue_multiple"] == pytest.approx(7741.2, abs=1.0)
    assert result["median_queue_multiple"] == pytest.approx(1035.0, abs=50.0)
    assert result["distinct_cycles"] == 1


def test_multi_cycle_run_omits_limitation(tmp_path: Path):
    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    _seed_zero_fill_run(reg, "run-1", cycles=3)

    result = write_statistics_report(db, "run-1", "shadow", tmp_path / "out")
    text = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "## Queue depth (shadow)" in text
    assert SINGLE_CYCLE_LINE not in text
    assert result["distinct_cycles"] == 3


def _seed_test_order(
    reg: OrderRegistry,
    run_id: str,
    order_id: str,
    *,
    posted_ms: int,
    last_polled_ms: int,
    status: str = "open",
    cancel_reason: str | None = None,
    size: float = 10.0,
) -> None:
    order = OrderRecord(
        id=order_id,
        condition_id="cond-1",
        token_id="tok-1",
        side="BUY",
        price=0.50,
        original_size=size,
        status="open",
        posted_ts=posted_ms,
        last_polled_ts=posted_ms,
        run_id=run_id,
    )
    reg.create_order(order)
    if status != "open":
        reg.update_order_status(
            local_id=order_id,
            status=status,
            last_polled_ts=last_polled_ms,
            cancel_reason=cancel_reason,
        )


def test_order_lifetime_cancelled_median_and_markdown(tmp_path: Path):
    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    run_id = "test-lifetime-run"

    # Seed 3 cancelled orders with lifetimes 10s, 30s, 50s
    _seed_test_order(reg, run_id, "ord-1", posted_ms=1_000, last_polled_ms=11_000, status="cancelled", cancel_reason="price_moved")
    _seed_test_order(reg, run_id, "ord-2", posted_ms=1_000, last_polled_ms=31_000, status="cancelled", cancel_reason="price_moved")
    _seed_test_order(reg, run_id, "ord-3", posted_ms=1_000, last_polled_ms=51_000, status="cancelled", cancel_reason="not_quoted")

    result = write_statistics_report(db, run_id, "shadow", tmp_path / "out")
    text = Path(result["report_path"]).read_text(encoding="utf-8")

    assert result["cancelled_lifetime_count"] == 3
    assert result["cancelled_median_lifetime_s"] == pytest.approx(30.0)
    assert result["filled_lifetime_count"] == 0
    assert result["filled_median_lifetime_s"] is None
    assert result["lifetime_unknown_orders"] == 0

    assert "- **Cancelled median lifetime**: `30.0s` (measured: `3`)" in text
    assert "- **Filled median lifetime**: `n/a` (measured: `0`)" in text
    assert "- **Lifetime unknown**: `0`" in text


def test_order_lifetime_with_filled_and_unknown_orders(tmp_path: Path):
    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    run_id = "test-mixed-run"

    # 1 cancelled (20s)
    _seed_test_order(reg, run_id, "ord-c", posted_ms=1_000, last_polled_ms=21_000, status="cancelled", cancel_reason="price_moved")
    # 2 filled (5s and 15s -> median 10s)
    _seed_test_order(reg, run_id, "ord-f1", posted_ms=1_000, last_polled_ms=6_000, status="filled")
    _seed_test_order(reg, run_id, "ord-f2", posted_ms=1_000, last_polled_ms=16_000, status="filled")
    # 1 open order (lifetime unknown)
    _seed_test_order(reg, run_id, "ord-open", posted_ms=1_000, last_polled_ms=1_000, status="open")
    # 1 order belonging to a DIFFERENT run (must be completely ignored)
    _seed_test_order(reg, "other-run", "ord-other", posted_ms=1_000, last_polled_ms=21_000, status="cancelled")

    result = write_statistics_report(db, run_id, "shadow", tmp_path / "out")
    text = Path(result["report_path"]).read_text(encoding="utf-8")

    assert result["cancelled_lifetime_count"] == 1
    assert result["cancelled_median_lifetime_s"] == pytest.approx(20.0)
    assert result["filled_lifetime_count"] == 2
    assert result["filled_median_lifetime_s"] == pytest.approx(10.0)
    assert result["lifetime_unknown_orders"] == 1
    assert result["lifetime_unknown_by_status"] == {"open": 1}

    assert "- **Cancelled median lifetime**: `20.0s` (measured: `1`)" in text
    assert "- **Filled median lifetime**: `10.0s` (measured: `2`)" in text
    assert "- **Lifetime unknown**: `1` (open: `1`)" in text


def test_cancel_reason_distribution_and_sorting(tmp_path: Path):
    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    run_id = "test-reasons-run"

    # 3 price_moved, 1 not_quoted, 1 empty reason
    _seed_test_order(reg, run_id, "ord-1", posted_ms=1_000, last_polled_ms=5_000, status="cancelled", cancel_reason="price_moved")
    _seed_test_order(reg, run_id, "ord-2", posted_ms=1_000, last_polled_ms=6_000, status="cancelled", cancel_reason="price_moved")
    _seed_test_order(reg, run_id, "ord-3", posted_ms=1_000, last_polled_ms=7_000, status="cancelled", cancel_reason="price_moved")
    _seed_test_order(reg, run_id, "ord-4", posted_ms=1_000, last_polled_ms=8_000, status="cancelled", cancel_reason="not_quoted")
    _seed_test_order(reg, run_id, "ord-5", posted_ms=1_000, last_polled_ms=9_000, status="cancelled", cancel_reason="  ")
    # Also 1 filled order (must be excluded from cancel reasons)
    _seed_test_order(reg, run_id, "ord-6", posted_ms=1_000, last_polled_ms=10_000, status="filled")

    result = write_statistics_report(db, run_id, "shadow", tmp_path / "out")
    text = Path(result["report_path"]).read_text(encoding="utf-8")

    assert result["cancelled_orders"] == 5
    reasons = result["cancel_reasons"]
    assert len(reasons) == 3

    assert reasons["price_moved"]["count"] == 3
    assert reasons["price_moved"]["share"] == pytest.approx(0.6)

    assert reasons["not_quoted"]["count"] == 1
    assert reasons["not_quoted"]["share"] == pytest.approx(0.2)

    assert reasons["(no reason recorded)"]["count"] == 1
    assert reasons["(no reason recorded)"]["share"] == pytest.approx(0.2)

    assert "- **Cancel reasons**:" in text
    assert "- `price_moved`: `3` (60.0%)" in text
    assert "- `not_quoted`: `1` (20.0%)" in text
    assert "- `(no reason recorded)`: `1` (20.0%)" in text


def test_live_report_omits_lifetime_and_cancel_keys(tmp_path: Path):
    db = tmp_path / "registry.db"
    reg = OrderRegistry(db)
    run_id = "test-live-run"

    _seed_test_order(reg, run_id, "ord-1", posted_ms=1_000, last_polled_ms=10_000, status="cancelled", cancel_reason="price_moved")

    result = write_statistics_report(db, run_id, "live", tmp_path / "out")
    text = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "cancelled_median_lifetime_s" not in result
    assert "cancel_reasons" not in result
    assert "## Queue depth (shadow)" not in text
    assert "Cancelled median lifetime" not in text

