"""N2-T02：RecallFusion persona-aware memory ranking tests."""

from __future__ import annotations

import math

from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from tests.fakes.chroma_client import FakeChromaClient


_QUERY_EMBEDDING = [1.0, 0.0] + [0.0] * 14


def _score_vector(score: float) -> list[float]:
    remainder = max(0.0, 1.0 - float(score) ** 2)
    return [float(score), math.sqrt(remainder)] + [0.0] * 14


def _make_service() -> tuple[RecallFusionServiceContract, FakeChromaClient]:
    chroma = FakeChromaClient(collection_profiles={"episodic": 16})
    service = RecallFusionServiceContract(chroma_client=chroma, use_hybrid=False)
    return service, chroma


def _upsert_memory(
    chroma: FakeChromaClient,
    *,
    memory_id: str,
    text: str,
    score: float,
    metadata: dict,
) -> None:
    chroma.upsert(
        memory_id=memory_id,
        text=text,
        embedding=_score_vector(score),
        metadata=metadata,
        collection="episodic",
    )


def test_self_protect_downranks_suppressed_fragile_memory() -> None:
    service, chroma = _make_service()
    _upsert_memory(
        chroma,
        memory_id="fragile-memory",
        text="那天我其实很难过，后来就不太想再提了。",
        score=0.92,
        metadata={
            "emotion_tag": "SAD",
            "emotion_color_tag": "SAD",
            "suppression_flag": True,
            "distortion_level": 0.6,
        },
    )
    _upsert_memory(
        chroma,
        memory_id="warm-memory",
        text="上次我们一起说好了周末再聊，我一直记得。",
        score=0.78,
        metadata={
            "emotion_tag": "HAPPY",
            "emotion_color_tag": "WARM",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )

    result = service.recall(
        request_id="req-persona-fragile",
        query="你还记得我们上次说好的事吗",
        query_embedding=_QUERY_EMBEDDING,
        mood_state="critical",
        bond_summary={"stage": "FRIEND"},
        motive_state={
            "dominant_motive": "self_protect",
            "drives": {"self_protect_drive": 0.91, "withdraw_drive": 0.84},
        },
    )

    candidates = result.output["candidates"]
    assert candidates[0]["doc_id"] == "warm-memory"
    fragile = next(item for item in candidates if item["doc_id"] == "fragile-memory")
    assert fragile["metadata"]["persona_recall_boost"] < 0.0
    assert "suppression_penalty" in fragile["metadata"]["persona_recall_reason_tags"]


def test_care_and_continuity_promote_warm_shared_history_memory() -> None:
    service, chroma = _make_service()
    _upsert_memory(
        chroma,
        memory_id="warm-continuity",
        text="上次你说好一起去看展，我后来一直在想。",
        score=0.82,
        metadata={
            "emotion_tag": "HAPPY",
            "emotion_color_tag": "WARM",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )
    _upsert_memory(
        chroma,
        memory_id="neutral-note",
        text="今天整理了一下普通日程记录。",
        score=0.86,
        metadata={
            "emotion_tag": "NEUTRAL",
            "emotion_color_tag": "NEUTRAL",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )

    result = service.recall(
        request_id="req-persona-care",
        query="有没有我们之前一起计划过的事",
        query_embedding=_QUERY_EMBEDDING,
        mood_state="engaged",
        bond_summary={"stage": "FRIEND", "score": 0.68},
        motive_state={
            "dominant_motive": "continuity",
            "drives": {
                "continuity_drive": 0.93,
                "care_drive": 0.82,
                "reach_out_drive": 0.74,
            },
        },
    )

    top = result.output["candidates"][0]
    assert top["doc_id"] == "warm-continuity"
    assert top["metadata"]["persona_recall_boost"] > 0.0
    assert "continuity_boost" in top["metadata"]["persona_recall_reason_tags"]
    assert "warm_memory_boost" in top["metadata"]["persona_recall_reason_tags"]


def test_deep_bond_boosts_continuity_memory_more_than_shallow_bond() -> None:
    service, chroma = _make_service()
    _upsert_memory(
        chroma,
        memory_id="shared-history",
        text="之前提过的那件事，我后来还记得。",
        score=0.81,
        metadata={
            "emotion_tag": "NEUTRAL",
            "emotion_color_tag": "NEUTRAL",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )
    _upsert_memory(
        chroma,
        memory_id="plain-note",
        text="一条普通独白记录。",
        score=0.86,
        metadata={
            "emotion_tag": "NEUTRAL",
            "emotion_color_tag": "NEUTRAL",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )

    shallow = service.recall(
        request_id="req-persona-shallow",
        query="你会不会想起我们之前说过的事",
        query_embedding=_QUERY_EMBEDDING,
        mood_state="steady",
        bond_summary={"stage": "CLOSE_FRIEND"},
        motive_state={"drives": {"continuity_drive": 0.35}},
    )
    deep = service.recall(
        request_id="req-persona-deep",
        query="你会不会想起我们之前说过的事",
        query_embedding=_QUERY_EMBEDDING,
        mood_state="steady",
        bond_summary={"stage": "INTIMATE"},
        motive_state={"drives": {"continuity_drive": 0.35}},
    )

    assert shallow.output["candidates"][0]["doc_id"] == "plain-note"
    assert deep.output["candidates"][0]["doc_id"] == "shared-history"


def test_no_persona_context_keeps_default_ranking() -> None:
    service, chroma = _make_service()
    _upsert_memory(
        chroma,
        memory_id="fragile-memory",
        text="那天我其实很难过，后来就不太想再提了。",
        score=0.92,
        metadata={
            "emotion_tag": "SAD",
            "emotion_color_tag": "SAD",
            "suppression_flag": True,
            "distortion_level": 0.6,
        },
    )
    _upsert_memory(
        chroma,
        memory_id="warm-memory",
        text="上次我们一起说好了周末再聊，我一直记得。",
        score=0.78,
        metadata={
            "emotion_tag": "HAPPY",
            "emotion_color_tag": "WARM",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )

    result = service.recall(
        request_id="req-persona-default",
        query="你还记得我们上次说好的事吗",
        query_embedding=_QUERY_EMBEDDING,
    )

    top = result.output["candidates"][0]
    assert top["doc_id"] == "fragile-memory"
    assert "persona_recall_boost" not in top["metadata"]


def test_joint_relational_signals_prioritize_shared_history_over_higher_score_neutral() -> None:
    service, chroma = _make_service()
    _upsert_memory(
        chroma,
        memory_id="shared-promise",
        text="I still remember the promise we made last time together.",
        score=0.74,
        metadata={
            "emotion_tag": "HAPPY",
            "emotion_color_tag": "WARM",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )
    _upsert_memory(
        chroma,
        memory_id="neutral-log",
        text="Routine archive entry for a neutral note.",
        score=0.91,
        metadata={
            "emotion_tag": "NEUTRAL",
            "emotion_color_tag": "NEUTRAL",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )

    result = service.recall(
        request_id="req-persona-resonance",
        query="Do you still remember what we promised last time?",
        query_embedding=_QUERY_EMBEDDING,
        mood_state="warm",
        bond_summary={"stage": "CLOSE_FRIEND", "score": 0.84},
        motive_state={
            "dominant_motive": "continuity",
            "drives": {
                "continuity_drive": 0.95,
                "care_drive": 0.88,
                "reach_out_drive": 0.78,
            },
        },
    )

    top = result.output["candidates"][0]
    assert top["doc_id"] == "shared-promise"
    assert top["metadata"]["persona_recall_multiplier"] > 1.25
    assert "shared_history_resonance" in top["metadata"]["persona_recall_reason_tags"]


def test_defensive_recoil_penalty_only_appears_for_high_guarded_fragile_memory() -> None:
    service, chroma = _make_service()
    _upsert_memory(
        chroma,
        memory_id="fragile-memory",
        text="I do not want to reopen that distorted and painful memory.",
        score=0.88,
        metadata={
            "emotion_tag": "SAD",
            "emotion_color_tag": "SAD",
            "suppression_flag": True,
            "distortion_level": 0.62,
        },
    )
    _upsert_memory(
        chroma,
        memory_id="neutral-memory",
        text="A neutral memory note without emotional pull.",
        score=0.71,
        metadata={
            "emotion_tag": "NEUTRAL",
            "emotion_color_tag": "NEUTRAL",
            "suppression_flag": False,
            "distortion_level": 0.0,
        },
    )

    mild = service.recall(
        request_id="req-persona-mild-guard",
        query="What happened there?",
        query_embedding=_QUERY_EMBEDDING,
        mood_state="steady",
        bond_summary={"stage": "FRIEND", "score": 0.62},
        motive_state={
            "dominant_motive": "care",
            "drives": {
                "continuity_drive": 0.36,
                "care_drive": 0.48,
                "self_protect_drive": 0.34,
                "withdraw_drive": 0.22,
            },
        },
    )
    guarded = service.recall(
        request_id="req-persona-high-guard",
        query="What happened there?",
        query_embedding=_QUERY_EMBEDDING,
        mood_state="withdrawn",
        bond_summary={"stage": "FRIEND", "score": 0.62},
        motive_state={
            "dominant_motive": "self_protect",
            "drives": {
                "continuity_drive": 0.22,
                "care_drive": 0.18,
                "self_protect_drive": 0.94,
                "withdraw_drive": 0.86,
            },
        },
    )

    mild_fragile = next(item for item in mild.output["candidates"] if item["doc_id"] == "fragile-memory")
    guarded_fragile = next(item for item in guarded.output["candidates"] if item["doc_id"] == "fragile-memory")
    assert guarded_fragile["metadata"]["persona_recall_multiplier"] < mild_fragile["metadata"]["persona_recall_multiplier"]
    assert "protective_recoil_penalty" in guarded_fragile["metadata"]["persona_recall_reason_tags"]
    assert "protective_recoil_penalty" not in mild_fragile["metadata"]["persona_recall_reason_tags"]
