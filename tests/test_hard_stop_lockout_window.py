"""#796: a hard stop closes its own market to fresh entries for a fixed window.

The #646 gate clears a mark when the picker publishes a newer feed that lists
the market again. For a 15-minute sports market that happens seconds after the
stop, so the run bought the same decaying side twice: `bun-stu-moe` was re-entered
four minutes after stop-out and the replacement was liquidated too. The window is
the floor on that, and it has two ends -- no publication younger than the window
may clear a hard-stop mark, and once the window has passed the block releases on
its own, so a market the feed stopped listing is not locked out for the run.

A merge's reset mark is not a hard stop and keeps the #646 publication-only rule.
"""

from __future__ import annotations

import time
from dataclasses import replace

from core_brain.config import DEFAULT_HARD_STOP_LOCKOUT_SEC, load
from core_brain.order_registry import OrderRegistry
from core_brain.trader_loop import MarketSpecList, run
from tests.test_trader_loop import FakeMarket, VenueSeam, _intent

CID = "0xLockout"


def _reg(tmp_path):
    return OrderRegistry(db_path=tmp_path / "t.db")


def _books(token="tok-up"):
    return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
            "bids": {0.59: 100}, "asks": {0.61: 100}}


def _seam(reg, submitted, lockout_sec=None):
    def decide(cfg, up, dn, inv, t_rem, wf):
        return [_intent()], ""

    base_cfg = None
    if lockout_sec is not None:
        base_cfg = replace(load(),
                           bot_risk_hard_stop_entry_lockout_sec=lockout_sec)
    return VenueSeam(
        client=object(),
        registry=reg,
        fetch_market=lambda cid: FakeMarket(cid),
        fetch_books=lambda host, token: _books(token),
        decide=decide,
        submit_fn=lambda c, r, m, i, cfg: submitted.append(
            [x.side for x in i]) or len(i),
        cancel_fn=lambda c, r, o: 0,
        reconcile_fn=lambda *a, **k: None,
        sweep_fn=lambda: None,
        base_cfg=base_cfg,
    )


def _run_once(reg, submitted, specs, lockout_sec=None):
    return run(_seam(reg, submitted, lockout_sec), interval=0.0, once=True,
               live=True, markets_fn=lambda: specs, sleep_fn=lambda s: None)


def _relisted_specs(*cids):
    """A publication newer than every mark in these tests, listing the market."""
    return MarketSpecList(
        [FakeMarket(cid) for cid in cids],
        published_mtime_ns=int(time.time() * 1_000_000_000),
        condition_ids=frozenset(c.lower() for c in cids))


def test_a_newer_publication_inside_the_window_does_not_reopen_the_market(
        tmp_path):
    """The whole point: the feed re-listing the market is not permission."""
    reg = _reg(tmp_path)
    reg.mark_hard_stop_block(CID, "hard_stop_exited",
                             int(time.time() * 1000) - 60_000)
    submitted = []

    results = _run_once(reg, submitted, _relisted_specs(CID))

    assert [(r.status, r.why) for r in results] == [
        ("SKIPPED", "awaiting_requalification")]
    assert submitted == []
    assert CID.lower() in reg.requalification_marks()


def test_a_merge_mark_still_clears_on_publication_inside_the_window(tmp_path):
    """Only hard stops get the floor; #646 keeps its rule for everything else."""
    reg = _reg(tmp_path)
    gen = reg.begin_lifecycle_reset(CID, "merged", now_ms=1_000)
    assert reg.finish_lifecycle_reset(CID, gen, reset_ts_ms=1_000)
    submitted = []

    results = _run_once(reg, submitted, _relisted_specs(CID))

    assert [r.status for r in results] == ["QUOTED"]
    assert submitted == [["UP"]]
    assert reg.requalification_marks() == {}


def test_the_window_ending_releases_the_block_without_a_publication(tmp_path):
    """A market the feed never re-lists is quotable again once the floor passes.

    The supplier here carries no publication metadata, so nothing but the
    release sweep can clear the mark -- which is the case the window exists for.
    """
    reg = _reg(tmp_path)
    stale_ms = int((time.time() - DEFAULT_HARD_STOP_LOCKOUT_SEC - 60) * 1000)
    reg.mark_hard_stop_block(CID, "hard_stop_exited", stale_ms)
    submitted = []

    results = _run_once(reg, submitted, [FakeMarket(CID)])

    assert [r.status for r in results] == ["QUOTED"]
    assert submitted == [["UP"]]
    assert reg.requalification_marks() == {}


def test_the_window_holding_one_second_past_its_end_is_still_a_block(tmp_path):
    """The mark inside the window survives the same loop that releases it."""
    reg = _reg(tmp_path)
    fresh_ms = int((time.time() - DEFAULT_HARD_STOP_LOCKOUT_SEC + 60) * 1000)
    reg.mark_hard_stop_block(CID, "hard_stop_exited", fresh_ms)
    submitted = []

    results = _run_once(reg, submitted, [FakeMarket(CID)])

    assert [(r.status, r.why) for r in results] == [
        ("SKIPPED", "awaiting_requalification")]
    assert submitted == []


def test_the_release_sweep_leaves_a_merge_mark_alone(tmp_path):
    """Merge marks clear on publication only; the sweep is not a back door."""
    reg = _reg(tmp_path)
    gen = reg.begin_lifecycle_reset(CID, "merged", now_ms=1_000)
    assert reg.finish_lifecycle_reset(CID, gen, reset_ts_ms=1_000)

    released = reg.release_hard_stop_blocks(
        now_ms=int(time.time() * 1000), lockout_sec=900.0)

    assert released == 0
    assert CID.lower() in reg.requalification_marks()


def test_a_zero_lockout_leaves_the_publication_gate_alone(tmp_path):
    """0 disables the floor, which is the pre-#796 behaviour exactly."""
    reg = _reg(tmp_path)
    reg.mark_hard_stop_block(CID, "hard_stop_exited",
                             int(time.time() * 1000) - 60_000)
    submitted = []

    results = _run_once(reg, submitted, _relisted_specs(CID), lockout_sec=0.0)

    assert [r.status for r in results] == ["QUOTED"]
    assert submitted == [["UP"]]
    assert reg.requalification_marks() == {}


def test_a_zero_lockout_still_waits_for_a_publication(tmp_path):
    """Turning the floor off must not turn #816's block off with it.

    Nothing but a newer qualifying publication may clear a hard-stop mark when
    the window is disabled -- the sweep is not a back door where the supplier
    carries no publication metadata at all.
    """
    reg = _reg(tmp_path)
    reg.mark_hard_stop_block(CID, "hard_stop_exited",
                             int(time.time() * 1000) - 60_000)
    submitted = []

    results = _run_once(reg, submitted, [FakeMarket(CID)], lockout_sec=0.0)

    assert [(r.status, r.why) for r in results] == [
        ("SKIPPED", "awaiting_requalification")]
    assert submitted == []
    assert CID.lower() in reg.requalification_marks()
