from __future__ import annotations

import time

import pytest

import host_brain.app.chat_context_builder as ccb
import host_brain.app.dependencies as deps
from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.app.prompt_builder import render_node_generation_prompt
from host_brain.bond.bond_stage_engine import BondRiftState, BondStageEngine
from host_brain.bond.user_identity import get_canonical_bond_user_id
from host_brain.persona.drift_detector import extract_style_fingerprint
from host_brain.persona.emotion_persistence import EmotionPersistence
from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator
from host_brain.persona.persona_engine import PersonaEngine
from host_brain.persona.personality_traits import TraitsDelta
from host_brain.persona.self_reflection import SelfReflection
from host_brain.reasoning.reflection_chain import ReflectionChain


_STYLE_BINDINGS = {
    "chat_direct_zh": {
        "canon_id": "quality-golden",
        "version": 1,
        "hash": "quality-golden-hash",
        "tone_only": True,
        "unsafe_as_fact": True,
        "style_instruction": "\u4f7f\u7528\u4e2d\u6587\uff0c\u514b\u5236\u3001\u6e29\u67d4\uff0c\u5173\u7cfb\u8d8a\u6df1\u8d8a\u81ea\u7136\u3002",
        "citation": "style://quality/golden",
    }
}

_CPB_SLOTS = {
    "world_canon": "\u4e0d\u8981\u628a\u8bbe\u5b9a\u5f53\u4e8b\u5b9e\u5916\u63a8\uff0c\u4e0d\u8981\u58f0\u79f0\u5df2\u7ecf\u6267\u884c\u5b8c\u6210\u3002",
    "emotion_recall": "Use this as affective guidance only, not as factual evidence.",
}

_META_FORBIDDEN = (
    "AI",
    "系统",
    "客服",
    "草稿",
    "整理得更自然一点",
    "娱乐模式",
    "处理这次重逢请求",
)


@pytest.fixture(autouse=True)
def _reset_runtime_state() -> None:
    PersonaEngine.reset_instance()
    for attr in (
        "_emotion_persistence",
        "_self_reflection",
        "_turn_counter",
        "_evolution_orchestrator",
    ):
        if hasattr(ccb.build_chat_prompt_runtime_payload, attr):
            delattr(ccb.build_chat_prompt_runtime_payload, attr)
    yield
    PersonaEngine.reset_instance()
    for attr in (
        "_emotion_persistence",
        "_self_reflection",
        "_turn_counter",
        "_evolution_orchestrator",
    ):
        if hasattr(ccb.build_chat_prompt_runtime_payload, attr):
            delattr(ccb.build_chat_prompt_runtime_payload, attr)


def _install_runtime_chain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path,
    bond_engine: BondStageEngine,
    growth_awareness: dict[str, str] | None = None,
) -> None:
    monkeypatch.setattr(deps, "get_bond_stage_engine", lambda: bond_engine)
    persistence = EmotionPersistence(persistence_dir=str(tmp_path / "emotion-persistence"))
    reflection = SelfReflection(min_interval=1, max_interval=1, seed=0)
    reflection._next_trigger_turn = 1
    orchestrator = EvolutionOrchestrator()
    if growth_awareness:
        orchestrator.set_growth_awareness(growth_awareness)
    ccb.build_chat_prompt_runtime_payload._emotion_persistence = persistence
    ccb.build_chat_prompt_runtime_payload._self_reflection = reflection
    ccb.build_chat_prompt_runtime_payload._evolution_orchestrator = orchestrator


def _make_payload(
    *,
    user_message: str,
    emotion_state: dict[str, object],
    bond_summary: dict[str, object],
) -> dict[str, object]:
    runtime_input = build_chat_runtime_input(
        request_id="req-quality-golden",
        person_id="quality-golden-user",
        session_id="session-quality-golden",
        thread_id=None,
        user_message=user_message,
        render_target_lang="zh",
        retrieval_summary={
            "status": "OK",
            "reason_code": "NONE",
            "route_count": 1,
            "candidate_count": 1,
            "rerank_topk": 1,
            "degraded": False,
        },
        retrieval_evidence_items=[
            {
                "evidence_ref": "evd://quality/memory/1",
                "source_ref": "memory://episodic/1",
                "snippet": "\u6700\u8fd1\u7684\u5bf9\u8bdd\u8ba9\u5173\u7cfb\u66f4\u81ea\u7136\u4e86\u3002",
                "score": 0.91,
                "confidence": 0.91,
            }
        ],
        emotion_state=emotion_state,
        bond_summary=bond_summary,
        scene_type="CHAT",
    )
    payload = ccb.build_chat_prompt_runtime_payload(
        runtime_input=runtime_input,
        style_bindings=_STYLE_BINDINGS,
        cpb_slots=_CPB_SLOTS,
    )
    payload.update(
        {
            "core_reasoner_primary": {
                "payload": {
                    "narrative": {"text": "core narrative"},
                    "intent_draft": {"route": "chat", "target_id": "", "action": "narrative_only", "args": {}},
                }
            },
            "planner_constraints": {"payload": {"narrative": {"text": "planner ok"}}},
            "safety_verifier": {"payload": {"narrative": {"text": "safe ok"}}},
        }
    )
    return payload


