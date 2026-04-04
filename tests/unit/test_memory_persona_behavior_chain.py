from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

import host_brain.app.chat_context_builder as ccb
from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.persona.proactive_voice import PersonaProactiveVoice, SCENE_TOPIC_FOLLOWUP
from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy


def _ts_at_hour(hour: int, minute: int = 0) -> int:
    return int(datetime.datetime(2026, 4, 4, hour, minute, 0).timestamp() * 1000)


def test_response_policy_memory_signal_restrains_followup_and_proactive() -> None:
    policy = decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.93, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            motive_state={
                "care_drive": 0.36,
                "continuity_drive": 0.32,
                "withdraw_drive": 0.54,
                "self_protect_drive": 0.76,
            },
            memory_persona_signal={
                "suppressed_memory_count": 1,
                "distorted_memory_count": 1,
                "avg_distortion_level": 0.56,
                "dominant_memory_emotion": "HURT",
                "recall_bias": "guarded",
            },
        )
    ).as_dict()

    assert policy["allow_followup"] is False
    assert policy["allow_proactive_after_reply"] is False
    assert policy["should_be_withdrawn"] is True
    assert policy["withdrawal_guard"] is True
    assert "memory:suppressed=1" in policy["policy_reason_tags"]
    assert any(tag.startswith("memory:fragile=") for tag in policy["policy_reason_tags"])
    assert "memory:protective_guard" in policy["policy_reason_tags"]


def test_response_policy_warm_memory_promotes_warmer_continuity() -> None:
    policy = decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.95, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            motive_state={
                "care_drive": 0.64,
                "continuity_drive": 0.71,
                "withdraw_drive": 0.12,
                "self_protect_drive": 0.08,
            },
            memory_persona_signal={
                "suppressed_memory_count": 0,
                "distorted_memory_count": 0,
                "avg_distortion_level": 0.12,
                "dominant_memory_emotion": "WARM",
                "recall_bias": "warm",
            },
        )
    ).as_dict()

    assert policy["allow_followup"] is True
    assert policy["min_sentences"] >= 2
    assert policy["warmth_floor"] >= 0.22
    assert "memory:warm=WARM" in policy["policy_reason_tags"]


def test_response_policy_recoil_reason_tag_blocks_even_without_fragile_emotion() -> None:
    policy = decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.94, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            motive_state={
                "care_drive": 0.28,
                "continuity_drive": 0.3,
                "withdraw_drive": 0.58,
                "self_protect_drive": 0.74,
            },
            memory_persona_signal={
                "suppressed_memory_count": 0,
                "distorted_memory_count": 0,
                "avg_distortion_level": 0.0,
                "dominant_memory_emotion": "NEUTRAL",
                "recall_bias": "neutral",
                "protective_recoil_count": 1,
            },
        )
    ).as_dict()

    assert policy["allow_followup"] is False
    assert policy["allow_proactive_after_reply"] is False
    assert policy["allow_topic_shift"] is False
    assert "memory:recoil=1" in policy["policy_reason_tags"]


def test_response_policy_resonance_reason_tag_warms_neutral_memory_continuity() -> None:
    policy = decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.97, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            motive_state={
                "care_drive": 0.72,
                "continuity_drive": 0.84,
                "reach_out_drive": 0.46,
                "withdraw_drive": 0.08,
                "self_protect_drive": 0.06,
            },
            memory_persona_signal={
                "suppressed_memory_count": 0,
                "distorted_memory_count": 0,
                "avg_distortion_level": 0.0,
                "dominant_memory_emotion": "NEUTRAL",
                "recall_bias": "neutral",
                "shared_history_resonance_count": 2,
            },
        )
    ).as_dict()

    assert policy["allow_followup"] is True
    assert policy["min_sentences"] >= 2
    assert policy["warmth_floor"] >= 0.26
    assert "memory:resonance=2" in policy["policy_reason_tags"]


