import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memory_store import (
    approve_review_queue_item,
    _build_helper_prompt,
    _event_date,
    _normalize_extracted_payload,
    _project_key,
    append_jsonl,
    get_review_queue,
    get_vault_status,
    persist_event,
    reject_review_queue_item,
    load_settings,
    normalize_event,
    should_store_in_graph,
)
from memory_store import get_events_by_date, get_recent_events, search_events
from runtime_layout import resolve_runtime_layout


class MemoryStoreTests(unittest.TestCase):
    def test_normalize_event_sets_defaults(self) -> None:
        normalized = normalize_event({"text": "hello"})
        self.assertIn("id", normalized)
        self.assertEqual(normalized["source"], "unknown")
        self.assertEqual(normalized["kind"], "note")
        self.assertEqual(normalized["text"], "hello")
        self.assertEqual(normalized["importance"], "normal")

    def test_should_store_in_graph_for_high_signal_kind(self) -> None:
        self.assertTrue(should_store_in_graph({"kind": "task_summary", "text": "done"}))

    def test_should_store_in_graph_for_explicit_prefix(self) -> None:
        self.assertTrue(should_store_in_graph({"kind": "note", "text": "remember: this"}))

    def test_should_store_in_graph_for_milestone(self) -> None:
        self.assertTrue(should_store_in_graph({"kind": "milestone", "text": "shipped the thing"}))

    def test_append_jsonl_writes_single_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            append_jsonl(str(path), {"text": "hi"})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["text"], "hi")

    def test_recent_and_date_queries_use_raw_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(str(path), {"text": "older", "timestamp": "2026-04-05T10:00:00+00:00"})
                append_jsonl(str(path), {"text": "newer", "timestamp": "2026-04-11T10:00:00+00:00"})
                recent = get_recent_events(limit=1)
                by_date = get_events_by_date("2026-04-05")
                self.assertEqual(recent[0]["text"], "newer")
                self.assertEqual(by_date[0]["text"], "older")
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_matches_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(str(path), {"text": "fixed graphiti issue", "importance": "high"})
                append_jsonl(str(path), {"text": "random note"})
                results = search_events("graphiti", limit=5)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["text"], "fixed graphiti issue")
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_normalize_extracted_payload_accepts_list_response(self) -> None:
        payload = _normalize_extracted_payload([{"name": "Andrew", "type": "person"}])
        self.assertEqual(len(payload["entities"]), 1)
        self.assertEqual(payload["entities"][0]["name"], "Andrew")
        self.assertEqual(payload["relations"], [])

    def test_project_and_date_helpers(self) -> None:
        self.assertEqual(_project_key(" Yellow-Com "), "yellow-com")
        self.assertEqual(_event_date("2026-04-13T10:20:30+00:00"), "2026-04-13")

    def test_build_helper_prompt_mentions_json_contract_and_examples(self) -> None:
        prompt = _build_helper_prompt({"kind": "task_summary", "project": "pharos", "text": "Andrew fixed Neo4j auth."})
        self.assertIn('Return only strict JSON', prompt)
        self.assertIn('Example output', prompt)
        self.assertIn('Andrew fixed Neo4j auth.', prompt)
        self.assertIn('project=pharos', prompt)

    def test_runtime_layout_uses_app_home_and_scaffolds_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_log = os.environ.get("MEMORY_LOG_PATH")
            old_vault = os.environ.get("VAULT_PATH")
            old_dsn = os.environ.get("POSTGRES_DSN")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ.pop("MEMORY_LOG_PATH", None)
            os.environ.pop("VAULT_PATH", None)
            os.environ["POSTGRES_DSN"] = "postgresql://localhost/brain"
            try:
                layout = resolve_runtime_layout()
                settings = load_settings()
                self.assertEqual(Path(layout["memory_log_path"]), Path(tmp_dir) / "memory" / "events.jsonl")
                self.assertEqual(Path(settings["vault_path"]), Path(tmp_dir) / "vault")
                self.assertTrue((Path(tmp_dir) / "vault" / "memory" / "events").exists())
                self.assertTrue((Path(tmp_dir) / "vault" / "memory" / "review").exists())
                self.assertTrue((Path(tmp_dir) / "vault" / "README.md").exists())
                self.assertTrue(settings["postgres_enabled"])
                self.assertEqual(settings["profile"], "simple")
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home
                if old_log is None:
                    os.environ.pop("MEMORY_LOG_PATH", None)
                else:
                    os.environ["MEMORY_LOG_PATH"] = old_log
                if old_vault is None:
                    os.environ.pop("VAULT_PATH", None)
                else:
                    os.environ["VAULT_PATH"] = old_vault
                if old_dsn is None:
                    os.environ.pop("POSTGRES_DSN", None)
                else:
                    os.environ["POSTGRES_DSN"] = old_dsn

    def test_persist_event_auto_writes_daily_notes_without_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED")
            old_helper_model = os.environ.get("MEMORY_HELPER_MODEL")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_HELPER_ENABLED"] = "0"
            os.environ.pop("MEMORY_HELPER_MODEL", None)
            try:
                event = {
                    "id": "evt-daily-1",
                    "timestamp": "2026-04-15T08:30:00+00:00",
                    "source": "manual",
                    "kind": "daily_checkin",
                    "text": "Starting the app-home migration.",
                    "project": "ai-memory-brain",
                    "cwd": "/tmp/project",
                    "importance": "normal",
                    "tags": ["daily", "checkin"],
                    "metadata": {},
                }
                first = persist_event(event)
                second = persist_event(event)
                note_path = Path(tmp_dir) / "vault" / "daily-notes" / "2026-04-15.md"
                content = note_path.read_text(encoding="utf-8")
                self.assertTrue(first["ok"])
                self.assertEqual(first["vault_auto_writes"], 1)
                self.assertEqual(second["vault_auto_writes"], 1)
                self.assertEqual(content.count("<!-- ai-memory-event:evt-daily-1 -->"), 1)
                self.assertIn("Starting the app-home migration.", content)
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home
                if old_helper_enabled is None:
                    os.environ.pop("MEMORY_HELPER_ENABLED", None)
                else:
                    os.environ["MEMORY_HELPER_ENABLED"] = old_helper_enabled
                if old_helper_model is None:
                    os.environ.pop("MEMORY_HELPER_MODEL", None)
                else:
                    os.environ["MEMORY_HELPER_MODEL"] = old_helper_model

    def test_persist_event_queues_review_note_for_milestones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED")
            old_helper_model = os.environ.get("MEMORY_HELPER_MODEL")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_HELPER_ENABLED"] = "0"
            os.environ.pop("MEMORY_HELPER_MODEL", None)
            try:
                result = persist_event(
                    {
                        "id": "evt-milestone-1",
                        "timestamp": "2026-04-15T18:45:00+00:00",
                        "source": "manual",
                        "kind": "milestone",
                        "text": "Moved runtime memory out of the repo.",
                        "project": "ai-memory-brain",
                        "cwd": "/tmp/project",
                        "importance": "high",
                        "tags": ["milestone"],
                        "metadata": {},
                    }
                )
                review_notes = list((Path(tmp_dir) / "vault" / "memory" / "review").glob("*.md"))
                self.assertTrue(result["ok"])
                self.assertEqual(result["vault_review_items"], 1)
                self.assertEqual(len(review_notes), 1)
                self.assertIn("Moved runtime memory out of the repo.", review_notes[0].read_text(encoding="utf-8"))
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home
                if old_helper_enabled is None:
                    os.environ.pop("MEMORY_HELPER_ENABLED", None)
                else:
                    os.environ["MEMORY_HELPER_ENABLED"] = old_helper_enabled
                if old_helper_model is None:
                    os.environ.pop("MEMORY_HELPER_MODEL", None)
                else:
                    os.environ["MEMORY_HELPER_MODEL"] = old_helper_model

    def test_review_queue_promote_and_reject_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED")
            old_helper_model = os.environ.get("MEMORY_HELPER_MODEL")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_HELPER_ENABLED"] = "0"
            os.environ.pop("MEMORY_HELPER_MODEL", None)
            try:
                persist_event(
                    {
                        "id": "evt-review-1",
                        "timestamp": "2026-04-15T20:00:00+00:00",
                        "source": "manual",
                        "kind": "decision",
                        "text": "Use JSONL as the first-write source of truth.",
                        "project": "ai-memory-brain",
                        "cwd": "/tmp/project",
                        "importance": "high",
                        "tags": ["decision"],
                        "metadata": {},
                    }
                )
                queue = get_review_queue(status="pending", limit=10)
                self.assertTrue(queue["ok"])
                self.assertEqual(queue["count"], 1)
                queue_key = queue["items"][0]["queue_key"]

                approved = approve_review_queue_item(
                    queue_key=queue_key,
                    target="projects",
                    title="jsonl-first-decision",
                )
                self.assertTrue(approved["ok"])
                self.assertEqual(approved["status"], "approved")
                self.assertTrue(Path(approved["promoted_path"]).exists())
                promoted_path = Path(approved["promoted_path"])
                self.assertEqual(promoted_path.name, "jsonl-first-decision.md")

                persist_event(
                    {
                        "id": "evt-review-3",
                        "timestamp": "2026-04-15T20:10:00+00:00",
                        "source": "manual",
                        "kind": "fix",
                        "text": "Fixed queue state updates.",
                        "project": "ai-memory-brain",
                        "cwd": "/tmp/project",
                        "importance": "high",
                        "tags": ["fix"],
                        "metadata": {},
                    }
                )
                queue_merge = get_review_queue(status="pending", limit=10)
                self.assertEqual(queue_merge["count"], 1)
                approved_second = approve_review_queue_item(
                    queue_key=queue_merge["items"][0]["queue_key"],
                    target="projects",
                    title="jsonl-first-decision",
                )
                self.assertTrue(approved_second["ok"])
                self.assertEqual(approved_second["promoted_path"], str(promoted_path))
                merged_content = promoted_path.read_text(encoding="utf-8")
                self.assertEqual(merged_content.count("<!-- ai-memory-event:evt-review-1 -->"), 1)
                self.assertEqual(merged_content.count("<!-- ai-memory-event:evt-review-3 -->"), 1)

                persist_event(
                    {
                        "id": "evt-review-2",
                        "timestamp": "2026-04-15T20:15:00+00:00",
                        "source": "manual",
                        "kind": "project_fact",
                        "text": "Postgres stays off the hot path.",
                        "project": "ai-memory-brain",
                        "cwd": "/tmp/project",
                        "importance": "high",
                        "tags": ["fact"],
                        "metadata": {},
                    }
                )
                queue_after = get_review_queue(status="pending", limit=10)
                self.assertEqual(queue_after["count"], 1)
                rejected = reject_review_queue_item(
                    queue_key=queue_after["items"][0]["queue_key"],
                    reason="Not ready to promote yet.",
                )
                self.assertTrue(rejected["ok"])
                self.assertEqual(rejected["status"], "rejected")

                health = get_vault_status()
                self.assertTrue(health["ok"])
                self.assertEqual(health["queue"]["approved"], 2)
                self.assertEqual(health["queue"]["rejected"], 1)
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home
                if old_helper_enabled is None:
                    os.environ.pop("MEMORY_HELPER_ENABLED", None)
                else:
                    os.environ["MEMORY_HELPER_ENABLED"] = old_helper_enabled
                if old_helper_model is None:
                    os.environ.pop("MEMORY_HELPER_MODEL", None)
                else:
                    os.environ["MEMORY_HELPER_MODEL"] = old_helper_model


if __name__ == "__main__":
    unittest.main()