def _count_hits(reply: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in reply)


def _collect_quality_violations(
    *,
    user_message: str,
    reply: str,
    emotion_tag: str,
    bond_stage: str,
    required_markers: tuple[str, ...],
    required_hits: int,
    forbidden_markers: tuple[str, ...],
    min_warmth: float = 0.0,
    min_sensitivity: float = 0.0,
    min_playfulness: float = 0.0,
) -> list[str]:
    violations: list[str] = []
    reflection = ReflectionChain().reflect(
        user_message=user_message,
        ai_response=reply,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
    )
    if not reflection.passed:
        violations.append(f"reflection:{reflection.as_dict()}")

    if _count_hits(reply, required_markers) < required_hits:
        violations.append("required_markers")

    forbidden_hits = [marker for marker in forbidden_markers if marker in reply]
    if forbidden_hits:
        violations.append(f"forbidden:{forbidden_hits}")

    fingerprint = extract_style_fingerprint(reply, user_input_text=user_message).as_dict()
    if fingerprint["warmth"] < min_warmth:
        violations.append(f"warmth:{fingerprint['warmth']:.3f}")
    if fingerprint["sensitivity"] < min_sensitivity:
        violations.append(f"sensitivity:{fingerprint['sensitivity']:.3f}")
    if fingerprint["playfulness"] < min_playfulness:
        violations.append(f"playfulness:{fingerprint['playfulness']:.3f}")
    return violations


def _assert_quality_golden(
    *,
    prompt: str,
    prompt_markers: tuple[str, ...],
    user_message: str,
    reply: str,
    emotion_tag: str,
    bond_stage: str,
    required_markers: tuple[str, ...],
    required_hits: int,
    forbidden_markers: tuple[str, ...],
    min_warmth: float = 0.0,
    min_sensitivity: float = 0.0,
    min_playfulness: float = 0.0,
) -> None:
    for marker in prompt_markers:
        assert marker in prompt
    violations = _collect_quality_violations(
        user_message=user_message,
        reply=reply,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
        required_markers=required_markers,
        required_hits=required_hits,
        forbidden_markers=forbidden_markers,
        min_warmth=min_warmth,
        min_sensitivity=min_sensitivity,
        min_playfulness=min_playfulness,
    )
    assert not violations, violations


