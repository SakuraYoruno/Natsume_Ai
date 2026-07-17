from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.internal_tribe_routes import set_tribe_event_bus
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.obs_routes_trace import refresh_trace_query_projections, router
from host_brain.tribe.event_bus import InMemoryTribeEventBus, new_tribe_event
from tests.fakes.obs_projection_runtime import compose_durable_obs_projection_runtime


@pytest.fixture
def projection_runtime(tmp_path):
    with compose_durable_obs_projection_runtime(
        tmp_path,
        request_id="obs-tribe-events",
    ) as runtime:
        yield runtime


def _build_client(projection_runtime) -> TestClient:
    refresh_trace_query_projections()
    app = FastAPI()
    app.state.obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: projection_runtime.store,
    )
    app.include_router(router)
    return TestClient(app)


def test_obs_tribe_events_returns_recent_items(projection_runtime) -> None:
    bus = InMemoryTribeEventBus(max_events=16)
    bus.publish(
        new_tribe_event(
            event_type="channel_message_received",
            source_agent="edge.channel",
            target_agent="cortex_agent",
            payload={"conversation_id": "conv_1"},
        )
    )
    bus.publish(
        new_tribe_event(
            event_type="voice_pipeline_started",
            source_agent="edge.somatic",
            target_agent="ans_agent",
            payload={"request_id": "req_1"},
        )
    )
    set_tribe_event_bus(bus)
    client = _build_client(projection_runtime)

    resp = client.get("/obs/tribe/events?limit=2")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["items"][0]["event_type"] == "voice_pipeline_started"
    assert data["items"][1]["event_type"] == "channel_message_received"


def test_obs_tribe_events_filter_by_event_type(projection_runtime) -> None:
    bus = InMemoryTribeEventBus(max_events=16)
    bus.publish(
        new_tribe_event(
            event_type="gateway_degraded",
            source_agent="edge.gateway",
            payload={"reason_code": "INPUT_REPORT_FAILED"},
        )
    )
    bus.publish(
        new_tribe_event(
            event_type="channel_message_received",
            source_agent="edge.channel",
            payload={"conversation_id": "conv_2"},
        )
    )
    set_tribe_event_bus(bus)
    client = _build_client(projection_runtime)

    resp = client.get("/obs/tribe/events?event_type=gateway_degraded&limit=10")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["event_type"] == "gateway_degraded"


def test_obs_tribe_events_filter_by_request_and_source(projection_runtime) -> None:
    bus = InMemoryTribeEventBus(max_events=16)
    bus.publish(
        new_tribe_event(
            event_type="voice_pipeline_started",
            source_agent="edge.somatic",
            target_agent="ans_agent",
            payload={"request_id": "req_1", "session_id": "sess_1"},
        )
    )
    bus.publish(
        new_tribe_event(
            event_type="voice_pipeline_started",
            source_agent="edge.somatic",
            target_agent="ans_agent",
            payload={"request_id": "req_2", "session_id": "sess_2"},
        )
    )
    bus.publish(
        new_tribe_event(
            event_type="channel_message_received",
            source_agent="edge.channel",
            target_agent="cortex_agent",
            payload={"request_id": "req_1", "conversation_id": "conv_1"},
        )
    )
    set_tribe_event_bus(bus)
    client = _build_client(projection_runtime)

    resp = client.get("/obs/tribe/events?request_id=req_1&source_agent=edge.somatic&limit=10")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["event_type"] == "voice_pipeline_started"
    assert data["items"][0]["source_agent"] == "edge.somatic"
