"""Loaded by every Python process the simulator smoke starts (PYTHONPATH): loopback only, by construction.

Any `socket.connect` or `getaddrinfo` toward a host that is not 127.0.0.1 / ::1 / localhost raises
before a packet leaves, whatever HTTP client or proxy setting the code uses. The smoke's dead proxy
then only backs this up. `scripts/sim_smoke.py` (the CI merge gate) puts this directory first on
PYTHONPATH for the simulator and for every `bazaar` command it runs.
"""

from __future__ import annotations

import socket
from typing import Any

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


class SmokeNetworkError(OSError):
    """A non-loopback connection inside the simulator smoke: the gate fails instead of reaching out."""


def _host(address: Any) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None  # AF_UNIX paths and the like are local


def _check(host: str | None) -> None:
    if host is not None and host not in LOOPBACK:
        raise SmokeNetworkError(f"sim smoke: refused a connection to {host!r} (loopback only)")


def connect(self: socket.socket, address: Any) -> None:
    _check(_host(address))
    _connect(self, address)


def connect_ex(self: socket.socket, address: Any) -> int:
    _check(_host(address))
    return _connect_ex(self, address)


def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    _check(None if host is None else (host.decode() if isinstance(host, bytes) else str(host)))
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect  # type: ignore[method-assign]
socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
socket.getaddrinfo = getaddrinfo
