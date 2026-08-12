"""Operator-facing market event telemetry tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_reason_codes_are_stable_and_specific(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "events.db"))
    from strategy import store

    assert store.reason_code("$120 naked >= $120 budget -- not adding") == "NAKED_CAP"
    assert store.reason_code("hedge token DOWN not tradeable (one-sided book)") == "ONE_SIDED_BOOK"
    assert store.reason_code("pair 0.520+0.480=$1.0000 cap") == "PAIR_COST"
    assert store.reason_code("fleet HALTED on pooled markout") == "FLEET_HALTED"
    # `hard_block` wraps the hedge leg's own rejection in its prose, so a
    # settled hedge carries BOTH "not tradeable" and "settled book". The code
    # must name the binding cause -- the market has already decided -- not the
    # wrapper, which would send the operator hunting a missing side on a book
    # that has two.
    assert store.reason_code(
        "hedge token UP not tradeable (settled book 0.999/0.001) -- "
        "a fill here could not be closed") == "PRICE_BAND"
    assert store.reason_code("0.850 outside band 0.30-0.70") == "PRICE_BAND"


def test_market_event_is_durable_and_reason_code_is_stored(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "events.db"))
    from strategy import store

    store.log_event(condition_id="c", market_slug="m", kind="BLOCKED",
                    reason="book too thin 12sh < 50sh")
    with store.db() as c:
        row = c.execute(
            "SELECT kind, reason, reason_code FROM market_events"
        ).fetchone()
    assert row == ("BLOCKED", "book too thin 12sh < 50sh", "THIN_BOOK")


def test_decision_schema_migrates_reason_code(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "events.db"))
    from strategy import store

    store.log_decision(condition_id="c", market_slug="m", action="BLOCKED",
                       reason="pair cap", reason_code="PAIR_COST")
    store.flush_decision(force=True)
    with store.db() as c:
        row = c.execute(
            "SELECT action, reason_code, count FROM decisions"
        ).fetchone()
    assert row == ("BLOCKED", "PAIR_COST", 1)
