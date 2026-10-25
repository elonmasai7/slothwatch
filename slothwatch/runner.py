"""Engine: runs the ladder over a date window and persists everything (alerts, receipt, score, profiles).
Runs are idempotent per window, so a Temporal retry or crash-resume can never produce a second alert."""
from __future__ import annotations
import hashlib, json
from datetime import date, datetime, timedelta
from .care import Care
from .config import Config, config_hash
from .db import connect, kv_get, kv_set
from .forecast import forecast
from .ingest import Tx
from .ladder import run_ladder
from .llm import Explainer
from .normalize import annotate
from .receipts import write_receipt
from .trace import Tracer


class Engine:
    def __init__(self, cfg: Config, tracer: Tracer | None = None, explainer: Explainer | None = None):
        self.cfg = cfg
        self.tracer = tracer or Tracer(cfg)
        self.explainer = explainer or Explainer(cfg, self.tracer)

    # ---------------------------------------------------------------- run
    def run(self, rows: list[Tx], start: date, end: date, mode: str = "real", refresh_profiles: bool = True,
            force: bool = False) -> dict:
        cfg = self.cfg
        rows = sorted(rows, key=lambda t: (t.date, t.seq, t.desc))
        for i, t in enumerate(rows):
            t.seq = i
        annotate(rows)
        hist = [t for t in rows if t.date < start]
        cur = [t for t in rows if start <= t.date <= end]
        wkey = hashlib.sha1(f"{start}:{end}:{config_hash(cfg)}:{mode}:{','.join(t.key for t in cur)}".encode()).hexdigest()
        con = connect(cfg)
        try:
            old = con.execute("SELECT summary_json FROM runs WHERE window_key=?", (wkey,)).fetchone()
            if old and not force:
                return {**json.loads(old["summary_json"]), "idempotent": True}

            decisions = [(json.loads(r["features_json"]), r["label"]) for r in
                         con.execute("SELECT features_json,label FROM decisions WHERE label IS NOT NULL")]
            care = Care(decisions, cfg)

            def repeat(m, k):
                return con.execute("SELECT COUNT(*) c FROM alerts WHERE merchant=? AND kind=?", (m, k)).fetchone()["c"]

            self.tracer.new_trace()
            with self.tracer.span("run", mode=mode, window=f"{start}..{end}", rows=len(cur)):
                out = run_ladder(hist, cur, cfg, care=care, explainer=self.explainer, tracer=self.tracer,
                                 repeat_fn=repeat)
            c = out["counts"]
            ts = datetime.now().isoformat(timespec="seconds")
            cur_run = con.execute("INSERT INTO runs(ts,mode,window_key,start,end,summary_json) VALUES(?,?,?,?,?,?)",
                                  (ts, mode, wkey, str(start), str(end), "{}"))
            run_id = cur_run.lastrowid

            alert_ids, act_ids = [], []
            for s in out["alerts"]:
                ex = s["explanation"]
                cur_a = con.execute(
                    "INSERT INTO alerts(run_id,created,row_key,merchant,date,amount,kind,deviation,severity,verdict,"
                    "action,draft,source,features_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, ts, s["key"], s["merchant"], s["date"], s["amount"], s["kind"], s["deviation"],
                     ex.severity, ex.verdict, ex.action, ex.draft, ex.source, json.dumps(s["features"])))
                alert_ids.append(cur_a.lastrowid)
                if ex.severity == "act":
                    act_ids.append(cur_a.lastrowid)

            settled = [{"key": r.key, "merchant": r.merchant, "amount": r.amount, "date": r.date, "rung": r.rung,
                        "reason": r.reason, "lo": r.lo, "hi": r.hi, "method": r.method}
                       for r in out["results"] if r.status == "slept"]
            logged = [{"key": r.key, "merchant": r.merchant, "amount": r.amount, "rung": r.rung, "reason": r.reason}
                      for r in out["results"] if r.status == "watch"]
            rc = write_receipt(con, cfg, {
                "date": str(end), "window": [str(start), str(end)], "rows_checked": c["rows"],
                "settled_by_rung": {"0": c["rung0"], "1": c["rung1"]}, "surprises": c["surprises_total"],
                "suppressed_by_learning": c["suppressed"], "gemma_wakeups": c["gemma_wakeups"],
                "human_pings": c["human_pings"], "methods": out["methods"], "care_skipped": out["care_skipped"],
                "care_method": out["care_method"], "model": cfg.ollama_model if self.explainer.ollama.available() else "template-fallback",
                "config_hash": config_hash(cfg), "settled_rows": settled, "logged_watch": logged})
            con.execute("INSERT INTO score VALUES(?,?,?,?,?,?,?,?)",
                        (run_id, ts, c["rows"], c["rung0"], c["rung1"], c["suppressed"], c["gemma_wakeups"],
                         c["human_pings"]))

            if refresh_profiles:
                self._refresh_profiles(con, rows, out["profiles"], end)

            msg = self._message(out, c, rc["id"], act_ids, con)
            summary = {"run_id": run_id, "receipt_id": rc["id"], "receipt_hash": rc["hash"], "message": msg,
                       "counts": c, "alert_ids": alert_ids, "act_alert_ids": act_ids, "pings": c["human_pings"],
                       "quiet": c["human_pings"] == 0, "methods": out["methods"], "care_skipped": out["care_skipped"],
                       "window": [str(start), str(end)],
                       "surprise_merchants": sorted({s["merchant"] for s in out["surprises_all"]}),
                       "surprise_rows": [[s["merchant"], s["date"], s["kind"]] for s in out["surprises_all"]],
                       "idempotent": False}
            con.execute("UPDATE runs SET summary_json=? WHERE id=?", (json.dumps(summary), run_id))
            con.commit()
            return summary
        finally:
            con.close()

    def _message(self, out, c, rid, act_ids, con) -> str:
        tail = f"{c['rows']} rows checked, {c['surprises_total']} surprise{'s' if c['surprises_total'] != 1 else ''}. Receipt #{rid}"
        if c["human_pings"] == 0:
            logged = c["surprises_total"]
            if logged == 0:
                return f"Nothing needs you. Go back to sleep. ({tail})"
            return (f"Nothing needs you right now. ({c['rows']} rows checked, {logged} minor item"
                    f"{'s' if logged != 1 else ''} logged, {c['suppressed']} hushed by learning. Receipt #{rid})")
        lines = [f"{c['human_pings']} thing{'s' if c['human_pings'] != 1 else ''} need{'s' if c['human_pings'] == 1 else ''} you ({tail}):"]
        for s in out["alerts"]:
            if s["explanation"].severity == "act":
                v = s["explanation"].verdict
                lines.append(f" - {v}" if s["merchant"].split()[0].lower() in v.lower() else f" - [{s['merchant']}] {v}")
        return "\n".join(lines)

    def _refresh_profiles(self, con, rows, profiles, end: date):
        for m, p in profiles.items():
            series = sorted([t for t in rows if t.merchant == m and t.date <= end], key=lambda t: (t.date, t.seq))
            dates, amts = [t.date for t in series], [t.amount for t in series]
            nxt = dates[-1] + timedelta(days=p.period)
            f = forecast(dates, amts, nxt, self.cfg)
            con.execute("INSERT OR REPLACE INTO profiles VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (m, p.category, p.cadence, len(amts), amts[-1], f.expected, f.lo, f.hi, f.method, str(nxt),
                         json.dumps(amts[-12:]), str(end)))

    # ---------------------------------------------------------------- real-data convenience
    def run_real(self, rows: list[Tx]) -> dict:
        con = connect(self.cfg)
        wm = kv_get(con, "watermark")
        con.close()
        if not rows:
            raise ValueError("No transactions found.")
        last = max(t.date for t in rows)
        start = date.fromisoformat(wm) + timedelta(days=1) if wm else last - timedelta(days=self.cfg.first_window_days - 1)
        if start > last:
            return {"message": "No new transactions. Sleeping.", "quiet": True, "pings": 0, "no_new_data": True}
        res = self.run(rows, start, last, mode="real")
        con = connect(self.cfg)
        kv_set(con, "watermark", str(last))
        con.close()
        return res

    # ---------------------------------------------------------------- human decisions (Rung 3)
    def decide(self, alert_id: int, decision: str, today: date | None = None) -> dict:
        assert decision in ("approve", "ignore", "snooze")
        con = connect(self.cfg)
        try:
            a = con.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if not a:
                return {"ok": False, "error": f"no alert #{alert_id}"}
            if a["status"] not in ("pending", "snoozed"):
                return {"ok": False, "error": f"alert #{alert_id} already {a['status']}"}
            label = {"approve": 1, "ignore": 0, "snooze": None}[decision]
            status = {"approve": "approved", "ignore": "ignored", "snooze": "snoozed"}[decision]
            until = str((today or date.today()) + timedelta(days=7)) if decision == "snooze" else None
            con.execute("UPDATE alerts SET status=?, snooze_until=? WHERE id=?", (status, until, alert_id))
            con.execute("INSERT INTO decisions(ts,alert_id,decision,label,features_json) VALUES(?,?,?,?,?)",
                        (datetime.now().isoformat(timespec="seconds"), alert_id, decision, label, a["features_json"]))
            con.commit()
            path = None
            if decision == "approve":  # SlothWatch never sends anything. It only prepares the draft.
                p = self.cfg.path("outbox", "drafts", f"alert-{alert_id}.txt")
                p.write_text(f"To: {a['merchant']}\nRe: charge of {a['amount']:.2f} on {a['date']}\n\n{a['draft']}\n")
                path = str(p)
            return {"ok": True, "status": status, "draft_path": path}
        finally:
            con.close()
