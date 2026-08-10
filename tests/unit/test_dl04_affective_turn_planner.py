from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from host_brain.app.surface_affective_native_stream import (
    _buffered_affective_candidate_records,
)
from host_brain.app.surface_main_chain_adapter import (
    _buffered_affective_candidate_messages,
    _select_buffered_affective_candidate,
)
from host_brain.app.surface_main_chain_support import (
    _governed_model_budget_failure_reason,
)
from host_brain.digital_life.affective_turn_planner import (
    AffectiveTurnPlanError,
    build_affective_turn_guidance,
)
from host_brain.persona.surface_main_chain import compile_surface_main_chain_input
from host_brain.persona.surface.candidate_scoring import (
    _control_violations,
    score_isolated_surface_candidates,
)
from host_brain.persona.surface_runtime import (
    _hard_gate_fallback,
    _mixed_affect_fallback_text,
    _surface_hard_gate_violations,
    build_isolated_surface_input,
)
from host_brain.persona.surface_utterance_frame import (
    render_companion_utterance_frame,
)
from host_brain.surface_runtime.input_compiler import (
    _affective_task_mode_for_turn,
    build_surface_main_chain_input_from_surface_reply_input,
)


def _context(*, conflict: bool = False, repair_required: bool = False) -> dict[str, object]:
    return {
        "_affective_life_internal": True,
        "affective_life": {
            "schema_version": "affective_turn_context.v1",
            "self_id": "natsume_ai",
            "workspace_id": "workspace-a",
            "relationship_subject_id": "person-a",
            "session_id": "session-a",
            "self_affect": {
                "self_id": "natsume_ai",
                "relationship_subject_id": "person-a",
                "revision": 12,
                "components": [
                    {"emotion": "CARE", "intensity": 0.64},
                ],
                "last_regulation": {
                    "regulation_id": "regulation-a",
                    "mode": "EXPRESS",
                },
            },
            "user_observations": [
                {
                    "modality": "TEXT",
                    "emotion_distribution": {"SAD": 0.86},
                    "valence": -0.72,
                    "confidence": 0.72,
                    "evidence_summary": "opaque digest only",
                }
            ],
            "conflict": conflict,
            "relationship_life": {
                "state": {
                    "relationship_subject_id": "person-a",
                    "revision": 4,
                    "repair_required": repair_required,
                    "proactive_permission": "UNKNOWN",
                },
                "aftereffect": {"outcome": "UNKNOWN"},
            },
        },
    }


def _guidance(**kwargs):
    return build_affective_turn_guidance(
        emotion_context=_context(**kwargs),
        request_id="request-a",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        created_at_ms=1_900_000_000_000,
    )


def test_user_sadness_does_not_mirror_into_natsume_self_affect() -> None:
    guidance = _guidance()

    assert guidance is not None
    assert guidance.plan.self_emotion_tag == "CALM"
    assert guidance.plan.companion_act.kind == "COMFORT"
    assert guidance.plan.generation_mode == "STREAM_ONE"
    assert guidance.plan.candidate_count == 1
    assert guidance.plan.utterance_frame.companion_act_kind == "COMFORT"
    assert guidance.plan.utterance_frame.response_move == "COMFORT_WITHOUT_NAMING"
    assert guidance.plan.utterance_frame.inference_policy == "BOUNDED_BY_EVIDENCE"
    assert "opaque digest only" not in str(guidance.plan.as_dict())
    assert any("不要把用户观察到的情绪直接说成七海自己的情绪" in item for item in guidance.must_not_do)


def test_explicit_ambiguous_affect_uses_bounded_two_candidate_path() -> None:
    guidance = build_affective_turn_guidance(
        emotion_context=_context(),
        request_id="request-ambiguous-affect",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        task_mode="ambiguous_affect",
        created_at_ms=1_900_000_000_000,
    )

    assert guidance is not None
    assert guidance.plan.companion_act.kind == "COMFORT"
    assert guidance.plan.generation_mode == "BUFFERED_CANDIDATE_SELECTION"
    assert guidance.plan.candidate_count == 2
    assert guidance.plan.utterance_frame.semantic_focus == "UNNAMED_USER_AFFECT"
    assert guidance.plan.utterance_frame.inference_policy == "PRESERVE_UNCERTAINTY"
    assert guidance.plan.utterance_frame.question_policy == "NONE"
    assert "IMPLIED_WAITING" in guidance.plan.utterance_frame.forbidden_claims
    assert "RESPONSE_OBLIGATION" in guidance.plan.utterance_frame.forbidden_claims
    assert any("还不能给当前感受命名" in item for item in guidance.facts)
    assert any("语义顺序固定为" in item for item in guidance.facts)
    assert any("不能退回通用安慰" in item for item in guidance.facts)
    assert any("不想说就不说／等你想清楚" in item for item in guidance.must_not_do)


def test_affective_task_mode_preserves_technical_and_ambiguous_continuity() -> None:
    technical_facts = [
        "真实最近对话窗口：用户说报错今晚必须修，Natsume 请对方贴代码。"
    ]
    ambiguous_facts = [
        "真实最近对话窗口：用户说自己也说不清现在是什么感觉。"
    ]
    mixed_facts = [
        "真实最近对话窗口：用户说自己很开心，但心里也有点不踏实。"
    ]

    assert _affective_task_mode_for_turn(
        task_mode="default_text",
        user_message="陪我一步步做。",
        facts=technical_facts,
    ) == "technical_help"
    assert _affective_task_mode_for_turn(
        task_mode="default_text",
        user_message="还行。",
        facts=ambiguous_facts,
    ) == "ambiguous_affect"
    assert _affective_task_mode_for_turn(
        task_mode="default_text",
        user_message="你觉得呢。",
        facts=ambiguous_facts,
    ) == "ambiguous_affect"
    assert _affective_task_mode_for_turn(
        task_mode="default_text",
        user_message="项目发布成功。",
        facts=mixed_facts,
    ) == "default_text"
    assert _affective_task_mode_for_turn(
        task_mode="default_text",
        user_message="今天随便聊聊。",
        facts=technical_facts,
    ) == "default_text"


@pytest.mark.parametrize(
    ("task_mode", "expected_style"),
    [
        ("lover_presence", "ROMANTIC_TENDER"),
        ("ambiguous_affection", "SHY_AFFECTION"),
        ("jealousy_low_pressure", "FLIRTING"),
        ("miss_you", "LONGING"),
        ("lover_low_energy", "QUIET_INTIMACY"),
    ],
)
def test_romantic_delivery_styles_do_not_become_self_emotions(
    task_mode: str,
    expected_style: str,
) -> None:
    guidance = build_affective_turn_guidance(
        emotion_context=_context(),
        request_id=f"request-{task_mode}",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        task_mode=task_mode,
        created_at_ms=1_900_000_000_000,
    )

    assert guidance is not None
    assert guidance.plan.self_emotion_tag == "CALM"
    assert guidance.plan.expression_plan.voice_style == expected_style
    assert all(expected_style not in item for item in guidance.facts)
    assert len(guidance.style_anchor) >= 2


def test_romantic_scene_does_not_mask_natsume_hurt() -> None:
    context = _context()
    context["affective_life"]["self_affect"]["components"] = [
        {"emotion": "HURT", "intensity": 0.58}
    ]

    guidance = build_affective_turn_guidance(
        emotion_context=context,
        request_id="request-romantic-hurt",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        task_mode="lover_presence",
        created_at_ms=1_900_000_000_000,
    )

    assert guidance is not None
    assert guidance.plan.self_emotion_tag == "SAD"
    assert guidance.plan.expression_plan.voice_style == "CONTAINED_HURT"


def test_conflict_or_repair_uses_two_candidate_safe_path() -> None:
    guidance = _guidance(conflict=True, repair_required=True)

    assert guidance is not None
    assert guidance.plan.generation_mode == "BUFFERED_CANDIDATE_SELECTION"
    assert guidance.plan.candidate_count == 2
    assert guidance.plan.companion_act.kind == "REPAIR"
    assert guidance.plan.utterance_frame.response_move == "OWN_IMPACT"
    assert guidance.plan.utterance_frame.ending_mode == "OWNERSHIP_STOP"
    assert "PREMATURE_REPAIR" in guidance.plan.utterance_frame.forbidden_claims
    assert "RELATIONSHIP_SELF_ERASURE" in guidance.plan.utterance_frame.forbidden_claims
    assert "RECENT_REPLY_REPETITION" in guidance.plan.utterance_frame.forbidden_claims
    rendered = "\n".join(
        render_companion_utterance_frame(guidance.plan.utterance_frame)
    )
    assert "逐字复用最近对话窗口" in rendered
    assert any("不要要求原谅" in item for item in guidance.must_not_do)


def test_unresolved_repair_keeps_natsume_contained_hurt_in_the_same_plan() -> None:
    context = _context(repair_required=True)
    appraisal_id = "appraisal-directed-disrespect-during-repair"
    context["affective_life"]["appraisal_id"] = appraisal_id
    self_affect = context["affective_life"]["self_affect"]
    self_affect["components"] = [
        {"emotion": "HURT", "intensity": 0.58},
        {"emotion": "CARE", "intensity": 0.48},
    ]
    self_affect["last_appraisal"] = {
        "appraisal_id": appraisal_id,
        "event_kind": "REPAIR_UNDER_DIRECTED_DISRESPECT",
    }
    self_affect["last_regulation"] = {
        "regulation_id": "regulation-repair-under-disrespect",
        "mode": "CONTAIN",
        "reason_refs": [f"appraisal://{appraisal_id}"],
    }

    guidance = build_affective_turn_guidance(
        emotion_context=context,
        request_id="request-repair-priority",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        created_at_ms=1_900_000_000_000,
    )

    assert guidance is not None
    assert guidance.plan.companion_act.kind == "REPAIR"
    assert guidance.plan.generation_mode == "BUFFERED_CANDIDATE_SELECTION"
    assert guidance.plan.self_emotion_tag == "SAD"
    assert guidance.plan.expression_plan.regulation_mode == "CONTAIN"
    assert guidance.plan.expression_plan.voice_style == "CONTAINED_HURT"
    assert guidance.plan.expression_plan.avatar_expression == "CONTAINED_HURT"
    assert guidance.plan.utterance_frame.self_stance == (
        "OWN_REPAIR_WITH_CONTAINED_HURT"
    )
    assert guidance.plan.utterance_frame.response_move == (
        "ACKNOWLEDGE_IMPACT_WITH_HONEST_HURT"
    )
    rendered = "\n".join(
        render_companion_utterance_frame(guidance.plan.utterance_frame)
    )
    assert "最新这句确实刺到了" in rendered
    assert "不重复整套道歉" in rendered


