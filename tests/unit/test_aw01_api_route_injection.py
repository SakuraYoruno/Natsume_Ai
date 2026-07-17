from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from types import SimpleNamespace

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from host_brain.app import app_factory, router_registry
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports


_SECURE_ROUTE_EXPECTATIONS = (
    (
        "/v1/host/obs-commands/test-command",
        "OBS_COMMAND_INGRESS_PROVIDER_UNAVAILABLE",
    ),
    (
        "/v1/pentest/secure-run",
        "PENTEST_SECURE_RUN_PROVIDER_UNAVAILABLE",
    ),
    (
        "/v1/host/skill-provider/intake",
        "SKILL_PROVIDER_INTAKE_PROVIDER_UNAVAILABLE",
    ),
)


def test_router_registry_mounts_secure_routes_with_stable_unavailable_defaults() -> None:
    app = FastAPI()
    router_registry.register_host_routers(
        app,
        input_runtime_ports=build_input_runtime_application_ports(),
        route_ports=HostRoutePorts(),
    )

    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths.count("/v1/host/obs-commands/{command_id}") == 1
    assert paths.count("/v1/pentest/secure-run") == 1
    assert paths.count("/v1/host/skill-provider/intake") == 1

    with TestClient(app) as client:
        for path, reason_code in _SECURE_ROUTE_EXPECTATIONS:
            response = client.post(path, content=b"")
            assert response.status_code == 503
            assert response.json()["reason_code"] == reason_code
        internal = client.post(
            "/internal/codex/work-orders/preview",
            json={"work_order": {}},
        )
        scheduler = client.get("/scheduler/health")
        host_scheduler = client.get("/host-scheduler/health")
        assert internal.status_code == 503
        assert internal.json()["detail"] == "INTERNAL_AGENT_INGRESS_UNAVAILABLE"
        assert scheduler.status_code == 503
        assert scheduler.json()["detail"] == "SCHEDULER_COMMAND_INGRESS_UNAVAILABLE"
        assert host_scheduler.status_code == 503
        assert host_scheduler.json()["detail"] == "SCHEDULER_COMMAND_INGRESS_UNAVAILABLE"


def test_app_factory_forwards_explicit_secure_route_providers(
    monkeypatch,
) -> None:
    calls = {"obs": 0, "pentest": 0, "signed_intake": 0}

    def provider(name: str) -> Callable[[], None]:
        def resolve() -> None:
            calls[name] += 1
            return None

        return resolve

    monkeypatch.setattr(
        app_factory,
        "configure_http_middleware",
        lambda _app: None,
    )
    app = app_factory.create_host_fastapi_app(
        lifespan=None,
        metrics=RuntimeMetrics(),
        logger=logging.getLogger("test.aw01.route-injection"),
        get_ledger=lambda: None,
        get_tool_broker=lambda: None,
        get_tool_catalog=lambda: None,
        get_bond_engine=lambda: None,
        chat_endpoint=lambda _body, _request: Response(),
        edge_events_endpoint=lambda _body: None,
        root_endpoint=lambda: Response(),
        obs_command_ingress_provider=provider("obs"),
        pentest_secure_run_composition_provider=provider("pentest"),
        signed_skill_provider_intake_provider=provider("signed_intake"),
        obs_route_ports=ObsRoutePorts(
            query_projection_store_provider=lambda: SimpleNamespace(
                get=lambda *_args, **_kwargs: None,
            ),
        ),
        input_runtime_ports=build_input_runtime_application_ports(),
    )

    with TestClient(app) as client:
        for path, reason_code in _SECURE_ROUTE_EXPECTATIONS:
            response = client.post(path, content=b"")
            assert response.status_code == 503
            assert response.json()["reason_code"] == reason_code

    assert calls == {"obs": 1, "pentest": 1, "signed_intake": 1}


def test_scheduler_duplicate_reaches_single_governed_service() -> None:
    class _DuplicateIngress:
        def evaluate_http(self, **_kwargs):
            return SimpleNamespace(
                admissible=True,
                duplicate=True,
                reason_code="SECURE_INGRESS_IDEMPOTENCY_DUPLICATE",
                request_id="request.scheduler.original",
                existing_result_ref="scheduler-result://original",
            )

    class _Service:
        def health(self):
            return {"status": "OK", "runtime": {"active": True}}

    app = FastAPI()
    router_registry.register_host_routers(
        app,
        input_runtime_ports=build_input_runtime_application_ports(),
        route_ports=HostRoutePorts(),
        scheduler_command_ingress_provider=lambda: _DuplicateIngress(),
        scheduler_command_service_provider=lambda: _Service(),
    )

    with TestClient(app) as client:
        for path in ("/scheduler/health", "/host-scheduler/health"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.json()["runtime"]["active"] is True


def test_router_registry_has_no_platform_runtime_or_app_state_reverse_lookup() -> None:
    source = inspect.getsource(router_registry)

    assert "platform_security_runtime" not in source
    assert "app.state" not in source
