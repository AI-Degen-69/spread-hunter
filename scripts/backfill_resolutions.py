"""One-off backfill: mark externally-ended markets resolved in an existing DB.

The resolution sweeper (``core_brain.market_resolution.sweep_market_resolutions``)
normally runs inside a shadow session from the first rotation. Sessions started
before that wiring shipped (or a paused/restarted fleet) never ran it, so ended
markets still read QUOTING off their append-only quotes ledger. This script runs
one sweep pass against a chosen registry -- reusing the exact same sweep logic,
so the result is indistinguishable from a session that ran it itself.

Refuses the production registry (data/orders.db) exactly as a shadow run does:
it writes `resolutions` + settlement `closes`, which must never enter the real
order history.

Usage:
    python -m scripts.backfill_resolutions --db data/01_shadow_29-08_23-50.db [--run-id shadow-01]
    python -m scripts.backfill_resolutions --db data/orders.db   # refused

It reads the current map of markets only to know what to SKIP (markets still
in the universe feed are deemed still open); every candidate it acts on is
confirmed externally through the public gamma API first.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger("backfill_resolutions")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="registry sqlite path to sweep")
    parser.add_argument("--run-id", default=None,
                        help="run id to stamp resolutions/closes with (default: "
                             "registry's own run id)")
    parser.add_argument("--book-settlement", action="store_true",
                        help="also book held-share settlement PnL (shadow only)")
    parser.add_argument("--gamma-host", default=None,
                        help="public gamma host (default: default)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S")

    db_path = Path(args.db)
    if not db_path.exists():
        log.error("db does not exist: %s", db_path)
        return 2

    # Same guard a shadow run uses: never write resolutions/closes into the
    # production registry.
    from core_brain.shadow_guard import assert_not_production_registry
    try:
        assert_not_production_registry(db_path)
    except Exception as e:
        log.error("refusing to backfill: %s", e)
        return 2

    from core_brain.market_resolution import (
        DEFAULT_GAMMA_HOST, sweep_market_resolutions,
    )
    from core_brain.order_registry import OrderRegistry
    from core_brain.trader_loop import _market_specs

    registry = OrderRegistry(db_path=db_path, run_id=args.run_id)
    run_id = args.run_id
    if not run_id:
        # The registry's process-level run id is the LIVE lock file's id, not
        # necessarily the id the rows in a shadow store actually carry. Infer
        # from the store's own orders -- the id that names the session.
        with registry._conn() as conn:
            row = conn.execute(
                """SELECT run_id, COUNT(*) n FROM orders
                   WHERE run_id IS NOT NULL AND run_id != ''
                   GROUP BY run_id ORDER BY n DESC LIMIT 1"""
            ).fetchone()
        if row and row["run_id"]:
            run_id = row["run_id"]
        log.info("inferred run id: %s", run_id)
    host = args.gamma_host or DEFAULT_GAMMA_HOST

    # The current graduated universe = markets still considered active by the
    # ranker. Anything not in it is a resolution candidate for the sweep, which
    # then confirms each through gamma before recording.
    try:
        universe = _market_specs()
    except Exception as e:
        log.warning("could not load current universe feed; sweeping all touched "
                    "cids (gamma still confirms each): %s", e)
        universe = []

    results = sweep_market_resolutions(
        registry, db_path, markets=universe, gamma_host=host,
        run_id=run_id, book_settlement=args.book_settlement,
    )

    acted = [r for r in results if r.action in ("resolved_recorded",
                                                "partial_stranded")]
    if not acted:
        log.info("no new resolutions to record (already resolved, still open, "
                 "or unreachable).")
        for r in results:
            log.info("  %s %s", r.action, r.condition_id[:12])
        return 0

    log.info("%d market(s) newly recorded as resolved:", len(acted))
    for r in acted:
        log.info("  %s  winner=%s  cancelled=%d stranded=%d  settle_pnl=%s",
                 r.condition_id[:16], r.winning_token or "-",
                 r.cancelled_rows, r.partial_rows,
                 f"${r.settled_pnl:.2f}" if r.settled_pnl else "-")
    return 0


if __name__ == "__main__":
    sys.exit(main())