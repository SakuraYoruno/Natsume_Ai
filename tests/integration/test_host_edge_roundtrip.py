from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge
from edge_client.tribe.peripheral_agent import PeripheralAgent as EdgePeripheralAgent
from edge_client.tribe.somatic_agent import SomaticAgent as EdgeSomaticAgent
from host_brain.app.internal_tribe_routes import router
from host_brain.tribe.event_bus import InMemoryTribeEventBus
from host_brain.tribe.peripheral_agent import PeripheralAgent as HostPeripheralAgent
from host_brain.tribe.registry import HostTribeRegistry
from host_brain.tribe.runtime_context import (
    TribeAgentCatalog,
    TribeRuntimeContext,
    activate_tribe_runtime_context,
    deactivate_tribe_runtime_context,
)
from host_brain.tribe.somatic_agent import SomaticAgent as HostSomaticAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _ResponseAdapter:
    def __init__(self, response) -> None:  # noqa: ANN001
        self.status_code = int(response.status_code)
        self.text = str(response.text)
        self.content = bytes(response.content)
        self._response = response

    def json(self) -> dict:
        return dict(self._response.json())


class _LoopbackSession:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def post(self, url, json, headers, timeout):  # noqa: ANN001
        _ = timeout
        path = urlsplit(str(url)).path or "/"
        response = self.client.post(path, json=json, headers=headers)
        return _ResponseAdapter(response)


def _build_host_runtime_context(
    *,
    bus: InMemoryTribeEventBus,
    state_registry: TribeStateRegistry,
) -> tuple[object, TribeRuntimeContext]:
    owner = object()
    return owner, TribeRuntimeContext(
        state_registry=state_registry,
        tribe_registry=HostTribeRegistry(),
        event_bus=bus,
        agent_catalog=TribeAgentCatalog(
            (("test_placeholder_agent", object),)
        ),
        active_owner_provider=lambda: owner,
    )


def _build_host_client(
    bus: InMemoryTribeEventBus,
    *,
    state_registry: TribeStateRegistry,
) -> TestClient:
    owner, context = _build_host_runtime_context(
        bus=bus,
        state_registry=state_registry,
    )
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def bind_test_tribe_context(request: Request, call_next):
        activation = activate_tribe_runtime_context(
            owner=owner,
            context=context,
        )
        try:
            return await call_next(request)
        finally:
            deactivate_tribe_runtime_context(activation)

    return TestClient(app)


def _dispatch_host_state_events(
    *,
    bus: InMemoryTribeEventBus,
    host_somatic: HostSomaticAgent,
    host_peripheral: HostPeripheralAgent,
) -> None:
    handlers = {
        "SOMATIC_STATE_UPDATED": host_somatic._on_somatic_state_updated,
        "PLATFORM_CONNECTED": host_peripheral._on_platform_connected,
        "PERIPHERAL_STATE_UPDATED": host_peripheral._on_peripheral_state_updated,
        "GATE_STATE_CHANGED": host_peripheral._on_gate_state_changed,
        "PLATFORM_MUTE_CHANGED": host_peripheral._on_platform_mute_changed,
    }
    events = list(reversed(bus.recent(limit=64)))
    for event in events:
        handler = handlers.get(str(event.event_type or "").strip().upper())
        if handler is not None:
            asyncio.run(handler(event))


def test_edge_to_host_state_backflow_updates_host_snapshots(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INTERNAL_TRIBE_TOKEN", "tribe-token")

    bus = InMemoryTribeEventBus(max_events=128)
    state_registry = TribeStateRegistry()
    host_somatic = HostSomaticAgent(state_registry=state_registry)
    host_peripheral = HostPeripheralAgent(state_registry=state_registry)
    owner, context = _build_host_runtime_context(
        bus=bus,
        state_registry=state_registry,
    )
    activation = activate_tribe_runtime_context(
        owner=owner,
        context=context,
    )
    try:
        host_somatic.register_subscriptions()
        host_peripheral.register_subscriptions()
    finally:
        deactivate_tribe_runtime_context(activation)

    with _build_host_client(
        bus,
        state_registry=state_registry,
    ) as host_client:
        bridge = EdgeTribeBridge(
            host_base_url="http://host.local",
            token="tribe-token",
            enabled=True,
            session=_LoopbackSession(host_client),  # type: ignore[arg-type]
        )
        edge_somatic = EdgeSomaticAgent(bridge=bridge)
        edge_peripheral = EdgePeripheralAgent(bridge=bridge)

        edge_somatic.publish_somatic_state(
            vts_connected=True,
            current_expression="smile",
            last_emotion_tag="JOY",
            last_intensity=0.66,
            error=None,
        )
        edge_peripheral.publish_platform_connected(platform="wechat", detail="adapter-online")
        edge_peripheral.on_gate_open_command({"platform": "wechat", "reason": "roundtrip_test"})
        edge_peripheral.on_platform_mute_command({"platform": "wechat", "mute": True})
        edge_peripheral.publish_peripheral_state(
            connected_platforms=["wechat"],
            gate_state={"wechat": "open"},
            mute_state={"wechat": True},
            error=None,
        )

    activation = activate_tribe_runtime_context(
        owner=owner,
        context=context,
    )
    try:
        _dispatch_host_state_events(
            bus=bus,
            host_somatic=host_somatic,
            host_peripheral=host_peripheral,
        )
    finally:
        deactivate_tribe_runtime_context(activation)

    somatic_state = host_somatic.export_runtime_state()
    peripheral_state = host_peripheral.export_runtime_state()

    assert somatic_state["vts_connected"] is True
    assert somatic_state["current_expression"] == "smile"
    assert somatic_state["last_emotion_tag"] == "JOY"
    assert somatic_state["last_intensity"] == 0.66

    assert "wechat" in peripheral_state["connected_platforms"]
    assert peripheral_state["gate_state"]["wechat"] == "open"
    assert peripheral_state["mute_state"]["wechat"] is True
    assert peripheral_state["last_platform_event"] == "connected"
