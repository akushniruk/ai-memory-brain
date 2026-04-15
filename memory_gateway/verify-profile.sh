#!/usr/bin/env bash
set -euo pipefail

DEFAULT_APP_HOME="$HOME/Library/Application Support/ai-memory-brain"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="simple"
APP_HOME="${AI_MEMORY_BRAIN_HOME:-$DEFAULT_APP_HOME}"

usage() {
  cat <<'EOF'
Usage:
  memory_gateway/verify-profile.sh --profile simple|recommended|power-user [--app-home <path>]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --app-home) APP_HOME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$PROFILE" != "simple" && "$PROFILE" != "recommended" && "$PROFILE" != "power-user" ]]; then
  echo "Invalid profile: $PROFILE" >&2
  usage
  exit 1
fi

CONFIG_PATH="$APP_HOME/config/memory.env"
if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Missing config file: $CONFIG_PATH" >&2
  echo "Run: memory_gateway/install-profile.sh --profile $PROFILE --app-home \"$APP_HOME\"" >&2
  exit 1
fi

set -a
source "$CONFIG_PATH"
set +a

echo "Profile: ${AI_MEMORY_INSTALL_PROFILE:-unknown}"
echo "App home: ${AI_MEMORY_BRAIN_HOME:-$APP_HOME}"

test -d "$APP_HOME/memory" || { echo "Missing: $APP_HOME/memory" >&2; exit 1; }
test -d "$APP_HOME/vault" || { echo "Missing: $APP_HOME/vault" >&2; exit 1; }
test -d "$APP_HOME/config" || { echo "Missing: $APP_HOME/config" >&2; exit 1; }
echo "OK: app-home layout exists"

echo "Checking MCP server startup..."
MCP_OUTPUT="$(printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"verify","version":"0.0.1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | python "$ROOT_DIR/memory_librarian/server.py" 2>/dev/null || true)"
if [[ "$MCP_OUTPUT" != *'"result"'* ]]; then
  echo "MCP server verify failed. Ensure Python deps installed and run from repo root." >&2
  exit 1
fi
echo "OK: MCP server responds"

if [[ "$PROFILE" == "recommended" || "$PROFILE" == "power-user" ]]; then
  if [[ -z "${POSTGRES_DSN:-}" ]]; then
    echo "Recommended/Power-user require POSTGRES_DSN in config." >&2
    exit 1
  fi
  echo "OK: POSTGRES_DSN configured"
fi

if [[ "$PROFILE" == "power-user" ]]; then
  if [[ -z "${NEO4J_URI:-}" || -z "${NEO4J_USER:-}" || -z "${NEO4J_PASSWORD:-}" ]]; then
    echo "Power-user requires NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD." >&2
    exit 1
  fi
  if [[ "${MEMORY_HELPER_ENABLED:-0}" != "1" ]]; then
    echo "Power-user requires MEMORY_HELPER_ENABLED=1." >&2
    exit 1
  fi
  if [[ -z "${MEMORY_HELPER_MODEL:-}" ]]; then
    echo "Power-user requires MEMORY_HELPER_MODEL." >&2
    exit 1
  fi
  echo "OK: Neo4j and helper settings configured"
fi

echo "Profile verification passed: $PROFILE"

