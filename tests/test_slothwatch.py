import json, os, shutil, socket, sqlite3, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path
from slothwatch.config import Config
from slothwatch.db import connect
from slothwatch.demo import generator as g
from slothwatch.ingest import parse_amount, read_csv, load_path
from slothwatch.llm import Explainer, validate
from slothwatch.normalize import clean_merchant
from slothwatch.privacy import guarded, ExternalConnection
from slothwatch.receipts import verify
from slothwatch.runner import Engine
from slothwatch.trace import NullTracer


def tmpcfg(**kw):
    return Config(data_dir=tempfile.mkdtemp(prefix="sw-test-"), desktop_notify=False, **kw)


def run(scn, cfg=None):
    cfg = cfg or tmpcfg()
    eng = Engine(cfg, tracer=NullTracer())
    return eng, eng.run(g.load_rows(scn), g.SCEN_START, g.SCEN_END, mode="test"), cfg


class TestIngest(unittest.TestCase):
    def write(self, text):
        p = Path(tempfile.mkdtemp()) / "x.csv"; p.write_text(text, encoding="utf-8-sig"); return p

    def test_amount_formats(self):
        for raw, want in [("(1,234.50)", -1234.50), ("$12.30", 12.30), ("-45", -45), ("1.234,56", 1234.56), ("12,50", 12.50), ("", None), ("£ 3,000.00", 3000.0)]:
            self.assertEqual(parse_amount(raw), want, raw)

    def test_debit_credit_columns_and_dayfirst(self):
        p = self.write("Posted Date,Details,Debit,Credit\n25/03/2026,NETFLIX 123,12.99,\n26/03/2026,SALARY,,2000.00\n")
        rows, info = read_csv(p, Config(dayfirst=True))
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0].date, date(2026, 3, 25)); self.assertEqual(rows[0].amount, 12.99)
        self.assertEqual(info["skipped_credits"], 1)

    def test_single_amount_sign_auto_and_parentheses(self):
        p = self.write("Date,Description,Amount\n2026-01-02,SHOP,(20.00)\n2026-01-03,SHOP,(5.00)\n2026-01-04,REFUND,9.00\n")
        rows, _ = read_csv(p, Config())
        self.assertEqual([r.amount for r in rows], [20.0, 5.0])

    def test_column_mapping_fallback(self):
        p = self.write("When,Who,HowMuch\n2026-01-02,SHOP,-20.00\n")
        rows, _ = read_csv(p, Config(columns={"date": "When", "description": "Who", "amount": "HowMuch"}))
        self.assertEqual(rows[0].amount, 20.0)

    def test_source_never_modified_and_true_repeats_kept(self):
        p = self.write("Date,Description,Amount\n2026-01-02,SHOP,-20.00\n2026-01-02,SHOP,-20.00\n")
        before = p.read_bytes()
        rows = load_path(p, Config())
        self.assertEqual(len(rows), 2); self.assertEqual(p.read_bytes(), before)

    def test_overlapping_exports_merged(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.csv").write_text("Date,Description,Amount\n2026-01-02,SHOP,-20.00\n2026-01-03,CAFE,-4.00\n")
        (d / "b.csv").write_text("Date,Description,Amount\n2026-01-03,CAFE,-4.00\n2026-01-04,BAR,-9.00\n")
        self.assertEqual(len(load_path(d, Config())), 3)

    def test_bad_header_message(self):
        with self.assertRaises(ValueError):
            read_csv(self.write("a,b,c\n1,2,3\n"), Config())


class TestNormalize(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(clean_merchant("CARD 4421 STREAMBOX PREMIUM 8472910A 04/15"), "STREAMBOX PREMIUM")
        self.assertEqual(clean_merchant("CITY POWER & LIGHT ELECTRIC #99120"), "CITY POWER LIGHT ELECTRIC")
        self.assertEqual(clean_merchant("POS PURCHASE FRESHMART 15/09 REF3A8F5"), "FRESHMART")


class TestMerchantGrouping(unittest.TestCase):
    def test_distinct_bills_are_not_merged_but_variants_are(self):
        from slothwatch.normalize import MerchantIndex
        i = MerchantIndex()
        self.assertNotEqual(i.resolve("SOUTH WASTE"), i.resolve("SOUTH WATER"))
        self.assertNotEqual(i.resolve("NORTH MOBILE"), i.resolve("SOUTH MOBILE"))
        self.assertEqual(i.resolve("STREAMBOX PREMIUM"), i.resolve("STREAMBOX PREMIUM CO"))
        self.assertEqual(i.resolve("BEANBAR COFFEE"), i.resolve("BEANBAR COFFE"))


class TestScenarios(unittest.TestCase):
    def test_clean_month_is_silence(self):
        _, res, _ = run("clean-month")
        self.assertTrue(res["quiet"]); self.assertEqual(res["counts"]["surprises_total"], 0)
        self.assertEqual(res["counts"]["gemma_wakeups"], 0)
        self.assertTrue(res["message"].startswith("Nothing needs you. Go back to sleep."))
        self.assertIn("Receipt #1", res["message"])

    def test_each_planted_anomaly_is_caught(self):
        for scn in ["double-subscription", "duplicate-charge", "electricity-spike", "creeping-increase", "everything-wrong"]:
            with self.subTest(scn):
                _, res, _ = run(scn)
                got = {(m, k) for m, _, k in res["surprise_rows"]}
                for p in g.planted(scn):
                    self.assertIn((clean_merchant(p["merchant"]), p["kind"]), got)
                self.assertEqual(len(res["surprise_rows"]), len(g.planted(scn)), "no false positives")

    def test_gemma_wakes_only_for_flagged_rows(self):
        _, res, _ = run("everything-wrong")
        c = res["counts"]
        self.assertEqual(c["gemma_wakeups"], 4); self.assertLess(c["gemma_wakeups"], c["rows"] / 5)

    def test_nothing_is_auto_sent_and_approve_writes_draft_only(self):
        eng, res, cfg = run("duplicate-charge")
        out = eng.decide(res["act_alert_ids"][0], "approve")
        self.assertTrue(out["ok"]); self.assertTrue(Path(out["draft_path"]).exists())
        self.assertFalse(eng.decide(res["act_alert_ids"][0], "ignore")["ok"])   # can't decide twice

    def test_idempotent_window_gives_exactly_one_alert(self):
        cfg = tmpcfg(); eng = Engine(cfg, tracer=NullTracer()); rows = g.load_rows("duplicate-charge")
        a = eng.run(rows, g.SCEN_START, g.SCEN_END, mode="t"); b = eng.run(rows, g.SCEN_START, g.SCEN_END, mode="t")
        self.assertTrue(b["idempotent"]); self.assertEqual(a["run_id"], b["run_id"])
        con = connect(cfg)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)

    def test_deterministic(self):
        a = run("everything-wrong")[1]; b = run("everything-wrong")[1]
        self.assertEqual(a["counts"], b["counts"]); self.assertEqual(a["surprise_rows"], b["surprise_rows"])

    def test_fast_forward_catches_duplicate_on_day_15(self):
        cfg = tmpcfg(); eng = Engine(cfg, tracer=NullTracer()); rows = g.load_rows("duplicate-charge")
        pinged = []
        d = g.SCEN_START
        while d <= g.SCEN_END:
            if eng.run(rows, d, d, mode="fast", refresh_profiles=False)["pings"]:
                pinged.append(d.day)
            d += timedelta(days=1)
        self.assertEqual(pinged, [15])


class TestReceipts(unittest.TestCase):
    def test_chain_verifies_and_tampering_fails(self):
        cfg = tmpcfg(); eng = Engine(cfg, tracer=NullTracer())
        for scn in ("clean-month", "everything-wrong"):
            eng.run(g.load_rows(scn), g.SCEN_START, g.SCEN_END, mode="t")
        con = connect(cfg)
        self.assertTrue(verify(con, cfg)["ok"])
        f = next(Path(cfg.data_dir, "receipts").glob("*-0001.json"))
        rec = json.loads(f.read_text()); rec["body"]["rows_checked"] += 1; f.write_text(json.dumps(rec))
        r = verify(con, cfg); self.assertFalse(r["ok"]); self.assertTrue(any("#1" in e for e in r["errors"]))
        # tamper the database too
        rec["body"]["rows_checked"] -= 1; f.write_text(json.dumps(rec, indent=1))
        con.execute("UPDATE receipts SET body_json=replace(body_json,'\"surprises\":0','\"surprises\":9') WHERE id=1"); con.commit()
        self.assertFalse(verify(con, cfg)["ok"])

    def test_deleted_receipt_breaks_chain(self):
        cfg = tmpcfg(); eng = Engine(cfg, tracer=NullTracer())
        for scn in ("clean-month", "everything-wrong"):
            eng.run(g.load_rows(scn), g.SCEN_START, g.SCEN_END, mode="t")
        con = connect(cfg); con.execute("DELETE FROM receipts WHERE id=1"); con.commit()
        self.assertFalse(verify(con, cfg)["ok"])


class TestCanary(unittest.TestCase):
    def test_canary_is_caught(self):
        from slothwatch.canary import run_canary
        from slothwatch.normalize import annotate
        rows = annotate(g.load_rows("clean-month"))
        h = [t for t in rows if t.date < g.SCEN_START]; c = [t for t in rows if t.date >= g.SCEN_START]
        r = run_canary(h, c, tmpcfg()); self.assertTrue(r["caught"], r)


class TestLearning(unittest.TestCase):
    def feats(self, **kw):
        return {"kind": "outside_interval", "category": "utility", "deviation_pct": 25.0, "recurrence": "monthly",
                "amount": 90.0, "day_of_month": 5, "repeat_count": 1, **kw}

    def test_cold_start_skips_rung(self):
        from slothwatch.care import Care
        cfg = Config(); self.assertIsNone(Care([(self.feats(), 0)] * 5, cfg).predict(self.feats()))
        self.assertIsNone(Care([(self.feats(), 0)] * 20, cfg).predict(self.feats()), "one-sided history is not enough")

    def test_learns_to_hush_what_you_ignore_but_not_what_you_approve(self):
        from slothwatch.care import Care
        cfg = Config(use_tabpfn=False)
        hist = [(self.feats(deviation_pct=20 + i), 0) for i in range(10)] + [(self.feats(kind="duplicate", deviation_pct=0, category="other", amount=60 + i), 1) for i in range(10)]
        c = Care(hist, cfg)
        self.assertLess(c.predict(self.feats(deviation_pct=24)), cfg.care_threshold)
        self.assertGreater(c.predict(self.feats(kind="duplicate", deviation_pct=0, category="other", amount=64)), 0.5)

    def test_safety_rail_never_suppresses_large_amounts(self):
        from slothwatch.care import Care
        from slothwatch.ladder import run_ladder
        from slothwatch.normalize import annotate
        cfg = Config(use_tabpfn=False, never_suppress_amount=50.0)
        rows = annotate(g.load_rows("double-subscription"))
        class Hush(Care):
            ready = True
            def predict(self, f): return 0.0
        h = [t for t in rows if t.date < g.SCEN_START]; c = [t for t in rows if t.date >= g.SCEN_START]
        out = run_ladder(h, c, cfg, care=Hush([], cfg), explainer=None)
        self.assertEqual(out["counts"]["suppressed"], 1, "21.69 < 50 is hushed")
        cfg.never_suppress_amount = 10.0
        out = run_ladder(h, c, cfg, care=Hush([], cfg), explainer=None)
        self.assertEqual(out["counts"]["suppressed"], 0, "above the rail nothing is hushed")

    def test_simulation_runs_and_reports_honestly(self):
        from slothwatch.demo.sim import run_both
        r = run_both()
        for k in ("on", "off"):
            self.assertEqual(len(r[k]["weeks"]), 12)
            self.assertLessEqual(r[k]["true_caught"], r[k]["true_total"])
        self.assertLessEqual(r["on"]["total_wakeups"], r["off"]["total_wakeups"])


class TestLLM(unittest.TestCase):
    def test_validate(self):
        ok = {"verdict_two_sentences": "A. B.", "severity": "act", "suggested_action": "x", "draft_message": ""}
        self.assertTrue(validate(ok)); self.assertIsNone(validate({**ok, "severity": "panic"})); self.assertIsNone(validate({"x": 1}))

    def test_fallback_is_labelled(self):
        cfg = Config(ollama_url="http://127.0.0.1:9")   # nothing listens here
        ex = Explainer(cfg).explain({"merchant": "X", "date": "2026-01-01", "amount": 9.0, "kind": "outside_interval", "deviation": 50.0})
        self.assertEqual(ex.source, "template"); self.assertTrue(ex.estimated)


class TestPrivacy(unittest.TestCase):
    def test_guard_blocks_external(self):
        def evil():
            socket.create_connection(("93.184.216.34", 80), timeout=1)
        with self.assertRaises(ExternalConnection):
            guarded(evil)

    def test_full_pipeline_makes_no_external_connections(self):
        from slothwatch.demo.cli import privacy_workload
        _, attempts = guarded(privacy_workload)
        self.assertEqual(attempts, [])


class TestTuningHarness(unittest.TestCase):
    def test_reference_scores_perfectly_and_garbage_scores_zero(self):
        from slothwatch.tuning.dataset import generate
        from slothwatch.tuning.evaluate import evaluate
        train, test = generate(200)
        ref = {r["prompt"]: r["completion"] for r in test}
        good = evaluate(lambda p: (ref[p], 0, 0), test); bad = evaluate(lambda p: ("nonsense", 0, 0), test)
        self.assertEqual(good["json_validity"], 1.0); self.assertEqual(good["severity_accuracy"], 1.0); self.assertEqual(good["rubric"], 1.0)
        self.assertEqual(bad["json_validity"], 0.0); self.assertEqual(len(train) + len(test), 200)


if __name__ == "__main__":
    unittest.main()
