"""Settled realized-position dashboard telemetry."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_settled_positions_exposes_sell_exit_math(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settled.db"))
    from strategy import store
    import strategy.stats as stats

    store.log_close(
        condition_id="c-sell", market_slug="market-sell", shares=10.0,
        up_price=0.52, dn_price=0.46, cost_basis=9.50, proceeds=9.80,
        fee=0.10, realized_pnl=0.20, forgone_vs_settlement=0.30,
        up_cost_removed=5.0, dn_cost_removed=4.5,
    )
    monkeypatch.setattr(stats, "DB", tmp_path / "settled.db")

    rows = stats.settled_positions()
    assert len(rows) == 1
    row = rows[0]
    assert row["method"] == "SELL"
    assert row["shares"] == 10.0
    assert row["avg_cost"] == 0.95
    assert abs(row["exit_price"] - 0.98) < 1e-9
    assert row["pnl_pct"] == (100.0 * 0.20 / 9.50)
    assert row["fee_or_gas"] == 0.10
    assert row["yes_exit"] == 0.52
    assert row["no_exit"] == 0.46


def test_settled_positions_uses_parity_for_merge_and_gas(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settled.db"))
    from strategy import store
    import strategy.stats as stats

    store.log_close(
        condition_id="c-merge", market_slug="market-merge", method="merge",
        gas=0.03, shares=20.0, cost_basis=19.60, proceeds=20.0,
        realized_pnl=0.37, forgone_vs_settlement=0.0,
        up_cost_removed=9.8, dn_cost_removed=9.8,
    )
    monkeypatch.setattr(stats, "DB", tmp_path / "settled.db")

    row = stats.settled_positions()[0]
    assert row["method"] == "MERGE"
    assert abs(row["avg_cost"] - 0.98) < 1e-9
    assert row["exit_price"] == 1.0
    assert row["pnl_pct"] == (100.0 * 0.37 / 19.60)
    assert row["fee_or_gas"] == 0.03
    assert row["yes_exit"] is None
    assert row["no_exit"] is None


def test_settled_positions_includes_resolution_only_naked_wins(monkeypatch, tmp_path):
    """A naked position that resolves without ever being voluntarily closed
    still has to show up here -- it is already inside `_realized()`'s
    aggregate total, and an operator seeing that total move with nothing new
    in this table read it as a bug, not as two views of the same money."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settled.db"))
    from strategy import store
    import strategy.stats as stats

    store.log_fill(market_slug="mkt-resolve", condition_id="c-resolve",
                   token_id="TOK-UP", side="UP", price=0.44, size=14.636362)
    store.record_resolution("c-resolve", "TOK-UP")
    monkeypatch.setattr(stats, "DB", tmp_path / "settled.db")

    rows = stats.settled_positions()
    assert len(rows) == 1
    row = rows[0]
    assert row["method"] == "RESOLVE"
    assert row["market_slug"] == "mkt-resolve"
    assert abs(row["shares"] - 14.636362) < 1e-6
    assert abs(row["avg_cost"] - 0.44) < 1e-9
    assert row["exit_price"] == 1.0
    assert abs(row["realized_pnl"] - 8.19636272) < 1e-6


def test_settled_positions_skips_fully_closed_resolved_markets(monkeypatch, tmp_path):
    """A market that resolved but was already fully closed voluntarily has
    nothing left to settle -- it must not double-count the close's own row."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "settled.db"))
    from strategy import store
    import strategy.stats as stats

    store.log_fill(market_slug="mkt-done", condition_id="c-done",
                   token_id="TOK-UP", side="UP", price=0.50, size=10.0)
    store.log_close(condition_id="c-done", market_slug="mkt-done", shares=10.0,
                    up_price=0.99, dn_price=0.01, cost_basis=5.0, proceeds=9.90,
                    fee=0.10, realized_pnl=4.80, forgone_vs_settlement=0.0,
                    up_cost_removed=5.0, dn_cost_removed=0.0)
    store.record_resolution("c-done", "TOK-UP")
    monkeypatch.setattr(stats, "DB", tmp_path / "settled.db")

    rows = stats.settled_positions()
    assert len(rows) == 1
    assert rows[0]["method"] == "SELL"

