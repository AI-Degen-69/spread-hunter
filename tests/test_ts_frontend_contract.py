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
    # Normalize to a leading-slash form: "api/state" -> "/api/state".
    return {c if c.startswith("/") else "/" + c for c in re.findall(r"api/[a-z0-9_/-]+", text)}


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
    """The POST control surface is exactly the controls the menu supports."""
    calls = _frontend_api_calls()
    control = {c for c in calls if "/system/" in c}
    assert control == {
        "/api/system/reset",
        "/api/system/service/start",
        "/api/system/service/stop",
        "/api/system/start",
        "/api/system/status",
        "/api/system/stop",
        "/api/system/sync",
    }