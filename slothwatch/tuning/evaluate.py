"""Evaluation harness: JSON validity, severity accuracy, rubric score, latency, tokens."""
from __future__ import annotations
import json, re, time
from ..llm import validate

HEDGES = ("might be", "could be", "possibly", "perhaps", "i think", "as an ai")


def sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def rubric(obj: dict, ex: dict) -> float:
    s = ex["input"]
    v = obj["verdict_two_sentences"]
    checks = [sentences(v) == 2, s["merchant"].split()[0].lower() in v.lower(),
              f'{s["amount"]:.2f}' in v or f'{s["amount"]:.0f}' in v,
              not any(h in v.lower() for h in HEDGES),
              (obj["severity"] == "ignore") == (obj["draft_message"].strip() == "") or obj["severity"] != "ignore",
              len(obj["suggested_action"].split()) <= 25]
    return sum(checks) / len(checks)


def evaluate(generate_fn, examples: list[dict]) -> dict:
    """generate_fn(prompt) -> (text, tokens_in, tokens_out)"""
    valid = sev = 0
    rub, lat, tin, tout = [], [], 0, 0
    for ex in examples:
        t0 = time.perf_counter()
        try:
            text, a, b = generate_fn(ex["prompt"])
        except Exception:
            text, a, b = "", 0, 0
        lat.append((time.perf_counter() - t0) * 1000); tin += a; tout += b
        try:
            obj = validate(json.loads(text))
        except Exception:
            obj = None
        if obj:
            valid += 1
            sev += obj["severity"] == ex["gold_severity"]
            rub.append(rubric(obj, ex))
    n = len(examples)
    return {"n": n, "json_validity": valid / n, "severity_accuracy": sev / n,
            "rubric": sum(rub) / len(rub) if rub else 0.0, "avg_latency_ms": sum(lat) / n,
            "avg_tokens": (tin + tout) / n}
