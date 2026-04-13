# AI Memory Brain Gateway

Global local memory gateway for Codex, Claude, Cursor and Ollama CLI.

## What it stores
- Every event into JSONL
- Important events into Neo4j
- Optional entity/relation extraction with local Ollama model

## Setup
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
python3 -m venv .venv-memory
source .venv-memory/bin/activate
pip install -r memory_librarian/requirements.txt
cp memory_gateway/.env.example memory_gateway/.env
```

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
tail -n 20 /Users/akushniruk/home_projects/ai-memory-brain/.run/memory/events.jsonl
```

Neo4j verify:
```cypher
MATCH (g:MemoryGroup)-[:HAS_MEMORY]->(m:Memory)
RETURN m.created_at, m.source, m.kind, m.text
ORDER BY m.created_at DESC
LIMIT 20;
```
