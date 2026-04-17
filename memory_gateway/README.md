# AI Memory Brain Gateway

Global local memory gateway for Codex, Claude, Cursor and Ollama CLI.

Operator runbook: `../docs/memory-operator-playbook.md`

## What it stores
- Every event into a JSONL ledger
- Optional vault scaffold beside the ledger
- Optional structured/index state in Postgres
- Optional graph projection in Neo4j
- Optional local extraction/compression with the Gemma librarian via Ollama
- Structured agent memory fields when provided: goal, changes, decisions, validation, next step, risks, repo branch/commit, commands, tests, and touched files

Default model policy:
- MCP + local Ollama/Gemma is the default low-cost path
- paid/high-tier remote models are optional and must be explicitly configured

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
cd /path/to/ai-memory-brain
python3 -m venv .venv-memory
source .venv-memory/bin/activate
pip install -r memory_librarian/requirements.txt
cp memory_gateway/.env.example memory_gateway/.env
```

## Install profiles (end-to-end)

Simple (JSONL + vault + MCP):
```bash
cd /path/to/ai-memory-brain
memory_gateway/install-profile.sh --profile simple
memory_gateway/verify-profile.sh --profile simple
```

Recommended (Simple + Postgres structured layer):
```bash
cd /path/to/ai-memory-brain
memory_gateway/install-profile.sh \
  --profile recommended \
  --postgres-dsn postgresql://localhost/ai_memory_brain
memory_gateway/verify-profile.sh --profile recommended
```

Power-user (Recommended + Neo4j + Ollama/Gemma):
```bash
cd /path/to/ai-memory-brain
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
cd /path/to/ai-memory-brain
memory_gateway/setup-power-user.sh --neo4j-password "<your-neo4j-password>"
```

Agent prompt users can paste:
```text
Set up AI Memory Brain power-user mode in this repo:
/path/to/ai-memory-brain

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

Cost posture:
- Default profile behavior keeps helper off unless power-user mode is selected.
- In power-user mode, helper defaults to local `gemma4:e2b` via Ollama.
- If you want paid/high-tier model routing, set that explicitly in your environment and documentation for your team.

Important:
- repo-local `.run` is no longer the default runtime storage location
- JSONL remains the authoritative first-write path
- vault and JSONL are colocated in the same app home

## Start gateway
```bash
cd /path/to/ai-memory-brain
source .venv-memory/bin/activate
memory_gateway/start-server.sh
```

## Auto-start on login (macOS)
```bash
cd /path/to/ai-memory-brain
memory_gateway/install-launch-agent.sh
```

## Global wrappers (Codex, Claude, Ollama)
```bash
cd /path/to/ai-memory-brain
memory_gateway/install-cli-wrappers.sh
```

## Global Cursor integration (all projects)
```bash
cd /path/to/ai-memory-brain
memory_gateway/install-cursor-global.sh
```

This installs:
- `~/.cursor/mcp.json` entry: `ai-memory-brain`
- `~/.cursor/hooks.json` stop hook that posts structured session summaries with branch/commit context

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

Run manual vault lint (no cron):
```bash
source .venv-memory/bin/activate
python memory_gateway/vault_lint.py
```

Run brain doctor (startup + storage + retrieval health):
```bash
source .venv-memory/bin/activate
python memory_gateway/brain_doctor.py
```

Brain doctor output includes:
- `checks`: runtime availability checks (gateway, launchctl, postgres, ollama, vault bridge)
- `drift_checks`: profile/config drift checks with per-check remediation guidance
- `dedupe`: active dedupe policy parameters

Wrapper/capture behavior:
- CLI wrappers now capture task start and completion with branch, commit SHA, and executed command
- CLI wrappers now also capture the current `git diff --name-only` file set when available
- Cursor stop hook writes task-summary-shaped session memory with validation and TODO fields
- `post_event.py` can emit structured memory directly through flags instead of only raw `--text`
- structured summaries with `next_step` or `risk` automatically create an open loop for later resume
- wrapper non-zero exits are stored as `failed_attempt` so negative memory is explicit

Dedupe tuning knobs:
- `MEMORY_DEDUPE_WINDOW_MINUTES` (default `30`)
- `MEMORY_DEDUPE_SIMILARITY_THRESHOLD` (default `0.86`)

For explicit non-deduped writes, set event metadata:
- `metadata.force_store=true`

Build a daily compact capsule for faster recall:
```bash
source .venv-memory/bin/activate
python memory_gateway/compact_day.py --date 2026-04-17
```

Run manual entity hygiene and graph backfill checks:
```bash
source .venv-memory/bin/activate
python memory_gateway/entity_hygiene.py
```

Neo4j verify:
```cypher
MATCH (g:MemoryGroup)-[:HAS_MEMORY]->(m:Memory)
RETURN m.created_at, m.source, m.kind, m.text
ORDER BY m.created_at DESC
LIMIT 20;
```
