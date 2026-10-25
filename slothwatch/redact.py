"""Shareable, anonymised exports for screenshots and the blog post: merchants hashed, amounts jittered (seeded)."""
from __future__ import annotations
import csv, hashlib, json, random
from pathlib import Path
from .db import connect
from .ingest import load_path
from .normalize import clean_merchant


def _alias(name: str) -> str:
    return "Merchant-" + hashlib.sha256(name.encode()).hexdigest()[:5]


def redact_csv(src, out, cfg, seed: int = 1) -> Path:
    rng = random.Random(seed)
    aliases: dict[str, float] = {}
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["Date", "Description", "Amount"])
        for t in load_path(src, cfg):
            m = clean_merchant(t.desc)
            jit = aliases.setdefault(m, rng.uniform(0.93, 1.07))   # same jitter per merchant keeps patterns intact
            w.writerow([t.date, _alias(m), f"{-round(t.amount * jit, 2):.2f}"])
    return out


def redact_export(out, cfg, seed: int = 1) -> Path:
    rng = random.Random(seed)
    con = connect(cfg)
    alerts = []
    for a in con.execute("SELECT date,merchant,amount,kind,deviation,severity,status FROM alerts ORDER BY id"):
        d = dict(a); d["merchant"] = _alias(d["merchant"]); d["amount"] = round(d["amount"] * rng.uniform(0.93, 1.07), 2)
        alerts.append(d)
    r = con.execute("SELECT body_json FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
    body = json.loads(r["body_json"]) if r else {}
    body.pop("settled_rows", None); body.pop("logged_watch", None)
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"alerts": alerts, "latest_receipt": body}, indent=1))
    return out
