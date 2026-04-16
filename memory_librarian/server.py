"""
stdio MCP server — memory librarian on top of memory_gateway/memory_store.

Loads runtime settings from app-home config with repo .env as fallback.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from rpc import error, handle_message


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            error(None, -32700, f"Parse error: {exc}")
            continue

        if isinstance(message, list):
            for item in message:
                handle_message(item)
        else:
            handle_message(message)


if __name__ == "__main__":
    main()
