"""SQLite persistence for runs, alerts, decisions and audit receipts."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS transactions (
 id TEXT PRIMARY KEY, run_id TEXT, tx_date TEXT NOT NULL, description TEXT NOT NULL,
 merchant TEXT NOT NULL, merchant_id TEXT NOT NULL, amount REAL NOT NULL,
 account TEXT, category TEXT, source_file TEXT, is_anomaly INTEGER DEFAULT 0,
 outcome TEXT, settled_by TEXT, forecast_low REAL, forecast_high REAL, forecast_method TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tx_merchant_date ON transactions(merchant_id, tx_date);
CREATE TABLE IF NOT EXISTS recurring_profiles (
 merchant_id TEXT PRIMARY KEY, merchant TEXT NOT NULL, cadence TEXT NOT NULL,
 amount_median REAL NOT NULL, observation_count INTEGER NOT NULL, last_date TEXT NOT NULL,
 next_amount REAL, lower_bound REAL, upper_bound REAL, method TEXT NOT NULL,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
 mode TEXT NOT NULL, source TEXT, rows_checked INTEGER DEFAULT 0,
 settled_r0 INTEGER DEFAULT 0, settled_r1 INTEGER DEFAULT 0, settled_r15 INTEGER DEFAULT 0,
 gemma_wakeups INTEGER DEFAULT 0, human_pings INTEGER DEFAULT 0, alerts INTEGER DEFAULT 0,
 suppressed INTEGER DEFAULT 0, config_hash TEXT, status TEXT DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS alerts (
 id TEXT PRIMARY KEY, run_id INTEGER, transaction_id TEXT, merchant TEXT, amount REAL,
 tx_date TEXT, verdict TEXT, severity TEXT, suggested_action TEXT, draft_message TEXT,
 status TEXT DEFAULT 'pending', snoozed_until TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS row_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, transaction_id TEXT NOT NULL,
 rung TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT, method TEXT, details_json TEXT,
 FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS human_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id TEXT NOT NULL, decision TEXT NOT NULL,
 features_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS receipts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, receipt_date TEXT NOT NULL,
 rows_checked INTEGER NOT NULL, body_json TEXT NOT NULL, previous_hash TEXT, receipt_hash TEXT NOT NULL UNIQUE,
 config_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS canary_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
 caught INTEGER NOT NULL, method TEXT NOT NULL, notes TEXT
);
CREATE TABLE IF NOT EXISTS trace_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, event_json TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Create a SQLite connection and ensure schema exists."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
