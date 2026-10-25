"""Command line interface. Commands are intentionally local-first."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

from .config import load_config
from .db import connect
from .tabpfn_local import local_checkpoint, truthy


def doctor(config_path: str) -> int:
    """Inspect every optional integration without contacting non-local services."""
    settings = load_config(config_path)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    values = settings.values

    # Ollama is checked only through the configured loopback HTTP endpoint.
    endpoint = str(values["llm"]["endpoint"])
    parsed = urlsplit(endpoint)
    local_host = parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.scheme == "http"
    ollama_executable = shutil.which("ollama")
    ollama_reachable = False
    pulled_model = False
    model_names: list[str] = []
    if local_host:
        tags_url = f"http://{parsed.netloc}/api/tags"
        try:
            request = urllib.request.Request(tags_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=1.0) as response:
                tags = json.loads(response.read().decode("utf-8"))
            model_names = [str(item.get("name", "")) for item in tags.get("models", [])]
            ollama_reachable = True
            configured_model = str(values["llm"]["model"])
            pulled_model = any(
                name == configured_model
                or name.startswith(configured_model + ":")
                or configured_model.startswith(name + ":")
                for name in model_names
            )
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            pass

    # Temporal's service is local-only here. Report SDK presence separately from service reachability.
    temporal_host, _, temporal_port = str(values["orchestration"]["temporal_endpoint"]).partition(":")
    temporal_host = temporal_host.strip("[]")
    temporal_sdk_installed = importlib.util.find_spec("temporalio") is not None
    temporal_cli_installed = shutil.which("temporal") is not None
    temporal_reachable = False
    if temporal_host in {"127.0.0.1", "localhost", "::1"}:
        try:
            with socket.create_connection((temporal_host, int(temporal_port or "7233")), timeout=1.0):
                temporal_reachable = True
        except (OSError, ValueError):
            pass

    tabpfn_installed = importlib.util.find_spec("tabpfn") is not None
    regressor_checkpoint = local_checkpoint("regressor")
    classifier_checkpoint = local_checkpoint("classifier")
    tabpfn_enabled = truthy(os.getenv("SLOTHWATCH_ENABLE_TABPFN"))
    tabpfn_token_present = bool(os.getenv("TABPFN_TOKEN"))

    sentry_sdk_installed = importlib.util.find_spec("sentry_sdk") is not None
    sentry_name = str(values["tracing"].get("sentry_dsn_env", "SENTRY_DSN"))
    sentry_dsn_configured = bool(os.getenv(sentry_name))

    tinker_installed = importlib.util.find_spec("tinker") is not None
    tinker_key_configured = bool(os.getenv("TINKER_API_KEY"))
    tinker_model = os.getenv("SLOTHWATCH_TUNING_MODEL", "Qwen/Qwen3.5-4B")

    results = {
        "python": sys.version.split()[0],
        "config": str(settings.source),
        "database": str(settings.db_path),
        "sqlite": "ok",
        "ollama": {
            "executable": ollama_executable or "not found",
            "endpoint_is_loopback": local_host,
            "service_reachable": ollama_reachable,
            "model": values["llm"]["model"],
            "model_pulled": pulled_model,
            "installed_models": model_names,
            "next_step": (
                "run `make install-ollama`, then `make setup-ollama`"
                if not ollama_executable
                else "start the Ollama service, then run `make setup-ollama`"
                if not ollama_reachable
                else "run `make setup-ollama` to pull the configured model"
                if not pulled_model
                else "ready"
            ),
        },
        "tabpfn": {
            "package_installed": tabpfn_installed,
            "explicitly_enabled": tabpfn_enabled,
            "regressor_checkpoint": str(regressor_checkpoint) if regressor_checkpoint else None,
            "classifier_checkpoint": str(classifier_checkpoint) if classifier_checkpoint else None,
            "token_present": tabpfn_token_present,
            "license_note": "Acceptance/commercial rights cannot be verified by code; check the exact model-weight terms at https://ux.priorlabs.ai/.",
            "ready_for_regression": bool(tabpfn_installed and tabpfn_enabled and regressor_checkpoint),
            "ready_for_classifier": bool(tabpfn_installed and tabpfn_enabled and classifier_checkpoint),
            "next_step": (
                "run `make install-tabpfn` to install TabPFN in .venv"
                if not tabpfn_installed
                else "review license terms, run `make tabpfn-prepare`, and configure local checkpoint paths"
                if not (regressor_checkpoint and classifier_checkpoint)
                else "set SLOTHWATCH_ENABLE_TABPFN=1 to opt in"
                if not tabpfn_enabled
                else "ready for configured local checkpoint inference"
            ),
        },
        "temporal": {
            "sdk_installed": temporal_sdk_installed,
            "cli_installed": temporal_cli_installed,
            "service_reachable": temporal_reachable,
            "enabled_in_config": bool(values["orchestration"].get("use_temporal")),
            "next_step": (
                "run `make install-temporal` to install the Python SDK in .venv"
                if not temporal_sdk_installed
                else "install the Temporal CLI then run `make temporal-up`, or use Docker with `make temporal-compose-up`"
                if not temporal_cli_installed
                else "run `make temporal-up` in another terminal"
                if not temporal_reachable
                else "ready; start worker with `make temporal-worker`"
            ),
        },
        "sentry": {
            "sdk_installed": sentry_sdk_installed,
            "dsn_configured": sentry_dsn_configured,
            "active": bool(sentry_sdk_installed and sentry_dsn_configured),
            "fallback": "local JSONL traces" if not (sentry_sdk_installed and sentry_dsn_configured) else "local JSONL plus Sentry",
            "next_step": (
                "run `make install-sentry` to install the Sentry SDK in .venv"
                if not sentry_sdk_installed
                else "create a Sentry project and set SENTRY_DSN only if remote traces are desired"
                if not sentry_dsn_configured
                else "ready; sanitized spans are configured for Sentry"
            ),
        },
        "tinker": {
            "sdk_installed": tinker_installed,
            "api_key_configured": tinker_key_configured,
            "ready_to_attempt_hosted_training": bool(tinker_installed and tinker_key_configured),
            "configured_model": tinker_model,
            "model_catalog": "https://tinker-docs.thinkingmachines.ai/tinker/models/",
            "privacy_note": "Training is hosted; the project only sends synthetic tuning examples.",
            "next_step": (
                "run `make install-tinker` to install the Tinker SDK in .venv"
                if not tinker_installed
                else "set TINKER_API_KEY in your shell/secret manager to opt in"
                if not tinker_key_configured
                else "ready to attempt hosted training; run `make tuning` first and review costs"
            ),
        },
        "install_command": "make install-all installs all Python extras; Ollama and the Temporal CLI are separate system tools.",
    }
    with connect(settings.db_path):
        pass
    print(json.dumps(results, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slothwatch", description="Local-first quiet anomaly watcher")
    parser.add_argument("--config", default="slothwatch.yaml", help="Path to YAML config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local setup")
    sub.add_parser("status", help="Show recent run status")
    sub.add_parser("score", help="Show Sloth Score")
    run = sub.add_parser("run", help="Analyze a CSV or the configured folder")
    run.add_argument("path", nargs="?", help="CSV file or folder (defaults to configured data folder)")
    run.add_argument("--no-learning", action="store_true", help="Disable Rung 1.5 decision learning")
    watch = sub.add_parser("watch", help="Poll the configured folder and process changed CSVs")
    watch.add_argument("--interval", type=float, default=30)
    approve = sub.add_parser("approve", help="Approve a pending draft; copies it to local outbox only")
    approve.add_argument("id")
    ignore = sub.add_parser("ignore", help="Ignore a pending alert")
    ignore.add_argument("id")
    snooze = sub.add_parser("snooze", help="Snooze a pending alert")
    snooze.add_argument("id")
    snooze.add_argument("--hours", type=int, default=24)
    redact = sub.add_parser("redact", help="Export anonymized run data")
    redact.add_argument("--run-id", type=int, required=False)
    receipt = sub.add_parser("receipt", help="Show or sample a silence receipt")
    receipt.add_argument("--sample", type=int, default=0)
    sub.add_parser("verify", help="Verify receipt hash chain")
    canary = sub.add_parser("canary", help="Run a local synthetic canary check")
    canary.add_argument("--seed", type=int, default=17)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return doctor(args.config)
    settings = load_config(args.config)
    from .service import handle_command
    return handle_command(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
