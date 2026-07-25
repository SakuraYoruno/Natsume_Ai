from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from fastapi import Request, Response
from fastapi.testclient import TestClient

from host_brain.app.app_factory import create_host_fastapi_app
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.tools.catalog import ToolCatalog


def _catalog(tool_id: str) -> ToolCatalog:
    catalog = ToolCatalog()
    catalog.register({
        "id": tool_id,
        "name": tool_id,
        "desc": f"route-scope:{tool_id}",
        "schema_hash": "sha256:" + "a" * 64,
        "side_effect_level": "READ",
        "auth_level": "NONE",
        "domain_tags": ["route-scope"],
        "version": "1.0.0",
    })
    return catalog


def _host_app(tool_id: str) -> tuple[object, dict[str, int], HostRoutePorts]:
    catalog = _catalog(tool_id)
    calls = {"catalog": 0}

    def get_catalog() -> ToolCatalog:
        calls["catalog"] += 1
        return catalog

    broker = SimpleNamespace(
        readiness_snapshot=lambda: {
            "status": "FAIL_CLOSED_NO_GOVERNED_EXECUTOR",
            "catalog_ids": (tool_id,),
            "registered_ids": (tool_id,),
            "governed_executable_ids": (),
        }
    )

    route_ports = HostRoutePorts(
        tool_catalog_provider=get_catalog,
        tool_broker_provider=lambda: broker,
    )

    async def chat(_body: object, _request: Request) -> Response:
        return Response(status_code=204)

    async def edge(_body: object) -> object:
        return object()

    async def root() -> Response:
        return Response(status_code=204)

    app = create_host_fastapi_app(
        lifespan=None,
        metrics=RuntimeMetrics(),
        logger=__import__("logging").getLogger(f"test.route-scope.{tool_id}"),
        get_ledger=lambda: None,
        get_tool_broker=lambda: broker,
        get_tool_catalog=get_catalog,
        get_bond_engine=lambda: None,
        chat_endpoint=chat,
        edge_events_endpoint=edge,
        root_endpoint=root,
        route_ports=route_ports,
        obs_route_ports=ObsRoutePorts(
            query_projection_store_provider=lambda: type(
                "EmptyProjectionStore",
                (),
                {"get": lambda *_args, **_kwargs: None},
            )(),
        ),
        input_runtime_ports=build_input_runtime_application_ports(),
    )
    return app, calls, route_ports


def _assert_own_tool(client: TestClient, own: str, foreign: str) -> None:
    own_response = client.get(f"/tools/{own}")
    assert own_response.status_code == 200
    assert own_response.json()["id"] == own
    assert client.get(f"/tools/{foreign}").status_code == 404


def test_host_route_ports_are_immutable_and_scoped_per_app() -> None:
    app_a, calls_a, ports_a = _host_app("tool.app-a")
    app_b, calls_b, _ports_b = _host_app("tool.app-b")

    with pytest.raises(FrozenInstanceError):
        ports_a.tool_catalog_provider = lambda: None  # type: ignore[misc]

    # B remains live while A is constructed, interleaved, and then closed.
    with TestClient(app_b) as client_b:
        _assert_own_tool(client_b, "tool.app-b", "tool.app-a")
        with TestClient(app_a) as client_a:
            _assert_own_tool(client_a, "tool.app-a", "tool.app-b")
            _assert_own_tool(client_b, "tool.app-b", "tool.app-a")
            _assert_own_tool(client_a, "tool.app-a", "tool.app-b")

        _assert_own_tool(client_b, "tool.app-b", "tool.app-a")

        # Constructing and closing a third Host app does not replace B's ports.
        app_c, _calls_c, _ports_c = _host_app("tool.app-c")
        with TestClient(app_c) as client_c:
            _assert_own_tool(client_c, "tool.app-c", "tool.app-b")
        _assert_own_tool(client_b, "tool.app-b", "tool.app-c")

    assert calls_a["catalog"] > 0
    assert calls_b["catalog"] > calls_a["catalog"]


def test_process_and_tribe_composition_fence_one_live_host_mode_owner() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "host_brain" / "app"
    port_source = (root / "application_port_wiring.py").read_text(encoding="utf-8")
    tribe_source = (root / "tribe_composition.py").read_text(encoding="utf-8")
    asgi_source = (root / "asgi.py").read_text(encoding="utf-8")

    assert "mode_transition_service_provider" not in port_source
    assert "mode_transition_service_provider" in tribe_source
    assert "build_process_application_ports()" in asgi_source
    assert "build_tribe_application_ports(" in asgi_source
    assert "tribe_application_ports=tribe_application_ports" in asgi_source
    assert "tribe_runtime.bind_application_ports(" in (
        root / "http_hooks.py"
    ).read_text(encoding="utf-8")
    assert "host_lifecycle.get_mode_transition_service" in asgi_source
    assert "mode_transition_service_provider=mode_transition_service_provider" not in asgi_source
