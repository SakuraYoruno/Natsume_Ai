from __future__ import annotations

import logging
from types import SimpleNamespace

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from host_brain.app import app_factory, health_routes
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.core.resource_manager import SystemResourceManager


def _guardian_state(scope_id: str) -> dict[str, object]:
    return {
        "status": "DEGRADED",
        "durability_required": True,
        "durability_status": "READY",
        "durability_authority": "RUNTIME_GUARDIAN_JOURNAL",
        "guardian_authority": {
            "authority": "RUNTIME_GUARDIAN",
            "runtime_scope_id": scope_id,
            "fencing_token": 7,
        },
    }


def _patch_health_report_dependencies(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        health_routes,
        "load_host_config",
        lambda: SimpleNamespace(success=False, config=None),
    )
    monkeypatch.setattr(health_routes, "get_ledger", lambda: None)
    monkeypatch.setattr(
        health_routes,
        "build_service_health_report",
        lambda **_kwargs: {
            "status": "OK",
            "summary": {
                "required_unavailable": 0,
                "optional_unavailable": 0,
            },
        },
    )


def test_production_health_router_reads_only_its_explicit_guardian_provider(
    monkeypatch,
) -> None:
    _patch_health_report_dependencies(monkeypatch)
    monkeypatch.setattr(
        "host_brain.observability.degradation_state.get_degradation_state",
        lambda: (_ for _ in ()).throw(AssertionError("legacy reader used")),
    )
    reads: list[str] = []

    def read_guardian() -> dict[str, object]:
        reads.append("read")
        return _guardian_state("runtime-scope-health-a")

    app = FastAPI()
    app.include_router(
        health_routes.create_health_router(
            degradation_state_provider=read_guardian,
        )
    )

    with TestClient(app) as client:
        health = client.get("/health?skip_dependency_probes=true")
        degradation = client.get("/health/degradation")

    assert health.status_code == 200
    assert health.json()["details"]["degradation_state"]["guardian_authority"] == {
        "authority": "RUNTIME_GUARDIAN",
        "runtime_scope_id": "runtime-scope-health-a",
        "fencing_token": 7,
    }
    assert degradation.status_code == 200
    assert degradation.json()["guardian_authority"]["runtime_scope_id"] == (
        "runtime-scope-health-a"
    )
    assert reads == ["read", "read"]


def test_production_health_router_fails_closed_without_guardian_provider(
    monkeypatch,
) -> None:
    _patch_health_report_dependencies(monkeypatch)
    app = FastAPI()
    app.include_router(health_routes.create_health_router())

    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health?skip_dependency_probes=true")
        degradation = client.get("/health/degradation")

    assert health.status_code == 503
    assert health.json()["detail"] == (
        "RUNTIME_GUARDIAN_DEGRADATION_PROVIDER_UNAVAILABLE"
    )
    assert degradation.status_code == 503
    assert degradation.json()["detail"] == (
        "RUNTIME_GUARDIAN_DEGRADATION_PROVIDER_UNAVAILABLE"
    )


def test_production_health_router_does_not_fallback_when_guardian_read_fails(
    monkeypatch,
) -> None:
    _patch_health_report_dependencies(monkeypatch)
    legacy_reads: list[str] = []
    monkeypatch.setattr(
        "host_brain.observability.degradation_state.get_degradation_state",
        lambda: legacy_reads.append("legacy") or _guardian_state("legacy"),
    )

    def failed_guardian_read() -> dict[str, object]:
        raise RuntimeError("guardian lease expired")

    app = FastAPI()
    app.include_router(
        health_routes.create_health_router(
            degradation_state_provider=failed_guardian_read,
        )
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health?skip_dependency_probes=true")
        degradation = client.get("/health/degradation")

    assert health.status_code == 503
    assert degradation.status_code == 503
    assert degradation.json()["detail"] == (
        "RUNTIME_GUARDIAN_DEGRADATION_READ_FAILED"
    )
    assert legacy_reads == []


def test_app_factory_forwards_app_local_degradation_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}
    provider = lambda: _guardian_state("runtime-scope-factory")  # noqa: E731

    def register(_app, **kwargs) -> None:  # noqa: ANN001
        captured.update(kwargs)

    monkeypatch.setattr(app_factory, "register_host_routers", register)
    monkeypatch.setattr(app_factory, "configure_http_middleware", lambda _app: None)

    app_factory.create_host_fastapi_app(
        lifespan=None,
        metrics=RuntimeMetrics(),
        logger=logging.getLogger("test.ne03.health-provider"),
        get_ledger=lambda: None,
        get_tool_broker=lambda: None,
        get_tool_catalog=lambda: None,
        get_bond_engine=lambda: None,
        chat_endpoint=lambda _body, _request: Response(),
        edge_events_endpoint=lambda _body: None,
        root_endpoint=lambda: Response(),
        degradation_state_provider=provider,
        obs_route_ports=ObsRoutePorts(
            query_projection_store_provider=lambda: SimpleNamespace(
                get=lambda *_args, **_kwargs: None,
            ),
        ),
        input_runtime_ports=build_input_runtime_application_ports(),
    )

    assert captured["degradation_state_provider"] is provider


def test_two_production_health_routers_keep_guardian_authority_app_local(
    monkeypatch,
) -> None:
    _patch_health_report_dependencies(monkeypatch)
    app_a = FastAPI()
    app_a.include_router(
        health_routes.create_health_router(
            degradation_state_provider=lambda: _guardian_state("scope-a"),
        )
    )
    app_b = FastAPI()
    app_b.include_router(
        health_routes.create_health_router(
            degradation_state_provider=lambda: _guardian_state("scope-b"),
        )
    )

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        assert client_a.get("/health/degradation").json()["guardian_authority"][
            "runtime_scope_id"
        ] == "scope-a"
        assert client_b.get("/health/degradation").json()["guardian_authority"][
            "runtime_scope_id"
        ] == "scope-b"
        assert client_a.get("/health/degradation").json()["guardian_authority"][
            "runtime_scope_id"
        ] == "scope-a"


def test_resource_manager_without_guardian_provider_fails_closed(monkeypatch) -> None:
    legacy_reads: list[str] = []
    monkeypatch.setattr(
        "host_brain.observability.degradation_state.get_degradation_state",
        lambda: legacy_reads.append("legacy") or _guardian_state("legacy"),
    )

    manager = SystemResourceManager(
        interval_s=999,
        llm_enabled=False,
        auto_gc_enabled=False,
    )

    state = manager._read_degradation_state()  # noqa: SLF001

    assert state["status"] == "UNAVAILABLE"
    assert state["durability_status"] == "FAILED"
    assert legacy_reads == []
