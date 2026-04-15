# AI Memory Brain vNext Handoff

## Current status
- Branch: `codex/app-home-vault-vnext`
- Core direction is already implemented in part:
  - JSONL-first capture is preserved
  - runtime storage moved off repo-local `.run`
  - app-home scaffold now includes colocated `memory/` and `vault/`
  - first vault bridge behavior exists
  - optional Postgres structured/index sink exists
- Focused tests are green:
  - `source .venv-memory/bin/activate && python -m unittest memory_gateway/test_memory_store.py memory_librarian/test_mcp_server.py`
  - Result: `16` tests passed
- Final release gate checklist: [2026-04-15-ai-memory-brain-v2-finalization-checklist.md](/Users/akushniruk/home_projects/ai-memory-brain/docs/superpowers/plans/2026-04-15-ai-memory-brain-v2-finalization-checklist.md)

## Locked architecture decisions
- JSONL is the authoritative first-write path
- Vault and JSONL live under the same app-owned home
- Postgres is optional and downstream only
- Neo4j is optional graph projection only
- Gemma librarian is optional but recommended
- Gemma is important because it compresses/extracts locally and saves expensive main-model tokens
- macOS-first default app home:
  - `~/Library/Application Support/ai-memory-brain/`

## Implemented so far

### 1. App-home runtime layout
- File: [runtime_layout.py](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/runtime_layout.py)
- Added centralized path/config resolution
- Default layout now includes:
  - `memory/events.jsonl`
  - `memory/logs/`
  - `vault/`
  - `config/`
- Vault scaffold now creates:
  - `vault/memory/events`
  - `vault/memory/checkins`
  - `vault/memory/checkouts`
  - `vault/memory/milestones`
  - `vault/memory/review`
  - `vault/daily-notes`
  - `vault/meetings`
  - `vault/projects`
  - `vault/people`
  - `vault/templates`

### 2. JSONL-first capture preserved
- File: [memory_store.py](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/memory_store.py)
- `persist_event()` still appends JSONL first
- Everything else is downstream best-effort

### 3. First downstream sinks
- File: [downstream_sinks.py](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/downstream_sinks.py)
- Added `sync_event_to_vault()`
- Added `persist_structured_event()`

Current vault bridge behavior:
- `daily_checkin` -> append into `vault/daily-notes/YYYY-MM-DD.md`
- `daily_checkout` -> append into `vault/daily-notes/YYYY-MM-DD.md`
- `meeting_summary` -> write a note in `vault/meetings/`
- Higher-signal knowledge kinds create review-first notes in `vault/memory/review/`

Current review-first kinds:
- `task_summary`
- `milestone`
- `decision`
- `project_fact`
- `identity`
- `preference`
- `bug`
- `fix`

### 4. Optional Postgres structured/index layer
- File: [downstream_sinks.py](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/downstream_sinks.py)
- Uses `psycopg` lazily if `POSTGRES_DSN` is set
- Creates tables on demand:
  - `memory_events`
  - `vault_bridge_writes`
  - `memory_review_queue`
- Failure is non-fatal by design

### 5. Requirements and docs
- Added `psycopg[binary]>=3.1,<4` to [requirements.txt](/Users/akushniruk/home_projects/ai-memory-brain/memory_librarian/requirements.txt)
- Updated:
  - [README.md](/Users/akushniruk/home_projects/ai-memory-brain/README.md)
  - [memory_gateway/README.md](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/README.md)
  - [memory_librarian/README.md](/Users/akushniruk/home_projects/ai-memory-brain/memory_librarian/README.md)
  - [2026-04-15-ai-memory-brain-vnext-design.md](/Users/akushniruk/home_projects/ai-memory-brain/docs/superpowers/specs/2026-04-15-ai-memory-brain-vnext-design.md)
  - [2026-04-15-ai-memory-brain-vnext-implementation.md](/Users/akushniruk/home_projects/ai-memory-brain/docs/superpowers/plans/2026-04-15-ai-memory-brain-vnext-implementation.md)

## What is still left

### 1. Real promotion workflow
Current state:
- review-first items only write markdown queue notes into `vault/memory/review/`
- there is no approval/promote flow yet

Needed next:
- add explicit promotion tooling
- support approving a review item into:
  - `vault/projects/`
  - `vault/people/`
  - `vault/references/` or equivalent if you add that folder
- make promotions idempotent
- persist promotion state so approvals are not repeated

### 2. MCP/status surfaces for new layers
Current state:
- Postgres and vault bridge are internal only
- result info is returned from `persist_event()` but not exposed in dedicated tools

Needed next:
- add MCP tools or status calls for:
  - vault status
  - bridge health
  - Postgres health
  - review queue listing
  - approve/reject promotion actions

### 3. Dedicated meeting-summary producer
Current state:
- `meeting_summary` is supported by the bridge
- no dedicated CLI or event producer currently creates it

Needed next:
- add a script or MCP helper for meeting summaries
- ensure it follows the same JSONL-first semantics

### 4. Install-flow refinement
Current state:
- profiles are documented
- app-home config/wrappers are partly updated

Needed next:
- tighten `simple`, `recommended`, `power-user` UX end-to-end
- make Recommended clearly install/configure Postgres
- make Power User clearly install/configure Neo4j + Ollama/Gemma
- verify docs and scripts are consistent with the actual code paths

### 5. Full verification pass
Current state:
- focused tests only

Needed next:
- run broader test coverage
- manually verify:
  - JSONL-only mode
  - Postgres-enabled mode
  - vault bridge writes
  - Neo4j-enabled mode still works after refactor

## Recommended next implementation order
1. Add review queue read/approve/reject APIs
2. Add promotion targets for project/people/reference notes
3. Add a dedicated `meeting_summary` producer
4. Add status/health MCP tools for vault bridge and Postgres
5. Tighten install/profile scripts and docs
6. Run a broader verification pass

## Constraints for the next agent
- Do not move Postgres onto the hot path
- Do not make Neo4j the only durable store
- Keep JSONL append first in `persist_event()`
- Keep vault and JSONL colocated in the same app home
- Keep Gemma framed as both:
  - local memory enrichment
  - token-saving local compression/librarian layer

## Suggested starting files
- [memory_store.py](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/memory_store.py)
- [downstream_sinks.py](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/downstream_sinks.py)
- [runtime_layout.py](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/runtime_layout.py)
- [test_memory_store.py](/Users/akushniruk/home_projects/ai-memory-brain/memory_gateway/test_memory_store.py)
- [README.md](/Users/akushniruk/home_projects/ai-memory-brain/README.md)

## Suggested handoff prompt
```text
Continue AI Memory Brain vNext on branch codex/app-home-vault-vnext.

Read first:
- docs/superpowers/plans/2026-04-15-ai-memory-brain-vnext-handoff.md
- docs/superpowers/specs/2026-04-15-ai-memory-brain-vnext-design.md
- docs/superpowers/plans/2026-04-15-ai-memory-brain-vnext-implementation.md

Constraints:
- JSONL must remain the first write
- Postgres must remain downstream only
- Neo4j must remain optional projection only
- vault and JSONL stay in the same app home

Next goal:
- implement the review queue / promotion workflow and expose status through MCP-friendly surfaces

Verify changes before claiming success.
```