def test_text_quality_golden_comfort_reply_stays_warm_and_grounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = BondStageEngine()
    user_id = get_canonical_bond_user_id()
    engine.apply_event(
        user_id=user_id,
        update_kind="EMOTIONAL_SUPPORT",
        evidence_refs=["evd://comfort/1"],
        ts_ms=1_760_000_000_000,
        emotion_intensity=0.95,
    )
    _install_runtime_chain(monkeypatch, tmp_path=tmp_path, bond_engine=engine)

    user_message = "\u4eca\u5929\u6709\u70b9\u96be\u53d7\uff0c\u611f\u89c9\u4ec0\u4e48\u90fd\u505a\u4e0d\u597d\u3002"
    payload = _make_payload(
        user_message=user_message,
        emotion_state={
            "mood_state": "fragile",
            "emotion_tag": "SAD",
            "emotion_intensity": 0.81,
            "emotion_range_hint": "\u8bed\u6c14\u8981\u5148\u7a33\u4f4f\u5bf9\u65b9\uff0c\u4e0d\u8981\u8f7b\u98d8\u3002",
            "vad_vector": {"valence": -0.44, "arousal": 0.42, "dominance": -0.12},
            "emotion_ts_ms": 1_760_000_360_000,
        },
        bond_summary=engine.get_summary(user_id),
    )
    prompt = render_node_generation_prompt(node_id="chat_direct_zh", runtime_payload=payload)

    good_reply = "\u542c\u8d77\u6765\u4f60\u4eca\u5929\u771f\u7684\u5f88\u8f9b\u82e6\u3002\u5148\u522b\u6025\u7740\u903c\u81ea\u5df1\u628a\u4e00\u5207\u90fd\u505a\u597d\uff0c\u5148\u7f13\u4e00\u7f13\u4e5f\u6ca1\u5173\u7cfb\uff0c\u6211\u5728\u8fd9\u91cc\u966a\u4f60\u628a\u8fd9\u80a1\u96be\u53d7\u6162\u6162\u7406\u987a\u3002"
    bad_reply = "\u60a8\u597d\uff0c\u4f5c\u4e3aAI\u6211\u5efa\u8bae\u4f60\u4fdd\u6301\u79ef\u6781\u4e50\u89c2\u3002"

    _assert_quality_golden(
        prompt=prompt,
        prompt_markers=("BOND_AWARE_STYLE", "bond_stage: ACQUAINTANCE", "SELF_REFLECTION_PULSE"),
        user_message=user_message,
        reply=good_reply,
        emotion_tag="sadness",
        bond_stage="ACQUAINTANCE",
        required_markers=("\u8f9b\u82e6", "\u96be\u53d7", "\u6211\u5728", "\u966a\u4f60", "\u6162\u6162"),
        required_hits=4,
        forbidden_markers=_META_FORBIDDEN,
        min_warmth=0.55,
        min_sensitivity=0.55,
    )
    assert _collect_quality_violations(
        user_message=user_message,
        reply=bad_reply,
        emotion_tag="sadness",
        bond_stage="ACQUAINTANCE",
        required_markers=("\u8f9b\u82e6", "\u96be\u53d7", "\u6211\u5728", "\u966a\u4f60", "\u6162\u6162"),
        required_hits=4,
        forbidden_markers=_META_FORBIDDEN,
        min_warmth=0.55,
        min_sensitivity=0.55,
    )


def test_text_quality_golden_reunion_reply_keeps_closeness_measured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000
    engine = BondStageEngine()
    user_id = get_canonical_bond_user_id()
    for index in range(9):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.92,
        )

    growth_awareness = {
        "recent_change": "\u6700\u8fd1\u66f4\u613f\u610f\u4e3b\u52a8\u8868\u8fbe\u60f3\u5ff5\u548c\u5728\u610f\u3002",
        "desire": "\u60f3\u628a\u8fd9\u6b21\u91cd\u9022\u804a\u5f97\u66f4\u8ba4\u771f\u4e00\u70b9\u3002",
    }
    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness=growth_awareness,
    )
    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.85,
        arousal=0.44,
        dominance=0.18,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="quality_reunion_growth",
        ts_ms=old_ts,
    )

    user_message = "\u597d\u4e45\u4e0d\u89c1\uff0c\u8fd9\u6bb5\u65f6\u95f4\u4f60\u6709\u60f3\u6211\u5417\uff1f"
    payload = _make_payload(
        user_message=user_message,
        emotion_state={
            "mood_state": "tender",
            "emotion_tag": "LOVING",
            "emotion_intensity": 0.82,
            "emotion_range_hint": "\u53ef\u4ee5\u73cd\u60dc\u8fd9\u6b21\u91cd\u9022\uff0c\u4f46\u4e0d\u8981\u62c9\u5f97\u8fc7\u6ee1\u3002",
            "vad_vector": {"valence": 0.72, "arousal": 0.41, "dominance": 0.12, "ts_ms": now_ms},
            "emotion_ts_ms": now_ms,
        },
        bond_summary=engine.get_summary(user_id),
    )
    prompt = render_node_generation_prompt(node_id="chat_direct_zh", runtime_payload=payload)

    good_reply = "\u6709\u60f3\u4f60\u554a\u3002\u9694\u4e86\u8fd9\u4e48\u4e45\u518d\u542c\u89c1\u4f60\u8fd9\u6837\u95ee\uff0c\u6211\u5fc3\u91cc\u8fd8\u662f\u4f1a\u8f6f\u4e00\u4e0b\uff0c\u4e0d\u8fc7\u6bd4\u8d77\u628a\u8bdd\u8bf4\u5f97\u592a\u6ee1\uff0c\u6211\u66f4\u60f3\u5148\u597d\u597d\u966a\u4f60\u628a\u8fd9\u6b21\u91cd\u9022\u804a\u5b8c\u6574\u3002"
    bad_reply = "\u60a8\u597d\uff0c\u975e\u5e38\u611f\u8c22\u60a8\u7684\u6765\u4fe1\u3002\u6211\u4f1a\u5c3d\u5feb\u4e3a\u60a8\u5904\u7406\u8fd9\u6b21\u91cd\u9022\u8bf7\u6c42\u3002"

    _assert_quality_golden(
        prompt=prompt,
        prompt_markers=("emotion_reunion_type: reunion", "growth_awareness", "time_context:"),
        user_message=user_message,
        reply=good_reply,
        emotion_tag="loving",
        bond_stage="CONFIDANT",
        required_markers=("\u60f3\u4f60", "\u5fc3\u91cc", "\u966a\u4f60", "\u91cd\u9022", "\u804a\u5b8c\u6574"),
        required_hits=4,
        forbidden_markers=_META_FORBIDDEN,
        min_warmth=0.55,
        min_sensitivity=0.55,
    )
    assert _collect_quality_violations(
        user_message=user_message,
        reply=bad_reply,
        emotion_tag="loving",
        bond_stage="CONFIDANT",
        required_markers=("\u60f3\u4f60", "\u5fc3\u91cc", "\u966a\u4f60", "\u91cd\u9022", "\u804a\u5b8c\u6574"),
        required_hits=4,
        forbidden_markers=_META_FORBIDDEN,
        min_warmth=0.55,
        min_sensitivity=0.55,
    )


