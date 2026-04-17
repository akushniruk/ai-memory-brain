"""Parse Cursor agent transcript JSONL into structured fields."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


def parse_transcript(path: str) -> dict[str, Any]:
    """Read transcript JSONL and extract goal, conclusion, tools, turn_count.

    Returns empty fields if file missing or unreadable.
    """
    p = Path(path)
    if not p.exists():
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0, "assistant_texts": [], "open_items": []}

    turns: list[dict[str, Any]] = []
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
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0, "assistant_texts": [], "open_items": []}

    if not turns:
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0, "assistant_texts": [], "open_items": []}

    # First user message → goal
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

    # Last 2 assistant text turns → conclusion
    assistant_texts: list[str] = []
    seen_tools: list[str] = []
    seen_tool_set: set[str] = set()

    for turn in turns:
        if turn.get("role") != "assistant":
            continue
        content = turn.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
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

    open_items: list[str] = []
    open_markers = ("todo", "next", "follow-up", "follow up", "remaining", "risk", "blocked")
    for text in assistant_texts[-4:]:
        lowered = text.lower()
        if any(marker in lowered for marker in open_markers):
            cleaned = re.sub(r"\s+", " ", text).strip()
            if cleaned and cleaned not in open_items:
                open_items.append(cleaned[:240])

    return {
        "goal": goal,
        "conclusion": conclusion,
        "tools": seen_tools,
        "turn_count": len(turns),
        "assistant_texts": assistant_texts[-6:],
        "open_items": open_items,
    }


def build_rule_based_summary(parsed: dict[str, Any]) -> str:
    """Build a task_summary-shaped text from parsed transcript fields."""
    goal = str(parsed.get("goal", "")).strip()
    conclusion = str(parsed.get("conclusion", "")).strip()
    tools = parsed.get("tools", [])
    turn_count = int(parsed.get("turn_count", 0) or 0)
    open_items = parsed.get("open_items", [])
    changes = conclusion or "Completed agent session work."
    decisions = f"Used tools: {', '.join(tools)}" if tools else "Used local transcript fallback summarization."
    validation = f"Cursor session completed with {turn_count} turns." if turn_count else "Cursor session completed."
    risks = "; ".join(open_items[:3]) if open_items else "Review transcript if unresolved follow-ups remain."
    parts = []
    if goal:
        parts.append(f"Goal: {goal}")
    parts.append(f"Changes: {changes}")
    parts.append(f"Decisions: {decisions}")
    parts.append(f"Validation: {validation}")
    parts.append(f"Risks/TODO: {risks}")
    return "\n".join(parts)


def build_structured_session_memory(parsed: dict[str, Any], *, summary_text: str = "") -> dict[str, Any]:
    goal = str(parsed.get("goal", "")).strip()
    conclusion = str(parsed.get("conclusion", "")).strip()
    tools = parsed.get("tools", [])
    turn_count = int(parsed.get("turn_count", 0) or 0)
    open_items = parsed.get("open_items", [])
    changes = str(summary_text or conclusion or "Completed agent session work.").strip()
    decisions = f"Used tools: {', '.join(tools)}" if tools else "No explicit tool calls captured."
    validation = f"Cursor session completed with {turn_count} turns." if turn_count else "Cursor session completed."
    risk = "; ".join(open_items[:3]) if open_items else "Review transcript if unresolved follow-ups remain."
    return {
        "goal": goal,
        "changes": changes,
        "decision": decisions,
        "validation": validation,
        "next_step": open_items[0] if open_items else "",
        "risk": risk,
        "summary": build_rule_based_summary(
            {
                "goal": goal,
                "conclusion": changes,
                "tools": tools,
                "turn_count": turn_count,
                "open_items": open_items,
            }
        ),
    }
