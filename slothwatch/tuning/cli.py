"""`slothwatch tune gen|eval|train`. Training needs the Tinker SDK and key; see docs/DECISIONS.md."""
from __future__ import annotations
import json, os
from pathlib import Path
from ..config import load_config
from ..llm import Ollama
from .dataset import generate
from .evaluate import evaluate

OUT = Path("benchmarks/results")


def main(a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    train, test = generate(a.n)
    if a.action == "gen":
        d = Path("benchmarks/tuning"); d.mkdir(parents=True, exist_ok=True)
        for name, rows in (("train", train), ("heldout", test)):
            with open(d / f"{name}.jsonl", "w") as f:
                for r in rows:
                    f.write(json.dumps({"prompt": r["prompt"], "completion": r["completion"], "gold_severity": r["gold_severity"]}) + "\n")
        print(f"Wrote {len(train)} train / {len(test)} held-out examples to {d}/")
        return 0
    if a.action == "eval":
        cfg = load_config(None)
        if a.model:
            cfg.ollama_model = a.model
        o = Ollama(cfg)
        # harness self-check: the reference answers must score perfectly or the harness is broken
        ref = {r["prompt"]: r["completion"] for r in test}
        chk = evaluate(lambda p: (ref[p], 0, 0), test)
        print(f"Harness self-check (reference completions): validity {chk['json_validity']:.2f}, severity {chk['severity_accuracy']:.2f}, rubric {chk['rubric']:.2f}")
        if not o.available():
            msg = (f"# Tuning results\n\nNot measured: no Ollama model '{cfg.ollama_model}' reachable. The dataset ({len(train)} train / {len(test)} held-out) "
                   f"and harness are ready (self-check above). Serve the stock and tuned models through Ollama and run "
                   f"`slothwatch tune eval --model <name>` for each.\n")
            (OUT / "tuning.md").write_text(msg); print(msg); return 0
        r = evaluate(o.generate, test)
        line = f"| {cfg.ollama_model} | {r['json_validity']:.3f} | {r['severity_accuracy']:.3f} | {r['rubric']:.3f} | {r['avg_latency_ms']:.0f} | {r['avg_tokens']:.0f} |"
        p = OUT / "tuning.md"
        head = "# Tuning results\n\n| model | JSON validity | severity accuracy | rubric | latency ms | tokens/call |\n|---|---|---|---|---|---|\n"
        p.write_text((p.read_text() if p.exists() and "| model |" in p.read_text() else head) + line + "\n")
        print(head + line)
        return 0
    if a.action == "train":
        try:
            import tinker  # noqa: F401
        except ImportError:
            raise SystemExit("Tinker SDK not installed. `pip install tinker`, set TINKER_API_KEY, check the currently "
                             "supported model list, then adapt this stub to the current Tinker docs. Data: `slothwatch tune gen`.")
        if not os.environ.get("TINKER_API_KEY"):
            raise SystemExit("Set TINKER_API_KEY first.")
        raise SystemExit("Tinker is installed, but this repo deliberately ships no unverified training loop. "
                         "Use benchmarks/tuning/train.jsonl with the current Tinker quickstart, then evaluate with `slothwatch tune eval --model <served-name>`.")
    return 1
