"""The Sloth Ladder. Each rung exists to avoid waking the next one.
 Rung 0   plain rules            -> settles most rows
 Rung 1   TabPFN / robust interval -> inside the interval = boring
 Rung 1.5 'does this person care?' -> learned from your decisions (skipped on cold start)
 Rung 2   Gemma (local)            -> wakes only for surprises
 Rung 3   you                      -> pinged only for severity 'act'; nothing is ever sent automatically
`run_ladder` is pure: no database writes, so the canary and benchmarks can reuse it."""
from __future__ import annotations
from dataclasses import dataclass
from .config import Config
from .forecast import forecast, detect_creep
from .ingest import Tx
from .normalize import detect_recurring
from .rules import apply_rules, build_index
from .trace import NullTracer


@dataclass
class RowResult:
    key: str
    date: str
    merchant: str
    amount: float
    rung: str          # "0" | "1" | "1.5" | "2" | "3"
    status: str        # slept | watch | surprise
    reason: str
    lo: float | None = None
    hi: float | None = None
    method: str = ""
    kind: str = ""


def _surprise(tx: Tx, kind: str, note: str, prof, f=None, dev: float = 0.0) -> dict:
    cat = tx.category if tx.category != "other" or prof is None else "subscription"
    return {"key": tx.key, "merchant": tx.merchant, "date": str(tx.date), "amount": tx.amount, "kind": kind,
            "note": note, "deviation": dev, "category": cat, "tx": tx,
            "expected": f.expected if f else tx.amount, "lo": f.lo if f else tx.amount,
            "hi": f.hi if f else tx.amount, "method": f.method if f else "rule",
            "recurrence": prof.cadence if prof else "none"}


