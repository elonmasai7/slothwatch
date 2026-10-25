from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from tuning.dataset import generate_dataset  # noqa: E402


def test_tuning_dataset_is_deterministic_and_has_15_percent_holdout(tmp_path):
    first = generate_dataset(tmp_path / "a", count=1200, seed=42)
    second = generate_dataset(tmp_path / "b", count=1200, seed=42)
    assert first["train_count"] == 1020
    assert first["holdout_count"] == 180
    assert Path(first["train_path"]).read_text() == Path(second["train_path"]).read_text()
    assert Path(first["holdout_path"]).read_text() == Path(second["holdout_path"]).read_text()
    required = {"verdict_two_sentences", "severity", "suggested_action", "draft_message"}
    for line in Path(first["train_path"]).read_text().splitlines():
        row = json.loads(line)
        target = json.loads(row["target"])
        assert set(target) == required
        assert all(isinstance(target[key], str) and target[key].strip() for key in required)
        assert target["draft_message"].strip()
        assert row["prompt"].find("Synthetic fixture only") >= 0