def test_chat_context_builder_passes_memory_signal_into_response_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ccb,
        "build_chat_generation_prompt_payload",
        lambda **kwargs: {
            "prompt_context": {
                "authoritative": [],
                "retrieval": [],
                "style_pack": {},
                "constraints": {"retrieval_status": "OK"},
                "memory_emotion_hint": {
                    "mood_state": "steady",
                    "emotion_tag": "SAD",
                    "humanized_memory_count": 2,
                    "suppressed_memory_count": 1,
                    "distorted_memory_count": 1,
                    "avg_distortion_level": 0.48,
                    "dominant_memory_emotion": "HURT",
                    "recall_bias": "guarded",
                },
                "memory_recall_summary": "记忆需要轻触试探。",
            },
            "prompt_context_validation": {"valid": True, "reason_code": None, "violation_count": 0},
            "prompt_context_observability": {"source_stats": {}},
            "emotion_context": {
                "mood_state": "steady",
                "emotion_tag": "SAD",
                "emotion_intensity": 0.75,
                "emotion_range_hint": "needs support",
            },
            "user_message": kwargs["user_message"],
        },
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.build_prompt_intelligence",
        lambda **_kwargs: {},
    )

    class _FakeAdapter:
        def adapt(self, *_args, **_kwargs):  # noqa: ANN001
            hint = SimpleNamespace(
                style_hint="respond naturally",
                tone="gentle",
                echo_weight_modifier=0.0,
                pace_hint="steady",
            )
            setattr(hint, "追问倾向", 0.0)
            return hint

    class _FakePersonaEngine:
        @staticmethod
        def get_instance():
            return SimpleNamespace(get_traits=lambda _user_id: SimpleNamespace())

    class _FakeMotiveState:
        def as_dict(self):
            return {
                "care_drive": 0.42,
                "reach_out_drive": 0.2,
                "continuity_drive": 0.3,
                "withdraw_drive": 0.18,
                "self_protect_drive": 0.22,
                "dominant_motive": "care",
            }

    captured: dict[str, object] = {}

    class _FakePolicy:
        def as_dict(self):
            return {"allow_followup": False, "max_sentences": 2}

    def _fake_decide_response_policy(inp):  # noqa: ANN001
        captured["memory_persona_signal"] = dict(inp.memory_persona_signal or {})
        return _FakePolicy()

    monkeypatch.setattr("host_brain.persona.expression_adapter.PersonaExpressionAdapter", _FakeAdapter)
    monkeypatch.setattr("host_brain.persona.persona_engine.PersonaEngine", _FakePersonaEngine)
    monkeypatch.setattr("host_brain.persona.motive_engine.compute_motive_state", lambda _inp: _FakeMotiveState())
    monkeypatch.setattr("host_brain.persona.response_policy.decide_response_policy", _fake_decide_response_policy)

    runtime_input = build_chat_runtime_input(
        request_id="req-memory-policy-pass",
        person_id="person-memory-policy-pass",
        session_id="session-memory-policy-pass",
        thread_id=None,
        user_message="我又想起那件事了",
        render_target_lang="zh",
        retrieval_summary={"status": "OK"},
        retrieval_evidence_items=[],
        emotion_state={"mood_state": "steady", "emotion_tag": "SAD", "emotion_intensity": 0.75},
        recall_slot_text={},
        bond_summary={},
        scene_type="CHAT",
    )

    ccb.build_chat_prompt_runtime_payload(
        runtime_input=runtime_input,
        style_bindings={},
    )

    assert dict(captured.get("memory_persona_signal") or {})["suppressed_memory_count"] == 1
    assert dict(captured.get("memory_persona_signal") or {})["dominant_memory_emotion"] == "HURT"


