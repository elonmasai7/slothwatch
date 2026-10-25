"""Hash-chained silence receipts persisted in SQLite and daily JSON files."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def receipt_hash(body: dict, previous_hash: str | None, config_hash: str) -> str:
    payload = {"body": body, "previous_hash": previous_hash, "config_hash": config_hash}
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def write_receipt(conn: sqlite3.Connection, state_dir: Path, run_id: int, body: dict, config_hash: str) -> dict:
    """Append a chained receipt and mirror it into the per-day JSON receipt file."""
    previous = conn.execute("SELECT receipt_hash FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
    previous_hash = previous["receipt_hash"] if previous else None
    digest = receipt_hash(body, previous_hash, config_hash)
    today = datetime.now().date().isoformat()
    cursor = conn.execute(
        "INSERT INTO receipts(run_id, receipt_date, rows_checked, body_json, previous_hash, receipt_hash, config_hash) VALUES(?,?,?,?,?,?,?)",
        (run_id, today, int(body.get("rows_checked", 0)), canonical(body), previous_hash, digest, config_hash),
    )
    row_id = int(cursor.lastrowid)
    receipt = {"id": row_id, "run_id": run_id, "receipt_date": today, "body": body, "previous_hash": previous_hash, "receipt_hash": digest, "config_hash": config_hash}
    receipts_dir = state_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    file_path = receipts_dir / f"{today}.json"
    existing: list[dict] = []
    if file_path.exists():
        try:
            loaded = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
            elif isinstance(loaded, dict):
                existing = [loaded]
        except json.JSONDecodeError:
            existing = []
    existing.append(receipt)
    file_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    conn.commit()
    return receipt


def verify_receipts(conn: sqlite3.Connection, state_dir: Path) -> tuple[bool, list[str]]:
    """Verify database chain and every JSON-file mirror against the persisted digest."""
    errors: list[str] = []
    previous = None
    db_rows = conn.execute("SELECT * FROM receipts ORDER BY id").fetchall()
    db_by_hash = {}
    for row in db_rows:
        body = json.loads(row["body_json"])
        expected = receipt_hash(body, previous, row["config_hash"])
        if row["previous_hash"] != previous:
            errors.append(f"receipt #{row['id']}: previous hash mismatch")
        if row["receipt_hash"] != expected:
            errors.append(f"receipt #{row['id']}: content hash mismatch")
        previous = row["receipt_hash"]
        db_by_hash[row["receipt_hash"]] = (body, row["config_hash"])
    folder = state_dir / "receipts"
    if folder.exists():
        for path in folder.glob("*.json"):
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(items, dict):
                    items = [items]
                for item in items:
                    digest = item.get("receipt_hash")
                    if digest not in db_by_hash:
                        errors.append(f"{path.name}: receipt missing from database")
                        continue
                    body, config_hash = db_by_hash[digest]
                    if item.get("body") != body or item.get("config_hash") != config_hash:
                        errors.append(f"{path.name}: JSON receipt content edited")
                    if receipt_hash(item.get("body", {}), item.get("previous_hash"), item.get("config_hash", "")) != digest:
                        errors.append(f"{path.name}: JSON receipt hash mismatch")
            except (OSError, json.JSONDecodeError, TypeError):
                errors.append(f"{path.name}: invalid receipt JSON")
    return not errors, errors
