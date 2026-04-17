from __future__ import annotations

import json
from typing import Any

from gateway import (
    approve_review_queue_item,
    get_brain_health,
    get_entity_context,
    get_events_by_date,
    get_graph_overview,
    get_graph_project_day,
    get_graph_recent,
    get_postgres_bridge_writes,
    get_postgres_recent_events,
    get_postgres_review_queue,
    get_postgres_status,
    get_project_context,
    get_recent_events,
    get_review_queue,
    get_today_graph,
    get_today_summary,
    get_vault_status,
    load_settings,
    persist_event,
    reject_review_queue_item,
    repair_graph,
    search_events,
    search_graph,
    summarize_events_with_helper,
    run_doctor,
    build_day_capsule,
    run_entity_hygiene,
)


def compact_event(event: dict[str, Any], *, max_text: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in event.items():
        if val in ("", [], {}, None):
            continue
        out[key] = val
    text = str(out.get("text", ""))
    if len(text) > max_text:
        out["text"] = text[: max_text - 1] + "\u2026"
    return out


def maybe_compact_list(
    events: list[dict[str, Any]],
    fmt: str,
    *,
    max_text: int,
) -> list[dict[str, Any]]:
    if fmt != "compact":
        return events
    return [compact_event(event, max_text=max_text) for event in events]


def maybe_compact_payload(data: dict[str, Any], fmt: str, max_text: int) -> dict[str, Any]:
    if fmt != "compact":
        return data
    out = dict(data)
    if "raw_results" in out:
        out["raw_results"] = maybe_compact_list(out["raw_results"], fmt, max_text=max_text)
    if "graph_results" in out:
        out["graph_results"] = maybe_compact_list(out["graph_results"], fmt, max_text=max_text)
    if "results" in out:
        out["results"] = maybe_compact_list(out["results"], fmt, max_text=max_text)
    if "context" in out and isinstance(out["context"], dict):
        ctx = out["context"]
        compact_ctx: dict[str, Any] = {}
        for key, val in ctx.items():
            if isinstance(val, list):
                compact_ctx[key] = maybe_compact_list(val, fmt, max_text=max_text)
            else:
                compact_ctx[key] = val
        out["context"] = compact_ctx
    return out


def tool_result(data: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=True, indent=2)}],
        "structuredContent": data,
        "isError": is_error,
    }


def maintenance_contract(
    payload: dict[str, Any],
    *,
    tool_name: str,
    fmt: str,
) -> dict[str, Any]:
    out = dict(payload)
    out.setdefault("ok", True)
    out.setdefault("error", "")
    out.setdefault(
        "explainability",
        {
            "tool": tool_name,
            "format": fmt,
            "contract_version": "v1",
        },
    )
    return out


