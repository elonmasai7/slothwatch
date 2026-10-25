"""Warm a TabPFN checkpoint using synthetic data after the user accepts its license."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    try:
        from tabpfn import TabPFNRegressor
    except ImportError:
        print("TabPFN is missing. Run: make install-tabpfn (this installs into the project .venv).", file=sys.stderr)
        return 2

    print("This downloads model weights if they are not already cached. Only synthetic rows are used.")
    print("First review and accept the selected model's terms at https://ux.priorlabs.ai (Licenses tab).")
    print("If this is a headless run, set TABPFN_TOKEN as directed by Prior Labs.")
    print("The default TabPFN-3.5 weights are non-commercial; do not use commercially without suitable rights.")
    try:
        model = TabPFNRegressor(random_state=0, show_progress_bar=False)
        model.fit([[0.0], [1.0], [2.0], [3.0]], [1.0, 1.5, 2.0, 2.5])
        model.predict([[4.0]])
    except Exception as exc:
        print(f"TabPFN preparation failed ({type(exc).__name__}). Check license access, token, runtime and checkpoint download.", file=sys.stderr)
        return 2

    cache = Path(os.getenv("TABPFN_MODEL_CACHE_DIR", str(Path.home() / ".cache" / "tabpfn"))).expanduser()
    candidates = []
    if cache.is_dir():
        candidates = sorted(p for p in cache.rglob("*") if p.is_file() and p.suffix.lower() in {".safetensors", ".ckpt", ".pt"})
    if candidates:
        print("Local checkpoint candidates:")
        for path in candidates[:20]:
            print(f"  {path}")
        unified = next((p for p in candidates if "3.5" in p.name.lower() and p.suffix.lower() == ".safetensors"), None)
        if unified:
            print(f"TabPFN-3.5 shared checkpoint candidate: {unified}")
            print(f'Use: export TABPFN_MODEL_PATH="{unified}"')
        else:
            print("For separate checkpoint files, set TABPFN_REGRESSOR_MODEL_PATH and TABPFN_CLASSIFIER_MODEL_PATH.")
        print("Then set SLOTHWATCH_ENABLE_TABPFN=1 and confirm `slothwatch doctor` shows the expected checkpoint paths.")
    else:
        print(f"TabPFN ran, but no supported checkpoint file was found under {cache}.")
        print("Set TABPFN_MODEL_PATH to an existing .safetensors/.ckpt/.pt checkpoint if your cache differs.")
    print("Preparation completed with synthetic input only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
