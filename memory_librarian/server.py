"""
stdio MCP server — memory librarian on top of memory_gateway/memory_store.

Loads Neo4j + log settings from scripts/memory_gateway/.env when present.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_GATEWAY = Path(__file__).resolve().parent.parent / "memory_gateway"
if str(_GATEWAY) not in sys.path:
    sys.path.insert(0, str(_GATEWAY))

load_dotenv(_GATEWAY / ".env")

from memory_store import (  # noqa: E402  pylint: disable=wrong-import-position
    get_brain_health,
    get_entity_context,
    get_events_by_date,
    get_graph_overview,
    get_graph_project_day,
    get_graph_recent,
    get_project_context,
    get_today_graph,
    get_today_summary,
    repair_graph,
    get_recent_events,
    persist_event,
    summarize_events_with_helper,
    search_events,
    search_graph,
)

SERVER_INFO = {
    "name": "ai-memory-brain-librarian",
    "version": "0.2.0",
}
PROTOCOL_VERSION = "2025-11-25"

_FORMAT_PROP = {
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


def _compact_event(event: dict[str, Any], *, max_text: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in event.items():
        if val in ("", [], {}, None):
            continue
        out[key] = val
    text = str(out.get("text", ""))
    if len(text) > max_text:
        out["text"] = text[: max_text - 1] + "\u2026"
    return out


def _maybe_compact_list(
    events: list[dict[str, Any]],
    fmt: str,
    *,
    max_text: int,
) -> list[dict[str, Any]]:
    if fmt != "compact":
        return events
    return [_compact_event(event, max_text=max_text) for event in events]


def _maybe_compact_payload(data: dict[str, Any], fmt: str, max_text: int) -> dict[str, Any]:
    if fmt != "compact":
        return data
    out = dict(data)
    if "raw_results" in out:
        out["raw_results"] = _maybe_compact_list(out["raw_results"], fmt, max_text=max_text)
    if "graph_results" in out:
        out["graph_results"] = _maybe_compact_list(out["graph_results"], fmt, max_text=max_text)
    if "results" in out:
        out["results"] = _maybe_compact_list(out["results"], fmt, max_text=max_text)
    if "context" in out and isinstance(out["context"], dict):
        ctx = out["context"]
        compact_ctx: dict[str, Any] = {}
        for key, val in ctx.items():
            if isinstance(val, list):
                compact_ctx[key] = _maybe_compact_list(val, fmt, max_text=max_text)
            else:
                compact_ctx[key] = val
        out["context"] = compact_ctx
    return out


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
                "branch": {"type": "string", "default": "", "description": "stored under metadata.branch"},
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
                "branch": {"type": "string", "default": ""},
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
        "name": "memory_entity_context",
        "title": "Entity Context",
        "description": "Find entity-focused context from graph memory (if Neo4j is configured).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 8},
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
            },
        },
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
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
                **_FORMAT_PROP,
            },
            "required": ["project"],
        },
    },
]


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _respond(request_id: Any, result: dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str) -> None:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if request_id is not None:
        payload["id"] = request_id
    _write(payload)


def _tool_result(data: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=True, indent=2)}],
        "structuredContent": data,
        "isError": is_error,
    }


def _merge_metadata(arguments: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(arguments.get("metadata") or {})
    branch = arguments.get("branch") or ""
    if branch:
        metadata.setdefault("branch", branch)
    return metadata


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    fmt = str(arguments.get("format", "full"))
    max_text = int(arguments.get("max_text_chars", 400))

    if name == "memory_add":
        payload: dict[str, Any] = {
            "text": arguments["text"],
            "kind": arguments.get("kind", "note"),
            "source": arguments.get("source", "agent"),
            "project": arguments.get("project", ""),
            "cwd": arguments.get("cwd", ""),
            "importance": arguments.get("importance", "normal"),
            "tags": arguments.get("tags", []),
            "graph": arguments.get("graph", False),
            "metadata": _merge_metadata(arguments),
        }
        if arguments.get("timestamp"):
            payload["timestamp"] = arguments["timestamp"]
        result = persist_event(payload)
        return _tool_result(result)

    if name == "memory_store_summary":
        payload = {
            "text": arguments["summary"],
            "kind": "task_summary",
            "source": arguments.get("source", "agent"),
            "project": arguments.get("project", ""),
            "cwd": arguments.get("cwd", ""),
            "importance": arguments.get("importance", "normal"),
            "tags": arguments.get("tags", []),
            "graph": arguments.get("graph", False),
            "metadata": _merge_metadata(arguments),
        }
        if arguments.get("timestamp"):
            payload["timestamp"] = arguments["timestamp"]
        result = persist_event(payload)
        return _tool_result(result)

    if name == "memory_search":
        query = arguments["query"]
        limit = int(arguments.get("limit", 10))
        filters = {
            "project": arguments.get("project", ""),
            "source": arguments.get("source", ""),
            "kind": arguments.get("kind", ""),
        }
        raw_results = search_events(query, limit=limit, **filters)
        graph_results = search_graph(query, limit=limit, **filters)
        payload = {"query": query, "raw_results": raw_results, "graph_results": graph_results}
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_entity_context":
        query = arguments["query"]
        limit = int(arguments.get("limit", 8))
        payload = {"query": query, "results": get_entity_context(query, limit=limit)}
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_graph_overview":
        limit = int(arguments.get("limit", 8))
        payload = {"overview": get_graph_overview(limit=limit)}
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_graph_project_day":
        project = arguments["project"]
        date = arguments["date"]
        limit = int(arguments.get("limit", 12))
        payload = {"project": project, "date": date, "neighborhood": get_graph_project_day(project, date, limit=limit)}
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_today_graph":
        project = arguments.get("project", "")
        date = arguments.get("date", "")
        limit = int(arguments.get("limit", 12))
        payload = get_today_graph(project=project, date=date, limit=limit)
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_brain_health":
        limit = int(arguments.get("limit", 8))
        payload = get_brain_health(limit=limit)
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_today_summary":
        project = arguments.get("project", "")
        date = arguments.get("date", "")
        payload = get_today_summary(project=project, date=date)
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_repair_graph":
        payload = repair_graph(
            limit=int(arguments.get("limit", 0)),
            project=arguments.get("project", ""),
            date=arguments.get("date", ""),
            missing_only=bool(arguments.get("missing_only", True)),
        )
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_daily_summary":
        date = arguments["date"]
        filters = {
            "project": arguments.get("project", ""),
            "source": arguments.get("source", ""),
            "kind": arguments.get("kind", ""),
        }
        events = get_events_by_date(date, **filters)
        settings = {}
        try:
            from memory_store import load_settings as _load_settings  # lazy to avoid cycles

            settings = _load_settings()
        except Exception:
            settings = {}
        summary = summarize_events_with_helper(date=date, events=events, settings=settings)
        payload = {"date": date, "summary": summary.get("summary", ""), "used_helper": summary.get("used_helper", False)}
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_recent":
        limit = int(arguments.get("limit", 10))
        filters = {
            "project": arguments.get("project", ""),
            "source": arguments.get("source", ""),
            "kind": arguments.get("kind", ""),
        }
        raw_results = get_recent_events(limit=limit, **filters)
        graph_results = get_graph_recent(limit=limit, **filters)
        payload = {"raw_results": raw_results, "graph_results": graph_results}
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name in ("memory_by_date", "memory_get_date"):
        date = arguments["date"]
        filters = {
            "project": arguments.get("project", ""),
            "source": arguments.get("source", ""),
            "kind": arguments.get("kind", ""),
        }
        results = get_events_by_date(date, **filters)
        payload = {"date": date, "results": results}
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_project_context":
        project = arguments["project"]
        limit = int(arguments.get("limit", 12))
        context = get_project_context(project, limit=limit)
        graph = get_graph_recent(limit=limit, project=project)
        payload = {"project": project, "context": context, "graph_results": graph}
        return _tool_result(_maybe_compact_payload(payload, fmt, max_text))

    raise KeyError(name)


def _handle_message(message: dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")

    if request_id is None and method and method.startswith("notifications/"):
        return

    params = message.get("params", {})

    if method == "initialize":
        requested_version = params.get("protocolVersion", PROTOCOL_VERSION)
        _respond(
            request_id,
            {
                "protocolVersion": requested_version,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
        return

    if method == "notifications/initialized":
        return

    if request_id is None:
        return

    if method == "ping":
        _respond(request_id, {})
        return

    if method == "tools/list":
        _respond(request_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not name:
            _respond(request_id, _tool_result({"error": "Missing tool name"}, is_error=True))
            return
        try:
            _respond(request_id, _call_tool(name, arguments))
        except KeyError:
            _error(request_id, -32601, f"Unknown tool: {name}")
        except Exception as exc:  # pragma: no cover - runtime guard
            _respond(request_id, _tool_result({"error": str(exc)}, is_error=True))
        return

    _error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _error(None, -32700, f"Parse error: {exc}")
            continue

        if isinstance(message, list):
            for item in message:
                _handle_message(item)
        else:
            _handle_message(message)


if __name__ == "__main__":
    main()
