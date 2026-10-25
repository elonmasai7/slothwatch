#!/usr/bin/env bash
set -euo pipefail

if command -v ollama >/dev/null 2>&1; then
  echo "Ollama is already installed: $(ollama --version)"
  exit 0
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This helper installs Ollama on Linux only. Use https://ollama.com/download for your OS." >&2
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required. On Ubuntu/Debian run: sudo apt install -y curl" >&2
  exit 2
fi

cat <<'MSG'
Installing Ollama from its official Linux installer.
This is a system-level service installation and may request sudo access.
Official instructions: https://ollama.com/download/linux
MSG
curl -fsSL https://ollama.com/install.sh | sh

if ! command -v ollama >/dev/null 2>&1; then
  echo "Installer finished but 'ollama' is still not on PATH. Open a new terminal and run 'ollama --version'." >&2
  exit 2
fi

echo "Ollama installed: $(ollama --version)"
echo "Next run: make setup-ollama"
