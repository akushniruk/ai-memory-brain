#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PYTHON_BIN="$ROOT_DIR/.venv-memory/bin/python"
FALLBACK_PYTHON_BIN="/Users/akushniruk/ai-router-local/.venv/bin/python"
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"

if [ ! -x "$PYTHON_BIN" ]; then
  if [ -x "$FALLBACK_PYTHON_BIN" ]; then
    PYTHON_BIN="$FALLBACK_PYTHON_BIN"
  else
    PYTHON_BIN="python3"
  fi
fi

echo "Using Python: $PYTHON_BIN"
exec "$PYTHON_BIN" "$ROOT_DIR/memory_gateway/memory_server.py"
