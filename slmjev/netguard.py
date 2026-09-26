"""Air-gap enforcement in code: refuse every non-loopback socket connection.

Ported from structured_deidentification ``app/python/run_engine.py`` ``_forbid_network()``, with one
difference: the judge must reach a llama-server on loopback, so loopback addresses stay allowed.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
from collections.abc import Mapping
from urllib.parse import urlparse

_ORIG_CONNECT = socket.socket.connect
_ORIG_CONNECT_EX = socket.socket.connect_ex
_ORIG_CREATE = socket.create_connection


class NetworkForbidden(OSError):
    pass


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_loopback_url(url: str) -> None:
    """Raise NetworkForbidden unless ``url`` points at this machine."""
    if not is_loopback_host(urlparse(url).hostname):
        raise NetworkForbidden(f"refusing non-loopback endpoint {url!r}")


def loopback_request(url: str, *, method: str = "GET", body: bytes | None = None,
                     headers: Mapping[str, str] | None = None,
                     timeout: float = 30) -> tuple[int, bytes]:
    """One plain-HTTP request to a loopback URL; returns (status, body).

    Uses ``http.client`` directly rather than ``urllib.request``: urllib honours proxy
    variables, and it builds a TLS context even for ``http://``. Some OpenSSL builds abort that
    process when ``SSLKEYLOGFILE`` is set (antivirus sets it); SD's bundled Python is one."""
    check_loopback_url(url)
    u = urlparse(url)
    if u.scheme != "http":
        raise NetworkForbidden(f"loopback calls are plain http, not {u.scheme!r}")
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        conn.request(method, (u.path or "/") + (f"?{u.query}" if u.query else ""), body=body,
                     headers=dict(headers or {}))
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def _host_of(address) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None  # AF_UNIX paths etc. are local by construction


def _guard(address) -> None:
    host = _host_of(address)
    if host is not None and not is_loopback_host(host):
        raise NetworkForbidden(f"network disabled (air-gapped): {host}")


def forbid_network() -> None:
    """Patch ``socket`` so that only loopback connections succeed. Idempotent."""

    def connect(self, address):
        _guard(address)
        return _ORIG_CONNECT(self, address)

    def connect_ex(self, address):
        _guard(address)
        return _ORIG_CONNECT_EX(self, address)

    def create_connection(address, *a, **k):
        _guard(address)
        return _ORIG_CREATE(address, *a, **k)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]


def allow_network() -> None:
    """Undo :func:`forbid_network` (tests only)."""
    socket.socket.connect = _ORIG_CONNECT  # type: ignore[method-assign]
    socket.socket.connect_ex = _ORIG_CONNECT_EX  # type: ignore[method-assign]
    socket.create_connection = _ORIG_CREATE  # type: ignore[assignment]
