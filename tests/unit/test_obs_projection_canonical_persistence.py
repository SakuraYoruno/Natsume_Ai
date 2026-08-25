from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from host_brain.app import health_routes
from host_brain.app.observability_runtime.domain_state_projection import (
    load_life_evolution_projection_source,
)
from host_brain.composition.life import PROCESS_LIFE_PROVIDER, ProcessLifeContainer
from host_brain.app.obs_application import build_default_obs_application_ports
from host_brain.observability.degradation_state import _default_runtime_event_db_path


def test_degradation_state_canonical_persistence_precedes_split_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    split_events = tmp_path / "split-events.sqlite3"
    monkeypatch.setenv("NATSUME_AUDIT_DB", str(canonical))
    monkeypatch.setenv("HOST_RUNTIME_EVENT_DB_PATH", str(split_events))

    assert _default_runtime_event_db_path() == canonical


def test_degradation_state_retains_legacy_event_db_as_last_resort(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-events.sqlite3"
    monkeypatch.delenv("NATSUME_AUDIT_DB", raising=False)
    monkeypatch.delenv("Natsume_AUDIT_DB", raising=False)
    monkeypatch.setenv("HOST_RUNTIME_EVENT_DB_PATH", str(legacy))

    assert _default_runtime_event_db_path() == legacy


def test_mcp_health_endpoints_use_typed_canonical_persistence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    legacy = tmp_path / "legacy-mcp.sqlite3"
    opened_paths: list[str] = []
    closed_paths: list[str] = []

    class RegistryProbe:
        def __init__(
            self,
            db_path: str,
        ) -> None:
            self.db_path = db_path
            opened_paths.append(db_path)

        def list_all(self) -> list[object]:
            return [object()]

        def close(self) -> None:
            closed_paths.append(self.db_path)

    monkeypatch.setenv("NATSUME_AUDIT_DB", str(canonical))
    monkeypatch.setenv("HOST_MCP_REGISTRY_DB", str(legacy))
    monkeypatch.setattr(health_routes, "MCPToolRegistry", RegistryProbe)
    get_persistence_settings.cache_clear()
    try:
        route_response = asyncio.run(health_routes.mcp_health_check())

        assert opened_paths == [str(canonical)]
        assert closed_paths == opened_paths
        assert route_response.details["db_path"] == str(canonical)
    finally:
        get_persistence_settings.cache_clear()


def test_obs_lifespan_uses_injected_reader_without_touching_host_life(
    tmp_path: Path,
) -> None:
    from host_brain.app import obs_fastapi_app

    host_life = ProcessLifeContainer(
        repository=object(),  # type: ignore[arg-type]
        command_service=object(),  # type: ignore[arg-type]
        snapshot_reader=object(),  # type: ignore[arg-type]
    )
    injected_reader = lambda person_id: {
        "person_id": person_id,
        "version": 1,
        "updated_at_ms": 10,
        "state_digest": "sha256:life",
        "state": {},
    }
    base_ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "canonical.sqlite3",
    )
    ports = replace(
        base_ports,
        life_snapshot_reader=injected_reader,
        projection_jobs=(),
    )
    app = obs_fastapi_app.create_obs_app(ports=ports)

    try:
        previous = PROCESS_LIFE_PROVIDER.require()
    except RuntimeError:
        previous = None
    if previous is not None:
        PROCESS_LIFE_PROVIDER.clear(previous)
    PROCESS_LIFE_PROVIDER.install(host_life)

    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert PROCESS_LIFE_PROVIDER.require() is host_life
            assert app.state.life_snapshot_reader is injected_reader
            payload, cursor = load_life_evolution_projection_source(
                "default",
                snapshot_reader=app.state.life_snapshot_reader,
            )
            assert payload["person_id"] == "default"
            assert cursor == 10
        assert PROCESS_LIFE_PROVIDER.require() is host_life
    finally:
        PROCESS_LIFE_PROVIDER.clear(host_life)
        if previous is not None:
            PROCESS_LIFE_PROVIDER.install(previous)
