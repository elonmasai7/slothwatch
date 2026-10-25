"""Silence Receipts: every run, quiet or not, writes a hash-chained receipt.
Honest limit: the chain makes casual edits detectable. Someone who can rewrite the whole database AND every
file could recompute it, so note `slothwatch verify`'s head hash somewhere off-machine if you want more."""
from __future__ import annotations
import hashlib, json, random
from pathlib import Path


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + canonical(body)).encode()).hexdigest()


def write_receipt(con, cfg, body: dict) -> dict:
    row = con.execute("SELECT id, hash FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
    rid, prev = (row["id"] + 1, row["hash"]) if row else (1, "GENESIS")
    body = {"n": rid, **body}
    h = _hash(prev, body)
    con.execute("INSERT INTO receipts(id,date,body_json,prev_hash,hash) VALUES(?,?,?,?,?)",
                (rid, body.get("date", ""), canonical(body), prev, h))
    rec = {"body": body, "prev_hash": prev, "hash": h}
    path = cfg.path("receipts", f"{body.get('date', 'x')}-{rid:04d}.json")
    path.write_text(json.dumps(rec, indent=1, default=str))
    return {"id": rid, "hash": h, "path": str(path)}


def verify(con, cfg) -> dict:
    errors, prev, n = [], "GENESIS", 0
    for r in con.execute("SELECT * FROM receipts ORDER BY id"):
        n += 1
        body = json.loads(r["body_json"])
        if r["prev_hash"] != prev:
            errors.append(f"#{r['id']}: chain break (prev_hash mismatch)")
        if _hash(r["prev_hash"], body) != r["hash"]:
            errors.append(f"#{r['id']}: content does not match its hash")
        f = Path(cfg.path("receipts", f"{r['date']}-{r['id']:04d}.json"))
        if not f.exists():
            errors.append(f"#{r['id']}: receipt file missing")
        else:
            rec = json.loads(f.read_text())
            if canonical(rec["body"]) != canonical(body) or rec["hash"] != r["hash"] or rec["prev_hash"] != r["prev_hash"]:
                errors.append(f"#{r['id']}: file differs from database")
            elif _hash(rec["prev_hash"], rec["body"]) != rec["hash"]:
                errors.append(f"#{r['id']}: file content does not match its hash")
        prev = r["hash"]
    return {"ok": not errors, "checked": n, "head": prev, "errors": errors}


def sample_slept(con, n: int = 3, seed: int | None = None) -> list[dict]:
    r = con.execute("SELECT body_json FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return []
    body = json.loads(r["body_json"])
    rows = body.get("settled_rows", [])
    interval = [x for x in rows if x.get("lo") is not None]
    rng = random.Random(seed)
    pick = rng.sample(interval, min(n, len(interval)))
    if len(pick) < n:
        rest = [x for x in rows if x not in pick]
        pick += rng.sample(rest, min(n - len(pick), len(rest)))
    return pick