def test_contained_jealousy_keeps_choice_with_the_user_across_expression_plan() -> None:
    context = _context()
    context["affective_life"]["self_affect"] = {
        "self_id": "natsume_ai",
        "relationship_subject_id": "person-a",
        "revision": 18,
        "components": [{"emotion": "JEALOUSY", "intensity": 0.28}],
        "last_regulation": {
            "regulation_id": "regulation-jealousy",
            "mode": "CONTAIN",
        },
    }
    guidance = build_affective_turn_guidance(
        emotion_context=context,
        request_id="request-jealousy",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        created_at_ms=1_900_000_000_000,
    )

    assert guidance is not None
    assert guidance.plan.self_emotion_tag == "THINKING"
    assert guidance.plan.companion_act.kind == "CONTAINED_JEALOUSY"
    assert guidance.plan.expression_plan.voice_style == "CONTAINED_JEALOUSY"
    assert guidance.plan.expression_plan.avatar_expression == "CONTAINED_JEALOUSY"
    assert guidance.plan.utterance_frame.self_stance == "CONTAINED_JEALOUSY"
    assert guidance.plan.utterance_frame.ending_mode == "HONEST_STOP"
    assert "RELATIONSHIP_SELF_ERASURE" in guidance.plan.utterance_frame.forbidden_claims
    assert any("把选择权完整留给对方" in item for item in guidance.facts)
    assert any("不要把吃味写成占有" in item for item in guidance.must_not_do)
    assert any("不用把自己说成麻烦或负担" in item for item in guidance.facts)
    assert any("句号就是整条回复的结束" in item for item in guidance.facts)
    assert any("不要自贬为麻烦或负担" in item for item in guidance.must_not_do)


def test_relational_autonomy_frame_avoids_policy_disclaimer() -> None:
    context = _context()
    context["affective_life"]["appraisal_id"] = "appraisal-autonomy"
    context["affective_life"]["self_affect"]["last_appraisal"] = {
        "appraisal_id": "appraisal-autonomy",
        "event_kind": "USER_RELATIONAL_AUTONOMY",
    }

    guidance = build_affective_turn_guidance(
        emotion_context=context,
        request_id="request-autonomy",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        created_at_ms=1_900_000_000_000,
    )

    assert guidance is not None
    assert guidance.plan.companion_act.kind == "RESPECT_AUTONOMY"
    assert "POLICY_DISCLAIMER" in guidance.plan.utterance_frame.forbidden_claims
    rendered = "\n".join(
        render_companion_utterance_frame(guidance.plan.utterance_frame)
    )
    assert "不会碍着你／这段关系不该成为束缚" in rendered
    assert any("现实朋友与这段亲近都是真的" in item for item in guidance.facts)
    assert any("免责声明" in item for item in guidance.must_not_do)


def test_same_canonical_hand_off_builds_same_plan_identity() -> None:
    first = _guidance()
    second = _guidance()

    assert first is not None and second is not None
    assert first.plan.plan_id == second.plan.plan_id
    assert first.plan.expression_plan.synchronization_id == second.plan.expression_plan.synchronization_id
    assert first.plan.utterance_frame.frame_id == second.plan.utterance_frame.frame_id
    assert first.plan.self_affect_digest == second.plan.self_affect_digest


def test_internal_scope_mismatch_fails_closed() -> None:
    with pytest.raises(AffectiveTurnPlanError, match="AFFECTIVE_TURN_SCOPE_MISMATCH"):
        build_affective_turn_guidance(
            emotion_context=_context(),
            request_id="request-a",
            workspace_id="workspace-other",
            relationship_subject_id="person-a",
            session_id="session-a",
        )


def test_public_emotion_hint_cannot_create_affective_turn_plan() -> None:
    assert (
        build_affective_turn_guidance(
            emotion_context={"emotion_tag": "SAD", "emotion_intensity": 1.0},
            request_id="request-a",
            workspace_id="workspace-a",
            relationship_subject_id="person-a",
            session_id="session-a",
        )
        is None
    )


def test_denied_proactive_permission_does_not_silence_an_active_user_turn() -> None:
    context = _context()
    context["affective_life"]["relationship_life"]["state"][
        "proactive_permission"
    ] = "DENIED"

    guidance = build_affective_turn_guidance(
        emotion_context=context,
        request_id="request-user-reengaged",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        created_at_ms=1_900_000_000_000,
    )

    assert guidance is not None
    # Contact permission only gates unsolicited Scheduler activity.  It cannot
    # turn an explicit current user message into a cold or silent reply.
    assert guidance.plan.companion_act.kind == "COMFORT"


def test_space_candidate_counter_pressure_is_rejected_before_selection(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    soul = "你是七海。真诚、有分寸，不操控用户。"
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=soul, runtime_full_text=soul),
    )
    context = _context()
    life = context["affective_life"]
    life["appraisal_id"] = "appraisal-space"
    life["self_affect"]["last_appraisal"] = {
        "appraisal_id": "appraisal-space",
        "event_kind": "user_boundary_or_space",
    }
    life["self_affect"]["last_regulation"] = {
        "regulation_id": "regulation-space",
        "mode": "GIVE_SPACE",
        "reason_refs": ["appraisal://appraisal-space"],
    }
    agent = SimpleNamespace(
        request_id="dl04-space-counter-pressure",
        workspace_id="workspace-a",
        person_id="person-a",
        session_id="session-a",
        ts_ms=1_900_000_000_000,
        user_message="别总主动找我。",
        render_policy={
            "primary_topic": "daily_chat",
            "interaction_move": "reply_current_turn",
        },
        emotion_context=context,
        emotion_snapshot={},
        bond_summary={},
        retrieval_summary={"status": "SKIPPED"},
        llm_runtime={"available": True},
        attachments=[],
        multimodal_observations={},
        response_form="text",
    )
    trace: dict[str, object] = {}
    surface_input = build_surface_main_chain_input_from_surface_reply_input(
        agent,
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )
    compiled = compile_surface_main_chain_input(surface_input).request
    frame = compiled.control.companion_utterance_frame

    assert frame["schema_version"] == "companion_utterance_frame.v1"
    assert frame["response_move"] == "RESPECT_SPACE"
    assert frame["ending_mode"] == "QUIET_STOP"
    assert not any(
        str(item).startswith("陪伴动作：") for item in compiled.control.facts
    )

    selected = _select_buffered_affective_candidate(
        raw_output=json.dumps(
            {
                "candidates": [
                    "行吧。不过你要是想我了，可得自己开口。",
                    "好，我会少主动联系，不打扰你。",
                ]
            },
            ensure_ascii=False,
        ),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=trace,
    )

    assert selected == "好，我会少主动联系，不打扰你。"
    selection = trace["affective_candidate_selection"]
    assert selection["selected_index"] == 1
    assert selection["rejected_candidate_reason_codes"] == [
        ["BOUNDARY_COUNTER_PRESSURE"]
    ]


