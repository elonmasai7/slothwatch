from __future__ import annotations

import copy
from pathlib import Path

import pytest

from slothwatch.config import Settings, load_config


@pytest.fixture

def settings_factory(tmp_path):
    def make(name: str = "test") -> Settings:
        base = load_config(Path(__file__).resolve().parents[1] / "slothwatch.yaml")
        values = copy.deepcopy(base.values)
        state = tmp_path / name
        values["app"]["state_dir"] = str(state)
        values["app"]["database"] = str(state / "slothwatch.sqlite3")
        values["tracing"]["local_trace_file"] = str(state / "traces" / "trace.jsonl")
        values["llm"]["enabled"] = False
        values["orchestration"]["use_temporal"] = False
        return Settings(values=values, source=base.source)
    return make
