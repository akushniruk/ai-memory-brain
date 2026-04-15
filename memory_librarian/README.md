# AI Memory Brain Librarian (MCP)

MCP stdio server for global machine memory.

Runtime defaults now resolve through the app-home storage model instead of repo-local `.run`.

Default macOS app home:

```bash
~/Library/Application\ Support/ai-memory-brain/
```

Tools:
- `memory_add`
- `memory_store_summary`
- `memory_search`
- `memory_recent`
- `memory_by_date` / `memory_get_date`
- `memory_project_context`
- `memory_entity_context`
- `memory_daily_summary`

## Run
```bash
cd /Users/akushniruk/home_projects/ai-memory-brain
source .venv-memory/bin/activate
python memory_librarian/server.py
```

The librarian reads runtime config from the app-home config location first and falls back to `memory_gateway/.env` for development compatibility.

## Cursor MCP config
Use `memory_gateway/install-cursor-global.sh` to auto-register globally.
