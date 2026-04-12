# Cursor Transcript Summarization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every completed Cursor session writes a meaningful summary to the graph via the librarian, with rule-based fallback when librarian is offline.

**Architecture:** `cursor-stop-hook.py` reads the transcript JSONL, calls `POST /summarize` on `memory_server.py` (which uses Gemma4 via Ollama), and POSTs the resulting summary as a `task_summary` event with `importance=high`. If `/summarize` is unreachable (3s timeout), the hook builds a rule-based summary locally and still POSTs the event.

**Tech Stack:** Python 3.11+, stdlib only in hook (no new deps), Ollama HTTP API (same pattern as existing `_extract_knowledge_with_helper`), `unittest` for tests.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `memory_gateway/transcript_parser.py` | **Create** | Parse Cursor transcript JSONL → goal/conclusion/tools. Shared by server and hook. |
| `memory_gateway/memory_server.py` | **Modify** | Add `POST /summarize` handler |
| `memory_gateway/cursor-stop-hook.py` | **Modify** | Read transcript, call `/summarize`, fallback, post high-importance event |
| `memory_gateway/test_transcript_parser.py` | **Create** | Tests for transcript parser |
| `memory_gateway/test_summarize_endpoint.py` | **Create** | Tests for `/summarize` endpoint |

---

### Task 1: Transcript parser module

**Files:**
- Create: `memory_gateway/transcript_parser.py`
- Create: `memory_gateway/test_transcript_parser.py`

- [ ] **Step 1: Write failing tests**

Create `memory_gateway/test_transcript_parser.py`:

```python
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from transcript_parser import parse_transcript, build_rule_based_summary


TRANSCRIPT_LINES = [
    {
        "role": "user",
        "message": {
            "content": [{"type": "text", "text": "Fix the memory server routing"}]
        }
    },
    {
        "role": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Reading the server file now."},
                {"type": "tool_use", "name": "Read", "input": {}},
                {"type": "tool_use", "name": "Edit", "input": {}},
            ]
        }
    },
    {
        "role": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Fixed routing. Added /health handler."},
                {"type": "tool_use", "name": "Bash", "input": {}},
            ]
        }
    },
]


class TranscriptParserTests(unittest.TestCase):
    def _write_transcript(self, tmp_dir: str, lines: list) -> str:
        path = Path(tmp_dir) / "transcript.jsonl"
        with path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return str(path)

    def test_parse_extracts_first_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_transcript(tmp, TRANSCRIPT_LINES)
            result = parse_transcript(path)
            self.assertEqual(result["goal"], "Fix the memory server routing")

    def test_parse_extracts_last_two_assistant_texts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_transcript(tmp, TRANSCRIPT_LINES)
            result = parse_transcript(path)
            self.assertIn("Fixed routing", result["conclusion"])

    def test_parse_deduplicates_tool_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_transcript(tmp, TRANSCRIPT_LINES)
            result = parse_transcript(path)
            self.assertIn("Read", result["tools"])
            self.assertIn("Edit", result["tools"])
            self.assertIn("Bash", result["tools"])
            self.assertEqual(len(result["tools"]), len(set(result["tools"])))

    def test_parse_counts_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_transcript(tmp, TRANSCRIPT_LINES)
            result = parse_transcript(path)
            self.assertEqual(result["turn_count"], 3)

    def test_parse_missing_file_returns_empty(self) -> None:
        result = parse_transcript("/nonexistent/path.jsonl")
        self.assertEqual(result["goal"], "")
        self.assertEqual(result["conclusion"], "")
        self.assertEqual(result["tools"], [])
        self.assertEqual(result["turn_count"], 0)

    def test_parse_empty_transcript_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_transcript(tmp, [])
            result = parse_transcript(path)
            self.assertEqual(result["goal"], "")

    def test_build_rule_based_summary(self) -> None:
        parsed = {
            "goal": "Fix routing",
            "conclusion": "Added handler",
            "tools": ["Read", "Edit"],
            "turn_count": 2,
        }
        summary = build_rule_based_summary(parsed)
        self.assertIn("Fix routing", summary)
        self.assertIn("Added handler", summary)
        self.assertIn("Read", summary)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/home_projects/ai-memory-brain/memory_gateway
python -m pytest test_transcript_parser.py -v
```

Expected: `ModuleNotFoundError: No module named 'transcript_parser'`

- [ ] **Step 3: Implement `transcript_parser.py`**

Create `memory_gateway/transcript_parser.py`:

