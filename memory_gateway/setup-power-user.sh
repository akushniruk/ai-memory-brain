#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_HOME="${AI_MEMORY_BRAIN_HOME:-$HOME/Library/Application Support/ai-memory-brain}"
POSTGRES_DSN="${POSTGRES_DSN:-postgresql://localhost/ai_memory_brain}"
NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-}"
HELPER_MODEL="${MEMORY_HELPER_MODEL:-gemma4:e2b}"
POSTGRES_BIN_DIR="/Applications/Postgres.app/Contents/Versions/latest/bin"

usage() {
  cat <<'EOF'
Usage:
  memory_gateway/setup-power-user.sh --neo4j-password <password> [options]

Options:
  --neo4j-password <password>   Required for Neo4j auth probe.
  --neo4j-user <user>           Default: neo4j
  --neo4j-uri <uri>             Default: bolt://localhost:7687
  --postgres-dsn <dsn>          Default: postgresql://localhost/ai_memory_brain
  --helper-model <model>        Default: gemma4:e2b
  --app-home <path>             Default: ~/Library/Application Support/ai-memory-brain
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --neo4j-password) NEO4J_PASSWORD="$2"; shift 2 ;;
    --neo4j-user) NEO4J_USER="$2"; shift 2 ;;
    --neo4j-uri) NEO4J_URI="$2"; shift 2 ;;
    --postgres-dsn) POSTGRES_DSN="$2"; shift 2 ;;
    --helper-model) HELPER_MODEL="$2"; shift 2 ;;
    --app-home) APP_HOME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$NEO4J_PASSWORD" ]]; then
  echo "Missing required --neo4j-password." >&2
  usage
  exit 1
fi

ensure_postgres_ready() {
  if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
      echo "Postgres already reachable on localhost:5432"
      return 0
    fi
  fi

  if [[ ! -x "$POSTGRES_BIN_DIR/initdb" || ! -x "$POSTGRES_BIN_DIR/pg_ctl" ]]; then
    echo "Postgres.app binaries not found at $POSTGRES_BIN_DIR." >&2
    echo "Install Postgres.app or make postgres binaries available in PATH." >&2
    exit 1
  fi

  local base_dir="$HOME/Library/Application Support/Postgres"
  local data_dir=""
  mkdir -p "$base_dir"

  data_dir="$(ls -d "$base_dir"/var-* 2>/dev/null | head -n 1 || true)"
  if [[ -z "$data_dir" ]]; then
    data_dir="$base_dir/var-17"
    mkdir -p "$data_dir"
    echo "Initializing Postgres cluster: $data_dir"
    "$POSTGRES_BIN_DIR/initdb" -D "$data_dir" -U "$USER" -A trust >/dev/null
  fi

  echo "Starting Postgres cluster: $data_dir"
  "$POSTGRES_BIN_DIR/pg_ctl" \
    -D "$data_dir" \
    -l "$base_dir/postgres.log" \
    start >/dev/null || true

  local i=0
  while [[ $i -lt 20 ]]; do
    if "$POSTGRES_BIN_DIR/pg_isready" -h localhost -p 5432 >/dev/null 2>&1; then
      echo "Postgres reachable on localhost:5432"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done

  echo "Postgres did not become ready. Check log: $base_dir/postgres.log" >&2
  exit 1
}

ensure_database() {
  if ! command -v psql >/dev/null 2>&1; then
    export PATH="$POSTGRES_BIN_DIR:$PATH"
  fi
  if ! psql -h localhost -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='ai_memory_brain'" | rg -q "1"; then
    echo "Creating database ai_memory_brain"
    createdb ai_memory_brain
  else
    echo "Database ai_memory_brain already exists"
  fi
}

echo "Ensuring Python dependencies"
source "$ROOT_DIR/.venv-memory/bin/activate"
pip install -r "$ROOT_DIR/memory_librarian/requirements.txt" >/dev/null

ensure_postgres_ready
ensure_database

echo "Installing power-user profile"
"$ROOT_DIR/memory_gateway/install-profile.sh" \
  --profile power-user \
  --app-home "$APP_HOME" \
  --postgres-dsn "$POSTGRES_DSN" \
  --neo4j-uri "$NEO4J_URI" \
  --neo4j-user "$NEO4J_USER" \
  --neo4j-password "$NEO4J_PASSWORD" \
  --helper-model "$HELPER_MODEL"

echo "Verifying power-user profile"
"$ROOT_DIR/memory_gateway/verify-profile.sh" --profile power-user --app-home "$APP_HOME"

echo "Power-user setup complete."
echo "Default helper model is local Ollama '$HELPER_MODEL'; paid/high-tier models require explicit opt-in."
echo "Start gateway: memory_gateway/start-server.sh"
