from __future__ import annotations

import json
from typing import Any
import uuid

from gateway import (
    approve_review_queue_item,
    get_brain_health,
    get_cleanup_candidates,
    get_execution_hints,
    get_entity_context,
    get_events_by_date,
    get_graph_overview,
    get_graph_project_day,
    get_graph_recent,
    get_machine_context,
    get_memory_quality_report,
    get_open_loops,
    get_postgres_bridge_writes,
    get_postgres_recent_events,
    get_postgres_review_queue,
    get_postgres_status,
    mark_memory_superseded,
    promote_memory_to_canon,
    get_project_canon,
    get_project_context,
    get_recent_events,
    get_review_queue,
    get_task_context,
    get_timeline,
    get_today_graph,
    get_today_summary,
    get_vault_status,
    load_settings,
    persist_event,
    reject_review_queue_item,
    repair_graph,
    search_events,
    search_graph,
    start_session,
    store_structured_memory,
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
    repo_context: dict[str, Any] = dict(metadata.get("repo_context") or {})
    commit_sha = str(arguments.get("commit_sha", "") or "").strip()
    if commit_sha:
        repo_context.setdefault("commit_sha", commit_sha)
    if branch:
        repo_context.setdefault("branch", branch)
    for field in ("files_touched", "commands_run", "tests", "artifacts"):
        raw_value = arguments.get(field, [])
        if raw_value:
            repo_context[field] = raw_value
    if repo_context:
        metadata["repo_context"] = repo_context
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


def validated_string_list(arguments: dict[str, Any], field_name: str) -> list[str]:
    raw_values = arguments.get(field_name, [])
    if not isinstance(raw_values, list):
        raise ValueError(f"{field_name} must be an array of strings")
    values: list[str] = []
    for item in raw_values:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} must be an array of strings")
        text = item.strip()
        if text:
            values.append(text)
    return values


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

    if name == "memory_store_structured":
        result = store_structured_memory(
            kind=str(arguments.get("kind", "task_summary")),
            source=str(arguments.get("source", "agent")),
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            importance=validated_importance(arguments),
            tags=validated_tags(arguments),
            graph=bool(arguments.get("graph", False)),
            metadata=merge_metadata(arguments),
            timestamp=str(arguments.get("timestamp", "") or ""),
            repo_context={
                "branch": str(arguments.get("branch", "") or ""),
                "commit_sha": str(arguments.get("commit_sha", "") or ""),
                "files_touched": validated_string_list(arguments, "files_touched"),
                "commands_run": validated_string_list(arguments, "commands_run"),
                "tests": validated_string_list(arguments, "tests"),
                "artifacts": validated_string_list(arguments, "artifacts"),
            },
            goal=str(arguments.get("goal", "")),
            changes=str(arguments.get("changes", "")),
            decision=str(arguments.get("decision", "")),
            why=str(arguments.get("why", "")),
            validation=str(arguments.get("validation", "")),
            next_step=str(arguments.get("next_step", "")),
            risk=str(arguments.get("risk", "")),
            title=str(arguments.get("title", "")),
            summary=str(arguments.get("summary", "")),
            status=str(arguments.get("status", "")),
        )
        return tool_result(result)

    if name == "memory_store_failed_attempt":
        result = store_structured_memory(
            kind="failed_attempt",
            source=str(arguments.get("source", "agent")),
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            importance=validated_importance(arguments),
            tags=validated_tags(arguments),
            graph=bool(arguments.get("graph", True)),
            metadata=merge_metadata(arguments),
            timestamp=str(arguments.get("timestamp", "") or ""),
            repo_context={
                "branch": str(arguments.get("branch", "") or ""),
                "commit_sha": str(arguments.get("commit_sha", "") or ""),
                "files_touched": validated_string_list(arguments, "files_touched"),
                "commands_run": validated_string_list(arguments, "commands_run"),
                "tests": validated_string_list(arguments, "tests"),
                "artifacts": validated_string_list(arguments, "artifacts"),
            },
            goal=str(arguments.get("goal", "")),
            changes=str(arguments.get("changes", "")),
            decision=str(arguments.get("decision", "")),
            why=str(arguments.get("why", "")),
            validation=str(arguments.get("validation", "")),
            next_step=str(arguments.get("next_step", "")),
            risk=str(arguments.get("risk", "")),
            title=str(arguments.get("title", "")),
            summary=str(arguments.get("summary", "")),
            status="failed",
        )
        return tool_result(result)

    if name == "memory_promote_canon":
        payload = promote_memory_to_canon(
            event_id=str(arguments["event_id"]),
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            title=str(arguments.get("title", "")),
            kind=str(arguments.get("kind", "project_fact")),
            note=str(arguments.get("note", "")),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_mark_superseded":
        payload = mark_memory_superseded(
            old_event_id=str(arguments["old_event_id"]),
            new_event_id=str(arguments.get("new_event_id", "")),
            reason=str(arguments.get("reason", "")),
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text), is_error=not payload.get("ok", False))

    if name == "memory_open_loop_add":
        metadata = merge_metadata(arguments)
        loop_id = str(arguments.get("loop_id", "") or "").strip() or str(uuid.uuid4())
        metadata["loop_id"] = loop_id
        metadata["title"] = str(arguments.get("title", ""))
        metadata["status"] = str(arguments.get("status", "open"))
        metadata["next_step"] = str(arguments.get("next_step", ""))
        metadata["risk"] = str(arguments.get("risk", ""))
        metadata["files_touched"] = validated_string_list(arguments, "files_touched")
        metadata["commands_run"] = validated_string_list(arguments, "commands_run")
        result = store_structured_memory(
            kind="open_loop",
            source=str(arguments.get("source", "agent")),
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            importance=validated_importance(arguments),
            tags=validated_tags(arguments),
            graph=bool(arguments.get("graph", False)),
            metadata=metadata,
            timestamp=str(arguments.get("timestamp", "") or ""),
            repo_context={
                "branch": str(arguments.get("branch", "") or ""),
                "commit_sha": str(arguments.get("commit_sha", "") or ""),
                "files_touched": validated_string_list(arguments, "files_touched"),
                "commands_run": validated_string_list(arguments, "commands_run"),
                "tests": validated_string_list(arguments, "tests"),
                "artifacts": validated_string_list(arguments, "artifacts"),
            },
            goal=str(arguments.get("title", "")),
            next_step=str(arguments.get("next_step", "")),
            risk=str(arguments.get("risk", "")),
            summary=str(arguments.get("note", "")),
            status=str(arguments.get("status", "open")),
            title=str(arguments.get("title", "")),
        )
        return tool_result(result)

    if name == "memory_open_loop_update":
        metadata = merge_metadata(arguments)
        metadata["loop_id"] = str(arguments["loop_id"])
        metadata["status"] = str(arguments.get("status", "open"))
        metadata["title"] = str(arguments.get("title", ""))
        metadata["next_step"] = str(arguments.get("next_step", ""))
        metadata["risk"] = str(arguments.get("risk", ""))
        metadata["note"] = str(arguments.get("note", ""))
        metadata["files_touched"] = validated_string_list(arguments, "files_touched")
        metadata["commands_run"] = validated_string_list(arguments, "commands_run")
        result = store_structured_memory(
            kind="open_loop_update",
            source=str(arguments.get("source", "agent")),
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            importance=validated_importance(arguments),
            tags=validated_tags(arguments),
            graph=bool(arguments.get("graph", False)),
            metadata=metadata,
            timestamp=str(arguments.get("timestamp", "") or ""),
            repo_context={
                "branch": str(arguments.get("branch", "") or ""),
                "commit_sha": str(arguments.get("commit_sha", "") or ""),
                "files_touched": validated_string_list(arguments, "files_touched"),
                "commands_run": validated_string_list(arguments, "commands_run"),
                "tests": validated_string_list(arguments, "tests"),
                "artifacts": validated_string_list(arguments, "artifacts"),
            },
            next_step=str(arguments.get("next_step", "")),
            risk=str(arguments.get("risk", "")),
            summary=str(arguments.get("note", "")),
            status=str(arguments.get("status", "open")),
            title=str(arguments.get("title", "")),
        )
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

    if name == "memory_open_loops":
        payload = get_open_loops(
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            status=str(arguments.get("status", "")),
            limit=int(arguments.get("limit", 20)),
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
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

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

    if name == "memory_start_session":
        payload = start_session(
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            query=str(arguments.get("query", "")),
            file_paths=validated_string_list(arguments, "file_paths"),
            limit=int(arguments.get("limit", 8)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_task_context":
        payload = get_task_context(
            arguments["query"],
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            file_paths=validated_string_list(arguments, "file_paths"),
            limit=int(arguments.get("limit", 10)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_project_canon":
        payload = get_project_canon(
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            limit=int(arguments.get("limit", 12)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_machine_context":
        payload = get_machine_context(
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            limit=int(arguments.get("limit", 12)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_execution_hints":
        payload = get_execution_hints(
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            limit=int(arguments.get("limit", 8)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_timeline":
        payload = get_timeline(
            project=str(arguments.get("project", "")),
            cwd=str(arguments.get("cwd", "")),
            since=str(arguments.get("since", "")),
            until=str(arguments.get("until", "")),
            days=int(arguments.get("days", 7)),
            limit=int(arguments.get("limit", 30)),
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_quality_report":
        payload = maintenance_contract(
            get_memory_quality_report(
                project=str(arguments.get("project", "")),
                cwd=str(arguments.get("cwd", "")),
                limit=int(arguments.get("limit", 100)),
            ),
            tool_name=name,
            fmt=fmt,
        )
        return tool_result(maybe_compact_payload(payload, fmt, max_text))

    if name == "memory_cleanup_candidates":
        payload = maintenance_contract(
            get_cleanup_candidates(
                project=str(arguments.get("project", "")),
                cwd=str(arguments.get("cwd", "")),
                limit=int(arguments.get("limit", 20)),
            ),
            tool_name=name,
            fmt=fmt,
        )
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
