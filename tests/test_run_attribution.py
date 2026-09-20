"""Run attribution: telemetry measures THIS run, and says what config ran.

Three defects from the 2026-08-26 rehearsal (shadow-c52e533f5725), each of
which corrupted a measurement an operator then reasoned from:

  * `settle_market` observed every open order in the shared store, not just
    this run's -- six marks stamped onto a rehearsal belonged to the PREVIOUS
    rehearsal's resting orders, and a Prime verification pass reached a false
    conclusion about touch-resting from them.
  * `cycle_stream._write_cycle_intent` fell back to the process-wide run id,
    which is the LIVE lock-file id for 12 hours -- every `cycle_intent` row of
    a rehearsal was attributed to the live session (`run-5eb297de8751`, all
    200 rows).
  * Nothing recorded the effective reward_offset / price_risk_widen a
    rehearsal ran under; recovering them cost a full verification pass over
    the quotes ledger.

How to verify (operator, PowerShell):

    python -m pytest -q tests/test_run_attribution.py ; python -m pytest -q

The first command runs the attribution tests alone; the second is the full
suite, which is the bar this repo merges against.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest


class FakeMarket:
    condition_id = "0xabc"
    up_token = "tok-up"
    down_token = "tok-dn"
    market_slug = "fake-market"

    @staticmethod
    def t_remaining(now=None):
        return 14400.0


def _rows(db_path, table="queue_marks"):
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    finally:
        conn.close()


class TestMarksMeasureThisRunOnly:
    """A shared store must not observe another run's resting orders."""

    def _seed_two_runs(self, db_path):
        from core_brain.order_registry import OrderRecord, OrderRegistry
        from core_brain.shadow_exec import ensure_shadow_tables

        ensure_shadow_tables(db_path)
        old = OrderRegistry(db_path=db_path, run_id="shadow-oldrunaaaaa")
        old.create_order(OrderRecord(
            id="local-old", order_id="oid-old", condition_id="0xabc",
            token_id="tok-up", side="BUY", price=0.51, original_size=20.0,
            status="open", posted_ts=1, last_polled_ts=1))
        new = OrderRegistry(db_path=db_path, run_id="shadow-newrunaaaaa")
        new.create_order(OrderRecord(
            id="local-new", order_id="oid-new", condition_id="0xabc",
            token_id="tok-up", side="BUY", price=0.47, original_size=20.0,
            status="open", posted_ts=2, last_polled_ts=2))
        return new

    def test_a_new_runs_marks_contain_only_its_own_prices(self, tmp_path):
        """The ghost-mark defect: six marks in run c52e sat at the PREVIOUS
        run's resting prices, four minutes before c52e ever quoted."""
        from core_brain.shadow_exec import settle_market

        db = tmp_path / "s.db"
        reg = self._seed_two_runs(db)

        def book_fn(_host, token_id):
            return {"token_id": token_id, "best_bid": 0.47, "best_ask": 0.49,
                    "bids": {0.47: 100.0}, "asks": {0.49: 100.0}}

        settle_market(reg, FakeMarket(), db_path=db,
                      traded_fn=lambda cid, seen: {}, seen=set(),
                      now_fn=lambda: 500.0, book_fn=book_fn)

        mine = [r["price"] for r in _rows(db)
                if r["run_id"] == "shadow-newrunaaaaa"]
        foreign = [r for r in _rows(db) if r["price"] == pytest.approx(0.51)]
        assert mine == [pytest.approx(0.47)], mine
        assert foreign == [], (
            "a previous run's resting order was measured as ours")


class TestIntentsCarryTheSessionId:
    """`cycle_intent` rows of a rehearsal are tagged `shadow-`, never the
    live lock id the process-wide resolver hands back."""

    def test_a_shadow_session_tags_its_own_intents(self, tmp_path, monkeypatch):
        from core_brain import order_registry
        from core_brain.quotes import QuoteIntent

        import core_brain.cycle_stream as cycle_stream
        from core_brain.shadow_run import run_shadow

        # Keep the ring file out of the repo's runtime directory, and make the
        # process-wide id a LIVE one so a fallback shows up as `run-liveone`.
        monkeypatch.setattr(cycle_stream, "DEFAULT_RING_PATH",
                            tmp_path / "ring.jsonl")
        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")

        intent = QuoteIntent(side="UP", token_id="tok-up", price=0.48, size=2,
                             mid=0.49, edge_vs_mid=0.01)
        run_shadow(
            minutes=0.0, db_path=tmp_path / "shadow.db",
            markets_fn=lambda max_markets=None: [FakeMarket()],
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([intent], ""),
        )

        tagged = {r["run_id"] for r in _rows(tmp_path / "shadow.db",
                                             "cycle_intent")}
        assert tagged, "no cycle_intent rows were written, so this proves none"
        assert all(i.startswith("shadow-") for i in tagged), tagged

    def test_the_submit_event_updates_the_row_the_decide_event_inserted(
            self, tmp_path, monkeypatch):
        """The decide event INSERTs the visit; the submit event UPDATEs its
        counts. `_update_cycle_intent` matches on cycle + market + run_id, so a
        submit that resolved the run id process-wide would match no row and the
        outcome of the visit would be lost -- silently, as a zero."""
        from core_brain import order_registry
        import core_brain.cycle_stream as cycle_stream

        monkeypatch.setattr(cycle_stream, "DEFAULT_RING_PATH",
                            tmp_path / "ring.jsonl")
        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")
        db = tmp_path / "shadow.db"
        mine = "shadow-emitaaaaaaa"

        cycle_stream.emit(
            7, "quoting", "decide", market_slug="fake-market",
            extra={"intent_count": 2, "condition_id": "0xabc"},
            db_path=db, run_id=mine)
        cycle_stream.emit(
            7, "quoting", "submit", market_slug="fake-market",
            extra={"submitted": 2, "cancelled": 1},
            db_path=db, run_id=mine)

        rows = _rows(db, "cycle_intent")
        assert len(rows) == 1, rows
        assert rows[0]["run_id"] == mine, rows[0]
        assert rows[0]["submitted"] == 2, (
            "the submit event did not reach the row the decide event inserted: "
            "its run id did not match, so the visit's outcome was dropped")
        assert rows[0]["cancelled"] == 1, rows[0]


