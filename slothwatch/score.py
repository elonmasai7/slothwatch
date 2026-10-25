"""Sloth Score: how many times each layer stayed asleep. Windows are measured in data time (run end dates)."""
from __future__ import annotations
from datetime import date, timedelta


def summary(con, days: int = 30) -> dict:
    last = con.execute("SELECT MAX(end) m FROM runs").fetchone()["m"]
    since = str(date.fromisoformat(last) - timedelta(days=days)) if last else "0000-00-00"
    r = con.execute("SELECT COUNT(*) runs, COALESCE(SUM(s.rows),0) rows, COALESCE(SUM(s.rung0),0) rung0, "
                    "COALESCE(SUM(s.rung1),0) rung1, COALESCE(SUM(s.suppressed),0) suppressed, "
                    "COALESCE(SUM(s.gemma),0) gemma, COALESCE(SUM(s.pings),0) pings "
                    "FROM score s JOIN runs r ON r.id=s.run_id WHERE r.end > ?", (since,)).fetchone()
    d = dict(r)
    d["asleep_pct"] = 100.0 * (d["rows"] - d["gemma"]) / d["rows"] if d["rows"] else 100.0
    return d


def history(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT s.run_id, s.rows, s.rung0, s.rung1, s.suppressed, s.gemma, s.pings, r.start, r.end "
        "FROM score s JOIN runs r ON r.id=s.run_id ORDER BY s.run_id")]
