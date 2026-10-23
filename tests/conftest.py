"""live/tests/conftest.py - Hermetic environment fixtures for live test suite.

Invariants:
1. Every test runs with credentials scrubbed from os.environ by default.
2. Any outbound network connection to a non-loopback address is blocked and raises RuntimeError.
3. No test may open the production registry `data/orders.db`.
4. No test may depend on a `PORT` exported by the operator's shell: it is
   dropped at conftest import, before any test module is collected.
"""
from __future__ import annotations

import os
import re
import socket
import sqlite3
import sys
import urllib.parse
from pathlib import Path

# Ensure project root is always first on sys.path for any runner (IDE or CLI)
REPO_ROOT = Path(__file__).resolve().parent.parent
repo_root_str = str(REPO_ROOT)
while repo_root_str in sys.path:
    sys.path.remove(repo_root_str)
sys.path.insert(0, repo_root_str)

# `dashboard/server.py` resolves its port at import time (`resolve_port(None)`),
# BEFORE any `--port` flag is parsed, so an unusable `PORT` inherited from the
# operator's shell -- the ambient `PORT=0` one session in this repo hit --
# raises `ValueError: PORT='0' is outside 1-65535` while pytest is still
# COLLECTING. Collection stops there, so every test file that imports the app
# fails without a single test having run (18 files when this was measured).
#
# Dropped here, at conftest import, because that is the last point that is
# guaranteed to precede collection: a fixture or an autouse fixture runs per
# test, long after the import that raises. `scripts/spread-hunter-menu.ps1`
# drops the same variable for the dashboard child it spawns. A test that wants
# a port sets its own (`tests/test_dashboard_port.py`).
os.environ.pop("PORT", None)

import pytest

from core_brain.order_registry import DEFAULT_DB_PATH

CREDENTIAL_VARS = (
    "POLY_PRIVATE_KEY",
    "POLY_KEY",
    "POLY_API_KEY",
    "POLY_API_SECRET",
    "POLY_API_PASSPHRASE",
    "POLY_FUNDER",
    "POLY_SIG_TYPE",
    "PRIVATE_KEY",
    "RELAYER_API_KEY",
    "RELAYER_API_KEY_ADDRESS",
    "POLYGON_RPC",
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: opt-in marker to allow network access in a test"
    )


def scrub_credentials(monkeypatch) -> None:
    """Scrub venue credentials and sensitive endpoint configs from os.environ."""
    for var in CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def scrub_credential_env(monkeypatch):
    """Scrub venue credentials and sensitive endpoint configs from os.environ."""
    scrub_credentials(monkeypatch)


@pytest.fixture(autouse=True)
def scrub_tuning_env(monkeypatch):
    """Scrub every `HUNTER_*`, `FILTER_*`, and `BOT_*` knob, so the suite cannot read the operator's .env.

    `core_brain.order_manager` calls `load_dotenv` at module import, so the
    first test to import it injects the real `.env` into `os.environ` for the
    rest of the process -- and `load_dotenv` writes the environment directly,
    which `monkeypatch` in a later test cannot undo. That made the suite's
    result depend on both the file order and on what the operator happened to
    have tuned: with `HUNTER_SINGLE_BUY_GRACE_SEC=45` on disk (the knob #619
    later retired), the shadow rotation tests saw a 45-second grace and
    recorded no close.

    A test that wants a knob sets it itself, after this has run.
    """
    for var in [v for v in os.environ if v.startswith(("HUNTER_", "FILTER_", "BOT_"))]:
        monkeypatch.delenv(var, raising=False)



def _is_loopback(address):
    if isinstance(address, tuple) and len(address) >= 1:
        host = address[0]
        if host in LOOPBACK_HOSTS:
            return True
        # Handle IP representation of localhost
        if isinstance(host, str) and (host.startswith("127.") or host == "::1"):
            return True
        return False
    elif isinstance(address, str):
        # AF_UNIX socket path on Linux
        return True
    return False


