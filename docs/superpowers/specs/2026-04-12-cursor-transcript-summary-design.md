# Cursor Transcript Summarization — Design

Date: 2026-04-12  
Status: Approved

## Problem

`cursor-stop-hook.py` logs only that a Cursor session ended (conversation_id + transcript_path). No summary of what was actually done. Graph gets noise, not signal.

## Goal

Every completed Cursor session writes a meaningful `task_summary` event to the graph: what was done, key decisions, open items — sourced from the transcript via the librarian (Gemma4), with rule-based fallback when librarian is offline.

## Architecture

```
Cursor session ends
       ↓
cursor-stop-hook.py
  reads transcript JSONL at transcript_path
  extracts raw turns (first user msg, last 2 assistant texts, tool names)
       ↓
  POST /summarize  →  memory_server.py
                          ↓
                    Gemma4 via Ollama
                          ↓ (offline → rule-based fallback inside server)
                    {"summary": "...", "used_llm": true/false}
       ↓  (timeout 3s — if /summarize unreachable, hook builds rule-based locally)
  POST /event  (kind=task_summary, importance=high, graph=True)
       ↓
  persist_event → JSONL + Neo4j graph + entity extraction
```

Two HTTP calls. Hook stays dumb. Server owns summarization logic. Graph always gets something.

## Components

### 1. `memory_server.py` — new `POST /summarize` endpoint

**Input:**
```json
{
  "transcript_path": "/path/to/transcript.jsonl",
  "project": "pharos",
  "cwd": "/Users/akushniruk/home_projects/pharos"
}
```

**Transcript parsing — extract:**
- First user `message.content[].text` → goal
- Last 2 assistant `message.content[].text` turns → conclusion
- All `message.content[].name` where `type=tool_use` → deduplicated tools list

**Gemma4 prompt:**
```
Summarize this Cursor agent session in 3-5 sentences.
Goal: {first_user_msg[:300]}
Last actions: {last_assistant_texts[:400]}
Tools used: {tool_names}
Write: what was done, key decisions, open items.
```

**Ollama timeout:** 5s  
**Output:** `{"summary": "...", "used_llm": true}`

**Fallback (Ollama offline):**
```
Goal: {first_user_msg[:200]}
Concluded: {last_assistant_text[:200]}
Tools: {tool_names}
```
Returns `{"summary": "...", "used_llm": false}`

### 2. `cursor-stop-hook.py` — updated

After receiving `status=completed` + `loop_count=0`:

1. Read transcript JSONL at `transcript_path` (if present)
2. Extract raw turns locally (same parser as server — shared util or inline)
3. POST to `/summarize` with 3s timeout
4. If unreachable/timeout → build rule-based summary locally
5. POST `/event` with summary text

### 3. Event schema written to graph

```json
{
  "source": "cursor-stop-hook",
  "kind": "task_summary",
  "text": "<librarian or fallback summary>",
  "project": "pharos",
  "importance": "high",
  "tags": ["cursor", "session-stop", "summarized"],
  "graph": true,
  "metadata": {
    "conversation_id": "...",
    "transcript_path": "...",
    "used_llm": true,
    "turn_count": 12
  }
}
```

`importance=high` + `graph=true` → existing `persist_event` logic triggers Gemma entity extraction automatically. No extra wiring.

## Error Handling

| Failure | Behavior |
|---|---|
| `transcript_path` missing or empty | Skip summarize. Write minimal event: `"Session ended, transcript unavailable"` |
| Transcript file not found on disk | Same as above |
| Ollama offline | Server returns rule-based summary, `used_llm=false` |
| `/summarize` timeout (3s) | Hook builds rule-based locally, still POSTs event |
| `/event` POST fails | Hook exits silently (unchanged behavior) |

No failure mode blocks Cursor. All paths write something.

## What Changes

| File | Change |
|---|---|
| `memory_gateway/memory_server.py` | Add `POST /summarize` handler + transcript parser + Gemma prompt |
| `memory_gateway/cursor-stop-hook.py` | Read transcript, call `/summarize`, fallback, set `importance=high` |
| `memory_gateway/memory_store.py` | Extract transcript parser as shared util (optional — can be inline) |

## What Does Not Change

- `persist_event` — unchanged
- Neo4j graph schema — unchanged  
- JSONL format — unchanged
- All other hooks and daily scripts — unchanged

## Success Criteria

- Completed Cursor session → `task_summary` event in graph with human-readable text
- Librarian offline → event still written with rule-based summary
- Cursor startup/shutdown not delayed by > 3s
- `used_llm` flag in metadata shows which path was taken
