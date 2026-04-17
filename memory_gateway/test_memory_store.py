import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from memory_store import (
    approve_review_queue_item,
    _build_helper_prompt,
    _event_date,
    _normalize_extracted_payload,
    _project_key,
    append_jsonl,
    get_cleanup_candidates,
    get_execution_hints,
    get_machine_context,
    get_memory_quality_report,
    get_open_loops,
    get_review_queue,
    get_task_context,
    get_timeline,
    get_vault_status,
    mark_memory_superseded,
    persist_event,
    promote_memory_to_canon,
    reject_review_queue_item,
    load_settings,
    normalize_event,
    start_session,
    store_structured_memory,
    should_store_in_graph,
)
from memory_store import get_events_by_date, get_project_context, get_recent_events, search_events
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
                self.assertIn("retrieval", results[0])
                self.assertIn("confidence", results[0]["retrieval"])
                self.assertIn("score_breakdown", results[0]["retrieval"])
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_matches_query_tokens_without_exact_substring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "Fixed retrieval ranking and dedupe tuning for project memory.",
                        "importance": "normal",
                        "timestamp": "2026-04-17T12:00:00+00:00",
                    },
                )
                # No exact substring "ranking retrieval dedupe", but all tokens exist.
                results = search_events("ranking retrieval dedupe", limit=5)
                self.assertEqual(len(results), 1)
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_ignores_stopwords_and_matches_signal_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "Implemented memory ranking improvements for retrieval quality.",
                        "importance": "normal",
                        "timestamp": "2026-04-17T12:10:00+00:00",
                    },
                )
                # stopwords should not block a relevant hit.
                results = search_events("the memory and retrieval", limit=5)
                self.assertEqual(len(results), 1)
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_allows_strong_partial_token_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "Fixed graph repair and retrieval ranking fallback path.",
                        "importance": "normal",
                        "timestamp": "2026-04-17T12:15:00+00:00",
                    },
                )
                # 3/4 query tokens match; should still be useful.
                results = search_events("graph repair ranking postgres", limit=5)
                self.assertEqual(len(results), 1)
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_matches_token_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "Improved retrieval ranking and memory dedupe behavior.",
                        "importance": "normal",
                        "timestamp": "2026-04-17T12:20:00+00:00",
                    },
                )
                # Prefix tokens should match full words.
                results = search_events("retriev rank mem", limit=5)
                self.assertEqual(len(results), 1)
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_avoids_midword_noise_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "Program state updated after deploy.",
                        "importance": "normal",
                        "timestamp": "2026-04-17T12:25:00+00:00",
                    },
                )
                # "gram" should not match the middle of "program".
                results = search_events("gram", limit=5)
                self.assertEqual(len(results), 0)
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_short_single_token_requires_exact_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "Merged graph recall pipeline.",
                        "importance": "normal",
                        "timestamp": "2026-04-17T12:30:00+00:00",
                    },
                )
                # Single short token should be strict to avoid broad noisy matches.
                results = search_events("gr", limit=5)
                self.assertEqual(len(results), 0)
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_short_token_exact_word_still_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "gr migration marker",
                        "importance": "normal",
                        "timestamp": "2026-04-17T12:31:00+00:00",
                    },
                )
                results = search_events("gr", limit=5)
                self.assertEqual(len(results), 1)
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_search_events_prefers_recent_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "migration decision finalized",
                        "importance": "normal",
                        "timestamp": "2026-01-01T10:00:00+00:00",
                    },
                )
                append_jsonl(
                    str(path),
                    {
                        "text": "migration decision finalized",
                        "importance": "normal",
                        "timestamp": "2026-04-16T10:00:00+00:00",
                    },
                )
                results = search_events("migration decision", limit=2)
                self.assertEqual(results[0]["timestamp"], "2026-04-16T10:00:00+00:00")
                self.assertGreaterEqual(results[0]["retrieval"]["confidence"], results[1]["retrieval"]["confidence"])
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_semantic_dedupe_skips_near_duplicate_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED")
            old_helper_model = os.environ.get("MEMORY_HELPER_MODEL")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_HELPER_ENABLED"] = "0"
            os.environ.pop("MEMORY_HELPER_MODEL", None)
            try:
                first = persist_event(
                    {
                        "id": "evt-semantic-1",
                        "timestamp": "2026-04-17T10:00:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": (
                            "Goal: finish retrieval ranker.\n"
                            "Changes: improved scoring and boosts.\n"
                            "Decisions: prefer recent project context.\n"
                            "Validation: tests passed.\n"
                            "Risks/TODO: tune weights."
                        ),
                        "project": "ai-memory-brain",
                        "cwd": "/tmp/project",
                        "importance": "normal",
                        "tags": ["retrieval"],
                        "metadata": {},
                    }
                )
                second = persist_event(
                    {
                        "id": "evt-semantic-2",
                        "timestamp": "2026-04-17T10:05:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": (
                            "Goal: finish retrieval ranking.\n"
                            "Changes: improved scoring with boosts.\n"
                            "Decisions: prefer recent project context.\n"
                            "Validation: tests are passing.\n"
                            "Risks/TODO: tune weight values."
                        ),
                        "project": "ai-memory-brain",
                        "cwd": "/tmp/project",
                        "importance": "normal",
                        "tags": ["retrieval"],
                        "metadata": {},
                    }
                )
                events_path = Path(tmp_dir) / "memory" / "events.jsonl"
                lines = events_path.read_text(encoding="utf-8").splitlines()
                self.assertTrue(first["ok"])
                self.assertFalse(first["deduplicated"])
                self.assertTrue(second["deduplicated"])
                self.assertEqual(len(lines), 1)
                self.assertEqual(second["duplicate_event_id"], "evt-semantic-1")
                self.assertIn("dedupe_explain", second)
                self.assertGreater(float(second["dedupe_explain"]["similarity"]), 0.0)
                self.assertEqual(second["dedupe_explain"]["window_minutes"], 60)
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

    def test_persist_event_dedupe_explain_shape_parity_between_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED")
            old_helper_model = os.environ.get("MEMORY_HELPER_MODEL")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_HELPER_ENABLED"] = "0"
            os.environ.pop("MEMORY_HELPER_MODEL", None)
            required_keys = {
                "similarity",
                "threshold",
                "window_minutes",
                "window_policy",
                "force_store",
                "matched_kind",
                "matched_project",
                "matched_source",
            }
            try:
                non_deduped = persist_event(
                    {
                        "id": "evt-dedupe-shape-1",
                        "timestamp": "2026-04-17T12:00:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": (
                            "Goal: stabilize ranking outputs.\n"
                            "Changes: tuned project boosts.\n"
                            "Decisions: keep recency weighting.\n"
                            "Validation: regression tests pass.\n"
                            "Risks/TODO: track drift."
                        ),
                        "project": "ai-memory-brain",
                    }
                )
                deduped = persist_event(
                    {
                        "id": "evt-dedupe-shape-2",
                        "timestamp": "2026-04-17T12:03:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": (
                            "Goal: stabilize ranking output.\n"
                            "Changes: tuned project boosts.\n"
                            "Decisions: keep recency weighting.\n"
                            "Validation: regression tests passing.\n"
                            "Risks/TODO: track drift."
                        ),
                        "project": "ai-memory-brain",
                    }
                )

                self.assertFalse(non_deduped["deduplicated"])
                self.assertTrue(deduped["deduplicated"])
                self.assertIn("dedupe_explain", non_deduped)
                self.assertIn("dedupe_explain", deduped)
                self.assertTrue(required_keys.issubset(non_deduped["dedupe_explain"].keys()))
                self.assertTrue(required_keys.issubset(deduped["dedupe_explain"].keys()))

                # Baseline write has no prior candidate, so similarity can be zero.
                self.assertEqual(float(non_deduped["dedupe_explain"]["similarity"]), 0.0)
                # Deduplicated responses must report positive overlap.
                self.assertGreater(float(deduped["dedupe_explain"]["similarity"]), 0.0)
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

    def test_dedupe_threshold_env_controls_sensitivity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED")
            old_helper_model = os.environ.get("MEMORY_HELPER_MODEL")
            old_threshold = os.environ.get("MEMORY_DEDUPE_SIMILARITY_THRESHOLD")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_HELPER_ENABLED"] = "0"
            os.environ.pop("MEMORY_HELPER_MODEL", None)
            try:
                os.environ["MEMORY_DEDUPE_SIMILARITY_THRESHOLD"] = "0.99"
                persist_event(
                    {
                        "id": "evt-threshold-1",
                        "timestamp": "2026-04-17T11:00:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": "Goal: tune ranking. Changes: add boosts. Decisions: keep simple. Validation: tests pass. Risks/TODO: monitor.",
                        "project": "ai-memory-brain",
                    }
                )
                second_high = persist_event(
                    {
                        "id": "evt-threshold-2",
                        "timestamp": "2026-04-17T11:03:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": "Goal: tune rankings. Changes: added boosts. Decisions: keep simple. Validation: tests are passing. Risks/TODO: monitor.",
                        "project": "ai-memory-brain",
                    }
                )
                self.assertFalse(second_high["deduplicated"])

                os.environ["MEMORY_DEDUPE_SIMILARITY_THRESHOLD"] = "0.75"
                second_low = persist_event(
                    {
                        "id": "evt-threshold-3",
                        "timestamp": "2026-04-17T11:05:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": "Goal: tune rankings. Changes: added boost rules. Decisions: keep simple. Validation: test suite passing. Risks/TODO: keep watching.",
                        "project": "ai-memory-brain",
                    }
                )
                self.assertTrue(second_low["deduplicated"])
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
                if old_threshold is None:
                    os.environ.pop("MEMORY_DEDUPE_SIMILARITY_THRESHOLD", None)
                else:
                    os.environ["MEMORY_DEDUPE_SIMILARITY_THRESHOLD"] = old_threshold

    def test_high_signal_kind_uses_weighted_dedupe_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_window = os.environ.get("MEMORY_DEDUPE_WINDOW_MINUTES")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_DEDUPE_WINDOW_MINUTES"] = "5"
            try:
                persist_event(
                    {
                        "id": "evt-window-1",
                        "timestamp": "2026-04-17T10:00:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": "Goal: weighted window. Changes: x. Decisions: y. Validation: z. Risks/TODO: n.",
                        "project": "ai-memory-brain",
                    }
                )
                # 9 minutes later should still dedupe because high-signal kinds use 2x window (10 min).
                second = persist_event(
                    {
                        "id": "evt-window-2",
                        "timestamp": "2026-04-17T10:09:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": "Goal: weighted window. Changes: x. Decisions: y. Validation: z. Risks/TODO: n.",
                        "project": "ai-memory-brain",
                    }
                )
                self.assertTrue(second["deduplicated"])
                self.assertEqual(second["dedupe_explain"]["window_minutes"], 10)
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home
                if old_window is None:
                    os.environ.pop("MEMORY_DEDUPE_WINDOW_MINUTES", None)
                else:
                    os.environ["MEMORY_DEDUPE_WINDOW_MINUTES"] = old_window

    def test_force_store_metadata_bypasses_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            try:
                first = persist_event(
                    {
                        "id": "evt-force-1",
                        "timestamp": "2026-04-17T10:00:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": "Goal: force store test. Changes: x. Decisions: y. Validation: z. Risks/TODO: n.",
                        "project": "ai-memory-brain",
                    }
                )
                second = persist_event(
                    {
                        "id": "evt-force-2",
                        "timestamp": "2026-04-17T10:01:00+00:00",
                        "source": "agent",
                        "kind": "task_summary",
                        "text": "Goal: force store test. Changes: x. Decisions: y. Validation: z. Risks/TODO: n.",
                        "project": "ai-memory-brain",
                        "metadata": {"force_store": True},
                    }
                )
                self.assertFalse(first["deduplicated"])
                self.assertFalse(second["deduplicated"])
                self.assertTrue(second["dedupe_explain"]["force_store"])
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home

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
            old_dedupe_window = os.environ.get("MEMORY_DEDUPE_WINDOW_MINUTES")
            old_dedupe_threshold = os.environ.get("MEMORY_DEDUPE_SIMILARITY_THRESHOLD")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ.pop("MEMORY_LOG_PATH", None)
            os.environ.pop("VAULT_PATH", None)
            os.environ["POSTGRES_DSN"] = "postgresql://localhost/brain"
            os.environ["MEMORY_DEDUPE_WINDOW_MINUTES"] = "45"
            os.environ["MEMORY_DEDUPE_SIMILARITY_THRESHOLD"] = "0.8"
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
                self.assertEqual(settings["dedupe_window_minutes"], 45)
                self.assertEqual(settings["dedupe_similarity_threshold"], 0.8)
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
                if old_dedupe_window is None:
                    os.environ.pop("MEMORY_DEDUPE_WINDOW_MINUTES", None)
                else:
                    os.environ["MEMORY_DEDUPE_WINDOW_MINUTES"] = old_dedupe_window
                if old_dedupe_threshold is None:
                    os.environ.pop("MEMORY_DEDUPE_SIMILARITY_THRESHOLD", None)
                else:
                    os.environ["MEMORY_DEDUPE_SIMILARITY_THRESHOLD"] = old_dedupe_threshold

    def test_get_recent_events_includes_retrieval_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(str(path), {"text": "older recent", "timestamp": "2026-04-10T10:00:00+00:00"})
                append_jsonl(str(path), {"text": "newer recent", "timestamp": "2026-04-11T10:00:00+00:00", "importance": "high"})
                results = get_recent_events(limit=2)
                self.assertEqual(results[0]["text"], "newer recent")
                self.assertIn("retrieval", results[0])
                self.assertIn("match_type", results[0]["retrieval"])
                self.assertIn("score_breakdown", results[0]["retrieval"])
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

    def test_project_context_includes_retrieval_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events.jsonl"
            os_environ = __import__("os").environ
            old_path = os_environ.get("MEMORY_LOG_PATH")
            os_environ["MEMORY_LOG_PATH"] = str(path)
            try:
                append_jsonl(
                    str(path),
                    {
                        "text": "important project context",
                        "project": "ai-memory-brain",
                        "importance": "high",
                        "timestamp": "2026-04-11T10:00:00+00:00",
                    },
                )
                ctx = get_project_context("ai-memory-brain", limit=5)
                self.assertTrue(ctx["recent"])
                self.assertIn("retrieval", ctx["recent"][0])
                self.assertEqual(ctx["recent"][0]["retrieval"]["match_type"], "recent_context")
            finally:
                if old_path is None:
                    del os_environ["MEMORY_LOG_PATH"]
                else:
                    os_environ["MEMORY_LOG_PATH"] = old_path

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
                self.assertEqual(second["vault_auto_writes"], 0)
                self.assertFalse(first["deduplicated"])
                self.assertTrue(second["deduplicated"])
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

    def test_persist_event_meeting_summary_writes_idempotent_meeting_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED")
            old_helper_model = os.environ.get("MEMORY_HELPER_MODEL")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_HELPER_ENABLED"] = "0"
            os.environ.pop("MEMORY_HELPER_MODEL", None)
            try:
                event = {
                    "id": "evt-meeting-1",
                    "timestamp": "2026-04-15T12:30:00+00:00",
                    "source": "manual",
                    "kind": "meeting_summary",
                    "text": "Discussed migration milestones and rollout sequencing.",
                    "project": "ai-memory-brain",
                    "cwd": "/tmp/project",
                    "importance": "normal",
                    "tags": ["meeting"],
                    "metadata": {},
                }
                first = persist_event(event)
                second = persist_event(event)
                meetings_dir = Path(tmp_dir) / "vault" / "meetings"
                notes = list(meetings_dir.glob("*.md"))
                self.assertEqual(len(notes), 1)
                content = notes[0].read_text(encoding="utf-8")
                self.assertTrue(first["ok"])
                self.assertEqual(first["vault_auto_writes"], 1)
                self.assertEqual(second["vault_auto_writes"], 0)
                self.assertEqual(content.count("<!-- ai-memory-event:evt-meeting-1 -->"), 1)
                self.assertIn("Discussed migration milestones and rollout sequencing.", content)
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
                promoted_content = promoted_path.read_text(encoding="utf-8")
                self.assertIn("---\n", promoted_content)
                self.assertIn('title: "jsonl-first-decision"', promoted_content)
                self.assertIn("memory_event_ids: []", promoted_content)
                self.assertIn("tags:", promoted_content)
                self.assertIn("#project/ai-memory-brain", promoted_content)
                self.assertIn("#target/projects", promoted_content)
                self.assertIn("[[memory/review/", promoted_content)

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
                self.assertIn("#target/projects", merged_content)

                persist_event(
                    {
                        "id": "evt-review-people-1",
                        "timestamp": "2026-04-15T20:11:00+00:00",
                        "source": "manual",
                        "kind": "identity",
                        "text": "Captured teammate profile details.",
                        "project": "ai-memory-brain",
                        "cwd": "/tmp/project",
                        "importance": "high",
                        "tags": ["identity"],
                        "metadata": {
                            "review_payload": {
                                "entities": [
                                    {"name": "Primary Repo", "entity_type": "repo"},
                                    {"name": "Andrew Kushniruk", "entity_type": "person"},
                                    {"name": "Cursor", "entity_type": "tool"},
                                ]
                            }
                        },
                    }
                )
                queue_people = get_review_queue(status="pending", limit=10)
                self.assertEqual(queue_people["count"], 1)
                approved_people = approve_review_queue_item(
                    queue_key=queue_people["items"][0]["queue_key"],
                    target="people",
                )
                self.assertTrue(approved_people["ok"])
                self.assertEqual(Path(approved_people["promoted_path"]).name, "andrew-kushniruk.md")
                people_content = Path(approved_people["promoted_path"]).read_text(encoding="utf-8")
                self.assertIn("#target/people", people_content)
                self.assertIn("[[memory/review/", people_content)

                persist_event(
                    {
                        "id": "evt-review-ref-1",
                        "timestamp": "2026-04-15T20:12:00+00:00",
                        "source": "manual",
                        "kind": "project_fact",
                        "text": "Documented tooling references.",
                        "project": "ai-memory-brain",
                        "cwd": "/tmp/project",
                        "importance": "high",
                        "tags": ["reference"],
                        "metadata": {
                            "knowledge": {
                                "entities": [
                                    {"name": "Andrew Kushniruk", "entity_type": "person"},
                                    {"name": "Graphiti MCP Docs", "entity_type": "document"},
                                    {"name": "GitHub", "entity_type": "tool"},
                                ]
                            }
                        },
                    }
                )
                queue_references = get_review_queue(status="pending", limit=10)
                self.assertEqual(queue_references["count"], 1)
                approved_references = approve_review_queue_item(
                    queue_key=queue_references["items"][0]["queue_key"],
                    target="references",
                )
                self.assertTrue(approved_references["ok"])
                self.assertEqual(Path(approved_references["promoted_path"]).name, "graphiti-mcp-docs.md")
                references_content = Path(approved_references["promoted_path"]).read_text(encoding="utf-8")
                self.assertIn("#target/references", references_content)
                self.assertIn("[[memory/review/", references_content)

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
                self.assertEqual(health["queue"]["approved"], 3)
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

    def test_store_structured_memory_and_session_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            old_helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            os.environ["MEMORY_HELPER_ENABLED"] = "0"
            try:
                result = store_structured_memory(
                    kind="task_summary",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    importance="high",
                    tags=["bootstrap"],
                    graph=True,
                    goal="Ship agent session bootstrap",
                    changes="Added session bootstrap and repo-aware retrieval",
                    decision="Keep JSONL canonical",
                    validation="unit tests passed",
                    next_step="exercise on the next live repo session",
                    risk="watch for noisy ranking",
                    repo_context={
                        "branch": "feature/bootstrap",
                        "commit_sha": "abc123",
                        "files_touched": ["memory_gateway/memory_store.py"],
                        "commands_run": ["python -m unittest memory_gateway/test_memory_store.py"],
                        "tests": ["memory_gateway/test_memory_store.py"],
                    },
                )
                self.assertTrue(result["ok"])
                session = start_session(project="ai-memory-brain", cwd="/tmp/project", query="bootstrap", file_paths=["memory_gateway/memory_store.py"])
                self.assertTrue(session["recent_events"])
                self.assertEqual(session["recent_events"][0]["metadata"]["repo_context"]["branch"], "feature/bootstrap")
                self.assertTrue(session["execution_hints"]["commands"])
                self.assertTrue(session["task_context"]["results"])
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home
                if old_helper_enabled is None:
                    os.environ.pop("MEMORY_HELPER_ENABLED", None)
                else:
                    os.environ["MEMORY_HELPER_ENABLED"] = old_helper_enabled

    def test_open_loop_lifecycle_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            try:
                created = store_structured_memory(
                    kind="open_loop",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    importance="high",
                    goal="Fix recall ranking drift",
                    next_step="compare ranking before/after token changes",
                    risk="could hide older important events",
                    title="Recall ranking drift",
                    status="open",
                    metadata={"loop_id": "loop-1", "title": "Recall ranking drift", "status": "open", "next_step": "compare ranking before/after token changes"},
                )
                self.assertTrue(created["ok"])
                updated = store_structured_memory(
                    kind="open_loop_update",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    importance="normal",
                    summary="resolved after tuning file overlap weights",
                    next_step="",
                    title="Recall ranking drift",
                    status="resolved",
                    metadata={"loop_id": "loop-1", "title": "Recall ranking drift", "status": "resolved"},
                )
                self.assertTrue(updated["ok"])
                all_loops = get_open_loops(project="ai-memory-brain", cwd="/tmp/project", limit=5)
                self.assertEqual(all_loops["items"][0]["status"], "resolved")
                report = get_memory_quality_report(project="ai-memory-brain", cwd="/tmp/project", limit=20)
                self.assertIn("issue_count", report)
                timeline = get_timeline(project="ai-memory-brain", cwd="/tmp/project", days=30, limit=10)
                self.assertTrue(timeline["days"])
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home

    def test_task_context_prefers_file_overlap_and_negative_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            try:
                store_structured_memory(
                    kind="failed_attempt",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    importance="high",
                    summary="initial fix regressed compact payload shape",
                    validation="tests failed locally",
                    risk="do not reuse the old compact serializer path",
                    repo_context={"files_touched": ["memory_librarian/handlers.py"]},
                )
                store_structured_memory(
                    kind="task_summary",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    importance="normal",
                    summary="updated handler flow for task context",
                    validation="tests passed",
                    repo_context={
                        "files_touched": ["memory_librarian/handlers.py"],
                        "commands_run": ["python -m unittest memory_librarian/test_mcp_server.py"],
                        "tests": ["memory_librarian/test_mcp_server.py"],
                    },
                )
                context = get_task_context(
                    "handler task context",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    file_paths=["memory_librarian/handlers.py"],
                    limit=5,
                )
                self.assertTrue(context["results"])
                self.assertTrue(context["negative_memories"])
                hints = get_execution_hints(project="ai-memory-brain", cwd="/tmp/project", limit=5)
                self.assertTrue(hints["commands"])
                self.assertTrue(hints["warnings"])
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home

    def test_structured_summary_auto_creates_open_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            try:
                result = store_structured_memory(
                    kind="task_summary",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    importance="high",
                    goal="Improve retrieval",
                    changes="Adjusted task ranking and scoring",
                    validation="unit tests passed",
                    next_step="Run against a live repo session",
                    risk="could still over-rank old memories",
                )
                self.assertTrue(result["ok"])
                self.assertTrue(result["auto_open_loop_created"])
                loops = get_open_loops(project="ai-memory-brain", cwd="/tmp/project", status="open", limit=5)
                self.assertEqual(len(loops["items"]), 1)
                self.assertIn("Run against a live repo session", loops["items"][0]["next_step"])
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home

    def test_failed_attempt_auto_creates_open_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            try:
                result = store_structured_memory(
                    kind="failed_attempt",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    importance="high",
                    goal="Run failing wrapper path",
                    changes="Wrapper exited non-zero",
                    validation="exit code 1 observed",
                    next_step="Inspect failure output before retrying",
                    risk="repeating blindly will waste time",
                )
                self.assertTrue(result["ok"])
                self.assertTrue(result["auto_open_loop_created"])
                loops = get_open_loops(project="ai-memory-brain", cwd="/tmp/project", status="open", limit=5)
                self.assertEqual(len(loops["items"]), 1)
                self.assertIn("Inspect failure output", loops["items"][0]["next_step"])
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home

    def test_task_context_reports_active_diff_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            try:
                store_structured_memory(
                    kind="task_summary",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    summary="worked on handlers task context path",
                    repo_context={"files_touched": ["memory_librarian/handlers.py"]},
                )
                with patch("memory_store._git_changed_files", return_value=["memory_librarian/handlers.py"]):
                    context = get_task_context(
                        "handlers",
                        project="ai-memory-brain",
                        cwd="/tmp/project",
                        limit=5,
                    )
                self.assertEqual(context["active_diff_files"], ["memory_librarian/handlers.py"])
                self.assertTrue(context["results"])
                self.assertGreater(context["results"][0]["retrieval"]["score_breakdown"]["active_diff_overlap"], 0.0)
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home

    def test_promote_canon_and_supersede_hides_old_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            try:
                first = store_structured_memory(
                    kind="task_summary",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    summary="Installed local wrappers and cursor hook",
                )
                second = store_structured_memory(
                    kind="task_summary",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    summary="Installed local wrappers and cursor hook with power-user profile",
                )
                promoted = promote_memory_to_canon(
                    event_id=second["event"]["id"],
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    title="Local wrapper setup",
                )
                self.assertTrue(promoted["ok"])
                superseded = mark_memory_superseded(
                    old_event_id=first["event"]["id"],
                    new_event_id=second["event"]["id"],
                    reason="newer rollout replaced older one",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                )
                self.assertTrue(superseded["ok"])
                recent = get_recent_events(limit=10, project="ai-memory-brain")
                recent_ids = {item["id"] for item in recent}
                self.assertNotIn(first["event"]["id"], recent_ids)
                machine = get_machine_context(project="ai-memory-brain", cwd="/tmp/project", limit=10)
                self.assertTrue(machine["canon"]["items"])
                self.assertTrue(any(item.get("canon") for item in machine["canon"]["items"]))
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home

    def test_cleanup_candidates_detect_similar_rollouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_home = os.environ.get("AI_MEMORY_BRAIN_HOME")
            os.environ["AI_MEMORY_BRAIN_HOME"] = tmp_dir
            try:
                store_structured_memory(
                    kind="task_summary",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    summary="Installed codex wrapper and cursor hook locally for agent memory rollout",
                    metadata={"force_store": True},
                )
                store_structured_memory(
                    kind="task_summary",
                    project="ai-memory-brain",
                    cwd="/tmp/project",
                    summary="Installed codex wrapper and cursor hook locally for agent memory rollout.",
                    metadata={"force_store": True},
                )
                candidates = get_cleanup_candidates(project="ai-memory-brain", cwd="/tmp/project", limit=10)
                self.assertGreaterEqual(candidates["count"], 1)
            finally:
                if old_home is None:
                    os.environ.pop("AI_MEMORY_BRAIN_HOME", None)
                else:
                    os.environ["AI_MEMORY_BRAIN_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
