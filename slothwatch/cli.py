"""slothwatch CLI. `slothwatch doctor` first."""
from __future__ import annotations
import argparse, json, shutil, sys, time
from datetime import date
from pathlib import Path
from . import __version__
from .config import load_config
from .db import connect


def _cfg(a, default_dir=".slothwatch"):
    return load_config(a.config, data_dir=getattr(a, "data_dir", None) or default_dir)


def _engine(cfg):
    from .runner import Engine
    return Engine(cfg)


def cmd_run(a):
    from .ingest import load_path
    from .notify import deliver
    cfg = _cfg(a)
    res = _engine(cfg).run_real(load_path(a.path, cfg))
    deliver(res["message"], cfg, res.get("pings", 0))
    return 0


def cmd_watch(a):
    from .ingest import load_path
    from .notify import deliver
    cfg = _cfg(a)
    p, seen = Path(a.path), None
    print(f"Watching {p} every {a.interval}s. Ctrl-C to stop. (A sloth would simply wait.)")
    while True:
        files = [p] if p.is_file() else sorted(p.glob("*.csv"))
        sig = tuple((f.name, f.stat().st_mtime) for f in files)
        if sig != seen and files:
            seen = sig
            res = _engine(cfg).run_real(load_path(p, cfg))
            deliver(res["message"], cfg, res.get("pings", 0))
        time.sleep(a.interval)


