from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


APP_NAME = "ai-memory-brain"
DEFAULT_GROUP_ID = "personal-brain"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8765
DEFAULT_PROFILE = "simple"
DEFAULT_HELPER_BASE_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_HELPER_TIMEOUT_SEC = 15

_SUPPORTED_PROFILES = {"simple", "recommended", "power-user"}


def default_app_home() -> Path:
    explicit = os.environ.get("AI_MEMORY_BRAIN_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    if os.uname().sysname.lower() == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def _normalized_profile(value: str) -> str:
    profile = value.strip().lower() or DEFAULT_PROFILE
    return profile if profile in _SUPPORTED_PROFILES else DEFAULT_PROFILE


def resolve_runtime_layout() -> dict[str, Any]:
    app_home = default_app_home()
    memory_dir = Path(os.environ.get("AI_MEMORY_MEMORY_DIR", "")).expanduser() if os.environ.get("AI_MEMORY_MEMORY_DIR") else app_home / "memory"
    logs_dir = Path(os.environ.get("AI_MEMORY_LOGS_DIR", "")).expanduser() if os.environ.get("AI_MEMORY_LOGS_DIR") else memory_dir / "logs"
    config_dir = Path(os.environ.get("AI_MEMORY_CONFIG_DIR", "")).expanduser() if os.environ.get("AI_MEMORY_CONFIG_DIR") else app_home / "config"
    vault_path = Path(os.environ.get("VAULT_PATH", "")).expanduser() if os.environ.get("VAULT_PATH") else app_home / "vault"
    memory_log_path = Path(os.environ.get("MEMORY_LOG_PATH", "")).expanduser() if os.environ.get("MEMORY_LOG_PATH") else memory_dir / "events.jsonl"
    profile = _normalized_profile(os.environ.get("AI_MEMORY_INSTALL_PROFILE", DEFAULT_PROFILE))

    return {
        "app_home": app_home,
        "memory_dir": memory_dir,
        "logs_dir": logs_dir,
        "config_dir": config_dir,
        "vault_path": vault_path,
        "memory_log_path": memory_log_path,
        "env_file": config_dir / "memory.env",
        "profile": profile,
    }


def ensure_runtime_layout(layout: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = dict(layout or resolve_runtime_layout())
    for key in ("app_home", "memory_dir", "logs_dir", "config_dir", "vault_path"):
        Path(resolved[key]).mkdir(parents=True, exist_ok=True)

    _ensure_vault_scaffold(Path(resolved["vault_path"]))
    return resolved


def load_runtime_env(gateway_dir: Path) -> Path:
    layout = ensure_runtime_layout()
    env_file = Path(layout["env_file"])
    fallback = gateway_dir / ".env"
    target = env_file if env_file.exists() else fallback
    load_dotenv(target)
    return target


def _ensure_vault_scaffold(vault_path: Path) -> None:
    required_dirs = [
        vault_path / "memory" / "events",
        vault_path / "memory" / "checkins",
        vault_path / "memory" / "checkouts",
        vault_path / "memory" / "milestones",
        vault_path / "memory" / "review",
        vault_path / "daily-notes",
        vault_path / "meetings",
        vault_path / "projects",
        vault_path / "people",
        vault_path / "templates",
    ]
    for path in required_dirs:
        path.mkdir(parents=True, exist_ok=True)

    readme_path = vault_path / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
            "# AI Memory Brain Vault\n\n"
            "Curated vault paired with the JSONL memory ledger. "
            "Use this for human-readable knowledge, while the ledger remains the operational source.\n",
            encoding="utf-8",
        )


def load_runtime_settings() -> dict[str, Any]:
    layout = ensure_runtime_layout()
    postgres_dsn = os.environ.get("POSTGRES_DSN", "").strip()
    neo4j_uri = os.environ.get("NEO4J_URI", "").strip()
    neo4j_user = os.environ.get("NEO4J_USER", "").strip()
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "").strip()
    helper_model = os.environ.get("MEMORY_HELPER_MODEL", "").strip()
    helper_enabled = os.environ.get("MEMORY_HELPER_ENABLED", "0").lower() in ("1", "true", "yes", "on")

    return {
        "app_home": str(layout["app_home"]),
        "memory_dir": str(layout["memory_dir"]),
        "logs_dir": str(layout["logs_dir"]),
        "config_dir": str(layout["config_dir"]),
        "vault_path": str(layout["vault_path"]),
        "memory_log_path": str(layout["memory_log_path"]),
        "env_file": str(layout["env_file"]),
        "profile": layout["profile"],
        "server_host": os.environ.get("MEMORY_SERVER_HOST", DEFAULT_SERVER_HOST),
        "server_port": int(os.environ.get("MEMORY_SERVER_PORT", str(DEFAULT_SERVER_PORT))),
        "group_id": os.environ.get("MEMORY_GROUP_ID", DEFAULT_GROUP_ID),
        "postgres_dsn": postgres_dsn,
        "postgres_enabled": bool(postgres_dsn),
        "neo4j_uri": neo4j_uri,
        "neo4j_user": neo4j_user,
        "neo4j_password": neo4j_password,
        "neo4j_enabled": bool(neo4j_uri and neo4j_user and neo4j_password),
        "helper_enabled": helper_enabled,
        "helper_model": helper_model,
        "helper_base_url": os.environ.get("MEMORY_HELPER_BASE_URL", DEFAULT_HELPER_BASE_URL),
        "helper_timeout_sec": int(os.environ.get("MEMORY_HELPER_TIMEOUT_SEC", str(DEFAULT_HELPER_TIMEOUT_SEC))),
    }
