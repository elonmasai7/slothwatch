# SlothWatch benchmark results

Generated: 2026-10-25 07:20:08

> **Mode: deterministic template fallback.** Gemma/Ollama was not called. Token counts are whitespace-based estimates, local template costs are $0, and the naive baseline is a controlled all-row explainer ablation rather than measured model latency. Closed-API cost is a configurable hypothetical estimate.

Across 6 scenarios, the ladder woke the explainer 8 times versus 1658 baseline wake-ups — a 99.52% reduction in modeled explainer wake-ups.

## Scenario results

| Scenario | Rows | Ladder wakes | Naive wakes | TP | FP | Missed | Precision | Recall | Ladder wall (s) | Ladder cost estimate (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| clean-month | 276 | 0 | 276 | 0 | 0 | 0 | 1.00 | 1.00 | 0.0482 | $0.000000 |
| double-subscription | 276 | 1 | 276 | 1 | 0 | 0 | 1.00 | 1.00 | 0.0487 | $0.000032 |
| duplicate-charge | 277 | 1 | 277 | 1 | 0 | 0 | 1.00 | 1.00 | 0.0464 | $0.000032 |
| electricity-spike | 276 | 1 | 276 | 1 | 0 | 0 | 1.00 | 1.00 | 0.0646 | $0.000032 |
| creeping-increase | 276 | 1 | 276 | 1 | 0 | 0 | 1.00 | 1.00 | 0.0580 | $0.000032 |
| everything-wrong | 277 | 4 | 277 | 4 | 0 | 0 | 1.00 | 1.00 | 0.1021 | $0.000127 |

## Laziness Curve (simulated persona, not learned user data)

| Setting | Modeled explainer wake-ups | Human pings | Recall | Precision |
|---|---:|---:|---:|---:|
| Learning off | 16 | 4 | 1.00 | 0.25 |
| Learning on | 4 | 4 | 1.00 | 1.00 |

The persona simulation uses fixed preferences (ignores modest utility movement, approves clear duplicates) and is only a mechanism demonstration. It is not evidence about real users. A production classifier stays cold-started until at least 15 explicit human decisions are available; safety rails prevent suppressing large amounts.

## Measurements and caveats

- Rows, rung outcomes, detected labels and wake-ups are read from the actual local execution of the project.
- Wall time is actual elapsed local runtime for the whole pipeline. It will vary by machine and installed optional integrations.
- Input/output token counts use a documented whitespace estimate because this default benchmark did not call Gemma. Cost uses assumptions from `slothwatch.yaml` and is not comparable to actual local inference cost, which is tracked as $0.
- Detection metrics are computed against planted anomaly labels in the synthetic fixture. Any misses and false positives are displayed rather than hidden.
- To benchmark actual local inference, install/start Ollama, pull the configured Gemma model, and run `python benchmarks/run.py --use-ollama`. This sends synthetic fixture rows only; it can take materially longer than the default template-mode run. If Ollama lacks token counters, token counts fall back to whitespace estimates.
