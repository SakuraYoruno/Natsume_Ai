from __future__ import annotations

from host_brain.app.sub_agent_context import (
    build_agent_context_envelope,
    sub_agent_history_turn_limit,
)


def _turns(count: int, *, chars: int = 900) -> list[dict[str, str]]:
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"turn-{index} " + ("x" * chars),
        }
        for index in range(count)
    ]


def _packet() -> dict[str, object]:
    return {
        "facts": [f"长期记忆：事实 {index}" for index in range(10)],
        "relationship_clues": [f"关系线索 {index}" for index in range(4)],
        "style_anchor": [f"风格锚点 {index}" for index in range(4)],
        "must_not_do": [f"不要事项 {index}" for index in range(8)],
        "evidence_refs": [f"evidence://{index}" for index in range(10)],
        "source_refs": [f"source://{index}" for index in range(10)],
        "confidence": 0.73,
    }


def _evidence(count: int) -> list[dict[str, object]]:
    return [
        {
            "doc_id": f"doc-{index}",
            "source_ref": f"life_episode://{index}",
            "snippet": f"evidence {index} " + ("y" * 900),
            "score": 0.9 - index * 0.01,
        }
        for index in range(count)
    ]


def test_intent_profile_keeps_constraints_without_style_noise() -> None:
    envelope = build_agent_context_envelope(
        agent_id="intent_agent",
        task="执行这个任务",
        context={
            "recent_turns": _turns(9),
            "surface_memory_recall_packet": _packet(),
            "retrieval_evidence_items": _evidence(10),
            "semantic_facts": [f"长期事实 {index}" for index in range(8)],
        },
    ).as_dict()

    window = envelope["conversation_window"]
    memory = envelope["memory_packet"]
    evidence = envelope["evidence_pack"]

    assert envelope["trace"]["context_profile"] == "intent"
    assert len(window["recent_turns"]) == 4
    assert window["recent_turns"][0]["content"].startswith("turn-5")
    assert len(window["recent_turns"][0]["content"]) <= 500
    assert len(memory["facts"]) == 4
    assert "relationship_clues" not in memory
    assert "style_anchor" not in memory
    assert len(memory["must_not_do"]) == 6
    assert len(envelope["semantic_facts"]) == 3
    assert len(evidence["items"]) == 6
    assert len(evidence["items"][0]["snippet"]) <= 480


def test_retrieval_profile_preserves_more_source_and_evidence_refs() -> None:
    envelope = build_agent_context_envelope(
        agent_id="retrieval_agent",
        task="找刚才推荐过的书",
        context={
            "recent_turns": _turns(8),
            "surface_memory_recall_packet": _packet(),
            "retrieval_evidence_items": _evidence(12),
        },
    ).as_dict()

    memory = envelope["memory_packet"]
    evidence = envelope["evidence_pack"]

    assert envelope["trace"]["context_profile"] == "retrieval"
    assert len(envelope["conversation_window"]["recent_turns"]) == 3
    assert len(memory["evidence_refs"]) == 8
    assert len(memory["source_refs"]) == 8
    assert "relationship_clues" not in memory
    assert "style_anchor" not in memory
    assert len(evidence["items"]) == 8
    assert sub_agent_history_turn_limit("retrieval_agent") == 3


def test_repair_profile_keeps_failure_evidence_with_short_dialog_window() -> None:
    envelope = build_agent_context_envelope(
        agent_id="repair_agent",
        task="修复后台任务失败",
        context={
            "recent_turns": _turns(7),
            "surface_memory_recall_packet": _packet(),
            "retrieval_evidence_items": _evidence(9),
            "scratchpad_summary": "failed attempt " + ("z" * 1_000),
        },
    ).as_dict()

    assert envelope["trace"]["context_profile"] == "repair"
    assert len(envelope["conversation_window"]["recent_turns"]) == 3
    assert len(envelope["memory_packet"]["facts"]) == 3
    assert "relationship_clues" not in envelope["memory_packet"]
    assert len(envelope["evidence_pack"]["items"]) == 8
    assert len(envelope["evidence_pack"]["items"][0]["snippet"]) <= 700
    assert len(envelope["scratchpad_summary"]) <= 900
