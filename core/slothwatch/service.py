"""CLI command implementations kept separate from argument parsing."""
from __future__ import annotations

import csv
import hashlib
import json
import random
import time
import yaml
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings
from .db import connect
from .ingest import Transaction, read_csv
from .ladder import approve_alert, run_pipeline
from .receipts import verify_receipts


def _csv_paths(path: str | None, settings: Settings) -> list[Path]:
    target = Path(path) if path else settings.data_dir
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.glob("*.csv") if p.is_file())
    raise FileNotFoundError(target)


def _load_rows(paths: list[Path]) -> list[Transaction]:
    """Load CSV rows using optional root-level column_mapping.yaml overrides."""
    mapping_path = Path("column_mapping.yaml")
    mapping: dict[str, str] | None = None
    if mapping_path.is_file():
        loaded = yaml.safe_load(mapping_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError("column_mapping.yaml must contain a YAML mapping")
        mapping = {str(k): str(v) for k, v in loaded.items() if isinstance(v, (str, int, float))}
    rows: list[Transaction] = []
    for path in paths:
        rows.extend(read_csv(path, mapping=mapping))
    return rows


def _redact(settings: Settings, run_id: int | None) -> Path:
    folder = settings.state_dir / "redacted"
    folder.mkdir(parents=True, exist_ok=True)
    with connect(settings.db_path) as conn:
        selected = run_id
        if selected is None:
            item = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            if not item:
                raise ValueError("No runs available to redact")
            selected = item["id"]
        rows = conn.execute("SELECT * FROM transactions WHERE run_id=?", (str(selected),)).fetchall()
        if not rows:
            raise ValueError(f"No transactions found for run #{selected}")
        safe = []
        for row in rows:
            h = hashlib.sha256(row["merchant_id"].encode()).hexdigest()[:7].upper()
            rng = random.Random(row["id"])
            jitter = rng.uniform(0.9, 1.1)
            safe.append({"transaction_id": hashlib.sha256(row["id"].encode()).hexdigest()[:12], "date": row["tx_date"], "merchant": f"Merchant-{h}", "amount": round(float(row["amount"]) * jitter, 2), "category": row["category"], "outcome": row["outcome"], "settled_by": row["settled_by"]})
    path = folder / f"run-{selected}-redacted.json"
    path.write_text(json.dumps({"run_id": selected, "anonymized": True, "rows": safe}, indent=2) + "\n", encoding="utf-8")
    return path


def handle_command(args, settings: Settings) -> int:
    command = args.command
    if command == "run":
        paths = _csv_paths(args.path, settings)
        if not paths:
            print(f"No CSV files found in {args.path or settings.data_dir}")
            return 0
        result = run_pipeline(_load_rows(paths), settings, mode="real", source=", ".join(str(p) for p in paths), no_learning=args.no_learning)
        print(f"Run #{result['run_id']} complete — Sloth Score {result['sloth_score']:.2f} ({result['gemma_wakeups']} Gemma wake-ups).")
        return 0
    if command == "watch":
        seen: dict[str, float] = {}
        print(f"Watching {settings.data_dir} every {args.interval:g}s. Ctrl-C to stop.")
        try:
            while True:
                for path in _csv_paths(None, settings):
                    mtime = path.stat().st_mtime
                    if seen.get(str(path)) != mtime:
                        run_pipeline(_load_rows([path]), settings, mode="real-watch", source=str(path))
                        seen[str(path)] = mtime
                time.sleep(max(1, args.interval))
        except KeyboardInterrupt:
            print("Watch stopped.")
        return 0
    if command == "status":
        with connect(settings.db_path) as conn:
            run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            pending = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
            if not run:
                print("No runs yet. Try: slothwatch run path/to/export.csv")
            else:
                print(json.dumps({"latest_run": dict(run), "pending_approvals": pending}, indent=2))
        return 0
    if command == "score":
        with connect(settings.db_path) as conn:
            rows = conn.execute("SELECT started_at,rows_checked,settled_r0,settled_r1,settled_r15,gemma_wakeups,human_pings,suppressed FROM runs WHERE started_at >= ? ORDER BY id", ((datetime.now() - timedelta(days=30)).isoformat(timespec="seconds"),)).fetchall()
            total_rows = sum(r["rows_checked"] for r in rows)
            wakes = sum(r["gemma_wakeups"] for r in rows)
            score = round(100 * (total_rows - wakes) / total_rows, 2) if total_rows else 100.0
            print(json.dumps({"period": "rolling 30 days", "runs": len(rows), "rows_checked": total_rows, "gemma_wakeups": wakes, "human_pings": sum(r["human_pings"] for r in rows), "settled_r0": sum(r["settled_r0"] for r in rows), "settled_r1": sum(r["settled_r1"] for r in rows), "settled_r1_5": sum(r["settled_r15"] for r in rows), "suppressed": sum(r["suppressed"] for r in rows), "sloth_score_pct": score}, indent=2))
        return 0
    if command in {"approve", "ignore", "snooze"}:
        decision = {"approve": "approve", "ignore": "ignore", "snooze": "snooze"}[command]
        ok = approve_alert(settings, args.id, decision, getattr(args, "hours", 24))
        print("Decision recorded. Approved drafts are in the local outbox and were not sent." if ok and command == "approve" else ("Decision recorded." if ok else "Alert ID not found."))
        return 0 if ok else 1
    if command == "redact":
        print(_redact(settings, args.run_id))
        return 0
    if command == "receipt":
        with connect(settings.db_path) as conn:
            rows = conn.execute("SELECT * FROM receipts ORDER BY id DESC").fetchall()
            if not rows:
                print("No receipts yet.")
                return 0
            if args.sample:
                body = json.loads(rows[0]["body_json"])
                samples = body.get("slept_samples", [])
                candidates = [x for x in samples if x.get("forecast")] or samples
                randomizer = random.Random()
                chosen = randomizer.sample(candidates, min(max(0, args.sample), len(candidates))) if candidates else []
                print(json.dumps({"receipt_id": rows[0]["id"], "samples": chosen}, indent=2))
            else:
                print(json.dumps({"latest_receipt_id": rows[0]["id"], "receipt_hash": rows[0]["receipt_hash"], "rows_checked": rows[0]["rows_checked"], "date": rows[0]["receipt_date"]}, indent=2))
        return 0
    if command == "verify":
        with connect(settings.db_path) as conn:
            ok, errors = verify_receipts(conn, settings.state_dir)
        print("Receipt chain verified." if ok else "Receipt verification FAILED:\n" + "\n".join(errors))
        return 0 if ok else 1
    if command == "canary":
        from .synthetic import generate_transactions
        rows = generate_transactions("electricity-spike", seed=args.seed)
        result = run_pipeline(rows, settings, mode="canary", source=f"canary-seed:{args.seed}", use_ollama=False)
        caught = any(a["merchant"] == "City Electricity" for a in result["alerts"])
        with connect(settings.db_path) as conn:
            conn.execute("INSERT INTO canary_runs(caught,method,notes) VALUES(?,?,?)", (int(caught), "synthetic electricity spike", f"run={result['run_id']}"))
            conn.commit()
        print(f"Is the sloth actually awake? {'YES — planted canary caught.' if caught else 'NO — canary missed.'}")
        return 0 if caught else 1
    raise ValueError(f"unsupported command {command}")
