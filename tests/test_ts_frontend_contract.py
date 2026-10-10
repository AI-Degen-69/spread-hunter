"""Every /api/* endpoint the TS frontend calls must exist on the Python server.

The bridge (server.ts) can only proxy what Python actually serves; a frontend
calling an endpoint Python does not define would silently 404 through the
bridge. This test is the drift guard.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "dashboard" / "static" / "app.js"
SERVER_PY = ROOT / "dashboard" / "server.py"


def _frontend_api_calls() -> set[str]:
    text = APP_JS.read_text(encoding="utf-8")
    # The master-toggle builds its control URL as `/api/system/${action}`, where
    # `action` is 'start' or 'stop' at runtime. Emit BOTH concrete endpoints so
    # the scan sees what the browser can actually call instead of the bare
    # `/api/system/` prefix the template leaves in the source (a path Python
    # never serves). A single `.replace()` would drop the second branch, so the
    # interpolation is expanded into both variants on separate source copies.
    expanded = {text.replace("${action}", variant)
                for variant in ("start", "stop")}
    # Normalize to a leading-slash form: "api/state" -> "/api/state".
    calls: set[str] = set()
    for source in expanded:
        calls |= {c if c.startswith("/") else "/" + c
                  for c in re.findall(r"api/[a-z0-9_/-]+", source)}
    return calls


def test_master_toggle_control_url_expands_to_served_endpoints():
    """`/api/system/${action}` must not leak the bare `/api/system/` prefix.

    The regex scan can read past a `${...}` only if the interpolation is
    expanded first; the two real targets, start and stop, must both survive and
    the phantom `/api/system/` (which Python does not route) must be gone.
    """
    calls = _frontend_api_calls()
    assert "/api/system/start" in calls
    assert "/api/system/stop" in calls
    assert "/api/system/" not in calls


def _python_api_routes() -> set[str]:
    text = SERVER_PY.read_text(encoding="utf-8")
    # re.findall with a single capture group returns the group strings directly.
    return set(re.findall(r'"(/api/[a-z0-9_/-]+)"', text))


def test_every_frontend_call_is_served_by_python():
    calls = _frontend_api_calls()
    routes = _python_api_routes()
    missing = {c for c in calls if c not in routes}
    assert not missing, f"frontend calls not served by Python: {sorted(missing)}"


def test_frontend_control_surface_is_expected():
    """The POST control surface is exactly the controls the menu supports.

    `/api/system/db` (the run switcher) is in this list on purpose. It is a
    POST that changes what the dashboard reads, guarded by the same control
    token as START and STOP, so it belongs in the declared surface -- a new
    control endpoint that nobody added here is exactly the drift this guards.
    """
    calls = _frontend_api_calls()
    control = {c for c in calls if "/system/" in c}
    assert control == {
        "/api/system/db",
        "/api/system/reset",
        "/api/system/service/start",
        "/api/system/service/stop",
        "/api/system/shadow/start",
        "/api/system/shadow/stop",
        "/api/system/start",
        "/api/system/status",
        "/api/system/stop",
        "/api/system/sync",
    }