# SlothWatch

A local-first agent for recurring personal numbers. Its preferred output is silence: **Nothing needs you. Go back to sleep.** It reads local CSV exports, applies rules and forecasts, and only drafts a human-reviewed response for flagged surprises. It never sends a message or email automatically.

> The local core/demo works without network access. Optional integrations are packaged as installable extras and checked separately by `slothwatch doctor`. OS-level tools (Ollama and the Temporal CLI), model-weight licensing, and user credentials remain explicit setup steps; they are not silently downloaded or enabled.

## Quickstart

Requires Python 3.11+. On Ubuntu/Debian, install virtual-environment support once if it is missing:

```bash
sudo apt update
sudo apt install -y python3-full python3-venv build-essential curl
```

From the repository root, the Makefile creates `.venv` and installs Python packages there; it never runs pip against the protected system Python:

```bash
make install                       # creates .venv; installs app + test dependencies
source .venv/bin/activate          # needed for direct `slothwatch ...` shell commands
make test
make demo                          # deterministic clean-month run + local dashboard
```

`make install-integrations`, `make install-all`, and the individual `make install-tabpfn`, `make install-temporal`, `make install-sentry`, and `make install-tinker` targets also create/use `.venv` automatically. If you do not want to activate the environment, use `make doctor` instead of `slothwatch doctor`. Do not use `sudo pip` or `--break-system-packages`.

The demo uses generated fictional CSV transactions and a templated explainer by default; no model download or network call is needed. `make demo` runs `clean-month` first (assertion-friendly silence line), then starts the dashboard in the foreground and attempts to open a browser. Run a scenario manually with:

```bash
slothwatch-demo run --scenario electricity-spike
slothwatch-demo run --scenario everything-wrong --fast
slothwatch-demo tour
```

Open the dashboard at `http://127.0.0.1:8765` after `make demo` (the command prints the URL; it does not require a browser opener).

## Real app

Place a bank CSV export in `data/real/`, or pass a specific file:

```bash
slothwatch run path/to/export.csv
slothwatch status
slothwatch score
slothwatch receipt --sample 3
slothwatch verify
slothwatch doctor
make real
```

The original CSV is read-only. Do not commit real exports. All local state is stored in `.slothwatch/`, which is gitignored. Column auto-detection supports common descriptions, dates, signed amounts, separate debit/credit columns, parentheses negatives, currency symbols, and thousands separators. If auto-detection is insufficient, edit `column_mapping.yaml` so each key (`date`, `description`, `amount`, `debit`, `credit`, `account`, `category`) maps to the exact CSV header.

## Optional integrations: install and enable

### Install every Python integration

From the repository root. These targets automatically create and use `.venv`, even if you have not activated it:

```bash
make install-integrations   # Temporal SDK, Sentry SDK, TabPFN and Tinker SDK
# or install those plus test dependencies in one command:
make install-all
make doctor                 # works without shell activation
source .venv/bin/activate   # optional; enables direct `slothwatch ...` commands
slothwatch doctor
```

`make install-all` and `make install-integrations` may download substantial ML dependencies for TabPFN (including PyTorch). If you want to add only one integration, use `make install-tabpfn`, `make install-temporal`, `make install-sentry`, or `make install-tinker`. Python extras do not install OS services, pull model weights, accept licenses, create a Sentry project, or create a Tinker account/key. On Debian/Ubuntu, if venv creation fails, install `python3-full python3-venv` as shown in Quickstart. Never bypass PEP 668 with `sudo pip` or `--break-system-packages`.

### Ollama + Gemma (local inference)

Install the Ollama system service using the official Linux installer, then pull the configured model:

```bash
make install-ollama             # official installer; may request sudo
make setup-ollama               # pulls SLOTHWATCH_MODEL or gemma3:4b
ollama list
make doctor                     # or `slothwatch doctor` after activating .venv
```

If you prefer manual installation, follow https://ollama.com/download/linux. If `ollama` is still not on PATH immediately afterwards, open a new terminal and check `ollama --version` before pulling the model.

`gemma3:4b` is a current Ollama model tag and requires Ollama 0.6 or later. Its download is several GB, so make sure you have sufficient disk space. The app calls the model only for flagged rows and only through a loopback HTTP endpoint. If the local service/model is unavailable, it labels and records the template fallback instead of claiming Gemma ran. Configure with `SLOTHWATCH_MODEL=gemma3:4b` and `SLOTHWATCH_OLLAMA_URL=http://127.0.0.1:11434/api/generate` when needed.

### TabPFN local weights (forecasting and optional Rung 1.5)

```bash
make install-tabpfn
# First review and accept the exact model-weight terms at https://ux.priorlabs.ai/
make tabpfn-prepare
export SLOTHWATCH_ENABLE_TABPFN=1
# Copy the real existing checkpoint path printed by the preparation step:
export TABPFN_MODEL_PATH="$HOME/.cache/tabpfn/your-tabpfn-checkpoint.safetensors"
slothwatch doctor
```