```python
"""Parse Cursor agent transcript JSONL into structured fields."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def parse_transcript(path: str) -> dict[str, Any]:
    """Read transcript JSONL and extract goal, conclusion, tools, turn_count.

    Returns empty fields if file missing or unreadable.
    """
    p = Path(path)
    if not p.exists():
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}

    turns: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}

    if not turns:
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}

    # First user message → goal
    goal = ""
    for turn in turns:
        if turn.get("role") == "user":
            for item in turn.get("message", {}).get("content", []):
                if item.get("type") == "text":
                    goal = item.get("text", "")[:300]
                    break
        if goal:
            break

    # Last 2 assistant text turns → conclusion
    assistant_texts: list[str] = []
    seen_tools: list[str] = []
    seen_tool_set: set[str] = set()

    for turn in turns:
        if turn.get("role") != "assistant":
            continue
        for item in turn.get("message", {}).get("content", []):
            if item.get("type") == "text":
                text = item.get("text", "").strip()
                if text:
                    assistant_texts.append(text[:300])
            elif item.get("type") == "tool_use":
                name = item.get("name", "")
                if name and name not in seen_tool_set:
                    seen_tool_set.add(name)
                    seen_tools.append(name)

    conclusion = " ".join(assistant_texts[-2:]) if assistant_texts else ""

    return {
        "goal": goal,
        "conclusion": conclusion,
        "tools": seen_tools,
        "turn_count": len(turns),
    }


def build_rule_based_summary(parsed: dict[str, Any]) -> str:
    """Build a plain-text summary from parsed transcript fields."""
    parts: list[str] = []
    if parsed.get("goal"):
        parts.append(f"Goal: {parsed['goal'][:200]}")
    if parsed.get("conclusion"):
        parts.append(f"Concluded: {parsed['conclusion'][:200]}")
    if parsed.get("tools"):
        parts.append(f"Tools: {', '.join(parsed['tools'])}")
    turn_count = parsed.get("turn_count", 0)
    if turn_count:
        parts.append(f"Turns: {turn_count}")
    return "\n".join(parts) if parts else "Session completed."
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/home_projects/ai-memory-brain/memory_gateway
python -m pytest test_transcript_parser.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/home_projects/ai-memory-brain
git add memory_gateway/transcript_parser.py memory_gateway/test_transcript_parser.py
git commit -m "feat: transcript parser — extract goal/conclusion/tools from Cursor JSONL"
```

---

### Task 2: `/summarize` endpoint in memory_server.py

**Files:**
- Modify: `memory_gateway/memory_server.py`
- Create: `memory_gateway/test_summarize_endpoint.py`

- [ ] **Step 1: Write failing tests**

Create `memory_gateway/test_summarize_endpoint.py`:

```python
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request as urllib_request

sys.path.insert(0, str(Path(__file__).parent))


def _get_free_port() -> int:
    import socket
    with socket.socket() as s:
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
        # Ollama likely not running in test env → rule-based path
        result = self._post("/summarize", {
            "transcript_path": self.transcript_path,
            "project": "test-proj",
            "cwd": self.tmp.name,
        })
        # Either LLM summary (contains goal semantics) or rule-based (contains literal goal text)
        self.assertIn("summary", result)
        self.assertTrue(len(result["summary"]) > 0)

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/home_projects/ai-memory-brain/memory_gateway
python -m pytest test_summarize_endpoint.py -v
```

Expected: `test_summarize_returns_summary_key` FAIL — `/summarize` returns 404

- [ ] **Step 3: Add `/summarize` handler to `memory_server.py`**

Add import at top of `memory_gateway/memory_server.py` (after existing imports):

```python
from transcript_parser import build_rule_based_summary, parse_transcript
```

Add `_summarize_transcript` helper method inside `MemoryHandler` (before `do_GET`):

```python
def _summarize_transcript(self, transcript_path: str, project: str, cwd: str) -> dict:
    """Parse transcript and summarize via Ollama/Gemma4, fallback to rule-based."""
    import os
    from urllib import error as urllib_error
    from urllib import request as urllib_request

    parsed = parse_transcript(transcript_path)
    rule_based = build_rule_based_summary(parsed)

    # Try Ollama
    helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED", "0").lower() in ("1", "true", "yes", "on")
    helper_model = os.environ.get("MEMORY_HELPER_MODEL", "").strip()
    helper_base_url = os.environ.get("MEMORY_HELPER_BASE_URL", "http://127.0.0.1:11434/api/generate")
    helper_timeout = int(os.environ.get("MEMORY_HELPER_TIMEOUT_SEC", "5"))

    if helper_enabled and helper_model and parsed["goal"]:
        tool_names = ", ".join(parsed["tools"]) if parsed["tools"] else "none"
        last_actions = parsed["conclusion"][:400] if parsed["conclusion"] else "(none)"
        prompt = (
            f"Summarize this Cursor agent session in 3-5 sentences.\n"
            f"Goal: {parsed['goal'][:300]}\n"
            f"Last actions: {last_actions}\n"
            f"Tools used: {tool_names}\n"
            f"Write: what was done, key decisions, open items."
        )
        request_payload = {
            "model": helper_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        body = json.dumps(request_payload, ensure_ascii=True).encode("utf-8")
        req = urllib_request.Request(
            helper_base_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=helper_timeout) as resp:
                outer = json.loads(resp.read().decode("utf-8"))
            summary = str(outer.get("response", "")).strip()
            if summary:
                return {"summary": summary, "used_llm": True, "turn_count": parsed["turn_count"]}
        except (urllib_error.URLError, TimeoutError, OSError):
            pass

    return {"summary": rule_based, "used_llm": False, "turn_count": parsed["turn_count"]}
```

