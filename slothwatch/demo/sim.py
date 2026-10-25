"""12-week Laziness Curve simulation: a persona with consistent tastes makes real decisions,
once with learning ON and once OFF. Ground truth is known, so missed true anomalies are reported."""
from __future__ import annotations
import json, random, shutil, tempfile
from datetime import date, timedelta
from pathlib import Path
from ..config import Config
from ..ingest import Tx
from ..runner import Engine
from ..trace import NullTracer
from ..llm import Explainer

UTIL = [f"{r} {k}" for r in ("NORTH", "EAST", "SOUTH") for k in ("ELECTRIC", "WATER", "GAS", "INTERNET", "MOBILE", "WASTE")]
SUBS = [f"{a} {b}" for a, b in zip(
    ["STREAM", "TUNE", "CLOUD", "PAGE", "FIT", "READ", "GAME", "LEARN", "PHOTO", "NEWS", "MOVIE", "PODCAST",
     "CODE", "MAP", "NOTE", "MAIL", "VPN", "CHEF", "KIDS", "CRAFT", "BIKE", "YOGA"],
    ["BOX PLUS", "WAVE MUSIC", "VAULT", "FLIX NEWS", "HUB GYM", "LIST PREMIUM", "PASS", "LAB", "BOOK CLOUD", "DAILY NEWS",
     "NOW", "CAST PLUS", "KIT PREMIUM", "SCOUT", "KEEPER CLOUD", "DESK", "SHIELD", "BOX", "CLUB", "BOX STREAM",
     "SHARE", "FLOW"])]
START = date(2026, 9, 7)  # sim week 1 (a Monday)


def _persona_approves(kind, category, dev):
    if kind == "duplicate":
        return True
    if category == "utility":
        return dev >= 80
    return dev >= 15


def _world(seed):
    rng = random.Random(seed)
    items = []
    for n in UTIL:
        items.append({"name": n, "cat": "utility", "base": round(rng.uniform(18, 140), 2), "day": rng.randint(1, 28), "sd": 0.06})
    for n in SUBS:
        items.append({"name": n, "cat": "subscription", "base": round(rng.uniform(6, 60), 2), "day": rng.randint(1, 28), "sd": 0.0})
    return items


def build_rows(seed=3, weeks=12):
    rng = random.Random(seed)
    items = _world(seed)
    rows, truth, seqn = [], {}, 0
    # 12 months history
    for i in range(12):
        y, m = 2025 + (8 + i) // 12, (8 + i) % 12 + 1
        for it in items:
            amt = round(it["base"] * (1 + rng.gauss(0, it["sd"])), 2)
            rows.append(Tx(date(y, m, it["day"]), f'{it["name"]} {rng.randint(1000, 9999)}', amt, key=f"h{seqn}")); seqn += 1
    # 12 sim weeks: each charge due in the week may be perturbed
    for w in range(weeks):
        ws = START + timedelta(days=7 * w)
        for d in (ws + timedelta(days=k) for k in range(7)):
            for it in items:
                if it["day"] != d.day:
                    continue
                amt = round(it["base"] * (1 + rng.gauss(0, it["sd"])), 2)
                kind, true = None, False
                if rng.random() < 0.5:
                    kinds = ["util_fluct", "util_spike", "sub_small", "sub_big", "dup"]
                    wts = [4, 1, 4, 2, 2] if it["cat"] == "utility" else [0, 0, 4, 2, 2]
                    kind = rng.choices(kinds, wts)[0] if sum(wts) else None
                if kind == "util_fluct" and it["cat"] == "utility":
                    amt = round(amt * rng.uniform(1.30, 1.55), 2)
                elif kind == "util_spike" and it["cat"] == "utility":
                    amt = round(it["base"] * rng.uniform(2.0, 2.6), 2); true = True
                elif kind == "sub_small" and it["cat"] == "subscription":
                    amt = round(it["base"] * rng.uniform(1.06, 1.10), 2)
                elif kind == "sub_big" and it["cat"] == "subscription":
                    amt = round(it["base"] * rng.uniform(1.25, 1.6), 2); true = True
                rows.append(Tx(d, f'{it["name"]} {rng.randint(1000, 9999)}', amt, key=f"s{seqn}")); seqn += 1
                if true:
                    truth[(it["name"], str(d))] = kind
                if kind == "dup" and it["base"] >= 15:
                    d2 = d + timedelta(days=1)
                    rows.append(Tx(d2, f'{it["name"]} {rng.randint(1000, 9999)}', amt, key=f"s{seqn}")); seqn += 1
                    truth[(it["name"], str(d2))] = "dup"
    return rows, truth


