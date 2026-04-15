# AI Memory Brain Gateway

Global local memory gateway for Codex, Claude, Cursor and Ollama CLI.

## What it stores
- Every event into a JSONL ledger
- Optional vault scaffold beside the ledger
- Optional structured/index state in Postgres
- Optional graph projection in Neo4j
- Optional local extraction/compression with the Gemma librarian via Ollama

Current bridge defaults:
- `daily_checkin` and `daily_checkout` append into `vault/daily-notes/`
- `meeting_summary` writes into `vault/meetings/`
- higher-signal knowledge candidates queue into `vault/memory/review/`

## Default storage home

macOS default:

```bash
~/Library/Application\ Support/ai-memory-brain/
```

Layout:
- `memory/events.jsonl`
- `memory/logs/`
- `vault/`
- `config/`

## Setup
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
python3 -m venv .venv-memory
source .venv-memory/bin/activate
pip install -r memory_librarian/requirements.txt
cp memory_gateway/.env.example memory_gateway/.env
```

## Install profiles (end-to-end)

Simple (JSONL + vault + MCP):
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
memory_gateway/install-profile.sh --profile simple
memory_gateway/verify-profile.sh --profile simple
```

Recommended (Simple + Postgres structured layer):
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
memory_gateway/install-profile.sh \
  --profile recommended \
  --postgres-dsn postgresql://localhost/ai_memory_brain
memory_gateway/verify-profile.sh --profile recommended
```

Power-user (Recommended + Neo4j + Ollama/Gemma):
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
memory_gateway/install-profile.sh \
  --profile power-user \
  --postgres-dsn postgresql://localhost/ai_memory_brain \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password your-password \
  --helper-model gemma4:e2b
memory_gateway/verify-profile.sh --profile power-user
```

One-command guided bootstrap for power-user (handles common setup issues):
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
memory_gateway/setup-power-user.sh --neo4j-password "<your-neo4j-password>"
```

Agent prompt users can paste:
```text
Set up AI Memory Brain power-user mode in this repo:
/Users/akushniruk/home_projects/ai-memory-brain

Run:
1) memory_gateway/setup-power-user.sh --neo4j-password "<REAL_NEO4J_PASSWORD>"
2) memory_gateway/start-server.sh
3) curl http://127.0.0.1:8765/health

Return:
- Postgres status
- Neo4j auth status
- Ollama tags endpoint status
- final gateway health JSON
```

Verify expectations and common errors by profile:
- `simple` verifies config file and app-home folders exist, then verifies MCP startup. Typical failures: `Missing config file...`, `Missing: ...`, `MCP server verify failed...`.
- `recommended` verifies everything in `simple`, requires `POSTGRES_DSN`, and actively probes Postgres (`SELECT 1`) when `psycopg` is installed. Typical failures: `Recommended/Power-user require POSTGRES_DSN...`, `FAIL: Postgres probe failed...`.
- `power-user` verifies everything in `recommended`, requires Neo4j/helper env vars, probes Neo4j TCP reachability (and auth when Python `neo4j` driver is installed), and probes Ollama at `/api/tags` with timeout. Typical failures: `Power-user requires ...`, `FAIL: Neo4j ...`, `FAIL: Ollama probe failed ...`.

Tradeoffs:
- `simple`: lowest setup complexity, no Postgres/Neo4j/helper requirements
- `recommended`: better structured retrieval/state with local Postgres
- `power-user`: richest recall and extraction, highest local dependency footprint

Important:
- repo-local `.run` is no longer the default runtime storage location
- JSONL remains the authoritative first-write path
- vault and JSONL are colocated in the same app home

## Start gateway
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
source .venv-memory/bin/activate
memory_gateway/start-server.sh
```

## Auto-start on login (macOS)
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
memory_gateway/install-launch-agent.sh
```

## Global wrappers (Codex, Claude, Ollama)
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
memory_gateway/install-cli-wrappers.sh
```

## Global Cursor integration (all projects)
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
memory_gateway/install-cursor-global.sh
```

This installs:
- `~/.cursor/mcp.json` entry: `ai-memory-brain`
- `~/.cursor/hooks.json` stop hook that posts session summaries

## Quick verify
```bash
tail -n 20 ~/Library/Application\ Support/ai-memory-brain/memory/events.jsonl
ls -la ~/Library/Application\ Support/ai-memory-brain/vault
```

Create a meeting summary event directly:
```bash
source .venv-memory/bin/activate
python memory_gateway/meeting_summary.py \
  --text "Retro: capture key decisions and follow-ups." \
  --project "ai-memory-brain" \
  --tags "meeting,retro"
```

Neo4j verify:
```cypher
MATCH (g:MemoryGroup)-[:HAS_MEMORY]->(m:Memory)
RETURN m.created_at, m.source, m.kind, m.text
ORDER BY m.created_at DESC
LIMIT 20;
```