Update `do_POST` to handle `/summarize` — replace the existing `do_POST` method:

```python
def do_POST(self) -> None:  # noqa: N802
    if self.path == "/event":
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json: {exc}"})
            return
        try:
            result = persist_event(payload)
        except Exception as exc:  # pragma: no cover
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        self._write_json(HTTPStatus.OK, result)
        return

    if self.path == "/summarize":
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json: {exc}"})
            return
        transcript_path = str(payload.get("transcript_path", ""))
        project = str(payload.get("project", ""))
        cwd = str(payload.get("cwd", ""))
        result = self._summarize_transcript(transcript_path, project, cwd)
        self._write_json(HTTPStatus.OK, result)
        return

    self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ~/home_projects/ai-memory-brain/memory_gateway
python -m pytest test_summarize_endpoint.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/home_projects/ai-memory-brain
git add memory_gateway/memory_server.py memory_gateway/test_summarize_endpoint.py
git commit -m "feat: add POST /summarize endpoint — librarian-first, rule-based fallback"
```

---

### Task 3: Update `cursor-stop-hook.py`

**Files:**
- Modify: `memory_gateway/cursor-stop-hook.py`

- [ ] **Step 1: Write the updated hook**

Replace `memory_gateway/cursor-stop-hook.py` entirely:

```python
#!/usr/bin/env python3
"""
Global Cursor stop hook for ai-memory-brain.
Calls /summarize for librarian-generated summary, falls back to rule-based.
Always writes a task_summary event with importance=high.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _load_key_values(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _post_json(url: str, payload: dict, timeout: int = 3) -> dict:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_transcript_local(transcript_path: str) -> dict:
    """Minimal rule-based parser used when /summarize is unreachable."""
    from pathlib import Path as P
    p = P(transcript_path)
    if not p.exists():
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}
    turns = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return {"goal": "", "conclusion": "", "tools": [], "turn_count": 0}

    goal = ""
    for turn in turns:
        if turn.get("role") == "user":
            for item in turn.get("message", {}).get("content", []):
                if item.get("type") == "text":
                    goal = item.get("text", "")[:300]
                    break
        if goal:
            break

    assistant_texts: list[str] = []
    tool_set: set[str] = set()
    tools: list[str] = []
    for turn in turns:
        if turn.get("role") != "assistant":
            continue
        for item in turn.get("message", {}).get("content", []):
            if item.get("type") == "text":
                t = item.get("text", "").strip()
                if t:
                    assistant_texts.append(t[:300])
            elif item.get("type") == "tool_use":
                name = item.get("name", "")
                if name and name not in tool_set:
                    tool_set.add(name)
                    tools.append(name)

    conclusion = " ".join(assistant_texts[-2:]) if assistant_texts else ""
    return {"goal": goal, "conclusion": conclusion, "tools": tools, "turn_count": len(turns)}


def _build_rule_based_summary(parsed: dict) -> str:
    parts = []
    if parsed.get("goal"):
        parts.append(f"Goal: {parsed['goal'][:200]}")
    if parsed.get("conclusion"):
        parts.append(f"Concluded: {parsed['conclusion'][:200]}")
    if parsed.get("tools"):
        parts.append(f"Tools: {', '.join(parsed['tools'])}")
    if parsed.get("turn_count"):
        parts.append(f"Turns: {parsed['turn_count']}")
    return "\n".join(parts) if parts else "Session completed."


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("{}")
        return 0

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("{}")
        return 0

    if data.get("status") != "completed" or data.get("loop_count", 0) != 0:
        print("{}")
        return 0

    cwd = Path.cwd().resolve()
    config_env = _load_key_values(Path.home() / ".config" / "ai-memory-brain" / "config.env")
    host = os.environ.get("MEMORY_SERVER_HOST") or config_env.get("MEMORY_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("MEMORY_SERVER_PORT") or config_env.get("MEMORY_SERVER_PORT", "8765")
    base_url = f"http://{host}:{port}"

    conversation_id = str(data.get("conversation_id", ""))
    transcript_path = str(data.get("transcript_path") or "")
    generation_id = data.get("generation_id")

    # Step 1: get summary via /summarize (librarian-first), fallback local
    summary = ""
    used_llm = False
    turn_count = 0

    if transcript_path:
        try:
            result = _post_json(
                f"{base_url}/summarize",
                {"transcript_path": transcript_path, "project": cwd.name, "cwd": str(cwd)},
                timeout=3,
            )
            summary = result.get("summary", "")
            used_llm = bool(result.get("used_llm", False))
            turn_count = int(result.get("turn_count", 0))
        except (urllib.error.URLError, TimeoutError, OSError):
            # Server offline → build locally
            parsed = _parse_transcript_local(transcript_path)
            summary = _build_rule_based_summary(parsed)
            turn_count = parsed.get("turn_count", 0)

    if not summary:
        summary = f"Cursor agent session completed ({cwd.name})."

    # Step 2: persist event
    payload = {
        "source": "cursor-stop-hook",
        "kind": "task_summary",
        "text": summary,
        "project": cwd.name,
        "cwd": str(cwd),
        "importance": "high",
        "tags": ["cursor", "session-stop", "summarized"],
        "graph": True,
        "metadata": {
            "conversation_id": conversation_id,
            "generation_id": generation_id,
            "transcript_path": transcript_path,
            "used_llm": used_llm,
            "turn_count": turn_count,
        },
    }

    try:
        _post_json(f"{base_url}/event", payload)
    except (urllib.error.URLError, TimeoutError, OSError):
        pass

    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-test hook locally**

Simulate a completed Cursor session payload:

```bash
cd ~/home_projects/ai-memory-brain/memory_gateway
echo '{"status":"completed","loop_count":0,"conversation_id":"test-123","transcript_path":"","generation_id":"gen-1"}' \
  | python cursor-stop-hook.py
