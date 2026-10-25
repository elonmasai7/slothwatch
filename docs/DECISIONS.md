# Engineering decisions

## 2026-10-25 — Local-first baseline and optional integrations
- The core path uses Python's standard library, PyYAML, FastAPI and Uvicorn. Heavy or service-dependent components are optional so demo mode can run offline.
- TabPFN is optional and must not be downloaded implicitly. Recent TabPFN weights are licensed separately from the Apache-2.0 code; the current upstream README says TabPFN-3.5 is the default and its weights are non-commercial. This differs from a blanket claim that all TabPFN weights are open for commercial use. See the upstream README: https://github.com/PriorLabs/TabPFN#license
- Temporal workflow code is optional because a running Temporal service is not assumed in the demo. Local demo mode is explicitly identified rather than pretending to be durable Temporal execution.
- Local JSONL traces are the default. Sentry is opt-in and is the only configured non-local network exception when a DSN is set.

## 2026-10-25 — Optional local TabPFN and tuning boundary
- The code uses the current TabPFN 9.2.x-compatible estimator entry points (`TabPFNRegressor` with mean/quantile prediction and `TabPFNClassifier` with `predict_proba`). Exact behavior can vary with the installed point release/checkpoint; inference is wrapped in a safe fallback. References: https://docs.priorlabs.ai/capabilities/estimators and https://github.com/PriorLabs/TabPFN/releases.
- The local estimator is opt-in (`SLOTHWATCH_ENABLE_TABPFN=1`) and requires a checkpoint already on disk (`TABPFN_MODEL_PATH` or `TABPFN_MODEL_CACHE_DIR`). Hub offline flags are set before importing the estimator; the personal-data pipeline will not fetch weights on its own. The model's license is not programmatically verifiable; review https://ux.priorlabs.ai/ before use.
- Current Tinker model documentation lists `Qwen/Qwen3.5-4B` as supported and did not list a Gemma-family model during this implementation. Feature C therefore defaults to Qwen for the separately opted-in hosted training path; only generated synthetic data is allowed. Reference: https://tinker-docs.thinkingmachines.ai/ (check current supported models before training).
- The benchmark outputs in `benchmarks/results/` were generated in local template mode. The report does not claim Gemma token counts, live-model latency, a Tinker run, or a live Temporal worker-crash/resume test. Those integrations need explicit local setup and should be measured separately.


## 2026-10-25 — Integration installability and current SDK compatibility
- Added `make install-integrations` and `make install-all` so the optional Python integrations can be installed in one explicit step. Heavy model dependencies are not part of the default core install.
- Fixed the Tinker extra from `tinker>=0.34.0` to `tinker>=0.24.0,<1.0`; the official SDK's current release metadata shows version 0.24.0, and the current official quickstart uses `Qwen/Qwen3.5-4B` for LoRA training. References: https://pypi.org/project/tinker/ and https://tinker-docs.thinkingmachines.ai/tinker/quickstart/.
- Updated the Temporal SDK minimum to 1.34.0 to align with the current official Python documentation and changed `make temporal-up` to use the official `temporal server start-dev` local development service rather than the earlier SQLite auto-setup compose configuration. References: https://docs.temporal.io/develop and https://docs.temporal.io/cli.
- Confirmed the current `gemma3:4b` Ollama model tag and added an explicit `make setup-ollama` pull step. Model weights are not bundled in the ZIP. Reference: https://ollama.com/library/gemma3:4b.
- Kept TabPFN at `>=9.2.0,<10.0` and added an opt-in synthetic-data-only warmup tool. TabPFN license acceptance and commercial rights cannot be verified by the application; the current upstream project describes recent 3.5 weights as non-commercial by default. Reference: https://github.com/PriorLabs/TabPFN#license.
- `slothwatch doctor` now reports installation and readiness separately for Ollama/Gemma, TabPFN checkpoints, Temporal SDK/server, Sentry SDK/DSN and Tinker SDK/API key. It never prints secrets or attempts hosted Tinker calls.
- Sentry spans retain sanitized local attributes and include GenAI operation/model/token attributes for AI monitoring. Sentry stays disabled unless the user explicitly configures a DSN; local JSONL traces remain available.

- License flow URL correction: current TabPFN upstream setup guidance points users to https://ux.priorlabs.ai (Licenses tab), with `TABPFN_TOKEN` for headless environments. The setup docs now use that current portal rather than the older `platform.priorlabs.ai` wording. Reference: https://github.com/PriorLabs/TabPFN#installation--setup.

- Replaced the invalid `temporalio/auto-setup` + SQLite compose file with the current upstream development-server image pattern (`temporalio/temporal:latest`, `server start-dev`, local file storage). Both host CLI and Docker Compose setup paths are documented; mapped ports bind to `127.0.0.1`. Reference: https://github.com/temporalio/samples-server/blob/main/compose/docker-compose-dev.yml.
- Adjusted the TabPFN preparation instructions for current TabPFN-3.5 unified `.safetensors` weights, which can serve classification and regression, while retaining role-specific checkpoint paths for other layouts. Reference: https://github.com/PriorLabs/TabPFN.
- The Tinker tuning baseline now explicitly states that the structured rule-reason field makes the synthetic reference deterministic; its perfect synthetic score is not evidence of independent anomaly-reasoning quality. Machine-specific scratch paths are omitted from the shareable tuning report.


## 2026-10-25 — PEP 668-safe installation and Temporal dependency correction
- User testing showed that the previous `make install-integrations` called the host `python3 -m pip` directly, triggering Debian/Ubuntu's externally-managed-environment protection when no virtual environment was active. Make targets now create `.venv` first and use its interpreter for all Python installs and app/test commands; individual install targets support isolated troubleshooting. Direct `slothwatch` commands require `source .venv/bin/activate`, while `make doctor` works without activation.
- Added `make install-ollama` as an explicit Linux system-service installation target using the official Ollama installer, separate from `make setup-ollama`, which only pulls the model. References: https://ollama.com/download/linux and https://packaging.python.org/en/latest/specifications/externally-managed-environments/.
- Corrected the Temporal Python SDK lower bound from `>=1.34.0` to `>=1.33.0`: official PyPI metadata listed 1.33.0 as the latest published release during this correction, meaning the previous minimum made the extras unsatisfiable. Reference: https://pypi.org/project/temporalio/.
