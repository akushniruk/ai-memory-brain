# AI Memory Brain vNext Implementation Plan

## Summary
Implement vNext in phases that preserve the existing JSONL-first hot path while introducing the app-home layout, vault scaffold, and profile-aware runtime configuration.

## Task groups
### 1. Runtime layout and config
- Add centralized app-home path resolution
- Replace repo-local `.run` defaults
- Add install profile, vault path, and structured-layer config surfaces
- Keep repo-local env as fallback only

### 2. JSONL-first preservation
- Keep JSONL as first-write capture path
- Ensure richer layers are downstream only
- Confirm failures in Postgres / Neo4j / Gemma do not break capture

### 3. Vault scaffold and bridge foundations
- Create vault scaffold under app home
- Add configuration surfaces for bridge behavior
- Document auto-write vs review-first defaults

### 4. Install profile UX
- Simple: MCP + JSONL + vault
- Recommended: add Postgres
- Power User: add Neo4j + Ollama/Gemma
- Provide both CLI and copy-paste agent setup guidance

### 5. Documentation and verification
- Update top-level README to app-home/profile model
- Update gateway and librarian READMEs
- Add targeted tests for app-home defaults and vault scaffold creation

## Verification
- Run focused unit tests for runtime/config changes
- Verify docs and examples no longer point at repo-local `.run`
- Confirm app-home and vault defaults are reflected consistently across scripts and docs