class TestEffectiveConfigIsLogged:
    """One INFO line at start retires 'what did this rehearsal actually run?'."""

    def test_the_overridden_values_appear_in_the_start_line(
            self, tmp_path, monkeypatch, caplog):
        from core_brain.shadow_run import run_shadow

        monkeypatch.setattr(order_registry_current(), "_CURRENT_RUN_ID",
                            "run-liveone")
        # `HUNTER_REWARD_OFFSET`/`HUNTER_PRICE_RISK_WIDEN` live on the
        # reward-offset-override branch; on main the overridable knob is the
        # completable cap, so assert through it.
        monkeypatch.setenv("HUNTER_COMPLETABLE_CAP", "0.995")

        with caplog.at_level(logging.INFO, logger="shadow_run"):
            run_shadow(
                minutes=0.0, db_path=tmp_path / "shadow.db",
                markets_fn=lambda max_markets=None: [FakeMarket()],
                client_fn=lambda: object(),
                decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
            )

        lines = [r.message for r in caplog.records
                 if "reward_offset" in r.message]
        assert lines, "no effective-config line was logged at run start"
        assert "0.9950" in lines[0], lines[0]
        for field in ("price_risk_widen", "min_reward_offset",
                      "max_completable_pair_cost"):
            assert field in lines[0], f"{field} missing from: {lines[0]}"


def order_registry_current():
    from core_brain import order_registry
    return order_registry


class TestTheFilterUnderItsTwoEdgeCases:
    """The run filter is `(o.run_id or mine) == mine`. Both halves of that
    expression are a decision about money-adjacent telemetry, and neither was
    pinned when the filter landed.

    An unstamped row is counted as ours, deliberately: `run_id` is nullable on
    `orders` (order_registry.py:301) and a store written before the column was
    populated holds rows no run can claim. Dropping them would silently shrink
    a rehearsal's measured resting set instead of loudly failing.

    A registry with no id of its own falls back to the process id, so a shadow
    store's `shadow-...` rows stop matching and the run measures NOTHING. That
    is the safe direction -- a zero is visible, a foreign price is not -- but
    it is fail-quiet, so it is pinned here rather than left to be rediscovered.
    """

    def _seed(self, db_path, order_run_id, mine_run_id):
        from core_brain.order_registry import OrderRecord, OrderRegistry
        from core_brain.shadow_exec import ensure_shadow_tables

        ensure_shadow_tables(db_path)
        writer = OrderRegistry(db_path=db_path, run_id=order_run_id)
        writer.create_order(OrderRecord(
            id="local-1", order_id="oid-1", condition_id="0xabc",
            token_id="tok-up", side="BUY", price=0.47, original_size=20.0,
            status="open", posted_ts=1, last_polled_ts=1))
        return OrderRegistry(db_path=db_path, run_id=mine_run_id)

    def _settle(self, reg, db):
        from core_brain.shadow_exec import settle_market

        def book_fn(_host, token_id):
            return {"token_id": token_id, "best_bid": 0.47, "best_ask": 0.49,
                    "bids": {0.47: 100.0}, "asks": {0.49: 100.0}}

        return settle_market(reg, FakeMarket(), db_path=db,
                             traded_fn=lambda cid, seen: {}, seen=set(),
                             now_fn=lambda: 500.0, book_fn=book_fn)

    def test_an_unstamped_order_is_measured_as_this_runs(self, tmp_path):
        db = tmp_path / "s.db"
        reg = self._seed(db, "shadow-writeraaaaa", "shadow-mineaaaaaaa")
        conn = sqlite3.connect(db)
        try:
            conn.execute("UPDATE orders SET run_id = NULL")
            conn.commit()
        finally:
            conn.close()

        self._settle(reg, db)

        marked = [r["price"] for r in _rows(db)]
        assert marked == [pytest.approx(0.47)], (
            "an order no run claims must be measured, not dropped: dropping it "
            "shrinks the resting set silently")

    def test_a_registry_without_its_own_id_measures_nothing_rather_than_foreign(
            self, tmp_path, monkeypatch):
        from core_brain import order_registry

        # Pin the process-wide id instead of inheriting whatever the ambient
        # session resolved. Without this the test still passes, but for an
        # accidental reason -- it would prove only that the ambient id differed
        # from the writer's, which says nothing about the filter.
        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")

        db = tmp_path / "s.db"
        reg = self._seed(db, "shadow-writeraaaaa", None)
        assert reg._run_id() == "run-liveone", (
            "the registry must fall through to the pinned process id")

        marks = self._settle(reg, db)

        assert marks == [], marks
        assert _rows(db) == [], (
            "a registry that fell back to the process id must measure nothing, "
            "never another session's resting prices")
