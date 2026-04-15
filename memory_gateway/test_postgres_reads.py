import unittest
from datetime import datetime, timezone

import postgres_reads


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.last_query = ""
        self.last_params = ()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def execute(self, query, params):
        self.last_query = query
        self.last_params = params

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._cursor = _FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def cursor(self):
        return self._cursor


class _FakePsycopg:
    def __init__(self, rows):
        self.rows = rows

    def connect(self, *_args, **_kwargs):
        return _FakeConn(self.rows)


class PostgresReadsTests(unittest.TestCase):
    def test_recent_returns_disabled_when_dsn_missing(self):
        result = postgres_reads.list_recent_events(dsn="")
        self.assertTrue(result["ok"])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "disabled")

    def test_recent_returns_driver_unavailable(self):
        old = postgres_reads.psycopg
        postgres_reads.psycopg = None
        try:
            result = postgres_reads.list_recent_events(dsn="postgresql://localhost/db")
            self.assertFalse(result["ok"])
            self.assertTrue(result["degraded"])
            self.assertEqual(result["reason"], "driver_unavailable")
        finally:
            postgres_reads.psycopg = old

    def test_recent_parses_rows(self):
        row_time = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        fake_rows = [
            (
                "evt-1",
                row_time,
                "agent",
                "task_summary",
                "ai-memory-brain",
                "/tmp/proj",
                "high",
                "Shipped postgres reads.",
                '["memory","postgres"]',
                '{"branch":"feature/postgres"}',
            )
        ]
        old = postgres_reads.psycopg
        postgres_reads.psycopg = _FakePsycopg(fake_rows)
        try:
            result = postgres_reads.list_recent_events(
                dsn="postgresql://localhost/db",
                limit=10,
                project="ai-memory-brain",
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["results"][0]["id"], "evt-1")
            self.assertEqual(result["results"][0]["storage"], "postgres")
            self.assertEqual(result["results"][0]["tags"], ["memory", "postgres"])
        finally:
            postgres_reads.psycopg = old

    def test_review_queue_and_bridge_reads(self):
        queue_rows = [
            (
                "review:evt-1",
                "evt-1",
                "knowledge_review",
                "/vault/review.md",
                '{"candidate_targets":["projects"]}',
                "pending",
                datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
            )
        ]
        bridge_rows = [
            (
                "evt-1",
                "meeting_summary",
                "/vault/meetings/meeting.md",
                "upsert",
                datetime(2026, 4, 15, 12, 30, tzinfo=timezone.utc),
            )
        ]
        old = postgres_reads.psycopg
        try:
            postgres_reads.psycopg = _FakePsycopg(queue_rows)
            queue = postgres_reads.list_review_queue(dsn="postgresql://localhost/db", status="pending", limit=5)
            self.assertTrue(queue["ok"])
            self.assertEqual(queue["count"], 1)
            self.assertEqual(queue["items"][0]["status"], "pending")
            self.assertEqual(queue["items"][0]["payload"]["candidate_targets"], ["projects"])

            postgres_reads.psycopg = _FakePsycopg(bridge_rows)
            bridge = postgres_reads.list_bridge_writes(dsn="postgresql://localhost/db", event_id="evt-1", limit=5)
            self.assertTrue(bridge["ok"])
            self.assertEqual(bridge["count"], 1)
            self.assertEqual(bridge["items"][0]["event_id"], "evt-1")
        finally:
            postgres_reads.psycopg = old


if __name__ == "__main__":
    unittest.main()