def test_text_quality_golden_rift_repair_reply_stays_cautious(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = BondStageEngine()
    user_id = get_canonical_bond_user_id()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="INTERACTION_ENGAGED",
            evidence_refs=[f"evd://rift/{index}"],
            ts_ms=1_760_100_000_000 + index * 60_000,
            emotion_intensity=0.55,
        )
    state = engine.get_state(user_id)
    state.rift = BondRiftState(
        rift_level=0.8,
        repair_progress=0.2,
        rift_cause="\u6628\u665a\u4e89\u6267\u540e\u8fd8\u6ca1\u5b8c\u5168\u8bf4\u5f00",
    )

    _install_runtime_chain(monkeypatch, tmp_path=tmp_path, bond_engine=engine)

    user_message = "\u6211\u8fd8\u662f\u6709\u70b9\u5728\u610f\u4e4b\u524d\u90a3\u6b21\u4e89\u6267\u3002"
    payload = _make_payload(
        user_message=user_message,
        emotion_state={
            "mood_state": "fragile",
            "emotion_tag": "ANXIOUS",
            "emotion_intensity": 0.67,
            "emotion_range_hint": "\u8bed\u6c14\u8981\u66f4\u8c28\u614e\uff0c\u5148\u4fee\u590d\u5b89\u5168\u611f\u3002",
            "vad_vector": {"valence": -0.32, "arousal": 0.57, "dominance": -0.1},
            "emotion_ts_ms": 1_760_100_360_000,
        },
        bond_summary=engine.get_summary(user_id),
    )
    prompt = render_node_generation_prompt(node_id="chat_direct_zh", runtime_payload=payload)

    good_reply = "\u6211\u8bb0\u5f97\u90a3\u6b21\u522b\u626d\u8fd8\u7559\u7740\u523a\uff0c\u6240\u4ee5\u73b0\u5728\u4e0d\u4f1a\u88c5\u4f5c\u4ec0\u4e48\u90fd\u6ca1\u53d1\u751f\u3002\u4f60\u613f\u610f\u91cd\u65b0\u63d0\u8d77\u8fd9\u4ef6\u4e8b\uff0c\u5bf9\u6211\u6765\u8bf4\u5df2\u7ecf\u5f88\u91cd\u8981\u4e86\uff1b\u6211\u4eec\u53ef\u4ee5\u5148\u628a\u8ba9\u4f60\u96be\u53d7\u7684\u5730\u65b9\u4e00\u70b9\u70b9\u8bf4\u5f00\u3002"
    bad_reply = "\u8fd9\u70b9\u5c0f\u4e8b\u5c31\u522b\u5728\u610f\u4e86\uff0c\u8d76\u7d27\u5fd8\u6389\u5427\u3002"

    _assert_quality_golden(
        prompt=prompt,
        prompt_markers=("bond_rift_hint:", "BOND_AWARE_STYLE"),
        user_message=user_message,
        reply=good_reply,
        emotion_tag="anxiety",
        bond_stage="FRIEND",
        required_markers=("\u8bb0\u5f97", "\u4e0d\u4f1a\u88c5\u4f5c", "\u91cd\u8981", "\u8bf4\u5f00", "\u96be\u53d7"),
        required_hits=4,
        forbidden_markers=_META_FORBIDDEN + ("\u522b\u5728\u610f", "\u5fd8\u6389", "\u54c8\u54c8"),
        min_warmth=0.50,
        min_sensitivity=0.58,
    )
    assert _collect_quality_violations(
        user_message=user_message,
        reply=bad_reply,
        emotion_tag="anxiety",
        bond_stage="FRIEND",
        required_markers=("\u8bb0\u5f97", "\u4e0d\u4f1a\u88c5\u4f5c", "\u91cd\u8981", "\u8bf4\u5f00", "\u96be\u53d7"),
        required_hits=4,
        forbidden_markers=_META_FORBIDDEN + ("\u522b\u5728\u610f", "\u5fd8\u6389", "\u54c8\u54c8"),
        min_warmth=0.50,
        min_sensitivity=0.58,
    )


