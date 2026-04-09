from __future__ import annotations

from host_brain.tribe.handoff import TribeHandoffCoordinator
from host_brain.tribe.registry import HostTribeRegistry
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_tribe_handoff_records_state_and_publishes_payload() -> None:
    published: list[dict] = []
    state_registry = TribeStateRegistry()
    coordinator = TribeHandoffCoordinator(
        state_registry=state_registry,
        tribe_registry=HostTribeRegistry(),
        event_publisher=lambda payload: published.append(dict(payload)),
    )

    payload = coordinator.record_dispatch_handoff(
        request_id="req_handoff_1",
        session_id="sess_handoff_1",
        route="skill",
        target_id="memory_recall",
        reason="keyword:search",
    )

    assert payload["source_family"] == "cortex"
    assert payload["target_family"] == "hippocampus"
    assert payload["route"] == "skill"
    assert payload["request_id"] == "req_handoff_1"

    source_state = state_registry.get_state("cortex")
    target_state = state_registry.get_state("hippocampus")
    assert source_state is not None
    assert target_state is not None
    assert source_state["state"]["handoff_count"] == 1
    assert target_state["state"]["handoff_count"] == 1
    assert source_state["state"]["last_handoff_outbound"]["request_id"] == "req_handoff_1"
    assert target_state["state"]["last_handoff_inbound"]["target_family"] == "hippocampus"

    assert len(published) == 1
    assert published[0]["request_id"] == "req_handoff_1"


def test_tribe_handoff_snapshot_contains_families_agents_and_states() -> None:
    state_registry = TribeStateRegistry()
    coordinator = TribeHandoffCoordinator(
        state_registry=state_registry,
        tribe_registry=HostTribeRegistry(),
        event_publisher=lambda _payload: None,
    )

    coordinator.record_dispatch_handoff(
        request_id="req_handoff_2",
        session_id="sess_handoff_2",
        route="plan",
        target_id=None,
        reason="llm_plan:plan",
    )

    snapshot = coordinator.snapshot()

    family_ids = {item["family_id"] for item in snapshot["families"]}
    agent_ids = {item["agent_id"] for item in snapshot["agents"]}
    assert "prefrontal" in family_ids
    assert "dispatch_agent" in agent_ids
    assert "cortex" in snapshot["states"]
    assert "prefrontal" in snapshot["states"]
    assert isinstance(snapshot["timestamp"], int)
