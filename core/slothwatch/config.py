"""Configuration loading and reproducible config hashing."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path("slothwatch.yaml")

DEFAULTS: dict[str, Any] = {
    "app": {"data_dir": "data/real", "state_dir": ".slothwatch", "database": ".slothwatch/slothwatch.sqlite3", "timezone": "Africa/Nairobi", "run_mode": "real", "fast_demo_seconds": 60, "default_currency": "USD"},
    "rules": {"duplicate_window_days": 3, "small_new_merchant_amount": 15.0, "recurring_min_observations": 4, "forecast_lower_quantile": 0.05, "forecast_upper_quantile": 0.95, "mad_multiplier": 3.5, "creep_window": 4, "creep_total_pct": 8.0, "high_severity_amount": 250.0, "learning_threshold": 0.25, "ignore_merchants": [], "allow_merchants": []},
    "llm": {"provider": "ollama", "endpoint": "http://127.0.0.1:11434/api/generate", "model": "gemma3:4b", "timeout_seconds": 20, "enabled": True},
    "orchestration": {"temporal_endpoint": "127.0.0.1:7233", "task_queue": "slothwatch-local", "use_temporal": False},
    "tracing": {"sentry_dsn_env": "SENTRY_DSN", "local_trace_file": ".slothwatch/traces/trace.jsonl"},
    "benchmark": {"closed_api_input_usd_per_million_tokens": 0.15, "closed_api_output_usd_per_million_tokens": 0.60},
}


def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass(frozen=True)
class Settings:
    """Resolved application settings."""

    values: dict[str, Any]
    source: Path

    @property
    def state_dir(self) -> Path:
        return Path(self.values["app"]["state_dir"])

    @property
    def db_path(self) -> Path:
        return Path(self.values["app"]["database"])

    @property
    def data_dir(self) -> Path:
        return Path(self.values["app"]["data_dir"])

    @property
    def config_hash(self) -> str:
        canonical = json.dumps(self.values, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def load_config(path: str | Path = DEFAULT_CONFIG) -> Settings:
    """Load YAML defaults and apply supported environment overrides."""
    source = Path(path)
    raw: dict[str, Any] = {}
    if source.exists():
        parsed = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if not isinstance(parsed, dict):
            raise ValueError("Config root must be a YAML mapping")
        raw = parsed
    values = _merge(DEFAULTS, raw)
    overrides = {
        "SLOTHWATCH_DATA_DIR": ("app", "data_dir"),
        "SLOTHWATCH_STATE_DIR": ("app", "state_dir"),
        "SLOTHWATCH_DATABASE": ("app", "database"),
        "SLOTHWATCH_OLLAMA_URL": ("llm", "endpoint"),
        "SLOTHWATCH_MODEL": ("llm", "model"),
        "SLOTHWATCH_USE_TEMPORAL": ("orchestration", "use_temporal"),
    }
    for env, keys in overrides.items():
        value = os.getenv(env)
        if value is not None:
            target = values
            for key in keys[:-1]:
                target = target[key]
            if keys[-1] == "use_temporal":
                target[keys[-1]] = value.lower() in {"1", "true", "yes", "on"}
            else:
                target[keys[-1]] = value
    return Settings(values=values, source=source)
