#!/usr/bin/env python3
import argparse
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from memory_store import persist_event


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
    load_dotenv(Path(__file__).resolve().parent / ".env")

    parser = argparse.ArgumentParser(description="Write a milestone memory.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--project", default="")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--tags", default="")
    args = parser.parse_args()

    project = args.project or _project_from_git(args.cwd)
    event = {
        "source": "manual",
        "kind": "milestone",
        "text": args.text,
        "project": project,
        "cwd": args.cwd,
        "importance": "high",
        "tags": [tag for tag in args.tags.split(",") if tag] + ["milestone"],
        "graph": True,
        "metadata": {},
    }
    persist_event(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