def merge_metadata(arguments: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(arguments.get("metadata") or {})
    branch = arguments.get("branch") or ""
    if branch:
        metadata.setdefault("branch", branch)
    return metadata


def validated_importance(arguments: dict[str, Any]) -> str:
    value = str(arguments.get("importance", "normal"))
    if value not in {"low", "normal", "high"}:
        raise ValueError("importance must be one of: low, normal, high")
    return value


def validated_tags(arguments: dict[str, Any]) -> list[str]:
    raw_tags = arguments.get("tags", [])
    if not isinstance(raw_tags, list):
        raise ValueError("tags must be an array of strings")
    tags: list[str] = []
    for item in raw_tags:
        if not isinstance(item, str):
            raise ValueError("tags must be an array of strings")
        if item.strip():
            tags.append(item.strip())
    return tags


def build_event_payload(
    arguments: dict[str, Any],
    *,
    text: str,
    kind: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": text,
        "kind": kind,
        "source": arguments.get("source", "agent"),
        "project": arguments.get("project", ""),
        "cwd": arguments.get("cwd", ""),
        "importance": validated_importance(arguments),
        "tags": validated_tags(arguments),
        "graph": bool(arguments.get("graph", False)),
        "metadata": merge_metadata(arguments),
    }
    if arguments.get("timestamp"):
        payload["timestamp"] = arguments["timestamp"]
    return payload


def _has_summary_sections(summary: str) -> bool:
    required_markers = (
        "goal:",
        "changes",
        "decision",
        "validation",
        "risk",
    )
    normalized = str(summary).strip().lower()
    return all(marker in normalized for marker in required_markers)


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    fmt = str(arguments.get("format", "full"))
    max_text = int(arguments.get("max_text_chars", 400))

    if name == "memory_add":
        payload = build_event_payload(
            arguments,
            text=arguments["text"],
            kind=str(arguments.get("kind", "note")),
        )
        result = persist_event(payload)
        return tool_result(result)

    if name == "memory_store_summary":
        if not _has_summary_sections(arguments["summary"]):
            raise ValueError(
                "summary must include: Goal, Changes, Decisions, Validation, and Risks/TODO."
            )
        payload = build_event_payload(
            arguments,
            text=arguments["summary"],
            kind="task_summary",
        )
        result = persist_event(payload)
        return tool_result(result)

    if name == "memory_meeting_summary":
        payload = build_event_payload(
            arguments,
            text=arguments["text"],
            kind="meeting_summary",
        )
        result = persist_event(payload)
        return tool_result(result)

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
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_vault_status":
        payload = maintenance_contract(get_vault_status(), tool_name=name, fmt=fmt)
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_postgres_status":
        payload = maintenance_contract(get_postgres_status(), tool_name=name, fmt=fmt)
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_postgres_recent":
        payload = get_postgres_recent_events(
            limit=int(arguments.get("limit", 20)),
            project=arguments.get("project", ""),
            source=arguments.get("source", ""),
            kind=arguments.get("kind", ""),
            since=arguments.get("since", ""),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_postgres_review_queue":
        payload = get_postgres_review_queue(
            status=arguments.get("status", ""),
            limit=int(arguments.get("limit", 50)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_postgres_bridge_writes":
        payload = get_postgres_bridge_writes(
            event_id=arguments.get("event_id", ""),
            limit=int(arguments.get("limit", 50)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_review_queue":
        payload = get_review_queue(
            status=arguments.get("status", ""),
            limit=int(arguments.get("limit", 50)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_review_approve":
        payload = approve_review_queue_item(
            queue_key=arguments["queue_key"],
            target=arguments["target"],
            title=arguments.get("title", ""),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_review_reject":
        payload = reject_review_queue_item(
            queue_key=arguments["queue_key"],
            reason=arguments.get("reason", ""),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_entity_context":
        query = arguments["query"]
        limit = int(arguments.get("limit", 8))
        payload = {"query": query, "results": get_entity_context(query, limit=limit)}
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_graph_overview":
        limit = int(arguments.get("limit", 8))
        payload = {"overview": get_graph_overview(limit=limit)}
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_graph_project_day":
        project = arguments["project"]
        date = arguments["date"]
        limit = int(arguments.get("limit", 12))
        payload = {"project": project, "date": date, "neighborhood": get_graph_project_day(project, date, limit=limit)}
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_today_graph":
        project = arguments.get("project", "")
        date = arguments.get("date", "")
        limit = int(arguments.get("limit", 12))
        payload = get_today_graph(project=project, date=date, limit=limit)
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_brain_health":
        limit = int(arguments.get("limit", 8))
        payload = maintenance_contract(get_brain_health(limit=limit), tool_name=name, fmt=fmt)
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_brain_doctor":
        payload = maintenance_contract(run_doctor(), tool_name=name, fmt=fmt)
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_compact_day":
        date = arguments["date"]
        project = str(arguments.get("project", ""))
        payload = maintenance_contract(build_day_capsule(date=date, project=project), tool_name=name, fmt=fmt)
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_entity_hygiene":
        payload = maintenance_contract(run_entity_hygiene(), tool_name=name, fmt=fmt)
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_today_summary":
        project = arguments.get("project", "")
        date = arguments.get("date", "")
        payload = get_today_summary(project=project, date=date)
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_repair_graph":
        payload = repair_graph(
            limit=int(arguments.get("limit", 0)),
            project=arguments.get("project", ""),
            date=arguments.get("date", ""),
            missing_only=bool(arguments.get("missing_only", True)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_daily_summary":
        date = arguments["date"]
        filters = {
            "project": arguments.get("project", ""),
            "source": arguments.get("source", ""),
            "kind": arguments.get("kind", ""),
        }
        events = get_events_by_date(date, **filters)
        settings = load_settings()
        summary = summarize_events_with_helper(date=date, events=events, settings=settings)
        payload = {"date": date, "summary": summary.get("summary", ""), "used_helper": summary.get("used_helper", False)}
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

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
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name in ("memory_by_date", "memory_get_date"):
        date = arguments["date"]
        filters = {
            "project": arguments.get("project", ""),
            "source": arguments.get("source", ""),
            "kind": arguments.get("kind", ""),
        }
        results = get_events_by_date(date, **filters)
        payload = {"date": date, "results": results}
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_project_context":
        project = arguments["project"]
        limit = int(arguments.get("limit", 12))
        context = get_project_context(project, limit=limit)
        graph = get_graph_recent(limit=limit, project=project)
        payload = {"project": project, "context": context, "graph_results": graph}
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    raise KeyError(name)
