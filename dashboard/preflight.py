"""Preflight verification suite for the Start Run launcher (#857).

Sequential, fail-closed checks that gate the live stack. No function here
places an order except `check_reach_probe`, which runs the controlled
1-share post-only buy-and-cancel probe and only with explicit operator
confirmation. Tests inject all venue-touching callables; nothing here
touches the network unless the default production callables are used.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

PREFLIGHT_TTL_SEC = 300.0
PREFLIGHT_STEPS = ("credentials", "screener", "stream", "reach_probe")
SCREENER_MAX_AGE_SEC = 15 * 60.0

_lock = threading.Lock()
_sessions: dict[str, dict[str, Any]] = {}


def _now() -> float:
    return time.time()


def _prune_locked(now: float | None = None) -> None:
    now = _now() if now is None else now
    expired = [pid for pid, s in _sessions.items()
               if now - float(s.get("created_at", 0.0)) > PREFLIGHT_TTL_SEC]
    for pid in expired:
        _sessions.pop(pid, None)


def new_session() -> str:
    """Create a fresh preflight session and return its id."""
    pid = uuid.uuid4().hex
    with _lock:
        _prune_locked()
        _sessions[pid] = {
            "created_at": _now(),
            "steps": {},
            "approved": False,
            "consumed": False,
        }
    return pid


def get_session(preflight_id: str | None) -> dict[str, Any] | None:
    """Return the session, or None when missing/expired (fail closed)."""
    if not preflight_id or not isinstance(preflight_id, str):
        return None
    with _lock:
        _prune_locked()
        sess = _sessions.get(preflight_id)
        if sess is None:
            return None
        return {"created_at": sess["created_at"],
                "steps": dict(sess["steps"]),
                "approved": bool(sess.get("approved")),
                "consumed": bool(sess.get("consumed"))}


def _next_step(sess: dict[str, Any]) -> str | None:
    for step in PREFLIGHT_STEPS:
        entry = sess["steps"].get(step)
        if not (isinstance(entry, dict) and entry.get("ok") is True):
            return step
    return None


def submit_step(preflight_id: str, step: str, ok: bool,
                summary: str = "", details: dict | None = None) -> tuple[bool, str | None]:
    """Record one step result, enforcing strict sequential order.

    Returns (accepted, error). A failed step is recorded but does not advance;
    the caller must retry the same step.
    """
    if step not in PREFLIGHT_STEPS:
        return False, f"Unknown step {step!r}."
    with _lock:
        _prune_locked()
        sess = _sessions.get(preflight_id)
        if sess is None:
            return False, "Unknown or expired preflight_id."
        if sess.get("consumed"):
            return False, "preflight_id already consumed."
        expected = _next_step(sess)
        if expected is None:
            return False, "All steps already passed."
        if step != expected:
            return False, f"Out of order: expected {expected!r}, got {step!r}."
        sess["steps"][step] = {
            "ok": bool(ok),
            "summary": summary,
            "details": details or {},
            "at": _now(),
        }
        if bool(ok) and all(
            isinstance(sess["steps"].get(s), dict) and sess["steps"][s].get("ok") is True
            for s in PREFLIGHT_STEPS
        ):
            sess["approved"] = True
        return True, None


def try_approve(preflight_id: str) -> bool:
    """Mark approved when all four steps passed; return approval state."""
    with _lock:
        _prune_locked()
        sess = _sessions.get(preflight_id)
        if sess is None or sess.get("consumed"):
            return False
        complete = all(
            isinstance(sess["steps"].get(s), dict) and sess["steps"][s].get("ok") is True
            for s in PREFLIGHT_STEPS
        )
        if complete:
            sess["approved"] = True
            return True
        return False


def consume_approval(preflight_id: str | None) -> bool:
    """Atomically consume a single-use approval. Fail closed on any doubt."""
    if not preflight_id or not isinstance(preflight_id, str):
        return False
    with _lock:
        _prune_locked()
        sess = _sessions.get(preflight_id)
        if sess is None:
            return False
        if sess.get("consumed"):
            return False
        if not sess.get("approved"):
            return False
        sess["consumed"] = True
        _sessions.pop(preflight_id, None)
        return True


def clear_sessions() -> None:
    """Test-only reset of the in-memory store."""
    with _lock:
        _sessions.clear()


# --- individual checks (pure, injectable, fail closed) ---------------------


def check_credentials(balance_fn: Callable[..., dict] | None = None,
                      env: dict | None = None) -> dict[str, Any]:
    """Validate signer/funder presence and USDC balance/allowance floor."""
    import os as _os

    from core_brain.config import VENUE_MIN_NOTIONAL_USD

    env = env if env is not None else dict(_os.environ)
    has_key = bool(env.get("POLY_PRIVATE_KEY") or env.get("POLY_KEY"))
    funder = env.get("POLY_FUNDER") or ""
    if not has_key or not funder:
        return {"ok": False,
                "summary": "Missing Polymarket credentials or funder address",
                "details": {"has_key": has_key, "has_funder": bool(funder)}}
    fn = balance_fn
    if fn is None:
        from core_brain.order_manager import get_balance_allowance as _bal
        fn = _bal
    try:
        info = fn()
    except SystemExit as exc:
        return {"ok": False, "summary": f"Balance read refused (exit {exc.code})",
                "details": {"error": "SystemExit"}}
    except Exception as exc:
        return {"ok": False, "summary": f"Balance read failed: {type(exc).__name__}",
                "details": {"error": f"{type(exc).__name__}: {exc}"}}
    try:
        bal = float(info.get("balance_usd", 0.0) or 0.0)
        allow = float(info.get("allowance_usd", 0.0) or 0.0)
    except (TypeError, ValueError):
        return {"ok": False, "summary": "Unreadable balance response",
                "details": {}}
    floor = float(VENUE_MIN_NOTIONAL_USD)
    if bal < floor or allow < floor:
        return {"ok": False,
                "summary": f"Balance ${bal:,.2f} / allowance ${allow:,.2f} below ${floor:,.2f} minimum",
                "details": {"balance_usd": bal, "allowance_usd": allow,
                            "minimum_usd": floor}}
    open_orders = info.get("open_orders")
    details: dict[str, Any] = {"balance_usd": bal, "allowance_usd": allow,
                               "minimum_usd": floor}
    if open_orders is not None:
        details["open_orders"] = open_orders
    return {"ok": True,
            "summary": f"Balance ${bal:,.2f} USDC, allowance verified, "
                       f"{details.get('open_orders', 0)} open orders"
            if "open_orders" in details else
            f"Balance ${bal:,.2f} USDC, allowance verified",
            "details": details}


def check_screener(snapshot_fn: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Reject a stale or missing market screener snapshot (>15 min)."""
    fn = snapshot_fn
    if fn is None:
        from core_brain.market_feed import load_graduated_snapshot as _load
        fn = _load
    try:
        snap = fn()
    except Exception as exc:
        return {"ok": False,
                "summary": f"Screener unreadable: {type(exc).__name__}",
                "details": {"error": f"{type(exc).__name__}: {exc}"}}
    try:
        rows = getattr(snap, "rows", snap)
        count = len(rows) if rows is not None else 0
    except TypeError:
        count = 0
    published_ns = getattr(snap, "published_mtime_ns", None)
    age_sec: float | None = None
    if published_ns is not None:
        try:
            age_sec = max(0.0, time.time() - float(published_ns) / 1e9)
        except (TypeError, ValueError):
            age_sec = None
    if age_sec is not None and age_sec > SCREENER_MAX_AGE_SEC:
        return {"ok": False,
                "summary": f"Screener data stale ({age_sec:.0f}s old)",
                "details": {"count": count, "age_sec": age_sec}}
    if count <= 0:
        return {"ok": False, "summary": "Screener returned no markets",
                "details": {"count": 0, "age_sec": age_sec}}
    summary = f"Screener fresh: {count} markets"
    if age_sec is not None:
        summary += f" ({age_sec:.0f}s old)"
    return {"ok": True, "summary": summary,
            "details": {"count": count, "age_sec": age_sec}}


