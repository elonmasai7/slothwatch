"""Seeded generator and scenario packs for the no-personal-data demo."""
from __future__ import annotations

import csv
import random
from datetime import date
from pathlib import Path

from .ingest import Transaction
from .normalize import normalize_transactions

SCENARIOS = ("clean-month", "double-subscription", "duplicate-charge", "electricity-spike", "creeping-increase", "everything-wrong")


def generate_transactions(scenario: str = "clean-month", seed: int = 2401) -> list[Transaction]:
    """Generate twelve months of fictional recurring bills and noisy everyday spending."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    rng = random.Random(seed)
    rows: list[Transaction] = []
    recurring = [
        ("Rent", 1250, "Housing", 0.0),
        ("City Electricity", 88, "Utilities", 0.12),
        ("Home Internet", 64, "Utilities", 0.0),
        ("Mobile Phone", 45, "Phone", 0.0),
        ("StreamFlix", 14.99, "Subscription", 0.0),
        ("TuneCloud", 9.99, "Subscription", 0.0),
        ("Cloud Storage", 2.99, "Subscription", 0.0),
        ("News Digital", 7.99, "Subscription", 0.0),
        ("Gym Membership", 39, "Fitness", 0.0),
        ("Northstar Insurance", 125, "Insurance", 0.0),
    ]
    for month in range(12):
        year = 2025 + month // 12
        mo = month % 12 + 1
        tx_date = date(year, mo, 4 if mo != 2 else 3).isoformat()
        for merchant, base, category, noise in recurring:
            amount = base
            if merchant == "City Electricity":
                season = [0.80, 0.82, 0.88, 0.92, 1.0, 1.08, 1.16, 1.14, 1.06, 0.98, 0.90, 0.84][month]
                amount = base * season * (1 + rng.uniform(-0.035, 0.035))
            elif merchant == "Northstar Insurance" and scenario in {"creeping-increase", "everything-wrong"}:
                amount = base * (1.028 ** month)
            elif noise:
                amount = base * (1 + rng.uniform(-noise, noise))
            # Scenario injections affect the latest month only, except the gradual insurance drift.
            if month == 11 and scenario in {"double-subscription", "everything-wrong"} and merchant == "StreamFlix":
                amount *= 1.40
            if month == 11 and scenario in {"electricity-spike", "everything-wrong"} and merchant == "City Electricity":
                amount *= 2.55
            row_id = f"syn-{scenario}-{month:02d}-{merchant.lower().replace(' ', '-')}-{len(rows):04d}"
            rows.append(Transaction(row_id, tx_date, merchant, round(amount, 2), "Demo checking", category, source_file=f"synthetic:{scenario}", is_anomaly=bool(month == 11 and ((scenario in {"double-subscription", "everything-wrong"} and merchant == "StreamFlix") or (scenario in {"electricity-spike", "everything-wrong"} and merchant == "City Electricity") or (scenario in {"creeping-increase", "everything-wrong"} and merchant == "Northstar Insurance")))))
        # Ordinary, modest spending stays boring at rung 0 and adds realistic row volume.
        for j in range(13):
            merchant = rng.choice(["Corner Grocer", "Coffee Counter", "Transit Tap", "Lunch Spot", "Market Basket", "Pharmacy"])
            day = 5 + (j * 2) % 24
            tx_day = min(day, 28 if mo == 2 else 30)
            amount = round(rng.uniform(3.5, 34.0), 2)
            row_id = f"syn-{scenario}-{month:02d}-daily-{j:02d}-{len(rows):04d}"
            rows.append(Transaction(row_id, date(year, mo, tx_day).isoformat(), merchant, amount, "Demo checking", "Everyday", source_file=f"synthetic:{scenario}"))
    if scenario in {"duplicate-charge", "everything-wrong"}:
        original = next(x for x in rows if x.description == "StreamFlix" and x.date.startswith("2025-12"))
        dup_date = "2025-12-06"
        rows.append(Transaction(f"syn-{scenario}-injected-duplicate", dup_date, original.description, original.amount, original.account, original.category, source_file=f"synthetic:{scenario}", is_anomaly=True))
    normalize_transactions(rows)
    return sorted(rows, key=lambda x: (x.date, x.id))


def write_csv(rows: list[Transaction], path: str | Path) -> Path:
    """Write a synthetic CSV in a standard bank-export shape."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Date", "Description", "Amount", "Account", "Category", "is_anomaly"])
        writer.writeheader()
        for row in rows:
            writer.writerow({"Date": row.date, "Description": row.description, "Amount": f"{row.amount:.2f}", "Account": row.account, "Category": row.category, "is_anomaly": int(row.is_anomaly)})
    return target


def generate_monthly_files(scenario: str, out_dir: str | Path, seed: int = 2401) -> list[Path]:
    """Write separate monthly CSV files for the scenario."""
    rows = generate_transactions(scenario, seed)
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    grouped: dict[str, list[Transaction]] = {}
    for tx in rows:
        grouped.setdefault(tx.date[:7], []).append(tx)
    for month, items in sorted(grouped.items()):
        paths.append(write_csv(items, target_dir / f"{month}.csv"))
    return paths
