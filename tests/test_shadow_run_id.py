"""Every rehearsal gets its own run_id, and never the live session's.

`order_registry._resolve_run_id` reuses `runtime/.current_run_id` for 12 hours
so the fleet, the dashboard and the exec process all tag one live session the
same way. That is right for live and wrong for a rehearsal: two shadow runs
3.85 hours apart welded themselves into `run-2809a7161de1`, so the 30-minute
baseline and the 30-minute re-run shared one bucket and could only be told
apart by spotting the gap in `posted_ts`. A rehearsal that cannot be selected
on its own is a rehearsal whose numbers cannot be compared to anything.
"""
from __future__ import annotations

import os
import re
from unittest import mock

import pytest

from core_brain.shadow_run import shadow_run_id


class TestShadowRunId:
    def test_every_call_mints_a_fresh_id(self, monkeypatch):
        monkeypatch.delenv("SH_RUN_ID", raising=False)
        assert shadow_run_id() != shadow_run_id()

    def test_the_generated_id_has_the_documented_shape(self, monkeypatch):
        # `shadow-` plus 12 lowercase hex. Pinned because the prefix is what
        # keeps a rehearsal out of the live run selector, and the width is what
        # makes a collision between two same-second sessions implausible.
        monkeypatch.delenv("SH_RUN_ID", raising=False)
        assert re.fullmatch(r"shadow-[0-9a-f]{12}", shadow_run_id())

    def test_the_id_is_marked_as_a_rehearsal(self, monkeypatch):
        monkeypatch.delenv("SH_RUN_ID", raising=False)
        # A shadow id must never be mistakable for a live one in the dashboard's
        # run selector, or a rehearsal's zero fills read as a live session's.
        rid = shadow_run_id()
        assert rid.startswith("shadow-")
        assert not rid.startswith("run-")

    def test_an_explicit_override_is_honoured(self):
        # The operator continuing one rehearsal across a restart is the one
        # case where sharing an id is the point.
        with mock.patch.dict(os.environ, {"SH_RUN_ID": "shadow-deadbeef"}):
            assert shadow_run_id() == "shadow-deadbeef"

    def test_the_live_lock_file_is_never_consulted(self, tmp_path, monkeypatch):
        # The failure this exists to stop: a rehearsal adopting the live
        # session's run_id because the lock file is under 12 hours old.
        from core_brain import order_registry

        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SH_RUN_ID", None)
            rid = shadow_run_id()
        assert rid != "run-liveone"
        assert rid.startswith("shadow-")


class FakeMarket:
    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False

    def t_remaining(self, now=None):
        return 14400.0


def _book(_clob_host, token_id):
    return {"token_id": token_id, "bids": {0.50: 500.0}, "asks": {0.51: 500.0},
            "best_bid": 0.50, "best_ask": 0.51, "malformed": 0}


def _one_session(tmp_path, monkeypatch, db_name, run_id_env=None):
    """One real shadow session over a stub market; returns the run_ids written."""
    import core_brain.markets as markets_mod
    from core_brain.order_registry import OrderRegistry
    from core_brain.quotes import QuoteIntent
    from core_brain.shadow_run import _Deadline, run_shadow

    seen = {"n": 0}

    def counting_sleep(_seconds):
        seen["n"] += 1
        if seen["n"] >= 2:
            raise _Deadline()

    monkeypatch.setattr("core_brain.shadow_run.make_deadline_sleep",
                        lambda *a, **k: counting_sleep)
    monkeypatch.setattr(markets_mod, "recent_trades", lambda cid, s: {})
    if run_id_env is None:
        monkeypatch.delenv("SH_RUN_ID", raising=False)
    else:
        monkeypatch.setenv("SH_RUN_ID", run_id_env)

    db = tmp_path / db_name
    run_shadow(
        minutes=1.0, db_path=db,
        markets_fn=lambda max_markets=None: [FakeMarket("0xabc")],
        client_fn=lambda: object(),
        decide_fn=lambda *a, **k: (
            [QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                         mid=0.5, edge_vs_mid=0.0)], "quoting"),
        fetch_books=_book,
        interval=0.0,
    )
    reg = OrderRegistry(db_path=db)
    return {o["run_id"] for o in reg.get_all_orders()}