def test_proactive_voice_suppressed_memory_blocks_topic_followup() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)

    result = voice.evaluate_scene(
        user_id="memory-guarded-block",
        context={
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "面试", "topic_keywords": ["面试"]}],
            "motive_state": {
                "reach_out_drive": 0.54,
                "care_drive": 0.48,
                "continuity_drive": 0.74,
                "withdraw_drive": 0.32,
                "self_protect_drive": 0.46,
                "dominant_motive": "continuity",
            },
            "memory_persona_signal": {
                "suppressed_memory_count": 1,
                "avg_distortion_level": 0.22,
                "dominant_memory_emotion": "HURT",
                "recall_bias": "guarded",
            },
        },
    )

    assert result is None


def test_proactive_voice_fragile_memory_softens_topic_followup_content() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)

    result = voice.evaluate_scene(
        user_id="memory-fragile-soften",
        context={
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "手术", "topic_keywords": ["手术"]}],
            "motive_state": {
                "reach_out_drive": 0.62,
                "care_drive": 0.84,
                "continuity_drive": 0.78,
                "withdraw_drive": 0.12,
                "self_protect_drive": 0.16,
                "dominant_motive": "care",
            },
            "memory_persona_signal": {
                "suppressed_memory_count": 0,
                "avg_distortion_level": 0.24,
                "dominant_memory_emotion": "ANXIOUS",
                "recall_bias": "guarded",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_TOPIC_FOLLOWUP
    assert "手术" in result.content
    assert "如果你现在愿意聊的话" in result.content
    assert "memory:guarded_soften_followup" in result.motive_influence


def test_proactive_voice_warm_memory_makes_followup_more_natural() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)

    result = voice.evaluate_scene(
        user_id="memory-warm-followup",
        context={
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "搬家", "topic_keywords": ["搬家"]}],
            "motive_state": {
                "reach_out_drive": 0.58,
                "care_drive": 0.66,
                "continuity_drive": 0.82,
                "withdraw_drive": 0.08,
                "self_protect_drive": 0.08,
                "dominant_motive": "continuity",
            },
            "memory_persona_signal": {
                "suppressed_memory_count": 0,
                "avg_distortion_level": 0.12,
                "dominant_memory_emotion": "WARM",
                "recall_bias": "warm",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_TOPIC_FOLLOWUP
    assert "搬家" in result.content
    assert "后来有新进展吗" in result.content
    assert "memory:warm_followup=WARM" in result.motive_influence


def test_proactive_voice_recoil_reason_blocks_topic_followup_without_fragile_emotion() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)

    result = voice.evaluate_scene(
        user_id="memory-recoil-block",
        context={
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "旅行", "topic_keywords": ["旅行"]}],
            "motive_state": {
                "reach_out_drive": 0.52,
                "care_drive": 0.36,
                "continuity_drive": 0.62,
                "withdraw_drive": 0.48,
                "self_protect_drive": 0.72,
                "dominant_motive": "self_protect",
            },
            "memory_persona_signal": {
                "suppressed_memory_count": 0,
                "avg_distortion_level": 0.0,
                "dominant_memory_emotion": "NEUTRAL",
                "recall_bias": "neutral",
                "protective_recoil_count": 1,
            },
        },
    )

    assert result is None


