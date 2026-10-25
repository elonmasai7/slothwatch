"""Generate/evaluate synthetic examples; optionally train a Tinker LoRA adapter.

This opt-in path sends only generated synthetic examples to the hosted Tinker service.
It must never be used with a user's CSV export or personal financial data.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .dataset import generate_dataset

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "benchmarks" / "results"
DATA = RESULTS / "tuning_data"
DEFAULT_MODEL = "Qwen/Qwen3.5-4B"


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _schema_metrics(prediction: str, target: str) -> dict[str, Any]:
    try:
        obj = json.loads(prediction)
        required = {"verdict_two_sentences", "severity", "suggested_action", "draft_message"}
        valid = (
            set(obj) == required
            and obj.get("severity") in {"ignore", "watch", "act"}
            and all(isinstance(obj.get(key), str) and obj[key].strip() for key in required)
        )
        sentences = len(re.findall(r"[.!?](?:\s|$)", str(obj.get("verdict_two_sentences", "")).strip()))
        clear = sentences == 2 and len(str(obj.get("suggested_action", "")).strip()) > 2
        severity_ok = obj.get("severity") == json.loads(target).get("severity")
        return {"json_valid": bool(valid), "severity_correct": bool(severity_ok), "verdict_clear": bool(clear)}
    except (ValueError, TypeError, KeyError):
        return {"json_valid": False, "severity_correct": False, "verdict_clear": False}


def _template_prediction(prompt: str) -> str:
    """Produce a deterministic strict-JSON response from structured synthetic context."""
    context_text = prompt.split("Context:", 1)[-1].strip()
    context = json.loads(context_text)
    category = str(context.get("merchant_category", "unknown"))
    amount = float(context.get("amount", 0))
    expected = float(context.get("expected", 0))
    reason = str(context.get("reason", "normal"))
    if reason in {"duplicate", "large-spike"}:
        severity, action = "act", "Compare with the provider statement before requesting reversal"
    elif reason in {"price-increase", "unexpected-amount"}:
        severity, action = "watch", "Review the provider statement and renewal terms"
    else:
        severity, action = "ignore", "No action needed"
    first = (f"The {category} charge of {amount:.2f} differs from the expected {expected:.2f}."
             if severity != "ignore" else f"The {category} charge of {amount:.2f} is consistent with the available context.")
    second = "Verify the provider statement before taking any action." if severity == "act" else "No immediate action is required; review it at your convenience."
    draft = (f"Hello, I am reviewing a {category} charge of {amount:.2f}. Please explain the amount and confirm whether it matches my plan." if severity != "ignore" else "No message needed; this row is classified as routine.")
    return json.dumps({"verdict_two_sentences": f"{first} {second}", "severity": severity, "suggested_action": action, "draft_message": draft}, separators=(",", ":"))


def baseline_report(holdout: list[dict[str, Any]]) -> dict[str, Any]:
    start = time.perf_counter()
    metrics = []
    token_estimate = 0
    for row in holdout:
        prediction = _template_prediction(row["prompt"])
        metrics.append(_schema_metrics(prediction, row["target"]))
        token_estimate += len((row["prompt"] + " " + prediction).split())
    elapsed = time.perf_counter() - start
    count = max(1, len(metrics))
    return {"status": "measured deterministic rule/template baseline (not a model call)", "examples": len(metrics), "json_valid_rate": sum(x["json_valid"] for x in metrics) / count, "severity_accuracy": sum(x["severity_correct"] for x in metrics) / count, "verdict_clarity_score": sum(x["verdict_clear"] for x in metrics) / count, "average_latency_ms": elapsed * 1000 / count, "tokens": f"{token_estimate} whitespace-estimated total", "cost_usd": 0.0, "limitation": "This is a local deterministic reference, not an LLM baseline. The structured rule-reason field is available to the baseline, so the perfect score measures consistency with synthetic labels, not independent anomaly reasoning. The optional Tinker model has not been run unless the report says it was trained and evaluated."}


def train_tinker(train: list[dict[str, Any]], holdout: list[dict[str, Any]], model: str, steps: int = 1) -> dict[str, Any]:
    """Train and evaluate a LoRA adapter using Tinker's current async SDK API."""
    try:
        import asyncio
        import tinker
    except ImportError as exc:
        raise RuntimeError("Install the optional SDK with `pip install 'tinker>=0.24.0,<1.0'` first") from exc
    if not os.getenv("TINKER_API_KEY"):
        raise RuntimeError("TINKER_API_KEY is required; only synthetic training rows are sent to Tinker")

    async def train_and_evaluate() -> dict[str, Any]:
        service = tinker.ServiceClient(display_name="slothwatch-synthetic-verdict-tuning")
        client = await service.create_lora_training_client_async(base_model=model, rank=16)
        tokenizer = client.get_tokenizer()
        rows = train[: min(len(train), 1024)]
        batch_size = 8
        started = time.perf_counter()

        def datum_for(row: dict[str, Any]) -> Any:
            prompt_text = tokenizer.apply_chat_template(
                [{"role": "user", "content": row["prompt"]}],
                tokenize=False, add_generation_prompt=True, enable_thinking=False,
            )
            prompt_tokens = tokenizer.encode(prompt_text, add_special_tokens=False)
            answer_tokens = tokenizer.encode(row["target"], add_special_tokens=False)
            all_tokens = prompt_tokens + answer_tokens
            weights = [0.0] * len(prompt_tokens) + [1.0] * len(answer_tokens)
            return tinker.Datum(
                model_input=tinker.ModelInput.from_ints(all_tokens[:-1]),
                loss_fn_inputs={"target_tokens": all_tokens[1:], "weights": weights[1:]},
            )

        for _epoch in range(max(1, steps)):
            for offset in range(0, len(rows), batch_size):
                datums = [datum_for(row) for row in rows[offset:offset + batch_size]]
                # Current Tinker docs recommend async methods; submit both operations before
                # awaiting their completion so the service can pipeline the requests.
                fwdbwd_future = await client.forward_backward_async(datums, "cross_entropy")
                optim_future = await client.optim_step_async(tinker.AdamParams(learning_rate=1e-4))
                await fwdbwd_future.result_async()
                await optim_future.result_async()

        save_future = await client.save_weights_for_sampler_async(name="slothwatch-synthetic-verdict-tuning")
        checkpoint = await save_future.result_async()
        sampling_client = await service.create_sampling_client_async(model_path=checkpoint.path)
        evaluated = []
        estimated_tokens = 0
        for row in holdout:
            prompt_text = tokenizer.apply_chat_template(
                [{"role": "user", "content": row["prompt"]}],
                tokenize=False, add_generation_prompt=True, enable_thinking=False,
            )
            prompt_tokens = tokenizer.encode(prompt_text, add_special_tokens=False)
            result = await sampling_client.sample_async(
                prompt=tinker.ModelInput.from_ints(prompt_tokens), num_samples=1,
                sampling_params=tinker.SamplingParams(max_tokens=260, temperature=0.0),
            )
            prediction = tokenizer.decode(result.sequences[0].tokens, skip_special_tokens=True).strip()
            evaluated.append(_schema_metrics(prediction, row["target"]))
            estimated_tokens += len(prompt_tokens) + len(result.sequences[0].tokens)
        elapsed = time.perf_counter() - started
        n = max(1, len(evaluated))
        return {"status": "trained and evaluated via Tinker", "model": model, "training_examples": len(rows), "holdout_examples": len(holdout), "steps": steps, "json_valid_rate": sum(x["json_valid"] for x in evaluated) / n, "severity_accuracy": sum(x["severity_correct"] for x in evaluated) / n, "verdict_clarity_score": sum(x["verdict_clear"] for x in evaluated) / n, "average_latency_ms": elapsed * 1000 / n, "tokens": f"{estimated_tokens} tokenizer-counted input+output tokens across holdout", "cost_usd": "retrieve from Tinker billing; not guessed", "checkpoint": str(checkpoint.path), "note": "Hosted training and held-out sampling were explicitly requested; only synthetic data was used."}

    try:
        return asyncio.run(train_and_evaluate())
    except RuntimeError as exc:
        # This CLI owns its event loop; surface clear setup/API errors rather than fabricating a score.
        raise RuntimeError(f"Tinker training/evaluation failed: {exc}") from exc


