#!/usr/bin/env bash
set -euo pipefail

MCP_PATH="$HOME/.cursor/mcp.json"
HOOKS_PATH="$HOME/.cursor/hooks.json"
HOOK_PATH="$HOME/.config/ai-memory-brain/cursor-stop-hook.py"
PYTHON_BIN="python3"

"$PYTHON_BIN" - "$MCP_PATH" "$HOOKS_PATH" "$HOOK_PATH" <<'PY'
import json
import sys
from pathlib import Path

mcp_path = Path(sys.argv[1]).expanduser()
hooks_path = Path(sys.argv[2]).expanduser()
hook_path = sys.argv[3]

if mcp_path.exists():
    try:
        mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        mcp = {}
    servers = mcp.get("mcpServers", {})
    if isinstance(servers, dict) and "ai-memory-brain" in servers:
        del servers["ai-memory-brain"]
        mcp_path.write_text(json.dumps(mcp, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

if hooks_path.exists():
    try:
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        hooks = {}
    stop = hooks.get("hooks", {}).get("stop", [])
    if isinstance(stop, list):
        new_stop = [item for item in stop if not (isinstance(item, dict) and item.get("command") == hook_path)]
        hooks.setdefault("hooks", {})["stop"] = new_stop
        hooks_path.write_text(json.dumps(hooks, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
PY

rm -f "$HOOK_PATH"
echo "Removed global Cursor ai-memory-brain MCP + hook."
