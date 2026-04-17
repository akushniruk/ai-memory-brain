from __future__ import annotations

import os
import sys
from pathlib import Path

_GATEWAY = Path(__file__).resolve().parent.parent / "memory_gateway"
if str(_GATEWAY) not in sys.path:
    sys.path.insert(0, str(_GATEWAY))

app_home = os.environ.get("AI_MEMORY_BRAIN_HOME", "").strip()
if app_home:
    app_home_path = Path(app_home).expanduser()
    os.environ.setdefault("VAULT_PATH", str(app_home_path / "vault"))
    os.environ.setdefault("AI_MEMORY_CONFIG_DIR", str(app_home_path / "config"))
    os.environ.setdefault("AI_MEMORY_MEMORY_DIR", str(app_home_path / "memory"))
    os.environ.setdefault("AI_MEMORY_LOGS_DIR", str(app_home_path / "memory" / "logs"))

from runtime_layout import load_runtime_env  # noqa: E402  pylint: disable=wrong-import-position

load_runtime_env(_GATEWAY)

from memory_store import (  # noqa: E402  pylint: disable=wrong-import-position
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
)
from brain_doctor import run_doctor  # noqa: E402  pylint: disable=wrong-import-position
from compact_day import build_day_capsule  # noqa: E402  pylint: disable=wrong-import-position
from entity_hygiene import run_entity_hygiene  # noqa: E402  pylint: disable=wrong-import-position
