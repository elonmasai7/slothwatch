"""End-to-end sloth ladder, explicit human gate, score engine and receipt creation."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings
from .db import connect
from .forecast import forecast_next_month, forecast_rows
from .ingest import Transaction
from .learning import decision_features, predict_approval
from .llm import explain_with_ollama
from .normalize import normalize_transactions
from .receipts import write_receipt
from .recurring import detect_recurring
from .rules import apply_rung0
from .tracing import LocalTracer, sentry_dsn_from_env

QUIET_LINE = "Nothing needs you. Go back to sleep."


def stable_alert_id(transaction_id: str, reason: str) -> str:
    """Create an idempotency key stable across replay of the same flagged row."""
    return hashlib.sha256(f"{transaction_id}|{reason}".encode()).hexdigest()[:18]


def _save_profiles(conn: sqlite3.Connection, rows: list[Transaction], settings: Settings) -> dict[str, dict]:
    profiles = detect_recurring(rows, int(settings.values["rules"]["recurring_min_observations"]))
    forecasts = forecast_rows(rows, int(settings.values["rules"]["recurring_min_observations"]), float(settings.values["rules"]["mad_multiplier"]))
    grouped: dict[str, list[Transaction]] = defaultdict(list)
    for row in rows:
        grouped[row.merchant_id].append(row)
    for merchant_id, profile in profiles.items():
        latest_forecast = forecast_next_month(profile["values"], profile["dates"])
        conn.execute(
            "INSERT INTO recurring_profiles(merchant_id,merchant,cadence,amount_median,observation_count,last_date,next_amount,lower_bound,upper_bound,method) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(merchant_id) DO UPDATE SET merchant=excluded.merchant,cadence=excluded.cadence,amount_median=excluded.amount_median,observation_count=excluded.observation_count,last_date=excluded.last_date,next_amount=excluded.next_amount,lower_bound=excluded.lower_bound,upper_bound=excluded.upper_bound,method=excluded.method,updated_at=CURRENT_TIMESTAMP",
            (merchant_id, profile["merchant"], profile["cadence"], profile["amount_median"], profile["observation_count"], profile["last_date"], latest_forecast["expected"], latest_forecast["low"], latest_forecast["high"], latest_forecast["method"]),
        )
    return forecasts


def _write_row_decision(conn, run_id: int, tx: Transaction, rung: str, outcome: str, reason: str, method: str = "rules", details: dict | None = None) -> None:
    conn.execute("INSERT INTO row_decisions(run_id,transaction_id,rung,outcome,reason,method,details_json) VALUES(?,?,?,?,?,?,?)", (run_id, tx.id, rung, outcome, reason, method, json.dumps(details or {}, sort_keys=True)))


def _store_alert(conn: sqlite3.Connection, run_id: int, tx: Transaction, verdict: dict, reason: str, context: dict, severity: str) -> tuple[str, bool]:
    alert_id = stable_alert_id(tx.id, reason)
    pending = severity == "act"
    # INSERT OR IGNORE provides exactly-once semantics for a replayed activity.
    conn.execute(
        "INSERT OR IGNORE INTO alerts(id,run_id,transaction_id,merchant,amount,tx_date,verdict,severity,suggested_action,draft_message,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (alert_id, run_id, tx.id, tx.merchant, tx.amount, tx.date, verdict["verdict_two_sentences"], severity, verdict["suggested_action"], verdict["draft_message"], "pending" if pending else "watch"),
    )
    return alert_id, pending


def run_pipeline(rows: list[Transaction], settings: Settings, mode: str = "real", source: str = "", no_learning: bool = False, use_ollama: bool = True) -> dict:
    """Analyze a set of rows, recording row outcomes, alerts, traces, Sloth Score and receipt."""
    start_time = time.perf_counter()
    rows = normalize_transactions(rows)
    values = settings.values
    state_dir = settings.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    tracer = LocalTracer(values["tracing"]["local_trace_file"], sentry_dsn_from_env(values["tracing"]))
    with connect(settings.db_path) as conn:
        started = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute("INSERT INTO runs(started_at,mode,source,config_hash) VALUES(?,?,?,?)", (started, mode, source, settings.config_hash))
        run_id = int(cur.lastrowid)
        stored_merchants = {r[0] for r in conn.execute("SELECT DISTINCT merchant_id FROM transactions")}
        with tracer.span("rung0.rules", rung=0, model="none", woke=False, slept=True) as span:
            r0 = apply_rung0(rows, values, stored_merchants)
            span["rows_checked"] = len(rows)
        with tracer.span("rung1.forecast", rung=1, model="robust-median-mad", woke=False, slept=True) as span:
            forecasts = _save_profiles(conn, rows, settings)
            span["rows_with_forecast"] = len(forecasts)
        grouped: dict[str, list[Transaction]] = defaultdict(list)
        for tx in rows:
            grouped[tx.merchant_id].append(tx)
        positions = {}
        for group in grouped.values():
            group.sort(key=lambda x: (x.date, x.id))
            for idx, tx in enumerate(group):
                positions[tx.id] = idx
        counts = Counter()
        results = []
        flagged: list[tuple[Transaction, dict, str, dict]] = []
        suppressed = 0
        slept_samples = []
        for tx in sorted(rows, key=lambda x: (x.date, x.id)):
            rule = r0[tx.id]
            tx_dict = tx.to_dict()
            if rule["settled"]:
                counts["r0"] += 1
                _write_row_decision(conn, run_id, tx, "r0", rule["outcome"], rule["reason"], details=rule)
                results.append({"transaction_id": tx.id, "merchant": tx.merchant, "date": tx.date, "amount": tx.amount, "outcome": rule["outcome"], "settled_by": "r0", "reason": rule["reason"], "forecast": None, "is_anomaly": tx.is_anomaly})
                slept_samples.append({"merchant": tx.merchant, "date": tx.date, "amount": tx.amount, "reason": rule["reason"], "forecast": None})
                continue
            context = forecasts.get(tx.id)
            reason = rule["reason"] if rule.get("duplicate") else ""
            surprise = bool(rule.get("duplicate"))
            if rule.get("duplicate"):
                # The deterministic duplicate finding outranks forecast interpretation.
                pass
            elif context:
                amount = abs(tx.amount)
                inside = context["low"] <= amount <= context["high"]
                if context.get("creeping_increase"):
                    surprise = True
                    reason = "creeping increase across recent recurring cycles"
                elif not inside:
                    surprise = True
                    ratio = abs(amount - context["expected"]) / max(context["expected"], 1)
                    reason = "utility spike outside forecast interval" if "electric" in tx.merchant.lower() and ratio > 0.75 else "amount outside expected forecast interval"
                elif not surprise:
                    counts["r1"] += 1
                    _write_row_decision(conn, run_id, tx, "r1", "boring", "inside prediction interval", context["method"], context)
                    results.append({"transaction_id": tx.id, "merchant": tx.merchant, "date": tx.date, "amount": tx.amount, "outcome": "boring", "settled_by": "r1", "reason": "inside prediction interval", "forecast": context, "is_anomaly": tx.is_anomaly})
                    slept_samples.append({"merchant": tx.merchant, "date": tx.date, "amount": tx.amount, "reason": "inside prediction interval", "forecast": context})
                    continue
            else:
                merchant_count = len(grouped[tx.merchant_id])
                if merchant_count == 1 and abs(tx.amount) >= float(values["rules"]["high_severity_amount"]):
                    surprise = True
                    reason = "new high-value merchant requires review"
                else:
                    counts["r1"] += 1
                    reason = "insufficient history; baseline learning only"
                    _write_row_decision(conn, run_id, tx, "r1", "watch", reason, "insufficient-history")
                    results.append({"transaction_id": tx.id, "merchant": tx.merchant, "date": tx.date, "amount": tx.amount, "outcome": "watch", "settled_by": "r1", "reason": reason, "forecast": None, "is_anomaly": tx.is_anomaly})
                    slept_samples.append({"merchant": tx.merchant, "date": tx.date, "amount": tx.amount, "reason": reason, "forecast": None})
                    continue
            feature_ctx = context or {"expected": None, "low": None, "high": None, "cadence": "unknown"}
            features = decision_features(tx_dict, feature_ctx, positions.get(tx.id, 0))
            prediction = predict_approval(conn, features, float(values["rules"]["high_severity_amount"]), float(values["rules"]["learning_threshold"]), no_learning=no_learning)
            if prediction.get("suppressed"):
                suppressed += 1
                counts["r15"] += 1
                _write_row_decision(conn, run_id, tx, "r1.5", "suppressed-watch", prediction["reason"], prediction["method"], prediction)
                results.append({"transaction_id": tx.id, "merchant": tx.merchant, "date": tx.date, "amount": tx.amount, "outcome": "watch", "settled_by": "r1.5", "reason": prediction["reason"], "forecast": context, "is_anomaly": tx.is_anomaly})
                continue
            flagged.append((tx, feature_ctx, reason, features))
        gemma_wakes = human_pings = 0
        alert_results = []
        for tx, context, reason, features in flagged:
            with tracer.span("rung2.explain", rung=2, model=values["llm"]["model"], woke=True, method="ollama-or-template") as span:
                verdict, model_meta = explain_with_ollama(tx.to_dict(), context, reason, values["llm"], use_ollama=use_ollama)
                span.update({"tokens_in": model_meta.get("tokens_in", 0), "tokens_out": model_meta.get("tokens_out", 0), "model": model_meta.get("model", model_meta.get("provider", "template")), "cost_usd": 0.0, "woke": True})
            gemma_wakes += 1
            alert_id, pending = _store_alert(conn, run_id, tx, verdict, reason, context, verdict["severity"])
            if pending:
                human_pings += 1
            _write_row_decision(conn, run_id, tx, "r2", "flagged", reason, model_meta.get("provider", "template"), {"forecast": context, "severity": verdict["severity"], "alert_id": alert_id})
            explanation_method = model_meta.get("provider", "template")
            alert_results.append({"id": alert_id, "transaction_id": tx.id, "merchant": tx.merchant, "date": tx.date, "amount": tx.amount, **verdict, "status": "pending" if pending else "watch", "reason": reason, "forecast": context, "is_anomaly": tx.is_anomaly, "explanation_method": explanation_method})
            results.append({"transaction_id": tx.id, "merchant": tx.merchant, "date": tx.date, "amount": tx.amount, "outcome": "alert", "settled_by": "r2", "reason": reason, "forecast": context, "is_anomaly": tx.is_anomaly, "severity": verdict["severity"], "explanation_method": explanation_method})
        for tx in rows:
            conn.execute("INSERT OR IGNORE INTO transactions(id,run_id,tx_date,description,merchant,merchant_id,amount,account,category,source_file,is_anomaly,outcome,settled_by,forecast_low,forecast_high,forecast_method) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tx.id, str(run_id), tx.date, tx.description, tx.merchant, tx.merchant_id, tx.amount, tx.account, tx.category, tx.source_file, int(tx.is_anomaly), next((x["outcome"] for x in results if x["transaction_id"] == tx.id), "boring"), next((x["settled_by"] for x in results if x["transaction_id"] == tx.id), "r0"), (forecasts.get(tx.id) or {}).get("low"), (forecasts.get(tx.id) or {}).get("high"), (forecasts.get(tx.id) or {}).get("method")))
        finished = datetime.now().isoformat(timespec="seconds")
        conn.execute("UPDATE runs SET finished_at=?,rows_checked=?,settled_r0=?,settled_r1=?,settled_r15=?,gemma_wakeups=?,human_pings=?,alerts=?,suppressed=?,status='complete' WHERE id=?", (finished, len(rows), counts["r0"], counts["r1"], counts["r15"], gemma_wakes, human_pings, len(alert_results), suppressed, run_id))
        conn.commit()
        row_count = max(1, len(rows))
        sloth_score = round(100 * (len(rows) - gemma_wakes) / row_count, 2)
        row_decisions = []
        for item in results:
            forecast = item.get("forecast") or {}
            row_decisions.append({
                "transaction_id": item["transaction_id"],
                "settled_by": item.get("settled_by", "unknown"),
                "outcome": item.get("outcome", "unknown"),
                "reason": item.get("reason", ""),
                "method": item.get("explanation_method") or forecast.get("method") or item.get("settled_by", "rules"),
                "forecast_interval": {key: forecast.get(key) for key in ("expected", "low", "high", "method", "history_count")} if forecast else None,
            })
        body = {
            "run_id": run_id, "mode": mode, "source": source, "rows_checked": len(rows),
            "settled_by_rung": {"r0_rules": counts["r0"], "r1_forecast": counts["r1"], "r1_5_learning": counts["r15"], "r2_explainer": gemma_wakes, "r3_human_review": human_pings},
            "gemma_wakeups": gemma_wakes, "human_pings": human_pings, "alert_count": len(alert_results),
            "suppressed_count": suppressed, "sloth_score": sloth_score, "config_hash": settings.config_hash,
            "method_counts": dict(Counter(item["method"] for item in row_decisions)),
            "row_decisions": row_decisions,
            "slept_samples": slept_samples[:100],
        }
        receipt = write_receipt(conn, state_dir, run_id, body, settings.config_hash)
        conn.commit()
    # All notifications are local; the only external effect permitted is a local desktop notification.
    if not alert_results:
        print(f"{QUIET_LINE} ({len(rows)} rows checked, 0 surprises. Receipt #{receipt['id']})")
    else:
        for alert in alert_results:
            print(alert["verdict_two_sentences"])
        _write_outbox_notice(settings.state_dir, alert_results)
    for alert in alert_results:
        if alert["status"] == "pending":
            _desktop_notice(f"SlothWatch: review {alert['merchant']}", alert["verdict_two_sentences"])
    elapsed = round(time.perf_counter() - start_time, 4)
    return {"run_id": run_id, "rows_checked": len(rows), "rung_counts": dict(counts), "gemma_wakeups": gemma_wakes, "human_pings": human_pings, "sloth_score": sloth_score, "alerts": alert_results, "results": results, "receipt": receipt, "elapsed_seconds": elapsed, "mode": mode, "orchestration": "local-in-process", "suppressed_count": suppressed}


def _write_outbox_notice(state_dir: Path, alerts: list[dict]) -> None:
    folder = state_dir / "outbox"
    folder.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(f"{a['merchant']} ({a['date']}, {abs(a['amount']):.2f})\n{a['verdict_two_sentences']}\nDraft only — not sent.\n{a['draft_message']}" for a in alerts)
    (folder / "latest-alerts.txt").write_text(body + "\n", encoding="utf-8")


def _desktop_notice(title: str, message: str) -> None:
    """Best-effort Linux desktop notice, silently skipped when unavailable."""
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", title, message[:240]], timeout=2, check=False, capture_output=True)
        except OSError:
            pass


def approve_alert(settings: Settings, alert_id: str, decision: str = "approve", hours: int = 24) -> bool:
    """Record an explicit human decision; approve only moves text to local outbox."""
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not row:
            return False
        if decision == "approve":
            conn.execute("UPDATE alerts SET status='approved' WHERE id=?", (alert_id,))
            folder = settings.state_dir / "outbox"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"approved-{alert_id}.txt").write_text(row["draft_message"] + "\n\nNot sent. Copy and send manually if appropriate.\n", encoding="utf-8")
        elif decision == "ignore":
            conn.execute("UPDATE alerts SET status='ignored' WHERE id=?", (alert_id,))
        elif decision == "snooze":
            snoozed = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
            conn.execute("UPDATE alerts SET status='snoozed',snoozed_until=? WHERE id=?", (snoozed, alert_id))
        else:
            raise ValueError("decision must be approve, ignore, or snooze")
        # Store explicit user feedback for Rung 1.5; production UI supplies a feature snapshot.
        tx = conn.execute("SELECT * FROM transactions WHERE id=?", (row["transaction_id"],)).fetchone()
        features = decision_features(dict(tx) if tx else {"amount": row["amount"], "date": row["tx_date"], "category": "unknown"}, {})
        conn.execute("INSERT INTO human_decisions(alert_id,decision,features_json) VALUES(?,?,?)", (alert_id, decision, json.dumps(features, sort_keys=True)))
        conn.commit()
        return True
