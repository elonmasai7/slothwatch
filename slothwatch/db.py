"""Local SQLite store. Everything lives under the (gitignored) data dir."""
from __future__ import annotations
import sqlite3
from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, ts TEXT, mode TEXT, window_key TEXT UNIQUE,
  start TEXT, end TEXT, summary_json TEXT);
CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY, date TEXT, body_json TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, run_id INTEGER, created TEXT, row_key TEXT, merchant TEXT,
  date TEXT, amount REAL, kind TEXT, deviation REAL, severity TEXT, verdict TEXT, action TEXT, draft TEXT,
  source TEXT, status TEXT DEFAULT 'pending', snooze_until TEXT, features_json TEXT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, ts TEXT, alert_id INTEGER, decision TEXT,
  label INTEGER, features_json TEXT);
CREATE TABLE IF NOT EXISTS score(run_id INTEGER PRIMARY KEY, ts TEXT, rows INTEGER, rung0 INTEGER, rung1 INTEGER,
  suppressed INTEGER, gemma INTEGER, pings INTEGER);
CREATE TABLE IF NOT EXISTS canary(id INTEGER PRIMARY KEY, ts TEXT, caught INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS profiles(merchant TEXT PRIMARY KEY, category TEXT, cadence TEXT, n INTEGER,
  last_amount REAL, expected REAL, lo REAL, hi REAL, method TEXT, next_date TEXT, series_json TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(cfg: Config) -> sqlite3.Connection:
    con = sqlite3.connect(cfg.path("slothwatch.db"), timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def kv_get(con, key, default=None):
    r = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def kv_set(con, key, value):
    con.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
    con.commit()