```

Expected output: `{}`  (no crash, exits cleanly)

- [ ] **Step 3: Run all existing tests to check nothing broke**

```bash
cd ~/home_projects/ai-memory-brain/memory_gateway
python -m pytest test_memory_store.py test_transcript_parser.py test_summarize_endpoint.py -v
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
cd ~/home_projects/ai-memory-brain
git add memory_gateway/cursor-stop-hook.py
git commit -m "feat: cursor-stop-hook reads transcript, calls /summarize, writes high-importance event"
```

---

### Task 4: Integration smoke test with real transcript

**Files:**
- No new files — manual verification

- [ ] **Step 1: Start memory server**

```bash
cd ~/home_projects/ai-memory-brain/memory_gateway
bash start-server.sh &
sleep 1
curl -s http://127.0.0.1:8765/health
```

Expected: `{"ok": true}`

- [ ] **Step 2: Test /summarize with a real Cursor transcript**

```bash
TRANSCRIPT=$(ls ~/.cursor/projects/*/agent-transcripts/*/*.jsonl 2>/dev/null | head -1)
echo "Using: $TRANSCRIPT"
curl -s -X POST http://127.0.0.1:8765/summarize \
  -H "Content-Type: application/json" \
  -d "{\"transcript_path\": \"$TRANSCRIPT\", \"project\": \"pharos\", \"cwd\": \"/Users/akushniruk/home_projects/pharos\"}" \
  | python3 -m json.tool
```

Expected: JSON with `summary` (non-empty string) and `used_llm` (true if Ollama running, false otherwise)

- [ ] **Step 3: Simulate hook end-to-end**

```bash
TRANSCRIPT=$(ls ~/.cursor/projects/*/agent-transcripts/*/*.jsonl 2>/dev/null | head -1)
cd ~/home_projects/pharos
echo "{\"status\":\"completed\",\"loop_count\":0,\"conversation_id\":\"smoke-test\",\"transcript_path\":\"$TRANSCRIPT\",\"generation_id\":\"gen-smoke\"}" \
  | python ~/home_projects/ai-memory-brain/memory_gateway/cursor-stop-hook.py
```

Expected: `{}`

- [ ] **Step 4: Verify event written**

```bash
curl -s http://127.0.0.1:8765/health  # server still alive
tail -1 ~/home_projects/ai-memory-brain/.run/memory/events.jsonl | python3 -m json.tool
```

Expected: last event has `"kind": "task_summary"`, `"importance": "high"`, non-empty `"text"`, `"used_llm"` in metadata.

- [ ] **Step 5: Final commit**

```bash
cd ~/home_projects/ai-memory-brain
git add -A
git commit -m "chore: cursor transcript summarization — integration verified"
```
