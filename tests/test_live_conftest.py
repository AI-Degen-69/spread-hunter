"""Verification of live suite hermetic guards and namespace precedence in live/tests/."""
from __future__ import annotations

import os
import importlib
import socket
import subprocess
import sys
from pathlib import Path
import pytest

REQUIRED_CREDENTIAL_VARS = frozenset({
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
})


from tests.conftest import CREDENTIAL_VARS, REPO_ROOT


def test_live_env_scrub_removes_credentials():
    """Default test environment in live/ must have zero venue credentials in os.environ."""
    assert REQUIRED_CREDENTIAL_VARS.issubset(set(CREDENTIAL_VARS)), (
        f"Missing credentials in CREDENTIAL_VARS: {REQUIRED_CREDENTIAL_VARS - set(CREDENTIAL_VARS)}"
    )
    for var in REQUIRED_CREDENTIAL_VARS:
        assert var not in os.environ, f"{var} leaked into test environment"


def test_scrub_credential_env_actively_removes_populated_vars(monkeypatch):
    """If environment variables are preset, the scrub logic removes all required venue credentials."""
    import tests.conftest as root_conftest

    for var in REQUIRED_CREDENTIAL_VARS:
        monkeypatch.setenv(var, "secret-test-token")

    # Invoke conftest scrub implementation
    root_conftest.scrub_credentials(monkeypatch)

    for var in REQUIRED_CREDENTIAL_VARS:
        assert var not in os.environ, f"{var} was not scrubbed from environment"




def test_live_network_block_raises_on_outbound_socket():
    """Attempting outbound connection to non-loopback address must raise RuntimeError."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="Network access denied"):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()


def test_every_engine_module_resolves_inside_live():
    """The engine is one package in one directory -- live/engine/ and nowhere else.

    Until 2026-08-18 this package was called `strategy` and had no
    `__init__.py`, so Python could merge it with any same-named directory on
    sys.path into one namespace package spanning both trees. Which directory a
    module resolved from depended on the operator's working directory. The
    rename ended that. This test is what keeps it ended.
    """
    import core_brain
    import core_brain.order_manager as live_exec
    import core_brain.order_registry as order_reg
    import core_brain.markets as markets
    import core_brain.config as config

    live_dir = Path(__file__).resolve().parent.parent

    # A regular package, so __path__ is exactly one directory -- not a namespace
    # package that can silently pick up a same-named directory elsewhere.
    assert list(core_brain.__path__) == [str(live_dir / "core_brain")], list(core_brain.__path__)

    for mod in (live_exec, order_reg, markets, config):
        path = Path(mod.__file__).resolve()
        assert path.parent == live_dir / "core_brain", f"{mod.__name__} resolved to {path}"


def test_conftest_places_repo_root_on_sys_path():
    """Conftest must guarantee repo root is first on sys.path for standalone and IDE test execution."""
    import sys
    from tests.conftest import REPO_ROOT
    assert sys.path[0] == str(REPO_ROOT)


def test_a_stray_port_never_reaches_the_suite():
    """`PORT` is dropped at conftest import, before any dashboard import.

    `dashboard/server.py` resolves its port at import time, so an unusable
    `PORT` inherited from the operator's shell (the ambient `PORT=0` this repo
    has hit) crashes collection for every test file that imports the app.
    """
    assert "PORT" not in os.environ, (
        "tests/conftest.py must drop a stray PORT before collection"
    )


def test_a_stray_port_cannot_fail_collection_of_a_dashboard_importing_file():
    """The same guarantee, measured end to end in a child process.

    The child runs pytest with `PORT=0` in its own environment, over a test
    file that imports `dashboard.server` at module level. Without the conftest
    scrub that run dies during collection -- exit 2, `ValueError: PORT='0' is
    outside 1-65535`, zero tests run -- and this test fails.
    """
    env = {k: v for k, v in os.environ.items() if k != "PORT"}
    env["PORT"] = "0"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header",
         "tests/test_dashboard_port.py"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )

    assert result.returncode == 0, (result.stdout + result.stderr)[-2000:]

