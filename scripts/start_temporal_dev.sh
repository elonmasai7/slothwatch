#!/usr/bin/env bash
set -euo pipefail
if ! command -v temporal >/dev/null 2>&1; then
  cat >&2 <<EOF
Temporal CLI is not installed or is not on PATH.
Install the official Temporal CLI for your operating system:
https://docs.temporal.io/cli#install-and-configure-the-cli

After installation, run `make temporal-up` in this terminal. The local dev service
keeps running in the foreground; use another terminal for `make temporal-worker`.
EOF
  exit 2
fi
printf 'Starting the official local Temporal dev service. Press Ctrl-C to stop it.\n'
exec temporal server start-dev
