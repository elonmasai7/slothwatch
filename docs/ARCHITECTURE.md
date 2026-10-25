# Architecture

```mermaid
flowchart TD
  CSV[Bank CSV folder] --> I[ingest + normalize + recurring detection]
  I --> R0{Rung 0 rules}
  R0 -- settled --> S[(Receipt: slept)]
  R0 -- duplicate / large new --> C
  R0 -- recurring, changed --> R1{Rung 1 TabPFN interval}
  R1 -- inside --> S
  R1 -- outside / creeping --> C{Rung 1.5 does this person care?}
  C -- hushed --> W[(logged: watch)]
  C -- passes or cold start --> G[Rung 2 Gemma, local]
  G -- ignore --> W
  G -- watch --> A[(alert, no ping)]
  G -- act --> H[Rung 3 human approve / ignore / snooze]
  H -- approve --> D[draft saved to outbox, never sent]
  H -- decision --> L[(labelled decision)] --> C
```

Temporal wraps the nightly run: `SlothNightly` workflow = pipeline activity (idempotent per window) -> notify activity ->
durable wait (7d) for an approve/ignore/snooze signal -> decision activity. Every run writes a hash-chained receipt.

| Module | Job |
|---|---|
| `ingest.py` | CSV quirks, never mutates the source |
| `normalize.py` | merchant cleaning (conservative grouping), categories, recurring detection |
| `rules.py` | Rung 0 |
| `forecast.py` | Rung 1: TabPFN or robust median+MAD, creeping detector |
| `care.py` | Rung 1.5: learns from your decisions (TabPFN classifier or kNN) |
| `llm.py` | Rung 2: Ollama/Gemma, strict JSON, retry, labelled template fallback |
| `ladder.py` | pure orchestration of the rungs |
| `runner.py` | persistence, idempotency, receipts, decisions |
| `receipts.py`, `canary.py` | trust layer |
| `workflows.py` | Temporal |
| `dashboard.py` / `.html` | localhost UI |
| `demo/` | generator, scenarios, diary, tour, 12-week simulation |
| `bench.py`, `tuning/` | benchmarks and Rung-2 tuning harness |
