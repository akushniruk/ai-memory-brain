import argparse
import json
import os
from urllib import request

from dotenv import load_dotenv


load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Post an event to the local memory gateway.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--project", default="")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--importance", default="normal", choices=["low", "normal", "high"])
    parser.add_argument("--tags", default="")
    parser.add_argument("--graph", action="store_true")
    args = parser.parse_args()

    host = os.environ.get("MEMORY_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("MEMORY_SERVER_PORT", "8765")

    payload = {
        "source": args.source,
        "kind": args.kind,
        "text": args.text,
        "project": args.project,
        "cwd": args.cwd,
        "importance": args.importance,
        "tags": [tag for tag in args.tags.split(",") if tag],
        "graph": args.graph,
        "metadata": {},
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
