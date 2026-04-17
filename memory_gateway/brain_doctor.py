from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from urllib import error as urllib_error
from urllib import request as urllib_request

from memory_store import get_brain_health, get_postgres_status, get_vault_status, load_settings


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class DriftCheck:
    name: str
    ok: bool
    detail: str
    remediation: str


def _http_ok(url: str, timeout_sec: float = 2.0) -> tuple[bool, str]:
    req = urllib_request.Request(url, method="GET")
    try:
        with urllib_request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8")
            return True, body[:500]
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def _launchctl_loaded(label: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - local runtime dependent
        return False, str(exc)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    present = label in output
    return present, "loaded" if present else "missing"


def run_doctor() -> dict[str, object]:
    settings = load_settings()
    checks: list[CheckResult] = []

    postgres_status = get_postgres_status()
    checks.append(
        CheckResult(
            name="postgres",
            ok=bool(postgres_status.get("ok")),
            detail=str(postgres_status.get("reason", "") or "ok"),
        )
    )

    gateway_ok, gateway_detail = _http_ok("http://127.0.0.1:8765/health")
    checks.append(CheckResult(name="gateway_http", ok=gateway_ok, detail=gateway_detail))

    ollama_ok, ollama_detail = _http_ok("http://127.0.0.1:11434/api/tags")
    checks.append(CheckResult(name="ollama_http", ok=ollama_ok, detail=ollama_detail))

    launch_ok, launch_detail = _launchctl_loaded("com.ai-memory-brain.gateway")
    checks.append(CheckResult(name="gateway_launchctl", ok=launch_ok, detail=launch_detail))

    vault_status = get_vault_status()
    checks.append(
        CheckResult(
            name="vault_bridge",
            ok=bool(vault_status.get("ok")),
            detail=json.dumps(vault_status.get("queue", {}), ensure_ascii=True),
        )
    )

    brain_health = get_brain_health(limit=8)
    drift_checks: list[DriftCheck] = []
    profile = str(settings.get("profile", "simple"))
    postgres_enabled = bool(settings.get("postgres_enabled"))
    neo4j_enabled = bool(settings.get("neo4j_enabled"))
    helper_enabled = bool(settings.get("helper_enabled"))
    dedupe_threshold = float(settings.get("dedupe_similarity_threshold", 0.86))
    dedupe_window = int(settings.get("dedupe_window_minutes", 30))

    if profile in {"recommended", "power-user"}:
        drift_checks.append(
            DriftCheck(
                name="profile_postgres_alignment",
                ok=postgres_enabled,
                detail=f"profile={profile}, postgres_enabled={postgres_enabled}",
                remediation="Set POSTGRES_DSN and verify connectivity for recommended/power-user profile.",
            )
        )
    if profile == "power-user":
        drift_checks.append(
            DriftCheck(
                name="profile_neo4j_alignment",
                ok=neo4j_enabled,
                detail=f"profile={profile}, neo4j_enabled={neo4j_enabled}",
                remediation="Set NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD and verify local Neo4j auth.",
            )
        )
        drift_checks.append(
            DriftCheck(
                name="profile_helper_alignment",
                ok=helper_enabled,
                detail=f"profile={profile}, helper_enabled={helper_enabled}",
                remediation="Enable MEMORY_HELPER_ENABLED=1 with local Ollama/Gemma helper model.",
            )
        )
    drift_checks.append(
        DriftCheck(
            name="dedupe_threshold_range",
            ok=0.5 <= dedupe_threshold <= 0.99,
            detail=f"dedupe_similarity_threshold={dedupe_threshold}",
            remediation="Set MEMORY_DEDUPE_SIMILARITY_THRESHOLD within 0.5..0.99.",
        )
    )
    drift_checks.append(
        DriftCheck(
            name="dedupe_window_minimum",
            ok=dedupe_window >= 1,
            detail=f"dedupe_window_minutes={dedupe_window}",
            remediation="Set MEMORY_DEDUPE_WINDOW_MINUTES to at least 1.",
        )
    )

    dynamic_hints = []
    for drift in drift_checks:
        if not drift.ok:
            dynamic_hints.append(f"{drift.name}: {drift.remediation}")

    result = {
        "ok": all(item.ok for item in checks),
        "checks": [asdict(item) for item in checks],
        "drift_checks": [asdict(item) for item in drift_checks],
        "brain_health": brain_health,
        "profile": profile,
        "dedupe": {
            "window_minutes": dedupe_window,
            "similarity_threshold": dedupe_threshold,
        },
        "hints": [
            "If postgres fails: brew services start postgresql@16",
            "If gateway launchctl missing: memory_gateway/install-launch-agent.sh",
            "If gateway_http fails: source .venv-memory/bin/activate && memory_gateway/start-server.sh",
            "If ollama_http fails: brew services start ollama",
        ]
        + dynamic_hints,
    }
    return result


def main() -> int:
    payload = run_doctor()
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