def test_proactive_voice_resonance_reason_enriches_followup_copy_without_warm_emotion() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)

    result = voice.evaluate_scene(
        user_id="memory-resonance-followup",
        context={
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "新工作", "topic_keywords": ["工作"]}],
            "motive_state": {
                "reach_out_drive": 0.6,
                "care_drive": 0.74,
                "continuity_drive": 0.86,
                "withdraw_drive": 0.08,
                "self_protect_drive": 0.08,
                "dominant_motive": "continuity",
            },
            "memory_persona_signal": {
                "suppressed_memory_count": 0,
                "avg_distortion_level": 0.0,
                "dominant_memory_emotion": "NEUTRAL",
                "recall_bias": "neutral",
                "shared_history_resonance_count": 2,
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_TOPIC_FOLLOWUP
    assert "新工作" in result.content
    assert "我还惦记着" in result.content
    assert "memory:resonance_followup=NEUTRAL" in result.motive_influence



def _build_runtime_payload_from_memory_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    memory_signal: dict[str, object],
    motive_state: dict[str, float | str],
) -> dict[str, object]:
    monkeypatch.setattr(
        ccb,
        "build_chat_generation_prompt_payload",
        lambda **kwargs: {
            "prompt_context": {
                "authoritative": [],
                "retrieval": [],
                "style_pack": {},
                "constraints": {"retrieval_status": "OK"},
                "memory_emotion_hint": {
                    "mood_state": "steady",
                    "emotion_tag": "NEUTRAL",
                    "humanized_memory_count": 2,
                    **memory_signal,
                },
                "memory_recall_summary": "固定 recall 包：记忆人格化对照样例。",
            },
            "prompt_context_validation": {"valid": True, "reason_code": None, "violation_count": 0},
            "prompt_context_observability": {"source_stats": {}},
            "emotion_context": {
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "emotion_intensity": 0.42,
                "emotion_range_hint": "steady",
            },
            "user_message": kwargs["user_message"],
        },
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.build_prompt_intelligence",
        lambda **_kwargs: {},
    )

    class _FakeAdapter:
        def adapt(self, *_args, **_kwargs):  # noqa: ANN001
            hint = SimpleNamespace(
                style_hint="respond naturally",
                tone="gentle",
                echo_weight_modifier=0.0,
                pace_hint="steady",
            )
            setattr(hint, "追问倾向", 0.0)
            return hint

    class _FakePersonaEngine:
        @staticmethod
        def get_instance():
            return SimpleNamespace(get_traits=lambda _user_id: SimpleNamespace())

    class _FakeMotiveState:
        def __init__(self, payload: dict[str, float | str]) -> None:
            self._payload = dict(payload)

        def as_dict(self):
            return dict(self._payload)

    monkeypatch.setattr("host_brain.persona.expression_adapter.PersonaExpressionAdapter", _FakeAdapter)
    monkeypatch.setattr("host_brain.persona.persona_engine.PersonaEngine", _FakePersonaEngine)
    monkeypatch.setattr(
        "host_brain.persona.motive_engine.compute_motive_state",
        lambda _inp: _FakeMotiveState(motive_state),
    )

    runtime_input = build_chat_runtime_input(
        request_id="req-memory-matrix",
        person_id="person-memory-matrix",
        session_id="session-memory-matrix",
        thread_id=None,
        user_message="我们继续说刚才那件事吧",
        render_target_lang="zh",
        retrieval_summary={"status": "OK"},
        retrieval_evidence_items=[],
        emotion_state={"mood_state": "steady", "emotion_tag": "NEUTRAL", "emotion_intensity": 0.42},
        recall_slot_text={},
        bond_summary={"stage": "FRIEND"},
        scene_type="CHAT",
    )
    return ccb.build_chat_prompt_runtime_payload(runtime_input=runtime_input, style_bindings={})


