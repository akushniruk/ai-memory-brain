#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/ai-memory-brain"
CONFIG_PATH="$CONFIG_DIR/config.env"
MARKER="AI_MEMORY_BRAIN_WRAPPER"
APP_HOME="${AI_MEMORY_BRAIN_HOME:-$HOME/Library/Application Support/ai-memory-brain}"
PROFILE="${AI_MEMORY_INSTALL_PROFILE:-}"
APP_HOME_CONFIG="$APP_HOME/config/memory.env"

if [ -z "$PROFILE" ] && [ -f "$APP_HOME_CONFIG" ]; then
  PROFILE="$(grep '^AI_MEMORY_INSTALL_PROFILE=' "$APP_HOME_CONFIG" 2>/dev/null | sed 's/^[^=]*="//; s/"$//')"
fi

if [ -z "$PROFILE" ]; then
  PROFILE="simple"
fi

mkdir -p "$BIN_DIR" "$CONFIG_DIR"
mkdir -p "$APP_HOME/memory/logs" "$APP_HOME/config" "$APP_HOME/vault"

find_real_binary() {
  local tool="$1"
  local real_key=""
  local current=""
  local preserved=""

  case "$tool" in
    codex) real_key="REAL_CODEX" ;;
    claude) real_key="REAL_CLAUDE" ;;
    ollama) real_key="REAL_OLLAMA" ;;
    *) echo "Unsupported tool $tool" >&2; return 1 ;;
  esac

  if [ -x "$BIN_DIR/$tool" ] && grep -q "$MARKER" "$BIN_DIR/$tool" 2>/dev/null; then
    current="$(grep "^${real_key}=" "$CONFIG_PATH" 2>/dev/null | sed 's/^[^=]*="//; s/"$//')"
    if [ -n "$current" ] && [ "$current" != "$BIN_DIR/$tool" ]; then
      printf '%s' "$current"
      return 0
    fi
  fi

  preserved="$BIN_DIR/$tool.ai-memory-brain-real"
  if [ -x "$preserved" ]; then
    printf '%s' "$preserved"
    return 0
  fi

  current="$(PATH="$(printf '%s' "$PATH" | awk -v bin="$BIN_DIR" 'BEGIN { RS=":"; ORS=":" } $0 != bin { print }' | sed 's/:$//')" command -v "$tool" || true)"
  if [ -z "$current" ]; then
    return 1
  fi

  if [ "$current" = "$BIN_DIR/$tool" ] && ! grep -q "$MARKER" "$current" 2>/dev/null; then
    mv "$current" "$preserved"
    printf '%s' "$preserved"
    return 0
  fi

  printf '%s' "$current"
}

REAL_CODEX="$(find_real_binary codex || true)"
REAL_CLAUDE="$(find_real_binary claude || true)"
REAL_OLLAMA="$(find_real_binary ollama || true)"

cat > "$CONFIG_PATH" <<EOF
ROOT_DIR="$ROOT_DIR"
PYTHON_BIN="$ROOT_DIR/.venv-memory/bin/python"
REAL_CODEX="$REAL_CODEX"
REAL_CLAUDE="$REAL_CLAUDE"
REAL_OLLAMA="$REAL_OLLAMA"
AI_MEMORY_BRAIN_HOME="$APP_HOME"
AI_MEMORY_INSTALL_PROFILE="$PROFILE"
VAULT_PATH="$APP_HOME/vault"
MEMORY_SERVER_HOST="127.0.0.1"
MEMORY_SERVER_PORT="8765"
EOF

create_wrapper() {
  local tool="$1"
  local wrapper_path="$BIN_DIR/$tool"
  cat > "$wrapper_path" <<'EOF'
#!/usr/bin/env bash
# AI_MEMORY_BRAIN_WRAPPER
set -euo pipefail

CONFIG_PATH="$HOME/.config/ai-memory-brain/config.env"
if [ ! -f "$CONFIG_PATH" ]; then
  echo "Missing config: $CONFIG_PATH" >&2
  exit 1
fi
source "$CONFIG_PATH"

TOOL_NAME="$(basename "$0")"
case "$TOOL_NAME" in
  codex) REAL_BIN="${REAL_CODEX:-}" ;;
  claude) REAL_BIN="${REAL_CLAUDE:-}" ;;
  ollama) REAL_BIN="${REAL_OLLAMA:-}" ;;
  *) echo "Unsupported wrapped tool: $TOOL_NAME" >&2; exit 1 ;;
