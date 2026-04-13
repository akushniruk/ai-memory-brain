#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATEWAY_DIR="$ROOT_DIR/memory_gateway"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv-memory/bin/python}"
CODEX_BIN="${CODEX_BIN:-codex}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

if [ "$#" -lt 1 ]; then
  echo "Usage: scripts/memory_gateway/codex-with-memory.sh \"your codex prompt\""
  exit 1
fi

PROMPT="$*"
PROJECT="$(basename "$ROOT_DIR")"
CWD="$(pwd)"

"$PYTHON_BIN" "$GATEWAY_DIR/post_event.py" \
  --source codex-cli \
  --kind task_start \
  --text "$PROMPT" \
  --project "$PROJECT" \
  --cwd "$CWD" \
  --importance normal || true

set +e
"$CODEX_BIN" "$@"
EXIT_CODE=$?
set -e

"$PYTHON_BIN" "$GATEWAY_DIR/post_event.py" \
  --source codex-cli \
  --kind task_summary \
  --text "Codex task finished: $PROMPT" \
  --project "$PROJECT" \
  --cwd "$CWD" \
  --importance high \
  --tags codex,session || true

exit "$EXIT_CODE"