def _build_runtime_payload_from_recall_pack(
    monkeypatch: pytest.MonkeyPatch,
    *,
    retrieval_evidence_items: list[dict[str, object]],
    motive_state: dict[str, float | str],
) -> dict[str, object]:
    import host_brain.app.dependencies as deps
    import host_brain.app.emotion_context as emotion_context

    normalized_emotion = {
        "mood_state": "steady",
        "emotion_tag": "NEUTRAL",
        "emotion_range_hint": "steady",
        "allowed_emotion_tags": ["NEUTRAL", "WARM"],
        "emotion_intensity": 0.42,
    }
    monkeypatch.setattr(emotion_context, "normalize_chat_emotion_context", lambda raw: normalized_emotion)
    monkeypatch.setattr(deps, "get_bond_stage_engine", lambda: None)
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.build_prompt_intelligence",
        lambda **_kwargs: {},
    )

    class _FakeAdapter:
        def adapt(self, *_args, **_kwargs):  # noqa: ANN001
            hint = SimpleNamespace(
                style_hint="respond naturally",
                tone="gentle",
                echo_weight_modifier=0.0,
                pace_hint="steady",
            )
            setattr(hint, "追问倾向", 0.0)
            return hint

    class _FakePersonaEngine:
        @staticmethod
        def get_instance():
            return SimpleNamespace(get_traits=lambda _user_id: SimpleNamespace())

    class _FakeMotiveState:
        def __init__(self, payload: dict[str, float | str]) -> None:
            self._payload = dict(payload)

        def as_dict(self):
            return dict(self._payload)

    monkeypatch.setattr("host_brain.persona.expression_adapter.PersonaExpressionAdapter", _FakeAdapter)
    monkeypatch.setattr("host_brain.persona.persona_engine.PersonaEngine", _FakePersonaEngine)
    monkeypatch.setattr(
        "host_brain.persona.motive_engine.compute_motive_state",
        lambda _inp: _FakeMotiveState(motive_state),
    )

    runtime_input = build_chat_runtime_input(
        request_id="req-memory-e2e",
        person_id="person-memory-e2e",
        session_id="session-memory-e2e",
        thread_id=None,
        user_message="我们继续刚才的话题吧",
        render_target_lang="zh",
        retrieval_summary={"status": "OK", "reason_code": "NONE"},
        retrieval_evidence_items=retrieval_evidence_items,
        emotion_state={"mood_state": "steady", "emotion_tag": "NEUTRAL", "emotion_intensity": 0.42},
        recall_slot_text={},
        bond_summary={"stage": "FRIEND"},
        scene_type="CHAT",
    )
    return ccb.build_chat_prompt_runtime_payload(runtime_input=runtime_input, style_bindings={})


@pytest.mark.parametrize(
    ("case_name", "memory_signal", "motive_state", "expectations"),
    [
        (
            "warm_continuity",
            {
                "suppressed_memory_count": 0,
                "distorted_memory_count": 0,
                "avg_distortion_level": 0.12,
                "dominant_memory_emotion": "WARM",
                "recall_bias": "warm",
            },
            {
                "care_drive": 0.68,
                "reach_out_drive": 0.44,
                "continuity_drive": 0.74,
                "withdraw_drive": 0.08,
                "self_protect_drive": 0.06,
                "dominant_motive": "continuity",
            },
            {
                "allow_followup": True,
                "allow_proactive_after_reply": True,
                "allow_topic_shift": True,
                "reason_tag": "memory:warm=WARM",
                "tone": "caring",
                "followup_floor": 0.12,
                "guidance_keyword": "温暖",
            },
        ),
        (
            "suppressed_pain",
            {
                "suppressed_memory_count": 1,
                "distorted_memory_count": 1,
                "avg_distortion_level": 0.52,
                "dominant_memory_emotion": "HURT",
                "recall_bias": "guarded",
            },
            {
                "care_drive": 0.34,
                "reach_out_drive": 0.22,
                "continuity_drive": 0.28,
                "withdraw_drive": 0.48,
                "self_protect_drive": 0.82,
                "dominant_motive": "self_protect",
            },
            {
                "allow_followup": False,
                "allow_proactive_after_reply": False,
                "allow_topic_shift": False,
                "reason_tag": "memory:protective_guard",
                "tone": "gentle",
                "followup_ceiling": 0.03,
                "guidance_keyword": "轻触试探",
            },
        ),
        (
            "distorted_fragile",
            {
                "suppressed_memory_count": 0,
                "distorted_memory_count": 1,
                "avg_distortion_level": 0.61,
                "dominant_memory_emotion": "ANXIOUS",
                "recall_bias": "uncertain",
            },
            {
                "care_drive": 0.46,
                "reach_out_drive": 0.26,
                "continuity_drive": 0.4,
                "withdraw_drive": 0.24,
                "self_protect_drive": 0.28,
                "dominant_motive": "care",
            },
            {
                "allow_followup": False,
                "allow_proactive_after_reply": False,
                "allow_topic_shift": False,
                "reason_tag": "memory:distortion=0.61",
                "tone": "gentle",
                "followup_ceiling": 0.02,
                "guidance_keyword": "模糊",
            },
        ),
    ],
)
def test_memory_persona_reply_runtime_regression_matrix(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    memory_signal: dict[str, object],
    motive_state: dict[str, float | str],
    expectations: dict[str, object],
) -> None:
    runtime_payload = _build_runtime_payload_from_memory_case(
        monkeypatch,
        memory_signal=memory_signal,
        motive_state=motive_state,
    )

    response_policy = dict(runtime_payload.get("response_policy") or {})
    hint = dict(runtime_payload.get("persona_expression_hint") or {})
    memory_summary = str(runtime_payload.get("memory_recall_summary") or "")

    assert response_policy["allow_followup"] is expectations["allow_followup"], case_name
    assert response_policy["allow_proactive_after_reply"] is expectations["allow_proactive_after_reply"], case_name
    assert response_policy["allow_topic_shift"] is expectations["allow_topic_shift"], case_name
    assert expectations["reason_tag"] in response_policy["policy_reason_tags"], case_name
    assert hint["tone"] == expectations["tone"], case_name
    assert expectations["guidance_keyword"] in str(hint.get("memory_guidance") or ""), case_name
    assert memory_summary == "固定 recall 包：记忆人格化对照样例。", case_name

    follow_up_tendency = float(hint.get("follow_up_tendency") or 0.0)
    if "followup_floor" in expectations:
        assert follow_up_tendency >= float(expectations["followup_floor"]), case_name
    if "followup_ceiling" in expectations:
        assert follow_up_tendency <= float(expectations["followup_ceiling"]), case_name


