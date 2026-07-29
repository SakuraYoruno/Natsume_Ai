from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.hippocampus_agent import HippocampusAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _Bus:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, object]] = []

    def subscribe(self, event_type: str, handler: object) -> None:
        self.subscriptions.append((event_type, handler))


def test_canonical_hippocampus_retires_legacy_memory_and_affect_writers(monkeypatch) -> None:
    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical Hippocampus must not revive legacy memory or affect side effects")

    registry = TribeStateRegistry()
    agent = HippocampusAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent.get_default_mode_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent.get_limbic_agent", _forbidden)

    agent.register_subscriptions()
    episodic = agent.write_episodic_memory_trace(
        person_id="person-42",
        session_id="session-42",
        content="must not persist through the legacy writer",
    )
    semantic = agent.write_semantic_memory_fact(
        person_id="person-42",
        fact="must not persist through the legacy writer",
    )
    autobiographical = agent.write_autobiographical_memory(
        person_id="person-42",
        narrative="must not persist through the legacy writer",
        gate_result={"decision": "ALLOW"},
    )
    asyncio.run(agent._on_interaction_recorded(SimpleNamespace(payload={})))
    asyncio.run(agent._on_loneliness_signal(SimpleNamespace(payload={})))
    asyncio.run(agent._on_task_success(SimpleNamespace(payload={})))

    assert bus.subscriptions == []
    assert {row["reason_code"] for row in (episodic, semantic, autobiographical)} == {
        "LEGACY_HIPPOCAMPUS_MEMORY_WRITER_RETIRED"
    }
    assert registry.get_state("hippocampus_agent") is None
    assert agent.resolve_memory_layer_snapshot()["last_write_type"] == ""
    assert agent.export_runtime_state()["reason_code"] == "LEGACY_HIPPOCAMPUS_MEMORY_WRITER_RETIRED"


def test_canonical_hippocampus_keeps_pure_retrieval_row_formatting() -> None:
    agent = HippocampusAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=TribeStateRegistry(),
    )

    rows = agent.build_prompt_retrieval_rows(
        retrieval_summary={"query_plan": {"intent": "research", "original_query": "memory"}},
        retrieval_evidence_items=[
            {
                "source_ref": "memory://accepted/1",
                "snippet": "只是一条已经过检索层裁决的摘要。",
                "metadata": {"store_id": "episodic"},
            }
        ],
    )

    assert rows == [
        {
            "slot": "retrieval",
            "kind": "episodic",
            "source_ref": "memory://accepted/1",
            "evidence_ref": None,
            "content": "只是一条已经过检索层裁决的摘要。",
            "score": None,
            "confidence": None,
            "store_id": "episodic",
            "canon_id": None,
            "lang": None,
            "speaker": None,
            "file_rel_path": None,
            "block_id": None,
            "line_index": None,
            "emotion_color_tag": None,
            "suppression_flag": None,
            "distortion_level": None,
        }
    ]
