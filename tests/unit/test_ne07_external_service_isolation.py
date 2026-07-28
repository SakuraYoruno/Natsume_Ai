"""NE-07 external-service isolation and reproducibility contracts."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "scripts/runtime/docker-compose.external.yml"
START_DOCKER = ROOT / "scripts/runtime/start_docker_services.ps1"
START_HOST = ROOT / "scripts/runtime/start_host_side.ps1"


def test_external_images_are_digest_pinned_and_password_has_no_repo_default() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "chromadb/chroma:latest" not in compose
    assert "redis:7-alpine" not in compose
    assert "NatsuNeo4j!" not in compose
    assert compose.count("@sha256:") == 3
    assert 'NATSUME_NEO4J_PASSWORD:-}"' in compose


def test_compose_can_isolate_names_network_and_data_root() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    for variable in (
        "NATSUME_NEO4J_CONTAINER_NAME",
        "NATSUME_CHROMA_CONTAINER_NAME",
        "NATSUME_REDIS_CONTAINER_NAME",
        "NATSUME_DOCKER_NETWORK_NAME",
        "NATSUME_DOCKER_DATA_ROOT",
    ):
        assert variable in compose
    assert compose.count("healthcheck:") == 3
    assert compose.count("natsume-external") >= 4


def test_docker_start_exports_acceptance_scope_without_secret_echo() -> None:
    source = START_DOCKER.read_text(encoding="utf-8")

    for parameter in (
        "[string]$DataRoot",
        "[string]$Neo4jContainerName",
        "[string]$ChromaContainerName",
        "[string]$RedisContainerName",
        "[string]$NetworkName",
    ):
        assert parameter in source
    assert '$env:NATSUME_DOCKER_DATA_ROOT = $DataRoot' in source
    assert "docker compose config failed" in source
    assert '"RUNTIME_INJECTED_REDACTED"' in source
    assert "auth_value" not in source.lower()


def test_process_environment_overrides_checked_in_env_for_acceptance() -> None:
    docker_source = START_DOCKER.read_text(encoding="utf-8")
    host_source = START_HOST.read_text(encoding="utf-8")

    assert (
        '$neo4jUri = if ($env:BOND_NEO4J_URI)'
        in docker_source
    )
    assert (
        '$chromaEndpoint = if ($env:CHROMA_ENDPOINT)'
        in docker_source
    )
    assert (
        '$redisUrl = if ($env:HOST_RATE_LIMIT_REDIS_URL)'
        in docker_source
    )
    process_lookup = host_source.index(
        '[Environment]::GetEnvironmentVariable($Name, "Process")'
    )
    file_lookup = host_source.index(
        "if ($Values -and $Values.ContainsKey($Name))",
        process_lookup,
    )
    assert process_lookup < file_lookup
