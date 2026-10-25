# Post notes (fill every number from `benchmarks/results/`)

Closed vs open, for the "why open" section. Fill the measured cells from your own run.

| | Closed API approach | SlothWatch (open) |
|---|---|---|
| Where statements go | uploaded to a third party | never leave the machine (`privacy-check`) |
| LLM calls on a typical month | one per row (baseline: 45–47) | 0–4 (see `benchmarks/results/report.md`) |
| Cost per run | tokens × price (estimate in report) | $0 local inference |
| Works offline | no | yes |
| Swap model / change behaviour | vendor's call | `ollama_model`, prompts in `prompts/`, rules in TOML |

Diary skeleton: "Day 1: nothing. Day 2: nothing. … Day 15: duplicate grocery charge, $64.20. … Day 30: nothing."
Laziness Curve: embed `benchmarks/results/laziness_curve.svg`. Say plainly what it shows (see `laziness_curve.json`) and that the persona is simulated.
Redact (`slothwatch redact`) anything real before screenshots.
