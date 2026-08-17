"""Verification of suite-wide hermetic guards in tests/conftest.py."""
import os
import socket
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


def test_env_scrub_removes_credentials():
    """Default test environment must have zero venue credentials in os.environ."""
    for var in CREDENTIAL_VARS:
        assert var not in os.environ, f"{var} leaked into test environment"


def test_network_block_raises_on_outbound_socket():
    """Attempting outbound connection to non-loopback address must raise RuntimeError."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match="Network access denied"):
        s.connect(("93.184.216.34", 80))
    s.close()


def test_network_block_allows_loopback():
    """Connections to 127.0.0.1 must be permitted through the guard."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(("127.0.0.1", port))
    finally:
        client.close()
        server.close()


@pytest.mark.allow_network
def test_allow_network_marker_bypasses_guard():
    """When marked @pytest.mark.allow_network, guard does not intercept connect."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.01)
    try:
        # Non-routable address to avoid hang; must NOT raise RuntimeError
        s.connect(("192.0.2.1", 80))
    except (socket.timeout, OSError) as e:
        assert not isinstance(e, RuntimeError)
        assert "Network access denied" not in str(e)
    finally:
        s.close()
