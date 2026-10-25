"""Deterministic synthetic dataset for optional strict-JSON model tuning."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

CATEGORIES = ["subscription", "utility", "insurance", "duplicate", "rent", "phone", "internet", "gym"]


def build_example(index: int, rng: random.Random) -> dict[str, Any]:
    category = CATEGORIES[index % len(CATEGORIES)]
    amount = round(rng.uniform(8, 420), 2)
    expected = round(max(4, amount / rng.uniform(0.65, 1.45)), 2)
    deviation = (amount - expected) / expected * 100
    anomaly_kind = "normal"
    severity = "ignore"
    action = "No action needed"
    if index % 13 == 0:
        anomaly_kind, severity, action = "duplicate", "act", "Check duplicate charge and request reversal if confirmed"
    elif index % 11 == 0:
        anomaly_kind, severity, action = "large-spike", "act", "Compare with provider statement before disputing"
    elif index % 7 == 0:
        anomaly_kind, severity, action = "price-increase", "watch", "Review plan price and renewal terms"
    elif abs(deviation) > 18:
        anomaly_kind, severity, action = "unexpected-amount", "watch", "Compare against the previous bill"
    prompt_obj = {
        "merchant_category": category,
        "amount": amount,
        "expected": expected,
        "interval_90": [round(expected * 0.9, 2), round(expected * 1.1, 2)],
        "deviation_pct": round(deviation, 2),
        "recurrence": rng.choice(["monthly", "weekly", "annual", "unknown"]),
        "reason": anomaly_kind,
        "repeat_count": rng.randint(0, 6),
        "note": "Synthetic fixture only; no real personal data.",
    }
    sentence1 = (f"The {category} charge of {amount:.2f} differs from the expected {expected:.2f}."
                 if anomaly_kind != "normal" else f"The {category} charge of {amount:.2f} is consistent with the available context.")
    sentence2 = ("Verify the provider statement before taking any action."
                 if severity == "act" else "No immediate action is required; review it at your convenience.")
    target = {
        "verdict_two_sentences": f"{sentence1} {sentence2}",
        "severity": severity,
        "suggested_action": action,
        "draft_message": (f"Hello, I am reviewing a {category} charge of {amount:.2f} dated in the current statement. "
                          "Please explain the amount and confirm whether it matches my plan." if severity != "ignore"
                          else "No message needed; this row is classified as routine."),
    }
    # Keep target exactly as machine-readable JSON; training inputs are exclusively generated fixtures.
    return {"id": f"syn-{index:05d}", "prompt": "Return strict JSON with keys verdict_two_sentences, severity, suggested_action, draft_message. Context: " + json.dumps(prompt_obj, sort_keys=True), "target": json.dumps(target, ensure_ascii=False, separators=(",", ":")), "label": severity, "anomaly_kind": anomaly_kind}


def generate_dataset(out_dir: str | Path, count: int = 1200, seed: int = 7341, holdout_fraction: float = 0.15) -> dict[str, Any]:
    """Write deterministic train/holdout JSONL, with at least 1,000 synthetic examples."""
    if count < 1000:
        raise ValueError("Feature C dataset must contain at least 1,000 examples")
    if not 0.1 <= holdout_fraction <= 0.25:
        raise ValueError("holdout_fraction must be between 0.10 and 0.25")
    rng = random.Random(seed)
    examples = [build_example(index, rng) for index in range(count)]
    rng.shuffle(examples)
    split = int(count * (1 - holdout_fraction))
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    train_path, holdout_path = folder / "train.jsonl", folder / "holdout.jsonl"
    train_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in examples[:split]), encoding="utf-8")
    holdout_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in examples[split:]), encoding="utf-8")
    return {"count": count, "train_count": split, "holdout_count": count - split, "seed": seed, "train_path": str(train_path), "holdout_path": str(holdout_path), "synthetic_only": True}