def cmd_status(a):
    cfg = _cfg(a)
    con = connect(cfg)
    r = con.execute("SELECT summary_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    pend = con.execute("SELECT COUNT(*) c FROM alerts WHERE status='pending'").fetchone()["c"]
    print(json.loads(r["summary_json"])["message"] if r else "No runs yet. Try `slothwatch run <csv>`.")
    print(f"Pending alerts: {pend}")
    for al in con.execute("SELECT id,severity,merchant,date,amount,verdict FROM alerts WHERE status='pending' ORDER BY id"):
        print(f"  #{al['id']} [{al['severity']}] {al['merchant']} {al['date']} {al['amount']:.2f}: {al['verdict']}")
    return 0


def cmd_score(a):
    from .score import summary
    cfg = _cfg(a)
    s = summary(connect(cfg), a.days)
    print(f"Sloth Score, last {a.days} days over {s['runs']} runs:")
    print(f"  rows checked: {s['rows']}\n  settled by rules (rung 0 asleep-through): {s['rung0']}\n  settled by forecast (rung 1): {s['rung1']}")
    print(f"  hushed by learning (rung 1.5): {s['suppressed']}\n  Gemma wake-ups: {s['gemma']}  | naive baseline would have been: {s['rows']}\n  human pings: {s['pings']}")
    print(f"  => Gemma stayed asleep for {s['rows'] - s['gemma']} of {s['rows']} rows ({s['asleep_pct']:.1f}%)")
    return 0


def _decide(a, decision):
    res = _engine(_cfg(a)).decide(a.id, decision)
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


def cmd_verify(a):
    from .receipts import verify
    r = verify(connect(_cfg(a)), _cfg(a))
    print(("OK" if r["ok"] else "FAILED") + f": {r['checked']} receipts checked. Head hash: {r['head'][:16]}…")
    for e in r["errors"]:
        print("  -", e)
    return 0 if r["ok"] else 1


def cmd_receipt(a):
    from .receipts import sample_slept
    cfg = _cfg(a)
    con = connect(cfg)
    r = con.execute("SELECT body_json FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        print("No receipts yet."); return 1
    b = json.loads(r["body_json"])
    print(f"Receipt #{b['n']} ({b['date']}): {b['rows_checked']} rows, rung0 settled {b['settled_by_rung']['0']}, "
          f"rung1 settled {b['settled_by_rung']['1']}, surprises {b['surprises']}, hushed {b['suppressed_by_learning']}, "
          f"model {b['model']}, forecast methods {b['methods']}")
    if a.sample:
        print(f"Spot-check: {a.sample} random rows that were left alone:")
        for x in sample_slept(con, a.sample):
            iv = f", interval {x['lo']:.2f}–{x['hi']:.2f} ({x['method']})" if x.get("lo") is not None else ""
            print(f"  {x['date']} {x['merchant']} {x['amount']:.2f} -> settled by rung {x['rung']} ({x['reason']}{iv})")
    return 0


def cmd_canary(a):
    from .canary import run_canary
    from .ingest import load_path
    from .normalize import annotate
    from datetime import timedelta
    cfg = _cfg(a)
    rows = annotate(load_path(a.path, cfg))
    last = max(t.date for t in rows)
    start = last - timedelta(days=cfg.first_window_days - 1)
    r = run_canary([t for t in rows if t.date < start], [t for t in rows if t.date >= start], cfg, connect(cfg))
    print(f"Canary: {'CAUGHT, the sloth is awake' if r['caught'] else 'MISSED' if r['caught'] is False else 'n/a'} ({r['detail']})")
    return 0 if r["caught"] is not False else 2


def cmd_redact(a):
    from .redact import redact_csv, redact_export
    cfg = _cfg(a)
    if a.what == "csv":
        print(f"Wrote {redact_csv(a.src, a.out, cfg)}")
    else:
        print(f"Wrote {redact_export(a.out, cfg)}")
    return 0


def cmd_doctor(a):
    from .forecast import tabpfn_available
    from .llm import Ollama
    cfg = _cfg(a)
    ok = lambda b: "OK  " if b else "MISS"
    o = Ollama(cfg)
    tp = tabpfn_available()
    try:
        import temporalio; tm = True
    except Exception:
        tm = False
    try:
        import sentry_sdk; sn = True
    except Exception:
        sn = False
    print(f"SlothWatch {__version__} doctor")
    print(f"[{ok(o.available())}] Ollama + model '{cfg.ollama_model}' at {cfg.ollama_url}  (else: templated verdicts, labelled as such)")
    print(f"[{ok(tp)}] TabPFN importable  (else: robust median+MAD intervals; accept the license at platform.priorlabs.ai first)")
    print(f"[{ok(tm)}] temporalio installed  (else: in-process orchestration; run `pip install temporalio` + `temporal server start-dev`)")
    print(f"[{ok(bool(cfg.sentry_dsn) and sn)}] Sentry configured  (else: traces go to {cfg.data_dir}/traces/*.jsonl only)")
    tuned = Path(cfg.data_dir, "tuned_model.txt")
    print(f"[{ok(tuned.exists())}] tuned Rung-2 model configured  (else: stock Gemma via Ollama)")
    print(f"data dir: {Path(cfg.data_dir).resolve()}")
    return 0


def cmd_dashboard(a):
    from .dashboard import serve
    serve(_cfg(a, a.default_dir), a.port, open_browser=not a.no_open)


def cmd_privacy(a):
    from .privacy import guarded
    from .demo.cli import privacy_workload
    _, attempts = guarded(privacy_workload)
    if attempts:
        print("FAIL: outbound connection attempted:", sorted(set(attempts))); return 1
    print("OK: the full pipeline ran with all non-local network access blocked. 0 external connection attempts.")
    return 0


def cmd_bench(a):
    from .bench import run_all
    run_all(a.out)
    print(Path(a.out, "report.md").read_text())
    return 0


def cmd_tune(a):
    from .tuning import cli as t
    return t.main(a)


def cmd_worker(a):
    from .workflows import run_worker
    import asyncio
    asyncio.run(run_worker(_cfg(a), a.address))


def cmd_schedule(a):
    from .workflows import create_schedule
    import asyncio
    asyncio.run(create_schedule(_cfg(a), a.path, a.address, a.hour))


def build_parser():
    p = argparse.ArgumentParser("slothwatch", description="A local agent whose best output is silence.")
    p.add_argument("--config"); p.add_argument("--data-dir", dest="data_dir")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run"); s.add_argument("path"); s.set_defaults(f=cmd_run)
    s = sub.add_parser("watch"); s.add_argument("path"); s.add_argument("--interval", type=int, default=30); s.set_defaults(f=cmd_watch)
    sub.add_parser("status").set_defaults(f=cmd_status)
    s = sub.add_parser("score"); s.add_argument("--days", type=int, default=30); s.set_defaults(f=cmd_score)
    for n in ("approve", "ignore", "snooze"):
        s = sub.add_parser(n); s.add_argument("id", type=int); s.set_defaults(f=lambda a, n=n: _decide(a, n))
    sub.add_parser("verify").set_defaults(f=cmd_verify)
    s = sub.add_parser("receipt"); s.add_argument("--sample", type=int, default=0); s.set_defaults(f=cmd_receipt)
    s = sub.add_parser("canary"); s.add_argument("path"); s.set_defaults(f=cmd_canary)
    s = sub.add_parser("redact"); s.add_argument("what", choices=["csv", "export"]); s.add_argument("--src"); s.add_argument("--out", required=True); s.set_defaults(f=cmd_redact)
    sub.add_parser("doctor").set_defaults(f=cmd_doctor)
    s = sub.add_parser("dashboard"); s.add_argument("--port", type=int, default=8765); s.add_argument("--no-open", action="store_true"); s.set_defaults(f=cmd_dashboard, default_dir=".slothwatch")
    sub.add_parser("privacy-check").set_defaults(f=cmd_privacy)
    s = sub.add_parser("bench"); s.add_argument("--out", default="benchmarks/results"); s.set_defaults(f=cmd_bench)
    s = sub.add_parser("tune"); s.add_argument("action", choices=["gen", "eval", "train"]); s.add_argument("--model"); s.add_argument("--n", type=int, default=1200); s.set_defaults(f=cmd_tune)
    s = sub.add_parser("worker"); s.add_argument("--address", default="localhost:7233"); s.set_defaults(f=cmd_worker)
    s = sub.add_parser("schedule"); s.add_argument("path"); s.add_argument("--address", default="localhost:7233"); s.add_argument("--hour", type=int, default=2); s.set_defaults(f=cmd_schedule)
    s = sub.add_parser("demo", help="synthetic-data demo app"); s.add_argument("rest", nargs=argparse.REMAINDER); s.set_defaults(f=lambda a: __import__("slothwatch.demo.cli", fromlist=["main"]).main(a.rest))
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        sys.exit(a.f(a) or 0)
    except (ValueError, FileNotFoundError) as e:
        print(f"slothwatch: {e}", file=sys.stderr)
        sys.exit(2)
