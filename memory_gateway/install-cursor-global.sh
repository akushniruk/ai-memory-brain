#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_SRC="$ROOT_DIR/memory_gateway/cursor-stop-hook.py"
HOOK_DST_DIR="$HOME/.config/ai-memory-brain"
HOOK_DST="$HOOK_DST_DIR/cursor-stop-hook.py"
MCP_PATH="$HOME/.cursor/mcp.json"
HOOKS_PATH="$HOME/.cursor/hooks.json"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv-memory/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

mkdir -p "$HOOK_DST_DIR" "$HOME/.cursor"
cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_DST"

"$PYTHON_BIN" - "$MCP_PATH" "$HOOKS_PATH" "$PYTHON_BIN" "$ROOT_DIR/memory_librarian/server.py" "$HOOK_DST" <<'PY'
import json
import sys
from pathlib import Path

mcp_path = Path(sys.argv[1]).expanduser()
hooks_path = Path(sys.argv[2]).expanduser()
python_bin = sys.argv[3]
server_path = sys.argv[4]
hook_path = sys.argv[5]

def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default

mcp = read_json(mcp_path, {})
mcp_servers = mcp.setdefault("mcpServers", {})
mcp_servers["ai-memory-brain"] = {
    "command": python_bin,
    "args": [server_path],
}
mcp_path.write_text(json.dumps(mcp, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

hooks = read_json(hooks_path, {})
hook_root = hooks.setdefault("hooks", {})
stop_hooks = hook_root.setdefault("stop", [])
entry = {"type": "command", "command": hook_path}
if not any(item.get("type") == "command" and item.get("command") == hook_path for item in stop_hooks if isinstance(item, dict)):
    stop_hooks.append(entry)
hooks_path.write_text(json.dumps(hooks, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
PY

echo "Installed global Cursor MCP + stop hook."
echo "MCP:   $MCP_PATH -> ai-memory-brain"
echo "Hooks: $HOOKS_PATH"
