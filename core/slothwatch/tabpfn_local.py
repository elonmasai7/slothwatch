"""Guarded local-only TabPFN helpers.

The estimator is never instantiated unless the user explicitly opts in and a
model checkpoint already exists on disk. That prevents surprise downloads and
keeps the personal-data pipeline offline by default.
"""
from __future__ import annotations

import os
from pathlib import Path


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def local_checkpoint(model_type: str = "any") -> Path | None:
    """Return an existing checkpoint path; never download weights."""
    role_env = f"TABPFN_{model_type.upper()}_MODEL_PATH" if model_type in {"regressor", "classifier"} else ""
    explicit = os.getenv(role_env) if role_env else None
    explicit = explicit or os.getenv("TABPFN_MODEL_PATH")
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    cache = Path(os.getenv("TABPFN_MODEL_CACHE_DIR", str(Path.home() / ".cache" / "tabpfn"))).expanduser()
    if cache.is_file():
        return cache
    if not cache.is_dir():
        return None
    candidates = [p for p in cache.rglob("*") if p.is_file() and p.suffix.lower() in {".safetensors", ".ckpt", ".pt"}]
    if not candidates:
        return None

    def priority(path: Path) -> tuple[int, int, str]:
        name = path.name.lower()
        # TabPFN-3.5 uses the same checkpoint for classification and regression.
        unified = 0 if ("3.5" in name and path.suffix.lower() == ".safetensors") else 1
        if model_type == "regressor":
            role = 0 if "regressor" in name else 1
        elif model_type == "classifier":
            role = 0 if "classifier" in name else 1
        else:
            role = 0
        return unified, role, name

    candidates.sort(key=priority)
    return candidates[0]


def enabled_checkpoint(model_type: str = "any") -> Path | None:
    """Return a checkpoint only when the explicit opt-in and local weights exist."""
    if not truthy(os.getenv("SLOTHWATCH_ENABLE_TABPFN")):
        return None
    return local_checkpoint(model_type)


def offline_import_environment() -> None:
    """Tell hub clients to stay offline before importing TabPFN."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TABPFN_NO_BROWSER", "1")