@pytest.fixture(autouse=True)
def block_non_loopback_sockets(request, monkeypatch):
    """Block all non-loopback socket connections unless explicitly marked with @pytest.mark.allow_network."""
    if request.node.get_closest_marker("allow_network"):
        return

    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        if not _is_loopback(address):
            raise RuntimeError(
                f"Network access denied: test attempted socket connect to non-loopback address {address!r}"
            )
        return orig_connect(self, address)

    def guarded_connect_ex(self, address):
        if not _is_loopback(address):
            raise RuntimeError(
                f"Network access denied: test attempted socket connect_ex to non-loopback address {address!r}"
            )
        return orig_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


class ProductionRegistryWriteError(BaseException):
    """Raised when a test opens the production registry.

    Deliberately not an `Exception`. `cycle_stream.emit()` wraps its whole body
    in `except Exception` and only prints a warning, so an `Exception` here
    would be swallowed and the test would pass while still writing rows into
    `data/orders.db`.
    """


def _uri_to_path(raw: str) -> str | None:
    """Resolve a SQLite `file:` URI to the path it actually opens.

    Returns None when the URI does not name a local file. SQLite decodes
    percent escapes and accepts a `localhost` authority, so comparing the raw
    URI text lets `file:data/orders%2edb` and `file://localhost/<abs>` through
    a guard that only strips the scheme.
    """
    parsed = urllib.parse.urlparse(raw)
    # An empty or `localhost` authority means this machine; anything else is a
    # host we cannot resolve to a local path.
    if parsed.netloc not in ("", "localhost"):
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if "memory" in query.get("mode", ()):
        return None
    path = urllib.parse.unquote(parsed.path)
    # `file:///C:/x` parses to `/C:/x`; drop the slash in front of the drive.
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path or None


def _is_production_registry(database, uri: bool = False) -> bool:
    """True when `database` names the production registry file.

    `uri` mirrors sqlite3.connect's own flag: without it a leading `file:` is
    part of the filename, not a scheme.
    """
    if not isinstance(database, (str, os.PathLike)):
        return False
    raw = os.fspath(database)
    if not raw or raw == ":memory:":
        return False
    if uri and raw.startswith("file:"):
        resolved = _uri_to_path(raw)
        if resolved is None:
            return False
        raw = resolved
    try:
        return Path(raw).resolve() == DEFAULT_DB_PATH.resolve()
    except (OSError, ValueError):
        return False


@pytest.fixture(autouse=True)
def block_production_registry(monkeypatch):
    """Fail any test that opens the production registry.

    `cycle_stream.emit()` and `order_registry.get_connection()` both fall back
    to `DEFAULT_DB_PATH` when no `db_path` is passed, so a single omitted
    keyword argument writes live rows into the real registry. Every test that
    needs a database has to point at `tmp_path`.
    """
    orig_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        # `uri` is the 8th positional parameter of sqlite3.connect, so args[6]
        # once `database` is peeled off.
        uri = args[6] if len(args) >= 7 else kwargs.get("uri", False)
        if _is_production_registry(database, uri=bool(uri)):
            raise ProductionRegistryWriteError(
                f"test opened the production registry {DEFAULT_DB_PATH}; "
                "pass an explicit db_path under tmp_path instead"
            )
        return orig_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)


@pytest.fixture(autouse=True)
def redirect_cycle_ring(tmp_path_factory, monkeypatch):
    """Point the cycle-telemetry ring at a session temp dir for every test.

    `cycle_stream.emit()` falls back to its module-global `DEFAULT_RING_PATH`
    (`runtime/cycle_events.jsonl`) whenever a caller omits `ring_path`, and
    `read_ring()` resolves through `LIVE_ROOT` the same way. The ring is capped
    at 500 lines with rotation and no archive, so every full-suite run that
    wrote there shredded part of the live engine's telemetry history -- the
    same class of accident the registry guard above exists to stop. Tests that
    exercise the ring pass `ring_path=` explicitly; this fixture covers the
    ones that do not.

    How to verify (operator, PowerShell):

        python -m pytest -q tests/test_cycle_ring_guard.py ; python -m pytest -q

    The proof this guard works is that the production ring is byte-identical
    across a full-suite run. Measured on this branch, sha256 of the first 16
    hex digits of `runtime/cycle_events.jsonl`:

        BEFORE lines: 528  pid14668 decides: 25  sha: 09bf14a613411944
        902 passed, 1 skipped in 81.06s
        AFTER  lines: 528  pid14668 decides: 25  sha: 09bf14a613411944

    Before this fixture the same run appended ~33 decide events and evicted an
    equal number of the operator's own.
    """
    import core_brain.cycle_stream as cycle_stream

    # Keep the production shape (<root>/runtime/cycle_events.jsonl): emit()
    # writes DEFAULT_RING_PATH verbatim while read_ring() resolves
    # DEFAULT_RING_PATH.name back under <LIVE_ROOT>/runtime/, so the two only
    # agree if the redirect preserves the directory layout.
    ring_dir = tmp_path_factory.mktemp("cycle-ring")
    monkeypatch.setattr(cycle_stream, "LIVE_ROOT", ring_dir)
    monkeypatch.setattr(cycle_stream, "DEFAULT_RING_PATH",
                        ring_dir / "runtime" / "cycle_events.jsonl")


