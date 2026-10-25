SYSTEM_PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
VENV_BIN := $(VENV)/bin
PYTHONPATH := core:.
export PYTHONPATH

.PHONY: create-venv install install-integrations install-all install-tabpfn install-temporal install-sentry install-tinker install-ollama test lint demo real dashboard privacy-check benchmark generate-demo setup-ollama tabpfn-prepare integrations-doctor doctor temporal-up temporal-compose-up temporal-compose-down temporal-worker temporal-schedule temporal-crash-demo tuning test-all benchmark-ollama

# PEP 668-safe setup: all Python packages are installed inside the project .venv.
create-venv:
	@if [ ! -x "$(VENV_PYTHON)" ]; then \
		echo "Creating project virtual environment at $(VENV) ..."; \
		$(SYSTEM_PYTHON) -m venv "$(VENV)" || { \
			echo "Could not create a virtual environment." >&2; \
			echo "On Ubuntu/Debian run: sudo apt update && sudo apt install -y python3-venv python3-full" >&2; \
			exit 2; \
		}; \
	fi
	@$(VENV_PYTHON) -m pip --version >/dev/null 2>&1 || { \
		echo "pip is missing from $(VENV). On Ubuntu/Debian run: sudo apt install -y python3-venv python3-full" >&2; \
		exit 2; \
	}
	@echo "Python environment ready: $(VENV)"

# Base install includes test tools; safe to repeat.
install: create-venv
	$(VENV_PYTHON) -m pip install -e ".[test]"
	@echo "Installed SlothWatch in $(VENV). Activate with: source $(VENV)/bin/activate"

# Install optional integrations into .venv; TabPFN may install PyTorch and use substantial disk space.
install-integrations: create-venv
	$(VENV_PYTHON) -m pip install -e ".[integrations]"
	@echo "Integrations installed in $(VENV). Activate with: source $(VENV)/bin/activate"

install-all: create-venv
	$(VENV_PYTHON) -m pip install -e ".[all]"
	@echo "All Python extras installed in $(VENV). Activate with: source $(VENV)/bin/activate"

# Granular targets help diagnose an optional integration without installing every extra.
install-tabpfn: create-venv
	$(VENV_PYTHON) -m pip install -e ".[tabpfn]"

install-temporal: create-venv
	$(VENV_PYTHON) -m pip install -e ".[temporal]"

install-sentry: create-venv
	$(VENV_PYTHON) -m pip install -e ".[sentry]"

install-tinker: create-venv
	$(VENV_PYTHON) -m pip install -e ".[tinker]"

# Installs the official Ollama system service on Linux. Separate from Python packages.
install-ollama:
	bash scripts/install_ollama.sh

# The model pull only runs after Ollama itself is installed and available on PATH.
setup-ollama:
	bash scripts/setup_ollama.sh

# Uses the project venv and gives a clear message if TabPFN package/weights are missing.
tabpfn-prepare: create-venv
	$(VENV_PYTHON) scripts/prepare_tabpfn.py

# Doctor is callable without relying on the shell's PATH to contain the .venv entry point.
doctor: install
	$(VENV_PYTHON) -m slothwatch.cli doctor

integrations-doctor: doctor

# Python-running targets install the lightweight project/test dependencies into .venv first.
test: install
	$(VENV_PYTHON) -m pytest -q

lint: create-venv
	@if command -v ruff >/dev/null 2>&1; then ruff check core workflows benchmarks tests; else echo 'ruff not installed; running syntax compilation instead (install ruff for lint rules)'; $(VENV_PYTHON) -m compileall -q core workflows benchmarks tests; fi

demo: install
	$(VENV_PYTHON) -m slothwatch.demo_cli run --scenario clean-month
	$(VENV_PYTHON) -m slothwatch.dashboard --open

real: install
	$(VENV_PYTHON) -m slothwatch.cli run
	$(VENV_PYTHON) -m slothwatch.dashboard --open

dashboard: install
	$(VENV_PYTHON) -m slothwatch.dashboard --open

privacy-check: install
	env -u SENTRY_DSN PYTHONPATH=$(PYTHONPATH) $(VENV_PYTHON) -m pytest -q tests/test_privacy.py

benchmark: install
	$(VENV_PYTHON) benchmarks/run.py

generate-demo: install
	$(VENV_PYTHON) -m slothwatch.demo_cli generate --scenario everything-wrong --out data/demo

# Temporal CLI creates a local development service; this is not a production deployment.
temporal-up:
	bash scripts/start_temporal_dev.sh

# Docker alternative to installing the Temporal CLI; UI at http://127.0.0.1:8233.
temporal-compose-up:
	docker compose up -d temporal
	@echo "Temporal dev server started. UI: http://127.0.0.1:8233"

temporal-compose-down:
	docker compose stop temporal

# These targets install their SDK extra before starting the worker or schedule.
temporal-worker: install-temporal
	$(VENV_PYTHON) -m workflows.worker

temporal-schedule: install-temporal
	$(VENV_PYTHON) -m workflows.schedule

# This is an idempotent replay/crash-boundary simulation, not a substitute for testing
# an actual kill/restart against a running Temporal server.
temporal-crash-demo: install
	$(VENV_PYTHON) -m pytest -q tests/test_ladder.py::test_replay_is_idempotent_and_receipt_tampering_detected

tuning: install
	$(VENV_PYTHON) -m tuning.run

test-all: lint test privacy-check

# Explicitly sends only synthetic benchmark rows to loopback Ollama for model latency/token measurements.
benchmark-ollama: install
	$(VENV_PYTHON) benchmarks/run.py --use-ollama