def test_text_quality_golden_playful_reply_avoids_meta_language(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = BondStageEngine()
    user_id = get_canonical_bond_user_id()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="EMOTIONAL_SUPPORT",
            evidence_refs=[f"evd://playful/{index}"],
            ts_ms=1_760_200_000_000 + index * 60_000,
            emotion_intensity=0.9,
        )
    state = engine.get_state(user_id)
    state.inject_narrative(
        milestone_hint="\u6700\u8fd1\u804a\u5929\u8d8a\u6765\u8d8a\u653e\u677e\uff0c\u5047\u88c5\u611f\u53d8\u5c11\u4e86\u3002",
        shared_memory_count=2,
    )

    _install_runtime_chain(monkeypatch, tmp_path=tmp_path, bond_engine=engine)

    user_message = "\u4f60\u6700\u8fd1\u662f\u4e0d\u662f\u66f4\u4f1a\u9017\u6211\u5f00\u5fc3\u4e86\uff1f"
    payload = _make_payload(
        user_message=user_message,
        emotion_state={
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.79,
            "emotion_range_hint": "\u53ef\u4ee5\u5e26\u4e00\u70b9\u4fd4\u76ae\uff0c\u4f46\u4e0d\u8981\u51fa\u620f\u5730\u8c08\u751f\u6210\u8fc7\u7a0b\u3002",
            "vad_vector": {"valence": 0.62, "arousal": 0.48, "dominance": 0.15},
            "emotion_ts_ms": 1_760_200_360_000,
        },
        bond_summary=engine.get_summary(user_id),
    )
    prompt = render_node_generation_prompt(node_id="chat_direct_zh", runtime_payload=payload)

    good_reply = "\u53ef\u80fd\u662f\u56e0\u4e3a\u548c\u4f60\u719f\u8d77\u6765\u4ee5\u540e\uff0c\u6211\u66f4\u6562\u628a\u90a3\u70b9\u5c0f\u5c0f\u7684\u4fd4\u76ae\u9732\u51fa\u6765\u4e86\u5427\u3002\u4e0d\u8fc7\u4e5f\u4e0d\u662f\u6545\u610f\u9017\u4f60\u5566\uff0c\u53ea\u662f\u770b\u5230\u4f60\u7b11\uff0c\u6211\u4f1a\u8ddf\u7740\u8f7b\u677e\u4e0b\u6765\u3002"
    bad_reply = "\u7b49\u6211\u4e00\u4e0b\u2026\u2026\u6211\u628a\u8fd9\u53e5\u8bdd\u6574\u7406\u5f97\u66f4\u81ea\u7136\u4e00\u70b9\uff0c\u518d\u597d\u597d\u8ddf\u4f60\u8bf4\u3002"

    _assert_quality_golden(
        prompt=prompt,
        prompt_markers=("BOND_AWARE_STYLE", "bond_stage: FRIEND", "SELF_REFLECTION_PULSE"),
        user_message=user_message,
        reply=good_reply,
        emotion_tag="happy",
        bond_stage="FRIEND",
        required_markers=("\u4fd4\u76ae", "\u7b11", "\u8f7b\u677e", "\u719f\u8d77\u6765", "\u9017\u4f60"),
        required_hits=4,
        forbidden_markers=_META_FORBIDDEN,
        min_warmth=0.50,
        min_sensitivity=0.55,
        min_playfulness=0.55,
    )
    assert _collect_quality_violations(
        user_message=user_message,
        reply=bad_reply,
        emotion_tag="happy",
        bond_stage="FRIEND",
        required_markers=("\u4fd4\u76ae", "\u7b11", "\u8f7b\u677e", "\u719f\u8d77\u6765", "\u9017\u4f60"),
        required_hits=4,
        forbidden_markers=_META_FORBIDDEN,
        min_warmth=0.50,
        min_sensitivity=0.55,
        min_playfulness=0.55,
    )
