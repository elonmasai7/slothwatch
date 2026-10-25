"""Local dashboard: stdlib http.server + one static page. No build step, no CDNs, works offline."""
from __future__ import annotations
import json, threading, webbrowser
import tempfile
from email import policy
from email.parser import BytesParser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import asdict
from . import score as scoremod
from .canary import run_canary
from .config import Config
from .db import connect
from .receipts import sample_slept, verify
from .runner import Engine

PAGE = (Path(__file__).parent / "dashboard.html")


def make_handler(cfg: Config, demo: bool):
    eng = Engine(cfg)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self._send(PAGE.read_bytes(), ctype="text/html; charset=utf-8")
            con = connect(cfg)
            try:
                if path == "/api/today":
                    r = con.execute("SELECT summary_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()
                    pend = [dict(x) for x in con.execute("SELECT * FROM alerts WHERE status='pending' AND severity='act'")]
                    return self._send({"demo": demo, "summary": json.loads(r["summary_json"]) if r else None, "awake": bool(pend)})
                if path == "/api/ladder":
                    h = scoremod.history(con)
                    return self._send({"latest": h[-1] if h else None, "totals": scoremod.summary(con, 30)})
                if path == "/api/recurring":
                    out = []
                    for r in con.execute("SELECT * FROM profiles ORDER BY merchant"):
                        d = dict(r); d["series"] = json.loads(d.pop("series_json")); out.append(d)
                    return self._send(out)
                if path == "/api/alerts":
                    return self._send([dict(x) for x in con.execute(
                        "SELECT * FROM alerts ORDER BY (status='pending') DESC, id DESC LIMIT 200")])
                if path == "/api/score":
                    return self._send({"history": scoremod.history(con), "totals": scoremod.summary(con, 30)})
                if path == "/api/receipts":
                    rows = [{"id": r["id"], "date": r["date"], "hash": r["hash"][:12], "rows": json.loads(r["body_json"])["rows_checked"],
                             "surprises": json.loads(r["body_json"])["surprises"]} for r in con.execute("SELECT * FROM receipts ORDER BY id DESC LIMIT 60")]
                    return self._send({"receipts": rows, "verify": verify(con, cfg),
                                       "canary": [dict(x) for x in con.execute("SELECT * FROM canary ORDER BY id DESC LIMIT 20")]})
                if path == "/api/sample":
                    return self._send(sample_slept(con, 3))
                if path == "/api/benchmark":
                    p = Path("benchmarks/results/benchmark.json")
                    return self._send(json.loads(p.read_text()) if p.exists() else [])
                if path == "/api/settings":
                    d = asdict(cfg); d["sentry_dsn"] = "set" if d["sentry_dsn"] else ""
                    return self._send(d)
                return self._send({"error": "not found"}, 404)
            finally:
                con.close()

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/import":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > 10 * 1024 * 1024:
                        raise ValueError("Choose a file smaller than 10 MB.")
                    content_type = self.headers.get("Content-Type", "")
                    if not content_type.lower().startswith("multipart/form-data;"):
                        raise ValueError("Choose a CSV or PDF file to import.")
                    message = BytesParser(policy=policy.default).parsebytes(
                        b"Content-Type: " + content_type.encode("ascii", "strict") + b"\r\nMIME-Version: 1.0\r\n\r\n" + self.rfile.read(size))
                    upload = next((part for part in message.iter_parts()
                                   if part.get_content_disposition() == "form-data" and part.get_filename()), None)
                    if upload is None:
                        raise ValueError("Choose a CSV or PDF file to import.")
                    filename = Path(upload.get_filename()).name
                    data = upload.get_payload(decode=True) or b""
                    if not data:
                        raise ValueError("That file is empty.")
                    from .ingest import read_csv_text, read_pdf
                    if filename.lower().endswith(".csv"):
                        rows, info = read_csv_text(data.decode("utf-8-sig", errors="replace"), cfg)
                    elif filename.lower().endswith(".pdf"):
                        rows, info = read_pdf(data, cfg)
                    else:
                        raise ValueError("Choose a .csv or .pdf file.")
                    if not rows:
                        raise ValueError("No spending transactions were found in that file.")
                    if demo:
                        # Keep each public demo upload separate and discard its state after analysis.
                        from dataclasses import replace
                        with tempfile.TemporaryDirectory(prefix="slothwatch-demo-upload-") as scratch:
                            upload_cfg = replace(cfg, data_dir=scratch, desktop_notify=False, sentry_dsn="")
                            result = Engine(upload_cfg).run_real(rows)
                    else:
                        result = eng.run_real(rows)
                    return self._send({"ok": True, "filename": filename, "rows": len(rows), "details": info,
                                       "message": result.get("message", "Import complete."),
                                       "summary": result}, 200)
                except (ValueError, UnicodeError) as exc:
                    return self._send({"error": str(exc)}, 400)
                except Exception:
                    return self._send({"error": "Import failed. Check the file format and try again."}, 400)
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            if path == "/api/decide" and data.get("decision") in ("approve", "ignore", "snooze"):
                return self._send(eng.decide(int(data["id"]), data["decision"]))
            if path == "/api/ignore-merchant" and data.get("merchant"):
                with open(cfg.path("ignore.txt"), "a") as f:
                    f.write(data["merchant"].strip().upper() + "\n")
                return self._send({"ok": True, "note": "applies from the next run"})
            if path == "/api/canary":
                return self._send({"ok": True, "note": "run `slothwatch canary <csv>` to plant and check a canary on your data"})
            return self._send({"error": "bad request"}, 400)

    return H


def serve(cfg: Config, port: int = 8765, open_browser: bool = True, demo: bool = False,
          host: str = "127.0.0.1"):
    srv = ThreadingHTTPServer((host, port), make_handler(cfg, demo))
    url = f"http://127.0.0.1:{port}/"
    print(f"SlothWatch {'DEMO ' if demo else ''}dashboard listening on {host}:{port}. Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
