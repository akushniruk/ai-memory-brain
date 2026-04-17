import json
from collections import Counter
from difflib import SequenceMatcher
import logging
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from downstream_sinks import (
    get_bridge_health,
    list_review_queue,
    persist_structured_event,
    promote_review_item,
    reject_review_item,
    sync_event_to_vault,
)
try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError, ServiceUnavailable
except ImportError:  # pragma: no cover - optional dependency
    GraphDatabase = None

    class AuthError(Exception):
        """Fallback auth error when neo4j dependency is missing."""

    class ServiceUnavailable(Exception):
        """Fallback service error when neo4j dependency is missing."""

from runtime_layout import load_runtime_settings
from postgres_reads import (
    list_bridge_writes as list_postgres_bridge_writes,
    list_recent_events as list_postgres_recent_events,
    list_review_queue as list_postgres_review_queue,
)

LOGGER = logging.getLogger(__name__)


GRAPH_KINDS = {
    "identity",
    "preference",
    "decision",
    "project_fact",
    "task_summary",
    "failed_attempt",
    "open_loop",
    "open_loop_update",
    "supersession",
    "bug",
    "fix",
    "milestone",
}

EXTRACT_KINDS = {
    "task_summary",
    "daily_checkin",
    "daily_checkout",
    "decision",
    "project_fact",
    "identity",
    "preference",
    "milestone",
    "failed_attempt",
    "open_loop",
}

STRUCTURED_SUMMARY_FIELDS = (
    "goal",
    "changes",
    "decision",
    "why",
    "validation",
    "next_step",
    "risk",
)

ACTIVE_LOOP_STATUSES = {"open", "blocked", "in_progress"}

LOW_SIGNAL_QUERY_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, value))

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(event)
    normalized.setdefault("id", str(uuid.uuid4()))
    normalized.setdefault("source", "unknown")
    normalized.setdefault("kind", "note")
    normalized.setdefault("text", "")
    normalized.setdefault("tags", [])
    normalized.setdefault("metadata", {})
    normalized.setdefault("project", "")
    normalized.setdefault("cwd", "")
    normalized.setdefault("importance", "normal")
    normalized.setdefault("timestamp", utc_now_iso())
    return normalized


