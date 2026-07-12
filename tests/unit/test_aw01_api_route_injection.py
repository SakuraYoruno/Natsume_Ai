from __future__ import annotations

import inspect
import logging
from collections.abc import Callable

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from host_brain.app import app_factory, router_registry
from host_brain.app.runtime_metrics import RuntimeMetrics


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
    router_registry.register_host_routers(app)

    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths.count("/v1/host/obs-commands/{command_id}") == 1
    assert paths.count("/v1/pentest/secure-run") == 1
    assert paths.count("/v1/host/skill-provider/intake") == 1

    with TestClient(app) as client:
        for path, reason_code in _SECURE_ROUTE_EXPECTATIONS:
            response = client.post(path, content=b"")
            assert response.status_code == 503
            assert response.json()["reason_code"] == reason_code


def test_app_factory_forwards_explicit_secure_route_providers(
    monkeypatch,
) -> None:
    calls = {"obs": 0, "pentest": 0, "signed_intake": 0}

    def provider(name: str) -> Callable[[], None]:
        def resolve() -> None:
            calls[name] += 1
            return None

        return resolve

    monkeypatch.setattr(app_factory, "wire_route_providers", lambda **_kwargs: None)
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
    )

    with TestClient(app) as client:
        for path, reason_code in _SECURE_ROUTE_EXPECTATIONS:
            response = client.post(path, content=b"")
            assert response.status_code == 503
            assert response.json()["reason_code"] == reason_code

    assert calls == {"obs": 1, "pentest": 1, "signed_intake": 1}


def test_router_registry_has_no_platform_runtime_or_app_state_reverse_lookup() -> None:
    source = inspect.getsource(router_registry)

    assert "platform_security_runtime" not in source
    assert "app.state" not in source
