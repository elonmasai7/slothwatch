"""Rung 1.5 decision learning. Optional TabPFN classifier; local fallback is explicit."""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from .tabpfn_local import enabled_checkpoint, offline_import_environment

FEATURE_FIELDS = ("category", "deviation_pct", "recurrence_type", "amount_size", "day_of_month", "repeat_count")


def decision_features(tx: dict, context: dict, repeat_count: int = 0) -> dict[str, Any]:
    """Produce the compact feature set stored with explicit human decisions."""
    amount = abs(float(tx.get("amount", 0)))
    expected = float(context.get("expected") or amount or 1)
    try:
        day = int(str(tx.get("date", ""))[8:10])
    except ValueError:
        day = 1
    return {"category": str(tx.get("category") or "unknown").lower(), "deviation_pct": round((amount - expected) / max(expected, 1) * 100, 3), "recurrence_type": str(context.get("cadence", "unknown")), "amount_size": math.log1p(amount), "day_of_month": day, "repeat_count": int(repeat_count)}


def _numeric_features(feature_rows: list[dict], current: dict) -> tuple[list[list[float]], list[float]]:
    """One-hot encode categorical features deterministically for TabPFN in-context fitting."""
    category_values = sorted({str(row.get("category", "unknown")) for row in feature_rows + [current]})
    recurrence_values = sorted({str(row.get("recurrence_type", "unknown")) for row in feature_rows + [current]})
    matrix: list[list[float]] = []
    numeric_fields = ("deviation_pct", "amount_size", "day_of_month", "repeat_count")
    for row in feature_rows + [current]:
        vector = [float(row.get(key, 0) or 0) for key in numeric_fields]
        vector.extend(1.0 if str(row.get("category", "unknown")) == value else 0.0 for value in category_values)
        vector.extend(1.0 if str(row.get("recurrence_type", "unknown")) == value else 0.0 for value in recurrence_values)
        matrix.append(vector)
    return matrix[:-1], matrix[-1:]


def _tabpfn_approval(features: dict, decisions: list[sqlite3.Row]) -> float | None:
    """Use a cached local TabPFN classifier if opted in; never fetch weights implicitly."""
    checkpoint = enabled_checkpoint("classifier")
    if checkpoint is None or len(decisions) < 15:
        return None
    history_features: list[dict] = []
    labels: list[int] = []
    for item in decisions:
        try:
            snapshot = json.loads(item["features_json"])
        except (ValueError, TypeError):
            continue
        history_features.append(snapshot)
        labels.append(1 if item["decision"] == "approve" else 0)
    if len(labels) < 15 or len(set(labels)) < 2:
        return None
    offline_import_environment()
    try:
        from tabpfn import TabPFNClassifier

        x_train, x_current = _numeric_features(history_features, features)
        model = TabPFNClassifier(model_path=str(checkpoint), random_state=0, show_progress_bar=False)
        model.fit(x_train, labels)
        probabilities = model.predict_proba(x_current)[0]
        class_index = list(model.classes_).index(1)
        return float(probabilities[class_index])
    except Exception:
        # Continue with the deterministic local estimator if optional local model fails.
        return None


def predict_approval(conn: sqlite3.Connection, features: dict, high_severity_amount: float, learning_threshold: float, no_learning: bool = False) -> dict:
    """Estimate whether a user usually approves similar anomalies after 15 human decisions."""
    if no_learning:
        return {"skipped": True, "reason": "--no-learning", "p_approve": None, "method": "disabled"}
    decisions = conn.execute("SELECT decision, features_json FROM human_decisions ORDER BY id DESC LIMIT 500").fetchall()
    if len(decisions) < 15:
        return {"skipped": True, "reason": "cold-start-fewer-than-15-decisions", "p_approve": None, "method": "cold-start"}
    # Prefer a local TabPFN in-context classifier when explicitly enabled and weights exist.
    p_approve = _tabpfn_approval(features, decisions)
    method = "TabPFN-local-classifier" if p_approve is not None else "local-decision-frequency"
    if p_approve is None:
        # Only use decisions close in category or recurrence to the current row.
        matched = []
        for item in decisions:
            old = json.loads(item["features_json"])
            if old.get("category") == features.get("category") or old.get("recurrence_type") == features.get("recurrence_type"):
                matched.append(item["decision"] == "approve")
        if not matched:
            matched = [item["decision"] == "approve" for item in decisions]
        p_approve = (sum(matched) + 1) / (len(matched) + 2)
    # A hard safety rail: big amounts are never suppressed by learning.
    if abs(float(features.get("amount_size", 0))) and math.expm1(float(features.get("amount_size", 0))) >= high_severity_amount:
        return {"skipped": False, "suppressed": False, "p_approve": round(p_approve, 3), "method": method, "reason": "high-severity-safety-rail"}
    suppressed = p_approve < learning_threshold
    return {"skipped": False, "suppressed": suppressed, "p_approve": round(p_approve, 3), "method": method, "reason": "low predicted approval" if suppressed else "approval probability above threshold"}


def simulate_persona_learning(weeks: int = 12, seed: int = 2401, learning_on: bool = True) -> dict:
    """Run a transparent persona simulation used to compare wakeups, not a real-user model."""
    # Persona: ignores low-value utility wiggles but consistently approves duplicates.
    rows = []
    # This comparison starts with 15 prior synthetic persona decisions, modeling a warmed-up user.
    # The production `predict_approval` path still remains cold-started until its own 15 recorded decisions.
    decisions = 15 if learning_on else 0
    gemma_wakes = 0
    human_pings = 0
    anomalies = 0
    detected = 0
    for week in range(weeks):
        weekly_gemma = 0
        weekly_human = 0
        # Each week contributes a noisy utility movement and one occasional clear duplicate.
        anomaly_types = [("utility", 16 + (week % 3) * 2, False)]
        if week in {2, 5, 8, 11}:
            anomaly_types.append(("duplicate", 35, True))
        for category, amount, truth in anomaly_types:
            anomalies += int(truth)
            p_approve = 0.08 if category == "utility" else 0.96
            suppress = learning_on and decisions >= 15 and p_approve < 0.25 and amount < 250
            if suppress:
                continue
            weekly_gemma += 1
            gemma_wakes += 1
            if truth:
                detected += 1
                weekly_human += 1
                human_pings += 1
            # The simulated persona supplies explicit decisions, so the cold-start ends honestly.
            decisions += 1
        rows.append({"week": week + 1, "gemma_wakeups": weekly_gemma, "human_pings": weekly_human})
    return {"weeks": rows, "learning_on": learning_on, "gemma_wakeups": gemma_wakes, "human_pings": human_pings, "true_anomalies": anomalies, "detected_true_anomalies": detected, "recall": detected / anomalies if anomalies else 1.0, "precision": detected / gemma_wakes if gemma_wakes else 1.0, "simulated_persona": "ignores small utility fluctuations; approves clear duplicates", "seed": seed}