def check_stream(book_fn: Callable[[str], dict] | None = None,
                 stream_factory: Callable[[], Any] | None = None,
                 snapshot_fn: Callable[..., Any] | None = None,
                 timeout_sec: float = 8.0) -> dict[str, Any]:
    """Fast REST book read plus a short-lived market-stream connectivity probe."""
    import os as _os

    rows: Any = None
    if snapshot_fn is None:
        try:
            from core_brain.market_feed import load_graduated_markets as _gm
            rows = _gm()
        except Exception:
            rows = None
    else:
        try:
            rows = snapshot_fn()
        except Exception:
            rows = None
    token_id: str | None = None
    try:
        first = rows[0] if rows else None
        token_id = getattr(first, "up_token", None) or getattr(first, "token_id", None)
        if token_id is None and isinstance(first, dict):
            token_id = first.get("up_token") or first.get("token_id")
    except (TypeError, IndexError, AttributeError):
        token_id = None
    if not token_id:
        return {"ok": False, "summary": "No market token available for stream probe",
                "details": {}}

    if book_fn is None:
        from core_brain.markets import full_book as _full

        host = _os.environ.get("CLOB_HOST", "https://clob.polymarket.com")

        def _default_book(tok: str) -> dict:
            return _full(host, tok)

        book_fn = _default_book
    try:
        book = book_fn(token_id)
    except Exception as exc:
        return {"ok": False,
                "summary": f"REST book read failed: {type(exc).__name__}",
                "details": {"error": f"{type(exc).__name__}: {exc}"}}
    if not isinstance(book, dict) or not book:
        return {"ok": False, "summary": "REST book read returned no depth",
                "details": {}}

    if stream_factory is None:
        from core_brain.market_stream import MarketStream as _MS

        stream_factory = _MS
    stream = None
    try:
        stream = stream_factory()
        start = getattr(stream, "start", None)
        if callable(start):
            start()
        deadline = time.time() + max(0.5, min(timeout_sec, 8.0))
        connected = False
        while time.time() < deadline:
            prop = getattr(stream, "connected", False)
            flag = bool(prop() if callable(prop) else prop)
            if flag:
                connected = True
                break
            time.sleep(0.05)
        if not connected:
            # A silent feed is a refusal, not a pass; REST proved the venue
            # answers, but the socket did not come up in time.
            return {"ok": False, "summary": "Market stream did not connect in time",
                    "details": {"token_id": token_id}}
    except Exception as exc:
        return {"ok": False,
                "summary": f"Stream probe failed: {type(exc).__name__}",
                "details": {"error": f"{type(exc).__name__}: {exc}"}}
    finally:
        try:
            stop = getattr(stream, "stop", None)
            if callable(stop):
                stop()
        except Exception:
            pass
    return {"ok": True, "summary": "Market data reachable (REST + stream)",
            "details": {"token_id": token_id}}


