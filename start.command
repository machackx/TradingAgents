#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

# The inherited proxy points at an unavailable local service on this machine.
# Use the direct connection verified during setup.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export PYTHONUTF8=1

exec "$PROJECT_DIR/.venv/bin/tradingagents" "$@"
