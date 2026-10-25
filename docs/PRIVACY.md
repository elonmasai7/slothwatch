# Privacy and network boundaries

## Default data flow

- Bank CSV exports are opened read-only. No original file is renamed, edited or deleted.
- SQLite database, receipts, alert drafts, outbox, and traces are under `.slothwatch/`, a gitignored directory.
- The dashboard listens on `127.0.0.1:8765`; no CDN, remote font, analytics tag, or hosted notification service is used.
- Local Ollama inference is allowed only when the configured endpoint resolves to a loopback host. The LLM prompt includes one flagged row, its forecast context and the reason—not the user's entire CSV or account history.
- With no Sentry DSN, spans are written to local `traces/*.jsonl`. Setting a Sentry DSN is an explicit opt-in to send sanitized span attributes outside the machine. Do not set it if a strict offline boundary is required.
- Temporal must be configured to a loopback endpoint. The default demo does not use a Temporal service.
- Outbox approval never sends an email, dispute, or cancellation; it writes text that the user can review and copy manually.

## Verify the local path

```bash
make privacy-check
slothwatch doctor
slothwatch verify
```

`make privacy-check` runs the pipeline under a socket guard which fails on non-local connection attempts. It deliberately sets no Sentry DSN and uses the template explainer, so it verifies the offline default path—not an opt-in external service. `doctor` refuses to probe non-loopback Ollama or Temporal endpoints. The check does not prove the operating system or every optional dependency is secure.

## Redacted sharing

`slothwatch redact` exports an anonymized JSON summary under `.slothwatch/redacted/`: merchant labels are replaced by short hashes, IDs are hashed, and amounts are jittered deterministically. Date, category and relative patterns remain useful for debugging, so review the file before publishing it.

## Tinker exception for Feature C

The optional `tuning.run --train` command uses a hosted Tinker training service and is therefore outside the personal-data runtime's local-only boundary. It generates its own fictional inputs and targets and submits only those synthetic examples. **Never pass real bank data, real merchant descriptions, or a production SQLite database into Feature C.** No Tinker request happens unless the user explicitly passes `--train` and configures credentials. The training command is optional and does not change runtime Ollama behavior by itself.

## Limits

A hash chain detects changes to receipt records unless an attacker can rewrite all records and recompute the chain. It is tamper-evident, not a digital signature. Local inference and CSV reading run with the current user's operating-system permissions. Protect the OS account and disk encryption as appropriate.
