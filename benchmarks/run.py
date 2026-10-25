"""Run deterministic scenarios through the ladder and a measured all-rows wakeup baseline."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from slothwatch.config import load_config, Settings  # noqa: E402
from slothwatch.ladder import run_pipeline  # noqa: E402
from slothwatch.synthetic import SCENARIOS, generate_transactions  # noqa: E402
from slothwatch.learning import simulate_persona_learning  # noqa: E402
from slothwatch.llm import explain_with_ollama  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

RESULTS = ROOT / "benchmarks" / "results"


def _metrics(rows, result):
    truth = {r.id for r in rows if r.is_anomaly}
    predicted = {r["transaction_id"] for r in result["results"] if r["outcome"] == "alert"}
    tp = len(truth & predicted)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    precision = tp / (tp + fp) if tp + fp else (1.0 if not truth else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"true_anomalies": len(truth), "detected_true_anomalies": tp, "false_positives": fp, "missed_true_anomalies": fn, "precision": round(precision, 4), "recall": round(recall, 4), "predicted_alerts": len(predicted)}


def _ollama_model_ready(llm_config: dict) -> bool:
    """Probe only a loopback Ollama endpoint before starting an optional model benchmark."""
    endpoint = str(llm_config.get("endpoint", "http://127.0.0.1:11434/api/generate"))
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    tags_url = f"{parsed.scheme}://{parsed.netloc}/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=2.0) as response:
            tags = json.loads(response.read().decode("utf-8"))
        model = str(llm_config.get("model", "gemma3:4b"))
        return any(str(item.get("name", "")) == model for item in tags.get("models", []))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        return False


def _baseline(rows, detector_result, values, use_ollama: bool = False):
    # Controlled baseline: same detected row labels, but every synthetic transaction reaches the explainer.
    start = time.perf_counter()
    result_by_id = {item["transaction_id"]: item for item in detector_result["results"]}
    input_tokens = output_tokens = successful_model_calls = 0
    token_sources = set()
    if use_ollama:
        for tx in rows:
            context = result_by_id.get(tx.id, {}).get("forecast") or {"expected": None, "low": None, "high": None, "method": "baseline-no-forecast"}
            _, metadata = explain_with_ollama(tx.to_dict(), context, "naive baseline: every transaction is sent to the explainer", values["llm"], use_ollama=True)
            input_tokens += int(metadata.get("tokens_in", 0))
            output_tokens += int(metadata.get("tokens_out", 0))
            token_sources.add(metadata.get("token_source", "whitespace-estimate"))
            successful_model_calls += int(metadata.get("provider") == "ollama")
        token_method = "Ollama reported token counts when available; otherwise whitespace estimates; every row attempted"
        baseline_mode = f"loopback Ollama; successful model calls {successful_model_calls}/{len(rows)}"
    else:
        input_tokens = sum(max(1, len((r.description + " " + r.date + " " + str(r.amount)).split())) + 28 for r in rows)
        output_tokens = len(rows) * 45
        token_method = "whitespace-based token estimate; no LLM service was called"
        baseline_mode = "controlled all-row explainer ablation; template-mode costs and latency are not model measurements"
    elapsed = time.perf_counter() - start
    pricing = values["benchmark"]
    estimated_cost = input_tokens * float(pricing["closed_api_input_usd_per_million_tokens"]) / 1_000_000 + output_tokens * float(pricing["closed_api_output_usd_per_million_tokens"]) / 1_000_000
    labels = _metrics(rows, detector_result)
    return {"wakeups": len(rows), "successful_model_calls": successful_model_calls if use_ollama else 0, "mode": baseline_mode, "tokens_in_estimate": input_tokens, "tokens_out_estimate": output_tokens, "wall_seconds": round(elapsed, 6), "estimated_closed_api_cost_usd": round(estimated_cost, 8), "precision": labels["precision"], "recall": labels["recall"], "labeling": "same detector outputs; all rows passed to explainer (controlled ablation)", "token_method": token_method}


def run(use_ollama: bool = False) -> dict:
    RESULTS.mkdir(parents=True, exist_ok=True)
    base = load_config()
    active_ollama = bool(use_ollama and _ollama_model_ready(base.values["llm"]))
    if use_ollama and not active_ollama:
        print("Requested Ollama benchmark, but the configured loopback model is not reachable/pulled; using explicitly labeled template mode.")
    scenarios = []
    for scenario in SCENARIOS:
        values = json.loads(json.dumps(base.values))
        values["app"]["state_dir"] = str(ROOT / ".slothwatch" / "benchmark" / scenario)
        values["app"]["database"] = str(ROOT / ".slothwatch" / "benchmark" / scenario / "slothwatch.sqlite3")
        values["tracing"]["local_trace_file"] = str(ROOT / ".slothwatch" / "benchmark" / scenario / "traces.jsonl")
        settings = Settings(values, base.source)
        rows = generate_transactions(scenario, seed=2401)
        start = time.perf_counter()
        result = run_pipeline(rows, settings, mode="benchmark", source=f"synthetic:{scenario}", use_ollama=active_ollama)
        wall = time.perf_counter() - start
        metrics = _metrics(rows, result)
        estimated_input = sum(max(1, len((r.description + " " + r.date + " " + str(r.amount)).split())) + 28 for r in rows if any(a["transaction_id"] == r.id for a in result["alerts"]))
        estimated_output = len(result["alerts"]) * 45
        price = values["benchmark"]
        estimated_cost = estimated_input * price["closed_api_input_usd_per_million_tokens"] / 1_000_000 + estimated_output * price["closed_api_output_usd_per_million_tokens"] / 1_000_000
        ladder = {"wakeups": result["gemma_wakeups"], "human_pings": result["human_pings"], "rows_checked": len(rows), "settled_r0": result["rung_counts"].get("r0", 0), "settled_r1": result["rung_counts"].get("r1", 0), "settled_r1_5": result["rung_counts"].get("r15", 0), "tokens_in_estimate": estimated_input, "tokens_out_estimate": estimated_output, "wall_seconds": round(wall, 6), "estimated_closed_api_cost_usd": round(estimated_cost, 8), **metrics, "explainer_mode": "local Ollama/Gemma" if active_ollama else "deterministic local template; Ollama/Gemma not invoked"}
        baseline = _baseline(rows, result, values, use_ollama=active_ollama)
        scenarios.append({"scenario": scenario, "ladder": ladder, "naive_baseline": baseline})
    learning_off = simulate_persona_learning(weeks=12, seed=2401, learning_on=False)
    learning_on = simulate_persona_learning(weeks=12, seed=2401, learning_on=True)
    total_wakes = sum(s["ladder"]["wakeups"] for s in scenarios)
    total_base = sum(s["naive_baseline"]["wakeups"] for s in scenarios)
    data = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "local Ollama synthetic run" if active_ollama else "local deterministic synthetic run", "use_ollama": active_ollama, "method_note": "The ladder and all-row baseline were run on synthetic rows only. In template mode, token counts are whitespace estimates and neither side called Gemma. With --use-ollama and a configured loopback model, the ladder calls Ollama only for surprises and the baseline attempts a call for every row; token telemetry depends on the Ollama response.", "summary": {"ladder_explainer_wakeups": total_wakes, "naive_explainer_wakeups": total_base, "ladder_gemma_wakeups": total_wakes, "naive_wakeups": total_base, "wakeups_reduced_pct": round(100 * (1 - total_wakes / total_base), 2) if total_base else 0, "scenarios": len(scenarios)}, "scenarios": scenarios, "learning": {"off": learning_off, "on": learning_on}}
    (RESULTS / "benchmark.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    lines = ["# SlothWatch benchmark results", "", f"Generated: {data['generated_at']}", "", ("> **Mode: local Ollama.** The flagged-only ladder used the configured loopback Ollama model and the naive baseline attempted every synthetic row. Token counts use Ollama response metrics when present, otherwise a whitespace estimate. The closed-API cost is only a configurable hypothetical estimate." if active_ollama else "> **Mode: deterministic template fallback.** Gemma/Ollama was not called. Token counts are whitespace-based estimates, local template costs are $0, and the naive baseline is a controlled all-row explainer ablation rather than measured model latency. Closed-API cost is a configurable hypothetical estimate."), "", f"Across {len(scenarios)} scenarios, the ladder woke the explainer {total_wakes} times versus {total_base} baseline wake-ups — a {data['summary']['wakeups_reduced_pct']}% reduction in modeled explainer wake-ups.", "", "## Scenario results", "", "| Scenario | Rows | Ladder wakes | Naive wakes | TP | FP | Missed | Precision | Recall | Ladder wall (s) | Ladder cost estimate (USD) |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in scenarios:
        a = s["ladder"]
        b = s["naive_baseline"]
        lines.append(f"| {s['scenario']} | {a['rows_checked']} | {a['wakeups']} | {b['wakeups']} | {a['detected_true_anomalies']} | {a['false_positives']} | {a['missed_true_anomalies']} | {a['precision']:.2f} | {a['recall']:.2f} | {a['wall_seconds']:.4f} | ${a['estimated_closed_api_cost_usd']:.6f} |")
    lines += ["", "## Laziness Curve (simulated persona, not learned user data)", "", "| Setting | Modeled explainer wake-ups | Human pings | Recall | Precision |", "|---|---:|---:|---:|---:|"]
    for key, title in (("off", "Learning off"), ("on", "Learning on")):
        a = data["learning"][key]
        lines.append(f"| {title} | {a['gemma_wakeups']} | {a['human_pings']} | {a['recall']:.2f} | {a['precision']:.2f} |")
    lines += ["", "The persona simulation uses fixed preferences (ignores modest utility movement, approves clear duplicates) and is only a mechanism demonstration. It is not evidence about real users. A production classifier stays cold-started until at least 15 explicit human decisions are available; safety rails prevent suppressing large amounts.", "", "## Measurements and caveats", "", "- Rows, rung outcomes, detected labels and wake-ups are read from the actual local execution of the project.", "- Wall time is actual elapsed local runtime for the whole pipeline. It will vary by machine and installed optional integrations.", "- Input/output token counts use a documented whitespace estimate because this default benchmark did not call Gemma. Cost uses assumptions from `slothwatch.yaml` and is not comparable to actual local inference cost, which is tracked as $0.", "- Detection metrics are computed against planted anomaly labels in the synthetic fixture. Any misses and false positives are displayed rather than hidden.", "- To benchmark actual local inference, install/start Ollama, pull the configured Gemma model, and run `python benchmarks/run.py --use-ollama`. This sends synthetic fixture rows only; it can take materially longer than the default template-mode run. If Ollama lacks token counters, token counts fall back to whitespace estimates.", ""]
    (RESULTS / "benchmark.md").write_text("\n".join(lines), encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        names = [s["scenario"] for s in scenarios]
        ladder_wakes = [s["ladder"]["wakeups"] for s in scenarios]
        naive_wakes = [s["naive_baseline"]["wakeups"] for s in scenarios]
        fig, ax = plt.subplots(figsize=(11, 5.5))
        x = list(range(len(names)))
        width = 0.36
        ax.bar([v - width / 2 for v in x], ladder_wakes, width, label="SlothWatch ladder")
        ax.bar([v + width / 2 for v in x], naive_wakes, width, label="Naive all-row baseline")
        ax.set_ylabel("Explainer wake-ups")
        ax.set_title("SlothWatch: avoid waking the explainer for boring rows")
        ax.set_xticks(x, names, rotation=20, ha="right")
        ax.legend()
        fig.tight_layout()
        fig.savefig(RESULTS / "benchmark.png", dpi=150)
        plt.close(fig)
        # A separate weekly view makes the simulated learning trade-off inspectable.
        fig, ax = plt.subplots(figsize=(10, 5.2))
        off_weeks = data["learning"]["off"]["weeks"]
        on_weeks = data["learning"]["on"]["weeks"]
        week_numbers = [item["week"] for item in off_weeks]
        ax.plot(week_numbers, [item["gemma_wakeups"] for item in off_weeks], marker="o", label="Explainer wakes · learning off")
        ax.plot(week_numbers, [item["gemma_wakeups"] for item in on_weeks], marker="o", label="Explainer wakes · learning on")
        ax.plot(week_numbers, [item["human_pings"] for item in off_weeks], linestyle="--", marker="x", label="Human pings · learning off")
        ax.plot(week_numbers, [item["human_pings"] for item in on_weeks], linestyle="--", marker="x", label="Human pings · learning on")
        ax.set_xlabel("Simulated week")
        ax.set_ylabel("Events")
        ax.set_title("Laziness Curve · deterministic fictional persona")
        ax.set_xticks(week_numbers)
        ax.legend(fontsize=8, ncols=2)
        fig.tight_layout()
        fig.savefig(RESULTS / "laziness_curve.png", dpi=150)
        plt.close(fig)
    except ImportError:
        # A dependency-free valid PNG with an explanatory fallback is not fabricated; tell the caller.
        (RESULTS / "benchmark.png.MISSING.txt").write_text("Install matplotlib to render benchmark.png and laziness_curve.png.\n", encoding="utf-8")
    print((RESULTS / "benchmark.md").read_text(encoding="utf-8"))
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--use-ollama", action="store_true", help="Call the configured local Ollama model; all benchmark inputs are synthetic")
    args = parser.parse_args()
    run(use_ollama=args.use_ollama)
