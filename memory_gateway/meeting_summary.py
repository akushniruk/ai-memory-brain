#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from memory_store import persist_event
from runtime_layout import load_runtime_env


def _project_from_git(cwd: str) -> str:
    try:
        root = subprocess.check_output(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return Path(root).name
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        print(f"[meeting_summary] fallback project name from cwd due to git lookup error: {exc}", file=sys.stderr)
        return Path(cwd).name


def main() -> int:
    load_runtime_env(Path(__file__).resolve().parent)

    parser = argparse.ArgumentParser(description="Write a meeting summary memory.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--project", default="")
    parser.add_argument("--source", default="manual")
    parser.add_argument("--importance", default="normal", choices=["low", "normal", "high"])
    parser.add_argument("--tags", default="")
    parser.add_argument("--cwd", default=os.getcwd())
    args = parser.parse_args()

    project = args.project or _project_from_git(args.cwd)
    tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
    event = {
        "source": args.source,
        "kind": "meeting_summary",
        "text": args.text,
        "project": project,
        "cwd": args.cwd,
        "importance": args.importance,
        "tags": tags,
        "graph": True,
        "metadata": {},
    }
    result = persist_event(event)
    print(
        json.dumps(
            {
                "ok": result.get("ok", False),
                "event_id": result.get("event", {}).get("id", ""),
                "stored_in_graph": result.get("stored_in_graph", False),
                "vault_auto_writes": result.get("vault_auto_writes", 0),
                "stored_in_postgres": result.get("stored_in_postgres", False),
            },
            ensure_ascii=True,
        )
    )
    if not result.get("ok", False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
