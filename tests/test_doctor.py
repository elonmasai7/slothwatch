from __future__ import annotations

import json
import socket
import urllib.error

from slothwatch import cli


def test_doctor_reports_all_integration_readiness_without_network(settings_factory, monkeypatch, capsys):
    settings = settings_factory("doctor")
    monkeypatch.setattr(cli, "load_config", lambda _path: settings)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cli, "local_checkpoint", lambda _role="any": None)

    def no_http(*_args, **_kwargs):
        raise urllib.error.URLError("loopback service is stopped")

    def no_socket(*_args, **_kwargs):
        raise OSError("loopback service is stopped")

    monkeypatch.setattr(cli.urllib.request, "urlopen", no_http)
    monkeypatch.setattr(cli.socket, "create_connection", no_socket)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("TINKER_API_KEY", raising=False)
    monkeypatch.delenv("TABPFN_TOKEN", raising=False)
    monkeypatch.delenv("SLOTHWATCH_ENABLE_TABPFN", raising=False)

    assert cli.doctor("unused.yaml") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ollama"]["model_pulled"] is False
    assert report["tabpfn"]["ready_for_regression"] is False
    assert report["temporal"]["service_reachable"] is False
    assert report["sentry"]["active"] is False
    assert report["tinker"]["ready_to_attempt_hosted_training"] is False
    assert "make install-all" in report["install_command"]


def test_install_extras_match_current_sdk_ranges():
    from pathlib import Path
    import tomllib

    package = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    extras = package["project"]["optional-dependencies"]
    assert any(dep.startswith("temporalio>=1.33.0") for dep in extras["temporal"])
    assert any(dep.startswith("tabpfn>=9.2.0") for dep in extras["tabpfn"])
    assert any(dep.startswith("tinker>=0.24.0") for dep in extras["tinker"])
    assert "sentry" in " ".join(extras["integrations"])
    assert {"temporalio", "sentry-sdk", "tabpfn", "tinker"}.issubset(
        {dep.split(">=")[0].split("<")[0] for dep in extras["integrations"]}
    )
