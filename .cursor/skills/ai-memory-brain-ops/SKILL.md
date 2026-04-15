---
name: ai-memory-brain-ops
description: Repository operating defaults for AI Memory Brain tasks
---

Use this skill when working in `ai-memory-brain` to keep implementation and decisions consistent.

## Default operating policy

1) Local-first models
- Default to local Ollama/Gemma for helper/model tasks.
- Paid/high-tier providers are opt-in only, never implicit.

2) Data path priority
- JSONL remains canonical first-write durability.
- Postgres is the structured read/index layer.
- Neo4j remains optional graph projection/relationship recall.

3) No scheduler policy
- Do not add cron/scheduled background automation for vault/wiki hygiene.
- Use manual/on-demand checks (e.g. `python memory_gateway/vault_lint.py`).

4) Retrieval preference
- Prefer Postgres-backed MCP read tools for structured queries.
- Use JSONL/Neo4j tools where they are the better fit for timeline or relationship exploration.

5) Completion checklist for changes that touch behavior
- Confirm docs reflect default model posture and no-cron policy.
- Run targeted tests for edited modules.
- Ensure no regression to paid-by-default model configuration.
