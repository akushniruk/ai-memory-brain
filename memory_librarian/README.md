# AI Memory Brain Librarian (MCP)

MCP stdio server for global machine memory.

Detailed install/use/schema guide:

- `docs/memory-librarian-howto-reference.md`

Runtime defaults now resolve through the app-home storage model instead of repo-local `.run`.

Default macOS app home:

```bash
~/Library/Application\ Support/ai-memory-brain/
```

Tools:

- `memory_add`
- `memory_store_summary`
- `memory_store_structured`
- `memory_store_failed_attempt`
- `memory_open_loop_add`
- `memory_open_loop_update`
- `memory_open_loops`
- `memory_promote_canon`
- `memory_mark_superseded`
- `memory_search`
- `memory_recent`
- `memory_by_date` / `memory_get_date`
- `memory_project_context`
- `memory_start_session`
- `memory_task_context`
- `memory_project_canon`
- `memory_machine_context`
- `memory_execution_hints`
- `memory_timeline`
- `memory_quality_report`
- `memory_cleanup_candidates`
- `memory_entity_context`
- `memory_daily_summary`

## Run

```bash
cd /path/to/ai-memory-brain
source .venv-memory/bin/activate
python memory_librarian/server.py
```

The librarian reads runtime config from the app-home config location first and falls back to `memory_gateway/.env` for development compatibility.

## Cursor MCP config

Use `memory_gateway/install-cursor-global.sh` to auto-register globally.
