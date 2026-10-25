"""Synthetic training set for Rung 2: (flagged charge + forecast context) -> strict-JSON verdict.
Labels come from a deterministic policy (llm.template), so this teaches FORMAT + SEVERITY POLICY, not world knowledge."""
from __future__ import annotations
import json, random
from ..config import Config
from ..llm import Explainer, template

MERCHANTS = ["STREAMBOX PREMIUM", "NORTHLINK FIBER", "CITY POWER LIGHT", "SHIELDSURE INSURANCE", "TUNEWAVE MUSIC",
             "FRESHMART", "IRONWORKS GYM", "PULSE MOBILE", "CLOUDVAULT STORAGE", "WATERWORKS WATER", "PAGEFLIX NEWS",
             "ORBIT FITNESS", "QUICKFUEL", "HOMEHARDWARE", "VIDEOHUB PLUS"]


def make_example(rng: random.Random, ex: Explainer) -> dict:
    kind = rng.choices(["outside_interval", "duplicate", "creeping_increase", "large_new_merchant"], [5, 2, 2, 1])[0]
    base = round(rng.uniform(5, 250), 2)
    dev = {"outside_interval": rng.choice([-1, 1]) * rng.uniform(4, 150), "creeping_increase": rng.uniform(8, 30)}.get(kind, 0.0)
    amount = round(base * (1 + dev / 100), 2) if kind in ("outside_interval", "creeping_increase") else (base if kind == "duplicate" else round(rng.uniform(300, 900), 2))
    s = {"merchant": rng.choice(MERCHANTS), "date": f"2026-0{rng.randint(1, 9)}-{rng.randint(10, 28)}", "amount": amount, "kind": kind,
         "note": {"duplicate": "same amount charged 1 day(s) earlier", "large_new_merchant": "first charge from this merchant"}.get(kind, ""),
         "expected": base, "lo": round(base * 0.93, 2), "hi": round(base * 1.07, 2), "method": rng.choice(["tabpfn", "robust_mad"]),
         "deviation": dev}
    gold = template(s)
    return {"input": s, "prompt": ex.prompt_for(s), "completion": json.dumps(gold), "gold_severity": gold["severity"]}


def generate(n: int = 1200, seed: int = 0, holdout: float = 0.15):
    rng, ex = random.Random(seed), Explainer(Config())
    data = [make_example(rng, ex) for _ in range(n)]
    cut = int(n * (1 - holdout))
    return data[:cut], data[cut:]
