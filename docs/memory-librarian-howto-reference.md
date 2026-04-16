# Memory Librarian: Install, Use, and Tool Schema

This guide explains what the Memory Librarian MCP server is, how to install it, how to use it, and what tool schemas it exposes.

## What it is

`memory_librarian/server.py` is a stdio MCP server for AI Memory Brain.

It serves tools for:

- writing memory events (`memory_add`, `memory_store_summary`, `memory_meeting_summary`)
- reading memory (`memory_recent`, `memory_search`, `memory_by_date`, `memory_project_context`)
- graph and health views (`memory_graph_overview`, `memory_today_graph`, `memory_brain_health`)
- vault and Postgres bridge operations (`memory_vault_status`, `memory_postgres_recent`, review queue tools)

The implementation is now split by responsibility:

- `memory_librarian/server.py`: entrypoint only
- `memory_librarian/rpc.py`: JSON-RPC protocol handling
- `memory_librarian/handlers.py`: tool behavior and validation
- `memory_librarian/tool_schemas.py`: tool metadata and input schemas
- `memory_librarian/gateway.py`: runtime bootstrap and `memory_store` integration

## Why this structure

- Keeps protocol plumbing separate from business logic.
- Makes tool behavior easier to test and modify.
- Reduces risk of regressions when adding new tools.
- Keeps schema definitions in one place for quick reference.

## Install

From the repo root:

```bash
python3 -m venv .venv-memory
source .venv-memory/bin/activate
pip install -r memory_librarian/requirements.txt
```

Optional local config:

```bash
cp memory_gateway/.env.example memory_gateway/.env
```

## Run the MCP server

```bash
source .venv-memory/bin/activate
python memory_librarian/server.py
```

## Connect from MCP clients

Example MCP config entry:

```json
{
  "mcpServers": {
    "ai-memory-brain": {
      "command": "python3",
      "args": ["/absolute/path/to/ai-memory-brain/memory_librarian/server.py"]
    }
  }
}
```

For Cursor global setup, you can use:

```bash
memory_gateway/install-cursor-global.sh
```

## How to use

Typical agent workflow:

1. Load context at task start:
  - `memory_project_context(project)`
2. Query details during task:
  - `memory_search(query, ...)`
  - `memory_by_date(date, ...)`
3. Persist outcome at task end:
  - `memory_store_summary(summary, project, tags, ...)`

Minimal write example (JSON-RPC):

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory_add","arguments":{"text":"Fixed MCP routing bug","kind":"fix","project":"ai-memory-brain","importance":"high"}}}
```

Minimal read example:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_recent","arguments":{"limit":10,"project":"ai-memory-brain"}}}
```

## Tool schema reference

The canonical schema is in `memory_librarian/tool_schemas.py` (`TOOLS` constant).

Common schema patterns:

- Most read tools support:
  - `format`: `full | compact`
  - `max_text_chars`: integer (for compact truncation)
- Common filters:
  - `project`, `source`, `kind`, `limit`
- Date-based tools use:
  - `date` with UTC `YYYY-MM-DD` prefix

Write tool required fields:

- `memory_add`: `text`
- `memory_store_summary`: `summary`
- `memory_meeting_summary`: `text`

Review queue tools:

- `memory_review_approve`: requires `queue_key`, `target`
- `memory_review_reject`: requires `queue_key`

Postgres read tools:

- `memory_postgres_status`
- `memory_postgres_recent`
- `memory_postgres_review_queue`
- `memory_postgres_bridge_writes`

Graph and health tools:

- `memory_graph_overview`
- `memory_graph_project_day`
- `memory_today_graph`
- `memory_brain_health`
- `memory_repair_graph`

## Validation behavior

Input validation is enforced in `memory_librarian/handlers.py`:

- `importance` must be one of: `low`, `normal`, `high`
- `tags` must be an array of strings
- invalid inputs return MCP tool errors instead of silent fallback

## Verify quickly

Run tests:

```bash
.venv-memory/bin/python -m unittest memory_librarian/test_mcp_server.py memory_gateway/test_memory_store.py memory_gateway/test_postgres_reads.py
```

Expected: all tests pass.