class TestRunShadowUsesIt:
    def test_orders_are_tagged_with_a_shadow_id(self, tmp_path, monkeypatch):
        ids = _one_session(tmp_path, monkeypatch, "a.db")
        assert ids, "the session wrote no orders, so it proves nothing"
        assert all(i.startswith("shadow-") for i in ids), ids

    def test_two_sessions_do_not_share_an_id(self, tmp_path, monkeypatch):
        # The exact failure: two 30-minute rehearsals 3.85 hours apart both
        # wrote as run-2809a7161de1 and could only be separated by eye.
        first = _one_session(tmp_path, monkeypatch, "first.db")
        second = _one_session(tmp_path, monkeypatch, "second.db")
        assert first and second
        assert first.isdisjoint(second), f"{first} overlaps {second}"


class TestRunIdIsSessionScoped:
    """CodeRabbit round 1: a process-wide id leaks out of the session.

    `set_run_id` mutated `order_registry._CURRENT_RUN_ID`, which every write
    path reads. Two consequences, both worse than the bug this branch set out
    to fix: a second shadow session starting while a first is alive silently
    re-tags the first one's later rows, and the shadow id stayed installed
    after `run_shadow` returned -- so a live order created afterwards in the
    same process would be stamped `shadow-...`. A rehearsal id on a real order
    is a worse lie than two rehearsals sharing one id.
    """

    def test_the_process_wide_id_is_untouched_by_a_session(
            self, tmp_path, monkeypatch):
        from core_brain import order_registry

        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")
        ids = _one_session(tmp_path, monkeypatch, "scoped.db")
        assert all(i.startswith("shadow-") for i in ids), ids
        # The live id must survive the rehearsal untouched.
        assert order_registry.get_run_id() == "run-liveone"

    def test_two_registries_interleaved_keep_their_own_ids(self, tmp_path):
        """Write a, then b, then a AGAIN.

        The earlier version of this test wrote a before b and stopped, which
        proves nothing an implementation of "whichever registry was built last
        wins" would not also pass. The second write through `a` is the whole
        assertion: a session's id has to survive another session starting.
        """
        from core_brain.order_registry import OrderRegistry, OrderRecord

        def order(oid, tok):
            return OrderRecord(
                id=f"local-{oid}", order_id=f"oid-{oid}", condition_id="0xabc",
                token_id=tok, side="BUY", price=0.47, original_size=20,
                status="open", posted_ts=1, last_polled_ts=1)

        a = OrderRegistry(db_path=tmp_path / "a.db", run_id="shadow-aaaaaaaaaaaa")
        b = OrderRegistry(db_path=tmp_path / "b.db", run_id="shadow-bbbbbbbbbbbb")

        a.create_order(order("a1", "tok-a"))
        b.create_order(order("b1", "tok-b"))
        a.create_order(order("a2", "tok-a"))   # after b exists and has written
        b.create_order(order("b2", "tok-b"))

        assert {o["run_id"] for o in a.get_all_orders()} == {"shadow-aaaaaaaaaaaa"}
        assert {o["run_id"] for o in b.get_all_orders()} == {"shadow-bbbbbbbbbbbb"}
        assert len(a.get_all_orders()) == 2 and len(b.get_all_orders()) == 2

    def test_a_registry_without_an_id_still_uses_the_process_one(
            self, tmp_path, monkeypatch):
        from core_brain import order_registry
        from core_brain.order_registry import OrderRegistry, OrderRecord

        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", "run-liveone")
        reg = OrderRegistry(db_path=tmp_path / "c.db")
        reg.create_order(OrderRecord(
            id="local-1", order_id="oid-1", condition_id="0xabc",
            token_id="tok", side="BUY", price=0.47, original_size=20,
            status="open", posted_ts=1, last_polled_ts=1))
        assert {o["run_id"] for o in reg.get_all_orders()} == {"run-liveone"}


class TestOverrideReachesTheRows:
    def test_an_explicit_override_tags_every_row(self, tmp_path, monkeypatch):
        # The override is only useful if it survives all the way to the store.
        ids = _one_session(tmp_path, monkeypatch, "ovr.db",
                           run_id_env="shadow-operatorpin")
        assert ids == {"shadow-operatorpin"}


