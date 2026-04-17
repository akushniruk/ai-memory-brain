import json
from collections import Counter
from difflib import SequenceMatcher
import logging
import re
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
    filtered = [event for event in events if _matches_filter(event, project=project, source=source, kind=kind)]
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
    raw_query_tokens = [token for token in re.findall(r"[a-z0-9_]+", query_lower) if token]
    signal_tokens = [token for token in raw_query_tokens if token not in LOW_SIGNAL_QUERY_TOKENS]
    query_tokens = signal_tokens or raw_query_tokens
    now_utc = datetime.now(timezone.utc)
    scored: list[tuple[float, dict[str, Any]]] = []

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
    }