def test_fixed_recall_pack_end_to_end_recoil_signal_reaches_response_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_payload = _build_runtime_payload_from_recall_pack(
        monkeypatch,
        retrieval_evidence_items=[
            {
                "snippet": "I should not reopen that part too abruptly.",
                "metadata": {
                    "persona_recall_reason_tags": ["protective_recoil_penalty"],
                },
            }
        ],
        motive_state={
            "care_drive": 0.28,
            "reach_out_drive": 0.24,
            "continuity_drive": 0.3,
            "withdraw_drive": 0.58,
            "self_protect_drive": 0.76,
            "dominant_motive": "self_protect",
        },
    )

    memory_signal = dict(runtime_payload.get("memory_persona_signal") or {})
    response_policy = dict(runtime_payload.get("response_policy") or {})

    assert memory_signal["protective_recoil_count"] == 1
    assert "防御性回缩" in str(runtime_payload.get("memory_recall_summary") or "")
    assert response_policy["allow_followup"] is False
    assert "memory:recoil=1" in response_policy["policy_reason_tags"]


def test_fixed_recall_pack_end_to_end_resonance_signal_reaches_proactive_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_payload = _build_runtime_payload_from_recall_pack(
        monkeypatch,
        retrieval_evidence_items=[
            {
                "snippet": "I still remember what we said last time together.",
                "metadata": {
                    "persona_recall_reason_tags": ["shared_history_resonance"],
                },
            }
        ],
        motive_state={
            "care_drive": 0.72,
            "reach_out_drive": 0.58,
            "continuity_drive": 0.84,
            "withdraw_drive": 0.08,
            "self_protect_drive": 0.08,
            "dominant_motive": "continuity",
        },
    )

    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)
    result = voice.evaluate_scene(
        user_id="memory-e2e-resonance",
        context={
            **runtime_payload,
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "新工作", "topic_keywords": ["工作"]}],
        },
    )

    assert dict(runtime_payload.get("memory_persona_signal") or {})["shared_history_resonance_count"] == 1
    assert result is not None
    assert result.scene_type == SCENE_TOPIC_FOLLOWUP
    assert "我还惦记着" in result.content
    assert "memory:resonance_followup=NEUTRAL" in result.motive_influence
