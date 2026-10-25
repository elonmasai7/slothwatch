"""slothwatch-demo: synthetic data, scripted scenarios, fast-forward diary, guided tour."""
from __future__ import annotations
import argparse, json, shutil, sys, tempfile, time
from datetime import date, timedelta
from pathlib import Path
from ..config import load_config
from ..db import connect
from ..runner import Engine
from . import generator as g

DEMO_DIR = ".slothwatch-demo"


def _fresh(a) -> "Config":
    cfg = load_config(a.config if hasattr(a, "config") else None, data_dir=DEMO_DIR, desktop_notify=False)
    shutil.rmtree(cfg.data_dir, ignore_errors=True)
    return cfg


def _mode_banner(eng) -> str:
    live = eng.explainer.ollama.available()
    from ..forecast import tabpfn_available
    return (f"[mode] Gemma: {'LIVE via Ollama (' + eng.cfg.ollama_model + ')' if live else 'OFFLINE, templated verdicts (token counts estimated)'} | "
            f"Forecast: {'TabPFN' if tabpfn_available() and eng.cfg.use_tabpfn else 'robust median+MAD fallback'} | "
            f"Orchestration: in-process (Temporal worker not used in demo run)")


def run_scenario(name, cfg, quiet=False):
    from ..notify import deliver
    eng = Engine(cfg)
    if not quiet:
        print(_mode_banner(eng))
    res = eng.run(g.load_rows(name), g.SCEN_START, g.SCEN_END, mode="demo")
    if not quiet:
        deliver(res["message"], cfg, 0)
    return eng, res


def funnel(c) -> str:
    def bar(n, tot): return "█" * max(1 if n else 0, round(30 * n / max(tot, 1)))
    rows = c["rows"]
    lines = [f"  rows checked           {rows:>4} {bar(rows, rows)}",
             f"  settled by rules       {c['rung0']:>4} {bar(c['rung0'], rows)}   (rung 0 never woke anything)",
             f"  settled by forecast    {c['rung1']:>4} {bar(c['rung1'], rows)}   (inside the interval = boring)",
             f"  surprises              {c['surprises_total']:>4} {bar(c['surprises_total'], rows)}",
             f"  hushed by learning     {c['suppressed']:>4}",
             f"  Gemma woke             {c['gemma_wakeups']:>4} {bar(c['gemma_wakeups'], rows)}   (naive baseline: {rows})",
             f"  human pinged           {c['human_pings']:>4}"]
    return "\n".join(lines)


def cmd_run(a):
    cfg = _fresh(a)
    eng, res = run_scenario(a.scenario, cfg)
    print("\nLadder:\n" + funnel(res["counts"]))
    return 0


def cmd_fast(a):
    cfg = _fresh(a)
    eng = Engine(cfg)
    print(_mode_banner(eng))
    rows = g.load_rows(a.scenario)
    print(f"\nA sloth's diary: scenario '{a.scenario}', 30 days.\n")
    d, n = g.SCEN_START, 1
    while d <= g.SCEN_END:
        res = eng.run(rows, d, d, mode="fast", refresh_profiles=(d == g.SCEN_END))
        c = res["counts"]
        if res["pings"]:
            print(f"Day {n:>2}: ⚠ {res['pings']} thing{'s' if res['pings'] > 1 else ''} need{'' if res['pings'] > 1 else 's'} you:")
            for s in res["surprise_rows"]:
                if s[1] == str(d):
                    print(f"        {s[0]} ({s[2]})")
        elif c["surprises_total"]:
            print(f"Day {n:>2}: nothing needs you. ({c['surprises_total']} minor item logged)")
        else:
            print(f"Day {n:>2}: nothing.")
        time.sleep(a.pace)
        d, n = d + timedelta(days=1), n + 1
    from ..score import summary
    s = summary(connect(cfg), 30)
    print(f"\nSloth Score: Gemma stayed asleep for {s['rows'] - s['gemma']} of {s['rows']} rows; woke {s['gemma']}x; pinged you {s['pings']}x.")
    return 0


def _pause(a, text):
    print("\n" + "─" * 70 + f"\n  {text}\n" + "─" * 70)
    if a.pace < 0:
        input("  (press Enter) ")
    else:
        time.sleep(a.pace)


