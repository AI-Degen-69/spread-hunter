"""Guarded execution behind one seam (#805).

Every cancel-all routes through here: the Order Manager verb and the
Dashboard server endpoints are adapters that render the result their own
way. The venue is injected, never created here, so tests drive the seam
with fakes and no adapter can wedge venue wiring into the call. Cancelling
is idempotent by construction -- an empty book still answers success, so a
repeated cancel stays ok.

There are no dollar caps on this path: cancelling rests nothing and can
never assemble a pair over $1.00. The guards are the live gate (a dry run
never touches the venue) and refusal mapping (a venue failure returns a
refusal, never raises).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class CancelAllResult:
    """What one cancel-all decided."""

    ok: bool
    dry_run: bool
    response: Any = None
    refusal: Optional[str] = None


def cancel_all_orders(
    *,
    client_fn: Callable[[], Any],
    live: bool,
) -> CancelAllResult:
    """Cancel every open order through the injected venue client.

    `live=False` is a dry run: the client is never built, let alone called.
    A venue failure is returned as a refusal so adapters decide how to
    report it (CLI exit code, dashboard status code).
    """
    if not live:
        return CancelAllResult(ok=False, dry_run=True)
    try:
        response = client_fn().cancel_all()
    except Exception as exc:
        return CancelAllResult(ok=False, dry_run=False, refusal=str(exc))
    return CancelAllResult(ok=True, dry_run=False, response=response)
