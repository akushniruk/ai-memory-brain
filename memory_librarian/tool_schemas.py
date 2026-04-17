from __future__ import annotations

from typing import Any

SERVER_INFO = {
    "name": "ai-memory-brain-librarian",
    "version": "0.2.0",
}
PROTOCOL_VERSION = "2025-11-25"

FORMAT_PROP = {
    "format": {
        "type": "string",
        "enum": ["full", "compact"],
        "default": "full",
        "description": "compact truncates text and drops empty fields to save tokens",
    },
    "max_text_chars": {
        "type": "integer",
        "default": 400,
        "description": "when format=compact, max characters kept from text",
    },
}

REPO_CONTEXT_PROPS = {
    "branch": {"type": "string", "default": "", "description": "stored under metadata.branch and repo_context.branch"},
    "commit_sha": {"type": "string", "default": "", "description": "git commit sha associated with this memory"},
    "files_touched": {"type": "array", "items": {"type": "string"}, "default": []},
    "commands_run": {"type": "array", "items": {"type": "string"}, "default": []},
    "tests": {"type": "array", "items": {"type": "string"}, "default": []},
    "artifacts": {"type": "array", "items": {"type": "string"}, "default": []},
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "memory_add",
        "title": "Add Memory",
        "description": "Store a memory event (JSONL always; Neo4j when importance/kind/graph rules match).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "kind": {"type": "string", "default": "note"},
                "source": {"type": "string", "default": "agent"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                **REPO_CONTEXT_PROPS,
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "graph": {"type": "boolean", "default": False},
                "metadata": {"type": "object", "default": {}},
                "timestamp": {
                    "type": "string",
                    "description": "Optional ISO-8601 timestamp; defaults to current UTC",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory_store_summary",
        "title": "Store Task Summary",
        "description": "Persist a task_summary memory (high-signal; eligible for graph by default rules).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "source": {"type": "string", "default": "agent"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                **REPO_CONTEXT_PROPS,
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "graph": {"type": "boolean", "default": False},
                "metadata": {"type": "object", "default": {}},
                "timestamp": {
                    "type": "string",
                    "description": "Optional ISO-8601 timestamp; defaults to current UTC",
                },
            },
            "required": ["summary"],
        },
    },
    {
        "name": "memory_meeting_summary",
        "title": "Store Meeting Summary",
        "description": "Persist a meeting_summary memory event (JSONL-first with existing downstream bridge behavior).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "source": {"type": "string", "default": "agent"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                **REPO_CONTEXT_PROPS,
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "graph": {"type": "boolean", "default": False},
                "metadata": {"type": "object", "default": {}},
                "timestamp": {
                    "type": "string",
                    "description": "Optional ISO-8601 timestamp; defaults to current UTC",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory_store_structured",
        "title": "Store Structured Memory",
        "description": "Persist structured agent memory with explicit goal, changes, decisions, validation, next steps, repo context, and optional failure/open-loop status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "default": "task_summary"},
                "summary": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
                "goal": {"type": "string", "default": ""},
                "changes": {"type": "string", "default": ""},
                "decision": {"type": "string", "default": ""},
                "why": {"type": "string", "default": ""},
                "validation": {"type": "string", "default": ""},
                "next_step": {"type": "string", "default": ""},
                "risk": {"type": "string", "default": ""},
                "status": {"type": "string", "default": ""},
                "source": {"type": "string", "default": "agent"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                **REPO_CONTEXT_PROPS,
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "graph": {"type": "boolean", "default": False},
                "metadata": {"type": "object", "default": {}},
                "timestamp": {"type": "string", "description": "Optional ISO-8601 timestamp; defaults to current UTC"},
            },
        },
    },
    {
        "name": "memory_store_failed_attempt",
        "title": "Store Failed Attempt",
        "description": "Persist a failed_attempt memory with what was tried, why it failed, what to avoid, and what to try next.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "default": ""},
                "goal": {"type": "string", "default": ""},
                "changes": {"type": "string", "default": ""},
                "decision": {"type": "string", "default": ""},
                "why": {"type": "string", "default": ""},
                "validation": {"type": "string", "default": ""},
                "next_step": {"type": "string", "default": ""},
                "risk": {"type": "string", "default": ""},
                "summary": {"type": "string", "default": ""},
                "source": {"type": "string", "default": "agent"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                **REPO_CONTEXT_PROPS,
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "high"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "graph": {"type": "boolean", "default": True},
                "metadata": {"type": "object", "default": {}},
                "timestamp": {"type": "string", "description": "Optional ISO-8601 timestamp; defaults to current UTC"},
            },
        },
    },
    {
        "name": "memory_open_loop_add",
        "title": "Add Open Loop",
        "description": "Create an unresolved task, blocker, or follow-up item that agents can resume later.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "note": {"type": "string", "default": ""},
                "loop_id": {"type": "string", "default": ""},
                "status": {"type": "string", "default": "open"},
                "next_step": {"type": "string", "default": ""},
                "risk": {"type": "string", "default": ""},
                "source": {"type": "string", "default": "agent"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                **REPO_CONTEXT_PROPS,
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "high"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "graph": {"type": "boolean", "default": True},
                "metadata": {"type": "object", "default": {}},
                "timestamp": {"type": "string", "description": "Optional ISO-8601 timestamp; defaults to current UTC"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "memory_open_loop_update",
        "title": "Update Open Loop",
        "description": "Change the status or next step for an existing open loop using a stable loop_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "loop_id": {"type": "string"},
                "title": {"type": "string", "default": ""},
                "note": {"type": "string", "default": ""},
                "status": {"type": "string", "default": "open"},
                "next_step": {"type": "string", "default": ""},
                "risk": {"type": "string", "default": ""},
                "source": {"type": "string", "default": "agent"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                **REPO_CONTEXT_PROPS,
                "importance": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "graph": {"type": "boolean", "default": True},
                "metadata": {"type": "object", "default": {}},
                "timestamp": {"type": "string", "description": "Optional ISO-8601 timestamp; defaults to current UTC"},
            },
            "required": ["loop_id"],
        },
    },
    {
        "name": "memory_open_loops",
        "title": "Open Loops",
        "description": "List unresolved or historical open loops reconstructed from JSONL memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "status": {"type": "string", "default": "", "description": "open, blocked, in_progress, resolved, abandoned, superseded"},
                "limit": {"type": "integer", "default": 20},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_promote_canon",
        "title": "Promote Canon",
        "description": "Promote an existing memory event into stable canon without mutating the original JSONL record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
                "kind": {"type": "string", "default": "project_fact"},
                "note": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "memory_mark_superseded",
        "title": "Mark Superseded",
        "description": "Non-destructively mark an older memory event as superseded by a newer one so retrieval can hide stale context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "old_event_id": {"type": "string"},
                "new_event_id": {"type": "string", "default": ""},
                "reason": {"type": "string", "default": ""},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["old_event_id"],
        },
    },
    {
        "name": "memory_start_session",
        "title": "Start Session",
        "description": "Bootstrap an agent session with recent work, canon, open loops, unfinished threads, task context, timeline, and execution hints.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "query": {"type": "string", "default": ""},
                "file_paths": {"type": "array", "items": {"type": "string"}, "default": []},
                "limit": {"type": "integer", "default": 8},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_task_context",
        "title": "Task Context",
        "description": "Retrieve task-relevant memories using text, file overlap, validations, and negative-memory boosts instead of plain text search only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "file_paths": {"type": "array", "items": {"type": "string"}, "default": []},
                "limit": {"type": "integer", "default": 10},
                **FORMAT_PROP,
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_project_canon",
        "title": "Project Canon",
        "description": "Return the promoted operational constitution for a project: preferences, decisions, project facts, and identity memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 12},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_machine_context",
        "title": "Machine Context",
        "description": "Return stable local machine/setup context for a project, including canon and setup-related memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 12},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_execution_hints",
        "title": "Execution Hints",
        "description": "Return recent commands, tests, and warnings so agents can act with fewer repeated mistakes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 8},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_timeline",
        "title": "Timeline",
        "description": "Summarize what changed over time for a project or cwd using JSONL as the canonical source.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "since": {"type": "string", "default": "", "description": "Optional ISO-8601 lower time bound."},
                "until": {"type": "string", "default": "", "description": "Optional ISO-8601 upper time bound."},
                "days": {"type": "integer", "default": 7},
                "limit": {"type": "integer", "default": 30},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_quality_report",
        "title": "Memory Quality Report",
        "description": "Lint memory quality for duplicates, missing validation/next step, and bad loop records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 100},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_cleanup_candidates",
        "title": "Cleanup Candidates",
        "description": "Suggest likely duplicate or supersedable memories for manual cleanup without mutating history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "cwd": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 20},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_vault_status",
        "title": "Vault Status",
        "description": "Report vault bridge health and queue counts.",
        "inputSchema": {"type": "object", "properties": {**FORMAT_PROP}},
    },
    {
        "name": "memory_postgres_status",
        "title": "Postgres Status",
        "description": "Check Postgres structured/index layer availability.",
        "inputSchema": {"type": "object", "properties": {**FORMAT_PROP}},
    },
    {
        "name": "memory_postgres_recent",
        "title": "Postgres Recent",
        "description": "Read recent structured events from Postgres (JSONL remains canonical).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "project": {"type": "string", "default": ""},
                "source": {"type": "string", "default": ""},
                "kind": {"type": "string", "default": ""},
                "since": {"type": "string", "default": "", "description": "ISO-8601 lower time bound."},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_postgres_review_queue",
        "title": "Postgres Review Queue",
        "description": "Read review queue rows from Postgres (JSONL/vault remain source of truth).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "default": "", "description": "pending, approved, rejected, or empty for all"},
                "limit": {"type": "integer", "default": 50},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_postgres_bridge_writes",
        "title": "Postgres Bridge Writes",
        "description": "Read bridge write provenance rows from Postgres by event id or recent history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 50},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_review_queue",
        "title": "Review Queue",
        "description": "List vault review items with optional status filter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "default": "", "description": "pending, approved, rejected, or empty for all"},
                "limit": {"type": "integer", "default": 50},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_review_approve",
        "title": "Review Approve",
        "description": "Approve a review queue item and promote it into vault targets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_key": {"type": "string"},
                "target": {"type": "string", "description": "projects, people, or references"},
                "title": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["queue_key", "target"],
        },
    },
    {
        "name": "memory_review_reject",
        "title": "Review Reject",
        "description": "Reject a review queue item with an optional reason.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_key": {"type": "string"},
                "reason": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["queue_key"],
        },
    },
    {
        "name": "memory_entity_context",
        "title": "Entity Context",
        "description": "Find entity-focused context from graph memory (if Neo4j is configured).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 8},
                **FORMAT_PROP,
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_graph_overview",
        "title": "Graph Overview",
        "description": "Summarize the current memory graph shape: counts, projects, days, and top entities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 8},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_graph_project_day",
        "title": "Project Day Graph",
        "description": "Fetch a graph neighborhood for one project on one day, including memories and linked entities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "date": {"type": "string", "description": "UTC date prefix, e.g. 2026-04-13"},
                "limit": {"type": "integer", "default": 12},
                **FORMAT_PROP,
            },
            "required": ["project", "date"],
        },
    },
    {
        "name": "memory_today_graph",
        "title": "Today Graph",
        "description": "Fetch today's graph neighborhoods. Optionally scope to one project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "date": {"type": "string", "default": "", "description": "Optional override; defaults to current UTC date."},
                "limit": {"type": "integer", "default": 12},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_brain_health",
        "title": "Brain Health",
        "description": "Report graph coverage, helper status, and missing project-day neighborhoods.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 8},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_brain_doctor",
        "title": "Brain Doctor",
        "description": "Run an end-to-end local health check across gateway, launch agent, storage, and helper.",
        "inputSchema": {"type": "object", "properties": {**FORMAT_PROP}},
    },
    {
        "name": "memory_compact_day",
        "title": "Compact Day",
        "description": "Build a compact per-day capsule from JSONL for fast recall.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "UTC date prefix, e.g. 2026-04-17"},
                "project": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["date"],
        },
    },
    {
        "name": "memory_entity_hygiene",
        "title": "Entity Hygiene",
        "description": "Inspect duplicate entity clusters and repair missing graph project-day neighborhoods when possible.",
        "inputSchema": {"type": "object", "properties": {**FORMAT_PROP}},
    },
    {
        "name": "memory_today_summary",
        "title": "Today Summary",
        "description": "Summarize today's memories, optionally scoped to one project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "default": ""},
                "date": {"type": "string", "default": "", "description": "Optional override; defaults to current UTC date."},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_repair_graph",
        "title": "Repair Graph",
        "description": "Backfill missing project-day graph neighborhoods from the JSONL memory log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 0},
                "project": {"type": "string", "default": ""},
                "date": {"type": "string", "default": ""},
                "missing_only": {"type": "boolean", "default": True},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_daily_summary",
        "title": "Daily Summary",
        "description": "Summarize a day's memories using the local librarian model when enabled.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "UTC date prefix, e.g. 2026-04-12"},
                "project": {"type": "string", "default": ""},
                "source": {"type": "string", "default": ""},
                "kind": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["date"],
        },
    },
    {
        "name": "memory_search",
        "title": "Search Memory",
        "description": "Substring search over JSONL + Neo4j memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "project": {"type": "string", "default": ""},
                "source": {"type": "string", "default": ""},
                "kind": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_recent",
        "title": "Recent Memory",
        "description": "Most recent events from JSONL tail + Neo4j.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
                "project": {"type": "string", "default": ""},
                "source": {"type": "string", "default": ""},
                "kind": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
        },
    },
    {
        "name": "memory_by_date",
        "title": "Memory By Date",
        "description": "Events whose UTC timestamp starts with YYYY-MM-DD.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "UTC date prefix, e.g. 2026-04-05"},
                "project": {"type": "string", "default": ""},
                "source": {"type": "string", "default": ""},
                "kind": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["date"],
        },
    },
    {
        "name": "memory_get_date",
        "title": "Memory Get Date",
        "description": "Alias of memory_by_date for natural phrasing ('what happened on April 5').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "UTC date prefix, e.g. 2026-04-05"},
                "project": {"type": "string", "default": ""},
                "source": {"type": "string", "default": ""},
                "kind": {"type": "string", "default": ""},
                **FORMAT_PROP,
            },
            "required": ["date"],
        },
    },
    {
        "name": "memory_project_context",
        "title": "Project Context",
        "description": "Compact bundle: recent + important for a project, plus graph slice.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "limit": {"type": "integer", "default": 12},
                **FORMAT_PROP,
            },
            "required": ["project"],
        },
    },
]
