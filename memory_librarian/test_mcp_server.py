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
        self.app_home = tempfile.mkdtemp(prefix="ai-memory-brain-")
        self.env = os.environ.copy()
        self.env["AI_MEMORY_BRAIN_HOME"] = self.app_home
        self.env["MEMORY_LOG_PATH"] = self.log_path
        self.env["NEO4J_URI"] = ""

    def tearDown(self) -> None:
        try:
            os.unlink(self.log_path)
        except OSError:
            pass
        try:
            __import__("shutil").rmtree(self.app_home)
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
                {
                    "memory_add",
                    "memory_store_summary",
                    "memory_meeting_summary",
                    "memory_get_date",
                    "memory_entity_context",
                    "memory_daily_summary",
                    "memory_graph_overview",
                    "memory_graph_project_day",
                    "memory_today_graph",
                    "memory_brain_health",
                    "memory_today_summary",
                    "memory_repair_graph",
                    "memory_vault_status",
                    "memory_postgres_status",
                    "memory_review_queue",
                    "memory_review_approve",
                    "memory_review_reject",
                }
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

            meeting_summary = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 51,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_meeting_summary",
                        "arguments": {"text": "Sync covered launch blockers.", "project": "pharos"},
                    },
                },
            )
            self.assertFalse(meeting_summary["result"].get("isError", False))
            meeting_payload = json.loads(meeting_summary["result"]["content"][0]["text"])
            self.assertTrue(meeting_payload["ok"])
            self.assertEqual(meeting_payload["event"]["kind"], "meeting_summary")

            meeting_with_metadata = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 52,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_meeting_summary",
                        "arguments": {
                            "text": "Timestamp + branch metadata test",
                            "project": "pharos",
                            "timestamp": "2026-04-05T16:00:00+00:00",
                            "branch": "feature/review-flow",
                        },
                    },
                },
            )
            self.assertFalse(meeting_with_metadata["result"].get("isError", False))
            meta_payload = json.loads(meeting_with_metadata["result"]["content"][0]["text"])
            self.assertEqual(meta_payload["event"]["timestamp"], "2026-04-05T16:00:00+00:00")
            self.assertEqual(meta_payload["event"]["metadata"]["branch"], "feature/review-flow")

            missing_text = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 53,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_meeting_summary",
                        "arguments": {"project": "pharos"},
                    },
                },
            )
            self.assertTrue("error" in missing_text or missing_text["result"].get("isError", False))

            bad_importance = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 54,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_meeting_summary",
                        "arguments": {"text": "bad importance", "importance": "urgent"},
                    },
                },
            )
            self.assertTrue(bad_importance["result"].get("isError", False))
            self.assertIn("importance must be one of", bad_importance["result"]["content"][0]["text"])

            bad_tags = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 55,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_meeting_summary",
                        "arguments": {"text": "bad tags", "tags": "ops,team"},
                    },
                },
            )
            self.assertTrue(bad_tags["result"].get("isError", False))
            self.assertIn("tags must be an array of strings", bad_tags["result"]["content"][0]["text"])

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

            milestone = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_add",
                        "arguments": {
                            "text": "Ship review queue operations",
                            "kind": "milestone",
                            "source": "test",
                            "project": "pharos",
                            "timestamp": "2026-04-05T14:00:00+00:00",
                        },
                    },
                },
            )
            self.assertFalse(milestone["result"].get("isError", False))

            queue = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "memory_review_queue", "arguments": {"status": "pending", "limit": 5}},
                },
            )
            queue_payload = json.loads(queue["result"]["content"][0]["text"])
            self.assertGreaterEqual(queue_payload["count"], 1)
            queue_key = queue_payload["items"][0]["queue_key"]

            approve = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_review_approve",
                        "arguments": {"queue_key": queue_key, "target": "projects"},
                    },
                },
            )
            approve_payload = json.loads(approve["result"]["content"][0]["text"])
            self.assertTrue(approve_payload["ok"])
            self.assertEqual(approve_payload["status"], "approved")

            vault_status = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {"name": "memory_vault_status", "arguments": {}},
                },
            )
            vault_payload = json.loads(vault_status["result"]["content"][0]["text"])
            self.assertTrue(vault_payload["ok"])
            self.assertGreaterEqual(vault_payload["queue"]["approved"], 1)
        finally:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
