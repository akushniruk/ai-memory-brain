#!/usr/bin/env python3
import argparse
import os
import subprocess
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
    except Exception:
        return Path(cwd).name


def main() -> int:
    load_runtime_env(Path(__file__).resolve().parent)
    parser = argparse.ArgumentParser(description="Write a daily check-out summary.")
    parser.add_argument(
        "--summary",
        default="Daily checkout:\n- Goal:\n- Changes made:\n- Decisions:\n- Validation:\n- Risks / TODO:",
    )
    parser.add_argument("--project", default="")
    parser.add_argument("--cwd", default=os.getcwd())
    args = parser.parse_args()

    project = args.project or _project_from_git(args.cwd)
    event = {
        "source": "manual",
        "kind": "daily_checkout",
        "text": args.summary,
        "project": project,
        "cwd": args.cwd,
        "importance": "high",
        "tags": ["daily", "checkout"],
        "graph": True,
        "metadata": {},
    }
    persist_event(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
