from __future__ import annotations

import json
import sys
from typing import Any

from handlers import call_tool, tool_result
from tool_schemas import PROTOCOL_VERSION, SERVER_INFO, TOOLS


def write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def respond(request_id: Any, result: dict[str, Any]) -> None:
    write_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def error(request_id: Any, code: int, message: str) -> None:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if request_id is not None:
        payload["id"] = request_id
    write_message(payload)


def handle_message(message: dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")

    if request_id is None and method and method.startswith("notifications/"):
        return

    params = message.get("params", {})

    if method == "initialize":
        requested_version = params.get("protocolVersion", PROTOCOL_VERSION)
        respond(
            request_id,
            {
                "protocolVersion": requested_version,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
        return

    if method == "notifications/initialized":
        return

    if request_id is None:
        return

    if method == "ping":
        respond(request_id, {})
        return

    if method == "tools/list":
        respond(request_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not name:
            respond(request_id, tool_result({"error": "Missing tool name"}, is_error=True))
            return
        try:
            respond(request_id, call_tool(name, arguments))
        except KeyError:
            error(request_id, -32601, f"Unknown tool: {name}")
        except Exception as exc:  # pragma: no cover - runtime guard
            respond(request_id, tool_result({"error": str(exc)}, is_error=True))
        return

    error(request_id, -32601, f"Method not found: {method}")
