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
                    "memory_store_structured",
                    "memory_store_failed_attempt",
                    "memory_open_loop_add",
                    "memory_open_loop_update",
                    "memory_open_loops",
                    "memory_promote_canon",
                    "memory_mark_superseded",
                    "memory_start_session",
                    "memory_task_context",
                    "memory_project_canon",
                    "memory_machine_context",
                    "memory_execution_hints",
                    "memory_timeline",
                    "memory_quality_report",
                    "memory_cleanup_candidates",
                    "memory_get_date",
                    "memory_entity_context",
                    "memory_daily_summary",
                    "memory_graph_overview",
                    "memory_graph_project_day",
                    "memory_today_graph",
                    "memory_brain_health",
                    "memory_brain_doctor",
                    "memory_compact_day",
                    "memory_entity_hygiene",
                    "memory_today_summary",
                    "memory_repair_graph",
                    "memory_vault_status",
                    "memory_postgres_status",
                    "memory_postgres_recent",
                    "memory_postgres_review_queue",
                    "memory_postgres_bridge_writes",
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
                        "arguments": {
                            "summary": (
                                "Goal: ship librarian.\n"
                                "Changes: wired MCP storage and retrieval polish.\n"
                                "Decisions: kept JSONL as canonical first-write path.\n"
                                "Validation: unit tests and MCP roundtrip checks passed.\n"
                                "Risks/TODO: monitor recall quality over the next sessions."
                            ),
                            "project": "pharos",
                        },
                    },
                },
            )
            self.assertFalse(summary["result"].get("isError", False))

            bad_summary_shape = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 501,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_store_summary",
                        "arguments": {"summary": "quick note only"},
                    },
                },
            )
            self.assertTrue(bad_summary_shape["result"].get("isError", False))
            self.assertIn("Goal, Changes, Decisions, Validation, and Risks/TODO", bad_summary_shape["result"]["content"][0]["text"])

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
            self.assertEqual(meta_payload["event"]["metadata"]["repo_context"]["branch"], "feature/review-flow")

            structured = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 521,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_store_structured",
                        "arguments": {
                            "kind": "task_summary",
                            "project": "pharos",
                            "goal": "Ship session bootstrap",
                            "changes": "Added open loops and execution hints",
                            "decision": "Keep JSONL canonical",
                            "validation": "mcp tests passed",
                            "next_step": "exercise on next session",
                            "risk": "watch ranking noise",
                            "branch": "feature/bootstrap",
                            "commit_sha": "deadbeef",
                            "files_touched": ["memory_gateway/memory_store.py"],
                            "commands_run": ["python -m unittest memory_librarian/test_mcp_server.py"],
                            "tests": ["memory_librarian/test_mcp_server.py"],
                        },
                    },
                },
            )
            self.assertFalse(structured["result"].get("isError", False))
            structured_payload = json.loads(structured["result"]["content"][0]["text"])
            self.assertEqual(structured_payload["event"]["metadata"]["repo_context"]["commit_sha"], "deadbeef")

            failed_attempt = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 5211,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_store_failed_attempt",
                        "arguments": {
                            "project": "pharos",
                            "goal": "Run wrapper failure path",
                            "changes": "wrapper exited with code 1",
                            "validation": "non-zero exit observed",
                            "next_step": "inspect output before retrying",
                            "risk": "repeating blindly wastes time",
                        },
                    },
                },
            )
            self.assertFalse(failed_attempt["result"].get("isError", False))
            failed_payload = json.loads(failed_attempt["result"]["content"][0]["text"])
            self.assertEqual(failed_payload["event"]["kind"], "failed_attempt")
            self.assertTrue(failed_payload["auto_open_loop_created"])

            open_loop = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 522,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_open_loop_add",
                        "arguments": {
                            "title": "Fix retrieval drift",
                            "project": "pharos",
                            "note": "ranking still noisy around file overlap",
                            "next_step": "compare results against recent session",
                            "files_touched": ["memory_gateway/memory_store.py"],
                        },
                    },
                },
            )
            self.assertFalse(open_loop["result"].get("isError", False))
            loop_payload = json.loads(open_loop["result"]["content"][0]["text"])
            loop_id = loop_payload["event"]["metadata"]["loop_id"]

            update_loop = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 523,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_open_loop_update",
                        "arguments": {
                            "loop_id": loop_id,
                            "project": "pharos",
                            "status": "blocked",
                            "note": "need more live examples",
                        },
                    },
                },
            )
            self.assertFalse(update_loop["result"].get("isError", False))

            loop_list = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 524,
                    "method": "tools/call",
                    "params": {"name": "memory_open_loops", "arguments": {"project": "pharos", "limit": 5}},
                },
            )
            loop_list_payload = json.loads(loop_list["result"]["content"][0]["text"])
            self.assertTrue(loop_list_payload["items"])
            self.assertEqual(loop_list_payload["items"][0]["status"], "blocked")

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

            bad_add_importance = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 56,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_add",
                        "arguments": {"text": "bad add importance", "importance": "critical"},
                    },
                },
            )
            self.assertTrue(bad_add_importance["result"].get("isError", False))
            self.assertIn("importance must be one of", bad_add_importance["result"]["content"][0]["text"])

            bad_summary_tags = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 57,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_store_summary",
                        "arguments": {
                            "summary": (
                                "Goal: test bad tags.\n"
                                "Changes: send invalid tags payload.\n"
                                "Decisions: validation should reject non-string entries.\n"
                                "Validation: expect schema error.\n"
                                "Risks/TODO: none."
                            ),
                            "tags": [1, "ok"],
                        },
                    },
                },
            )
            self.assertTrue(bad_summary_tags["result"].get("isError", False))
            self.assertIn("tags must be an array of strings", bad_summary_tags["result"]["content"][0]["text"])

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
            self.assertIn("retrieval", hit)
            self.assertIn("confidence", hit["retrieval"])

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
            approve_payload = {"ok": False}
            for idx, item in enumerate(queue_payload["items"]):
                queue_key = item.get("queue_key", "")
                if not isinstance(queue_key, str) or not queue_key.startswith("review:"):
                    continue
                approve = _rpc(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": 10 + idx,
                        "method": "tools/call",
                        "params": {
                            "name": "memory_review_approve",
                            "arguments": {"queue_key": queue_key, "target": "projects"},
                        },
                    },
                )
                approve_payload = json.loads(approve["result"]["content"][0]["text"])
                if approve_payload.get("ok"):
                    break
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

            recent = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 111,
                    "method": "tools/call",
                    "params": {"name": "memory_recent", "arguments": {"project": "pharos", "limit": 10}},
                },
            )
            recent_payload = json.loads(recent["result"]["content"][0]["text"])
            self.assertIn("raw_results", recent_payload)
            self.assertTrue(recent_payload["raw_results"])
            first_recent = recent_payload["raw_results"][0]
            self.assertIn("retrieval", first_recent)
            self.assertIn("confidence", first_recent["retrieval"])
            self.assertIn("score_breakdown", first_recent["retrieval"])

            project_context = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 112,
                    "method": "tools/call",
                    "params": {"name": "memory_project_context", "arguments": {"project": "pharos", "limit": 10}},
                },
            )
            project_payload = json.loads(project_context["result"]["content"][0]["text"])
            self.assertIn("context", project_payload)
            self.assertIn("recent", project_payload["context"])
            self.assertTrue(project_payload["context"]["recent"])
            first_context = project_payload["context"]["recent"][0]
            self.assertIn("retrieval", first_context)
            self.assertIn("match_type", first_context["retrieval"])
            self.assertIn("score_breakdown", first_context["retrieval"])

            start_session = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 113,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_start_session",
                        "arguments": {"project": "pharos", "query": "bootstrap", "file_paths": ["memory_gateway/memory_store.py"]},
                    },
                },
            )
            start_payload = json.loads(start_session["result"]["content"][0]["text"])
            self.assertIn("open_loops", start_payload)
            self.assertIn("execution_hints", start_payload)
            self.assertIn("task_context", start_payload)

            task_context = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 114,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_task_context",
                        "arguments": {"query": "retrieval drift", "project": "pharos", "file_paths": ["memory_gateway/memory_store.py"]},
                    },
                },
            )
            task_payload = json.loads(task_context["result"]["content"][0]["text"])
            self.assertIn("results", task_payload)

            canon = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 115,
                    "method": "tools/call",
                    "params": {"name": "memory_project_canon", "arguments": {"project": "pharos"}},
                },
            )
            canon_payload = json.loads(canon["result"]["content"][0]["text"])
            self.assertIn("items", canon_payload)

            hints = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 116,
                    "method": "tools/call",
                    "params": {"name": "memory_execution_hints", "arguments": {"project": "pharos"}},
                },
            )
            hints_payload = json.loads(hints["result"]["content"][0]["text"])
            self.assertIn("commands", hints_payload)

            timeline = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 117,
                    "method": "tools/call",
                    "params": {"name": "memory_timeline", "arguments": {"project": "pharos", "days": 30}},
                },
            )
            timeline_payload = json.loads(timeline["result"]["content"][0]["text"])
            self.assertIn("days", timeline_payload)

            quality = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 118,
                    "method": "tools/call",
                    "params": {"name": "memory_quality_report", "arguments": {"project": "pharos"}},
                },
            )
            quality_payload = json.loads(quality["result"]["content"][0]["text"])
            self.assertIn("issue_count", quality_payload)
            self.assertIn("explainability", quality_payload)

            canon_source_event_id = structured_payload["event"]["id"]
            promote = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 119,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_promote_canon",
                        "arguments": {"event_id": canon_source_event_id, "project": "pharos", "title": "Pharos bootstrap canon"},
                    },
                },
            )
            promote_payload = json.loads(promote["result"]["content"][0]["text"])
            self.assertTrue(promote_payload["ok"])

            supersede = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 120,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_mark_superseded",
                        "arguments": {
                            "old_event_id": failed_payload["event"]["id"],
                            "new_event_id": canon_source_event_id,
                            "project": "pharos",
                            "reason": "newer setup memory supersedes older one",
                        },
                    },
                },
            )
            supersede_payload = json.loads(supersede["result"]["content"][0]["text"])
            self.assertTrue(supersede_payload["ok"])

            machine = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 121,
                    "method": "tools/call",
                    "params": {"name": "memory_machine_context", "arguments": {"project": "pharos"}},
                },
            )
            machine_payload = json.loads(machine["result"]["content"][0]["text"])
            self.assertIn("canon", machine_payload)

            cleanup = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 122,
                    "method": "tools/call",
                    "params": {"name": "memory_cleanup_candidates", "arguments": {"project": "pharos"}},
                },
            )
            cleanup_payload = json.loads(cleanup["result"]["content"][0]["text"])
            self.assertIn("items", cleanup_payload)

            doctor = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "tools/call",
                    "params": {"name": "memory_brain_doctor", "arguments": {"format": "compact"}},
                },
            )
            self.assertFalse(doctor["result"].get("isError", False))
            doctor_payload = json.loads(doctor["result"]["content"][0]["text"])
            self.assertIn("checks", doctor_payload)
            self.assertIn("explainability", doctor_payload)
            self.assertEqual(doctor_payload["explainability"]["tool"], "memory_brain_doctor")

            compact_day = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 13,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_compact_day",
                        "arguments": {"date": "2026-04-05", "project": "pharos"},
                    },
                },
            )
            self.assertFalse(compact_day["result"].get("isError", False))
            compact_payload = json.loads(compact_day["result"]["content"][0]["text"])
            self.assertEqual(compact_payload["date"], "2026-04-05")
            self.assertEqual(compact_payload["project"], "pharos")
            self.assertIn("explainability", compact_payload)

            hygiene = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 14,
                    "method": "tools/call",
                    "params": {"name": "memory_entity_hygiene", "arguments": {"format": "compact"}},
                },
            )
            self.assertFalse(hygiene["result"].get("isError", False))
            hygiene_payload = json.loads(hygiene["result"]["content"][0]["text"])
            self.assertIn("duplicate_cluster_count", hygiene_payload)
            self.assertIn("explainability", hygiene_payload)

            # Maintenance tool contract parity: compact and full both expose core contract keys.
            for tool_name, args in (
                ("memory_vault_status", {}),
                ("memory_postgres_status", {}),
                ("memory_brain_health", {"limit": 5}),
                ("memory_brain_doctor", {}),
                ("memory_entity_hygiene", {}),
                ("memory_quality_report", {"project": "pharos"}),
            ):
                full_resp = _rpc(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": 2000,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": {"format": "full", **args}},
                    },
                )
                compact_resp = _rpc(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": 2001,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": {"format": "compact", **args}},
                    },
                )
                full_payload = json.loads(full_resp["result"]["content"][0]["text"])
                compact_payload = json.loads(compact_resp["result"]["content"][0]["text"])
                for payload in (full_payload, compact_payload):
                    self.assertIn("ok", payload)
                    self.assertIn("error", payload)
                    self.assertIn("explainability", payload)
                    self.assertEqual(payload["explainability"]["tool"], tool_name)
        finally:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
