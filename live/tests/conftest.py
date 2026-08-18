"""live/tests/conftest.py - Hermetic environment fixtures for live test suite.

Invariants:
1. Every test runs with credentials scrubbed from os.environ by default.
2. Any outbound network connection to a non-loopback address is blocked and raises RuntimeError.
"""
from __future__ import annotations

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

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: opt-in marker to allow network access in a test"
    )


@pytest.fixture(autouse=True)
def scrub_credential_env(monkeypatch):
    """Scrub venue credentials and sensitive endpoint configs from os.environ."""
    for var in CREDENTIAL_VARS:
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
