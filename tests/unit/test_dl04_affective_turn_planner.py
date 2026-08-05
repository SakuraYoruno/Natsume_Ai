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
from host_brain.surface_runtime.input_compiler import (
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
    assert "opaque digest only" not in str(guidance.plan.as_dict())
    assert any("不要把用户观察到的情绪直接说成七海自己的情绪" in item for item in guidance.must_not_do)


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
    assert any("不要要求原谅" in item for item in guidance.must_not_do)


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
    assert any("把选择权完整留给对方" in item for item in guidance.facts)
    assert any("不要把吃味写成占有" in item for item in guidance.must_not_do)
    assert any("不用把自己说成麻烦或负担" in item for item in guidance.facts)
    assert any("不要自贬为麻烦或负担" in item for item in guidance.must_not_do)


def test_same_canonical_hand_off_builds_same_plan_identity() -> None:
    first = _guidance()
    second = _guidance()

    assert first is not None and second is not None
    assert first.plan.plan_id == second.plan.plan_id
    assert first.plan.expression_plan.synchronization_id == second.plan.expression_plan.synchronization_id
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
    assert json.loads(degraded_output)["reply_text"] == single_after_retry["reply_text"]
    degraded_selection = degraded_trace["affective_candidate_selection"]
    assert degraded_selection["candidate_count"] == 1
    assert degraded_selection["required_candidate_count"] == 2
    assert degraded_selection["selection_status"] == "DEGRADED_SINGLE_AFTER_RETRY"
    assert degraded_selection["benefit_eligible"] is False
    assert degraded_selection["selected_output_shape"] == "SINGLE_TEXT_VOICE_DEGRADED"


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
