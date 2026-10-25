"""Cadence and amount-stability detection for recurring transactions."""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date

from .ingest import Transaction


def detect_recurring(rows: list[Transaction], min_observations: int = 4) -> dict[str, dict]:
    """Return recurring merchant profiles where intervals and amounts are sufficiently stable."""
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for row in rows:
        groups[row.merchant_id].append(row)
    profiles: dict[str, dict] = {}
    for merchant_id, items in groups.items():
        items.sort(key=lambda x: x.date)
        if len(items) < min_observations:
            continue
        days = [(date.fromisoformat(b.date) - date.fromisoformat(a.date)).days for a, b in zip(items, items[1:])]
        if not days:
            continue
        cadence_days = statistics.median(days)
        if 5 <= cadence_days <= 9:
            cadence = "weekly"
        elif 25 <= cadence_days <= 35:
            cadence = "monthly"
        elif 350 <= cadence_days <= 380:
            cadence = "annual"
        else:
            continue
        amounts = [abs(x.amount) for x in items]
        median = statistics.median(amounts)
        if median <= 0:
            continue
        deviations = [abs(a - median) for a in amounts]
        mad = statistics.median(deviations)
        # A recurring profile must be reasonably stable; utility bills get a looser bound.
        allowed = max(median * (0.45 if any(k in items[-1].merchant.lower() for k in ("electric", "utility", "water", "gas")) else 0.20), 2.0)
        if mad > allowed:
            continue
        profiles[merchant_id] = {
            "merchant_id": merchant_id,
            "merchant": items[-1].merchant,
            "cadence": cadence,
            "amount_median": median,
            "observation_count": len(items),
            "last_date": items[-1].date,
            "values": amounts,
            "dates": [x.date for x in items],
        }
    return profiles
