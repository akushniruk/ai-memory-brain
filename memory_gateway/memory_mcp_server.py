"""
Shim entry point: canonical MCP implementation lives in memory_librarian/server.py.
Keep this path stable for existing Cursor configs and docs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_server_module():
    path = Path(__file__).resolve().parent.parent / "memory_librarian" / "server.py"
    spec = importlib.util.spec_from_file_location("ai_memory_brain_librarian_server", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load memory librarian server from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_server = _load_server_module()
main = _server.main

if __name__ == "__main__":
    main()
