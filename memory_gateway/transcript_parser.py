"""Parse Cursor agent transcript JSONL into structured fields."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def parse_transcript(path: str) -> dict[str, Any]:
    """Read transcript JSONL and extract goal, conclusion, tools, turn_count.

    Returns empty fields if file missing or unreadable.
    """
    p = Path(path)
    if not p.exists():
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}

    turns: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}

    if not turns:
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}

    # First user message → goal
    goal = ""
    for turn in turns:
        if turn.get("role") == "user":
            for item in turn.get("message", {}).get("content", []):
                if item.get("type") == "text":
                    goal = item.get("text", "")[:300]
                    break
        if goal:
            break

    # Last 2 assistant text turns → conclusion
    assistant_texts: list[str] = []
    seen_tools: list[str] = []
    seen_tool_set: set[str] = set()

    for turn in turns:
        if turn.get("role") != "assistant":
            continue
        for item in turn.get("message", {}).get("content", []):
            if item.get("type") == "text":
                text = item.get("text", "").strip()
                if text:
                    assistant_texts.append(text[:300])
            elif item.get("type") == "tool_use":
                name = item.get("name", "")
                if name and name not in seen_tool_set:
                    seen_tool_set.add(name)
                    seen_tools.append(name)

    conclusion = " ".join(assistant_texts[-2:]) if assistant_texts else ""

    return {
        "goal": goal,
        "conclusion": conclusion,
        "tools": seen_tools,
        "turn_count": len(turns),
    }


def build_rule_based_summary(parsed: dict[str, Any]) -> str:
    """Build a plain-text summary from parsed transcript fields."""
    parts: list[str] = []
    if parsed.get("goal"):
        parts.append(f"Goal: {parsed['goal'][:200]}")
    if parsed.get("conclusion"):
        parts.append(f"Concluded: {parsed['conclusion'][:200]}")
    if parsed.get("tools"):
        parts.append(f"Tools: {', '.join(parsed['tools'])}")
    turn_count = parsed.get("turn_count", 0)
    if turn_count:
        parts.append(f"Turns: {turn_count}")
    return "\n".join(parts) if parts else "Session completed."