def run_sim(learning: bool, seed=3, workdir=None, weeks=12):
    tmp = Path(workdir or tempfile.mkdtemp(prefix="sloth-sim-"))
    cfg = Config(data_dir=str(tmp), learning=learning, use_tabpfn=False, desktop_notify=False)
    eng = Engine(cfg, tracer=NullTracer(), explainer=Explainer(cfg))
    rows, truth = build_rows(seed, weeks)
    wk, missed = [], []
    from ..normalize import clean_merchant
    for w in range(weeks):
        ws = START + timedelta(days=7 * w)
        res = eng.run(rows, ws, ws + timedelta(days=6), mode="sim", refresh_profiles=False)
        con = __import__("sqlite3").connect(cfg.path("slothwatch.db")); con.row_factory = __import__("sqlite3").Row
        alerts = con.execute("SELECT * FROM alerts WHERE run_id=?", (res["run_id"],)).fetchall()
        week_truth = {k for k in truth if ws <= date.fromisoformat(k[1]) <= ws + timedelta(days=6)}
        surfaced = {(a["merchant"], a["date"]) for a in alerts}
        for k in week_truth:
            if (clean_merchant(k[0]), k[1]) not in surfaced:
                missed.append({"week": w + 1, "merchant": k[0], "date": k[1], "kind": truth[k]})
        con.close()
        for a in alerts:  # the persona decides on every surfaced alert, exactly like a human in the dashboard
            f = json.loads(a["features_json"])
            eng.decide(a["id"], "approve" if _persona_approves(a["kind"], f["category"], abs(a["deviation"])) else "ignore")
        c = res["counts"]
        wk.append({"week": w + 1, "wakeups": c["gemma_wakeups"], "pings": c["human_pings"],
                      "suppressed": c["suppressed"], "alerts": c["alerts"], "true_anomalies": len(week_truth),
                      "caught": len(week_truth) - sum(1 for m in missed if m["week"] == w + 1)})
    total_true = sum(x["true_anomalies"] for x in wk)
    return {"learning": learning, "weeks": wk, "total_wakeups": sum(x["wakeups"] for x in wk),
            "total_pings": sum(x["pings"] for x in wk), "true_total": total_true,
            "true_caught": sum(x["caught"] for x in wk), "missed": missed}


def run_both(seed=3, weeks=12) -> dict:
    return {"on": run_sim(True, seed, weeks=weeks), "off": run_sim(False, seed, weeks=weeks)}


def chart_svg(res) -> str:
    on, off = res["on"]["weeks"], res["off"]["weeks"]
    top = max(max(w["wakeups"] for w in off), 1)
    W, H, pad = 640, 300, 40
    def pts(ws):
        return " ".join(f"{pad + i * (W - 2 * pad) / max(len(ws) - 1, 1):.1f},{H - pad - w['wakeups'] / top * (H - 2 * pad):.1f}" for i, w in enumerate(ws))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="12">'
            f'<rect width="{W}" height="{H}" fill="white"/><text x="{pad}" y="20" font-size="14" font-weight="bold">'
            f'The Laziness Curve: Gemma wake-ups per week</text>'
            f'<line x1="{pad}" y1="{H-pad}" x2="{W-pad}" y2="{H-pad}" stroke="#888"/><line x1="{pad}" y1="{pad}" x2="{pad}" y2="{H-pad}" stroke="#888"/>'
            f'<polyline fill="none" stroke="#c0392b" stroke-width="2.5" points="{pts(off)}"/>'
            f'<polyline fill="none" stroke="#27ae60" stroke-width="2.5" points="{pts(on)}"/>'
            f'<text x="{W-210}" y="{pad+10}" fill="#c0392b">learning OFF</text><text x="{W-210}" y="{pad+28}" fill="#27ae60">learning ON</text>'
            f'<text x="{pad}" y="{H-10}">week 1</text><text x="{W-pad-45}" y="{H-10}">week {len(on)}</text></svg>')
