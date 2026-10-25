# Privacy

- Network calls are limited to `localhost` services: Ollama (`:11434`), Temporal (`:7233`), the dashboard (`127.0.0.1`).
  Sentry is contacted ONLY if you set `SENTRY_DSN`; traces always also go to `.slothwatch/traces/*.jsonl`.
- The Gemma prompt contains only the single flagged charge and its forecast context, never the statement.
- Verify, don't trust: `slothwatch privacy-check` runs the whole pipeline (ingest, ladder, receipts, redaction) with every
  non-local `connect()` and DNS lookup blocked and recorded; it fails if any is attempted. It is also a unit test.
  Limit: it guards Python sockets in-process; for a stronger check also run under `unshare -n` or a firewall.
- Nothing is ever sent for you. Approving an alert writes a draft to `.slothwatch/outbox/drafts/`.
- `slothwatch redact csv|export` makes a shareable copy (merchants hashed, amounts jittered ±7% per merchant).
- Receipts include merchant names and amounts of rows left alone; they are local files. Redact before sharing.
