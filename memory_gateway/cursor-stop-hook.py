#!/usr/bin/env python3
"""
Global Cursor stop hook for ai-memory-brain.
Calls /summarize for librarian-generated summary, falls back to rule-based.
Always writes a task_summary event with importance=high.
"""

from __future__ import annotations

import json
import os
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
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}
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
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}

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
    return {"goal": goal, "conclusion": conclusion, "tools": tools, "turn_count": len(turns)}


def _build_rule_based_summary(parsed: dict) -> str:
    parts = []
    if parsed.get("goal"):
        parts.append(f"Goal: {parsed['goal']}")
    if parsed.get("conclusion"):
        parts.append(f"Concluded: {parsed['conclusion']}")
    if parsed.get("tools"):
        parts.append(f"Tools: {', '.join(parsed['tools'])}")
    if parsed.get("turn_count"):
        parts.append(f"Turns: {parsed['turn_count']}")
    return "\n".join(parts) if parts else "Session completed."


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
        except (urllib.error.URLError, TimeoutError, OSError):
            parsed = _parse_transcript_local(transcript_path)
            summary = _build_rule_based_summary(parsed)
            turn_count = parsed.get("turn_count", 0)

    if not summary:
        summary = f"Cursor agent session completed ({cwd.name})."

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
