# Memory Operator Playbook

Practical operator guide for keeping memory quality high while honoring project policy:
- local-first
- JSONL-first durability
- no scheduler/cron dependency

## Operating Principles

- JSONL is canonical: always preserve first-write durability to `memory/events.jsonl`.
- Graph, Postgres, and helper extraction are optional overlays, never the source of truth.
- Run hygiene and repair on demand from operator commands; do not rely on background schedulers.
- Tune with small changes, then re-check health and retrieval quality.

## 1) Quality Triage (daily/when recall feels off)

Start with profile and system checks:

```bash
source .venv-memory/bin/activate
memory_gateway/verify-profile.sh --profile simple
python memory_gateway/brain_doctor.py
```

If running `recommended` or `power-user`, verify that profile explicitly:

```bash
source .venv-memory/bin/activate
memory_gateway/verify-profile.sh --profile recommended
# or
memory_gateway/verify-profile.sh --profile power-user
```

Triage signals to watch:
- `brain_doctor.py` `checks` failures -> runtime/service issues first.
- `drift_checks` failures -> env/profile mismatch; fix before tuning retrieval.
- weak retrieval confidence/score metadata in memory tool responses -> retrieval tuning needed.
- repeated near-duplicate events -> dedupe tuning needed.

## 2) Dedupe Tuning

Current knobs:
- `MEMORY_DEDUPE_WINDOW_MINUTES` (default `30`)
- `MEMORY_DEDUPE_SIMILARITY_THRESHOLD` (default `0.86`)

Workflow:
1. Run baseline health:
   ```bash
   source .venv-memory/bin/activate
   python memory_gateway/brain_doctor.py
   ```
2. Adjust one knob in `memory_gateway/.env`.
3. Re-run health and inspect dedupe behavior from tool responses (`dedupe_explain`).
4. Keep changes minimal; prefer threshold tuning before expanding window.

Operator rule of thumb:
- Too many false dedupes (distinct events collapsed): raise threshold and/or reduce window.
- Too many duplicates slipping through: lower threshold slightly or increase window modestly.
- Need explicit keep-both write: set `metadata.force_store=true` for that event.

## 3) Retrieval Tuning

Use retrieval metadata emitted by memory tools (`confidence`, `score`, `score_breakdown`, `match_type`) to calibrate quality.

Fast practical loop:
1. Capture current behavior with a few representative queries.
2. If duplicates/noise dominate, tune dedupe first.
3. If recall is thin, run day compaction and entity hygiene:

```bash
source .venv-memory/bin/activate
python memory_gateway/compact_day.py --date 2026-04-17
python memory_gateway/entity_hygiene.py
```

4. Re-check query quality and confidence metadata.

Notes:
- Search already uses token-aware matching and stopword filtering; prefer cleaner event text and better summaries over aggressive knob changes.
- Keep JSONL-first semantics intact while tuning overlays.

## 4) Repair Flows

### A) Config/runtime drift repair

```bash
source .venv-memory/bin/activate
memory_gateway/verify-profile.sh --profile simple
python memory_gateway/brain_doctor.py
```

Then fix the first failing check/drift item and re-run.

### B) Graph/entity hygiene repair

```bash
source .venv-memory/bin/activate
python memory_gateway/entity_hygiene.py
python memory_gateway/compact_day.py --date 2026-04-17
python memory_gateway/brain_doctor.py
```

### C) Gateway and MCP path repair

```bash
source .venv-memory/bin/activate
memory_gateway/start-server.sh
curl http://127.0.0.1:8765/health
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.0.1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | python memory_librarian/server.py
```

If these pass, continue with retrieval/dedupe checks rather than broad resets.

## 5) No-Scheduler Ops Rhythm

- Run `brain_doctor.py` at start/end of focused maintenance sessions.
- Run `compact_day.py` once per day (manual trigger) for active projects.
- Run `entity_hygiene.py` when graph/entity quality drops or after large ingest bursts.
- Keep all automation explicit and operator-invoked; no cron requirement.
