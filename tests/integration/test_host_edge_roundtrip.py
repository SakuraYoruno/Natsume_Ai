from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import EdgeConfig
from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge
from edge_client.tribe.peripheral_agent import PeripheralAgent as EdgePeripheralAgent
from edge_client.tribe.registry import get_edge_tribe_registry
from edge_client.tribe.somatic_agent import SomaticAgent as EdgeSomaticAgent
from host_brain.app.internal_tribe_routes import router, set_tribe_event_bus
from host_brain.tribe.edge_command_pusher import EdgeCommandPusher
from host_brain.tribe.event_bus import InMemoryTribeEventBus
from host_brain.tribe.peripheral_agent import PeripheralAgent as HostPeripheralAgent
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


class _FakeVtsDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def set_expression(self, emotion_tag: str, intensity: float | None = None) -> SimpleNamespace:
        self.calls.append({"emotion_tag": emotion_tag, "intensity": intensity})
        return SimpleNamespace(success=True)


def _build_edge_app() -> FastAPI:
    return create_edge_fastapi_app(
        config=EdgeConfig(
            env="test",
            rpc_host="127.0.0.1",
            rpc_port=17800,
            hmac_shared_key=None,
            hmac_shared_key_ref=None,
            key_profile_status=None,
            allow_insecure_auth=True,
            rpc_auth_window_ms=60000,
            rpc_nonce_window_ms=60000,
            rpc_gateway_enabled=False,
            asr_enabled=False,
            tts_enabled=False,
            barge_in_enabled=False,
            fallback_llm_enabled=False,
            resource_gate_enabled=False,
            max_vram_mb=1024,
            max_queue_length=32,
        ),
        runtime=SimpleNamespace(),
        startup_hook=lambda _runtime: None,
        shutdown_hook=lambda _runtime: None,
        health_collector=lambda _runtime: {},
    )


def _build_host_client(bus: InMemoryTribeEventBus) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    set_tribe_event_bus(bus)
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


def test_host_to_edge_command_roundtrip_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INTERNAL_TRIBE_TOKEN", "tribe-token")

    app = _build_edge_app()
    registry = get_edge_tribe_registry()
    registry.reinitialize(bridge=EdgeTribeBridge(host_base_url="", enabled=False))
    registry.channel.on_gate_control_command(gate_open=True, reason="reset")
    fake_vts = _FakeVtsDriver()
    registry.somatic.set_vts_driver(fake_vts)

    with TestClient(app) as edge_client:
        pusher = EdgeCommandPusher(
            edge_base_url="http://edge.local",
            token="tribe-token",
            enabled=True,
            session=_LoopbackSession(edge_client),  # type: ignore[arg-type]
        )

        emotion_result = pusher.push_emotion_state(
            emotion_tag="joy",
            intensity=0.81,
            session_id="sess_edge_1",
            source_agent="limbic_agent",
        )
        gate_result = pusher.push_gate_control(
            gate_open=False,
            reason="platform_disconnected",
            platform="wechat",
            source_agent="cortex_agent",
        )

    assert emotion_result["ok"] is True
    assert emotion_result["dispatched"] is True
    assert fake_vts.calls[0]["emotion_tag"] == "JOY"
    assert fake_vts.calls[0]["intensity"] == 0.81

    assert gate_result["ok"] is True
    assert gate_result["dispatched"] is True
    assert registry.channel.gate_open is False


def test_edge_to_host_state_backflow_updates_host_snapshots(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INTERNAL_TRIBE_TOKEN", "tribe-token")

    bus = InMemoryTribeEventBus(max_events=128)
    set_tribe_event_bus(bus)
    state_registry = TribeStateRegistry()
    host_somatic = HostSomaticAgent(state_registry=state_registry)
    host_peripheral = HostPeripheralAgent(state_registry=state_registry)
    host_somatic.register_subscriptions()
    host_peripheral.register_subscriptions()

    with _build_host_client(bus) as host_client:
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

    _dispatch_host_state_events(
        bus=bus,
        host_somatic=host_somatic,
        host_peripheral=host_peripheral,
    )

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
