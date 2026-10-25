"""CLI for seeded synthetic scenarios and the guided four-step demo."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .config import load_config
from .ladder import run_pipeline
from .synthetic import SCENARIOS, generate_monthly_files, generate_transactions


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slothwatch-demo", description="SlothWatch synthetic-data demo")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run a scenario")
    run.add_argument("--scenario", choices=SCENARIOS, default="clean-month")
    run.add_argument("--seed", type=int, default=2401)
    run.add_argument("--fast", action="store_true", help="Print a 30-day sloth diary with an accelerated clock")
    run.add_argument("--seconds", type=int, default=60, help="Total duration for --fast diary")
    run.add_argument("--use-ollama", action="store_true", help="Use local Ollama for flagged rows")
    gen = sub.add_parser("generate", help="Generate twelve monthly synthetic CSVs")
    gen.add_argument("--scenario", choices=SCENARIOS, default="clean-month")
    gen.add_argument("--out", default="data/demo")
    gen.add_argument("--seed", type=int, default=2401)
    sub.add_parser("tour", help="Run the guided four-step demo")
    return p


def _run(scenario: str, seed: int, use_ollama: bool = False) -> dict:
    settings = load_config()
    rows = generate_transactions(scenario, seed)
    print(f"Mode: local-in-process orchestration; explainer: {'local Ollama with template fallback' if use_ollama else 'deterministic local template (no model service required)'}")
    print(f"Scenario: {scenario} | deterministic seed: {seed} | fictional rows: {len(rows)}")
    result = run_pipeline(rows, settings, mode="demo", source=f"synthetic:{scenario}:seed={seed}", use_ollama=use_ollama)
    print(f"Sloth Score: {result['sloth_score']:.2f} | Rung 0: {result['rung_counts'].get('r0', 0)} | Rung 1: {result['rung_counts'].get('r1', 0)} | Gemma/template wakes: {result['gemma_wakeups']} | human pings: {result['human_pings']}")
    if result["human_pings"]:
        print("Paused for human approval. No message was sent; use the dashboard or slothwatch approve <id> to copy a draft to outbox.")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "generate":
        paths = generate_monthly_files(args.scenario, args.out, args.seed)
        print(f"Generated {len(paths)} monthly CSV files under {Path(args.out).resolve()}")
        return 0
    if args.command == "run":
        if args.fast:
            duration = max(0, args.seconds)
            pause = duration / 30 if duration else 0
            for day in range(1, 31):
                print(f"Day {day}: nothing." if day != 17 else "Day 17: a tiny surprise; the sloth checks the forecast.")
                if pause:
                    time.sleep(pause)
        _run(args.scenario, args.seed, args.use_ollama)
        return 0
    if args.command == "tour":
        stages = [
            ("1/4 — CLEAN MONTH", "No personal data. The rules and forecasts stay quiet."),
            ("2/4 — INJECT ANOMALIES", "Replay a double subscription, duplicate charge, utility spike and creeping increase."),
            ("3/4 — WATCH THE LADDER", "Rules settle boring rows, forecast intervals surface surprises, and the local explainer wakes only for those rows."),
            ("4/4 — TRACE AND SCORE", "Inspect receipts, the funnel, and explicit human approvals. Nothing is sent automatically."),
        ]
        for title, text in stages:
            print(f"\n{title}\n{text}")
            time.sleep(1)
        _run("clean-month", 2401, False)
        _run("everything-wrong", 2401, False)
        print("Tour complete. Dashboard: http://127.0.0.1:8765 (start with `make dashboard`).")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
