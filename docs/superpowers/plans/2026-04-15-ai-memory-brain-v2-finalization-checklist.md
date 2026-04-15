# AI Memory Brain v2 Finalization Checklist

Use this as the release gate. Every checkbox must be complete before Go.

## Release readiness
- [ ] Scope frozen for v2 (no new feature work, only blockers)
- [ ] All v2-critical tests pass in CI and locally
- [ ] No open P0/P1 defects in capture, retrieval, bridge, or MCP flows
- [ ] Rollback path documented and validated (previous stable behavior recoverable)

**Acceptance gate:** `PASS` only if scope is frozen, tests are green, and no unresolved P0/P1 issues remain.

## Profile verification matrix (simple/recommended/power-user)
- [ ] **Simple:** JSONL-only flow works end-to-end (capture, readback, search)
- [ ] **Recommended:** JSONL + Postgres structured sink works end-to-end
- [ ] **Power-user:** JSONL + Postgres + Neo4j projection + optional Gemma path works end-to-end
- [ ] Setup docs and environment knobs match actual runtime behavior for all 3 profiles

**Acceptance gate:** `PASS` only if each profile has one successful clean-environment verification run.

## Data model guarantees
- [ ] JSONL remains first durable write in the event pipeline
- [ ] Postgres remains primary structured/query layer and stays downstream of JSONL
- [ ] Neo4j remains optional projection layer (never required for durability)
- [ ] Failure of Postgres or Neo4j does not break JSONL persistence

**Acceptance gate:** `PASS` only if code paths and tests prove JSONL-first durability under downstream sink failures.

## Bridge/provenance/idempotency checks
- [ ] Bridge writes include stable event provenance (source event identity retained)
- [ ] Reprocessing the same event does not create duplicate promoted artifacts
- [ ] Promotion/approval actions are idempotent and auditable
- [ ] Provenance links allow tracing from JSONL event -> bridge record -> promoted artifact

**Acceptance gate:** `PASS` only if replay and duplicate-submit tests show no double-apply behavior.

## MCP surface completion
- [ ] Required MCP tools are present for status/health/reporting surfaces
- [ ] Review queue visibility and approve/reject actions are exposed (if included in v2 scope)
- [ ] Tool responses are consistent, documented, and machine-parseable
- [ ] Error semantics are explicit and non-destructive

**Acceptance gate:** `PASS` only if MCP tools cover declared v2 operational needs and return stable schemas.

## Observability/health checks
- [ ] Health checks cover JSONL write path, bridge path, and Postgres sink status
- [ ] Logs include enough context to debug failed bridge/sink operations
- [ ] At least one operational smoke run demonstrates healthy metrics/log signals
- [ ] Degraded downstream state is visible without losing ingestion

**Acceptance gate:** `PASS` only if health/reporting clearly separates `healthy`, `degraded`, and `failed` states.

## Docs + migration notes
- [ ] Top-level docs reflect v2 architecture and profile guidance
- [ ] Migration notes describe upgrade path from pre-v2 behavior/config
- [ ] Known limitations and non-goals are clearly stated
- [ ] Operational runbook includes quick verification commands

**Acceptance gate:** `PASS` only if a new contributor can set up and verify one profile using docs alone.

## Go/No-Go signoff block

Release decision:
- [ ] `GO`
- [ ] `NO-GO`

Signoff:
- Engineering owner: ____________________  Date: __________
- Product/PM owner: _____________________  Date: __________
- Operations owner: _____________________  Date: __________

Blocking notes / remaining risks:
- _______________________________________
- _______________________________________

## Current evidence snapshot (2026-04-15)

Local validation executed in this workspace:
- `python3 -m unittest memory_gateway/test_memory_store.py memory_gateway/test_postgres_reads.py`
  - Result: `Ran 19 tests ... OK`
- `python3 -m unittest memory_librarian/test_mcp_server.py`
  - Result: `Ran 3 tests ... OK`

Feature evidence captured in code/docs:
- Wiki promotion templates upgraded with Obsidian frontmatter/tags/backlinks in `memory_gateway/downstream_sinks.py`.
- Postgres read surface added via `memory_gateway/postgres_reads.py` and MCP tools in `memory_librarian/server.py`:
  - `memory_postgres_recent`
  - `memory_postgres_review_queue`
  - `memory_postgres_bridge_writes`
- Manual no-cron vault hygiene added with `python memory_gateway/vault_lint.py`.
- Default model policy clarified in docs/scripts: local Ollama/Gemma default, paid high-tier model usage opt-in only.

Release decision (current):
- [ ] `GO`
- [x] `NO-GO`

Why NO-GO right now:
- CI status was not re-collected in this run.
- Cross-owner signoff fields are still unfilled.
