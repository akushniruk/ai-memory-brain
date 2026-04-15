#!/usr/bin/env bash
set -euo pipefail

DEFAULT_APP_HOME="$HOME/Library/Application Support/ai-memory-brain"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="simple"
APP_HOME="${AI_MEMORY_BRAIN_HOME:-$DEFAULT_APP_HOME}"

fail() {
  echo "$1" >&2
  exit 1
}

probe_pass() {
  echo "PASS: $1"
}

probe_skip() {
  echo "SKIP: $1"
}

probe_fail() {
  fail "FAIL: $1"
}

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
  fail "Missing config file: $CONFIG_PATH
Run: memory_gateway/install-profile.sh --profile $PROFILE --app-home \"$APP_HOME\""
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
  fail "MCP server verify failed. Ensure Python deps installed and run from repo root."
fi
echo "OK: MCP server responds"

probe_postgres() {
  echo "Checking Postgres connectivity..."
  if ! python -c "import psycopg" >/dev/null 2>&1; then
    probe_skip "Postgres probe skipped (python package 'psycopg' not installed)"
    return 0
  fi

  if python - <<'PY'
import os
import psycopg

dsn = os.environ["POSTGRES_DSN"]
with psycopg.connect(dsn, connect_timeout=3) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        row = cur.fetchone()
        if not row or row[0] != 1:
            raise RuntimeError("unexpected SELECT 1 result")
PY
  then
    probe_pass "Postgres reachable and queryable"
  else
    probe_fail "Postgres probe failed (POSTGRES_DSN=$POSTGRES_DSN)"
  fi
}

probe_neo4j() {
  echo "Checking Neo4j connectivity..."
  if python - <<'PY'
import os
import socket
import sys
from urllib.parse import urlparse

uri = os.environ["NEO4J_URI"]
parsed = urlparse(uri)
host = parsed.hostname
port = parsed.port
if not host:
    raise RuntimeError(f"Cannot parse host from NEO4J_URI: {uri}")
if not port:
    if parsed.scheme in ("bolt", "neo4j", "neo4j+s", "neo4j+ssc"):
        port = 7687
    elif parsed.scheme in ("http", "https"):
        port = 443 if parsed.scheme == "https" else 7474
    else:
        raise RuntimeError(f"Unsupported NEO4J_URI scheme: {parsed.scheme}")

with socket.create_connection((host, int(port)), timeout=3):
    pass
PY
  then
    probe_pass "Neo4j TCP reachability (NEO4J_URI)"
  else
    probe_fail "Neo4j TCP probe failed (NEO4J_URI=$NEO4J_URI)"
  fi

  if python -c "import neo4j" >/dev/null 2>&1; then
    if python - <<'PY'
import os
from neo4j import GraphDatabase

uri = os.environ["NEO4J_URI"]
user = os.environ["NEO4J_USER"]
password = os.environ["NEO4J_PASSWORD"]
driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=3)
try:
    driver.verify_connectivity()
finally:
    driver.close()
PY
    then
      probe_pass "Neo4j auth probe passed"
    else
      probe_fail "Neo4j auth probe failed (NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD)"
    fi
  else
    probe_skip "Neo4j auth probe skipped (python package 'neo4j' not installed)"
  fi
}

probe_ollama() {
  echo "Checking Ollama helper endpoint..."
  local helper_base="${MEMORY_HELPER_BASE_URL:-http://127.0.0.1:11434/api/generate}"
  local probe_url
  probe_url="${helper_base%/api/generate}/api/tags"
  if curl --silent --show-error --fail --max-time 3 "$probe_url" >/dev/null; then
    probe_pass "Ollama /api/tags reachable ($probe_url)"
  else
    probe_fail "Ollama probe failed at $probe_url"
  fi
}

if [[ "$PROFILE" == "recommended" || "$PROFILE" == "power-user" ]]; then
  if [[ -z "${POSTGRES_DSN:-}" ]]; then
    fail "Recommended/Power-user require POSTGRES_DSN in config."
  fi
  echo "OK: POSTGRES_DSN configured"
  probe_postgres
fi

if [[ "$PROFILE" == "power-user" ]]; then
  if [[ -z "${NEO4J_URI:-}" || -z "${NEO4J_USER:-}" || -z "${NEO4J_PASSWORD:-}" ]]; then
    fail "Power-user requires NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD."
  fi
  if [[ "${MEMORY_HELPER_ENABLED:-0}" != "1" ]]; then
    fail "Power-user requires MEMORY_HELPER_ENABLED=1."
  fi
  if [[ -z "${MEMORY_HELPER_MODEL:-}" ]]; then
    fail "Power-user requires MEMORY_HELPER_MODEL."
  fi
  echo "OK: Neo4j and helper settings configured"
  probe_neo4j
  probe_ollama
fi

echo "Profile verification passed: $PROFILE"

