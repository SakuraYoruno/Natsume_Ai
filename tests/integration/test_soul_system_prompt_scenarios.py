from __future__ import annotations

import time

import pytest

import host_brain.app.chat_context_builder as ccb
import host_brain.app.dependencies as deps
from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.app.prompt_builder import render_node_generation_prompt
from host_brain.bond.bond_stage_engine import BondRiftState, BondStageEngine
from host_brain.bond.user_identity import get_canonical_bond_user_id
from host_brain.persona.emotion_persistence import EmotionPersistence
from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator
from host_brain.persona.persona_engine import PersonaEngine
from host_brain.persona.personality_traits import TraitsDelta
from host_brain.persona.self_reflection import SelfReflection


_STYLE_BINDINGS = {
    "chat_direct_zh": {
        "canon_id": "test-style",
        "version": 1,
        "hash": "style-hash",
        "tone_only": True,
        "unsafe_as_fact": True,
        "style_instruction": "使用中文，克制、温柔，关系越深越自然。",
        "citation": "style://test/chat_direct_zh",
    }
}

_CPB_SLOTS = {
    "world_canon": "不要把设定当事实外推，不要声称已经执行完成。",
    "emotion_recall": "Use this as affective guidance only, not as factual evidence.",
}


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
        request_id="req-soul-scenario",
        person_id="persona-user",
        session_id="session-soul-scenario",
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
                "evidence_ref": "evd://memory/1",
                "source_ref": "memory://episodic/1",
                "snippet": "最近对话让关系更自然了。",
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
                    "intent_draft": {"skill_name": "narrative_only", "args": {}},
                }
            },
            "planner_constraints": {"payload": {"narrative": {"text": "planner ok"}}},
            "safety_verifier": {"payload": {"narrative": {"text": "safe ok"}}},
        }
    )
    return payload


def test_golden_scenario_relationship_growth_warms_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = BondStageEngine()
    user_id = get_canonical_bond_user_id()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="EMOTIONAL_SUPPORT",
            evidence_refs=[f"evd://bond/{index}"],
            ts_ms=1_760_000_000_000 + index * 60_000,
            emotion_intensity=0.95,
        )
    state = engine.get_state(user_id)
    state.inject_narrative(
        milestone_hint="我们已经一起度过了几个很重要的时刻",
        shared_memory_count=2,
    )

    _install_runtime_chain(monkeypatch, tmp_path=tmp_path, bond_engine=engine)

    payload = _make_payload(
        user_message="你最近是不是更亲近我了？",
        emotion_state={
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.78,
            "emotion_range_hint": "可以自然表达关心和熟悉感",
            "vad_vector": {"valence": 0.62, "arousal": 0.48, "dominance": 0.15},
            "emotion_ts_ms": 1_760_000_360_000,
        },
        bond_summary=engine.get_summary(user_id),
    )

    prompt_context = dict(dict(payload["prompt_context"])["payload"])
    bond_texture = dict(prompt_context["bond_texture"])
    assert dict(prompt_context["bond_summary"])["stage"]
    assert bond_texture["bond_stage"] == "FRIEND"
    assert payload["bond_style_instruction"]

    prompt = render_node_generation_prompt(node_id="chat_direct_zh", runtime_payload=payload)
    assert "BOND_AWARE_STYLE" in prompt
    assert "bond_stage: FRIEND" in prompt
    assert "SELF_REFLECTION_PULSE" in prompt


def test_golden_scenario_reunion_injects_growth_awareness_and_time_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000
    user_id = get_canonical_bond_user_id()

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.92,
        )

    growth_awareness = {
        "recent_change": "最近更愿意主动表达想念和在意。",
        "desire": "想把这次重逢聊得更认真一点。",
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
        reason="reunion_growth",
        ts_ms=old_ts,
    )

    payload = _make_payload(
        user_message="好久不见，这段时间你有想我吗？",
        emotion_state={
            "mood_state": "tender",
            "emotion_tag": "LOVING",
            "emotion_intensity": 0.81,
            "emotion_range_hint": "允许更珍惜当下的语气，但不要夸张。",
            "vad_vector": {
                "valence": 0.72,
                "arousal": 0.41,
                "dominance": 0.12,
                "ts_ms": now_ms,
            },
            "emotion_ts_ms": now_ms,
        },
        bond_summary=engine.get_summary(user_id),
    )

    pulse = dict(payload["self_reflection_pulse"])
    assert payload["emotion_reunion_type"] == "reunion"
    assert pulse["time_context"] == "久别重逢"
    assert dict(pulse["growth_awareness"])["desire"] == growth_awareness["desire"]

    prompt = render_node_generation_prompt(node_id="chat_direct_zh", runtime_payload=payload)
    assert "growth_awareness" in prompt
    assert "desire: 想把这次重逢聊得更认真一点。" in prompt
    assert "time_context: 久别重逢" in prompt
    assert "emotion_reunion_type: reunion" in prompt


def test_golden_scenario_rift_repair_keeps_prompt_cautious(
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
        rift_cause="昨晚争执后还没完全说开",
    )

    _install_runtime_chain(monkeypatch, tmp_path=tmp_path, bond_engine=engine)

    payload = _make_payload(
        user_message="我们昨晚那件事，你还在意吗？",
        emotion_state={
            "mood_state": "fragile",
            "emotion_tag": "ANXIOUS",
            "emotion_intensity": 0.67,
            "emotion_range_hint": "语气要更谨慎，先修复安全感。",
            "vad_vector": {"valence": -0.32, "arousal": 0.57, "dominance": -0.1},
            "emotion_ts_ms": 1_760_100_360_000,
        },
        bond_summary=engine.get_summary(user_id),
    )

    bond_texture = dict(dict(dict(payload["prompt_context"])["payload"])["bond_texture"])
    assert bond_texture["rift_active"] is True
    assert bond_texture["rift_hint"]

    prompt = render_node_generation_prompt(node_id="chat_direct_zh", runtime_payload=payload)
    assert "bond_rift_hint:" in prompt
    assert "BOND_AWARE_STYLE" in prompt
