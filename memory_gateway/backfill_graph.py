#!/usr/bin/env python3
import argparse
from pathlib import Path

from dotenv import load_dotenv

from memory_store import load_settings, repair_graph


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent / ".env")

    parser = argparse.ArgumentParser(description="Backfill Neo4j graph from JSONL memory log.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of events to backfill")
    args = parser.parse_args()

    settings = load_settings()
    if not settings["neo4j_uri"] or not settings["neo4j_user"] or not settings["neo4j_password"]:
        raise SystemExit("Neo4j is not configured in memory_gateway/.env")

    result = repair_graph(limit=args.limit, missing_only=False)
    if not result.get("ok"):
        raise SystemExit(result.get("error", "Graph repair failed"))

    print(f"Backfilled {result['written']} events into graph.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