def check_reach_probe(confirm: bool = False,
                      probe_fn: Callable[..., dict] | None = None) -> dict[str, Any]:
    """Controlled 1-share post-only buy-and-cancel verification.

    Refuses without explicit `confirm=True`. Passes ONLY on a verified
    cancel with zero matched size.
    """
    if confirm is not True:
        return {"ok": False,
                "summary": "Reach probe needs explicit operator confirmation",
                "details": {"requires_confirm": True}}
    fn = probe_fn
    if fn is None:
        from core_brain.reach_probe import reach_probe_cmd as _probe
        fn = _probe
    try:
        evidence = fn()
    except SystemExit as exc:
        return {"ok": False,
                "summary": f"Reach probe refused (exit {exc.code})",
                "details": {"error": "SystemExit"}}
    except Exception as exc:
        return {"ok": False,
                "summary": f"Reach probe failed: {type(exc).__name__}",
                "details": {"error": f"{type(exc).__name__}: {exc}"}}
    if not isinstance(evidence, dict):
        return {"ok": False, "summary": "Reach probe returned no evidence",
                "details": {}}
    result = str(evidence.get("result", "")).upper()
    matched = evidence.get("matched_size", evidence.get("matched", 0))
    try:
        matched_f = float(matched or 0)
    except (TypeError, ValueError):
        matched_f = 999.0
    cancelled = evidence.get("cancelled", result == "PASS")
    if result == "PASS" and matched_f == 0 and cancelled:
        return {"ok": True, "summary": "Reach probe passed: order cancelled, 0 matched",
                "details": {"order_id": evidence.get("order_id"),
                            "matched_size": 0}}
    return {"ok": False,
            "summary": "Reach probe did not verify a clean cancel",
            "details": {"result": result, "matched_size": matched_f}}
