"""Rung 0 rules: allow/ignore lists, duplicates, stable recurring charges and small-noise handling."""
from __future__ import annotations

from datetime import date

from .ingest import Transaction


def apply_rung0(rows: list[Transaction], config: dict, known_merchants: set[str] | None = None) -> dict[str, dict]:
    """Return per-row rule decisions. Duplicate candidates are flagged rather than hidden."""
    rules = config.get("rules", config)
    duplicate_days = int(rules.get("duplicate_window_days", 3))
    small_amount = float(rules.get("small_new_merchant_amount", 15.0))
    ignore = {str(x).casefold() for x in rules.get("ignore_merchants", [])}
    allow = {str(x).casefold() for x in rules.get("allow_merchants", [])}
    seen: dict[str, list[Transaction]] = {}
    outcomes: dict[str, dict] = {}
    historical = known_merchants or set()
    for tx in sorted(rows, key=lambda x: (x.date, x.id)):
        merchant_key = tx.merchant.casefold()
        history = seen.setdefault(tx.merchant_id, [])
        duplicate = next((old for old in history if abs(abs(old.amount) - abs(tx.amount)) <= 0.01 and abs((date.fromisoformat(tx.date) - date.fromisoformat(old.date)).days) <= duplicate_days), None)
        if duplicate:
            outcomes[tx.id] = {"settled": False, "outcome": "surprise", "reason": f"possible duplicate of transaction on {duplicate.date}", "rung": "r0", "duplicate": True}
        elif merchant_key in ignore or tx.merchant_id in ignore:
            outcomes[tx.id] = {"settled": True, "outcome": "ignored", "reason": "merchant on ignore-list", "rung": "r0"}
        elif merchant_key in allow or tx.merchant_id in allow:
            outcomes[tx.id] = {"settled": True, "outcome": "boring", "reason": "merchant on allow-list", "rung": "r0"}
        elif history and abs(abs(history[-1].amount) - abs(tx.amount)) <= max(0.01, abs(tx.amount) * 0.005):
            gap = abs((date.fromisoformat(tx.date) - date.fromisoformat(history[-1].date)).days)
            if gap >= 5:
                outcomes[tx.id] = {"settled": True, "outcome": "boring", "reason": "amount matches previous cycle", "rung": "r0"}
            else:
                outcomes[tx.id] = {"settled": False, "outcome": "continue", "reason": "same merchant and amount inside duplicate window", "rung": "r0"}
        elif tx.merchant_id not in historical and not history and abs(tx.amount) <= small_amount:
            outcomes[tx.id] = {"settled": True, "outcome": "watch", "reason": "small first-seen merchant; observe only", "rung": "r0"}
        elif abs(tx.amount) <= max(small_amount, 40.0) and not tx.is_anomaly:
            outcomes[tx.id] = {"settled": True, "outcome": "boring", "reason": "low-value variable spend below review threshold", "rung": "r0"}
        else:
            outcomes[tx.id] = {"settled": False, "outcome": "continue", "reason": "rules cannot settle this row", "rung": "r0"}
        history.append(tx)
    return outcomes
