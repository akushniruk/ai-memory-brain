import json
import sys
import tempfile
import threading
import unittest
import warnings
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request as urllib_request

sys.path.insert(0, str(Path(__file__).parent))

# Suppress known neo4j asyncio deprecation noise in summarize endpoint tests.
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r".*iscoroutinefunction.*",
    module=r"neo4j\..*",
)


def _get_free_port() -> int:
    import socket
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


TRANSCRIPT_LINES = [
    {
        "role": "user",
        "message": {"content": [{"type": "text", "text": "Add /summarize endpoint"}]}
    },
    {
        "role": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Implementing the endpoint now."},
                {"type": "tool_use", "name": "Edit", "input": {}},
            ]
        }
    },
    {
        "role": "assistant",
        "message": {
            "content": [{"type": "text", "text": "Done. Endpoint returns rule-based summary when Ollama offline."}]
        }
    },
]


class SummarizeEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        from memory_server import MemoryHandler
        port = _get_free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), MemoryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{port}"
        self.tmp = tempfile.TemporaryDirectory()
        self.transcript_path = str(Path(self.tmp.name) / "t.jsonl")
        with open(self.transcript_path, "w") as f:
            for line in TRANSCRIPT_LINES:
                f.write(json.dumps(line) + "\n")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def test_summarize_returns_summary_key(self) -> None:
        result = self._post("/summarize", {
            "transcript_path": self.transcript_path,
            "project": "test-proj",
            "cwd": self.tmp.name,
        })
        self.assertIn("summary", result)
        self.assertIsInstance(result["summary"], str)
        self.assertGreater(len(result["summary"]), 10)

    def test_summarize_includes_used_llm_flag(self) -> None:
        result = self._post("/summarize", {
            "transcript_path": self.transcript_path,
            "project": "test-proj",
            "cwd": self.tmp.name,
        })
        self.assertIn("used_llm", result)
        self.assertIsInstance(result["used_llm"], bool)

    def test_summarize_missing_transcript_returns_fallback(self) -> None:
        result = self._post("/summarize", {
            "transcript_path": "/nonexistent/path.jsonl",
            "project": "test-proj",
            "cwd": self.tmp.name,
        })
        self.assertIn("summary", result)
        self.assertEqual(result["used_llm"], False)

    def test_summarize_rule_based_contains_goal(self) -> None:
        # Ollama not running in test env → rule-based path executes
        result = self._post("/summarize", {
            "transcript_path": self.transcript_path,
            "project": "test-proj",
            "cwd": self.tmp.name,
        })
        self.assertIn("summary", result)
        self.assertIn("Add /summarize endpoint", result["summary"])

    def test_summarize_unknown_path_returns_404(self) -> None:
        from urllib.error import HTTPError
        body = json.dumps({"transcript_path": "/x"}).encode()
        req = urllib_request.Request(
            f"{self.base_url}/unknown",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            urllib_request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 404)
        # Explicitly close HTTPError response object to avoid ResourceWarning noise.
        ctx.exception.close()


if __name__ == "__main__":
    unittest.main()
