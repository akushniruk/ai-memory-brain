#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_APP_HOME="$HOME/Library/Application Support/ai-memory-brain"

PROFILE="simple"
APP_HOME="${AI_MEMORY_BRAIN_HOME:-$DEFAULT_APP_HOME}"
POSTGRES_DSN="${POSTGRES_DSN:-}"
NEO4J_URI="${NEO4J_URI:-}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-}"
HELPER_MODEL="${MEMORY_HELPER_MODEL:-gemma4:e2b}"
HELPER_BASE_URL="${MEMORY_HELPER_BASE_URL:-http://127.0.0.1:11434/api/generate}"
HELPER_TIMEOUT_SEC="${MEMORY_HELPER_TIMEOUT_SEC:-15}"

usage() {
  cat <<'EOF'
Usage:
  memory_gateway/install-profile.sh --profile simple|recommended|power-user [options]

Options:
  --profile <name>           Install profile (required)
  --app-home <path>          App home root (default macOS app support path)
  --postgres-dsn <dsn>       Postgres DSN (recommended or power-user)
  --neo4j-uri <uri>          Neo4j URI (power-user)
  --neo4j-user <user>        Neo4j user (power-user, default neo4j)
  --neo4j-password <pass>    Neo4j password (power-user)
  --helper-model <model>     Ollama model name (power-user, default gemma4:e2b)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --app-home) APP_HOME="$2"; shift 2 ;;
    --postgres-dsn) POSTGRES_DSN="$2"; shift 2 ;;
    --neo4j-uri) NEO4J_URI="$2"; shift 2 ;;
    --neo4j-user) NEO4J_USER="$2"; shift 2 ;;
    --neo4j-password) NEO4J_PASSWORD="$2"; shift 2 ;;
    --helper-model) HELPER_MODEL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$PROFILE" != "simple" && "$PROFILE" != "recommended" && "$PROFILE" != "power-user" ]]; then
  echo "Invalid profile: $PROFILE" >&2
  usage
  exit 1
fi

mkdir -p "$APP_HOME/memory/logs" "$APP_HOME/config" "$APP_HOME/vault"

MEMORY_HELPER_ENABLED="0"
if [[ "$PROFILE" == "recommended" || "$PROFILE" == "power-user" ]]; then
  if [[ -z "$POSTGRES_DSN" ]]; then
    POSTGRES_DSN="postgresql://localhost/ai_memory_brain"
    echo "[install-profile] POSTGRES_DSN not provided, using default: $POSTGRES_DSN" >&2
  fi
fi
if [[ "$PROFILE" == "power-user" ]]; then
  if [[ -z "$NEO4J_URI" ]]; then
    NEO4J_URI="bolt://localhost:7687"
    echo "[install-profile] NEO4J_URI not provided, using default: $NEO4J_URI" >&2
  fi
  MEMORY_HELPER_ENABLED="1"
fi

CONFIG_PATH="$APP_HOME/config/memory.env"
cat > "$CONFIG_PATH" <<EOF
AI_MEMORY_BRAIN_HOME=$APP_HOME
AI_MEMORY_INSTALL_PROFILE=$PROFILE
MEMORY_SERVER_HOST=127.0.0.1
MEMORY_SERVER_PORT=8765
MEMORY_GROUP_ID=personal-brain
MEMORY_LOG_PATH=$APP_HOME/memory/events.jsonl
VAULT_PATH=$APP_HOME/vault
POSTGRES_DSN=$POSTGRES_DSN
NEO4J_URI=$NEO4J_URI
NEO4J_USER=$NEO4J_USER
NEO4J_PASSWORD=$NEO4J_PASSWORD
MEMORY_HELPER_ENABLED=$MEMORY_HELPER_ENABLED
MEMORY_HELPER_MODEL=$HELPER_MODEL
MEMORY_HELPER_BASE_URL=$HELPER_BASE_URL
MEMORY_HELPER_TIMEOUT_SEC=$HELPER_TIMEOUT_SEC
EOF

echo "Installed profile: $PROFILE"
echo "Config file: $CONFIG_PATH"
echo "Next: memory_gateway/verify-profile.sh --profile $PROFILE --app-home \"$APP_HOME\""
echo "Then start: memory_gateway/start-server.sh"

