import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memory_store import (
    _build_helper_prompt,
    _event_date,
    _normalize_extracted_payload,
    _project_key,
    append_jsonl,
    normalize_event,
    should_store_in_graph,
)
from memory_store import get_events_by_date, get_recent_events, search_events


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


if __name__ == "__main__":
    unittest.main()