class TestEveryChangedWritePathIsScoped:
    """One test per write path the run-id change touched.

    Twelve call sites moved from the process-wide `get_run_id()` to the
    registry's own `_run_id()`. Covering only `create_order` left eleven paths
    asserting nothing: a shadow session records fills, quotes, market events,
    markouts, closes and telemetry too, and any one of them still reading the
    global would stamp live ids onto rehearsal rows.

    Every case sets the process id to a LIVE id first, so a regression shows up
    as `run-liveone` appearing in a shadow store rather than as a silent pass.
    """

    LIVE = "run-liveone"
    MINE = "shadow-scopedaaaaa"

    @pytest.fixture()
    def reg(self, tmp_path, monkeypatch):
        from core_brain import order_registry
        from core_brain.order_registry import OrderRegistry

        monkeypatch.setattr(order_registry, "_CURRENT_RUN_ID", self.LIVE)
        return OrderRegistry(db_path=tmp_path / "scoped.db", run_id=self.MINE)

    def _one(self, reg, table):
        import sqlite3

        conn = sqlite3.connect(reg.db_path)
        try:
            conn.row_factory = sqlite3.Row
            return [r["run_id"] for r in conn.execute(f"SELECT run_id FROM {table}")]
        finally:
            conn.close()

    def test_orders(self, reg):
        from core_brain.order_registry import OrderRecord

        reg.create_order(OrderRecord(
            id="local-1", order_id="oid-1", condition_id="0xabc",
            token_id="tok", side="BUY", price=0.47, original_size=20,
            status="open", posted_ts=1, last_polled_ts=1))
        assert self._one(reg, "orders") == [self.MINE]

    def test_fills(self, reg):
        from core_brain.order_registry import FillRecord, OrderRecord

        reg.create_order(OrderRecord(
            id="local-1", order_id="oid-1", condition_id="0xabc",
            token_id="tok", side="BUY", price=0.47, original_size=20,
            status="open", posted_ts=1, last_polled_ts=1))
        reg.record_fill(FillRecord(trade_id="t1", order_uuid="local-1",
                                   size=5.0, price=0.47))
        assert self._one(reg, "fills") == [self.MINE]

    def test_quotes(self, reg):
        from core_brain.order_registry import QuoteRecord

        reg.log_quote(QuoteRecord(ts=1.0, condition_id="0xabc", token_id="tok",
                                  side="BUY", price=0.47, size=20))
        assert self._one(reg, "quotes") == [self.MINE]

    def test_market_events(self, reg):
        from core_brain.order_registry import MarketEventRecord

        reg.log_market_event(MarketEventRecord(
            ts=1.0, condition_id="0xabc", kind="skip", reason="test"))
        assert self._one(reg, "market_events") == [self.MINE]

    def test_markouts(self, reg):
        from core_brain.order_registry import MarkoutRecord

        reg.log_markout(MarkoutRecord(ts=1.0, condition_id="0xabc", side="BUY",
                                      fill_price=0.47, size=5.0))
        assert self._one(reg, "markouts") == [self.MINE]

    def test_closes(self, reg):
        from core_brain.order_registry import CloseRecord

        reg.log_close(CloseRecord(ts=1.0, condition_id="0xabc",
                                  method="shadow_merge"))
        assert self._one(reg, "closes") == [self.MINE]

    def test_venue_errors(self, reg):
        from core_brain.order_registry import VenueErrorRecord

        reg.log_venue_error(VenueErrorRecord(
            ts=1.0, condition_id="0xabc", side="BUY", price=0.47, size=20,
            error_code="X", raw_error_msg="boom"))
        assert self._one(reg, "venue_errors") == [self.MINE]

    def test_float_and_account_marks(self, reg):
        reg.log_float_mark(unrealized_usd=1.0, committed_open_usd=10.0,
                           naked_usd=0.0, ts=1.0)
        reg.log_account_mark({"account_value_usd": 100.0}, ts=1.0)
        assert self._one(reg, "float_marks") == [self.MINE]
        assert self._one(reg, "account_marks") == [self.MINE]

    def test_an_explicit_record_id_still_wins(self, reg):
        """The `record.run_id or ...` half of the contract is unchanged."""
        from core_brain.order_registry import QuoteRecord

        reg.log_quote(QuoteRecord(ts=1.0, condition_id="0xabc", token_id="tok",
                                  side="BUY", price=0.47, size=20,
                                  run_id="shadow-explicit"))
        assert self._one(reg, "quotes") == ["shadow-explicit"]
