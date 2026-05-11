from __future__ import annotations

from types import SimpleNamespace

from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.isolated_surface_harness import build_isolated_surface_messages
from host_brain.persona.surface_memory_recall import (
    SURFACE_MEMORY_EVIDENCE_REQUIRED,
    SurfaceMemoryRecallPacket,
    build_surface_input_with_memory_recall,
    build_surface_memory_recall_packet,
    recall_surface_memory_packet,
    write_surface_turn_memory,
)


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


def _recall_result() -> SimpleNamespace:
    return SimpleNamespace(
        output={
            "query": "还记得上次说的展吗",
            "status": "OK",
            "stage": "S_RET_RECALL",
            "degraded": False,
            "reason_code": None,
            "routes": [
                {"route_id": "episodic", "store_id": "episodic_vector"},
                {"route_id": "bond", "store_id": "neo4j_bond"},
                {"route_id": "world", "store_id": "world_canon"},
            ],
            "route_rows": [],
            "route_errors": [],
            "candidate_count": 4,
            "candidates": [
                {
                    "doc_id": "mem-1",
                    "source_ref": "memory://episodic/mem-1",
                    "evidence_refs": "evd://episodic/mem-1",
                    "snippet": 'memory written: {"text":"上次说好周末去看展"} chat://session/s1',
                    "score": 0.93,
                    "confidence": 0.93,
                    "metadata": {"store_id": "episodic_vector"},
                },
                {
                    "doc_id": "bond-1",
                    "source_ref": "bond://nini",
                    "evidence_ref": "evd://bond/bond-1",
                    "snippet": "关系阶段：熟人私聊，别升级亲密。",
                    "score": 0.82,
                    "metadata": {"store_id": "neo4j_bond"},
                },
                {
                    "doc_id": "world-1",
                    "source_ref": "canon://world/1",
                    "evidence_ref": "evd://world/world-1",
                    "snippet": "新海天这个名字是身份锚点。",
                    "score": 0.74,
                    "metadata": {"store_id": "world_canon"},
                },
                {
                    "doc_id": "mem-2",
                    "source_ref": "memory://episodic/mem-2",
                    "evidence_ref": "evd://episodic/mem-2",
                    "snippet": "第二条同路由记忆应当在多路优先后再补位。",
                    "score": 0.71,
                    "metadata": {"store_id": "episodic_vector"},
                },
            ],
        }
    )


def test_surface_memory_recall_packet_compresses_multi_route_candidates() -> None:
    packet = build_surface_memory_recall_packet(_recall_result(), max_facts=3)

    assert isinstance(packet, SurfaceMemoryRecallPacket)
    assert packet.facts == (
        "记忆线索：上次说好周末去看展",
        "关系线索：关系阶段：熟人私聊，别升级亲密。",
        "世界事实：新海天这个名字是身份锚点。",
    )
    assert packet.route_ids == ("episodic", "bond", "world")
    assert packet.evidence_refs == (
        "evd://episodic/mem-1",
        "evd://bond/bond-1",
        "evd://world/world-1",
    )
    assert "chat://session" not in "\n".join(packet.facts)
    assert "{" not in "\n".join(packet.facts)


def test_recall_surface_memory_packet_calls_recall_and_surface_gets_only_facts() -> None:
    captured: dict[str, object] = {}

    class _RecallService:
        def recall(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return _recall_result()

    packet = recall_surface_memory_packet(
        recall_service=_RecallService(),
        request_id="req-memory",
        query="还记得上次说的展吗",
        query_embedding=[0.1] * 16,
        person_id="p1",
        session_id="s1",
        target_store_ids=["episodic_vector", "neo4j_bond"],
        max_facts=2,
    )
    context = ThreeLayerContinuityContext(
        current_user_message="还记得上次说的展吗",
        mid_term_summary="尼尼和新海天在验证记忆召回压缩。",
        task_mode_hint="recall_memory",
        question_budget=0,
    )
    request = build_surface_input_with_memory_recall(
        context,
        packet=packet,
        soul_md=SOUL,
        case_id="memory_surface",
        max_facts=8,
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert captured["request_id"] == "req-memory"
    assert captured["target_store_ids"] == ["episodic_vector", "neo4j_bond"]
    assert "记忆线索：上次说好周末去看展" in rendered
    assert "关系线索：关系阶段：熟人私聊，别升级亲密。" in rendered
    assert "memory://episodic/mem-1" not in rendered
    assert "metadata" not in rendered


def test_write_surface_turn_memory_writes_compact_evidence_gated_record() -> None:
    captured: dict[str, object] = {}

    class _Store:
        def write_memory(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return SimpleNamespace(success=True, reason_code=None, detail="memory written", memory_id="mem-1")

    result = write_surface_turn_memory(
        episodic_store=_Store(),
        request_id="req-1",
        session_id="sess-1",
        person_id="person-1",
        user_message="我今天有点累，但还想继续调文本质量",
        assistant_reply="先别硬撑，我们只看下一处问题。",
        ts_ms=123,
        intent_id="intent-1",
    )

    assert result.success
    assert result.memory_id == "mem-1"
    assert captured["evidence_refs"] == [
        "chat://request/req-1",
        "chat://session/sess-1",
        "chat://person/person-1",
    ]
    assert "当前回合摘要" in str(captured["text"])
    assert "调文本质量" in str(captured["text"])
    assert captured["decay_profile_id"] == "SLOW"
    assert captured["visibility"] == "internal"


def test_write_surface_turn_memory_requires_evidence_refs() -> None:
    class _Store:
        def write_memory(self, **kwargs):  # noqa: ANN003
            raise AssertionError("should not be called")

    result = write_surface_turn_memory(
        episodic_store=_Store(),
        request_id="",
        session_id="",
        person_id="",
        user_message="早",
        assistant_reply="早啊。",
    )

    assert not result.success
    assert result.reason_code == SURFACE_MEMORY_EVIDENCE_REQUIRED