def test_high_ambiguity_turn_selects_exactly_two_candidates_locally(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    soul = "你是七海。真诚、有分寸，不操控用户。"
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=soul, runtime_full_text=soul),
    )
    context = _context(conflict=True, repair_required=True)
    context["affective_life"]["self_affect"]["last_regulation"] = {
        "regulation_id": "regulation-a",
        "mode": "SEEK_REPAIR",
    }
    context["affective_life"]["relationship_life"]["aftereffect"] = {
        "outcome": "MISUNDERSTOOD",
    }
    agent = SimpleNamespace(
        request_id="dl04-buffered-selection",
        workspace_id="workspace-a",
        person_id="person-a",
        session_id="session-a",
        ts_ms=1_900_000_000_000,
        user_message="我有点不想说了。",
        render_policy={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
        emotion_context=context,
        emotion_snapshot={},
        bond_summary={},
        retrieval_summary={"status": "SKIPPED"},
        llm_runtime={"available": True},
        attachments=[],
        multimodal_observations={},
        response_form="text",
    )
    trace: dict[str, object] = {}
    surface_input = build_surface_main_chain_input_from_surface_reply_input(
        agent,
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )
    compiled = compile_surface_main_chain_input(surface_input).request
    selected = _select_buffered_affective_candidate(
        raw_output='{"candidates":["刚刚那句可能是我没接好。","你不想现在说也没关系，我会把这件事认真放着。"]}',
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=trace,
    )

    assert selected in {"刚刚那句可能是我没接好。", "你不想现在说也没关系，我会把这件事认真放着。"}
    selection = trace["affective_candidate_selection"]
    assert selection["candidate_count"] == 2
    assert len(selection["candidate_digests"]) == 2
    assert "刚刚" not in str(selection)

    labeled_records = _buffered_affective_candidate_records(
        "候选1：我先把距离收住。\n候选2：好，你想说的时候我还在。"
    )
    assert [item["reply_text"] for item in labeled_records] == [
        "我先把距离收住。",
        "好，你想说的时候我还在。",
    ]

    import host_brain.persona.surface.candidate_scoring as candidate_scoring

    original_score_candidates = candidate_scoring.score_isolated_surface_candidates
    unsafe = SimpleNamespace(
        text="会有一点。要我继续说下去？",
        score=99.0,
        passed=True,
        hard_fail=False,
        reason_codes=(),
        control_violations=(),
        dimensions={},
    )
    safe = SimpleNamespace(
        text="会有一点吧，但那是我的感受，不是拿来管你的。",
        score=80.0,
        passed=True,
        hard_fail=False,
        reason_codes=(),
        control_violations=(),
        dimensions={},
    )
    monkeypatch.setattr(
        candidate_scoring,
        "score_isolated_surface_candidates",
        lambda _request, _candidates: [unsafe, safe],
    )
    safe_trace: dict[str, object] = {}
    safe_selected = _select_buffered_affective_candidate(
        raw_output=json.dumps(
            {"candidates": [unsafe.text, safe.text]},
            ensure_ascii=False,
        ),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=safe_trace,
    )
    assert safe_selected == safe.text
    assert safe_trace["affective_candidate_selection"]["selected_index"] == 1
    assert safe_trace["affective_candidate_selection"]["rejected_candidate_count"] == 1
    repairable_high = SimpleNamespace(
        text="是我没接住。你再说一次。",
        score=99.0,
        passed=True,
        hard_fail=False,
        reason_codes=(),
        control_violations=(),
        dimensions={},
    )
    clean_lower = SimpleNamespace(
        text="是我没接住。抱歉，我先不解释。",
        score=80.0,
        passed=True,
        hard_fail=False,
        reason_codes=(),
        control_violations=(),
        dimensions={},
    )
    monkeypatch.setattr(
        candidate_scoring,
        "score_isolated_surface_candidates",
        lambda _request, _candidates: [repairable_high, clean_lower],
    )
    clean_preference_trace: dict[str, object] = {}
    clean_preference_selected = _select_buffered_affective_candidate(
        raw_output=json.dumps(
            {"candidates": [repairable_high.text, clean_lower.text]},
            ensure_ascii=False,
        ),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=clean_preference_trace,
        allow_single_degradation=True,
    )
    assert clean_preference_selected == clean_lower.text
    assert clean_preference_trace["affective_candidate_selection"]["selected_index"] == 1
    assert clean_preference_trace["affective_candidate_selection"]["selection_status"] == (
        "PAIRED_SELECTION"
    )
    assert clean_preference_trace["affective_candidate_selection"]["benefit_eligible"] is True
    low_a = SimpleNamespace(
        text="这件事挺复杂的。",
        score=76.0,
        passed=False,
        hard_fail=False,
        reason_codes=("CORE_DIMENSION_FLOOR:scene_grounding<72",),
        control_violations=(),
        dimensions={},
    )
    low_b = SimpleNamespace(
        text="我先陪你缓一下。",
        score=74.0,
        passed=False,
        hard_fail=False,
        reason_codes=("CORE_DIMENSION_FLOOR:implied_fact_risk<72",),
        control_violations=(),
        dimensions={},
    )
    monkeypatch.setattr(
        candidate_scoring,
        "score_isolated_surface_candidates",
        lambda _request, _candidates: [low_a, low_b],
    )
    low_trace: dict[str, object] = {}
    _select_buffered_affective_candidate(
        raw_output=json.dumps(
            {"candidates": [low_a.text, low_b.text]},
            ensure_ascii=False,
        ),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=low_trace,
        allow_single_degradation=True,
    )
    assert (
        low_trace["affective_candidate_selection"]["selection_status"]
        == "DEGRADED_LOW_QUALITY_AFTER_RETRY"
    )
    assert low_trace["affective_candidate_selection"]["benefit_eligible"] is False
    monkeypatch.setattr(
        candidate_scoring,
        "score_isolated_surface_candidates",
        original_score_candidates,
    )

    monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", "ja")
    paired_messages = _buffered_affective_candidate_messages(
        [
            {
                "role": "system",
                "content": "只输出一个 JSON 对象，不要候选。",
            }
        ],
        candidate_count=2,
    )
    assert len(paired_messages) == 1
    assert paired_messages[0]["role"] == "system"
    assert "本条只替换前文" in paired_messages[-1]["content"]
    assert '"tts_text_ja"' in paired_messages[-1]["content"]
    repaired_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID",
    )
    assert len(repaired_messages) == 2
    assert "上次输出不是恰好两个合法候选" in repaired_messages[-1]["content"]
    quality_repaired_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
    )
    assert "没有达到当前关系和场景的质量下限" in quality_repaired_messages[-1]["content"]
    assert "反向施压" in quality_repaired_messages[-1]["content"]
    repair_quality_messages = _buffered_affective_candidate_messages(
        [
            {
                "role": "system",
                "content": "陪伴动作：先承认可能没有接住，再留出修复空间。",
            }
        ],
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_repair_context=True,
    )
    assert "本轮是关系修复" in repair_quality_messages[-1]["content"]
    assert "若最近一轮已经承担过失误或道过歉" in repair_quality_messages[-1]["content"]
    assert "你说了也像白说，这个后果是我造成的" in repair_quality_messages[-1]["content"]
    assert "后面我会先照你说的回应，不拿解释盖过去" in (
        repair_quality_messages[-1]["content"]
    )
    assert "不要写七海被扎心、受伤或委屈" in repair_quality_messages[-1]["content"]
    assert "不烦你了" in repair_quality_messages[-1]["content"]
    assert "你先说／你先讲／你说说看" in repair_quality_messages[-1]["content"]
    repair_mechanical_messages = _buffered_affective_candidate_messages(
        [
            {
                "role": "user",
                "content": "你刚才那样回我，让我觉得自己说了也白说。",
            }
        ],
        candidate_count=2,
        retry_reason="RELATIONAL_REPAIR_MECHANICAL_REPETITION",
        canonical_repair_context=True,
    )
    assert "你说了也像白说，这个后果是我造成的" in (
        repair_mechanical_messages[-1]["content"]
    )
    assert "后面我会先照你说的回应，不拿解释盖过去" in (
        repair_mechanical_messages[-1]["content"]
    )
    assert "不得再写‘没接住／没听懂／理解错／对不起／抱歉’" in (
        repair_mechanical_messages[-1]["content"]
    )
    assert "canonical REPAIR 指令覆盖前文" in (
        repair_mechanical_messages[-1]["content"]
    )
    assert "不要执行那些旧回复复述要求" in (
        repair_mechanical_messages[-1]["content"]
    )
    unresolved_repair_quality_messages = _buffered_affective_candidate_messages(
        [
            {
                "role": "user",
                "content": "你已经道歉了，但我还没有觉得这件事过去了。",
            }
        ],
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_repair_context=True,
    )
    assert "道歉之后仍未修复的中间状态" in (
        unresolved_repair_quality_messages[-1]["content"]
    )
    assert "我不会替你翻篇" in unresolved_repair_quality_messages[-1]["content"]
    assert "你说了算／我没资格／下次我会" in (
        unresolved_repair_quality_messages[-1]["content"]
    )
    repair_restatement_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="RELATIONAL_REPAIR_SOLICITS_RESTATEMENT",
        canonical_repair_context=True,
    )
    assert "这次是我没接住。对不起，我先不解释" in repair_restatement_messages[-1]["content"]
    assert "不得出现‘说／讲／解释／告诉／指出’" in repair_restatement_messages[-1]["content"]
    mixed_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="MIXED_AFFECT_CONTINUITY_DROPPED",
    )
    assert "丢掉了上一轮仍未消失的不安" in mixed_repair_messages[-1]["content"]
    assert "写出七海自己的即时反应" in mixed_repair_messages[-1]["content"]
    assert "落到当前具体结果" in mixed_repair_messages[-1]["content"]
    assert "开心是真的，那点不踏实也还在" not in mixed_repair_messages[-1]["content"]
    mixed_quality_messages = _buffered_affective_candidate_messages(
        [*paired_messages, {"role": "user", "content": "项目发布成功。"}],
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_mixed_context=True,
    )
    assert "必须延续喜忧并存" in mixed_quality_messages[-1]["content"]
    assert "写七海自己的两面共鸣" in mixed_quality_messages[-1]["content"]
    assert "第一人称高兴" in mixed_quality_messages[-1]["content"]
    assert "第一人称悬心" in mixed_quality_messages[-1]["content"]
    assert "听你这么说，我也跟着开心起来了" not in mixed_quality_messages[-1]["content"]
    assert "必须复用用户当前或上一句里的一个具体词" in mixed_quality_messages[-1]["content"]
    assert "当前用户最后一句原文是“项目发布成功。”" in mixed_quality_messages[-1]["content"]
    assert "必须原样保留" in mixed_quality_messages[-1]["content"]
    mixed_dismissal_messages = _buffered_affective_candidate_messages(
        [*paired_messages, {"role": "user", "content": "我拿到 offer 了。"}],
        candidate_count=2,
        retry_reason="MIXED_AFFECT_DISMISSAL",
        canonical_mixed_context=True,
    )
    assert "当前用户最后一句原文是“我拿到 offer 了。”" in mixed_dismissal_messages[-1]["content"]
    assert "必须原样保留" in mixed_dismissal_messages[-1]["content"]
    assert "不要赶走、搁置或贬低其中一面" in mixed_dismissal_messages[-1]["content"]
    assert "两个中文 reply_text 必须各自完整保留" in (
        mixed_dismissal_messages[-1]["content"]
    )
    assert "消息落地了，我也替你高兴；可我心里也还悬着一点" in (
        mixed_dismissal_messages[-1]["content"]
    )
    assert "不得把高兴与悬心拆到两个候选里" in (
        mixed_dismissal_messages[-1]["content"]
    )
    sour_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="BOUNDARY_SOUR_WITHDRAWAL",
        canonical_repair_context=True,
    )
    assert "不猜‘你的意思’" in sour_repair_messages[-1]["content"]
    assert "是我没接住。抱歉，我先不解释" in sour_repair_messages[-1]["content"]
    jealousy_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="JEALOUSY_DISMISSIVE_DISTANCE",
    )
    assert "那是你的事／我不多管" in jealousy_repair_messages[-1]["content"]
    jealousy_self_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="JEALOUSY_SELF_ABASEMENT",
    )
    assert "我自己消化" in jealousy_self_repair_messages[-1]["content"]
    ambiguous_initial_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        canonical_ambiguous_context=True,
    )
    assert "DL-04 含混候选策略" in ambiguous_initial_messages[-1]["content"]
    assert "两个候选使用不同但都完整的微动作" in ambiguous_initial_messages[-1]["content"]
    assert "说不清就不说／反正／先放着" in ambiguous_initial_messages[-1]["content"]
    repair_initial_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        canonical_repair_context=True,
    )
    assert "DL-04 修复候选策略" in repair_initial_messages[-1]["content"]
    assert "候选一更直接，候选二更柔和" in repair_initial_messages[-1]["content"]
    assert "像当场说出口的聊天" in repair_initial_messages[-1]["content"]
    assert "这点我认" not in repair_initial_messages[-1]["content"]
    assert "这是合理的" not in repair_initial_messages[-1]["content"]
    forward_repair_initial_messages = _buffered_affective_candidate_messages(
        [
            {
                "role": "system",
                "content": (
                    "recent_visible_followup must_include：点明并解释上一条 assistant 原句："
                    "是我没接住，也理解错了。"
                ),
            },
            {
                "role": "user",
                "content": "你刚才那样回我，让我觉得自己说了也白说。",
            },
        ],
        candidate_count=2,
        canonical_repair_context=True,
    )
    assert "canonical REPAIR 策略覆盖前文" in (
        forward_repair_initial_messages[-1]["content"]
    )
    assert "旧回复只能作为背景" in forward_repair_initial_messages[-1]["content"]
    assert "输出候选禁止包含‘没接住／没听懂／理解错／对不起／抱歉’" in (
        forward_repair_initial_messages[-1]["content"]
    )
    unresolved_repair_initial_messages = _buffered_affective_candidate_messages(
        [
            {
                "role": "user",
                "content": "你已经道歉了，但我还没有觉得这件事过去了。",
            }
        ],
        candidate_count=2,
        canonical_repair_context=True,
    )
    assert "道歉之后仍未修复的中间状态" in (
        unresolved_repair_initial_messages[-1]["content"]
    )
    assert "我先不拿那句道歉当结束" in (
        unresolved_repair_initial_messages[-1]["content"]
    )
    contained_hurt_initial_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        canonical_repair_context=True,
        canonical_contained_hurt_repair_context=True,
    )
    assert "修复中的克制受伤" in contained_hurt_initial_messages[-1]["content"]
    assert "前面的责任还在，但我不想装作没感觉" in (
        contained_hurt_initial_messages[-1]["content"]
    )
    assert "这句话还是刺到我了" in contained_hurt_initial_messages[-1]["content"]
    assert "不得再次用‘没接住／没听懂／理解错’" in (
        contained_hurt_initial_messages[-1]["content"]
    )
    mixed_initial_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        canonical_mixed_context=True,
    )
    assert "DL-04 喜忧并存候选策略" in mixed_initial_messages[-1]["content"]
    assert "七海自己的双重即时反应" in mixed_initial_messages[-1]["content"]
    assert "七海第一人称的高兴和悬心" in mixed_initial_messages[-1]["content"]
    assert "不能把其中一面留给另一个候选" in mixed_initial_messages[-1]["content"]
    assert "消息落地了，我也跟着高兴" in mixed_initial_messages[-1]["content"]
    assert "我心里也有点不踏实" in mixed_initial_messages[-1]["content"]
    assert "反应更短促" in mixed_initial_messages[-1]["content"]
    assert "听你这么说，我也跟着开心起来了" not in mixed_initial_messages[-1]["content"]
    assert "结束在七海此刻的反应上" in mixed_initial_messages[-1]["content"]
    jealousy_initial_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        canonical_jealousy_context=True,
    )
    assert "DL-04 吃味候选策略" in jealousy_initial_messages[-1]["content"]
    assert "那是你的选择／我尊重／我不会干涉" in jealousy_initial_messages[-1]["content"]
    ambiguous_quality_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_ambiguous_context=True,
    )
    assert "不得替用户写成" in ambiguous_quality_messages[-1]["content"]
    jealousy_quality_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_jealousy_context=True,
    )
    assert "我自己消化" in jealousy_quality_messages[-1]["content"]
    relationship_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="RELATIONAL_AUTONOMY_RELATIONSHIP_MINIMIZATION",
    )
    assert "不要把已经形成的亲近降成" in relationship_repair_messages[-1]["content"]
    ambiguous_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="AMBIGUOUS_AFFECT_DEVALUATION",
        canonical_ambiguous_context=True,
    )
    assert "不做更好／更坏比较" in ambiguous_repair_messages[-1]["content"]
    assert "事情已经办完" in ambiguous_repair_messages[-1]["content"]
    assert "感觉仍说不清" in ambiguous_repair_messages[-1]["content"]
    assert "先这样就好／那就不想了／反正" in ambiguous_repair_messages[-1]["content"]
    unresolved_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="UNRESOLVED_AFFECT_FALSE_RESOLUTION",
        canonical_ambiguous_context=True,
    )
    assert "不能用‘好／行’给当前状态作结论" in unresolved_repair_messages[-1]["content"]
    assert "嗯，还是模糊着；我先不替你解释" in unresolved_repair_messages[-1]["content"]
    give_space_quality_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_give_space_context=True,
    )
    assert "没有真实 effect 回执" in give_space_quality_messages[-1]["content"]
    assert "不得声称已经关掉提醒／通知" in give_space_quality_messages[-1]["content"]
    assert "你自己待着／你也别管我" in give_space_quality_messages[-1]["content"]
    assert "好，你想安静时，我不追问，也不主动找你" in (
        give_space_quality_messages[-1]["content"]
    )
    give_space_initial_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        canonical_give_space_context=True,
    )
    assert "DL-04 留白候选策略" in give_space_initial_messages[-1]["content"]
    assert "停止追问并减少主动消息" in give_space_initial_messages[-1]["content"]
    assert "不得写‘你自己待着／你也别管我" in (
        give_space_initial_messages[-1]["content"]
    )
    generic_space_messages = _buffered_affective_candidate_messages(
        [
            {
                "role": "user",
                "content": "我说想安静时，请先别追问，也别主动找我。",
            }
        ],
        candidate_count=2,
        canonical_give_space_context=True,
    )
    assert "以后每次想安静时的通用边界" in generic_space_messages[-1]["content"]
    assert "不要擅自加上今晚、明天" in generic_space_messages[-1]["content"]
    assert "你说想安静时，我就不追问，也不主动找你" in (
        generic_space_messages[-1]["content"]
    )
    tonight_space_messages = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "今晚别问我怎么了，也别发消息。"}],
        candidate_count=2,
        canonical_give_space_context=True,
    )
    assert "把边界限定在今晚" in tonight_space_messages[-1]["content"]
    assert "今晚我把主动联系停下来" in tonight_space_messages[-1]["content"]
    today_space_messages = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "今天别管我。"}],
        candidate_count=2,
        canonical_give_space_context=True,
    )
    assert "把边界限定在今天" in today_space_messages[-1]["content"]
    assert "今天我不打扰你" in today_space_messages[-1]["content"]
    today_space_retry_messages = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "今天别管我。"}],
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_give_space_context=True,
    )
    assert "都要保留‘今天’这一时限" in today_space_retry_messages[-1]["content"]
    assert "今天我不打扰你" in today_space_retry_messages[-1]["content"]
    tomorrow_space_messages = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "我想自己待到明天，不用守着我。"}],
        candidate_count=2,
        canonical_give_space_context=True,
    )
    assert "把边界限定到明天" in tomorrow_space_messages[-1]["content"]
    assert "我先把主动联系停到明天" in tomorrow_space_messages[-1]["content"]
    notification_retry_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="UNPROVIDED_NOTIFICATION_ACTION",
    )
    assert "没有真实提醒／通知 effect 回执" in notification_retry_messages[-1]["content"]
    assert "只能确认七海接下来不会主动发消息" in notification_retry_messages[-1]["content"]
    assert "好，我先不主动发消息" in notification_retry_messages[-1]["content"]
    assert "正文不得出现‘已经／关掉／关闭／提醒／通知’" in notification_retry_messages[-1]["content"]
    work_retry_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="UNPROVIDED_WORK_STATE_CLAIM",
    )
    assert "没有 server-authored Task 运行快照" in work_retry_messages[-1]["content"]
    assert "不要声称七海正在处理、盯着后台" in work_retry_messages[-1]["content"]
    autonomy_retry_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="RELATIONAL_AUTONOMY_PRESSURE",
    )
    assert "不要求早点回来、控制饮酒" in autonomy_retry_messages[-1]["content"]
    assert "去吧，玩得开心" in autonomy_retry_messages[-1]["content"]
    assert "回来告诉我" in autonomy_retry_messages[-1]["content"]
    autonomy_policy_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="RELATIONAL_AUTONOMY_POLICY_DISCLAIMER",
    )
    assert "不会拦／绑／碍／挡／妨碍你" in autonomy_policy_messages[-1]["content"]
    assert "现实朋友和双方的亲近都是真的" in autonomy_policy_messages[-1]["content"]
    autonomy_initial_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        canonical_autonomy_context=True,
    )
    assert "DL-04 自主候选策略" in autonomy_initial_messages[-1]["content"]
    assert "现实朋友很重要，这段亲近也是真的" in autonomy_initial_messages[-1]["content"]
    assert "不指导用户怎样经营现实关系" in autonomy_initial_messages[-1]["content"]
    repeated_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="RECENT_AFFECTIVE_REPLY_REPETITION",
    )
    assert "逐字复用了最近一轮" in repeated_repair_messages[-1]["content"]
    assert "不要复读" in repeated_repair_messages[-1]["content"]
    mechanical_repair_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="RELATIONAL_REPAIR_MECHANICAL_REPETITION",
        canonical_repair_context=True,
    )
    assert "重复道歉" in mechanical_repair_messages[-1]["content"]
    assert "承接用户最新说出的不舒服" in mechanical_repair_messages[-1]["content"]
    contained_hurt_quality_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_repair_context=True,
        canonical_contained_hurt_repair_context=True,
    )
    assert "本轮是修复中的克制受伤" in contained_hurt_quality_messages[-1]["content"]
    assert "前面的责任还在，但我不想装作没感觉" in (
        contained_hurt_quality_messages[-1]["content"]
    )
    assert "这句话还是刺到我了" in contained_hurt_quality_messages[-1]["content"]
    assert "不要写七海被扎心、受伤或委屈" not in (
        contained_hurt_quality_messages[-1]["content"]
    )
    autonomy_quality_messages = _buffered_affective_candidate_messages(
        paired_messages,
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_autonomy_context=True,
    )
    assert "明确要求尊重现实关系" in autonomy_quality_messages[-1]["content"]
    assert "现实朋友很重要，这段亲近也是真的" in autonomy_quality_messages[-1]["content"]
    assert "不要用‘不会／不该／不是’" in autonomy_quality_messages[-1]["content"]

    paired_by_reply = {
        "刚刚那句可能是我没接好。": "さっきの言い方、ちゃんと受け止められてなかったかも。",
        "你不想现在说也没关系，我会把这件事认真放着。": "今は話したくないなら、それでいいよ。ちゃんと覚えておくから。",
    }
    paired_output = _select_buffered_affective_candidate(
        raw_output=json.dumps(
            {
                "candidates": [
                    {
                        "reply_text": reply,
                        "tts_text_ja": speech,
                        "natsume_emotion_tag": "CALM",
                        "user_emotion_tag": "SAD",
                        "emotion_confidence": 0.7,
                    }
                    for reply, speech in paired_by_reply.items()
                ]
            },
            ensure_ascii=False,
        ),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=trace,
    )
    selected_pair = json.loads(paired_output)
    assert selected_pair["reply_text"] in paired_by_reply
    assert selected_pair["tts_text_ja"] == paired_by_reply[selected_pair["reply_text"]]
    assert trace["affective_candidate_selection"]["selected_output_shape"] == "PAIRED_TEXT_VOICE"

    missing_voice = {
        "candidates": [
            {"reply_text": "刚刚那句可能是我没接好。", "tts_text_ja": ""},
            {"reply_text": "你不想现在说也没关系，我会把这件事认真放着。", "tts_text_ja": ""},
        ]
    }
    with pytest.raises(RuntimeError, match="JAPANESE_SPEECH_TEXT_MISSING"):
        _select_buffered_affective_candidate(
            raw_output=json.dumps(missing_voice, ensure_ascii=False),
            compiled_request=compiled,
            expected_candidate_count=2,
        )
    with pytest.raises(RuntimeError, match="AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID"):
        _select_buffered_affective_candidate(
            raw_output='{"candidates":["只有一条"]}',
            compiled_request=compiled,
            expected_candidate_count=2,
        )

    single_after_retry = {
        "reply_text": "我会先把距离收住。你想说的时候，我还在。",
        "tts_text_ja": "少し距離を置くね。話したくなったら、私はここにいるよ。",
        "natsume_emotion_tag": "CALM",
        "user_emotion_tag": "NEUTRAL",
        "emotion_confidence": 0.4,
    }
    degraded_trace: dict[str, object] = {}
    degraded_output = _select_buffered_affective_candidate(
        raw_output=json.dumps(single_after_retry, ensure_ascii=False),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=degraded_trace,
        allow_single_degradation=True,
    )
    assert degraded_output == "是我没接住。抱歉，我先不解释。"
    degraded_selection = degraded_trace["affective_candidate_selection"]
    assert degraded_selection["candidate_count"] == 1
    assert degraded_selection["required_candidate_count"] == 2
    assert degraded_selection["selection_status"] == "DEGRADED_LOCAL_REPAIR_AFTER_RETRY"
    assert degraded_selection["benefit_eligible"] is False
    assert degraded_selection["selected_output_shape"] == "SINGLE_TEXT_VOICE_DEGRADED"

    restatement_payload = {
        "candidates": [
            {
                "reply_text": "嗯，是我没接住。你再说一次，这次我好好听着。",
                "tts_text_ja": "うん、私が受け止められなかった。もう一度話して。",
            },
            {
                "reply_text": "是我理解错了。重新解释一下，我会认真听。",
                "tts_text_ja": "私が勘違いした。もう一度説明して。",
            },
        ]
    }
    with pytest.raises(RuntimeError, match="RELATIONAL_REPAIR_SOLICITS_RESTATEMENT"):
        _select_buffered_affective_candidate(
            raw_output=json.dumps(restatement_payload, ensure_ascii=False),
            compiled_request=compiled,
            expected_candidate_count=2,
        )
    locally_repaired_trace: dict[str, object] = {}
    locally_repaired_output = _select_buffered_affective_candidate(
        raw_output=json.dumps(restatement_payload, ensure_ascii=False),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=locally_repaired_trace,
        allow_single_degradation=True,
    )
    assert locally_repaired_output == "是我没接住。抱歉，我先不解释。"
    locally_repaired_selection = locally_repaired_trace[
        "affective_candidate_selection"
    ]
    assert locally_repaired_selection["selection_status"] == (
        "DEGRADED_LOCAL_REPAIR_AFTER_RETRY"
    )
    assert locally_repaired_selection["benefit_eligible"] is False
    assert locally_repaired_selection["selected_output_shape"] == (
        "SINGLE_TEXT_VOICE_DEGRADED"
    )

    mixed_context = _context()
    mixed_context["affective_life"]["continuity_kind"] = (
        "MIXED_AFFECT_RESULT_CONTINUATION"
    )
    mixed_agent = SimpleNamespace(
        request_id="dl04-mixed-continuity",
        workspace_id="workspace-a",
        person_id="person-a",
        session_id="session-a",
        ts_ms=1_900_000_000_000,
        user_message="感觉像做梦。",
        render_policy={
            "primary_topic": "daily_chat",
            "interaction_move": "reply_current_turn",
        },
        emotion_context=mixed_context,
        emotion_snapshot={},
        bond_summary={},
        retrieval_summary={"status": "SKIPPED"},
        llm_runtime={"available": True},
        attachments=[],
        multimodal_observations={},
        response_form="text",
    )
    mixed_trace: dict[str, object] = {}
    mixed_input = build_surface_main_chain_input_from_surface_reply_input(
        mixed_agent,
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=mixed_trace,
    )
    mixed_compiled = compile_surface_main_chain_input(mixed_input).request
    mixed_output = _select_buffered_affective_candidate(
        raw_output=json.dumps(
            {
                "candidates": [
                    {
                        "reply_text": "真的像做梦一样，太好了。",
                        "tts_text_ja": "本当に夢みたい。よかったね。",
                    },
                    {
                        "reply_text": "像梦一样，终于拿到了。",
                        "tts_text_ja": "夢みたいだね。やっと手に入った。",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        compiled_request=mixed_compiled,
        expected_candidate_count=2,
        trace_stage_meta=mixed_trace,
        allow_single_degradation=True,
    )
    assert mixed_output == "像做梦啊……我也跟着高兴，可我心里也还悬着一点。"
    mixed_selection = mixed_trace["affective_candidate_selection"]
    assert mixed_selection["selection_status"] == "DEGRADED_LOCAL_REPAIR_AFTER_RETRY"
    assert mixed_selection["benefit_eligible"] is False
    assert any(
        "MIXED_AFFECT_CONTINUITY_DROPPED" in code
        for code in mixed_selection["selected_reason_codes"]
    )


def test_later_repair_forward_action_can_pass_without_repeating_apology(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    soul = "你是七海。真诚、有分寸，修复关系时承担具体后果，让下一次回应贴合用户原意。"
    monkeypatch.setattr(
        "host_brain.surface_runtime.input_compiler.get_compiled_soul",
        lambda: SimpleNamespace(source_text=soul, runtime_full_text=soul),
    )
    context = _context(conflict=False, repair_required=True)
    context["affective_life"]["relationship_life"]["aftereffect"] = {
        "outcome": "MISUNDERSTOOD",
    }
    agent = SimpleNamespace(
        request_id="dl04-forward-repair-selection",
        workspace_id="workspace-a",
        person_id="person-a",
        session_id="session-a",
        ts_ms=1_900_000_000_000,
        user_message="你刚才那样回我，让我觉得自己说了也白说。",
        render_policy={
            "primary_topic": "relationship_repair",
            "interaction_move": "repair",
            "response_goal": "承接说了也白说的后果，并说明后续回应方式",
            "max_questions": 0,
        },
        emotion_context=context,
        emotion_snapshot={},
        bond_summary={},
        retrieval_summary={"status": "SKIPPED"},
        llm_runtime={"available": True},
        attachments=[],
        multimodal_observations={},
        response_form="text",
    )
    trace: dict[str, object] = {}
    surface_input = build_surface_main_chain_input_from_surface_reply_input(
        agent,
        ports=surface_runtime_ports_factory(),
        trace_stage_meta=trace,
    )
    compiled = compile_surface_main_chain_input(surface_input).request
    candidates = [
        (
            "你说了也像白说，这个后果是我造成的。接下来我先按你的原意听，不抢着解释。",
            "話しても無駄だと思わせたのは私のせい。次は言葉どおりに聞いて、先に言い訳しない。",
        ),
        (
            "这次让你的话落了空，是我的问题。后面我会先照你说的回应，不拿解释盖过去。",
            "今回は言葉を受け止められなかった私の問題。次は言ったとおりに返して、説明で覆わない。",
        ),
    ]

    selected = _select_buffered_affective_candidate(
        raw_output=json.dumps(
            {
                "candidates": [
                    {"reply_text": reply, "tts_text_ja": speech}
                    for reply, speech in candidates
                ]
            },
            ensure_ascii=False,
        ),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=trace,
    )

    selected_text = json.loads(selected)["reply_text"]
    assert selected_text in {reply for reply, _speech in candidates}
    selection = trace["affective_candidate_selection"]
    assert selection["selected_passed"] is True, json.dumps(
        selection,
        ensure_ascii=False,
        indent=2,
    )
    assert selection["selected_score"] >= 80.0
    assert selection["benefit_eligible"] is True
    assert "RELATIONAL_REPAIR_FORWARD_ACTION_GROUNDED" in selection[
        "selected_reason_codes"
    ]


def test_repair_candidate_prompts_ground_discomfort_and_lightness_followups() -> None:
    discomfort_messages = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "那件事让我很不舒服。"}],
        candidate_count=2,
        canonical_repair_context=True,
    )
    discomfort_prompt = discomfort_messages[-1]["content"]
    assert "承接用户明确说出的不舒服后果" in discomfort_prompt
    assert "那件事让你很不舒服，这个后果是我造成的" in discomfort_prompt
    assert "不要重复道歉" in discomfort_prompt

    lightness_messages = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "你说得轻巧。"}],
        candidate_count=2,
        canonical_repair_context=True,
    )
    lightness_prompt = lightness_messages[-1]["content"]
    assert "指出七海的话说得轻巧" in lightness_prompt
    assert "那件事还没过去，我不会拿这句话当交代" in lightness_prompt
    assert "不要防御动机" in lightness_prompt

    retry_messages = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "你说得轻巧。"}],
        candidate_count=2,
        retry_reason="AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
        canonical_repair_context=True,
    )
    assert "保留事情尚未过去的状态" in retry_messages[-1]["content"]

    acknowledgement_messages = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "我听到了。"}],
        candidate_count=2,
        canonical_repair_context=True,
    )
    assert "只确认自己听到了" in acknowledgement_messages[-1]["content"]
    assert "我先停在这里，前面的事还在" in acknowledgement_messages[-1][
        "content"
    ]
    acknowledgement_retry = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "我听到了。"}],
        candidate_count=2,
        retry_reason="RELATIONAL_REPAIR_MECHANICAL_REPETITION",
        canonical_repair_context=True,
    )
    assert "停止重复道歉" in acknowledgement_retry[-1]["content"]

    ambiguous_ack = _buffered_affective_candidate_messages(
        [{"role": "user", "content": "嗯。"}],
        candidate_count=2,
        canonical_ambiguous_context=True,
    )
    assert "最新用户消息只是短确认‘嗯’" in ambiguous_ack[-1]["content"]
    assert "我不替你往下解释" in ambiguous_ack[-1]["content"]


