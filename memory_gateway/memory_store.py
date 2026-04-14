import json
from collections import Counter
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


def get_graph_overview(limit: int = 8) -> dict[str, Any]:
    settings = load_settings()
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
        "neo4j_enabled": bool(settings.get("neo4j_uri") and settings.get("neo4j_user") and settings.get("neo4j_password")),
    }


def _graph_project_day_keys(settings: dict[str, Any]) -> set[tuple[str, str]]:
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


def persist_event(event: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    normalized = normalize_event(event)
    append_jsonl(settings["memory_log_path"], normalized)

    stored_in_graph = False
    extracted = {"entities": [], "relations": [], "summary": ""}
    neo_ready = bool(settings["neo4j_uri"] and settings["neo4j_user"] and settings["neo4j_password"])
    wants_graph = should_store_in_graph(normalized)

    if wants_graph and neo_ready:
        try:
            extracted = _extract_for_event(normalized, settings)
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
