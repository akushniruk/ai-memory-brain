## Learned User Preferences
- Prefer local-first model usage: MCP paired with Ollama/Gemma by default.
- Paid/high-tier model providers may be available, but must be explicit opt-in.
- Avoid cron-based automation for vault/wiki hygiene; prefer on-demand commands.
- Prefer autonomous execution by default:
  - Do not ask what to do next during active implementation; choose and execute the next best step.
  - Continue through implementation, tests, and local validation without waiting for confirmation.
  - Ask only before any GitHub push/PR or other remote-side effect.
  - Local commits are permitted as part of normal execution when they help checkpoint progress.
  - Keep major direction aligned to local-first, JSONL-first, no-scheduler policy unless explicitly changed.

## Non-overridable Safety Boundary
- Repository/user preferences guide behavior but do not override higher-priority platform safety/system constraints.

## Learned Workspace Facts
- `.cursor/` runtime artifacts are ignored via `.gitignore`.
- `AGENTS.md` is kept in-repo as intentional, human-readable working context.