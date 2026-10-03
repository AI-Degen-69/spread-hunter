"""Release one dead loop's `instance_lock` row, or refuse.

A kill-stopped loop never runs the lock's `finally` cleanup, so its row
survives and the next loop dies with `InstanceInUse`. The caller (the menu's
resume path) proves the holder PID dead with its own process scan and passes
it here; this script deletes the row only when the holder still matches that
PID. Fail-closed: a live holder, a mismatched holder, an unreadable table, or
the production registry refuses instead of deleting.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_ERROR = 1

class LockRefusal(SystemExit):
    """Fail-closed refusal that keeps its reason for the JSON report."""

    def __init__(self, code: int, message: str):
        super().__init__(code)
        self.message = message


def _refuse(msg: str) -> "NoReturn":
    raise LockRefusal(EXIT_REFUSED, msg)


def _fail(msg: str) -> "NoReturn":
    raise LockRefusal(EXIT_ERROR, msg)


def _guard_production(db: Path) -> None:
    try:
        if db.resolve().name == "orders.db":
            _refuse(f"Refusing: {db} looks like the production registry.")
    except OSError:
        _fail(f"Could not resolve path: {db}")


def read_lock(db_path: Path | str, role: str) -> dict | None:
    """Return the row for `role` as {holder, acquired_ts, age_ms}, or None."""
    db = Path(db_path)
    if not db.is_file():
        _fail(f"Store not found: {db}")
    _guard_production(db)
    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT holder, acquired_ts FROM instance_lock WHERE role = ?",
                (role,),
            ).fetchone()
    except sqlite3.Error as exc:
        _refuse(f"Lock table unreadable: {exc}")
    if row is None:
        return None
    now_ms = int(time.time() * 1000)
    return {
        "holder": str(row["holder"]),
        "acquired_ts": int(row["acquired_ts"]),
        "age_ms": now_ms - int(row["acquired_ts"]),
    }


def release_if_holder(
    db_path: Path | str, role: str, holder_pid: int
) -> tuple[bool, dict | None]:
    """Delete the row only when its holder is `<holder_pid>:*`.

    Returns (released, row-before-delete). Empty store is a successful no-op.
    """
    row = read_lock(db_path, role)
    if row is None:
        return False, None
    if not str(row["holder"]).startswith(f"{int(holder_pid)}:"):
        return False, row
    db = Path(db_path)
    try:
        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "DELETE FROM instance_lock WHERE role = ? AND holder = ?",
                (role, row["holder"]),
            )
            conn.commit()
            if cur.rowcount == 0:
                return False, row
    except sqlite3.Error as exc:
        _refuse(f"Release failed: {exc}")
    return True, row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Resume store to operate on.")
    parser.add_argument("--role", default="fleet", help="Lock role (default: fleet).")
    parser.add_argument("--holder-pid", required=False, type=int, default=None,
                        help="Dead holder PID the row must belong to. "
                        "Omitted: show the row without touching it.")
    args = parser.parse_args(argv)
    if args.holder_pid is None:
        try:
            row = read_lock(args.db, args.role)
        except LockRefusal as exc:
            print(json.dumps({"holder": None, "age_ms": None,
                              "error": exc.message}))
            return int(exc.code)
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else EXIT_ERROR
            print(json.dumps({"holder": None, "age_ms": None}))
            return code
        print(json.dumps({"holder": row["holder"] if row else None,
                          "age_ms": row["age_ms"] if row else None}))
        return EXIT_OK
    try:
        released, row = release_if_holder(args.db, args.role, args.holder_pid)
        if row is not None and not released:
            _refuse(f"Holder {row['holder']} does not match PID "
                      f"{args.holder_pid}; refusing.")
    except LockRefusal as exc:
        print(json.dumps({"released": False, "holder": None,
                          "age_ms": None, "error": exc.message}))
        return int(exc.code)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else EXIT_ERROR
        print(json.dumps({"released": False, "holder": None,
                          "age_ms": None}))
        return code
    print(json.dumps({"released": released,
                      "holder": row["holder"] if row else None,
                      "age_ms": row["age_ms"] if row else None}))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
