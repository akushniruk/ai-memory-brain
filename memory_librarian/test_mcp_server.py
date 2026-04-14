import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SERVER = Path(__file__).resolve().parent / "server.py"
SHIM = Path(__file__).resolve().parent.parent / "memory_gateway" / "memory_mcp_server.py"


def _rpc(proc: subprocess.Popen[str], req: dict) -> dict:
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(req, ensure_ascii=True) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line)


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._log = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self._log.close()
        self.log_path = self._log.name
        self.env = os.environ.copy()
        self.env["MEMORY_LOG_PATH"] = self.log_path
        self.env["NEO4J_URI"] = ""

    def tearDown(self) -> None:
        try:
            os.unlink(self.log_path)
        except OSError:
            pass

    def _start(self, entry: Path) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(entry)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=self.env,
            cwd=str(entry.parent),
        )

    def test_initialize_and_tools_list(self) -> None:
        proc = self._start(SERVER)
        try:
            init = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.0.1"},
                    },
                },
            )
            self.assertEqual(init["result"]["serverInfo"]["name"], "ai-memory-brain-librarian")

            listed = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            names = {tool["name"] for tool in listed["result"]["tools"]}
            self.assertTrue(
                {"memory_add", "memory_store_summary", "memory_get_date", "memory_entity_context", "memory_daily_summary", "memory_graph_overview", "memory_graph_project_day", "memory_today_graph", "memory_brain_health", "memory_today_summary", "memory_repair_graph"}
                <= names
            )
        finally:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=5)

    def test_shim_loads_same_tools(self) -> None:
        proc = self._start(SHIM)
        try:
            listed = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            names = {tool["name"] for tool in listed["result"]["tools"]}
            self.assertIn("memory_by_date", names)
        finally:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=5)

    def test_memory_roundtrip_and_compact(self) -> None:
        proc = self._start(SERVER)
        try:
            _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.0.1"},
                    },
                },
            )

            add = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_add",
                        "arguments": {
                            "text": "alpha librarian marker",
                            "kind": "note",
                            "source": "test",
                            "project": "pharos",
                            "timestamp": "2026-04-05T12:00:00+00:00",
                        },
                    },
                },
            )
            self.assertFalse(add["result"].get("isError", False))
            structured = json.loads(add["result"]["content"][0]["text"])
            self.assertTrue(structured["ok"])

            by_date = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "memory_get_date", "arguments": {"date": "2026-04-05"}},
                },
            )
            payload = json.loads(by_date["result"]["content"][0]["text"])
            self.assertEqual(len(payload["results"]), 1)
            self.assertIn("librarian", payload["results"][0]["text"])

            summary = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_store_summary",
                        "arguments": {"summary": "shipped librarian", "project": "pharos"},
                    },
                },
            )
            self.assertFalse(summary["result"].get("isError", False))

            long_text = "x" * 800
            _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_add",
                        "arguments": {"text": long_text, "source": "test"},
                    },
                },
            )
            search = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_search",
                        "arguments": {"query": "xxx", "format": "compact", "max_text_chars": 120},
                    },
                },
            )
            compact_payload = json.loads(search["result"]["content"][0]["text"])
            hit = next(item for item in compact_payload["raw_results"] if item["text"].startswith("x"))
            self.assertTrue(hit["text"].endswith("\u2026"))
            self.assertLessEqual(len(hit["text"]), 121)
        finally:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
