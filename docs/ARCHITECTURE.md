# SlothWatch architecture

SlothWatch is a local-first Python application. The same `core/slothwatch` interfaces power both the personal-data CLI and the synthetic demo. CSV files remain unchanged; normalized rows, forecasts, decisions, receipts, local traces and drafts live in `.slothwatch/`.

```mermaid
flowchart TD
    CSV[Read-only CSV import] --> N[Normalize merchants and detect cadence]
    N --> R0[Rung 0: Rules]
    R0 -->|exact duplicate / allow / ignore / stable cycle| S[Settled row + receipt]
    R0 -->|unsettled| R1[Rung 1: Forecast interval]
    R1 -->|inside interval| S
    R1 -->|outside interval / creep| R15[Rung 1.5: Laziness Curve]
    R15 -->|cold start / low P(approve), not high severity| W[Watch without wake-up]
    R15 -->|eligible surprise| R2[Rung 2: Gemma via local Ollama]
    R2 -->|invalid/unavailable| T[Validated template fallback]
    R2 --> H[Rung 3: Human approval gate]
    T --> H
    H -->|approve| O[Local outbox only]
    H -->|ignore / snooze| D[Record decision]
    S --> RC[Hash-chained silence receipt]
    W --> RC
    O --> RC
    D --> RC
    RC --> DB[(SQLite + daily JSON mirror)]
    DB --> UI[Local FastAPI dashboard]
    DB --> TR[Local JSONL trace]
    WF[Optional Temporal Schedule] --> ACT[Durable workflow + retryable activities]
    ACT --> N
```

## Shared interfaces

- `ingest.read_csv(path, mapping=None)` returns typed `Transaction` objects and never writes to input files.
- `normalize.normalize_transactions` gives merchant variants stable identities; `recurring.detect_recurring` identifies recurring cadence and amount stability.
- `rules.apply_rung0` is deterministic and requires no ML service.
- `forecast.forecast_rows` returns expected values, interval bounds, method and history count. With `SLOTHWATCH_ENABLE_TABPFN=1` and an existing local checkpoint, it tries the current `TabPFNRegressor` quantile API; absent weights or incompatible local inference falls back to robust median/MAD. No checkpoint download is initiated by the app.
- `learning.predict_approval` records the six required decision features. After 15 stored decisions it tries a local `TabPFNClassifier` if explicitly enabled and a compatible local checkpoint is present; otherwise it uses the transparent local decision-frequency fallback. High-amount events bypass learning suppression.
- `llm.explain_with_ollama` submits only a flagged transaction and its forecast/reason context to a loopback Ollama endpoint, validates strict JSON, retries once, then falls back to a template.
- `receipts.write_receipt` chains run receipts through SHA-256 and mirrors the records to `.slothwatch/receipts/YYYY-MM-DD.json`.
- `ladder.run_pipeline` composes those interfaces and produces the one-line quiet state or a local review draft.

## Runtime modes and durability

The demo defaults to `local-in-process`. Optional Temporal worker and Schedule sources are under `workflows/`; they retry activities and wait on an explicit workflow signal for pending actions. Stable alert IDs and SQLite `INSERT OR IGNORE` make activity replay idempotent. The local replay test covers database idempotency, but it does not claim a live Temporal worker-kill integration test unless run against a real local Temporal server.

The dashboard binds to loopback (`127.0.0.1`) only. It has no external assets or CDNs. Sentry is opt-in and is the sole configured outbound runtime exception; Tinker is an independent, explicit synthetic-data-only training command rather than part of the personal-data pipeline.
