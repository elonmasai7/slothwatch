"""Privacy guard: run any callable with all non-local network connections blocked (and recorded)."""
from __future__ import annotations
import socket

LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::ffff:127.0.0.1"}


class ExternalConnection(Exception):
    pass


def guarded(fn, *a, **kw):
    attempts: list[str] = []
    orig_connect, orig_connect_ex, orig_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(addr):
        host = addr[0] if isinstance(addr, tuple) else str(addr)
        if isinstance(addr, str) or host in LOCAL or str(host).startswith("127."):
            return
        attempts.append(str(host))
        raise ExternalConnection(f"blocked outbound connection to {host}")

    def connect(self, addr):
        check(addr)
        return orig_connect(self, addr)

    def connect_ex(self, addr):
        check(addr)
        return orig_connect_ex(self, addr)

    def gai(host, *args, **kw2):
        if host not in LOCAL and not str(host).startswith("127."):
            attempts.append(str(host))
            raise ExternalConnection(f"blocked DNS lookup for {host}")
        return orig_gai(host, *args, **kw2)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, gai
    try:
        result = fn(*a, **kw)
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = orig_connect, orig_connect_ex, orig_gai
    return result, attempts