def _parse_iso8601(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _normalized_text_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _token_set(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", _normalized_text_key(value)))


def _text_similarity(left: str, right: str) -> float:
    left_norm = _normalized_text_key(left)
    right_norm = _normalized_text_key(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = _token_set(left_norm)
    right_tokens = _token_set(right_norm)
    if not left_tokens or not right_tokens:
        return ratio
    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    # Favor overlap for summary-like content where line edits are common.
    return max(ratio, overlap)


def _find_recent_duplicate_event(
    events: list[dict[str, Any]],
    event: dict[str, Any],
    *,
    window_minutes: int = 30,
    similarity_threshold: float = 0.9,
) -> tuple[dict[str, Any] | None, float]:
    target_text = str(event.get("text", ""))
    if not _normalized_text_key(target_text):
        return None, 0.0
    target_kind = str(event.get("kind", "")).strip().lower()
    target_project = str(event.get("project", "")).strip().lower()
    target_source = str(event.get("source", "")).strip().lower()
    target_time = _parse_iso8601(str(event.get("timestamp", "")))
    if target_time is None:
        return None, 0.0
    lower_bound = target_time - timedelta(minutes=max(1, window_minutes))

    best_similarity = 0.0
    for existing in reversed(events):
        existing_time = _parse_iso8601(str(existing.get("timestamp", "")))
        if existing_time is None or existing_time < lower_bound:
            continue
        similarity = _text_similarity(str(existing.get("text", "")), target_text)
        if similarity > best_similarity:
            best_similarity = similarity
        if similarity < similarity_threshold:
            continue
        if str(existing.get("kind", "")).strip().lower() != target_kind:
            continue
        if str(existing.get("project", "")).strip().lower() != target_project:
            continue
        if str(existing.get("source", "")).strip().lower() != target_source:
            continue
        return existing, similarity
    return None, best_similarity


def _effective_dedupe_window_minutes(base_minutes: int, kind: str) -> int:
    normalized_kind = str(kind or "").strip().lower()
    high_signal_kinds = {"task_summary", "decision", "milestone", "meeting_summary"}
    if normalized_kind in high_signal_kinds:
        return max(1, int(base_minutes * 2))
    return max(1, base_minutes)


def should_store_in_graph(event: dict[str, Any]) -> bool:
    if event.get("graph") is True:
        return True

    if event.get("importance") == "high":
        return True

    if event.get("kind") in GRAPH_KINDS or event.get("kind") in EXTRACT_KINDS:
        return True

    text = event.get("text", "").lower()
    important_prefixes = (
        "remember:",
        "decision:",
        "important:",
        "preference:",
        "fact:",
    )
    return text.startswith(important_prefixes)


def append_jsonl(log_path: str, event: dict[str, Any]) -> None:
    path = Path(log_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")


def read_jsonl_events(log_path: str) -> list[dict[str, Any]]:
    path = Path(log_path).expanduser()
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _safe_name(value: Any) -> str:
    return str(value or "").strip()


def _safe_rel_type(value: Any) -> str:
    raw = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().lower())
    return raw[:64] if raw else "related_to"


def _listify_str_values(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif value in (None, "", []):
        return []
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _metadata(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("metadata", {})
    return raw if isinstance(raw, dict) else {}


def _structured_metadata(event: dict[str, Any]) -> dict[str, Any]:
    value = _metadata(event).get("structured", {})
    return value if isinstance(value, dict) else {}


def _structured_field(event: dict[str, Any], key: str) -> str:
    value = _structured_metadata(event).get(key, "")
    return str(value or "").strip()


def _repo_context(event: dict[str, Any]) -> dict[str, Any]:
    value = _metadata(event).get("repo_context", {})
    return value if isinstance(value, dict) else {}


def _event_files(event: dict[str, Any]) -> list[str]:
    repo_context = _repo_context(event)
    return _listify_str_values(repo_context.get("files_touched", []))


def _event_commands(event: dict[str, Any]) -> list[str]:
    repo_context = _repo_context(event)
    return _listify_str_values(repo_context.get("commands_run", []))


def _event_tests(event: dict[str, Any]) -> list[str]:
    repo_context = _repo_context(event)
    return _listify_str_values(repo_context.get("tests", []))


def _event_artifacts(event: dict[str, Any]) -> list[str]:
    repo_context = _repo_context(event)
    return _listify_str_values(repo_context.get("artifacts", []))


def _event_branch(event: dict[str, Any]) -> str:
    repo_context = _repo_context(event)
    branch = str(repo_context.get("branch", "") or "").strip()
    if branch:
        return branch
    return str(_metadata(event).get("branch", "") or "").strip()


def _event_commit(event: dict[str, Any]) -> str:
    repo_context = _repo_context(event)
    return str(repo_context.get("commit_sha", "") or "").strip()


def _structured_items(value: str) -> list[str]:
    lines = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*]\s*", "", line)
        if line:
            lines.append(line)
    return lines


def _first_structured_item(event: dict[str, Any], key: str) -> str:
    items = _structured_items(_structured_field(event, key))
    return items[0] if items else ""


def _extract_summary_sections(text: str) -> dict[str, str]:
    sections = {key: "" for key in STRUCTURED_SUMMARY_FIELDS}
    aliases = {
        "goal": "goal",
        "changes": "changes",
        "decision": "decision",
        "decisions": "decision",
        "why": "why",
        "validation": "validation",
        "next step": "next_step",
        "next steps": "next_step",
        "risks/todo": "risk",
        "risk/todo": "risk",
        "risks": "risk",
        "risk": "risk",
    }
    current = ""
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = re.match(r"^([A-Za-z /]+):\s*(.*)$", line)
        if matched:
            label = matched.group(1).strip().lower()
            key = aliases.get(label)
            if key:
                current = key
                sections[current] = matched.group(2).strip()
                continue
        if current:
            sections[current] = (sections[current] + "\n" + line).strip()
    return sections


def _open_loop_status(raw_status: Any) -> str:
    status = str(raw_status or "").strip().lower()
    if status in {"open", "blocked", "in_progress", "resolved", "abandoned", "superseded"}:
        return status
    return "open"


def _event_scope_match(event: dict[str, Any], *, project: str = "", cwd: str = "") -> bool:
    if project and str(event.get("project", "")).strip() != project:
        return False
    if cwd and str(event.get("cwd", "")).strip() != cwd:
        return False
    return True


def _git_changed_files(cwd: str) -> list[str]:
    target = str(cwd or "").strip()
    if not target:
        return []
    try:
        result = subprocess.run(
            ["git", "-C", target, "diff", "--name-only", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _supersession_map(events: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    for event in events:
        if str(event.get("kind", "")).strip().lower() != "supersession":
            continue
        md = _metadata(event)
        old_event_id = str(md.get("old_event_id", "") or "").strip()
        if not old_event_id:
            continue
        mapping[old_event_id] = {
            "new_event_id": str(md.get("new_event_id", "") or "").strip(),
            "reason": str(md.get("reason", "") or "").strip(),
            "supersession_event_id": str(event.get("id", "") or "").strip(),
        }
    return mapping


def _is_superseded(event: dict[str, Any], supersession_map: dict[str, dict[str, str]]) -> bool:
    event_id = str(event.get("id", "") or "").strip()
    if not event_id:
        return False
    return event_id in supersession_map


def _project_key(value: str) -> str:
    project = _safe_name(value)
    return _normalize_key(project) if project else "unscoped"


def _event_date(timestamp: str) -> str:
    return str(timestamp or "")[:10]


def _normalize_extracted_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        payload = {"entities": payload, "relations": [], "summary": ""}
    if not isinstance(payload, dict):
        return {"entities": [], "relations": [], "summary": ""}

    entities_out: list[dict[str, str]] = []
    seen_entities: set[str] = set()
    for item in payload.get("entities", []):
        if isinstance(item, str):
            name = _safe_name(item)
            entity_type = "unknown"
            role = "mentioned"
        elif isinstance(item, dict):
            name = _safe_name(item.get("name"))
            entity_type = _safe_name(item.get("type") or item.get("entity_type") or "unknown")
            role = _safe_name(item.get("role") or "mentioned")
        else:
            continue
        if not name:
            continue
        name_key = _normalize_key(name)
        if not name_key or name_key in seen_entities:
            continue
        seen_entities.add(name_key)
        entities_out.append(
            {
                "name": name,
                "name_key": name_key,
                "entity_type": entity_type[:64] or "unknown",
                "role": role[:64] or "mentioned",
            }
        )

    relations_out: list[dict[str, str]] = []
    seen_relations: set[tuple[str, str, str]] = set()
    for item in payload.get("relations", []):
        if not isinstance(item, dict):
            continue
        source = _safe_name(item.get("source"))
        target = _safe_name(item.get("target"))
        if not source or not target:
            continue
        source_key = _normalize_key(source)
        target_key = _normalize_key(target)
        if not source_key or not target_key:
            continue
        rel_type = _safe_rel_type(item.get("type") or item.get("relation") or "related_to")
        relation_key = (source_key, target_key, rel_type)
        if relation_key in seen_relations:
            continue
        seen_relations.add(relation_key)
        fact = _safe_name(item.get("fact"))
        relations_out.append(
            {
                "source": source,
                "source_key": source_key,
                "target": target,
                "target_key": target_key,
                "rel_type": rel_type,
                "fact": fact[:500],
            }
        )

    summary = _safe_name(payload.get("summary"))[:500]
    return {"entities": entities_out, "relations": relations_out, "summary": summary}


def _build_helper_prompt(event: dict[str, Any]) -> str:
    payload_text = (
        f"kind={event.get('kind', '')}\n"
        f"source={event.get('source', '')}\n"
        f"project={event.get('project', '')}\n"
        f"importance={event.get('importance', '')}\n"
        f"text={str(event.get('text', ''))[:1600]}"
    )
    return (
        "Extract stable, reusable knowledge from this memory event. "
        "Prefer people, repos, tools, features, projects, dates, and durable decisions. "
        "Ignore filler, greetings, and short-lived coordination.\n\n"
        "Return only strict JSON with keys: entities, relations, summary.\n"
        "entities: array of objects with name, type, role.\n"
        "relations: array of objects with source, target, type, fact.\n"
        "summary: short plain text.\n\n"
        "Rules:\n"
        "- Use lowercase snake-like relation types such as works_on, uses, prefers, fixed, decided, reviewed.\n"
        "- Keep entity names human-readable.\n"
        "- Only include facts grounded in the event text.\n"
        "- If no structured facts are present, return empty arrays and an empty summary.\n\n"
        "Example output:\n"
        '{"entities":[{"name":"Andrew","type":"person","role":"owner"},{"name":"Pharos","type":"project","role":"focus"}],"relations":[{"source":"Andrew","target":"Pharos","type":"works_on","fact":"Andrew worked on Pharos."}],"summary":"Andrew worked on Pharos."}\n\n'
        f"EVENT:\n{payload_text}"
    )


def _extract_for_event(event: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    wants_extract = event.get("kind") in EXTRACT_KINDS or event.get("importance") == "high"
    if not wants_extract:
        return {"entities": [], "relations": [], "summary": ""}
    return _extract_knowledge_with_helper(event, settings)


def _extract_knowledge_with_helper(event: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    if not settings.get("helper_enabled"):
        return {"entities": [], "relations": [], "summary": ""}
    if not settings.get("helper_model"):
        return {"entities": [], "relations": [], "summary": ""}

    prompt = _build_helper_prompt(event)
    request_payload = {
        "model": settings["helper_model"],
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }
    request_data = json.dumps(request_payload, ensure_ascii=True).encode("utf-8")
    req = urllib_request.Request(
        settings["helper_base_url"],
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=settings["helper_timeout_sec"]) as resp:
            body = resp.read().decode("utf-8")
    except (urllib_error.URLError, TimeoutError, OSError):
        return {"entities": [], "relations": [], "summary": ""}

    try:
        outer = json.loads(body)
    except json.JSONDecodeError:
        return {"entities": [], "relations": [], "summary": ""}

    raw_response = outer.get("response")
    if isinstance(raw_response, str):
        raw_response = raw_response.strip()
        if raw_response.startswith("```"):
            raw_response = raw_response.strip("`")
            raw_response = raw_response.replace("json", "", 1).strip()
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            return {"entities": [], "relations": [], "summary": ""}
        return _normalize_extracted_payload(parsed)

    return _normalize_extracted_payload(raw_response)


def summarize_events_with_helper(
    *,
    date: str,
    events: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    if not settings.get("helper_enabled"):
        return {"summary": "", "used_helper": False}
    if not settings.get("helper_model"):
        return {"summary": "", "used_helper": False}

    if not events:
        return {"summary": "No memories found for that date.", "used_helper": False}

    lines: list[str] = []
    for event in events[:50]:
        timestamp = str(event.get("timestamp", ""))
        kind = str(event.get("kind", ""))
        source = str(event.get("source", ""))
        text = str(event.get("text", ""))[:400]
        lines.append(f"- {timestamp} [{source}/{kind}] {text}")

    prompt = (
        f"Summarize memories for date {date}. "
        "Return a concise human summary with sections:\n"
        "Summary:\n"
        "Key items:\n"
        "Decisions:\n"
        "Open items:\n\n"
        "Memories:\n"
        + "\n".join(lines)
    )

    request_payload = {
        "model": settings["helper_model"],
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    request_data = json.dumps(request_payload, ensure_ascii=True).encode("utf-8")
    req = urllib_request.Request(
        settings["helper_base_url"],
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=settings["helper_timeout_sec"]) as resp:
            body = resp.read().decode("utf-8")
    except (urllib_error.URLError, TimeoutError, OSError):
        return {"summary": "", "used_helper": False}

    try:
        outer = json.loads(body)
    except json.JSONDecodeError:
        return {"summary": "", "used_helper": False}

    summary_text = outer.get("response") if isinstance(outer, dict) else ""
    return {"summary": str(summary_text or "").strip(), "used_helper": True}


def store_graph_memory(
    event: dict[str, Any],
    *,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    group_id: str,
    knowledge: dict[str, Any] | None = None,
) -> None:
    if GraphDatabase is None:
        raise RuntimeError(
            "Neo4j driver is unavailable. Install optional dependency with: pip install neo4j"
        )
    extracted = knowledge or {"entities": [], "relations": [], "summary": ""}
    project = _safe_name(event.get("project")) or "unscoped"
    project_key = _project_key(project)
    event_date = _event_date(str(event.get("timestamp", ""))) or "unknown-date"
    query = """
    MERGE (g:MemoryGroup {id: $group_id})
    MERGE (p:Project {group_id: $group_id, key: $project_key})
    ON CREATE SET
        p.name = $project,
        p.created_at = $timestamp
    SET
        p.name = $project,
        p.updated_at = $timestamp
    MERGE (d:Day {group_id: $group_id, date: $event_date})
    ON CREATE SET d.created_at = $timestamp
    SET d.updated_at = $timestamp
    MERGE (pd:ProjectDay {group_id: $group_id, project_key: $project_key, date: $event_date})
    ON CREATE SET
        pd.project = $project,
        pd.created_at = $timestamp
    SET
        pd.project = $project,
        pd.updated_at = $timestamp
    MERGE (g)-[:HAS_PROJECT]->(p)
    MERGE (g)-[:HAS_DAY]->(d)
    MERGE (p)-[:HAS_DAY]->(pd)
    MERGE (pd)-[:ON_DAY]->(d)
    MERGE (m:Memory {id: $event_id})
    ON CREATE SET
        m.created_at = $timestamp
    SET
        m.text = $text,
        m.kind = $kind,
        m.source = $source,
        m.project = $project,
        m.cwd = $cwd,
        m.importance = $importance,
        m.tags = $tags,
        m.metadata_json = $metadata_json,
        m.extraction_summary = $summary,
        m.updated_at = $timestamp
    MERGE (g)-[:HAS_MEMORY]->(m)
    MERGE (p)-[:HAS_MEMORY]->(m)
    MERGE (d)-[:HAS_MEMORY]->(m)
    MERGE (pd)-[:HAS_MEMORY]->(m)
    FOREACH (entity IN $entities |
        MERGE (e:Entity {group_id: $group_id, name_key: entity.name_key})
        ON CREATE SET
            e.name = entity.name,
            e.entity_type = entity.entity_type,
            e.created_at = $timestamp
        SET
            e.updated_at = $timestamp,
            e.last_seen_at = $timestamp
        MERGE (g)-[:HAS_ENTITY]->(e)
        MERGE (p)-[:HAS_ENTITY]->(e)
        MERGE (d)-[:MENTIONED_ENTITY]->(e)
        MERGE (pd)-[:MENTIONED_ENTITY]->(e)
        MERGE (m)-[me:MENTIONS]->(e)
        SET me.role = entity.role,
            me.updated_at = $timestamp
    )
    FOREACH (relation IN $relations |
        MERGE (source_entity:Entity {group_id: $group_id, name_key: relation.source_key})
        ON CREATE SET
            source_entity.name = relation.source,
            source_entity.entity_type = 'unknown',
            source_entity.created_at = $timestamp
        SET
            source_entity.updated_at = $timestamp,
            source_entity.last_seen_at = $timestamp
        MERGE (target_entity:Entity {group_id: $group_id, name_key: relation.target_key})
        ON CREATE SET
            target_entity.name = relation.target,
            target_entity.entity_type = 'unknown',
            target_entity.created_at = $timestamp
        SET
            target_entity.updated_at = $timestamp,
            target_entity.last_seen_at = $timestamp
        MERGE (g)-[:HAS_ENTITY]->(source_entity)
        MERGE (g)-[:HAS_ENTITY]->(target_entity)
        MERGE (source_entity)-[r:RELATES_TO {
            group_id: $group_id,
            source_key: relation.source_key,
            target_key: relation.target_key,
            rel_type: relation.rel_type
        }]->(target_entity)
        ON CREATE SET r.created_at = $timestamp
        SET
            r.updated_at = $timestamp,
            r.fact = relation.fact,
            r.memory_id = $event_id
    )
    """

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    try:
        try:
            with driver.session() as session:
                session.run(
                    query,
                    group_id=group_id,
                    event_id=event["id"],
                    text=event["text"],
                    kind=event["kind"],
                    source=event["source"],
                    project=project,
                    project_key=project_key,
                    event_date=event_date,
                    cwd=event["cwd"],
                    importance=event["importance"],
                    tags=event["tags"],
                    metadata_json=json.dumps(event["metadata"], ensure_ascii=True, sort_keys=True),
                    summary=extracted.get("summary", ""),
                    entities=extracted.get("entities", []),
                    relations=extracted.get("relations", []),
                    timestamp=event["timestamp"],
                ).consume()
        except AuthError as exc:
            raise RuntimeError(
                "Neo4j authentication failed. Update NEO4J_USER/NEO4J_PASSWORD in "
                "your app-home config or memory_gateway/.env to match your local Neo4j instance."
            ) from exc
        except ServiceUnavailable as exc:
            raise RuntimeError(
                "Neo4j is unavailable at the configured URI. Start Neo4j Desktop and "
                "verify NEO4J_URI in your app-home config or memory_gateway/.env."
            ) from exc
    finally:
        driver.close()


def load_settings() -> dict[str, Any]:
    return load_runtime_settings()


def _matches_filter(event: dict[str, Any], *, project: str = "", source: str = "", kind: str = "") -> bool:
    if project and event.get("project") != project:
        return False
    if source and event.get("source") != source:
        return False
    if kind and event.get("kind") != kind:
        return False
    return True


def get_recent_events(limit: int = 20, *, project: str = "", source: str = "", kind: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    filtered = [
        event
        for event in events
        if _matches_filter(event, project=project, source=source, kind=kind) and not _is_superseded(event, supersession_map)
    ]
    ordered = list(reversed(filtered[-limit:]))
    annotated: list[dict[str, Any]] = []
    for idx, event in enumerate(ordered):
        item = dict(event)
        confidence = _clamp_confidence(0.72 - (idx * 0.03))
        if event.get("importance") == "high":
            confidence = _clamp_confidence(confidence + 0.18)
        item["retrieval"] = {
            "confidence": round(confidence, 3),
            "score": round(confidence * 10.0, 3),
            "score_breakdown": {
                "importance_boost": 1.8 if event.get("importance") == "high" else 0.0,
                "position_decay": round(idx * 0.3, 3),
            },
            "match_type": "recent_context",
        }
        annotated.append(item)
    return annotated


def get_events_by_date(date_str: str, *, project: str = "", source: str = "", kind: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    filtered: list[dict[str, Any]] = []
    for event in events:
        if not _matches_filter(event, project=project, source=source, kind=kind):
            continue
        if _is_superseded(event, supersession_map):
            continue
        timestamp = str(event.get("timestamp", ""))
        if timestamp.startswith(date_str):
            filtered.append(event)
    return filtered


def search_events(query: str, limit: int = 10, *, project: str = "", source: str = "", kind: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    query_lower = query.lower().strip()
    raw_query_tokens = [token for token in re.findall(r"[a-z0-9_]+", query_lower) if token]
    signal_tokens = [token for token in raw_query_tokens if token not in LOW_SIGNAL_QUERY_TOKENS]
    query_tokens = signal_tokens or raw_query_tokens
    now_utc = datetime.now(timezone.utc)
    scored: list[tuple[float, dict[str, Any]]] = []

    for event in events:
        if not _matches_filter(event, project=project, source=source, kind=kind):
            continue
        if _is_superseded(event, supersession_map):
            continue

        haystacks = [
            str(event.get("text", "")),
            str(event.get("kind", "")),
            str(event.get("source", "")),
            str(event.get("project", "")),
            " ".join(event.get("tags", [])),
        ]
        combined = " ".join(haystacks).lower()
        event_tokens = re.findall(r"[a-z0-9_]+", combined)
        has_exact_substring = (" " in query_lower) and bool(query_lower) and query_lower in combined
        token_hits = 0
        for token in query_tokens:
            if len(token) <= 2:
                matched = any(event_token == token for event_token in event_tokens)
            else:
                matched = any(event_token == token or event_token.startswith(token) for event_token in event_tokens)
            if matched:
                token_hits += 1
        token_hit_ratio = (token_hits / len(query_tokens)) if query_tokens else 0.0
        has_token_match = bool(query_tokens) and (
            token_hits == len(query_tokens) or (len(query_tokens) >= 3 and token_hit_ratio >= 0.7)
        )
        if not has_exact_substring and not has_token_match:
            continue

        score = 0.0
        score_breakdown: dict[str, float] = {
            "exact_phrase": 0.0,
            "token_match": 0.0,
            "token_ratio": 0.0,
            "importance_boost": 0.0,
            "project_boost": 0.0,
            "recency_boost": 0.0,
        }
        match_type = "token_partial"
        if has_exact_substring:
            exact_hits = float(combined.count(query_lower))
            score += exact_hits
            score += 1.0
            score_breakdown["exact_phrase"] = exact_hits + 1.0
            match_type = "exact_phrase"
        if has_token_match:
            token_count_hits = 0
            for token in query_tokens:
                for event_token in event_tokens:
                    if len(token) <= 2:
                        if event_token == token:
                            token_count_hits += 1
                    elif event_token == token or event_token.startswith(token):
                        token_count_hits += 1
            score += float(token_count_hits)
            score += token_hit_ratio
            score_breakdown["token_match"] = float(token_count_hits)
            score_breakdown["token_ratio"] = token_hit_ratio
            if match_type != "exact_phrase" and token_hit_ratio >= 1.0:
                match_type = "token_full"
        if event.get("importance") == "high":
            score += 2.0
            score_breakdown["importance_boost"] = 2.0
        event_project = str(event.get("project", "")).strip()
        if project and event_project == project:
            score += 3.0
            score_breakdown["project_boost"] = 3.0
        # Prefer fresher context without making old memories disappear.
        event_time = _parse_iso8601(str(event.get("timestamp", "")))
        if event_time is not None:
            age_days = max(0.0, (now_utc - event_time.astimezone(timezone.utc)).total_seconds() / 86400.0)
            recency_boost = max(0.0, 3.0 - min(3.0, age_days / 3.0))
            score += recency_boost
            score_breakdown["recency_boost"] = recency_boost

        # Convert scoring signal into a stable confidence metric for agents.
        confidence = _clamp_confidence(score / 10.0)
        annotated = dict(event)
        annotated["retrieval"] = {
            "confidence": round(confidence, 3),
            "score": round(score, 3),
            "score_breakdown": {key: round(val, 3) for key, val in score_breakdown.items()},
            "match_type": match_type,
        }
        scored.append((score, annotated))

    scored.sort(key=lambda item: (item[0], item[1].get("timestamp", "")), reverse=True)
    return [event for _, event in scored[:limit]]


def get_project_context(project: str, limit: int = 12) -> dict[str, list[dict[str, Any]]]:
    recent_raw = get_recent_events(limit=limit, project=project)
    recent: list[dict[str, Any]] = []
    for idx, event in enumerate(recent_raw):
        annotated = dict(event)
        confidence = _clamp_confidence(0.75 - (idx * 0.04))
        if event.get("importance") == "high":
            confidence = _clamp_confidence(confidence + 0.15)
        annotated["retrieval"] = {
            "confidence": round(confidence, 3),
            "score": round(confidence * 10.0, 3),
            "score_breakdown": {
                "importance_boost": 1.5 if event.get("importance") == "high" else 0.0,
                "position_decay": round(idx * 0.4, 3),
            },
            "match_type": "recent_context",
        }
        recent.append(annotated)
    important = [event for event in recent if event.get("importance") == "high"][:limit]
    return {
        "important": important,
        "recent": recent,
    }


def _build_structured_summary_text(fields: dict[str, Any]) -> str:
    rendered: list[str] = []
    label_map = {
        "goal": "Goal",
        "changes": "Changes",
        "decision": "Decisions",
        "why": "Why",
        "validation": "Validation",
        "next_step": "Next Step",
        "risk": "Risks/TODO",
    }
    for key in STRUCTURED_SUMMARY_FIELDS:
        value = str(fields.get(key, "") or "").strip()
        if value:
            rendered.append(f"{label_map[key]}: {value}")
    return "\n".join(rendered)


def store_structured_memory(
    *,
    kind: str = "task_summary",
    source: str = "agent",
    project: str = "",
    cwd: str = "",
    importance: str = "normal",
    tags: list[str] | None = None,
    graph: bool = False,
    metadata: dict[str, Any] | None = None,
    timestamp: str = "",
    repo_context: dict[str, Any] | None = None,
    goal: str = "",
    changes: str = "",
    decision: str = "",
    why: str = "",
    validation: str = "",
    next_step: str = "",
    risk: str = "",
    title: str = "",
    summary: str = "",
    status: str = "",
) -> dict[str, Any]:
    structured = {
        "goal": goal,
        "changes": changes,
        "decision": decision,
        "why": why,
        "validation": validation,
        "next_step": next_step,
        "risk": risk,
        "title": title,
        "status": status,
        "summary": summary,
    }
    text = str(summary or "").strip() or _build_structured_summary_text(structured)
    event_metadata = dict(metadata or {})
    event_metadata["structured"] = {key: value for key, value in structured.items() if str(value or "").strip()}
    if repo_context:
        event_metadata["repo_context"] = {
            key: value
            for key, value in dict(repo_context).items()
            if value not in ("", [], {}, None)
        }
    payload: dict[str, Any] = {
        "text": text,
        "kind": kind,
        "source": source,
        "project": project,
        "cwd": cwd,
        "importance": importance,
        "tags": tags or [],
        "graph": graph,
        "metadata": event_metadata,
    }
    if timestamp:
        payload["timestamp"] = timestamp
    return persist_event(payload)


def _auto_open_loop_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(event.get("kind", "")).strip().lower()
    if kind in {"open_loop", "open_loop_update", "daily_checkin", "daily_checkout"}:
        return None
    metadata = _metadata(event)
    if metadata.get("auto_open_loop") is False:
        return None
    structured = _structured_metadata(event)
    next_step = str(structured.get("next_step", "") or "").strip()
    risk = str(structured.get("risk", "") or "").strip()
    if not next_step and not risk:
        return None
    title = (
        str(structured.get("title", "") or "").strip()
        or str(structured.get("goal", "") or "").strip()
        or f"Follow-up from {kind}"
    )
    loop_id = str(metadata.get("loop_id", "") or metadata.get("auto_open_loop_id", "") or event.get("id", "")).strip()
    repo_context = dict(_repo_context(event))
    loop_metadata = {
        "loop_id": loop_id,
        "title": title,
        "status": "open",
        "next_step": next_step,
        "risk": risk,
        "auto_generated_from": str(event.get("id", "")),
        "auto_open_loop": False,
    }
    files = _event_files(event)
    commands = _event_commands(event)
    if files:
        loop_metadata["files_touched"] = files
    if commands:
        loop_metadata["commands_run"] = commands
    return {
        "text": _build_structured_summary_text(
            {
                "goal": title,
                "next_step": next_step,
                "risk": risk,
            }
        ),
        "kind": "open_loop",
        "source": event.get("source", "agent"),
        "project": event.get("project", ""),
        "cwd": event.get("cwd", ""),
        "importance": "high" if event.get("importance") == "high" else "normal",
        "tags": list(dict.fromkeys([*event.get("tags", []), "auto-open-loop"])),
        "graph": bool(event.get("graph", False)),
        "metadata": {
            **loop_metadata,
            "structured": {
                "title": title,
                "goal": title,
                "next_step": next_step,
                "risk": risk,
                "status": "open",
            },
            "repo_context": repo_context,
        },
        "timestamp": event.get("timestamp", ""),
    }


def _collect_open_loops(events: list[dict[str, Any]], *, project: str = "", cwd: str = "") -> list[dict[str, Any]]:
    loops: dict[str, dict[str, Any]] = {}
    for event in events:
        if not _event_scope_match(event, project=project, cwd=cwd):
            continue
        kind = str(event.get("kind", "")).strip().lower()
        if kind not in {"open_loop", "open_loop_update"}:
            continue
        md = _metadata(event)
        loop_id = str(md.get("loop_id", "") or event.get("id", "")).strip()
        if not loop_id:
            continue
        existing = loops.get(loop_id, {})
        structured = _structured_metadata(event)
        title = str(md.get("title", "") or structured.get("title", "") or existing.get("title", "")).strip()
        note = str(md.get("note", "") or event.get("text", "")).strip()
        next_step = str(md.get("next_step", "") or structured.get("next_step", "") or existing.get("next_step", "")).strip()
        risk = str(md.get("risk", "") or structured.get("risk", "") or existing.get("risk", "")).strip()
        status = _open_loop_status(md.get("status", existing.get("status", "open")))
        files_touched = _listify_str_values(md.get("files_touched", [])) or existing.get("files_touched", [])
        commands_run = _listify_str_values(md.get("commands_run", [])) or existing.get("commands_run", [])
        record = {
            "loop_id": loop_id,
            "title": title,
            "status": status,
            "project": event.get("project", ""),
            "cwd": event.get("cwd", ""),
            "kind": kind,
            "created_at": existing.get("created_at", event.get("timestamp", "")),
            "updated_at": event.get("timestamp", ""),
            "note": note,
            "next_step": next_step,
            "risk": risk,
            "files_touched": files_touched,
            "commands_run": commands_run,
            "source_event_id": event.get("id", ""),
        }
        loops[loop_id] = record
    return sorted(loops.values(), key=lambda item: item.get("updated_at", ""), reverse=True)


def get_open_loops(
    *,
    project: str = "",
    cwd: str = "",
    status: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    loops = _collect_open_loops(events, project=project, cwd=cwd)
    if status:
        loops = [item for item in loops if item.get("status") == status]
    return {
        "project": project,
        "cwd": cwd,
        "status": status or "all",
        "count": min(limit, len(loops)),
        "items": loops[:limit],
    }


def _task_context_score(
    event: dict[str, Any],
    *,
    query_tokens: list[str],
    file_paths: list[str],
    active_files: list[str],
    project: str = "",
    cwd: str = "",
) -> tuple[float, dict[str, float]]:
    haystacks = [
        str(event.get("text", "")),
        str(event.get("kind", "")),
        str(event.get("project", "")),
        " ".join(_event_files(event)),
        " ".join(_event_commands(event)),
        " ".join(_event_tests(event)),
    ]
    combined = " ".join(haystacks).lower()
    score = 0.0
    breakdown = {
        "token_match": 0.0,
        "file_overlap": 0.0,
        "active_diff_overlap": 0.0,
        "project_scope": 0.0,
        "cwd_scope": 0.0,
        "importance": 0.0,
        "recency": 0.0,
        "open_loop": 0.0,
        "validation": 0.0,
        "negative_memory": 0.0,
    }
    if query_tokens:
        token_hits = sum(1 for token in query_tokens if token in combined)
        breakdown["token_match"] = float(token_hits)
        score += float(token_hits)
    if file_paths:
        files = " ".join(_event_files(event)).lower()
        overlap = sum(1 for path in file_paths if path.lower() in files)
        breakdown["file_overlap"] = float(overlap) * 2.0
        score += breakdown["file_overlap"]
    if active_files:
        files = " ".join(_event_files(event)).lower()
        active_overlap = sum(1 for path in active_files if path.lower() in files)
        breakdown["active_diff_overlap"] = float(active_overlap) * 2.5
        score += breakdown["active_diff_overlap"]
    if project and str(event.get("project", "")) == project:
        breakdown["project_scope"] = 3.0
        score += 3.0
    if cwd and str(event.get("cwd", "")) == cwd:
        breakdown["cwd_scope"] = 2.0
        score += 2.0
    if event.get("importance") == "high":
        breakdown["importance"] = 2.0
        score += 2.0
    if str(event.get("kind", "")) == "open_loop":
        breakdown["open_loop"] = 2.5
        score += 2.5
    if _structured_field(event, "validation") or _event_tests(event):
        breakdown["validation"] = 1.0
        score += 1.0
    if str(event.get("kind", "")) in {"failed_attempt", "bug"}:
        breakdown["negative_memory"] = 1.25
        score += 1.25
    event_time = _parse_iso8601(str(event.get("timestamp", "")))
    if event_time is not None:
        age_days = max(0.0, (datetime.now(timezone.utc) - event_time.astimezone(timezone.utc)).total_seconds() / 86400.0)
        breakdown["recency"] = max(0.0, 2.5 - min(2.5, age_days / 5.0))
        score += breakdown["recency"]
    return score, breakdown


def get_task_context(
    query: str,
    *,
    project: str = "",
    cwd: str = "",
    file_paths: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    query_tokens = [token for token in re.findall(r"[a-z0-9_]+", query.lower()) if token not in LOW_SIGNAL_QUERY_TOKENS]
    paths = _listify_str_values(file_paths or [])
    active_files = _git_changed_files(cwd)
    scored: list[tuple[float, dict[str, Any]]] = []
    for event in events:
        if project and str(event.get("project", "")) != project:
            continue
        if _is_superseded(event, supersession_map):
            continue
        score, breakdown = _task_context_score(
            event,
            query_tokens=query_tokens,
            file_paths=paths,
            active_files=active_files,
            project=project,
            cwd=cwd,
        )
        if score <= 0.0:
            continue
        annotated = dict(event)
        annotated["retrieval"] = {
            "score": round(score, 3),
            "confidence": round(_clamp_confidence(score / 12.0), 3),
            "match_type": "task_context",
            "score_breakdown": {key: round(value, 3) for key, value in breakdown.items()},
        }
        scored.append((score, annotated))
    scored.sort(key=lambda item: (item[0], item[1].get("timestamp", "")), reverse=True)
    results = [event for _, event in scored[:limit]]
    negative = [event for event in results if str(event.get("kind", "")) in {"failed_attempt", "bug"}][:5]
    validations = [
        {
            "event_id": event.get("id", ""),
            "timestamp": event.get("timestamp", ""),
            "validation": _structured_field(event, "validation"),
            "tests": _event_tests(event),
            "commands_run": _event_commands(event),
        }
        for event in results
        if _structured_field(event, "validation") or _event_tests(event) or _event_commands(event)
    ][:5]
    return {
        "query": query,
        "project": project,
        "cwd": cwd,
        "file_paths": paths,
        "active_diff_files": active_files,
        "results": results,
        "negative_memories": negative,
        "validations": validations,
    }


def get_project_canon(*, project: str = "", cwd: str = "", limit: int = 12) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    canon: list[dict[str, Any]] = []
    for event in reversed(events):
        if not _event_scope_match(event, project=project, cwd=cwd):
            continue
        if _is_superseded(event, supersession_map):
            continue
        kind = str(event.get("kind", "")).strip().lower()
        is_canon = bool(_metadata(event).get("canon"))
        if kind not in {"decision", "preference", "project_fact", "identity"} and not is_canon:
            continue
        canon.append(
            {
                "event_id": event.get("id", ""),
                "kind": kind,
                "timestamp": event.get("timestamp", ""),
                "text": event.get("text", ""),
                "project": event.get("project", ""),
                "importance": event.get("importance", ""),
                "canon": is_canon,
            }
        )
        if len(canon) >= limit:
            break
    return {
        "project": project,
        "cwd": cwd,
        "count": len(canon),
        "items": canon,
    }


def get_execution_hints(*, project: str = "", cwd: str = "", limit: int = 8) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    commands: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    seen_commands: set[str] = set()
    seen_tests: set[str] = set()
    for event in reversed(events):
        if not _event_scope_match(event, project=project, cwd=cwd):
            continue
        if _is_superseded(event, supersession_map):
            continue
        for command in _event_commands(event):
            if command in seen_commands:
                continue
            seen_commands.add(command)
            commands.append(
                {
                    "command": command,
                    "timestamp": event.get("timestamp", ""),
                    "event_id": event.get("id", ""),
                    "validation": _structured_field(event, "validation"),
                }
            )
            if len(commands) >= limit:
                break
        for test_name in _event_tests(event):
            if test_name in seen_tests:
                continue
            seen_tests.add(test_name)
            tests.append(
                {
                    "test": test_name,
                    "timestamp": event.get("timestamp", ""),
                    "event_id": event.get("id", ""),
                }
            )
            if len(tests) >= limit:
                break
        if str(event.get("kind", "")) in {"failed_attempt", "bug"}:
            warning = _first_structured_item(event, "risk") or _first_structured_item(event, "decision") or str(event.get("text", ""))
            if warning:
                warnings.append(
                    {
                        "event_id": event.get("id", ""),
                        "timestamp": event.get("timestamp", ""),
                        "warning": warning,
                    }
                )
        if len(commands) >= limit and len(tests) >= limit and len(warnings) >= limit:
            break
    return {
        "project": project,
        "cwd": cwd,
        "commands": commands[:limit],
        "tests": tests[:limit],
        "warnings": warnings[:limit],
    }


def get_timeline(
    *,
    project: str = "",
    cwd: str = "",
    since: str = "",
    until: str = "",
    days: int = 7,
    limit: int = 30,
) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    now = datetime.now(timezone.utc)
    since_time = _parse_iso8601(since) if since else now - timedelta(days=max(1, days))
    until_time = _parse_iso8601(until) if until else now + timedelta(seconds=1)
    selected: list[dict[str, Any]] = []
    for event in reversed(events):
        if not _event_scope_match(event, project=project, cwd=cwd):
            continue
        if _is_superseded(event, supersession_map):
            continue
        event_time = _parse_iso8601(str(event.get("timestamp", "")))
        if event_time is None:
            continue
        event_utc = event_time.astimezone(timezone.utc)
        if event_utc < since_time or event_utc > until_time:
            continue
        selected.append(event)
        if len(selected) >= limit:
            break
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in selected:
        grouped.setdefault(_event_date(str(event.get("timestamp", ""))), []).append(
            {
                "event_id": event.get("id", ""),
                "timestamp": event.get("timestamp", ""),
                "kind": event.get("kind", ""),
                "project": event.get("project", ""),
                "text": event.get("text", ""),
            }
        )
    return {
        "project": project,
        "cwd": cwd,
        "since": since_time.isoformat(),
        "until": until_time.isoformat(),
        "days": [
            {"date": date, "events": items}
            for date, items in sorted(grouped.items(), reverse=True)
        ],
    }


def get_memory_quality_report(*, project: str = "", cwd: str = "", limit: int = 100) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    scoped = [
        event
        for event in events
        if _event_scope_match(event, project=project, cwd=cwd) and not _is_superseded(event, supersession_map)
    ][-limit:]
    issues: list[dict[str, Any]] = []
    normalized_seen: dict[tuple[str, str, str], str] = {}
    for event in scoped:
        event_id = str(event.get("id", ""))
        kind = str(event.get("kind", ""))
        text_key = _normalized_text_key(str(event.get("text", "")))
        dedupe_key = (str(event.get("project", "")), kind, text_key)
        if text_key and dedupe_key in normalized_seen:
            issues.append({"event_id": event_id, "severity": "warn", "code": "duplicate_text", "message": "near-identical memory already exists"})
        else:
            normalized_seen[dedupe_key] = event_id
        if kind in {"task_summary", "failed_attempt", "fix"}:
            sections = _structured_metadata(event) or _extract_summary_sections(str(event.get("text", "")))
            if not str(sections.get("validation", "")).strip():
                issues.append({"event_id": event_id, "severity": "warn", "code": "missing_validation", "message": "high-signal memory has no validation"})
            if not str(sections.get("next_step", "")).strip() and kind != "fix":
                issues.append({"event_id": event_id, "severity": "warn", "code": "missing_next_step", "message": "memory does not capture what to do next"})
        if kind in {"open_loop", "open_loop_update"}:
            md = _metadata(event)
            if not str(md.get("loop_id", "")).strip():
                issues.append({"event_id": event_id, "severity": "error", "code": "missing_loop_id", "message": "open loop event missing loop_id"})
        if not str(event.get("project", "")).strip() and not str(event.get("cwd", "")).strip():
            issues.append({"event_id": event_id, "severity": "warn", "code": "missing_scope", "message": "memory is not scoped to project or cwd"})
    return {
        "project": project,
        "cwd": cwd,
        "checked_events": len(scoped),
        "issue_count": len(issues),
        "issues": issues[:limit],
    }


def promote_memory_to_canon(
    *,
    event_id: str,
    project: str = "",
    cwd: str = "",
    title: str = "",
    kind: str = "project_fact",
    note: str = "",
) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    target = next((event for event in events if str(event.get("id", "")) == event_id), None)
    if target is None:
        return {"ok": False, "error": f"event_id not found: {event_id}"}
    target_project = project or str(target.get("project", ""))
    target_cwd = cwd or str(target.get("cwd", ""))
    canon_title = title or _first_structured_item(target, "goal") or str(target.get("kind", "canon"))
    canon_text = note or str(target.get("text", ""))
    metadata = {
        "canon": True,
        "source_event_id": event_id,
        "canon_title": canon_title,
        "auto_open_loop": False,
    }
    result = store_structured_memory(
        kind=kind,
        source="agent",
        project=target_project,
        cwd=target_cwd,
        importance="high",
        tags=list(dict.fromkeys([*target.get("tags", []), "canon"])),
        graph=True,
        metadata=metadata,
        title=canon_title,
        summary=canon_text,
    )
    return {"ok": True, "source_event_id": event_id, "canon_event": result.get("event", {}), "result": result}


def mark_memory_superseded(
    *,
    old_event_id: str,
    new_event_id: str = "",
    reason: str = "",
    project: str = "",
    cwd: str = "",
) -> dict[str, Any]:
    metadata = {
        "old_event_id": old_event_id,
        "new_event_id": new_event_id,
        "reason": reason,
        "auto_open_loop": False,
    }
    result = persist_event(
        {
            "text": f"Superseded {old_event_id}" + (f" with {new_event_id}" if new_event_id else ""),
            "kind": "supersession",
            "source": "agent",
            "project": project,
            "cwd": cwd,
            "importance": "normal",
            "tags": ["cleanup", "supersession"],
            "graph": False,
            "metadata": metadata,
        }
    )
    return {"ok": True, "supersession": result.get("event", {}), "result": result}


def get_cleanup_candidates(*, project: str = "", cwd: str = "", limit: int = 20) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    scoped = [
        event
        for event in events
        if _event_scope_match(event, project=project, cwd=cwd) and not _is_superseded(event, supersession_map)
    ]
    candidates: list[dict[str, Any]] = []
    for idx, event in enumerate(scoped):
        kind = str(event.get("kind", "")).strip().lower()
        if kind not in {"task_summary", "failed_attempt", "project_fact", "decision"}:
            continue
        for other in scoped[idx + 1 :]:
            if str(other.get("kind", "")).strip().lower() != kind:
                continue
            similarity = _text_similarity(str(event.get("text", "")), str(other.get("text", "")))
            if similarity < 0.88:
                continue
            candidates.append(
                {
                    "kind": kind,
                    "similarity": round(similarity, 4),
                    "older_event_id": event.get("id", ""),
                    "newer_event_id": other.get("id", ""),
                    "older_timestamp": event.get("timestamp", ""),
                    "newer_timestamp": other.get("timestamp", ""),
                    "older_text": str(event.get("text", ""))[:180],
                    "newer_text": str(other.get("text", ""))[:180],
                }
            )
            if len(candidates) >= limit:
                return {"project": project, "cwd": cwd, "count": len(candidates), "items": candidates}
    return {"project": project, "cwd": cwd, "count": len(candidates), "items": candidates}


def get_machine_context(*, project: str = "", cwd: str = "", limit: int = 12) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    scoped = [
        event
        for event in events
        if _event_scope_match(event, project=project, cwd=cwd) and not _is_superseded(event, supersession_map)
    ]
    canon = get_project_canon(project=project, cwd=cwd, limit=limit)
    setup_events: list[dict[str, Any]] = []
    for event in reversed(scoped):
        tags = set(event.get("tags", []))
        text = str(event.get("text", "")).lower()
        if tags.intersection({"rollout", "setup", "install", "wrappers", "cursor"}) or "wrapper" in text or "cursor" in text:
            setup_events.append(
                {
                    "event_id": event.get("id", ""),
                    "kind": event.get("kind", ""),
                    "timestamp": event.get("timestamp", ""),
                    "text": event.get("text", ""),
                }
            )
        if len(setup_events) >= limit:
            break
    return {
        "project": project,
        "cwd": cwd,
        "profile": settings.get("profile", ""),
        "helper_enabled": bool(settings.get("helper_enabled")),
        "helper_model": settings.get("helper_model", ""),
        "vault_path": settings.get("vault_path", ""),
        "memory_log_path": settings.get("memory_log_path", ""),
        "canon": canon,
        "setup_events": setup_events,
    }


def start_session(
    *,
    project: str = "",
    cwd: str = "",
    query: str = "",
    file_paths: list[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    supersession_map = _supersession_map(events)
    scoped = [
        event
        for event in events
        if _event_scope_match(event, project=project, cwd=cwd) and not _is_superseded(event, supersession_map)
    ]
    recent = list(reversed(scoped[-limit:]))
    last_summary = next(
        (event for event in reversed(scoped) if str(event.get("kind", "")) in {"task_summary", "daily_checkout", "daily_checkin", "failed_attempt", "fix"}),
        None,
    )
    open_loops = [item for item in _collect_open_loops(events, project=project, cwd=cwd) if item.get("status") in ACTIVE_LOOP_STATUSES][:limit]
    decisions: list[dict[str, Any]] = []
    for event in reversed(scoped):
        if str(event.get("kind", "")) not in {"decision", "preference", "project_fact"}:
            continue
        decisions.append(
            {
                "event_id": event.get("id", ""),
                "kind": event.get("kind", ""),
                "timestamp": event.get("timestamp", ""),
                "text": event.get("text", ""),
            }
        )
        if len(decisions) >= limit:
            break
    unfinished = []
    for event in recent:
        next_step = _first_structured_item(event, "next_step")
        risk = _first_structured_item(event, "risk")
        if next_step or risk:
            unfinished.append(
                {
                    "event_id": event.get("id", ""),
                    "timestamp": event.get("timestamp", ""),
                    "next_step": next_step,
                    "risk": risk,
                }
            )
    return {
        "project": project,
        "cwd": cwd,
        "query": query,
        "project_canon": get_project_canon(project=project, cwd=cwd, limit=limit),
        "recent_events": recent,
        "last_summary": last_summary or {},
        "open_loops": open_loops,
        "active_decisions": decisions,
        "unfinished_threads": unfinished[:limit],
        "task_context": get_task_context(query or project or cwd or "recent work", project=project, cwd=cwd, file_paths=file_paths or [], limit=limit),
        "execution_hints": get_execution_hints(project=project, cwd=cwd, limit=limit),
        "timeline": get_timeline(project=project, cwd=cwd, days=3, limit=limit),
        "brain_health": {
            "raw_event_count": len(events),
            "helper_enabled": bool(settings.get("helper_enabled")),
            "helper_model": settings.get("helper_model", ""),
        },
    }


def _decode_graph_record(record: Any) -> dict[str, Any]:
    metadata_json = record.get("metadata_json", "")
    metadata: dict[str, Any] = {}
    if metadata_json:
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {"raw": metadata_json}

    return {
        "text": record.get("text", ""),
        "kind": record.get("kind", ""),
        "source": record.get("source", ""),
        "project": record.get("project", ""),
        "cwd": record.get("cwd", ""),
        "importance": record.get("importance", ""),
        "tags": record.get("tags", []),
        "metadata": metadata,
        "timestamp": record.get("created_at", ""),
        "storage": "graph",
    }


def get_graph_recent(limit: int = 20, *, project: str = "", source: str = "", kind: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    if GraphDatabase is None:
        return []
    if not settings["neo4j_uri"] or not settings["neo4j_user"] or not settings["neo4j_password"]:
        return []
    query = """
    MATCH (:MemoryGroup {id: $group_id})-[:HAS_MEMORY]->(m:Memory)
    WHERE ($project = '' OR m.project = $project)
      AND ($source = '' OR m.source = $source)
      AND ($kind = '' OR m.kind = $kind)
    RETURN m.text AS text,
           m.kind AS kind,
           m.source AS source,
           m.project AS project,
           m.cwd AS cwd,
           m.importance AS importance,
           m.tags AS tags,
           m.metadata_json AS metadata_json,
           m.created_at AS created_at
    ORDER BY m.created_at DESC
    LIMIT $limit
    """
    driver = GraphDatabase.driver(settings["neo4j_uri"], auth=(settings["neo4j_user"], settings["neo4j_password"]))
    try:
        with driver.session() as session:
            records = session.run(
                query,
                group_id=settings["group_id"],
                project=project,
                source=source,
                kind=kind,
                limit=limit,
            )
            return [_decode_graph_record(record) for record in records]
    except Exception:
        return []
    finally:
        driver.close()


def search_graph(query_text: str, limit: int = 10, *, project: str = "", source: str = "", kind: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    if GraphDatabase is None:
        return []
    if not settings["neo4j_uri"] or not settings["neo4j_user"] or not settings["neo4j_password"]:
        return []
    query = """
    MATCH (:MemoryGroup {id: $group_id})-[:HAS_MEMORY]->(m:Memory)
    WHERE ($project = '' OR m.project = $project)
      AND ($source = '' OR m.source = $source)
      AND ($kind = '' OR m.kind = $kind)
      AND toLower(m.text) CONTAINS toLower($query_text)
    RETURN m.text AS text,
           m.kind AS kind,
           m.source AS source,
           m.project AS project,
           m.cwd AS cwd,
           m.importance AS importance,
           m.tags AS tags,
           m.metadata_json AS metadata_json,
           m.created_at AS created_at
    ORDER BY m.created_at DESC
    LIMIT $limit
    """
    driver = GraphDatabase.driver(settings["neo4j_uri"], auth=(settings["neo4j_user"], settings["neo4j_password"]))
    try:
        with driver.session() as session:
            records = session.run(
                query,
                group_id=settings["group_id"],
                project=project,
                source=source,
                kind=kind,
                query_text=query_text,
                limit=limit,
            )
            return [_decode_graph_record(record) for record in records]
    except Exception:
        return []
    finally:
        driver.close()


def get_entity_context(query_text: str, limit: int = 8) -> list[dict[str, Any]]:
    settings = load_settings()
    if GraphDatabase is None:
        return []
    if not settings["neo4j_uri"] or not settings["neo4j_user"] or not settings["neo4j_password"]:
        return []

    query = """
    MATCH (:MemoryGroup {id: $group_id})-[:HAS_ENTITY]->(e:Entity)
    WHERE toLower(e.name) CONTAINS toLower($query_text)
    OPTIONAL MATCH (m:Memory)-[:MENTIONS]->(e)
    RETURN
      e.name AS entity,
      e.entity_type AS entity_type,
      e.last_seen_at AS last_seen_at,
      collect(m.text)[0..$limit] AS memory_texts
    ORDER BY e.last_seen_at DESC
    LIMIT $limit
    """
    driver = GraphDatabase.driver(settings["neo4j_uri"], auth=(settings["neo4j_user"], settings["neo4j_password"]))
    try:
        with driver.session() as session:
            records = session.run(
                query,
                group_id=settings["group_id"],
                query_text=query_text,
                limit=limit,
            )
            return [
                {
                    "entity": record.get("entity", ""),
                    "entity_type": record.get("entity_type", ""),
                    "last_seen_at": record.get("last_seen_at", ""),
                    "memory_texts": [text for text in record.get("memory_texts", []) if text],
                    "storage": "graph_entity",
                }
                for record in records
            ]
    except Exception:
        return []
    finally:
        driver.close()


def get_graph_overview(limit: int = 8) -> dict[str, Any]:
    settings = load_settings()
    if GraphDatabase is None:
        return {}
    if not settings["neo4j_uri"] or not settings["neo4j_user"] or not settings["neo4j_password"]:
        return {}

    query = """
    MATCH (g:MemoryGroup {id: $group_id})
    CALL (g) {
      OPTIONAL MATCH (g)-[:HAS_PROJECT]->(p:Project)
      RETURN count(DISTINCT p) AS project_count, collect(DISTINCT p.name)[0..$limit] AS projects
    }
    CALL (g) {
      OPTIONAL MATCH (g)-[:HAS_DAY]->(d:Day)
      RETURN count(DISTINCT d) AS day_count, collect(DISTINCT d.date)[0..$limit] AS days
    }
    CALL (g) {
      OPTIONAL MATCH (g)-[:HAS_MEMORY]->(m:Memory)
      RETURN count(DISTINCT m) AS memory_count
    }
    CALL (g) {
      OPTIONAL MATCH (g)-[:HAS_ENTITY]->(e:Entity)
      RETURN count(DISTINCT e) AS entity_count
    }
    CALL (g) {
      OPTIONAL MATCH (:Entity)-[r:RELATES_TO {group_id: g.id}]->(:Entity)
      RETURN count(DISTINCT r) AS relation_count
    }
    CALL (g) {
      OPTIONAL MATCH (g)-[:HAS_PROJECT]->(p:Project)-[:HAS_DAY]->(pd:ProjectDay)
      RETURN collect(DISTINCT {
        project: pd.project,
        date: pd.date,
        updated_at: pd.updated_at,
        memory_count: size([(pd)-[:HAS_MEMORY]->(:Memory) | 1])
      })[0..$limit] AS project_days
    }
    CALL (g) {
      OPTIONAL MATCH (g)-[:HAS_ENTITY]->(e:Entity)
      RETURN collect(DISTINCT {
        entity: e.name,
        entity_type: e.entity_type,
        last_seen_at: e.last_seen_at
      })[0..$limit] AS top_entities
    }
    RETURN {
      group_id: g.id,
      counts: {
        projects: project_count,
        days: day_count,
        memories: memory_count,
        entities: entity_count,
        relations: relation_count
      },
      projects: projects,
      days: days,
      project_days: project_days,
      top_entities: top_entities
    } AS overview
    """
    driver = GraphDatabase.driver(settings["neo4j_uri"], auth=(settings["neo4j_user"], settings["neo4j_password"]))
    try:
        with driver.session() as session:
            record = session.run(query, group_id=settings["group_id"], limit=limit).single()
            return dict(record["overview"]) if record else {}
    except Exception:
        return {}
    finally:
        driver.close()


def get_graph_project_day(project: str, date: str, limit: int = 12) -> dict[str, Any]:
    settings = load_settings()
    if GraphDatabase is None:
        return {}
    if not settings["neo4j_uri"] or not settings["neo4j_user"] or not settings["neo4j_password"]:
        return {}

    query = """
    MATCH (:MemoryGroup {id: $group_id})-[:HAS_PROJECT]->(p:Project {key: $project_key})
    MATCH (p)-[:HAS_DAY]->(pd:ProjectDay {date: $date})
    CALL (pd) {
      OPTIONAL MATCH (pd)-[:HAS_MEMORY]->(m:Memory)
      RETURN collect(DISTINCT {
        id: m.id,
        text: m.text,
        kind: m.kind,
        source: m.source,
        project: m.project,
        cwd: m.cwd,
        importance: m.importance,
        tags: coalesce(m.tags, []),
        timestamp: m.created_at,
        extraction_summary: coalesce(m.extraction_summary, '')
      })[0..$limit] AS memories
    }
    CALL (pd) {
      OPTIONAL MATCH (pd)-[:MENTIONED_ENTITY]->(e:Entity)
      OPTIONAL MATCH (e)-[:RELATES_TO]->(other:Entity)
      WITH e, collect(DISTINCT other.name)[0..5] AS related_entities
      RETURN collect(DISTINCT {
        entity: e.name,
        entity_type: e.entity_type,
        last_seen_at: e.last_seen_at,
        related_entities: [name IN related_entities WHERE name IS NOT NULL]
      })[0..$limit] AS entities
    }
    RETURN {
      project: p.name,
      date: pd.date,
      memories: [item IN memories WHERE item.id IS NOT NULL],
      entities: [item IN entities WHERE item.entity IS NOT NULL]
    } AS neighborhood
    """
    driver = GraphDatabase.driver(settings["neo4j_uri"], auth=(settings["neo4j_user"], settings["neo4j_password"]))
    try:
        with driver.session() as session:
            record = session.run(
                query,
                group_id=settings["group_id"],
                project_key=_project_key(project),
                date=date,
                limit=limit,
            ).single()
            return dict(record["neighborhood"]) if record else {}
    except Exception:
        return {}
    finally:
        driver.close()


def get_today_graph(*, project: str = "", date: str = "", limit: int = 12) -> dict[str, Any]:
    target_date = date or datetime.now(timezone.utc).date().isoformat()
    if project:
        return {
            "date": target_date,
            "projects": [get_graph_project_day(project, target_date, limit=limit)],
        }

    overview = get_graph_overview(limit=max(limit, 8))
    projects = []
    for item in overview.get("project_days", []):
        if item.get("date") != target_date or not item.get("project"):
            continue
        neighborhood = get_graph_project_day(str(item["project"]), target_date, limit=limit)
        if neighborhood:
            projects.append(neighborhood)
    return {"date": target_date, "projects": projects}


def get_brain_health(limit: int = 8) -> dict[str, Any]:
    settings = load_settings()
    raw_events = read_jsonl_events(settings["memory_log_path"])
    graph_eligible = [event for event in raw_events if should_store_in_graph(event)]
    project_counter = Counter(event.get("project") or "unscoped" for event in raw_events)
    day_counter = Counter(_event_date(str(event.get("timestamp", ""))) or "unknown-date" for event in raw_events)

    overview = get_graph_overview(limit=limit)
    graph_counts = overview.get("counts", {}) if isinstance(overview, dict) else {}
    graph_project_days = overview.get("project_days", []) if isinstance(overview, dict) else []
    graph_pd_keys = {
        (str(item.get("project", "")), str(item.get("date", "")))
        for item in graph_project_days
        if item.get("project") and item.get("date")
    }

    expected_pd_keys = {
        (str(event.get("project") or "unscoped"), _event_date(str(event.get("timestamp", ""))) or "unknown-date")
        for event in graph_eligible
    }
    missing_project_days = [
        {"project": project, "date": date}
        for project, date in sorted(expected_pd_keys - graph_pd_keys)[:limit]
    ]

    return {
        "raw_event_count": len(raw_events),
        "graph_eligible_count": len(graph_eligible),
        "graph_counts": graph_counts,
        "coverage": {
            "graph_memory_ratio": round(min(1.0, (graph_counts.get("memories", 0) / len(graph_eligible))), 3) if graph_eligible else 1.0,
            "project_day_ratio": round((len(graph_pd_keys) / len(expected_pd_keys)), 3) if expected_pd_keys else 1.0,
        },
        "top_projects": [{"project": name, "count": count} for name, count in project_counter.most_common(limit)],
        "top_days": [{"date": name, "count": count} for name, count in day_counter.most_common(limit)],
        "missing_project_days": missing_project_days,
        "helper_enabled": bool(settings.get("helper_enabled")),
        "helper_model": settings.get("helper_model", ""),
        "neo4j_enabled": bool(
            GraphDatabase is not None
            and settings.get("neo4j_uri")
            and settings.get("neo4j_user")
            and settings.get("neo4j_password")
        ),
    }


def _graph_project_day_keys(settings: dict[str, Any]) -> set[tuple[str, str]]:
    if GraphDatabase is None:
        return set()
    if not settings["neo4j_uri"] or not settings["neo4j_user"] or not settings["neo4j_password"]:
        return set()
    query = """
    MATCH (:MemoryGroup {id: $group_id})-[:HAS_PROJECT]->(p:Project)-[:HAS_DAY]->(pd:ProjectDay)
    RETURN DISTINCT p.name AS project, pd.date AS date
    """
    driver = GraphDatabase.driver(settings["neo4j_uri"], auth=(settings["neo4j_user"], settings["neo4j_password"]))
    try:
        with driver.session() as session:
            records = session.run(query, group_id=settings["group_id"])
            return {
                (str(record.get("project", "")), str(record.get("date", "")))
                for record in records
                if record.get("project") and record.get("date")
            }
    except Exception:
        return set()
    finally:
        driver.close()


def repair_graph(*, limit: int = 0, project: str = "", date: str = "", missing_only: bool = True) -> dict[str, Any]:
    settings = load_settings()
    if not settings["neo4j_uri"] or not settings["neo4j_user"] or not settings["neo4j_password"]:
        return {"ok": False, "error": "Neo4j is not configured."}

    events = read_jsonl_events(settings["memory_log_path"])
    if limit > 0:
        events = events[-limit:]

    graph_keys = _graph_project_day_keys(settings) if missing_only else set()
    written = 0
    skipped = 0
    repaired_keys: set[tuple[str, str]] = set()

    for event in events:
        if not should_store_in_graph(event):
            continue
        event_project = str(event.get("project") or "unscoped")
        event_date = _event_date(str(event.get("timestamp", ""))) or "unknown-date"
        if project and event_project != project:
            continue
        if date and event_date != date:
            continue
        key = (event_project, event_date)
        if missing_only and key in graph_keys:
            skipped += 1
            continue
        knowledge = _extract_for_event(event, settings)
        store_graph_memory(
            event,
            neo4j_uri=settings["neo4j_uri"],
            neo4j_user=settings["neo4j_user"],
            neo4j_password=settings["neo4j_password"],
            group_id=settings["group_id"],
            knowledge=knowledge,
        )
        written += 1
        repaired_keys.add(key)
        graph_keys.add(key)

    return {
        "ok": True,
        "written": written,
        "skipped": skipped,
        "missing_only": missing_only,
        "repaired_project_days": [
            {"project": project_name, "date": date_value}
            for project_name, date_value in sorted(repaired_keys)
        ],
    }


def get_today_summary(*, project: str = "", date: str = "") -> dict[str, Any]:
    target_date = date or datetime.now(timezone.utc).date().isoformat()
    events = get_events_by_date(target_date, project=project)
    settings = load_settings()
    summary = summarize_events_with_helper(date=target_date, events=events, settings=settings)
    return {
        "date": target_date,
        "project": project,
        "summary": summary.get("summary", ""),
        "used_helper": summary.get("used_helper", False),
        "event_count": len(events),
    }


def get_vault_status() -> dict[str, Any]:
    settings = load_settings()
    return get_bridge_health(settings=settings)


def get_review_queue(*, status: str = "", limit: int = 50) -> dict[str, Any]:
    settings = load_settings()
    return list_review_queue(settings=settings, status=status, limit=limit)


def approve_review_queue_item(*, queue_key: str, target: str, title: str = "") -> dict[str, Any]:
    settings = load_settings()
    return promote_review_item(settings=settings, queue_key=queue_key, target=target, title=title)


def reject_review_queue_item(*, queue_key: str, reason: str = "") -> dict[str, Any]:
    settings = load_settings()
    return reject_review_item(settings=settings, queue_key=queue_key, reason=reason)


def get_postgres_status() -> dict[str, Any]:
    settings = load_settings()
    dsn = str(settings.get("postgres_dsn", "")).strip()
    if not dsn:
        return {"ok": True, "enabled": False, "reachable": False, "reason": "disabled"}
    try:
        import psycopg
    except ImportError:
        return {"ok": False, "enabled": True, "reachable": False, "reason": "driver_unavailable"}
    try:
        with psycopg.connect(dsn, connect_timeout=2, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
        return {"ok": True, "enabled": True, "reachable": bool(row and row[0] == 1), "reason": ""}
    except Exception:
        return {"ok": False, "enabled": True, "reachable": False, "reason": "connect_or_query_failed"}


def get_postgres_recent_events(
    *,
    limit: int = 20,
    project: str = "",
    source: str = "",
    kind: str = "",
    since: str = "",
) -> dict[str, Any]:
    settings = load_settings()
    return list_postgres_recent_events(
        dsn=str(settings.get("postgres_dsn", "")),
        limit=limit,
        project=project,
        source=source,
        kind=kind,
        since=since,
    )


def get_postgres_review_queue(*, status: str = "", limit: int = 50) -> dict[str, Any]:
    settings = load_settings()
    return list_postgres_review_queue(
        dsn=str(settings.get("postgres_dsn", "")),
        status=status,
        limit=limit,
    )


def get_postgres_bridge_writes(*, event_id: str = "", limit: int = 50) -> dict[str, Any]:
    settings = load_settings()
    return list_postgres_bridge_writes(
        dsn=str(settings.get("postgres_dsn", "")),
        event_id=event_id,
        limit=limit,
    )


def persist_event(event: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    normalized = normalize_event(event)
    force_store = bool((normalized.get("metadata") or {}).get("force_store"))
    if force_store:
        dedupe_window = int(settings.get("dedupe_window_minutes", 30))
        dedupe_threshold = float(settings.get("dedupe_similarity_threshold", 0.86))
    else:
        dedupe_window = _effective_dedupe_window_minutes(
            int(settings.get("dedupe_window_minutes", 30)),
            str(normalized.get("kind", "")),
        )
        dedupe_threshold = float(settings.get("dedupe_similarity_threshold", 0.86))
    existing_events = read_jsonl_events(settings["memory_log_path"])
    duplicate_of: dict[str, Any] | None = None
    duplicate_similarity = 0.0
    if not force_store:
        duplicate_of, duplicate_similarity = _find_recent_duplicate_event(
            existing_events,
            normalized,
            window_minutes=dedupe_window,
            similarity_threshold=dedupe_threshold,
        )
    if duplicate_of is not None:
        return {
            "ok": True,
            "deduplicated": True,
            "duplicate_event_id": str(duplicate_of.get("id", "")),
            "stored_in_graph": False,
            "extracted_entities": 0,
            "extracted_relations": 0,
            "vault_auto_writes": 0,
            "vault_review_items": 0,
            "stored_in_postgres": False,
            "dedupe_explain": {
                "similarity": round(duplicate_similarity, 4),
                "threshold": dedupe_threshold,
                "window_minutes": dedupe_window,
                "window_policy": "kind_weighted",
                "force_store": force_store,
                "matched_kind": normalized.get("kind", ""),
                "matched_project": normalized.get("project", ""),
                "matched_source": normalized.get("source", ""),
            },
            "event": duplicate_of,
        }
    append_jsonl(settings["memory_log_path"], normalized)

    stored_in_graph = False
    extracted = {"entities": [], "relations": [], "summary": ""}
    structured_result = {"attempted": False, "stored": False}
    vault_result = {"auto_writes": [], "review_items": []}
    auto_open_loop_result: dict[str, Any] | None = None
    neo_ready = bool(settings["neo4j_uri"] and settings["neo4j_user"] and settings["neo4j_password"])
    wants_graph = should_store_in_graph(normalized)
    wants_extract = normalized.get("kind") in EXTRACT_KINDS or normalized.get("importance") == "high"

    if wants_extract:
        extracted = _extract_for_event(normalized, settings)

    vault_result = sync_event_to_vault(normalized, settings=settings, knowledge=extracted)
    structured_result = persist_structured_event(
        normalized,
        settings=settings,
        knowledge=extracted,
        bridge_result=vault_result,
    )

    if wants_graph and neo_ready:
        try:
            store_graph_memory(
                normalized,
                neo4j_uri=settings["neo4j_uri"],
                neo4j_user=settings["neo4j_user"],
                neo4j_password=settings["neo4j_password"],
                group_id=settings["group_id"],
                knowledge=extracted,
            )
            stored_in_graph = True
        except RuntimeError as exc:
            # JSONL already committed; graph auth or connectivity can be fixed without losing the event.
            LOGGER.warning("Graph persistence skipped after JSONL write for event_id=%s: %s", normalized["id"], exc)

    auto_loop_payload = _auto_open_loop_payload(normalized)
    if auto_loop_payload is not None:
        auto_open_loop_result = persist_event(auto_loop_payload)

    return {
        "ok": True,
        "deduplicated": False,
        "duplicate_event_id": "",
        "stored_in_graph": stored_in_graph,
        "extracted_entities": len(extracted.get("entities", [])),
        "extracted_relations": len(extracted.get("relations", [])),
        "vault_auto_writes": len(vault_result.get("auto_writes", [])),
        "vault_review_items": len(vault_result.get("review_items", [])),
        "stored_in_postgres": structured_result.get("stored", False),
        "auto_open_loop_created": bool(auto_open_loop_result and not auto_open_loop_result.get("deduplicated", False)),
        "auto_open_loop_event_id": (
            str((auto_open_loop_result or {}).get("event", {}).get("id", ""))
            if auto_open_loop_result
            else ""
        ),
        "dedupe_explain": {
            "similarity": round(duplicate_similarity, 4),
            "threshold": dedupe_threshold,
            "window_minutes": dedupe_window,
            "window_policy": "kind_weighted",
            "force_store": force_store,
            "matched_kind": normalized.get("kind", ""),
            "matched_project": normalized.get("project", ""),
            "matched_source": normalized.get("source", ""),
        },
        "event": normalized,
        "auto_open_loop": auto_open_loop_result or {},
    }
