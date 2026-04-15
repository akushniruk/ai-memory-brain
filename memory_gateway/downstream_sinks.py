from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUTO_WRITE_KINDS = {"daily_checkin", "daily_checkout", "meeting_summary"}
REVIEW_QUEUE_KINDS = {
    "task_summary",
    "milestone",
    "decision",
    "project_fact",
    "identity",
    "preference",
    "bug",
    "fix",
}

REVIEW_STATE_FILENAME = "review_queue_state.json"
PROMOTION_TARGETS = {"projects", "people", "references"}
LOGGER = logging.getLogger(__name__)


def persist_structured_event(
    event: dict[str, Any],
    *,
    settings: dict[str, Any],
    knowledge: dict[str, Any] | None = None,
    bridge_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dsn = str(settings.get("postgres_dsn", "")).strip()
    if not dsn:
        return {"attempted": False, "stored": False, "reason": "disabled"}

    try:
        import psycopg
    except ImportError:
        return {"attempted": True, "stored": False, "reason": "driver_unavailable"}

    extracted = knowledge or {"entities": [], "relations": [], "summary": ""}
    bridge = bridge_result or {"auto_writes": [], "review_items": []}

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            _ensure_postgres_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO memory_events (
                        id,
                        timestamp,
                        source,
                        kind,
                        project,
                        cwd,
                        importance,
                        text,
                        tags_json,
                        metadata_json,
                        extraction_summary,
                        entities_json,
                        relations_json
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s, %s::jsonb, %s::jsonb
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        timestamp = EXCLUDED.timestamp,
                        source = EXCLUDED.source,
                        kind = EXCLUDED.kind,
                        project = EXCLUDED.project,
                        cwd = EXCLUDED.cwd,
                        importance = EXCLUDED.importance,
                        text = EXCLUDED.text,
                        tags_json = EXCLUDED.tags_json,
                        metadata_json = EXCLUDED.metadata_json,
                        extraction_summary = EXCLUDED.extraction_summary,
                        entities_json = EXCLUDED.entities_json,
                        relations_json = EXCLUDED.relations_json,
                        updated_at = NOW()
                    """,
                    (
                        event["id"],
                        event["timestamp"],
                        event["source"],
                        event["kind"],
                        event["project"],
                        event["cwd"],
                        event["importance"],
                        event["text"],
                        json.dumps(event.get("tags", []), ensure_ascii=True),
                        json.dumps(event.get("metadata", {}), ensure_ascii=True, sort_keys=True),
                        extracted.get("summary", ""),
                        json.dumps(extracted.get("entities", []), ensure_ascii=True),
                        json.dumps(extracted.get("relations", []), ensure_ascii=True),
                    ),
                )

                for item in bridge.get("auto_writes", []):
                    cur.execute(
                        """
                        INSERT INTO vault_bridge_writes (event_id, note_kind, note_path, write_mode)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (event_id, note_kind) DO UPDATE SET
                            note_path = EXCLUDED.note_path,
                            write_mode = EXCLUDED.write_mode
                        """,
                        (
                            event["id"],
                            item.get("note_kind", "unknown"),
                            item.get("note_path", ""),
                            item.get("write_mode", "append"),
                        ),
                    )

                for item in bridge.get("review_items", []):
                    cur.execute(
                        """
                        INSERT INTO memory_review_queue (queue_key, event_id, queue_type, target_path, payload_json, status)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (queue_key) DO UPDATE SET
                            queue_type = EXCLUDED.queue_type,
                            target_path = EXCLUDED.target_path,
                            payload_json = EXCLUDED.payload_json,
                            status = EXCLUDED.status
                        """,
                        (
                            item.get("queue_key", event["id"]),
                            event["id"],
                            item.get("queue_type", "knowledge_review"),
                            item.get("target_path", ""),
                            json.dumps(item.get("payload", {}), ensure_ascii=True, sort_keys=True),
                            item.get("status", "pending"),
                        ),
                    )
    except Exception as exc:
        LOGGER.warning("Postgres structured persistence failed for event_id=%s: %s", event.get("id", ""), exc)
        return {"attempted": True, "stored": False, "reason": "connect_or_write_failed"}

    return {
        "attempted": True,
        "stored": True,
        "auto_writes_recorded": len(bridge.get("auto_writes", [])),
        "review_items_recorded": len(bridge.get("review_items", [])),
    }


def sync_event_to_vault(
    event: dict[str, Any],
    *,
    settings: dict[str, Any],
    knowledge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    vault_root = Path(settings["vault_path"]).expanduser()
    extracted = knowledge or {"entities": [], "relations": [], "summary": ""}
    result = {"auto_writes": [], "review_items": []}

    if event.get("kind") in AUTO_WRITE_KINDS:
        if event.get("kind") == "meeting_summary":
            note_path = _meeting_note_path(vault_root, event)
            _write_markdown_note(note_path, _render_meeting_summary(event, extracted), marker=event["id"])
            result["auto_writes"].append(
                {
                    "note_kind": "meeting_summary",
                    "note_path": str(note_path),
                    "write_mode": "upsert",
                }
            )
            return result

        note_path = _daily_note_path(vault_root, event)
        _append_markdown_block(note_path, _render_daily_note_entry(event, extracted), marker=event["id"])
        result["auto_writes"].append(
            {
                "note_kind": "daily_note",
                "note_path": str(note_path),
                "write_mode": "append",
            }
        )
        return result

    if event.get("kind") in REVIEW_QUEUE_KINDS:
        review_path = _review_note_path(vault_root, event)
        candidate_targets = _candidate_targets(event, extracted)
        _write_markdown_note(
            review_path,
            _render_review_candidate(event, extracted, candidate_targets),
            marker=event["id"],
        )
        result["review_items"].append(
            {
                "queue_key": f"review:{event['id']}",
                "queue_type": "knowledge_review",
                "target_path": str(review_path),
                "status": "pending",
                "payload": {
                    "event_id": event["id"],
                    "kind": event.get("kind", ""),
                    "project": event.get("project", ""),
                    "candidate_targets": candidate_targets,
                },
            }
        )

    return result


def list_review_queue(
    *,
    settings: dict[str, Any],
    status: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    vault_root = Path(settings["vault_path"]).expanduser()
    review_dir = vault_root / "memory" / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    state = _load_review_state(settings)
    items: list[dict[str, Any]] = []

    for note_path in sorted(review_dir.glob("*.md")):
        event_id = _extract_event_id_from_review_note(note_path)
        if not event_id:
            continue
        queue_key = f"review:{event_id}"
        state_item = state.get(queue_key, {})
        item_status = str(state_item.get("status", "pending"))
        if status and item_status != status:
            continue
        items.append(
            {
                "queue_key": queue_key,
                "event_id": event_id,
                "status": item_status,
                "review_note_path": str(note_path),
                "decision_reason": state_item.get("reason", ""),
                "promoted_path": state_item.get("promoted_path", ""),
                "updated_at": state_item.get("updated_at", ""),
            }
        )
        if len(items) >= limit:
            break

    return {
        "ok": True,
        "count": len(items),
        "status_filter": status or "all",
        "items": items,
    }


def get_bridge_health(*, settings: dict[str, Any]) -> dict[str, Any]:
    vault_root = Path(settings["vault_path"]).expanduser()
    queue = list_review_queue(settings=settings, status="", limit=500)
    pending = [item for item in queue.get("items", []) if item.get("status") == "pending"]
    approved = [item for item in queue.get("items", []) if item.get("status") == "approved"]
    rejected = [item for item in queue.get("items", []) if item.get("status") == "rejected"]

    return {
        "ok": True,
        "vault_path": str(vault_root),
        "daily_notes_count": _count_markdown_files(vault_root / "daily-notes"),
        "meetings_count": _count_markdown_files(vault_root / "meetings"),
        "review_notes_count": _count_markdown_files(vault_root / "memory" / "review"),
        "queue": {
            "pending": len(pending),
            "approved": len(approved),
            "rejected": len(rejected),
            "total": len(queue.get("items", [])),
        },
    }


def promote_review_item(
    *,
    settings: dict[str, Any],
    queue_key: str,
    target: str,
    title: str = "",
) -> dict[str, Any]:
    target_name = str(target).strip().lower()
    if target_name not in PROMOTION_TARGETS:
        return {"ok": False, "error": f"Invalid target '{target}'. Use one of: {sorted(PROMOTION_TARGETS)}"}
    if not queue_key.startswith("review:"):
        return {"ok": False, "error": "queue_key must start with 'review:'"}

    event_id = queue_key.split("review:", 1)[1].strip()
    if not event_id:
        return {"ok": False, "error": "queue_key is missing an event id"}

    event = _find_event_by_id(settings=settings, event_id=event_id)
    if not event:
        return {"ok": False, "error": f"No event found for {event_id}"}

    review_note = _review_note_for_event(settings=settings, event_id=event_id)
    if review_note is None:
        return {"ok": False, "error": f"Review note not found for {event_id}"}

    promoted_path = _promotion_note_path(settings=settings, event=event, target=target_name, title=title)
    _ensure_promoted_note_template(promoted_path, target=target_name, event=event, title=title)
    _append_promoted_entry(
        promoted_path,
        marker=event_id,
        section=_promotion_section_for_event(target=target_name, event=event),
        entry=_render_promoted_entry(event=event),
    )

    state = _load_review_state(settings)
    state[queue_key] = {
        "status": "approved",
        "reason": "",
        "promoted_path": str(promoted_path),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_review_state(settings, state)
    LOGGER.info(
        "Approved review queue item queue_key=%s target=%s promoted_path=%s",
        queue_key,
        target_name,
        promoted_path,
    )

    return {
        "ok": True,
        "queue_key": queue_key,
        "review_note_path": str(review_note),
        "promoted_path": str(promoted_path),
        "target": target_name,
        "status": "approved",
    }


def reject_review_item(
    *,
    settings: dict[str, Any],
    queue_key: str,
    reason: str = "",
) -> dict[str, Any]:
    if not queue_key.startswith("review:"):
        return {"ok": False, "error": "queue_key must start with 'review:'"}
    event_id = queue_key.split("review:", 1)[1].strip()
    if not event_id:
        return {"ok": False, "error": "queue_key is missing an event id"}
    review_note = _review_note_for_event(settings=settings, event_id=event_id)
    if review_note is None:
        return {"ok": False, "error": f"Review note not found for {event_id}"}

    state = _load_review_state(settings)
    state[queue_key] = {
        "status": "rejected",
        "reason": reason.strip(),
        "promoted_path": "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_review_state(settings, state)
    LOGGER.info("Rejected review queue item queue_key=%s reason=%s", queue_key, reason.strip())
    return {
        "ok": True,
        "queue_key": queue_key,
        "review_note_path": str(review_note),
        "status": "rejected",
        "reason": reason.strip(),
    }


def _ensure_postgres_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_events (
                id TEXT PRIMARY KEY,
                timestamp TIMESTAMPTZ NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                project TEXT NOT NULL,
                cwd TEXT NOT NULL,
                importance TEXT NOT NULL,
                text TEXT NOT NULL,
                tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                extraction_summary TEXT NOT NULL DEFAULT '',
                entities_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                relations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vault_bridge_writes (
                event_id TEXT NOT NULL,
                note_kind TEXT NOT NULL,
                note_path TEXT NOT NULL,
                write_mode TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (event_id, note_kind)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_review_queue (
                queue_key TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                queue_type TEXT NOT NULL,
                target_path TEXT NOT NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def _daily_note_path(vault_root: Path, event: dict[str, Any]) -> Path:
    return vault_root / "daily-notes" / f"{_event_date(event)}.md"


def _meeting_note_path(vault_root: Path, event: dict[str, Any]) -> Path:
    date_value = _event_date(event)
    project_slug = _slugify(str(event.get("project", "")) or "meeting")
    return vault_root / "meetings" / f"{date_value}-{project_slug}-{str(event['id'])[:8]}.md"


def _review_note_path(vault_root: Path, event: dict[str, Any]) -> Path:
    date_value = _event_date(event)
    kind_slug = _slugify(str(event.get("kind", "")) or "memory")
    project_slug = _slugify(str(event.get("project", "")) or "general")
    return vault_root / "memory" / "review" / f"{date_value}-{kind_slug}-{project_slug}-{str(event['id'])[:8]}.md"


def _append_markdown_block(path: Path, block: str, *, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    marker_line = f"<!-- ai-memory-event:{marker} -->"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    else:
        existing = f"# {path.stem}\n"
    if marker_line in existing:
        return
    rendered = existing.rstrip() + f"\n\n{marker_line}\n{block.strip()}\n"
    path.write_text(rendered, encoding="utf-8")


def _write_markdown_note(path: Path, content: str, *, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    marker_line = f"<!-- ai-memory-event:{marker} -->"
    if path.exists() and marker_line in path.read_text(encoding="utf-8"):
        return
    path.write_text(f"{marker_line}\n{content.strip()}\n", encoding="utf-8")


def _render_daily_note_entry(event: dict[str, Any], knowledge: dict[str, Any]) -> str:
    timestamp = _event_timestamp(event)
    heading = "Check-in" if event.get("kind") == "daily_checkin" else "Checkout"
    summary = str(knowledge.get("summary", "")).strip()
    lines = [
        f"## {heading} ({timestamp})",
        "",
        str(event.get("text", "")).strip(),
    ]
    if summary:
        lines.extend(["", f"Summary: {summary}"])
    return "\n".join(lines).strip()


def _render_meeting_summary(event: dict[str, Any], knowledge: dict[str, Any]) -> str:
    title = str(event.get("project", "")).strip() or "Meeting Summary"
    summary = str(knowledge.get("summary", "")).strip()
    lines = [
        f"# {title}",
        "",
        f"- Timestamp: {_event_timestamp(event)}",
        f"- Source kind: {event.get('kind', '')}",
        "",
        "## Notes",
        "",
        str(event.get("text", "")).strip(),
    ]
    if summary:
        lines.extend(["", "## Summary", "", summary])
    return "\n".join(lines).strip()


def _render_review_candidate(
    event: dict[str, Any],
    knowledge: dict[str, Any],
    candidate_targets: list[str],
) -> str:
    entities = knowledge.get("entities", [])
    relations = knowledge.get("relations", [])
    lines = [
        f"# Review: {event.get('kind', 'memory')}",
        "",
        f"- Event ID: {event.get('id', '')}",
        f"- Timestamp: {_event_timestamp(event)}",
        f"- Project: {event.get('project', '') or 'unscoped'}",
        f"- Candidate targets: {', '.join(candidate_targets) if candidate_targets else 'projects'}",
        "",
        "## Source memory",
        "",
        str(event.get("text", "")).strip(),
    ]
    summary = str(knowledge.get("summary", "")).strip()
    if summary:
        lines.extend(["", "## Extracted summary", "", summary])
    if entities:
        lines.extend(["", "## Entities", ""])
        for entity in entities:
            lines.append(f"- {entity.get('name', '')} ({entity.get('entity_type', 'unknown')})")
    if relations:
        lines.extend(["", "## Relations", ""])
        for relation in relations:
            lines.append(
                f"- {relation.get('source', '')} -> {relation.get('target', '')} [{relation.get('rel_type', '')}]"
            )
    return "\n".join(lines).strip()


def _candidate_targets(event: dict[str, Any], knowledge: dict[str, Any]) -> list[str]:
    targets: set[str] = {"projects"}
    for entity in knowledge.get("entities", []):
        entity_type = str(entity.get("entity_type", "")).lower()
        if entity_type == "person":
            targets.add("people")
        if entity_type in {"repo", "tool", "reference", "document"}:
            targets.add("references")
    if event.get("kind") in {"identity", "preference"}:
        targets.add("people")
    return sorted(targets)


def _event_date(event: dict[str, Any]) -> str:
    return str(event.get("timestamp", ""))[:10] or "unknown-date"


def _event_timestamp(event: dict[str, Any]) -> str:
    raw = str(event.get("timestamp", "")).strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return raw


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "memory"


def _load_review_state(settings: dict[str, Any]) -> dict[str, Any]:
    config_dir = Path(str(settings.get("config_dir", ""))).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    state_path = config_dir / REVIEW_STATE_FILENAME
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOGGER.warning("Review queue state JSON is invalid at path=%s; resetting state", state_path)
        return {}
    except OSError as exc:
        LOGGER.warning("Failed to read review queue state path=%s: %s", state_path, exc)
        return {}


def _save_review_state(settings: dict[str, Any], state: dict[str, Any]) -> None:
    config_dir = Path(str(settings.get("config_dir", ""))).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    state_path = config_dir / REVIEW_STATE_FILENAME
    payload = json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True)
    temp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(state_path)


def _review_note_for_event(*, settings: dict[str, Any], event_id: str) -> Path | None:
    review_dir = Path(settings["vault_path"]).expanduser() / "memory" / "review"
    for note_path in review_dir.glob("*.md"):
        if _extract_event_id_from_review_note(note_path) == event_id:
            return note_path
    return None


def _extract_event_id_from_review_note(note_path: Path) -> str:
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("Failed to read review note path=%s: %s", note_path, exc)
        return ""
    marker = "<!-- ai-memory-event:"
    if marker not in text:
        return ""
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("-->", start)
    if end < 0:
        return ""
    return text[start:end].strip()


def _find_event_by_id(*, settings: dict[str, Any], event_id: str) -> dict[str, Any] | None:
    log_path = Path(str(settings["memory_log_path"])).expanduser()
    if not log_path.exists():
        return None
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(event.get("id", "")) == event_id:
                return event
    return None


def _promotion_note_path(
    *,
    settings: dict[str, Any],
    event: dict[str, Any],
    target: str,
    title: str = "",
) -> Path:
    vault_root = Path(settings["vault_path"]).expanduser()
    target_dir = vault_root / target
    target_dir.mkdir(parents=True, exist_ok=True)
    if title.strip():
        base = title.strip()
    elif target == "projects":
        base = str(event.get("project", "")).strip() or "unscoped-project"
    elif target == "people":
        base = str(event.get("project", "")).strip() or "people-knowledge"
    else:
        base = str(event.get("project", "")).strip() or "references"
    slug = _slugify(base)
    return target_dir / f"{slug}.md"


def _render_promoted_entry(*, event: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"### {event.get('kind', 'memory')} ({_event_date(event)})",
            "",
            f"- Event ID: {event.get('id', '')}",
            f"- Source: {event.get('source', '')}",
            f"- Project: {event.get('project', '') or 'unscoped'}",
            f"- Timestamp: {_event_timestamp(event)}",
            "",
            "Source memory:",
            "",
            str(event.get("text", "")).strip(),
        ]
    ).strip()


def _promotion_section_for_event(*, target: str, event: dict[str, Any]) -> str:
    kind = str(event.get("kind", "")).strip()
    if target == "projects":
        if kind in {"decision", "task_summary", "bug", "fix"}:
            return "## Decisions and Changes"
        if kind in {"milestone", "project_fact"}:
            return "## Milestones and Facts"
        return "## Updates"
    if target == "people":
        if kind in {"identity", "preference"}:
            return "## Profile and Preferences"
        return "## Collaboration Notes"
    return "## References and Notes"


def _ensure_promoted_note_template(path: Path, *, target: str, event: dict[str, Any], title: str = "") -> None:
    if path.exists():
        return
    display_title = title.strip()
    if not display_title:
        display_title = str(event.get("project", "")).strip() or path.stem.replace("-", " ").title()
    if target == "projects":
        content = "\n".join(
            [
                f"# Project: {display_title}",
                "",
                "## Overview",
                "",
                "Curated notes promoted from operational memory.",
                "",
                "## Decisions and Changes",
                "",
                "## Milestones and Facts",
                "",
                "## Updates",
                "",
            ]
        )
    elif target == "people":
        content = "\n".join(
            [
                f"# Person: {display_title}",
                "",
                "## Profile and Preferences",
                "",
                "Promoted identity and preference notes.",
                "",
                "## Collaboration Notes",
                "",
            ]
        )
    else:
        content = "\n".join(
            [
                f"# Reference: {display_title}",
                "",
                "## Context",
                "",
                "Curated references extracted from memory events.",
                "",
                "## References and Notes",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_promoted_entry(path: Path, *, marker: str, section: str, entry: str) -> None:
    marker_line = f"<!-- ai-memory-event:{marker} -->"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker_line in existing:
        return
    if section not in existing:
        existing = existing.rstrip() + f"\n\n{section}\n"
    section_idx = existing.find(section)
    if section_idx < 0:
        rendered = existing.rstrip() + f"\n\n{section}\n\n{marker_line}\n{entry}\n"
        path.write_text(rendered, encoding="utf-8")
        return
    insert_at = section_idx + len(section)
    next_header_idx = existing.find("\n## ", insert_at)
    block = f"\n\n{marker_line}\n{entry}\n"
    if next_header_idx < 0:
        rendered = existing[:insert_at] + block + existing[insert_at:]
    else:
        rendered = existing[:next_header_idx] + block + existing[next_header_idx:]
    path.write_text(rendered.rstrip() + "\n", encoding="utf-8")


def _count_markdown_files(path: Path) -> int:
    if not path.exists():
        return 0
    return len(list(path.glob("*.md")))
