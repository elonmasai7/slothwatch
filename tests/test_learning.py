import json
import math

from slothwatch.db import connect
from slothwatch.learning import predict_approval, simulate_persona_learning


def test_learning_cold_start_and_safety_rail(tmp_path):
    db = connect(tmp_path / "state.sqlite")
    features = {"category": "utilities", "deviation_pct": 5, "recurrence_type": "monthly", "amount_size": math.log1p(20), "day_of_month": 4, "repeat_count": 4}
    result = predict_approval(db, features, 250, 0.25)
    assert result["skipped"] and result["reason"] == "cold-start-fewer-than-15-decisions"
    for i in range(15):
        db.execute("INSERT INTO human_decisions(alert_id,decision,features_json) VALUES(?,?,?)", (str(i), "ignore", json.dumps(features)))
    db.commit()
    result = predict_approval(db, features, 250, 0.25)
    assert result["suppressed"]
    large = dict(features, amount_size=math.log1p(400))
    result = predict_approval(db, large, 250, 0.25)
    assert not result["suppressed"]


def test_laziness_curve_suppresses_more_with_same_detection_recall():
    off = simulate_persona_learning(weeks=12, learning_on=False)
    on = simulate_persona_learning(weeks=12, learning_on=True)
    assert on["gemma_wakeups"] < off["gemma_wakeups"]
    assert on["recall"] == off["recall"] == 1.0
