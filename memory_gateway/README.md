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
