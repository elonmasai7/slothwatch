"""Temporal activities. Activity functions use the same shared local pipeline as the CLI."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from temporalio import activity

from slothwatch.config import load_config
from slothwatch.ingest import read_csv
from slothwatch.ladder import approve_alert, run_pipeline


@activity.defn
async def process_nightly(payload: dict) -> dict:
    """Run the shared pipeline for one file/folder; stable alert IDs make retries idempotent."""
    config = str(payload.get("config", "slothwatch.yaml"))
    settings = load_config(config)
    path = Path(payload.get("path") or settings.data_dir)
    paths = [path] if path.is_file() else sorted(path.glob("*.csv"))
    rows = []
    for file_path in paths:
        rows.extend(read_csv(file_path))
    result = run_pipeline(rows, settings, mode="temporal", source=", ".join(map(str, paths)))
    return {"run_id": result["run_id"], "alerts": result["alerts"], "human_pings": result["human_pings"], "rows_checked": result["rows_checked"]}


@activity.defn
async def finalize_human_decision(payload: dict) -> dict:
    """Record the human's decision. This still never sends a message."""
    settings = load_config(str(payload.get("config", "slothwatch.yaml")))
    ok = approve_alert(settings, str(payload["alert_id"]), str(payload["decision"]), int(payload.get("hours", 24)))
    if not ok:
        raise ValueError("alert ID not found")
    return {"alert_id": payload["alert_id"], "decision": payload["decision"], "completed_at": datetime.now().isoformat(timespec="seconds"), "sent": False}