The example paths are placeholders: use the real checkpoint paths printed on your machine. Current TabPFN-3.5 weights may use one shared `.safetensors` checkpoint for both classification and regression; in that case set only `TABPFN_MODEL_PATH` to that existing file. Separate role-specific paths remain supported for older checkpoint layouts. TabPFN prepares its weights separately from the Python package; the preparer uses only four synthetic rows and may download licensed weights. Do not run that preparer until you have reviewed the license. Current TabPFN-3.5 weights are non-commercial by default, even though the code package is Apache-2.0; confirm your intended use rights. The core personal-data pipeline never downloads weights and uses the robust fallback unless explicit opt-in and a local checkpoint are present. See https://github.com/PriorLabs/TabPFN#license.

### Temporal durable workflows

Install the Python SDK and choose either the official Temporal CLI or Docker Compose for the local dev server:

```bash
make install-temporal
# Option A: install Temporal CLI for your OS: https://docs.temporal.io/cli#install-and-configure-the-cli
make temporal-up                # foreground service; local UI at http://127.0.0.1:8233
# Option B (instead of the CLI):
make temporal-compose-up         # needs Docker Engine + Docker Compose; same local UI
```

In another terminal in the same project/virtual environment:

```bash
make temporal-worker
make temporal-schedule          # in a third terminal, creates the daily schedule
slothwatch doctor
```

Enable `orchestration.use_temporal: true` in `slothwatch.yaml` (or use `SLOTHWATCH_USE_TEMPORAL=1`) only once the service is reachable. The Docker Compose option uses the official Temporal development-server image/command with persistent local file storage, and binds its ports to loopback. The worker reconnects through Temporal and uncompleted activities can be redelivered after restart. `make temporal-crash-demo` still verifies local idempotency; a live kill/restart acceptance test needs the running service and should be performed separately.

### Sentry tracing (optional, external network exception)

Create a Sentry project, install the extra with `make install-sentry`, then set `SENTRY_DSN` in your shell before launching SlothWatch. `slothwatch doctor` reports whether the SDK and DSN are both available. With no DSN (or no SDK) it remains local-only and writes `.slothwatch/traces/trace.jsonl`; with a DSN, sanitized rung spans are also sent to Sentry. Review `docs/PRIVACY.md` before enabling this outbound connection. Never put your DSN in Git.

### Tinker tuning (optional hosted training)

```bash
make install-tinker
make tuning                       # generates 1,200 deterministic synthetic examples and baseline results
export TINKER_API_KEY="..."       # only in your shell/secret manager, never commit it
PYTHONPATH=core:. python -m tuning.run --train --model Qwen/Qwen3.5-4B
```

The SDK extra accepts the current `tinker` 0.24.x-and-later release line; the prior `>=0.34.0` constraint was impossible against the official SDK release available when the fix was made. The official Tinker model catalog currently lists `Qwen/Qwen3.5-4B` as a trainable option and does not list a Gemma-family model; confirm the live catalog before running because supported models can change: https://tinker-docs.thinkingmachines.ai/tinker/models/. This is hosted training and a deliberate network exception. Only the project's generated synthetic rows are passed to Tinker; never point the tuning command at a personal CSV.

### Verify integration availability

```bash
make doctor                     # no need to activate .venv first
# or, after `source .venv/bin/activate`:
slothwatch doctor
```

The report separates Python SDK installation from the local service/model/checkpoint/credential state. It never prints token or DSN values, and it does not claim license acceptance can be verified programmatically. Run `slothwatch doctor` again after any setup step.

## Commands

`slothwatch run|watch|status|score|approve <id>|ignore <id>|snooze <id>|redact|receipt --sample 3|verify|canary|doctor`.

`slothwatch-demo run --scenario <name> [--fast]`, `slothwatch-demo tour`, and `slothwatch-demo generate` use seeded synthetic data.

## Test and benchmark

```bash
make test
make lint
make privacy-check
make benchmark
# Optional; uses the locally pulled Gemma model and can take much longer:
make benchmark-ollama
```

Benchmark reports and charts are written under `benchmarks/results/`. Numbers are generated by the local run and labeled as synthetic, not measured user outcomes. `make tuning` writes `benchmarks/results/tuning.md`; it will not claim trained-model scores unless `--train` is explicitly run with Tinker credentials. Estimated closed-API cost is only a configurable comparison assumption; no paid API is called. Detection precision/recall uses planted labels in the synthetic fixtures.

## Safety and limitations

SlothWatch is a personal-data assistant, not financial advice. Review all alerts and drafts. It never executes a cancellation, dispute, transfer or other financial action. The receipt hash chain is tamper-evident, not a digital signature: an attacker who rewrites the whole database and every receipt could recompute it. The local template model is deterministic; using Ollama is optional and may vary by local model. Important implementation boundary: TabPFN regression/classification runs only when explicitly enabled and an already-local checkpoint exists; missing weights or estimator incompatibility safely fall back to robust median/MAD intervals or a local decision-frequency estimate. The fallback and its method name are recorded per row. The Rung 1.5 decision-frequency estimator is the non-TabPFN fallback, not a claim of model inference. `make temporal-crash-demo` verifies replay idempotency locally; a true worker-kill/resume acceptance run requires the optional Temporal SDK and a running Temporal service. `docs/DEMO_SCRIPT.md`, `docs/ARCHITECTURE.md`, `docs/PRIVACY.md`, and `docs/POST_NOTES.md` contain details.
