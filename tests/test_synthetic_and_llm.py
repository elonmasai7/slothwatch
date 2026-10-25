import json

import pytest

from slothwatch.llm import validate_verdict, template_verdict
from slothwatch.synthetic import SCENARIOS, generate_monthly_files, generate_transactions


def test_scenarios_are_seeded_and_have_ground_truth():
    for scenario in SCENARIOS:
        a = generate_transactions(scenario, seed=99)
        b = generate_transactions(scenario, seed=99)
        assert [(x.id, x.amount, x.is_anomaly) for x in a] == [(x.id, x.amount, x.is_anomaly) for x in b]
    assert sum(x.is_anomaly for x in generate_transactions("clean-month")) == 0
    assert sum(x.is_anomaly for x in generate_transactions("everything-wrong")) == 4


def test_generator_writes_12_month_files(tmp_path):
    paths = generate_monthly_files("clean-month", tmp_path, seed=11)
    assert len(paths) == 12
    assert all(path.read_text().startswith("Date,Description,Amount") for path in paths)


def test_llm_json_schema_and_template_contract():
    result = template_verdict({"merchant": "Demo Store", "amount": 123, "date": "2026-10-25"}, {"expected": 50, "low": 45, "high": 55}, "amount outside expected forecast interval")
    assert set(result) == {"verdict_two_sentences", "severity", "suggested_action", "draft_message"}
    assert result["severity"] in {"ignore", "watch", "act"}
    with pytest.raises(ValueError):
        validate_verdict({"severity": "bad"})
