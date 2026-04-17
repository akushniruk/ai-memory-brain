#!/usr/bin/env python3
"""
Global Cursor stop hook for ai-memory-brain.
Calls /summarize for librarian-generated summary, falls back to rule-based.
Always writes a task_summary event with importance=high.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _load_key_values(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _post_json(url: str, payload: dict, timeout: int = 3) -> dict:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_transcript_local(transcript_path: str) -> dict:
    """Minimal rule-based parser used when /summarize is unreachable."""
    p = Path(transcript_path)
    if not p.exists():
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0, "open_items": []}
    turns = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0, "open_items": []}

    goal = ""
    for turn in turns:
        if turn.get("role") == "user":
            content = turn.get("message", {}).get("content", [])
            if not isinstance(content, list):
                break
            for item in content:
                if item.get("type") == "text":
                    goal = item.get("text", "")[:300]
                    break
        if goal:
            break

    assistant_texts: list[str] = []
    tool_set: set[str] = set()
    tools: list[str] = []
    for turn in turns:
        if turn.get("role") != "assistant":
            continue
        content = turn.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if item.get("type") == "text":
                t = item.get("text", "").strip()
                if t:
                    assistant_texts.append(t[:300])
            elif item.get("type") == "tool_use":
                name = item.get("name", "")
                if name and name not in tool_set:
                    tool_set.add(name)
                    tools.append(name)

    conclusion = " ".join(assistant_texts[-2:]) if assistant_texts else ""
    open_items = []
    for text in assistant_texts[-4:]:
        lowered = text.lower()
        if any(marker in lowered for marker in ("todo", "next", "follow-up", "follow up", "remaining", "risk", "blocked")):
            open_items.append(text[:240])
    return {"goal": goal, "conclusion": conclusion, "tools": tools, "turn_count": len(turns), "open_items": open_items}


def _build_rule_based_summary(parsed: dict) -> str:
    goal = str(parsed.get("goal", "")).strip()
    conclusion = str(parsed.get("conclusion", "")).strip() or "Completed agent session work."
    tools = parsed.get("tools", [])
    turn_count = int(parsed.get("turn_count", 0) or 0)
    open_items = parsed.get("open_items", [])
    parts = []
    if goal:
        parts.append(f"Goal: {goal}")
    parts.append(f"Changes: {conclusion}")
    parts.append(f"Decisions: Used tools: {', '.join(tools)}" if tools else "Decisions: Used local transcript fallback summarization.")
    parts.append(f"Validation: Cursor session completed with {turn_count} turns." if turn_count else "Validation: Cursor session completed.")
    parts.append(f"Risks/TODO: {'; '.join(open_items[:3])}" if open_items else "Risks/TODO: Review transcript if unresolved follow-ups remain.")
    return "\n".join(parts)


def _git_value(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _git_changed_files(cwd: Path) -> list[str]:
    raw = _git_value(cwd, "diff", "--name-only", "HEAD")
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("{}")
        return 0

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("{}")
        return 0

    if data.get("status") != "completed" or int(data.get("loop_count") or 0) != 0:
        print("{}")
        return 0

    cwd = Path.cwd().resolve()
    config_env = _load_key_values(Path.home() / ".config" / "ai-memory-brain" / "config.env")
    host = os.environ.get("MEMORY_SERVER_HOST") or config_env.get("MEMORY_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("MEMORY_SERVER_PORT") or config_env.get("MEMORY_SERVER_PORT", "8765")
    base_url = f"http://{host}:{port}"

    conversation_id = str(data.get("conversation_id", ""))
    transcript_path = str(data.get("transcript_path") or "")
    generation_id = data.get("generation_id")

    summary = ""
    used_llm = False
    turn_count = 0
    structured: dict[str, object] = {}

    if transcript_path:
        try:
            result = _post_json(
                f"{base_url}/summarize",
                {"transcript_path": transcript_path, "project": cwd.name, "cwd": str(cwd)},
                timeout=3,
            )
            summary = result.get("summary", "")
            used_llm = bool(result.get("used_llm", False))
            turn_count = int(result.get("turn_count", 0))
            structured = dict(result.get("structured") or {})
        except (urllib.error.URLError, TimeoutError, OSError):
            parsed = _parse_transcript_local(transcript_path)
            summary = _build_rule_based_summary(parsed)
            turn_count = parsed.get("turn_count", 0)
            structured = {
                "goal": parsed.get("goal", ""),
                "changes": parsed.get("conclusion", ""),
                "decision": f"Used tools: {', '.join(parsed.get('tools', []))}" if parsed.get("tools") else "",
                "validation": f"Cursor session completed with {turn_count} turns." if turn_count else "Cursor session completed.",
                "next_step": (parsed.get("open_items", []) or [""])[0],
                "risk": "; ".join(parsed.get("open_items", [])[:3]) if parsed.get("open_items") else "",
                "summary": summary,
            }

    if not summary:
        summary = f"Cursor agent session completed ({cwd.name})."
    if not structured:
        structured = {
            "goal": "",
            "changes": summary,
            "decision": "",
            "validation": "Cursor session completed.",
            "next_step": "",
            "risk": "",
            "summary": summary,
        }

    branch = _git_value(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    commit_sha = _git_value(cwd, "rev-parse", "HEAD")
    changed_files = _git_changed_files(cwd)

    payload = {
        "source": "cursor-stop-hook",
        "kind": "task_summary",
        "text": summary,
        "project": cwd.name,
        "cwd": str(cwd),
        "importance": "high",
        "tags": ["cursor", "session-stop", "summarized"],
        "graph": True,
        "metadata": {
            "conversation_id": conversation_id,
            "generation_id": generation_id,
            "transcript_path": transcript_path,
            "used_llm": used_llm,
            "turn_count": turn_count,
            "branch": branch,
            "repo_context": {
                "branch": branch,
                "commit_sha": commit_sha,
                "files_touched": changed_files,
            },
            "structured": {
                key: value
                for key, value in structured.items()
                if value not in ("", [], {}, None)
            },
        },
    }

    try:
        _post_json(f"{base_url}/event", payload)
    except (urllib.error.URLError, TimeoutError, OSError):
        pass

    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
