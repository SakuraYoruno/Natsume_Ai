"""Regression checks for the external-service port contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _env_value(name: str) -> str | None:
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return None


def test_compose_consumes_the_ports_its_startup_script_exports() -> None:
    compose = (ROOT / "scripts/runtime/docker-compose.external.yml").read_text(
        encoding="utf-8"
    )
    assert '"${NATSUME_NEO4J_BOLT_PORT:-17687}:7687"' in compose
    assert '"${NATSUME_CHROMA_PORT:-18000}:8000"' in compose
    assert '"${NATSUME_REDIS_PORT:-6379}:6379"' in compose


def test_clean_checkout_defaults_match_compose_host_ports() -> None:
    assert _env_value("BOND_NEO4J_URI") == "bolt://127.0.0.1:17687"
    assert _env_value("CHROMA_ENDPOINT") == "http://127.0.0.1:18000"
    assert _env_value("CHROMA_PORT") == "18000"

    docker_start = (ROOT / "scripts/runtime/start_docker_services.ps1").read_text(
        encoding="utf-8"
    )
    host_start = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(
        encoding="utf-8"
    )
    external_check = (ROOT / "scripts/runtime/check_external_env.py").read_text(
        encoding="utf-8"
    )
    for source in (docker_start, host_start, external_check):
        assert "bolt://127.0.0.1:17687" in source
