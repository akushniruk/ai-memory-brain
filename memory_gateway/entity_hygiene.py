from __future__ import annotations

import json
from collections import Counter
from typing import Any

from memory_store import get_brain_health, get_graph_overview, repair_graph


def _normalized(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def run_entity_hygiene(limit: int = 200) -> dict[str, Any]:
    overview = get_graph_overview(limit=limit)
    top_entities = overview.get("top_entities", []) if isinstance(overview, dict) else []
    dup_counter: Counter[str] = Counter()
    name_map: dict[str, list[str]] = {}
    for item in top_entities:
        name = str(item.get("entity", "")).strip()
        if not name:
            continue
        key = _normalized(name)
        dup_counter[key] += 1
        name_map.setdefault(key, []).append(name)
    duplicate_clusters = [
        {"normalized": key, "variants": sorted(set(values)), "count": dup_counter[key]}
        for key, values in name_map.items()
        if dup_counter[key] > 1
    ]
    health = get_brain_health(limit=8)
    repaired = {}
    if health.get("missing_project_days"):
        repaired = repair_graph(limit=0, missing_only=True)
    return {
        "ok": True,
        "duplicate_clusters": duplicate_clusters,
        "duplicate_cluster_count": len(duplicate_clusters),
        "missing_project_days": health.get("missing_project_days", []),
        "repair_result": repaired,
    }


def main() -> int:
    payload = run_entity_hygiene()
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
