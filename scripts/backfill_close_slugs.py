"""One-off backfill: fill missing closes.market_slug from quotes in an existing DB.

Shadow writers (merge sweep, rescue exits) never carried the slug, so every
close in older tournament stores reads as market ``None`` while ``quotes``
knew it all along (``pair_fill_report`` prints a full page of ``None`` rows).
New closes resolve the slug at write time (``OrderRegistry.log_close``); this
script repairs stores written before that wiring shipped.

Refuses the production registry (data/orders.db) exactly as a shadow run
does: rewriting history there is never a migration.

Usage:
    python -m scripts.backfill_close_slugs --db data/487_tournament_05_prudent-2_20261011-231751.db
    python -m scripts.backfill_close_slugs --db data/orders.db   # refused
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger("backfill_close_slugs")


def backfill(db_path: Path) -> tuple[int, int]:
    """Fill NULL closes.market_slug from quotes. Returns (fixed, still_null)."""
    conn = sqlite3.connect(db_path)
    try:
        missing = conn.execute(
            "SELECT COUNT(*) FROM closes WHERE market_slug IS NULL"
        ).fetchone()[0]
        # Row-by-row: SQLite does not correlate the UPDATE target inside a
        # scalar subquery, so the lookup runs as its own statement. Same-run
        # quotes win; any quote for the condition is the fallback.
        for rowid, condition_id, run_id in conn.execute(
            "SELECT rowid, condition_id, run_id FROM closes"
            " WHERE market_slug IS NULL"
        ).fetchall():
            slug_row = conn.execute(
                "SELECT market_slug FROM quotes"
                " WHERE condition_id = ? AND market_slug IS NOT NULL"
                " AND run_id = ? ORDER BY ts DESC LIMIT 1",
                (condition_id, run_id),
            ).fetchone()
            if slug_row is None:
                slug_row = conn.execute(
                    "SELECT market_slug FROM quotes"
                    " WHERE condition_id = ? AND market_slug IS NOT NULL"
                    " ORDER BY ts DESC LIMIT 1",
                    (condition_id,),
                ).fetchone()
            if slug_row is not None:
                conn.execute("UPDATE closes SET market_slug = ? WHERE rowid = ?",
                             (slug_row[0], rowid))
        conn.commit()
        fixed_row = conn.execute(
            "SELECT COUNT(*) FROM closes WHERE market_slug IS NOT NULL"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM closes").fetchone()[0]
        log.info("closes missing slug before: %d", missing)
        log.info("closes with slug after: %d / %d", fixed_row, total)
        return missing, total - fixed_row
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="registry sqlite path to repair")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S")

    db_path = Path(args.db)
    if not db_path.exists():
        log.error("db does not exist: %s", db_path)
        return 2

    # Same guard a shadow run uses: never rewrite the production registry.
    # BaseException on purpose: ShadowSafetyViolation deliberately escapes
    # `except Exception` (a trader-loop handler must never swallow it). This
    # is the CLI edge, not a loop handler, so it is caught here for a clean
    # refusal instead of a traceback.
    from core_brain.shadow_guard import assert_not_production_registry
    try:
        assert_not_production_registry(db_path)
    except BaseException as e:
        log.error("refusing to backfill: %s", e)
        return 2

    missing, still_null = backfill(db_path)
    if still_null:
        log.warning("%d closes still have no slug (no quotes for condition)",
                    still_null)
    log.info("done: repaired %d closes", missing - still_null)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