def run_ladder(history: list[Tx], current: list[Tx], cfg: Config, care=None, explainer=None,
               tracer=None, repeat_fn=None) -> dict:
    tracer = tracer or NullTracer()
    current = sorted(current, key=lambda t: t.seq)
    pool = sorted(history + current, key=lambda t: t.seq)
    by_m = build_index(pool)
    results: dict[str, RowResult] = {}
    surprises: list[dict] = []
    methods: dict[str, int] = {}

    # ---- Rung 0 -------------------------------------------------------------------------------
    with tracer.span("rung0_rules", rung=0, rows_in=len(current)) as sp:
        profiles = detect_recurring(history, cfg)
        hist_m = {t.merchant for t in history}
        need_r1: list[Tx] = []
        for tx in current:
            o = apply_rules(tx, by_m, profiles, hist_m, cfg)
            if o.settled:
                results[tx.key] = RowResult(tx.key, str(tx.date), tx.merchant, tx.amount, "0", "slept", o.reason)
            elif o.flag:
                surprises.append(_surprise(tx, o.flag, o.note, profiles.get(tx.merchant)))
            else:
                need_r1.append(tx)
        sp.set(settled=sum(1 for r in results.values() if r.rung == "0"), escalated=len(need_r1) + len(surprises),
               woke=bool(need_r1 or surprises), model="rules")

    # ---- Rung 1 -------------------------------------------------------------------------------
    with tracer.span("rung1_forecast", rung=1, rows_in=len(need_r1)) as sp:
        for tx in need_r1:
            prior = sorted([o for o in by_m[tx.merchant] if o.seq < tx.seq], key=lambda o: (o.date, o.seq))
            dates, amts = [o.date for o in prior], [o.amount for o in prior]
            prof = profiles[tx.merchant]
            f = forecast(dates, amts, tx.date, cfg)
            methods[f.method] = methods.get(f.method, 0) + 1
            creep = detect_creep(amts + [tx.amount], cfg)
            if creep is not None:
                surprises.append(_surprise(tx, "creeping_increase", f"{creep:.0f}% total over last {cfg.creep_len} charges",
                                           prof, f, creep))
            elif f.lo <= tx.amount <= f.hi:
                results[tx.key] = RowResult(tx.key, str(tx.date), tx.merchant, tx.amount, "1", "slept",
                                            "within_interval", round(f.lo, 2), round(f.hi, 2), f.method)
            else:
                surprises.append(_surprise(tx, "outside_interval", f"expected {f.expected:.2f}", prof, f,
                                           (tx.amount / f.expected - 1) * 100 if f.expected else 0.0))
        sp.set(settled=sum(1 for r in results.values() if r.rung == "1"), surprises=len(surprises), methods=methods,
               woke=bool(surprises), model=",".join(methods) or "none")

    # ---- Rung 1.5 -----------------------------------------------------------------------------
    suppressed, passing, care_skipped, care_method = [], [], False, "none"
    with tracer.span("rung1_5_care", rung=1.5, rows_in=len(surprises)) as sp:
        for s in surprises:
            s["features"] = {"kind": s["kind"], "category": s["category"], "deviation_pct": round(s["deviation"], 1),
                             "recurrence": s["recurrence"], "amount": s["amount"],
                             "day_of_month": int(s["date"][8:10]),
                             "repeat_count": repeat_fn(s["merchant"], s["kind"]) if repeat_fn else 0}
            p = care.predict(s["features"]) if (care is not None and cfg.learning) else None
            if p is None:
                care_skipped = True
                passing.append(s)
            elif p < cfg.care_threshold and s["amount"] < cfg.never_suppress_amount:
                s["care_p"] = round(p, 3)
                suppressed.append(s)
            else:
                s["care_p"] = round(p, 3)
                passing.append(s)
        care_method = getattr(care, "method", "none") if care is not None else "none"
        sp.set(suppressed=len(suppressed), skipped=care_skipped and not suppressed, method=care_method,
               woke=bool(passing), model=care_method)
    for s in suppressed:
        results[s["key"]] = RowResult(s["key"], s["date"], s["merchant"], s["amount"], "1.5", "watch",
                                      f"care_p={s['care_p']}", kind=s["kind"])

    # ---- Rung 2 / 3 ---------------------------------------------------------------------------
    wakeups = tin = tout = 0
    final: list[dict] = []
    with tracer.span("rung2_explain", rung=2, rows_in=len(passing)) as sp:
        for s in passing:
            ex = None
            if explainer is not None:
                ex = explainer.explain({k: v for k, v in s.items() if k != "tx"})
                wakeups += 1
                tin, tout = tin + ex.tokens_in, tout + ex.tokens_out
            s["explanation"] = ex
            if ex is not None and ex.severity == "ignore":
                results[s["key"]] = RowResult(s["key"], s["date"], s["merchant"], s["amount"], "2", "watch",
                                              "gemma_ignore", kind=s["kind"])
            else:
                rung = "3" if (ex is not None and ex.severity == "act") else "2"
                results[s["key"]] = RowResult(s["key"], s["date"], s["merchant"], s["amount"], rung, "surprise",
                                              s["kind"], kind=s["kind"])
                final.append(s)
        sp.set(woke=wakeups > 0, wakeups=wakeups, tokens_in=tin, tokens_out=tout, estimated_tokens=True,
               model=getattr(explainer, "cfg", cfg).ollama_model if explainer else "none")
    pings = sum(1 for s in final if s["explanation"] and s["explanation"].severity == "act")
    with tracer.span("rung3_human", rung=3, pings=pings) as sp:
        sp.set(woke=pings > 0, model="human")

    ordered = [results[t.key] for t in current if t.key in results]
    counts = {"rows": len(current),
              "rung0": sum(r.rung == "0" for r in ordered), "rung1": sum(r.rung == "1" for r in ordered),
              "surprises_total": len(surprises), "suppressed": len(suppressed),
              "gemma_wakeups": wakeups, "ignored_by_gemma": sum(1 for r in ordered if r.reason == "gemma_ignore"),
              "alerts": len(final), "human_pings": pings, "tokens_in": tin, "tokens_out": tout}
    return {"results": ordered, "surprises_all": surprises, "alerts": final, "suppressed": suppressed,
            "counts": counts, "methods": methods, "profiles": profiles,
            "care_skipped": care_skipped and care is not None and cfg.learning, "care_method": care_method}
