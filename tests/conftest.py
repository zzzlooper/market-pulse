import ipaddress
import os
import socket
import tempfile

import pytest

# Set before any test imports app.main, whose engine loads settings and state at import time:
# tests must never read your real config.toml or write to your real data folder.
os.environ["MARKET_PULSE_CONFIG"] = os.path.join(tempfile.mkdtemp(prefix="mp-test-"), "config.toml")
os.environ["MARKET_PULSE_DATA"] = tempfile.mkdtemp(prefix="mp-test-data-")

_connect = socket.socket.connect


def _local_only(sock, address, *args):
    host = address[0] if isinstance(address, tuple) else address
    try:
        allowed = isinstance(host, str) and (host == "localhost" or ipaddress.ip_address(host).is_loopback)
    except ValueError:
        allowed = False
    if not allowed:
        raise RuntimeError(f"tests must not use the network (tried {host})")
    return _connect(sock, address, *args)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing a test does may reach the internet: no SEC, Yahoo or phone requests, and so no
    contact address or token leaving this machine."""
    monkeypatch.setattr(socket.socket, "connect", _local_only)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (_ for _ in ()).throw(
        RuntimeError(f"tests must not use the network (tried to resolve {host})"))
        if host not in ("localhost", "127.0.0.1", "::1") else _getaddrinfo(host, *a, **k))


_getaddrinfo = socket.getaddrinfo
