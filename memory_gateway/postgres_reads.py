from __future__ import annotations

import json
from datetime import datetime
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover - optional dependency
    psycopg = None


def _disabled_payload(reason: str) -> dict[str, Any]:
    return {
        "ok": reason == "disabled",
        "enabled": False if reason == "disabled" else True,
        "reachable": False,
        "degraded": reason != "disabled",
        "reason": reason,
    }


def _as_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _loads_json(value: Any, default: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if value in ("", None):
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def list_recent_events(
    *,
    dsn: str,
    limit: int = 20,
    project: str = "",
    source: str = "",
    kind: str = "",
    since: str = "",
) -> dict[str, Any]:
    if not dsn.strip():
        payload = _disabled_payload("disabled")
        payload["results"] = []
        return payload
    if psycopg is None:
        payload = _disabled_payload("driver_unavailable")
        payload["results"] = []
        return payload

    query = """
    SELECT id, timestamp, source, kind, project, cwd, importance, text, tags_json, metadata_json
    FROM memory_events
    WHERE (%s = '' OR project = %s)
      AND (%s = '' OR source = %s)
      AND (%s = '' OR kind = %s)
      AND (%s = '' OR timestamp >= %s::timestamptz)
    ORDER BY timestamp DESC
    LIMIT %s
    """
    params = (project, project, source, source, kind, kind, since, since, max(1, min(limit, 200)))

    try:
        with psycopg.connect(dsn, connect_timeout=2, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
    except Exception:
        payload = _disabled_payload("connect_or_query_failed")
        payload["results"] = []
        return payload

    results = [
        {
            "id": str(row[0]),
            "timestamp": _as_iso(row[1]),
            "source": str(row[2] or ""),
            "kind": str(row[3] or ""),
            "project": str(row[4] or ""),
            "cwd": str(row[5] or ""),
            "importance": str(row[6] or ""),
            "text": str(row[7] or ""),
            "tags": _loads_json(row[8], []),
            "metadata": _loads_json(row[9], {}),
            "storage": "postgres",
        }
        for row in rows
    ]
    return {
        "ok": True,
        "enabled": True,
        "reachable": True,
        "degraded": False,
        "reason": "",
        "count": len(results),
        "results": results,
    }


def list_review_queue(*, dsn: str, status: str = "", limit: int = 50) -> dict[str, Any]:
    if not dsn.strip():
        payload = _disabled_payload("disabled")
        payload["items"] = []
        return payload
    if psycopg is None:
        payload = _disabled_payload("driver_unavailable")
        payload["items"] = []
        return payload

    query = """
    SELECT queue_key, event_id, queue_type, target_path, payload_json, status, created_at
    FROM memory_review_queue
    WHERE (%s = '' OR status = %s)
    ORDER BY created_at DESC
    LIMIT %s
    """
    params = (status, status, max(1, min(limit, 500)))

    try:
        with psycopg.connect(dsn, connect_timeout=2, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
    except Exception:
        payload = _disabled_payload("connect_or_query_failed")
        payload["items"] = []
        return payload

    items = [
        {
            "queue_key": str(row[0] or ""),
            "event_id": str(row[1] or ""),
            "queue_type": str(row[2] or ""),
            "target_path": str(row[3] or ""),
            "payload": _loads_json(row[4], {}),
            "status": str(row[5] or ""),
            "created_at": _as_iso(row[6]),
            "storage": "postgres",
        }
        for row in rows
    ]
    return {
        "ok": True,
        "enabled": True,
        "reachable": True,
        "degraded": False,
        "reason": "",
        "status_filter": status or "all",
        "count": len(items),
        "items": items,
    }


def list_bridge_writes(*, dsn: str, event_id: str = "", limit: int = 50) -> dict[str, Any]:
    if not dsn.strip():
        payload = _disabled_payload("disabled")
        payload["items"] = []
        return payload
    if psycopg is None:
        payload = _disabled_payload("driver_unavailable")
        payload["items"] = []
        return payload

    query = """
    SELECT event_id, note_kind, note_path, write_mode, created_at
    FROM vault_bridge_writes
    WHERE (%s = '' OR event_id = %s)
    ORDER BY created_at DESC
    LIMIT %s
    """
    params = (event_id, event_id, max(1, min(limit, 500)))

    try:
        with psycopg.connect(dsn, connect_timeout=2, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
    except Exception:
        payload = _disabled_payload("connect_or_query_failed")
        payload["items"] = []
        return payload

    items = [
        {
            "event_id": str(row[0] or ""),
            "note_kind": str(row[1] or ""),
            "note_path": str(row[2] or ""),
            "write_mode": str(row[3] or ""),
            "created_at": _as_iso(row[4]),
            "storage": "postgres",
        }
        for row in rows
    ]
    return {
        "ok": True,
        "enabled": True,
        "reachable": True,
        "degraded": False,
        "reason": "",
        "count": len(items),
        "event_id_filter": event_id or "all",
        "items": items,
    }
