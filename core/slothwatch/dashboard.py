"""Offline-capable local dashboard with JSON endpoints; binds only to loopback by default."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import Settings
from .db import connect
from .ladder import approve_alert
from .receipts import verify_receipts

STATIC_PAGE = Path(__file__).parent / "static" / "index.html"


class DecisionRequest(BaseModel):
    decision: str
    hours: int = 24


class SettingsRequest(BaseModel):
    data_dir: str | None = None
    model: str | None = None
    high_severity_amount: float | None = None
    learning_threshold: float | None = None
    ignore_merchants: list[str] | None = None


def make_app(settings: Settings) -> FastAPI:
    """Create the local dashboard application against this Settings instance."""
    app = FastAPI(title="SlothWatch Local Dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return STATIC_PAGE.read_text(encoding="utf-8")

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        with connect(settings.db_path) as conn:
            run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            pending = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
            watch = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='watch'").fetchone()[0]
            rec = conn.execute("SELECT * FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
            body = json.loads(rec["body_json"]) if rec else {}
        return {"latest_run": dict(run) if run else None, "pending_approvals": pending, "watch_alerts": watch, "latest_receipt": {"id": rec["id"], "hash": rec["receipt_hash"], "body": body} if rec else None, "mode": "local-in-process"}

    @app.get("/api/alerts")
    def alerts() -> list[dict]:
        with connect(settings.db_path) as conn:
            rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    @app.post("/api/alerts/{alert_id}/decision")
    def alert_decision(alert_id: str, request: DecisionRequest) -> dict:
        if request.decision not in {"approve", "ignore", "snooze"}:
            raise HTTPException(status_code=400, detail="decision must be approve, ignore, or snooze")
        if not approve_alert(settings, alert_id, request.decision, request.hours):
            raise HTTPException(status_code=404, detail="alert not found")
        return {"ok": True, "decision": request.decision, "note": "Approved drafts are copied to the local outbox only; nothing is sent."}

    @app.get("/api/recurring")
    def recurring() -> list[dict]:
        with connect(settings.db_path) as conn:
            rows = conn.execute("SELECT * FROM recurring_profiles ORDER BY merchant COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    @app.get("/api/score")
    def score() -> dict:
        with connect(settings.db_path) as conn:
            rows = conn.execute("SELECT id,started_at,mode,rows_checked,settled_r0,settled_r1,settled_r15,gemma_wakeups,human_pings,suppressed,alerts FROM runs ORDER BY id DESC LIMIT 30").fetchall()
        values = [dict(row) for row in rows]
        total = sum(item["rows_checked"] or 0 for item in values)
        wakes = sum(item["gemma_wakeups"] or 0 for item in values)
        return {"runs": list(reversed(values)), "rows_checked": total, "gemma_wakeups": wakes, "sloth_score": round(100 * (total - wakes) / total, 2) if total else 100.0}

    @app.get("/api/receipts")
    def receipts() -> dict:
        with connect(settings.db_path) as conn:
            ok, errors = verify_receipts(conn, settings.state_dir)
            rows = conn.execute("SELECT id,run_id,receipt_date,rows_checked,receipt_hash,previous_hash,body_json FROM receipts ORDER BY id DESC LIMIT 30").fetchall()
            canaries = conn.execute("SELECT * FROM canary_runs ORDER BY id DESC LIMIT 20").fetchall()
        canary_values = [dict(row) for row in canaries]
        catches = sum(1 for item in canary_values if item["caught"])
        return {"verified": ok, "errors": errors, "receipts": [{**dict(row), "body": json.loads(row["body_json"])} for row in rows], "canaries": canary_values, "canary_checks": len(canary_values), "canary_caught": catches, "canary_catch_rate": (catches / len(canary_values)) if canary_values else None}

    @app.post("/api/verify")
    def verify() -> dict:
        with connect(settings.db_path) as conn:
            ok, errors = verify_receipts(conn, settings.state_dir)
        return {"verified": ok, "errors": errors}

    @app.get("/api/settings")
    def get_settings() -> dict:
        return {"data_dir": str(settings.values["app"]["data_dir"]), "model": settings.values["llm"]["model"], "high_severity_amount": settings.values["rules"]["high_severity_amount"], "learning_threshold": settings.values["rules"]["learning_threshold"], "ignore_merchants": settings.values["rules"].get("ignore_merchants", []), "config_hash": settings.config_hash}

    @app.post("/api/settings")
    def update_settings(request: SettingsRequest) -> dict:
        if request.high_severity_amount is not None and request.high_severity_amount < 0:
            raise HTTPException(status_code=400, detail="high severity amount must be nonnegative")
        if request.learning_threshold is not None and not 0 <= request.learning_threshold <= 1:
            raise HTTPException(status_code=400, detail="learning threshold must be between 0 and 1")
        if request.data_dir is not None:
            settings.values["app"]["data_dir"] = request.data_dir
        if request.model is not None:
            settings.values["llm"]["model"] = request.model
        if request.high_severity_amount is not None:
            settings.values["rules"]["high_severity_amount"] = request.high_severity_amount
        if request.learning_threshold is not None:
            settings.values["rules"]["learning_threshold"] = request.learning_threshold
        if request.ignore_merchants is not None:
            settings.values["rules"]["ignore_merchants"] = [x.strip() for x in request.ignore_merchants if x.strip()]
        settings.source.write_text(yaml.safe_dump(settings.values, sort_keys=False), encoding="utf-8")
        return {"saved": True, "config_hash": settings.config_hash}

    @app.get("/api/benchmark")
    def benchmark() -> dict:
        report = Path("benchmarks/results/benchmark.json")
        if not report.exists():
            return {"available": False, "message": "Run `make benchmark` to generate a measured local report."}
        return {"available": True, **json.loads(report.read_text(encoding="utf-8"))}

    return app


def main() -> None:
    import argparse
    import webbrowser
    import uvicorn
    from .config import load_config

    parser = argparse.ArgumentParser(description="Run SlothWatch localhost dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback only by default")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the local browser")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Privacy guard: dashboard host must be loopback")
    url = f"http://127.0.0.1:{args.port}"
    print(f"SlothWatch dashboard: {url}")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(make_app(load_config()), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
