from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_store import get_events_by_date, load_settings


def build_day_capsule(*, date: str, project: str = "", limit: int = 1000) -> dict[str, Any]:
    events = get_events_by_date(date, project=project)[:limit]
    kind_counts = Counter(str(event.get("kind", "note")) for event in events)
    source_counts = Counter(str(event.get("source", "unknown")) for event in events)
    high_signal = [event for event in events if event.get("importance") == "high"][:30]
    return {
        "date": date,
        "project": project,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "kind_counts": dict(kind_counts),
        "source_counts": dict(source_counts),
        "high_signal": [
            {
                "id": event.get("id", ""),
                "timestamp": event.get("timestamp", ""),
                "kind": event.get("kind", ""),
                "source": event.get("source", ""),
                "project": event.get("project", ""),
                "text": str(event.get("text", ""))[:280],
            }
            for event in high_signal
        ],
    }


def write_day_capsule(*, date: str, project: str = "") -> Path:
    settings = load_settings()
    app_home = Path(str(settings.get("app_home", ""))).expanduser()
    capsule_dir = app_home / "memory" / "capsules"
    capsule_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{project}" if project else ""
    out_path = capsule_dir / f"{date}{suffix}.json"
    capsule = build_day_capsule(date=date, project=project)
    out_path.write_text(json.dumps(capsule, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a compact daily memory capsule.")
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--project", default="")
    args = parser.parse_args()
    path = write_day_capsule(date=args.date, project=args.project)
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