def run(do_train: bool = False, model: str = DEFAULT_MODEL, count: int = 1200, steps: int = 1) -> dict[str, Any]:
    RESULTS.mkdir(parents=True, exist_ok=True)
    dataset_meta = generate_dataset(DATA, count=count)
    train, holdout = _load(Path(dataset_meta["train_path"])), _load(Path(dataset_meta["holdout_path"]))
    baseline = baseline_report(holdout)
    # Keep machine-specific scratch paths out of the shareable report.
    dataset_report = {key: value for key, value in dataset_meta.items() if key not in {"train_path", "holdout_path"}}
    report: dict[str, Any] = {"dataset": dataset_report, "baseline": baseline, "tuned": {"status": "not run; requires --train and TINKER_API_KEY"}, "model_selection": {"model": model, "reason": "Current Tinker supported-model table lists Qwen/Qwen3.5-4B and does not list a Gemma-family model. The runtime app remains local Gemma via Ollama; this is a separate opt-in training experiment."}}
    if do_train:
        report["tuned"] = train_tinker(train, holdout, model, steps)
    (RESULTS / "tuning.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = ["# Optional model tuning results", "", f"Dataset: {dataset_meta['count']} synthetic-only examples ({dataset_meta['train_count']} train, {dataset_meta['holdout_count']} holdout; seed {dataset_meta['seed']}).", "", "## Reference baseline", "", "| Method | JSON validity | Severity accuracy | Verdict clarity | Average latency | Tokens | Cost |", "|---|---:|---:|---:|---:|---|---|", f"| {baseline['status']} | {baseline['json_valid_rate']:.3f} | {baseline['severity_accuracy']:.3f} | {baseline['verdict_clarity_score']:.3f} | {baseline['average_latency_ms']:.3f} ms | {baseline['tokens']} | ${baseline['cost_usd']:.2f} |", "", "## Tuned model", "", f"Status: {report['tuned']['status']}."]
    lines += ["", "## Baseline versus tuned comparison", "", "| Method | JSON validity | Severity accuracy | Verdict clarity | Average latency | Tokens | Cost |", "|---|---:|---:|---:|---:|---|---|", f"| Deterministic rule/template baseline | {baseline['json_valid_rate']:.3f} | {baseline['severity_accuracy']:.3f} | {baseline['verdict_clarity_score']:.3f} | {baseline['average_latency_ms']:.3f} ms | {baseline['tokens']} | ${baseline['cost_usd']:.2f} |"]
    if report["tuned"].get("json_valid_rate") is not None:
        tuned_row = report["tuned"]
        lines.append(f"| {tuned_row.get('model', model)} | {tuned_row['json_valid_rate']:.3f} | {tuned_row['severity_accuracy']:.3f} | {tuned_row['verdict_clarity_score']:.3f} | {tuned_row['average_latency_ms']:.3f} ms | {tuned_row['tokens']} | {tuned_row['cost_usd']} |")
    else:
        lines.append("| Tuned model | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |")
    lines += ["", "The tuned row is intentionally marked NOT RUN until the user explicitly starts hosted training with `--train` and a valid Tinker credential; no performance gain is asserted."]
    if report["tuned"].get("json_valid_rate") is not None:
        tuned = report["tuned"]
        lines += ["", "| Method | JSON validity | Severity accuracy | Verdict clarity | Average latency | Tokens | Cost |", "|---|---:|---:|---:|---:|---|---|", f"| {tuned.get('model', model)} | {tuned['json_valid_rate']:.3f} | {tuned['severity_accuracy']:.3f} | {tuned['verdict_clarity_score']:.3f} | {tuned['average_latency_ms']:.3f} ms | {tuned['tokens']} | {tuned['cost_usd']} |", "", "Compare these measured held-out metrics with the reference baseline before treating tuning as an improvement."]
    else:
        lines += ["", "No Tinker run was claimed because no explicit API credential/training request was supplied. No tuned-model performance number is fabricated."]
    lines += ["", "## Model and privacy decision", "", report["model_selection"]["reason"], "", baseline["limitation"], "", "**Privacy warning:** Tinker training is hosted. This command sends only the generated synthetic dataset and is a deliberate exception to the local-runtime network policy. Never point it at bank statements or personal data.", ""]
    (RESULTS / "tuning.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", action="store_true", help="Opt in to hosted Tinker training on synthetic data only")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--count", type=int, default=1200)
    parser.add_argument("--steps", type=int, default=1, help="Training epochs (budget will increase with dataset size)")
    args = parser.parse_args(argv)
    try:
        report = run(args.train, args.model, args.count, args.steps)
    except (RuntimeError, ValueError) as exc:
        print(f"Tuning stopped safely: {exc}")
        return 2
    print(json.dumps(report, indent=2))
    print(f"Results: {RESULTS / 'tuning.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
