"""Hands-on proof for #796: a hard-stopped market does not reopen early.

Runs the production functions the trader uses -- `mark_hard_stop_block`,
`_evaluate_requalification`, `release_hard_stop_blocks`, `_enforce_entry_gate`
-- against a throwaway registry, with a synthetic clock so the whole window fits
in one second. No venue, no signer, no money, and nothing is written under
`data/` or `runtime/`.

    python scripts/verify_796_lockout.py

Exit code 0 when every step holds, 1 otherwise.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_brain.config import DEFAULT_HARD_STOP_LOCKOUT_SEC
from core_brain.order_registry import OrderRegistry
from core_brain.trader_loop import (
    MarketSpecList, _enforce_entry_gate, _evaluate_requalification,
)

CID = "0xbun-stu-moe"
WINDOW_SEC = DEFAULT_HARD_STOP_LOCKOUT_SEC
PAIR = "pair-managed-on-the-same-market"
T0 = int(time.time() * 1000)


class _Intent:
    def __init__(self, pair_id=None):
        self.pair_id = pair_id


def _publication(offset_sec: float) -> MarketSpecList:
    """A feed publication that lists the market, `offset_sec` after the stop."""
    return MarketSpecList(
        [], published_mtime_ns=(T0 + int(offset_sec * 1000)) * 1_000_000,
        condition_ids=frozenset({CID.lower()}))


def _blocked(registry: OrderRegistry) -> tuple[int, bool]:
    """(fresh submits kept, blocked) through the trader's real entry gate."""
    seam = SimpleNamespace(registry=registry)
    kept, blocked = _enforce_entry_gate(seam, CID, [_Intent()], None)
    return len(kept), blocked


def main() -> int:
    failures: list[str] = []

    def step(label: str, ok: bool, detail: str) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        reg = OrderRegistry(db_path=Path(tmp) / "proof.db")

        # 1. The hard stop lands. #816's mark goes in without touching rows,
        #    so the residual stays managed.
        reg.mark_hard_stop_block(CID, "hard_stop_exited", T0)
        marks = reg.requalification_marks()
        mark = marks.get(CID.lower())
        step("stop marks the market",
             mark is not None and mark.status == "awaiting",
             f"mark status={mark.status if mark else 'none'}")

        # 2. The picker re-lists the market 45s later -- the exact case that
        #    reopened bun-stu-moe four minutes after its stop.
        _evaluate_requalification(reg, _publication(45), lockout_sec=WINDOW_SEC)
        kept, blocked = _blocked(reg)
        step("publication 45s after the stop does not reopen",
             CID.lower() in reg.requalification_marks() and blocked and kept == 0,
             "mark kept, fresh submit refused")

        # 3. A pair already managed on the same market keeps its hedge.
        seam = SimpleNamespace(registry=reg)
        hedge = _Intent(PAIR)
        kept_hedge, blocked_hedge = _enforce_entry_gate(
            seam, CID, [hedge, _Intent()], PAIR)
        step("managed pair on the same market keeps hedging",
             kept_hedge == [hedge] and blocked_hedge,
             "lifecycle submit kept, fresh submit still dropped")

        # 4. Inside the window but one minute from its end, still closed.
        _evaluate_requalification(
            reg, _publication(WINDOW_SEC - 60), lockout_sec=WINDOW_SEC)
        _, blocked_late = _blocked(reg)
        step("publication 60s before the window ends still does not reopen",
             blocked_late and CID.lower() in reg.requalification_marks(),
             "mark kept")

        # 5. Past the window the rotation releases the block itself, so a
        #    market the feed never re-lists is quotable again.
        released = reg.release_hard_stop_blocks(
            now_ms=T0 + int((WINDOW_SEC + 60) * 1000), lockout_sec=WINDOW_SEC)
        _, blocked_after = _blocked(reg)
        step("window end releases the block",
             released == 1 and not blocked_after,
             f"released={released}, fresh submit allowed")

        # 6. Control: a merge's mark is not a hard stop and keeps the #646
        #    publication-only rule -- it clears on the re-listing above.
        merge_cid = "0xmerged-market"
        gen = reg.begin_lifecycle_reset(merge_cid, "merged", now_ms=T0)
        reg.finish_lifecycle_reset(merge_cid, gen, reset_ts_ms=T0)
        fresh = MarketSpecList(
            [], published_mtime_ns=(T0 + 45_000) * 1_000_000,
            condition_ids=frozenset({merge_cid}))
        _evaluate_requalification(reg, fresh, lockout_sec=WINDOW_SEC)
        step("a merge mark still clears on publication inside the window",
             merge_cid not in reg.requalification_marks(),
             "merge mark cleared")

    print()
    if failures:
        print(f"{len(failures)} step(s) failed: {', '.join(failures)}")
        return 1
    print(f"All steps held. Window = BOT_RISK_HARD_STOP_ENTRY_LOCKOUT_SEC "
          f"({WINDOW_SEC:.0f}s default).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
