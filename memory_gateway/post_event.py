import argparse
import json
import os
from pathlib import Path
import uuid
from urllib import request

from runtime_layout import load_runtime_env


load_runtime_env(Path(__file__).resolve().parent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post an event to the local memory gateway.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--text", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--importance", default="normal", choices=["low", "normal", "high"])
    parser.add_argument("--tags", default="")
    parser.add_argument("--graph", action="store_true")
    parser.add_argument("--branch", default="")
    parser.add_argument("--commit-sha", default="")
    parser.add_argument("--files-touched", default="")
    parser.add_argument("--commands-run", default="")
    parser.add_argument("--tests", default="")
    parser.add_argument("--artifacts", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--goal", default="")
    parser.add_argument("--changes", default="")
    parser.add_argument("--decision", default="")
    parser.add_argument("--why", default="")
    parser.add_argument("--validation", default="")
    parser.add_argument("--next-step", default="")
    parser.add_argument("--risk", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--loop-id", default="")
    parser.add_argument("--metadata-json", default="")
    args = parser.parse_args()

    host = os.environ.get("MEMORY_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("MEMORY_SERVER_PORT", "8765")

    def split_csv(text: str) -> list[str]:
        return [item.strip() for item in text.split(",") if item.strip()]

    metadata = {}
    if args.metadata_json:
        metadata = json.loads(args.metadata_json)

    repo_context = {
        "branch": args.branch,
        "commit_sha": args.commit_sha,
        "files_touched": split_csv(args.files_touched),
        "commands_run": split_csv(args.commands_run),
        "tests": split_csv(args.tests),
        "artifacts": split_csv(args.artifacts),
    }
    repo_context = {key: value for key, value in repo_context.items() if value not in ("", [], {}, None)}
    if args.branch:
        metadata["branch"] = args.branch
    if repo_context:
        metadata["repo_context"] = repo_context

    structured = {
        "title": args.title,
        "goal": args.goal,
        "changes": args.changes,
        "decision": args.decision,
        "why": args.why,
        "validation": args.validation,
        "next_step": args.next_step,
        "risk": args.risk,
        "status": args.status,
    }
    structured = {key: value for key, value in structured.items() if value}
    if structured:
        metadata["structured"] = structured

    if args.kind == "open_loop":
        metadata["loop_id"] = args.loop_id or str(uuid.uuid4())
        if args.title:
            metadata["title"] = args.title
        metadata["status"] = args.status or "open"
        if args.next_step:
            metadata["next_step"] = args.next_step
        if args.risk:
            metadata["risk"] = args.risk

    text = args.text
    if not text:
        parts = []
        if args.goal:
            parts.append(f"Goal: {args.goal}")
        if args.changes:
            parts.append(f"Changes: {args.changes}")
        if args.decision:
            parts.append(f"Decisions: {args.decision}")
        if args.why:
            parts.append(f"Why: {args.why}")
        if args.validation:
            parts.append(f"Validation: {args.validation}")
        if args.next_step:
            parts.append(f"Next Step: {args.next_step}")
        if args.risk:
            parts.append(f"Risks/TODO: {args.risk}")
        if args.title and not parts:
            parts.append(args.title)
        text = "\n".join(parts)
    if not text:
        raise SystemExit("--text or structured fields are required")

    payload = {
        "source": args.source,
        "kind": args.kind,
        "text": text,
        "project": args.project,
        "cwd": args.cwd,
        "importance": args.importance,
        "tags": [tag for tag in args.tags.split(",") if tag],
        "graph": args.graph,
        "metadata": metadata,
    }

    req = request.Request(
        f"http://{host}:{port}/event",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(req, timeout=10) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
