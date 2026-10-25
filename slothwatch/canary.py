"""'Is the sloth actually awake?' Plant a hidden anomaly in a copy of the data and make sure the ladder
catches it. Pure: nothing is written to the real receipts or alerts."""
from __future__ import annotations
import copy
from datetime import datetime
from .ladder import run_ladder
from .normalize import detect_recurring


def run_canary(history, current, cfg, con=None) -> dict:
    prof = detect_recurring(history, cfg)
    if not prof:
        res = {"caught": None, "detail": "no recurring items to plant a canary on"}
    else:
        m = max(prof.values(), key=lambda p: (p.cadence == "monthly", p.n)).merchant
        last = max((t for t in history if t.merchant == m), key=lambda t: (t.date, t.seq))
        hist, cur = copy.deepcopy(history), copy.deepcopy(current)
        end = max([t.date for t in cur] + [last.date])
        base = max(t.seq for t in hist + cur) + 1
        planted = copy.copy(last)
        planted.date, planted.amount, planted.key, planted.seq = end, round(last.amount * 3, 2), "CANARY0000", base
        # the canary replaces the genuine charge for that merchant in this copy so it isn't a dupe test
        cur = [t for t in cur if t.merchant != m] + [planted]
        out = run_ladder(hist, cur, cfg, care=None, explainer=None)
        caught = any(s["key"] == "CANARY0000" for s in out["surprises_all"])
        res = {"caught": bool(caught), "detail": f"planted 3x charge on {m}"}
    if con is not None:
        con.execute("INSERT INTO canary(ts,caught,detail) VALUES(?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), -1 if res["caught"] is None else int(res["caught"]),
                     res["detail"]))
        con.commit()
    return res