def cmd_tour(a):
    from ..bench import compare_scenario
    cfg = _fresh(a)
    _pause(a, "STEP 1/4  A clean month. The agent has nothing to say, and that is the product.")
    eng, res = run_scenario("clean-month", cfg)
    _pause(a, "STEP 2/4  Now inject anomalies: a streaming plan up 40%, a duplicate grocery charge, an electricity spike, a creeping insurance premium.")
    eng, res = run_scenario("everything-wrong", cfg)
    _pause(a, "STEP 3/4  The ladder: who woke up for what.")
    print(funnel(res["counts"]))
    _pause(a, "STEP 4/4  Proof: the trace of that run, the Silence Receipt, and the Sloth Score against a naive baseline.")
    tf = sorted(Path(cfg.data_dir, "traces").glob("*.jsonl"))[-1]
    last = [json.loads(l) for l in tf.read_text().splitlines()]
    tid = [x for x in last if x["span"] == "run"][-1]["trace"]
    for x in [y for y in last if y["trace"] == tid]:
        at = x["attrs"]
        print(f"  span {x['span']:<16} {x['duration_ms']:>8.1f} ms  woke={at.get('woke', '-')}  cost=${at.get('cost_usd', 0):.2f}")
    from ..receipts import verify
    v = verify(connect(cfg), cfg)
    print(f"\n  Receipt chain: {'verified' if v['ok'] else 'BROKEN'} ({v['checked']} receipts, head {v['head'][:12]}…)")
    r = compare_scenario("everything-wrong")
    print(f"  Sloth Score: {r['sloth_score']} of {r['rows']} rows never woke the model. Ladder woke it {r['ladder_gemma_wakeups']}x; naive baseline {r['baseline_llm_calls']}x.")
    print(f"  Mode: {r['mode']}")
    print("\n  Open the dashboard: slothwatch-demo serve")
    return 0


def cmd_sim(a):
    from .sim import run_both, chart_svg
    res = run_both(weeks=a.weeks)
    on, off = res["on"], res["off"]
    print("week | wake-ups OFF | wake-ups ON | hushed ON")
    for w_off, w_on in zip(off["weeks"], on["weeks"]):
        print(f"{w_off['week']:>4} | {w_off['wakeups']:>12} | {w_on['wakeups']:>11} | {w_on['suppressed']:>9}")
    print(f"\nTotal Gemma wake-ups: OFF {off['total_wakeups']}  ON {on['total_wakeups']}")
    print(f"True anomalies caught: OFF {off['true_caught']}/{off['true_total']}  ON {on['true_caught']}/{on['true_total']}")
    if on["missed"] or off["missed"]:
        print("Missed true anomalies (honest accounting):")
        for tag, r in (("OFF", off), ("ON", on)):
            for m in r["missed"]:
                print(f"  [{tag}] week {m['week']} {m['merchant']} {m['date']} ({m['kind']})")
    out = Path("benchmarks/results"); out.mkdir(parents=True, exist_ok=True)
    (out / "laziness_curve.json").write_text(json.dumps(res, indent=1))
    (out / "laziness_curve.svg").write_text(chart_svg(res))
    print(f"\nWrote {out}/laziness_curve.json and .svg")
    return 0


def cmd_gen(a):
    for p in g.write_csvs(a.scenario, a.out):
        print(p)
    return 0


def cmd_serve(a):
    from ..dashboard import serve
    serve(load_config(None, data_dir=DEMO_DIR, desktop_notify=False), a.port, open_browser=not a.no_open, demo=True)


def cmd_all(a):
    cfg = _fresh(a)
    run_scenario("clean-month", cfg)
    eng, res = run_scenario("everything-wrong", cfg)
    print("\nLadder:\n" + funnel(res["counts"]))
    from ..bench import run_all
    run_all()
    print("\nOpening dashboard…")
    cmd_serve(argparse.Namespace(port=a.port, no_open=a.no_open))


def privacy_workload():
    """Everything the product does, in a temp dir, with Ollama absent or local only."""
    tmp = tempfile.mkdtemp(prefix="sloth-privacy-")
    cfg = load_config(None, data_dir=tmp, desktop_notify=False, sentry_dsn="")
    cfg.sentry_dsn = ""
    run_scenario("everything-wrong", cfg, quiet=True)
    from ..redact import redact_export
    redact_export(Path(tmp) / "x.json", cfg)


def main(argv=None):
    p = argparse.ArgumentParser("slothwatch-demo")
    p.add_argument("--config")
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = list(g.SCENARIOS)
    s = sub.add_parser("run"); s.add_argument("scenario", choices=sc); s.set_defaults(f=cmd_run)
    s = sub.add_parser("fast"); s.add_argument("scenario", choices=sc, nargs="?", default="everything-wrong"); s.add_argument("--pace", type=float, default=2.0, help="seconds per simulated day (2.0 ≈ 60s)"); s.set_defaults(f=cmd_fast)
    s = sub.add_parser("tour"); s.add_argument("--pace", type=float, default=3.0, help="seconds between steps; negative = wait for Enter"); s.set_defaults(f=cmd_tour)
    s = sub.add_parser("sim", help="Laziness Curve simulation"); s.add_argument("--weeks", type=int, default=12); s.set_defaults(f=cmd_sim)
    s = sub.add_parser("gen"); s.add_argument("scenario", choices=sc); s.add_argument("out"); s.set_defaults(f=cmd_gen)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8766); s.add_argument("--no-open", action="store_true"); s.set_defaults(f=cmd_serve)
    s = sub.add_parser("all"); s.add_argument("--port", type=int, default=8766); s.add_argument("--no-open", action="store_true"); s.set_defaults(f=cmd_all)
    a = p.parse_args(argv)
    sys.exit(a.f(a) or 0)


if __name__ == "__main__":
    main()
