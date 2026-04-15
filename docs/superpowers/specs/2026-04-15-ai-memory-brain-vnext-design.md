# AI Memory Brain vNext Design

## Summary
AI Memory Brain vNext combines two layers:

- **Operational memory**: JSONL-first event capture, optional structured serving/index state, optional graph projection
- **Curated knowledge**: Obsidian-compatible vault stored beside the event ledger in the same app-owned home

The design goal is to keep capture fast and local while making long-term knowledge easier for both humans and agents to maintain.

## Core decisions
- JSONL is the authoritative first-write path
- Runtime storage moves out of repo-local `.run` into an app-owned home
- Vault and JSONL are colocated under the same app home
- Gemma is the optional but recommended local librarian
- Postgres is the structured serving/index layer and must not sit on the hot path
- Neo4j is the optional graph projection layer and must be rebuildable
- Install profiles are `simple`, `recommended`, and `power-user`

## Storage layout
Default macOS app home:

`~/Library/Application Support/ai-memory-brain/`

Default layout:
- `memory/events.jsonl`
- `memory/logs/`
- `vault/`
- `config/`

Vault scaffold includes:
- `vault/memory/events/`
- `vault/memory/checkins/`
- `vault/memory/checkouts/`
- `vault/memory/milestones/`
- `vault/daily-notes/`
- `vault/meetings/`
- `vault/projects/`
- `vault/people/`
- `vault/templates/`

## Write path
1. Append event to JSONL immediately
2. Optionally run Gemma librarian to compress/extract local structure
3. Optionally persist structured/index state in Postgres
4. Optionally project graph neighborhoods into Neo4j

Capture must still succeed if Gemma, Postgres, or Neo4j are unavailable.

## Layer roles
### JSONL
- append-only raw ledger
- authoritative source for new events
- rebuild source for richer layers

### Gemma librarian
- local summarization/extraction/compression
- reduces frontier-model token spend
- improves privacy by processing locally
- enriches downstream Postgres and Neo4j state

### Postgres
- normalized event metadata
- promotion queue / bridge state
- install/profile config
- retrieval-serving state

### Neo4j
- project/day/entity neighborhoods
- connected recall
- rebuildable projection only

### Vault
- curated human-readable layer
- long-lived editable knowledge
- destination for selected operational-memory promotions

## Vault bridge behavior
Default bridge rules:
- auto-write:
  - daily notes
  - meeting summaries
- review-first:
  - project knowledge
  - people knowledge
  - reusable references

Bridge writes must keep provenance to the source event and remain idempotent.

## Install profiles
### Simple
- MCP server
- JSONL ledger
- vault scaffold

### Recommended
- Simple +
- local Postgres
- structured serving/index state
- vault bridge state

### Power User
- Recommended +
- Neo4j graph projection
- Ollama + Gemma librarian

## Acceptance criteria
- New events no longer default to repo-local `.run`
- App home is created automatically
- Vault scaffold is created automatically
- JSONL capture still works when advanced services are unavailable
- Install profile config is surfaced centrally
- Docs explain the three profiles and the app-home layout clearly
