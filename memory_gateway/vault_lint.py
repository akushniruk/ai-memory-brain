#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from runtime_layout import load_runtime_settings


EVENT_MARKER_RE = re.compile(r"<!--\s*ai-memory-event:([^\s>]+)\s*-->")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _iter_markdown_files(vault_path: Path) -> list[Path]:
    if not vault_path.exists():
        return []
    return sorted(path for path in vault_path.rglob("*.md") if path.is_file())


def _resolve_wikilink(vault_path: Path, target: str) -> Path:
    target_clean = target.split("#", 1)[0].strip()
    if not target_clean:
        return Path("")
    return (vault_path / target_clean).with_suffix(".md")


def lint_vault(vault_path: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    marker_locations: dict[str, list[str]] = {}

    md_files = _iter_markdown_files(vault_path)
    promoted_files = [path for path in md_files if path.parent.name in {"projects", "people", "references"}]

    for path in md_files:
        content = path.read_text(encoding="utf-8")

        for marker in EVENT_MARKER_RE.findall(content):
            marker_locations.setdefault(marker, []).append(str(path))

        for raw_link in WIKILINK_RE.findall(content):
            target_path = _resolve_wikilink(vault_path, raw_link)
            if not target_path:
                continue
            if not target_path.exists():
                issues.append(
                    {
                        "kind": "broken_wikilink",
                        "path": str(path),
                        "detail": raw_link,
                    }
                )

    for marker, locations in marker_locations.items():
        if len(locations) > 1:
            issues.append(
                {
                    "kind": "duplicate_event_marker",
                    "path": ", ".join(sorted(locations)),
                    "detail": marker,
                }
            )

    for path in promoted_files:
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            issues.append(
                {
                    "kind": "missing_frontmatter",
                    "path": str(path),
                    "detail": "promoted file has no YAML frontmatter",
                }
            )

    return {
        "ok": True,
        "vault_path": str(vault_path),
        "files_scanned": len(md_files),
        "issues_count": len(issues),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run non-scheduled lint checks on vault markdown files.")
    parser.add_argument("--vault-path", default="", help="Optional override. Defaults to runtime VAULT_PATH.")
    args = parser.parse_args()

    settings = load_runtime_settings()
    vault_path = Path(args.vault_path or settings["vault_path"]).expanduser()
    result = lint_vault(vault_path)
    print(result)
    return 0 if result["issues_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
