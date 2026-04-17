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
BRANCH="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
COMMIT_SHA="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true)"
CHANGED_FILES="$(git -C "$ROOT_DIR" diff --name-only HEAD 2>/dev/null | paste -sd, - || true)"
COMMAND_LINE="codex $*"

"$PYTHON_BIN" "$GATEWAY_DIR/post_event.py" \
  --source codex-cli \
  --kind task_start \
  --text "$PROMPT" \
  --project "$PROJECT" \
  --cwd "$CWD" \
  --branch "$BRANCH" \
  --commit-sha "$COMMIT_SHA" \
  --files-touched "$CHANGED_FILES" \
  --commands-run "$COMMAND_LINE" \
  --importance normal || true

set +e
"$CODEX_BIN" "$@"
EXIT_CODE=$?
set -e

RESULT_KIND="task_summary"
RISK_TEXT=""
CHANGES_TEXT="Codex wrapper session finished with exit code $EXIT_CODE."
VALIDATION_TEXT="CLI process exited with code $EXIT_CODE."
DECISION_TEXT="Capture wrapper-level session summary for later recall."
NEXT_STEP_TEXT=""

if [ "$EXIT_CODE" -ne 0 ]; then
  RESULT_KIND="failed_attempt"
  CHANGES_TEXT="Codex wrapper session failed with exit code $EXIT_CODE."
  DECISION_TEXT="Treat non-zero wrapper exits as negative memory so future sessions can avoid repeating the same failed attempt blindly."
  RISK_TEXT="Inspect terminal output for failure details."
  NEXT_STEP_TEXT="Review the failing command output and adjust the next attempt."
fi

"$PYTHON_BIN" "$GATEWAY_DIR/post_event.py" \
  --source codex-cli \
  --kind "$RESULT_KIND" \
  --project "$PROJECT" \
  --cwd "$CWD" \
  --importance high \
  --branch "$BRANCH" \
  --commit-sha "$COMMIT_SHA" \
  --files-touched "$CHANGED_FILES" \
  --commands-run "$COMMAND_LINE" \
  --goal "$PROMPT" \
  --changes "$CHANGES_TEXT" \
  --decision "$DECISION_TEXT" \
  --validation "$VALIDATION_TEXT" \
  --next-step "$NEXT_STEP_TEXT" \
  --risk "$RISK_TEXT" \
  --tags codex,session || true

exit "$EXIT_CODE"
