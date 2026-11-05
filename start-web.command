#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

# Same as start.command: the inherited proxy points at an unavailable local
# service on this machine, so use the direct connection.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export PYTHONUTF8=1

exec "$PROJECT_DIR/.venv/bin/python" -m webapp "$@"
