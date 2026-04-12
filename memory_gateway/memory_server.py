import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dotenv import load_dotenv

from memory_store import persist_event
from transcript_parser import build_rule_based_summary, parse_transcript


load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


class MemoryHandler(BaseHTTPRequestHandler):
    server_version = "PharosMemoryGateway/0.1"

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _summarize_transcript(self, transcript_path: str, project: str, cwd: str) -> dict:
        """Parse transcript and summarize via Ollama/Gemma4, fallback to rule-based."""
        import os
        from urllib import error as urllib_error
        from urllib import request as urllib_request

        parsed = parse_transcript(transcript_path)
        rule_based = build_rule_based_summary(parsed)

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

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"ok": True})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

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

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    host = os.environ.get("MEMORY_SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("MEMORY_SERVER_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), MemoryHandler)
    print(f"Memory gateway listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