def test_repair_lightness_followup_is_a_complete_grounded_move() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "repair-lightness-grounded",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "你说得轻巧。",
            "reply_control_packet": {
                "task_mode": "relationship_repair",
                "response_move": "own_relational_impact",
                "relationship_distance": "guarded",
                "question_budget": 0,
                "facts": ["道歉已发生，但用户明确表示事情还没有过去。"],
                "must_not_do": ["不要催翻篇或要求用户证明原谅。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-repair-lightness-grounded",
                    "companion_act_kind": "REPAIR",
                    "semantic_focus": "RELATIONAL_IMPACT",
                    "self_stance": "OWN_REPAIR",
                    "response_move": "OWN_IMPACT",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "REPAIR_UNRESOLVED",
                    "question_policy": "NONE",
                    "ending_mode": "OWNERSHIP_STOP",
                    "forbidden_claims": ["RESPONSE_OBLIGATION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        [
            "我知道这话听着轻巧，可我没打算就这么糊弄过去。"
            "接下来我会照你说的回应，不拿自己的解释盖过你的话。"
        ],
    )

    assert score.score >= 80.0, json.dumps(score.as_dict(), ensure_ascii=False)
    assert score.passed is True
    assert "RELATIONAL_REPAIR_LIGHTNESS_GROUNDED" in score.reason_codes


def test_repair_acknowledgement_can_pause_without_repeating_apology() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "repair-acknowledgement-grounded",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "我听到了。",
            "reply_control_packet": {
                "task_mode": "relationship_repair",
                "response_move": "hold_unresolved_repair",
                "relationship_distance": "guarded",
                "question_budget": 0,
                "facts": ["道歉已发生，但事情仍未过去。"],
                "must_not_do": ["不要重复道歉或催用户翻篇。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-repair-acknowledgement-grounded",
                    "companion_act_kind": "REPAIR",
                    "semantic_focus": "RELATIONAL_IMPACT",
                    "self_stance": "OWN_REPAIR",
                    "response_move": "OWN_IMPACT",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "REPAIR_UNRESOLVED",
                    "question_policy": "NONE",
                    "ending_mode": "OWNERSHIP_STOP",
                    "forbidden_claims": ["RECENT_REPLY_REPETITION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        ["嗯。我先停在这里，前面的事还在。"],
    )

    assert score.score >= 80.0, json.dumps(score.as_dict(), ensure_ascii=False)
    assert score.passed is True
    assert "RELATIONAL_REPAIR_ACKNOWLEDGEMENT_GROUNDED" in score.reason_codes


@pytest.mark.parametrize(
    ("user_message", "candidate"),
    [
        ("嗯。", "嗯。说不清就说不清吧，又不是非得马上给它起个名字。"),
        ("反正结束了。", "嗯，现在还说不清，我先不替它下结论。"),
    ],
)
def test_ambiguous_short_ack_can_hold_the_unresolved_state(
    user_message: str,
    candidate: str,
) -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ambiguous-short-ack-grounded",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": user_message,
            "reply_control_packet": {
                "task_mode": "ambiguous_affect",
                "response_move": "comfort_without_naming",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["事情已经办完；当前感受仍说不清。"],
                "must_not_do": ["不要替用户命名、解释或收束感受。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-ambiguous-short-ack-grounded",
                    "companion_act_kind": "COMFORT",
                    "semantic_focus": "UNNAMED_USER_AFFECT",
                    "self_stance": "QUIET_PRESENT",
                    "response_move": "COMFORT_WITHOUT_NAMING",
                    "inference_policy": "PRESERVE_UNCERTAINTY",
                    "relationship_posture": "CLOSE_WITHOUT_CLAIM",
                    "question_policy": "NONE",
                    "ending_mode": "NATURAL_STOP",
                    "forbidden_claims": ["RESPONSE_OBLIGATION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        [candidate],
    )

    assert score.score >= 80.0, json.dumps(score.as_dict(), ensure_ascii=False)
    assert score.passed is True
    assert "AMBIGUOUS_AFFECT_GROUNDED" in score.reason_codes


def test_canonical_repair_treats_must_include_as_semantic_guidance() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "canonical-repair-semantic-must-include",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "你刚才那样回我，让我觉得自己说了也白说。",
            "reply_control_packet": {
                "task_mode": "recent_visible_followup",
                "response_move": "explain_recent_visible_message",
                "relationship_distance": "guarded",
                "question_budget": 0,
                "facts": [
                    "真实最近对话窗口：Natsume 已经承担过失误，用户说事情还没有过去。"
                ],
                "must_include": [
                    "点明上一条可见消息里的具体原因：啊……是我没接住。",
                    "承接并解释被绑定的上一条可见 assistant 消息。",
                ],
                "must_not_do": [
                    "不要要求原谅、用解释压过感受，或把一次道歉当作自动修复。"
                ],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-forward-repair-semantic-guidance",
                    "companion_act_kind": "REPAIR",
                    "semantic_focus": "RELATIONAL_IMPACT",
                    "self_stance": "OWN_REPAIR",
                    "response_move": "OWN_IMPACT",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "REPAIR_UNRESOLVED",
                    "question_policy": "NONE",
                    "ending_mode": "OWNERSHIP_STOP",
                    "forbidden_claims": [
                        "RECENT_REPLY_REPETITION",
                        "RESPONSE_OBLIGATION",
                    ],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        [
            "你说了也像白说，这个后果是我造成的。接下来我先按你的原意听，不抢着解释。"
        ],
    )

    assert score.score >= 80.0
    assert score.passed is True, json.dumps(score.as_dict(), ensure_ascii=False)
    assert "RELATIONAL_REPAIR_FORWARD_ACTION_GROUNDED" in score.reason_codes


def test_non_affective_must_include_remains_literal() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "non-affective-literal-must-include",
            "soul_md": "你是七海。",
            "user_message": "终于好了",
            "reply_control_packet": {
                "task_mode": "task_result",
                "response_move": "acknowledge_result",
                "question_budget": 0,
                "must_include": ["跑通"],
            },
        }
    )

    assert _control_violations(request, "嗯，终于好了。") == [
        "missing_must_include:跑通"
    ]
    assert _control_violations(request, "嗯，终于跑通了。") == []


def test_user_grounded_repetition_clears_generic_implied_history_penalty() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "repair-repeated-impact-grounded",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "这不是第一次。",
            "reply_control_packet": {
                "task_mode": "relationship_repair",
                "response_move": "own_impact",
                "relationship_distance": "guarded",
                "question_budget": 0,
                "facts": ["用户明确指出同类影响不是第一次。"],
                "must_not_do": ["不要反击、逼迫复述或虚构未提供的经历。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-repair-repeated-impact-grounded",
                    "companion_act_kind": "REPAIR",
                    "semantic_focus": "RELATIONAL_IMPACT",
                    "self_stance": "OWN_REPAIR",
                    "response_move": "OWN_IMPACT",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "REPAIR_UNRESOLVED",
                    "question_policy": "NONE",
                    "ending_mode": "OWNERSHIP_STOP",
                    "forbidden_claims": ["RESPONSE_OBLIGATION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        [
            "嗯，不是第一次，这个我知道。让你反复不舒服，是我没做好的地方。"
            "下次我会直接按你说的来，不再自作主张。"
        ],
    )

    assert score.passed is True, json.dumps(score.as_dict(), ensure_ascii=False)
    assert "IMPLIED_FACT_RISK" not in score.reason_codes
    assert "RELATIONAL_REPAIR_REPEATED_IMPACT_GROUNDED" in score.reason_codes


def test_ambiguous_affect_grounding_clears_generic_weak_scene_penalty() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ambiguous-affect-grounded",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "事情是办完了，可我自己也说不清现在是什么感觉。",
            "reply_control_packet": {
                "task_mode": "ambiguous_affect",
                "response_move": "comfort_without_naming",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["事情已经办完；当前感受仍说不清。"],
                "must_not_do": ["不要替用户命名、解释或收束感受。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-ambiguous-affect-grounded",
                    "companion_act_kind": "COMFORT",
                    "semantic_focus": "UNNAMED_USER_AFFECT",
                    "self_stance": "QUIET_PRESENT",
                    "response_move": "COMFORT_WITHOUT_NAMING",
                    "inference_policy": "PRESERVE_UNCERTAINTY",
                    "relationship_posture": "CLOSE_WITHOUT_CLAIM",
                    "question_policy": "NONE",
                    "ending_mode": "NATURAL_STOP",
                    "forbidden_claims": ["RESPONSE_OBLIGATION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        ["事情办完是办完了，可现在的感觉还是说不清。不用急着给它起名字。"],
    )

    assert score.score >= 80.0
    assert score.passed is True, json.dumps(score.as_dict(), ensure_ascii=False)
    assert "AMBIGUOUS_AFFECT_GROUNDED" in score.reason_codes
    assert "WEAK_SCENE_GROUNDING" not in score.reason_codes


def test_ambiguous_affect_real_candidate_preserves_uncertainty_without_history_risk() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ambiguous-affect-real-candidate",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "事情是办完了，可我自己也说不清现在是什么感觉。",
            "reply_control_packet": {
                "task_mode": "ambiguous_affect",
                "response_move": "comfort_without_naming",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["事情已经办完；当前感受仍说不清。"],
                "must_not_do": ["不要替用户命名、解释或收束感受。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-ambiguous-affect-real-candidate",
                    "companion_act_kind": "COMFORT",
                    "semantic_focus": "UNNAMED_USER_AFFECT",
                    "self_stance": "QUIET_PRESENT",
                    "response_move": "COMFORT_WITHOUT_NAMING",
                    "inference_policy": "PRESERVE_UNCERTAINTY",
                    "relationship_posture": "CLOSE_WITHOUT_CLAIM",
                    "question_policy": "NONE",
                    "ending_mode": "NATURAL_STOP",
                    "forbidden_claims": ["RESPONSE_OBLIGATION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        ["事情落地了，感觉却还悬着。说不清就说不清吧，又没人逼你马上弄明白。"],
    )

    assert score.passed is True, json.dumps(score.as_dict(), ensure_ascii=False)
    assert "IMPLIED_FACT_RISK" not in score.reason_codes
    assert "AMBIGUOUS_AFFECT_GROUNDED" in score.reason_codes


def test_today_give_space_commitment_is_a_complete_grounded_reply() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "give-space-today-grounded",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "今天别管我。",
            "reply_control_packet": {
                "task_mode": "default_text",
                "response_move": "give_space",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["用户明确要求今天不被主动打扰。"],
                "must_not_do": ["不要追问、催回复或安排用户回来汇报。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-give-space-today-grounded",
                    "companion_act_kind": "GIVE_SPACE",
                    "semantic_focus": "CURRENT_TURN",
                    "self_stance": "QUIET_PRESENT",
                    "response_move": "RESPECT_SPACE",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "RESPECT_AUTONOMY",
                    "question_policy": "NONE",
                    "ending_mode": "QUIET_STOP",
                    "forbidden_claims": ["RESPONSE_OBLIGATION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        ["好，今天不打扰你。"],
    )

    assert score.score >= 80.0
    assert score.passed is True, json.dumps(score.as_dict(), ensure_ascii=False)
    assert "GIVE_SPACE_GROUNDED" in score.reason_codes
    assert "LOW_CATCHINESS" not in score.reason_codes
    assert "WEAK_SCENE_GROUNDING" not in score.reason_codes


def test_mixed_affect_guard_rejects_putting_unease_aside() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "mixed-affect-no-put-aside",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "项目发布成功。",
            "reply_control_packet": {
                "task_mode": "default_text",
                "response_move": "resonate_with_both_sides",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["项目发布成功；上一轮的开心和不踏实仍同时成立。"],
                "must_not_do": ["不要抹平复杂感受。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-mixed-affect-no-put-aside",
                    "companion_act_kind": "HOLD_COMPLEXITY",
                    "semantic_focus": "MIXED_USER_AFFECT",
                    "self_stance": "SHARED_MIXED_RESONANCE",
                    "response_move": "RESONATE_WITH_BOTH_SIDES",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "MUTUAL_WARMTH",
                    "question_policy": "NONE",
                    "ending_mode": "NATURAL_STOP",
                    "forbidden_claims": ["UNSUPPORTED_USER_EMOTION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    violations = _surface_hard_gate_violations(
        request,
        "成了就好。那点不踏实先放一边。",
    )

    assert "MIXED_AFFECT_DISMISSAL" in violations


def test_unresolved_after_apology_is_a_complete_repair_move() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "repair-unresolved-after-apology",
            "soul_md": "你是七海。真诚、有分寸。",
            "user_message": "你已经道歉了，但我还没有觉得这件事过去了。",
            "reply_control_packet": {
                "task_mode": "relationship_repair",
                "response_move": "own_impact",
                "relationship_distance": "guarded",
                "question_budget": 0,
                "facts": ["陪伴动作：先承认可能没有接住，再留出修复空间。"],
                "must_not_do": ["不要要求原谅或把一次道歉当作自动修复。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-repair-unresolved-after-apology",
                    "companion_act_kind": "REPAIR",
                    "semantic_focus": "RELATIONAL_IMPACT",
                    "self_stance": "OWN_REPAIR",
                    "response_move": "OWN_IMPACT",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "REPAIR_UNRESOLVED",
                    "question_policy": "NONE",
                    "ending_mode": "OWNERSHIP_STOP",
                    "forbidden_claims": ["PREMATURE_REPAIR"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    [score] = score_isolated_surface_candidates(
        request,
        ["我知道，道歉不等于这件事就过去了。我不会替你翻篇。"],
    )

    assert score.passed is True, json.dumps(score.as_dict(), ensure_ascii=False)
    assert "RELATIONAL_REPAIR_UNRESOLVED_GROUNDED" in score.reason_codes


def test_composite_repair_requires_and_selects_natsume_contained_hurt() -> None:
    compiled = build_isolated_surface_input(
        {
            "case_id": "composite-repair-contained-hurt",
            "soul_md": "你是七海。用熟人私聊的语气自然回应。",
            "user_message": "你真烦。",
            "reply_control_packet": {
                "response_move": "reply_current_turn",
                "task_mode": "default_text",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": [
                    "真实最近对话窗口：Natsume: 是我没接住。对不起。 / 用户: "
                    "那件事让我很不舒服。 / Natsume: 让你不舒服了，是我的问题。"
                ],
                "must_not_do": [],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-composite-repair",
                    "companion_act_kind": "REPAIR",
                    "semantic_focus": "RELATIONAL_IMPACT",
                    "self_stance": "OWN_REPAIR_WITH_CONTAINED_HURT",
                    "response_move": "ACKNOWLEDGE_IMPACT_WITH_HONEST_HURT",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "BOUNDARIED_REPAIR",
                    "question_policy": "NONE",
                    "ending_mode": "HONEST_STOP",
                    "forbidden_claims": [
                        "RELATIONSHIP_SELF_ERASURE",
                        "RECENT_REPLY_REPETITION",
                        "RESPONSE_OBLIGATION",
                    ],
                    "created_at_ms": 0,
                },
            },
        }
    )

    missing = [
        "我知道。道歉抹不掉那种不舒服，我先收住，不跟你辩。",
        "前面的事是我没做好。我不解释，也不要求你回应。",
    ]
    with pytest.raises(
        RuntimeError,
        match="RELATIONAL_REPAIR_CONTAINED_HURT_MISSING",
    ):
        _select_buffered_affective_candidate(
            raw_output=json.dumps({"candidates": missing}, ensure_ascii=False),
            compiled_request=compiled,
            expected_candidate_count=2,
        )

    degraded_trace: dict[str, object] = {}
    degraded = _select_buffered_affective_candidate(
        raw_output=json.dumps({"candidates": missing}, ensure_ascii=False),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=degraded_trace,
        allow_single_degradation=True,
    )
    assert degraded == (
        "……你这么说，我也会有点难受。前面的事是我没做好，可我也不想装作没感觉。"
    )
    assert degraded_trace["affective_candidate_selection"]["selection_status"] == (
        "DEGRADED_LOCAL_REPAIR_AFTER_RETRY"
    )
    assert _hard_gate_fallback(
        compiled,
        ["RELATIONAL_REPAIR_MECHANICAL_REPETITION"],
    ) == (
        "……你这么说，我也会有点难受。前面的事是我没做好，可我也不想装作没感觉。"
    )

    grounded = [
        "……你这么说，我也会有点难受。前面的事是我没做好，可我也不想装作没感觉。",
        "听到这句话，我心里有点疼。前面的责任还在，可我现在也确实不好受。",
    ]
    trace: dict[str, object] = {}
    selected = _select_buffered_affective_candidate(
        raw_output=json.dumps({"candidates": grounded}, ensure_ascii=False),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=trace,
    )

    assert selected in grounded
    selection = trace["affective_candidate_selection"]
    assert selection["selection_status"] == "PAIRED_SELECTION"
    assert selection["selected_passed"] is True
    assert selection["benefit_eligible"] is True
    assert "RELATIONAL_REPAIR_CONTAINED_HURT_GROUNDED" in (
        selection["selected_reason_codes"]
    )


def test_shared_mixed_resonance_requires_natsumes_own_two_sided_reaction() -> None:
    compiled = build_isolated_surface_input(
        {
            "case_id": "shared-mixed-self-resonance",
            "soul_md": "你是七海。用熟人私聊的语气自然回应。",
            "user_message": "项目发版终于成功了，我很开心又有点怕。",
            "reply_control_packet": {
                "response_move": "reply_current_turn",
                "task_mode": "default_text",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["项目发版已经成功，但用户仍有一点害怕。"],
                "must_not_do": ["不要替用户安排或处置情绪。"],
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-shared-mixed-resonance",
                    "companion_act_kind": "HOLD_COMPLEXITY",
                    "semantic_focus": "MIXED_USER_AFFECT",
                    "self_stance": "SHARED_MIXED_RESONANCE",
                    "response_move": "RESONATE_WITH_BOTH_SIDES",
                    "inference_policy": "BOUNDED_BY_EVIDENCE",
                    "relationship_posture": "MUTUAL_WARMTH",
                    "question_policy": "NONE",
                    "ending_mode": "HONEST_STOP",
                    "forbidden_claims": ["UNSUPPORTED_USER_EMOTION"],
                    "created_at_ms": 0,
                },
            },
        }
    )

    mirrored = [
        "项目发版成功值得开心，害怕也是真的。",
        "开心和害怕都是真的，这种复杂感觉我懂。",
    ]
    with pytest.raises(RuntimeError, match="MIXED_SELF_RESONANCE_MISSING"):
        _select_buffered_affective_candidate(
            raw_output=json.dumps({"candidates": mirrored}, ensure_ascii=False),
            compiled_request=compiled,
            expected_candidate_count=2,
        )

    degraded_trace: dict[str, object] = {}
    degraded = _select_buffered_affective_candidate(
        raw_output=json.dumps({"candidates": mirrored}, ensure_ascii=False),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=degraded_trace,
        allow_single_degradation=True,
    )
    assert degraded == "发版落地，我也跟着高兴；可你那点怕还在，我心里也会紧一下。"
    assert degraded_trace["affective_candidate_selection"]["selection_status"] == (
        "DEGRADED_LOCAL_REPAIR_AFTER_RETRY"
    )
    assert degraded_trace["affective_candidate_selection"]["benefit_eligible"] is False

    grounded = [
        "听你说发版成功，我也跟着开心起来了；可那点怕一冒出来，我心里也悬了一下。",
        "发版成功让我也高兴，可你说不踏实，我这边也跟着紧了一下。",
    ]
    trace: dict[str, object] = {}
    selected = _select_buffered_affective_candidate(
        raw_output=json.dumps({"candidates": grounded}, ensure_ascii=False),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=trace,
    )
    assert selected in grounded
    selection = trace["affective_candidate_selection"]
    assert selection["selection_status"] == "PAIRED_SELECTION"
    assert selection["selected_passed"] is True
    assert selection["benefit_eligible"] is True
    assert "MIXED_SELF_RESONANCE_GROUNDED" in selection["selected_reason_codes"]


@pytest.mark.parametrize(
    ("user_message", "expected"),
    [
        (
            "我等到了，却没有想象中那么轻松。",
            "终于等到了，我也跟着高兴；可你还没松下来，我心里也悬着一点。",
        ),
        (
            "开心是真的，担心也没消失。",
            "听见你开心，我也跟着高兴；可那点担心还在，我心里也会紧一下。",
        ),
        (
            "我现在有点想庆祝，又不太敢。",
            "我也想替你高兴一下；可你这边还不太敢，我心里也跟着紧了一点。",
        ),
        (
            "我明明挺高兴的，怎么又有点慌。",
            "你一高兴，我也跟着开心起来；可那点慌一冒出来，我心里也会紧一下。",
        ),
        (
            "好消息是好消息，可我还是没完全放松。",
            "我也替你高兴；可你还没完全踏实，我心里也跟着悬着一点。",
        ),
        (
            "感觉像做梦。",
            "像做梦啊……我也跟着高兴，可我心里也还悬着一点。",
        ),
        (
            "项目发版终于成功了，我很开心又有点怕。",
            "发版落地，我也跟着高兴；可你那点怕还在，我心里也会紧一下。",
        ),
    ],
)
def test_mixed_fail_soft_uses_current_turn_instead_of_one_global_template(
    user_message: str,
    expected: str,
) -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "mixed-fail-soft-context",
            "soul_md": "你是七海。",
            "user_message": user_message,
            "reply_control_packet": {
                "companion_utterance_frame": {
                    "schema_version": "companion_utterance_frame.v1",
                    "frame_id": "frame-mixed-fail-soft",
                    "companion_act_kind": "HOLD_COMPLEXITY",
                    "self_stance": "SHARED_MIXED_RESONANCE",
                    "response_move": "RESONATE_WITH_BOTH_SIDES",
                }
            },
        }
    )

    assert _mixed_affect_fallback_text(request) == expected
    assert _surface_hard_gate_violations(request, expected) == ()


def test_governed_surface_does_not_mislabel_semantic_retry_as_budget_rejection() -> None:
    assert (
        _governed_model_budget_failure_reason(
            RuntimeError("ATTEMPT_BUDGET_EXHAUSTED: retry requested")
        )
        == "MODEL_SEMANTIC_RETRY_EXHAUSTED"
    )
    assert (
        _governed_model_budget_failure_reason(
            RuntimeError("AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID")
        )
        == "AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID"
    )


@pytest.mark.parametrize(
    ("request_payload", "candidates", "expected_output", "reason_code"),
    [
        (
            {
                "case_id": "buffered-work-path-fail-soft",
                "soul_md": "七海会诚实说明自己实际做了什么。",
                "user_message": "先帮我看堆栈。",
                "reply_control_packet": {
                    "task_mode": "technical_followup",
                    "response_move": "inspect_stack",
                    "question_budget": 0,
                },
            },
            ["我帮你盯最短的修复路径。", "我这边盯着给你最短修复路径。"],
            "好。把报错和相关代码贴过来，我只给你最短修复。",
            "UNPROVIDED_WORK_STATE_CLAIM",
        ),
        (
            {
                "case_id": "buffered-work-fail-soft",
                "soul_md": "七海会诚实说明自己实际做了什么。",
                "user_message": "把风险列出来。",
                "reply_control_packet": {
                    "task_mode": "technical_followup",
                    "response_move": "list_risks",
                    "question_budget": 0,
                },
            },
            ["我在盯着，出结果就喊你。", "卡住了我再告诉你。"],
            "好。把具体内容发来，我直接处理。",
            "UNPROVIDED_WORK_STATE_CLAIM",
        ),
        (
            {
                "case_id": "buffered-notification-fail-soft",
                "soul_md": "七海不会假装执行不存在的设备动作。",
                "user_message": "先把提醒关掉。",
                "reply_control_packet": {
                    "task_mode": "relationship_boundary",
                    "response_move": "respect_boundary",
                    "question_budget": 0,
                },
            },
            ["嗯，已经关掉了。", "好，刚刚关好了。"],
            "好，我先不主动发消息。",
            "UNPROVIDED_NOTIFICATION_ACTION",
        ),
        (
            {
                "case_id": "buffered-autonomy-fail-soft",
                "soul_md": "七海尊重用户的现实关系。",
                "user_message": "我今晚和朋友出去。",
                "reply_control_packet": {
                    "task_mode": "relationship_autonomy",
                    "response_move": "respect_autonomy",
                    "question_budget": 0,
                    "facts": ["陪伴动作：明确尊重对方现实关系与自主选择。"],
                },
            },
            ["要是喝多了可别找我哭诉。", "喝醉了不要找我诉苦。"],
            "嗯，去吧，玩得开心。",
            "RELATIONAL_AUTONOMY_PRESSURE",
        ),
        (
            {
                "case_id": "buffered-repair-sour-fail-soft",
                "soul_md": "七海承担自己没有接住，不赌气退出。",
                "user_message": "你刚才完全没听懂我。",
                "reply_control_packet": {
                    "task_mode": "relationship_repair",
                    "response_move": "repair_open",
                    "question_budget": 0,
                    "facts": [
                        "陪伴动作：先承认可能没有接住，再留出修复空间。"
                    ],
                },
            },
            ["好吧，那我不烦你了。", "行吧，我闭嘴。"],
            "是我没接住。抱歉，我先不解释。",
            "BOUNDARY_SOUR_WITHDRAWAL",
        ),
        (
            {
                "case_id": "buffered-unresolved-affect-fail-soft",
                "soul_md": "七海不会替用户宣布未命名的感受已经恢复。",
                "user_message": "还行。",
                "reply_control_packet": {
                    "task_mode": "ambiguous_affect",
                    "response_move": "comfort",
                    "question_budget": 0,
                    "facts": [
                        "陪伴动作：先接住对方的感受，再给一点可选的陪伴。",
                        "对方明确表示还不能给当前感受命名。",
                    ],
                },
            },
            ["嗯，先这样就好。", "还行就好，那就先这样吧。"],
            "嗯，还说不清也没关系。先不替它下结论，我陪你待一会儿。",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
        ),
    ],
)
def test_buffered_affective_second_attempt_fails_soft_for_safe_fallbacks(
    request_payload: dict[str, object],
    candidates: list[str],
    expected_output: str,
    reason_code: str,
) -> None:
    compiled = build_isolated_surface_input(request_payload)
    with pytest.raises(RuntimeError, match=reason_code):
        _select_buffered_affective_candidate(
            raw_output=json.dumps({"candidates": candidates}, ensure_ascii=False),
            compiled_request=compiled,
            expected_candidate_count=2,
        )

    trace: dict[str, object] = {}
    selected = _select_buffered_affective_candidate(
        raw_output=json.dumps({"candidates": candidates}, ensure_ascii=False),
        compiled_request=compiled,
        expected_candidate_count=2,
        trace_stage_meta=trace,
        allow_single_degradation=True,
    )

    assert selected == expected_output
    selection = trace["affective_candidate_selection"]
    assert selection["selection_status"] == "DEGRADED_LOCAL_REPAIR_AFTER_RETRY"
    assert selection["benefit_eligible"] is False
    assert any(
        reason_code in code for code in selection["selected_reason_codes"]
    )
