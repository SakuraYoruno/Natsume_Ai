from __future__ import annotations

from host_brain.tribe.event_bus import InMemoryTribeEventBus, new_tribe_event


def test_event_bus_recent_returns_latest_first() -> None:
    bus = InMemoryTribeEventBus(max_events=8)
    bus.publish(new_tribe_event(event_type="A", source_agent="edge.channel"))
    bus.publish(new_tribe_event(event_type="B", source_agent="edge.somatic"))

    items = bus.recent(limit=2)

    assert len(items) == 2
    assert items[0].event_type == "B"
    assert items[1].event_type == "A"


def test_event_bus_recent_filters_by_event_type() -> None:
    bus = InMemoryTribeEventBus(max_events=8)
    bus.publish(new_tribe_event(event_type="body_sense_updated", source_agent="edge.somatic"))
    bus.publish(new_tribe_event(event_type="channel_message_received", source_agent="edge.channel"))
    bus.publish(new_tribe_event(event_type="body_sense_updated", source_agent="edge.somatic"))

    items = bus.recent(limit=10, event_type="body_sense_updated")

    assert len(items) == 2
    assert all(item.event_type == "body_sense_updated" for item in items)


def test_event_bus_recent_filters_by_payload_ids_and_agents() -> None:
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
            event_type="channel_message_received",
            source_agent="edge.channel",
            target_agent="cortex_agent",
            payload={"conversation_id": "conv_1"},
        )
    )
    bus.publish(
        new_tribe_event(
            event_type="voice_pipeline_ended",
            source_agent="edge.somatic",
            target_agent="ans_agent",
            payload={"request_id": "req_2", "session_id": "sess_2"},
        )
    )

    by_request = bus.recent(limit=10, request_id="req_1")
    assert len(by_request) == 1
    assert by_request[0].event_type == "voice_pipeline_started"

    by_session_and_source = bus.recent(limit=10, session_id="sess_2", source_agent="edge.somatic")
    assert len(by_session_and_source) == 1
    assert by_session_and_source[0].event_type == "voice_pipeline_ended"

    by_target_and_conversation = bus.recent(limit=10, target_agent="cortex_agent", conversation_id="conv_1")
    assert len(by_target_and_conversation) == 1
    assert by_target_and_conversation[0].event_type == "channel_message_received"
