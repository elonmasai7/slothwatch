"""Seeded, deterministic synthetic life: 12 months of history + one scenario month, as bank-style CSVs.
Dates are fixed (not 'today') so every run is reproducible."""
from __future__ import annotations
import csv, math, random
from datetime import date, timedelta
from pathlib import Path

HIST_START = date(2025, 9, 1)       # 12 months of history: 2025-09 .. 2026-08
SCEN_START, SCEN_END = date(2026, 9, 1), date(2026, 9, 30)

# name, base amount, day of month, seasonal amplitude, noise sd
RECURRING = [
    ("RENT SUNRISE PROPERTIES", 1200.00, 1, 0.0, 0.0),
    ("CITY POWER & LIGHT ELECTRIC", 95.00, 5, 0.18, 0.05),
    ("NORTHLINK FIBER INTERNET", 59.99, 8, 0.0, 0.0),
    ("PULSE MOBILE", 45.00, 12, 0.0, 0.0),
    ("STREAMBOX PREMIUM", 15.49, 15, 0.0, 0.0),
    ("TUNEWAVE MUSIC", 10.99, 18, 0.0, 0.0),
    ("CLOUDVAULT STORAGE", 2.99, 20, 0.0, 0.0),
    ("PAGEFLIX NEWS", 8.00, 22, 0.0, 0.0),
    ("IRONWORKS GYM", 39.00, 3, 0.0, 0.0),
    ("SHIELDSURE INSURANCE", 118.40, 25, 0.0, 0.0),
    ("WATERWORKS WATER", 32.00, 10, 0.12, 0.05),
]
EVERYDAY = [("FRESHMART", 25, 140), ("GREENGROCER", 8, 45), ("BEANBAR COFFEE", 3, 7), ("QUICKFUEL", 30, 70),
            ("CORNER BISTRO", 12, 55), ("HEALTHPLUS PHARMACY", 6, 40), ("PAPERTRAIL BOOKS", 9, 30),
            ("URBANRIDE", 6, 25), ("HOMEHARDWARE", 10, 90)]

SCENARIOS = {
    "clean-month": [],
    "double-subscription": [("STREAMBOX PREMIUM", "outside_interval")],
    "duplicate-charge": [("FRESHMART", "duplicate")],
    "electricity-spike": [("CITY POWER & LIGHT ELECTRIC", "outside_interval")],
    "creeping-increase": [("SHIELDSURE INSURANCE", "creeping_increase")],
    "everything-wrong": [("STREAMBOX PREMIUM", "outside_interval"), ("FRESHMART", "duplicate"),
                         ("CITY POWER & LIGHT ELECTRIC", "outside_interval"),
                         ("SHIELDSURE INSURANCE", "creeping_increase")],
}


def _months():
    y, m = HIST_START.year, HIST_START.month
    for i in range(13):
        yield i, y + (m - 1 + i) // 12, (m - 1 + i) % 12 + 1


def _desc(rng, name, d):
    style = rng.choice([0, 1, 2])
    ref = "".join(rng.choice("0123456789ABCDEF") for _ in range(8))
    if style == 0:
        return f"CARD {rng.randint(1000, 9999)} {name} {ref} {d:%m/%d}"
    if style == 1:
        return f"{name} #{rng.randint(10000, 99999)}"
    return f"POS PURCHASE {name} {d:%d/%m} REF{ref[:5]}"


def build(scenario: str = "clean-month", seed: int = 7) -> list[tuple[date, str, float]]:
    """Returns [(date, description, amount_negative_for_spend)]."""
    if scenario not in SCENARIOS:
        raise KeyError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    rng = random.Random(seed)
    rows: list[tuple[date, str, float]] = []
    wrong = {m for m, _ in SCENARIOS[scenario]}
    for i, y, mo in _months():
        for name, base, day, seas, sd in RECURRING:
            d = date(y, mo, min(day + rng.choice([0, 0, 0, 1, 2]) if day < 27 else day, 28))
            amt = base * (1 + seas * math.cos(2 * math.pi * (mo - 1) / 12)) * (1 + rng.gauss(0, sd))
            if i == 12 and name in wrong:
                if name == "STREAMBOX PREMIUM":
                    amt = base * 1.40
                elif name == "CITY POWER & LIGHT ELECTRIC":
                    amt = amt * 2.3
            if name == "SHIELDSURE INSURANCE" and name in wrong and i >= 9:
                amt = base * 1.03 ** (i - 9)           # 3% creep each month over the last four cycles
            rows.append((d, _desc(rng, name, d), -round(amt, 2)))
        for _ in range(rng.randint(24, 34)):            # noisy everyday spending
            name, lo, hi = rng.choice(EVERYDAY)
            d = date(y, mo, rng.randint(1, 28))
            rows.append((d, _desc(rng, name, d), -round(rng.uniform(lo, hi), 2)))
        if i == 12 and "FRESHMART" in wrong:
            d = date(y, mo, 14)
            amt = 64.20
            rows.append((d, f"CARD 4421 FRESHMART {rng.randint(10**7, 10**8)} {d:%m/%d}", -amt))
            rows.append((d + timedelta(days=1), f"CARD 4421 FRESHMART {rng.randint(10**7, 10**8)} {(d + timedelta(days=1)):%m/%d}", -amt))
        if rng.random() < 0.5:                          # a refund/credit so credits are exercised
            rows.append((date(y, mo, 20), "PAYROLL ACME CORP", round(rng.uniform(2800, 3000), 2)))
    return sorted(rows, key=lambda r: (r[0], r[1]))


def planted(scenario: str) -> list[dict]:
    return [{"merchant": m, "kind": k} for m, k in SCENARIOS[scenario]]


def write_csvs(scenario: str, out_dir: str | Path, seed: int = 7) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = build(scenario, seed)
    paths = []
    for _, y, mo in _months():
        p = out / f"statement-{y}-{mo:02d}.csv"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Description", "Amount"])
            for d, desc, amt in rows:
                if (d.year, d.month) == (y, mo):
                    w.writerow([d.isoformat(), desc, f"{amt:.2f}"])
        paths.append(p)
    return paths


def load_rows(scenario: str, seed: int = 7, workdir: str | Path | None = None):
    """Round-trips through real CSV files so the demo exercises the real ingest path."""
    import tempfile
    from ..config import Config
    from ..ingest import load_path
    d = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="slothwatch-demo-"))
    write_csvs(scenario, d, seed)
    return load_path(d, Config())
