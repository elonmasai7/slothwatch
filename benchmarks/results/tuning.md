# Optional model tuning results

Dataset: 1200 synthetic-only examples (1020 train, 180 holdout; seed 7341).

## Reference baseline

| Method | JSON validity | Severity accuracy | Verdict clarity | Average latency | Tokens | Cost |
|---|---:|---:|---:|---:|---|---|
| measured deterministic rule/template baseline (not a model call) | 1.000 | 1.000 | 1.000 | 0.019 ms | 13586 whitespace-estimated total | $0.00 |

## Tuned model

Status: not run; requires --train and TINKER_API_KEY.

## Baseline versus tuned comparison

| Method | JSON validity | Severity accuracy | Verdict clarity | Average latency | Tokens | Cost |
|---|---:|---:|---:|---:|---|---|
| Deterministic rule/template baseline | 1.000 | 1.000 | 1.000 | 0.019 ms | 13586 whitespace-estimated total | $0.00 |
| Tuned model | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

The tuned row is intentionally marked NOT RUN until the user explicitly starts hosted training with `--train` and a valid Tinker credential; no performance gain is asserted.

No Tinker run was claimed because no explicit API credential/training request was supplied. No tuned-model performance number is fabricated.

## Model and privacy decision

Current Tinker supported-model table lists Qwen/Qwen3.5-4B and does not list a Gemma-family model. The runtime app remains local Gemma via Ollama; this is a separate opt-in training experiment.

This is a local deterministic reference, not an LLM baseline. The structured rule-reason field is available to the baseline, so the perfect score measures consistency with synthetic labels, not independent anomaly reasoning. The optional Tinker model has not been run unless the report says it was trained and evaluated.

**Privacy warning:** Tinker training is hosted. This command sends only the generated synthetic dataset and is a deliberate exception to the local-runtime network policy. Never point it at bank statements or personal data.
