# Post notes: benchmark-backed claims

This note was generated from `benchmarks/results/benchmark.json` written on **2026-10-25 07:20:08**. The run mode recorded in the file is `local deterministic synthetic run`. It used fictional synthetic rows and the deterministic template fallback; it did **not** call Gemma/Ollama.

## Current benchmark measurements

Across **6 scenarios and 1,658 checked rows**, SlothWatch passed **8** rows to the explainer versus **1,658** for the all-row baseline ablation, a measured reduction of **99.52%** in modeled explainer wake-ups.

The local template pipeline averaged **0.0613 seconds per scenario** (observed range 0.0464–0.1021 seconds on this run). These wall times describe this machine and the no-model fallback, not Gemma response latency.

| Scenario | Rows | Explainer wakes | Planted anomalies detected | False positives | Misses | Precision | Recall | Measured local wall (s) | Hypothetical closed-API cost (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| clean-month | 276 | 0 | 0 | 0 | 0 | 1.00 | 1.00 | 0.0482 | $0.000000 |
| double-subscription | 276 | 1 | 1 | 0 | 0 | 1.00 | 1.00 | 0.0487 | $0.000032 |
| duplicate-charge | 277 | 1 | 1 | 0 | 0 | 1.00 | 1.00 | 0.0464 | $0.000032 |
| electricity-spike | 276 | 1 | 1 | 0 | 0 | 1.00 | 1.00 | 0.0646 | $0.000032 |
| creeping-increase | 276 | 1 | 1 | 0 | 0 | 1.00 | 1.00 | 0.0580 | $0.000032 |
| everything-wrong | 277 | 4 | 4 | 0 | 0 | 1.00 | 1.00 | 0.1021 | $0.000127 |

The fixture sweep detected **8/8 planted anomalies**, with **0 false positives and 0 misses** across these six synthetic scenarios (aggregate precision and recall are both 1.00). This is a result for the known fixtures, not evidence that real bank statements will have the same accuracy.

### Open local approach versus a hypothetical closed-API approach

| Dimension | Closed API (hypothetical comparator) | SlothWatch local-first path |
|---|---|---|
| Data location | If operated as an all-row hosted API, transaction prompts leave the machine. | CSV, SQLite, receipt chain, traces and drafts stay in `.slothwatch/`; optional Ollama is loopback only. Setting a Sentry DSN or explicitly running Tinker changes the network boundary. |
| Cost per roughly 276-row scenario | Naive all-row cost estimate averaged **$0.008778** using the configurable rates in `slothwatch.yaml`. | The measured template run incurred **$0.00 model/API cost**. If the estimated ladder token counts were billed at those same assumed rates, the estimated cost averaged **$0.000042**; electricity/hardware cost is not measured. |
| Offline ability | Hosted inference requires connectivity to its provider. | Default demo works without any model service; Ollama and TabPFN work locally after manual install/model setup. Temporal is optional for durability. |
| Swappable model | Usually follows the selected provider’s available models and APIs. | Ollama endpoint/model are configurable; the Gemma explainer can fall back to a template, and TabPFN is an optional local forecast/classifier behind an explicit flag. |
| Tunable behavior | Mostly provider settings plus application-side filtering. | YAML ignore/allow lists, amount thresholds, recurrence rules, interval fallback, learning threshold, and versioned prompt are in the repo and can be changed locally. |

**Cost assumptions:** input tokens are estimated using the documented whitespace heuristic where no Ollama metrics exist; output tokens are estimated. The default benchmark does not make cloud calls. The cost column is a comparison assumption, not a bill or a measured closed-API invocation. The naive baseline preserves the ladder’s detector labels to isolate how often an explainer would be woken; its default-mode wall time is not comparable to actual LLM latency. For real local-model measurements on synthetic rows only, run `make benchmark-ollama` with loopback Ollama running and the configured Gemma model pulled.

## Laziness Curve results

| Simulated persona | Modeled explainer wakes | Human pings | Precision | Recall | Missed true anomalies |
|---|---:|---:|---:|---:|---:|
| Learning off | 16 | 4 | 0.25 | 1.00 | 0 |
| Learning on | 4 | 4 | 1.00 | 1.00 | 0 |

On this seeded 12-week simulation, learning reduced modeled explainer wakes from 16 to 4 (75%) while retaining all four planted duplicate anomalies and keeping the four human pings. The precision change (0.25 to 1.00) counts all explainer wakes as predicted anomalies in this toy simulation, including the small utility fluctuations. This is a warm-start persona simulation with 15 prior synthetic decisions, not a result learned from a real user.

## Tuning feature status

`benchmarks/results/tuning.md` records a measured deterministic rule/template baseline on 180 synthetic holdout examples. The optional hosted Tinker model was **not run**, so its quality, latency and cost remain `NOT RUN`. The baseline is not a model-to-model comparison; no improvement claim is made.

## Sloth diary skeleton

```text
Day 1: nothing.
Day 2: nothing.
Day 3: nothing.
...
Day 17: a synthetic surprise appeared; the sloth checked its interval.
Day 18: nothing.
```

## Charts

- `benchmarks/results/benchmark.png` — scenario wake-up comparison.
- `benchmarks/results/laziness_curve.png` — weekly explainer and human-ping simulation.

All figures above are read from the generated JSON benchmark output. Re-run `make benchmark` to refresh them; the elapsed timings can vary on another machine.
