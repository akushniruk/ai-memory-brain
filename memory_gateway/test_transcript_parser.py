import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from transcript_parser import build_rule_based_summary, build_structured_session_memory, parse_transcript


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
        self.assertEqual(result["open_items"], [])

    def test_parse_empty_transcript_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_transcript(tmp, [])
            result = parse_transcript(path)
            self.assertEqual(result["goal"], "")

    def test_parse_conclusion_uses_only_last_two_assistant_texts(self) -> None:
        three_assistant_lines = [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "Do the thing"}]}
            },
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "First action"}]}
            },
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "Second action"}]}
            },
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "Third action"}]}
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_transcript(tmp, three_assistant_lines)
            result = parse_transcript(path)
            self.assertNotIn("First action", result["conclusion"])
            self.assertIn("Second action", result["conclusion"])
            self.assertIn("Third action", result["conclusion"])

    def test_build_rule_based_summary(self) -> None:
        parsed = {
            "goal": "Fix routing",
            "conclusion": "Added handler",
            "tools": ["Read", "Edit"],
            "turn_count": 2,
            "open_items": ["Check deploy logs"],
        }
        summary = build_rule_based_summary(parsed)
        self.assertIn("Fix routing", summary)
        self.assertIn("Added handler", summary)
        self.assertIn("Read", summary)
        self.assertIn("Validation:", summary)
        self.assertIn("Risks/TODO:", summary)

    def test_build_structured_session_memory(self) -> None:
        parsed = {
            "goal": "Fix routing",
            "conclusion": "Added handler",
            "tools": ["Read", "Edit"],
            "turn_count": 2,
            "open_items": ["Check deploy logs"],
        }
        structured = build_structured_session_memory(parsed)
        self.assertEqual(structured["goal"], "Fix routing")
        self.assertIn("Added handler", structured["changes"])
        self.assertIn("Read", structured["decision"])
        self.assertEqual(structured["next_step"], "Check deploy logs")


if __name__ == "__main__":
    unittest.main()
