from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app import health_routes, obs_routes_task_runtime, system_handler
from host_brain.app.observability_runtime.domain_state_projection import (
    load_life_evolution_projection_source,
)
from host_brain.app.settings.persistence import get_persistence_settings
from host_brain.composition.life import PROCESS_LIFE_PROVIDER, ProcessLifeContainer
from host_brain.observability.degradation_state import _default_runtime_event_db_path


def test_task_projection_uses_typed_canonical_persistence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    monkeypatch.setenv("Natsume_AUDIT_DB", str(canonical))
    monkeypatch.setenv("HOST_TASK_RUNTIME_DB_PATH", str(tmp_path / "split-task.sqlite3"))
    monkeypatch.setenv("HOST_AGENT_REVIEW_DB_PATH", str(tmp_path / "split-review.sqlite3"))
    monkeypatch.setenv("HOST_RUNTIME_EVENT_DB_PATH", str(tmp_path / "split-events.sqlite3"))
    get_persistence_settings.cache_clear()
    try:
        assert obs_routes_task_runtime._canonical_db_path() == canonical
    finally:
        get_persistence_settings.cache_clear()


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
            *,
            check_same_thread: bool,
        ) -> None:
            assert check_same_thread is False
            self.db_path = db_path
            opened_paths.append(db_path)

        def list_all(self) -> list[object]:
            return [object()]

        def close(self) -> None:
            closed_paths.append(self.db_path)

    monkeypatch.setenv("NATSUME_AUDIT_DB", str(canonical))
    monkeypatch.setenv("HOST_MCP_REGISTRY_DB", str(legacy))
    monkeypatch.setattr(health_routes, "MCPToolRegistry", RegistryProbe)
    monkeypatch.setattr(system_handler, "MCPToolRegistry", RegistryProbe)
    get_persistence_settings.cache_clear()
    try:
        route_response = asyncio.run(health_routes.mcp_health_check())
        handler_response = system_handler.mcp_health_check_response()

        assert opened_paths == [str(canonical), str(canonical)]
        assert closed_paths == opened_paths
        assert route_response.details["db_path"] == str(canonical)
        assert handler_response.details["db_path"] == str(canonical)
    finally:
        get_persistence_settings.cache_clear()


def test_obs_lifespan_owns_the_composed_life_reader(monkeypatch, tmp_path: Path) -> None:
    from host_brain.app import obs_fastapi_app

    class Reader:
        def get_snapshot(self, person_id: str) -> dict[str, object]:
            return {
                "person_id": person_id,
                "version": 1,
                "updated_at_ms": 10,
                "state_digest": "sha256:life",
                "state": {},
            }

    life = ProcessLifeContainer(
        repository=object(),  # type: ignore[arg-type]
        command_service=object(),  # type: ignore[arg-type]
        snapshot_reader=Reader(),  # type: ignore[arg-type]
    )
    ledger = SimpleNamespace(canonical_db_path=str(tmp_path / "canonical.sqlite3"))
    monkeypatch.setattr(obs_fastapi_app, "_get_ledger_dep", lambda: ledger)
    monkeypatch.setattr(
        obs_fastapi_app,
        "get_persistence_settings",
        lambda: SimpleNamespace(world_canon_root=tmp_path / "world"),
    )
    monkeypatch.setattr(obs_fastapi_app, "build_process_canon_container", lambda **_kwargs: object())
    monkeypatch.setattr(obs_fastapi_app, "build_process_life_container", lambda **_kwargs: life)
    monkeypatch.setattr(obs_fastapi_app, "register_projection_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(obs_fastapi_app, "start_projection_jobs", lambda **_kwargs: {"started": True})
    monkeypatch.setattr(obs_fastapi_app, "stop_projection_jobs", lambda: {"stopped": True})

    try:
        previous = PROCESS_LIFE_PROVIDER.require()
    except RuntimeError:
        previous = None
    if previous is not None:
        PROCESS_LIFE_PROVIDER.clear(previous)

    async def exercise() -> None:
        app = SimpleNamespace(state=SimpleNamespace())
        async with obs_fastapi_app.lifespan(app):
            assert PROCESS_LIFE_PROVIDER.require() is life
            payload, cursor = load_life_evolution_projection_source("default")
            assert payload["person_id"] == "default"
            assert cursor == 10
        with pytest.raises(RuntimeError, match="PROCESS_LIFE_AUTHORITY_NOT_COMPOSED"):
            PROCESS_LIFE_PROVIDER.require()

    try:
        asyncio.run(exercise())
    finally:
        PROCESS_LIFE_PROVIDER.clear(life)
        if previous is not None:
            PROCESS_LIFE_PROVIDER.install(previous)
