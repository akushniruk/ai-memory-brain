import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable


GRAPH_KINDS = {
    "identity",
    "preference",
    "decision",
    "project_fact",
    "task_summary",
    "bug",
    "fix",
}

EXTRACT_KINDS = {
    "task_summary",
    "daily_checkin",
    "daily_checkout",
    "decision",
    "project_fact",
    "identity",
    "preference",
}

DEFAULT_LOG_PATH = str(Path(__file__).resolve().parents[1] / ".run" / "memory" / "events.jsonl")


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


def _extract_knowledge_with_helper(event: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    if not settings.get("helper_enabled"):
        return {"entities": [], "relations": [], "summary": ""}
    if not settings.get("helper_model"):
        return {"entities": [], "relations": [], "summary": ""}

    payload_text = (
        f"kind={event.get('kind', '')}\n"
        f"source={event.get('source', '')}\n"
        f"project={event.get('project', '')}\n"
        f"text={str(event.get('text', ''))[:1200]}"
    )
    prompt = (
        "Extract stable facts from this memory event. "
        "Return only strict JSON with keys: entities, relations, summary.\n\n"
        "entities: array of objects with name, type, role.\n"
        "relations: array of objects with source, target, type, fact.\n"
        "summary: short plain text.\n\n"
        "If no structured facts are present, return empty arrays.\n\n"
        f"EVENT:\n{payload_text}"
    )
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
    extracted = knowledge or {"entities": [], "relations": [], "summary": ""}
    query = """
    MERGE (g:MemoryGroup {id: $group_id})
    CREATE (m:Memory {
        id: $event_id,
        text: $text,
        kind: $kind,
        source: $source,
        project: $project,
        cwd: $cwd,
        importance: $importance,
        tags: $tags,
        metadata_json: $metadata_json,
        extraction_summary: $summary,
        created_at: $timestamp,
        updated_at: $timestamp
    })
    CREATE (g)-[:HAS_MEMORY]->(m)
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
                    project=event["project"],
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
                "scripts/memory_gateway/.env to match your local Neo4j instance."
            ) from exc
        except ServiceUnavailable as exc:
            raise RuntimeError(
                "Neo4j is unavailable at the configured URI. Start Neo4j Desktop and "
                "verify NEO4J_URI in scripts/memory_gateway/.env."
            ) from exc
    finally:
        driver.close()


def load_settings() -> dict[str, Any]:
    return {
        "memory_log_path": os.environ.get("MEMORY_LOG_PATH", DEFAULT_LOG_PATH),
        "neo4j_uri": os.environ.get("NEO4J_URI", ""),
        "neo4j_user": os.environ.get("NEO4J_USER", ""),
        "neo4j_password": os.environ.get("NEO4J_PASSWORD", ""),
        "group_id": os.environ.get("MEMORY_GROUP_ID", "personal-brain"),
        "helper_enabled": os.environ.get("MEMORY_HELPER_ENABLED", "0").lower() in ("1", "true", "yes", "on"),
        "helper_model": os.environ.get("MEMORY_HELPER_MODEL", "").strip(),
        "helper_base_url": os.environ.get("MEMORY_HELPER_BASE_URL", "http://127.0.0.1:11434/api/generate"),
        "helper_timeout_sec": int(os.environ.get("MEMORY_HELPER_TIMEOUT_SEC", "15")),
    }


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
    filtered = [event for event in events if _matches_filter(event, project=project, source=source, kind=kind)]
    return list(reversed(filtered[-limit:]))


def get_events_by_date(date_str: str, *, project: str = "", source: str = "", kind: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    filtered: list[dict[str, Any]] = []
    for event in events:
        if not _matches_filter(event, project=project, source=source, kind=kind):
            continue
        timestamp = str(event.get("timestamp", ""))
        if timestamp.startswith(date_str):
            filtered.append(event)
    return filtered


def search_events(query: str, limit: int = 10, *, project: str = "", source: str = "", kind: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    events = read_jsonl_events(settings["memory_log_path"])
    query_lower = query.lower().strip()
    scored: list[tuple[int, dict[str, Any]]] = []

    for event in events:
        if not _matches_filter(event, project=project, source=source, kind=kind):
            continue

        haystacks = [
            str(event.get("text", "")),
            str(event.get("kind", "")),
            str(event.get("source", "")),
            str(event.get("project", "")),
            " ".join(event.get("tags", [])),
        ]
        combined = " ".join(haystacks).lower()
        if query_lower not in combined:
            continue

        score = combined.count(query_lower)
        if event.get("importance") == "high":
            score += 2
        scored.append((score, event))

    scored.sort(key=lambda item: (item[0], item[1].get("timestamp", "")), reverse=True)
    return [event for _, event in scored[:limit]]


def get_project_context(project: str, limit: int = 12) -> dict[str, list[dict[str, Any]]]:
    recent = get_recent_events(limit=limit, project=project)
    important = [event for event in recent if event.get("importance") == "high"][:limit]
    return {
        "important": important,
        "recent": recent,
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


def persist_event(event: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    normalized = normalize_event(event)
    append_jsonl(settings["memory_log_path"], normalized)

    stored_in_graph = False
    extracted = {"entities": [], "relations": [], "summary": ""}
    neo_ready = bool(settings["neo4j_uri"] and settings["neo4j_user"] and settings["neo4j_password"])
    wants_graph = should_store_in_graph(normalized)
    wants_extract = normalized.get("kind") in EXTRACT_KINDS or normalized.get("importance") == "high"

    if wants_graph and neo_ready:
        try:
            if wants_extract:
                extracted = _extract_knowledge_with_helper(normalized, settings)
            store_graph_memory(
                normalized,
                neo4j_uri=settings["neo4j_uri"],
                neo4j_user=settings["neo4j_user"],
                neo4j_password=settings["neo4j_password"],
                group_id=settings["group_id"],
                knowledge=extracted,
            )
            stored_in_graph = True
        except RuntimeError:
            # JSONL already committed; graph auth or connectivity can be fixed without losing the event.
            pass

    return {
        "ok": True,
        "stored_in_graph": stored_in_graph,
        "extracted_entities": len(extracted.get("entities", [])),
        "extracted_relations": len(extracted.get("relations", [])),
        "event": normalized,
    }