@pytest.fixture(autouse=True)
def redirect_runtime_dir(tmp_path_factory, monkeypatch):
    """Point every runtime-state writer at a per-test temp dir.

    `core_brain.runtime_paths.RUNTIME_DIR` is where `runtime_file()` puts
    everything that is not given an explicit path, and `order_manager.RUN` is
    where `live_orders.json` / `live_events.log` go. A shadow run in a test
    passes its own DB path but not its heartbeat, so a full-suite run left ~55
    `shadow_run_<id>.json` files in the operator's `runtime/` and overwrote the
    live order log. Same fix as `redirect_cycle_ring`: keep the layout, move the
    root.

    Readers that resolve through `resolve_runtime_file(root=...)` still point at
    the real tree; tests that need them patch their own module.
    """
    from pathlib import Path as _Path

    import core_brain.order_manager as order_manager
    import core_brain.runtime_paths as runtime_paths

    runtime_dir = tmp_path_factory.mktemp("runtime-state") / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime_paths, "RUNTIME_DIR", _Path(runtime_dir))
    monkeypatch.setattr(order_manager, "RUN", _Path(runtime_dir))


#: The real, operator-facing state a test must never touch.
REAL_STATE_DIRS = ("data", "runtime", "reports")


def _real_state_snapshot() -> dict[str, tuple[int, int]]:
    """`relative path -> (size, mtime_ns)` for every file under the state dirs."""
    state: dict[str, tuple[int, int]] = {}
    for name in REAL_STATE_DIRS:
        root = REPO_ROOT / name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            state[path.relative_to(REPO_ROOT).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return state


@pytest.fixture(autouse=True)
def block_real_state_writes(request):
    """Fail any test that writes into the repo's own `data/`, `runtime/` or `reports/`.

    Two full-suite runs left the operator's tree dirtier than any code change did:
    ~55 heartbeats under `runtime/`, a `reports/stat_*` directory per run, and
    `data/demo_orders.db` -- a **tracked** fixture -- rewritten on every run by a
    KPI read that opens it read-write. Each one looked like a real change in
    `git status`, and each one had to be cleaned up by hand.

    The other hermetic fixtures each patch one writer (credentials, sockets, the
    production registry, the cycle ring). This one is the backstop for the writers
    nobody has patched yet: it snapshots the three state directories before the
    test and fails, naming the exact paths, if anything was created, changed or
    removed. Point the test at `tmp_path` instead -- for a tournament plan that
    means `artifacts_root=tmp_path`, because `base_dir` only moves the stores.

    Opt out per test with `@pytest.mark.writes_real_state` for a test whose whole
    subject is the real path -- there is one such test for this guard.
    """
    if request.node.get_closest_marker("writes_real_state"):
        yield
        return

    before = _real_state_snapshot()
    yield
    after = _real_state_snapshot()

    created = sorted(set(after) - set(before))
    changed = [path for path in sorted(set(before) & set(after)) if before[path] != after[path]]
    removed = sorted(set(before) - set(after))
    offenders = (
        [f"+ {path}" for path in created]
        + [f"~ {path}" for path in changed]
        + [f"- {path}" for path in removed]
    )
    assert not offenders, (
        "this test wrote into the repo's real state instead of tmp_path "
        "(data/, runtime/, reports/ are the operator's, not the suite's):\n"
        + "\n".join(offenders)
    )
