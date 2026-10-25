#!/usr/bin/env bash
set -euo pipefail

MODEL="${SLOTHWATCH_MODEL:-gemma3:4b}"
if ! command -v ollama >/dev/null 2>&1; then
  cat >&2 <<EOF2
Ollama itself is not installed or is not on PATH.
From the SlothWatch project root run:
  make install-ollama
Then pull the configured model:
  make setup-ollama
Official instructions: https://ollama.com/download/linux
EOF2
  exit 2
fi

printf 'Ollama executable: '
ollama --version
printf 'Pulling configured local model: %s\n' "$MODEL"
ollama pull "$MODEL"
printf '\nModel pull complete. Confirm with: ollama list\n'
printf 'Then run `make doctor` or activate the project environment and run `slothwatch doctor`.\n'
printf 'Override the default model with SLOTHWATCH_MODEL.\n'
