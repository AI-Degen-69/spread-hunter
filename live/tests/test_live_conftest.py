"""Verification of live suite hermetic guards and namespace precedence in live/tests/."""
from __future__ import annotations

import os
import importlib
import socket
from pathlib import Path
import pytest

CREDENTIAL_VARS = (
    "POLY_PRIVATE_KEY",
    "POLY_KEY",
    "POLY_FUNDER",
    "POLY_SIG_TYPE",
    "PRIVATE_KEY",
    "RELAYER_API_KEY",
    "RELAYER_API_KEY_ADDRESS",
    "POLYGON_RPC",
)


def test_live_env_scrub_removes_credentials():
    """Default test environment in live/ must have zero venue credentials in os.environ."""
    for var in CREDENTIAL_VARS:
        assert var not in os.environ, f"{var} leaked into test environment"


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

    Until 2026-08-18 this package was called `strategy`, the same name as the
    simulation package at the repo root. Neither had an `__init__.py`, so Python
    merged them into a namespace package spanning both trees: `markets` and
    `config` came from the simulation while `live_exec` came from live/, and
    which of the two directories resolved depended on the working directory the
    operator happened to be in. The rename ended that. This test is what keeps
    it ended.
    """
    import engine
    import engine.live_exec as live_exec
    import engine.order_registry as order_reg
    import engine.markets as markets
    import engine.config as config

    live_dir = Path(__file__).resolve().parent.parent

    # A regular package, so __path__ is exactly one directory -- not a namespace
    # package that can silently pick up a same-named directory elsewhere.
    assert list(engine.__path__) == [str(live_dir / "engine")], list(engine.__path__)

    for mod in (live_exec, order_reg, markets, config):
        path = Path(mod.__file__).resolve()
        assert path.parent == live_dir / "engine", f"{mod.__name__} resolved to {path}"


def test_live_code_cannot_reach_the_simulation_package():
    """`import strategy` must fail from the live suite.

    The repo root is deliberately absent from live's sys.path (pytest.ini
    sets `pythonpath = .`, and live_exec bootstraps ROOT only). If the
    simulation becomes importable again the two `strategy` trees can start
    merging again without anyone noticing.
    """
    import engine.live_exec  # noqa: F401  -- runs its sys.path bootstrap

    with pytest.raises(ImportError):
        importlib.import_module("strategy.quotes")
