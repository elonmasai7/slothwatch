from __future__ import annotations

import socket

from slothwatch.ladder import run_pipeline
from slothwatch.synthetic import generate_transactions


def test_pipeline_makes_no_nonlocal_connections(settings_factory, monkeypatch, capsys):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def guarded_connect(sock, address):
        if isinstance(address, tuple):
            host = address[0]
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise AssertionError(f"non-local outbound connection attempted: {host}")
        return real_connect(sock, address)

    def guarded_create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError(f"non-local outbound connection attempted: {host}")
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    result = run_pipeline(generate_transactions("clean-month"), settings_factory("privacy"), mode="privacy-check", use_ollama=False)
    capsys.readouterr()
    assert result["gemma_wakeups"] == 0
