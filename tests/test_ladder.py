from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from slothwatch.db import connect
from slothwatch.ladder import approve_alert, run_pipeline
from slothwatch.receipts import verify_receipts
from slothwatch.synthetic import generate_transactions


def test_clean_month_is_silent_with_receipt(settings_factory, capsys):
    settings = settings_factory("clean")
    result = run_pipeline(generate_transactions("clean-month"), settings, mode="demo", source="clean-month", use_ollama=False)
    output = capsys.readouterr().out
    assert "Nothing needs you. Go back to sleep." in output
    assert "0 surprises" in output
    assert result["gemma_wakeups"] == 0
    assert result["human_pings"] == 0
    with connect(settings.db_path) as conn:
        ok, errors = verify_receipts(conn, settings.state_dir)
        saved = conn.execute("SELECT body_json FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
    assert ok, errors
    receipt_body = json.loads(saved["body_json"])
    assert len(receipt_body["row_decisions"]) == len(generate_transactions("clean-month"))
    assert receipt_body["settled_by_rung"]["r2_explainer"] == 0
    assert all({"settled_by", "reason", "method", "forecast_interval"} <= set(item) for item in receipt_body["row_decisions"])


def test_each_scenario_detects_planted_issues(settings_factory, capsys):
    cases = {
        "double-subscription": {"Streamflix"},
        "duplicate-charge": {"Streamflix"},
        "electricity-spike": {"City Electricity"},
        "creeping-increase": {"Northstar Insurance"},
        "everything-wrong": {"Streamflix", "City Electricity", "Northstar Insurance"},
    }
    for scenario, expected in cases.items():
        settings = settings_factory(scenario)
        rows = generate_transactions(scenario, seed=2401)
        result = run_pipeline(rows, settings, mode="demo", source=scenario, use_ollama=False)
        capsys.readouterr()
        got = {alert["merchant"] for alert in result["alerts"]}
        assert expected <= got, (scenario, expected, got)
        assert result["gemma_wakeups"] == len(result["alerts"])
        assert result["human_pings"] == sum(a["severity"] == "act" for a in result["alerts"])
        assert all(a["is_anomaly"] for a in result["alerts"]), (scenario, result["alerts"])
    # Every wrong issue set includes both deterministic duplicate detection and a material act-level spike.
    all_wrong = settings_factory("all-wrong-check")
    result = run_pipeline(generate_transactions("everything-wrong"), all_wrong, mode="demo", use_ollama=False)
    capsys.readouterr()
    assert any("duplicate" in a["reason"] for a in result["alerts"])
    assert result["human_pings"] >= 2


def test_duplicate_is_human_gated_and_never_sent(settings_factory, capsys):
    settings = settings_factory("approval")
    result = run_pipeline(generate_transactions("duplicate-charge"), settings, mode="demo", use_ollama=False)
    capsys.readouterr()
    alert = next(x for x in result["alerts"] if x["severity"] == "act")
    with connect(settings.db_path) as conn:
        before = conn.execute("SELECT status FROM alerts WHERE id=?", (alert["id"],)).fetchone()["status"]
    assert before == "pending"
    assert approve_alert(settings, alert["id"], "approve")
    outbox = settings.state_dir / "outbox" / f"approved-{alert['id']}.txt"
    assert outbox.exists()
    assert "Not sent" in outbox.read_text()
    with connect(settings.db_path) as conn:
        after = conn.execute("SELECT status FROM alerts WHERE id=?", (alert["id"],)).fetchone()["status"]
        decisions = conn.execute("SELECT COUNT(*) FROM human_decisions WHERE alert_id=?", (alert["id"],)).fetchone()[0]
    assert after == "approved"
    assert decisions == 1


def test_replaying_activity_keeps_exactly_one_alert(settings_factory, capsys):
    settings = settings_factory("replay")
    rows = generate_transactions("duplicate-charge")
    first = run_pipeline(rows, settings, mode="temporal-simulated", source="replay-test", use_ollama=False)
    capsys.readouterr()
    second = run_pipeline(rows, settings, mode="temporal-simulated", source="replay-test", use_ollama=False)
    capsys.readouterr()
    alert_id = first["alerts"][0]["id"]
    assert second["alerts"][0]["id"] == alert_id
    with connect(settings.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM alerts WHERE id=?", (alert_id,)).fetchone()[0]
    assert count == 1


def test_receipt_json_edit_is_detected(settings_factory, capsys):
    settings = settings_factory("receipt-tamper")
    run_pipeline(generate_transactions("clean-month"), settings, mode="demo", use_ollama=False)
    capsys.readouterr()
    receipt_dir = settings.state_dir / "receipts"
    path = next(receipt_dir.glob("*.json"))
    data = json.loads(path.read_text())
    data[0]["body"]["rows_checked"] += 1
    path.write_text(json.dumps(data, indent=2))
    with connect(settings.db_path) as conn:
        ok, errors = verify_receipts(conn, settings.state_dir)
    assert not ok
    assert any("edited" in message or "hash mismatch" in message for message in errors)
