"""Verification of live suite hermetic guards and namespace precedence in live/tests/."""
from __future__ import annotations

import os
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


def test_namespace_precedence_and_module_resolution():
    """Assert strategy.live_exec & order_registry resolve under live/strategy/ while strategy.markets resolves to root strategy/."""
    import strategy.live_exec as live_exec
    import strategy.order_registry as order_reg
    import strategy.markets as markets

    live_exec_path = Path(live_exec.__file__).resolve()
    order_reg_path = Path(order_reg.__file__).resolve()
    markets_path = Path(markets.__file__).resolve()

    # live_exec and order_registry must resolve under live/strategy/
    assert (
        "live" in live_exec_path.parts and "strategy" in live_exec_path.parts
    ), f"Expected live_exec under live/strategy/, got {live_exec_path}"
    assert live_exec_path.name == "live_exec.py"

    assert (
        "live" in order_reg_path.parts and "strategy" in order_reg_path.parts
    ), f"Expected order_registry under live/strategy/, got {order_reg_path}"
    assert order_reg_path.name == "order_registry.py"

    # strategy.markets must resolve to the root strategy package (outside live/)
    assert (
        "live" not in markets_path.parts and "strategy" in markets_path.parts
    ), f"Expected markets under root strategy/ (not live/), got {markets_path}"
    assert markets_path.name == "markets.py"