esac

if [ -z "${REAL_BIN:-}" ] || [ ! -x "$REAL_BIN" ]; then
  exec "$TOOL_NAME" "$@"
fi

POSTER="$ROOT_DIR/memory_gateway/post_event.py"
PYTHON_TO_USE="${PYTHON_BIN:-python3}"
if [ ! -x "$PYTHON_TO_USE" ]; then
  PYTHON_TO_USE="python3"
fi

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PROJECT_NAME="$(basename "$PROJECT_ROOT")"
CURRENT_CWD="$(pwd)"
PROMPT_TEXT="${*:-interactive}"
CURRENT_BRANCH="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
CURRENT_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || true)"
CHANGED_FILES="$(git -C "$PROJECT_ROOT" diff --name-only HEAD 2>/dev/null | paste -sd, - || true)"
COMMAND_LINE="$TOOL_NAME ${*:-interactive}"

"$PYTHON_TO_USE" "$POSTER" \
  --source "$TOOL_NAME-cli" \
  --kind task_start \
  --text "$PROMPT_TEXT" \
  --project "$PROJECT_NAME" \
  --cwd "$CURRENT_CWD" \
  --importance normal \
  --branch "$CURRENT_BRANCH" \
  --commit-sha "$CURRENT_COMMIT" \
  --files-touched "$CHANGED_FILES" \
  --commands-run "$COMMAND_LINE" \
  --tags "$TOOL_NAME,session" >/dev/null 2>&1 || true

set +e
"$REAL_BIN" "$@"
EXIT_CODE=$?
set -e

RESULT_KIND="task_summary"
RISK_TEXT=""
CHANGES_TEXT="$TOOL_NAME finished with exit code $EXIT_CODE."
VALIDATION_TEXT="CLI process exited with code $EXIT_CODE."
DECISION_TEXT="Capture wrapper-level session summary for later recall."
NEXT_STEP_TEXT=""

if [ "$EXIT_CODE" -ne 0 ]; then
  RESULT_KIND="failed_attempt"
  CHANGES_TEXT="$TOOL_NAME failed with exit code $EXIT_CODE."
  DECISION_TEXT="Treat non-zero wrapper exits as negative memory so future sessions can avoid repeating the same failed attempt blindly."
  RISK_TEXT="Inspect terminal output for failure details."
  NEXT_STEP_TEXT="Review the failing command output and adjust the next attempt."
fi

"$PYTHON_TO_USE" "$POSTER" \
  --source "$TOOL_NAME-cli" \
  --kind "$RESULT_KIND" \
  --project "$PROJECT_NAME" \
  --cwd "$CURRENT_CWD" \
  --importance high \
  --branch "$CURRENT_BRANCH" \
  --commit-sha "$CURRENT_COMMIT" \
  --files-touched "$CHANGED_FILES" \
  --commands-run "$COMMAND_LINE" \
  --goal "$PROMPT_TEXT" \
  --changes "$CHANGES_TEXT" \
  --decision "$DECISION_TEXT" \
  --validation "$VALIDATION_TEXT" \
  --next-step "$NEXT_STEP_TEXT" \
  --risk "$RISK_TEXT" \
  --tags "$TOOL_NAME,session" >/dev/null 2>&1 || true

exit "$EXIT_CODE"
EOF
  chmod +x "$wrapper_path"
}

[ -n "${REAL_CODEX:-}" ] && create_wrapper codex
[ -n "${REAL_CLAUDE:-}" ] && create_wrapper claude
[ -n "${REAL_OLLAMA:-}" ] && create_wrapper ollama

echo "Installed wrappers in $BIN_DIR"
echo "Config: $CONFIG_PATH"
