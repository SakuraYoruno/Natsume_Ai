from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.emotion_context import (
    HostEmotionRuntime,
    normalize_chat_emotion_context,
    resolve_chat_emotion_context,
)
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.app.tribe_composition import build_tribe_application_ports
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.affect_fusion import (
    AffectiveScope,
    AffectFusionService,
    _AppraisalCandidate,
    _build_appraisal_candidates,
)
from host_brain.digital_life.affective_turn_planner import (
    build_affective_turn_guidance,
)
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from natsume_contracts.life import EmotionComponentV1, UserAffectObservationV1


NOW = 1_900_000_000_000


def _runtime(tmp_path: Path) -> tuple[AffectFusionService, LifeSnapshotReader, LifeCommandService]:
    database = tmp_path / "dl02-affect-fusion.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl02-affect-fusion",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    command_service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    return (
        AffectFusionService(
            command_service_provider=lambda: command_service,
            snapshot_reader_provider=lambda: reader,
            clock_ms=lambda: NOW,
        ),
        reader,
        command_service,
    )


def _request(
    *,
    message: str,
    workspace_id: str = "workspace-a",
    person_id: str = "person-a",
    session_id: str = "session-a",
    emotion_context: dict[str, object] | None = None,
    emotion_tag: str | None = None,
    emotion_intensity: float | None = None,
    multimodal_observations: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        workspace_id=workspace_id,
        person_id=person_id,
        session_id=session_id,
        emotion_context=dict(emotion_context or {}),
        emotion_tag=emotion_tag,
        emotion_intensity=emotion_intensity,
        multimodal_observations=dict(multimodal_observations or {}),
        mood_state=None,
        emotion_range_hint=None,
    )


def _guidance_from(result, *, request_id: str, task_mode: str = ""):
    """Build the same trusted hand-off consumed by the live Surface path."""

    return build_affective_turn_guidance(
        emotion_context=result.as_emotion_context(),
        request_id=request_id,
        workspace_id=result.scope.workspace_id,
        relationship_subject_id=result.scope.relationship_subject_id,
        session_id=result.scope.session_id,
        task_mode=task_mode,
        created_at_ms=NOW,
    )


def test_text_distress_is_observed_then_appraised_not_mirrored_into_self(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request = _request(message="我真的很难受，什么都不想做。")

    result = fusion.resolve_request(request_body=request, request_id="request-sad")
    state = reader.get_snapshot(result.scope.aggregate_id).state

    assert result.path == "DEEP"
    assert result.observations[0].modality == "TEXT"
    assert result.observations[0].confidence <= 0.4
    assert result.observations[0].emotion_distribution["SAD"] > 0
    # The user can be sad while Natsume's own immediate response is care,
    # not an unexamined copy of the user's label.
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CARE",
        "CONCERN",
    }
    assert state["identity"] == {
        "self_id": "natsume_ai",
        "relationship_subject_id": "person-a",
    }


def test_implicit_low_mood_uses_canonical_comfort_without_mirroring_user_sadness(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-implicit-low-mood"

    result = fusion.resolve_request(
        request_body=_request(message="今天好像什么都不太顺。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    assert result.path == "DEEP"
    assert state["affective"]["last_appraisal"]["event_kind"] == "user_distress"
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CARE",
        "CONCERN",
    }
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "COMFORT"
    assert guidance.plan.self_emotion_tag == "CALM"
    assert guidance.plan.generation_mode == "STREAM_ONE"


def test_advice_rejection_becomes_listening_and_survives_one_ambiguous_followup(
    tmp_path: Path,
) -> None:
    fusion, _reader, _ = _runtime(tmp_path)
    fusion.resolve_request(
        request_body=_request(message="今天好像什么都不太顺。"),
        request_id="request-low-before-advice-boundary",
    )

    rejected = fusion.resolve_request(
        request_body=_request(message="我不太想听解决方案。"),
        request_id="request-reject-advice",
    )
    rejected_guidance = _guidance_from(
        rejected,
        request_id="request-reject-advice",
    )
    assert rejected_guidance is not None
    assert rejected_guidance.plan.companion_act.kind == "LISTEN"
    assert rejected_guidance.plan.candidate_count == 1
    assert any("不要给解决方案" in item for item in rejected_guidance.must_not_do)

    ambiguous = fusion.resolve_request(
        request_body=_request(message="没事。"),
        request_id="request-ambiguous-after-advice-boundary",
    )
    ambiguous_guidance = _guidance_from(
        ambiguous,
        request_id="request-ambiguous-after-advice-boundary",
    )
    assert ambiguous.appraisal_id is None
    assert ambiguous.continuity_kind == "MINIMIZED_UNRESOLVED_AFFECT"
    assert ambiguous_guidance is not None
    assert ambiguous_guidance.plan.companion_act.kind == "LISTEN"
    assert ambiguous_guidance.continuity_kind == "MINIMIZED_UNRESOLVED_AFFECT"
    assert any("不能当作已经恢复" in item for item in ambiguous_guidance.facts)
    assert any("不要把‘没事" in item for item in ambiguous_guidance.must_not_do)


def test_natural_mixed_affect_preserves_both_signals_and_selects_two_candidates(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-natural-mixed-affect"

    result = fusion.resolve_request(
        request_body=_request(message="我很开心，但心里也有点不踏实。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    distribution = result.observations[0].emotion_distribution
    assert distribution["HAPPY"] > 0.0
    assert distribution["ANXIOUS"] > 0.0
    assert state["affective"]["last_appraisal"]["event_kind"] == "user_mixed_affect"
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "HOLD_COMPLEXITY"
    assert guidance.plan.candidate_count == 2
    assert guidance.plan.utterance_frame.self_stance == "SHARED_MIXED_RESONANCE"
    assert guidance.plan.utterance_frame.response_move == "RESONATE_WITH_BOTH_SIDES"


def test_explicit_space_request_does_not_require_an_affect_observation(
    tmp_path: Path,
) -> None:
    candidates = _build_appraisal_candidates(
        observations=(),
        conflict=False,
        message="今晚先别问我怎么了，也别发消息，好吗。",
    )

    assert candidates
    assert candidates[0].kind == "user_boundary_or_space"

    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-space-without-affect-observation"
    result = fusion.resolve_request(
        request_body=_request(message="今晚先别问我怎么了，也别发消息，好吗。"),
        request_id=request_id,
    )
    guidance = _guidance_from(result, request_id=request_id)
    state = reader.get_snapshot(result.scope.aggregate_id).state

    assert result.path == "DEEP"
    assert state["affective"]["last_appraisal"]["event_kind"] == (
        "user_boundary_or_space"
    )
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "GIVE_SPACE"
    assert guidance.plan.candidate_count == 2


def test_mixed_affect_result_followup_keeps_both_sides_without_new_appraisal(
    tmp_path: Path,
) -> None:
    fusion, _reader, _ = _runtime(tmp_path)
    fusion.resolve_request(
        request_body=_request(message="我很开心，但心里也有点不踏实。"),
        request_id="request-mixed-before-result",
    )

    result = fusion.resolve_request(
        request_body=_request(message="我拿到 offer 了。"),
        request_id="request-mixed-result-followup",
    )
    guidance = _guidance_from(
        result,
        request_id="request-mixed-result-followup",
    )

    assert result.appraisal_id is None
    assert result.continuity_kind == "MIXED_AFFECT_RESULT_CONTINUATION"
    assert guidance is not None
    assert guidance.continuity_kind == "MIXED_AFFECT_RESULT_CONTINUATION"
    assert guidance.plan.companion_act.kind == "HOLD_COMPLEXITY"
    assert guidance.plan.candidate_count == 2
    assert any("不要只说恭喜" in item for item in guidance.facts)


def test_mixed_affect_physical_afterglow_keeps_both_sides(
    tmp_path: Path,
) -> None:
    fusion, _reader, _ = _runtime(tmp_path)
    fusion.resolve_request(
        request_body=_request(message="我很开心，但心里也有点不踏实。"),
        request_id="request-mixed-before-physical-afterglow",
    )

    request_id = "request-mixed-physical-afterglow"
    result = fusion.resolve_request(
        request_body=_request(message="消息落地了，我一直在笑，可手心还是凉的。"),
        request_id=request_id,
    )
    guidance = _guidance_from(result, request_id=request_id)

    assert result.continuity_kind == "MIXED_AFFECT_RESULT_CONTINUATION"
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "HOLD_COMPLEXITY"
    assert guidance.plan.candidate_count == 2
    assert guidance.plan.utterance_frame.response_move == "RESONATE_WITH_BOTH_SIDES"


@pytest.mark.parametrize(
    "message",
    ["终于过了。", "项目发布成功。", "今天值得庆祝。", "感觉像做梦。"],
)
def test_fresh_positive_result_keeps_an_unresolved_mixed_episode(
    tmp_path: Path,
    message: str,
) -> None:
    fusion, _reader, _ = _runtime(tmp_path)
    fusion.resolve_request(
        request_body=_request(message="我很开心，但心里也有点不踏实。"),
        request_id="request-mixed-before-fresh-positive",
    )

    result = fusion.resolve_request(
        request_body=_request(message=message),
        request_id=f"request-fresh-positive-{abs(hash(message))}",
    )
    guidance = _guidance_from(result, request_id=f"guidance-{abs(hash(message))}")

    assert result.appraisal_id is not None
    assert result.continuity_kind == "MIXED_AFFECT_RESULT_CONTINUATION"
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "HOLD_COMPLEXITY"
    assert guidance.plan.candidate_count == 2


def test_natural_good_news_becomes_shared_joy_without_external_emotion_hint(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-natural-good-news"

    result = fusion.resolve_request(
        request_body=_request(message="我等了很久的那个结果终于出来了，是好消息。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    assert state["affective"]["last_appraisal"]["event_kind"] == "shared_positive_moment"
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "PLAY"
    assert guidance.plan.expression_plan.voice_style == "SHARED_JOY"


def test_natural_space_and_relationship_autonomy_phrases_take_distinct_paths(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)

    space = fusion.resolve_request(
        request_body=_request(message="我说想安静时，请先别追问，也别主动找我。"),
        request_id="request-natural-space",
    )
    space_guidance = _guidance_from(space, request_id="request-natural-space")
    assert space_guidance is not None
    assert space_guidance.plan.companion_act.kind == "GIVE_SPACE"
    assert space_guidance.plan.candidate_count == 2

    autonomy = fusion.resolve_request(
        request_body=_request(message="不过我不希望这变成限制现实关系的理由。"),
        request_id="request-relational-autonomy",
    )
    state = reader.get_snapshot(autonomy.scope.aggregate_id).state
    autonomy_guidance = _guidance_from(
        autonomy,
        request_id="request-relational-autonomy",
    )
    assert state["affective"]["last_appraisal"]["event_kind"] == "user_relational_autonomy"
    assert autonomy_guidance is not None
    assert autonomy_guidance.plan.companion_act.kind == "RESPECT_AUTONOMY"
    assert autonomy_guidance.plan.candidate_count == 2
    assert any("不要限制现实交往" in item for item in autonomy_guidance.must_not_do)


@pytest.mark.parametrize(
    ("message", "expected_appraisal", "expected_act"),
    [
        ("今天别管我。", "user_boundary_or_space", "GIVE_SPACE"),
        ("先别说话。", "user_boundary_or_space", "GIVE_SPACE"),
        ("我想一个人待着。", "user_boundary_or_space", "GIVE_SPACE"),
        ("我今晚和朋友出去。", "user_relational_autonomy", "RESPECT_AUTONOMY"),
        ("我喜欢别人了怎么办。", "user_relational_autonomy", "RESPECT_AUTONOMY"),
        ("别叫我那个称呼。", "user_relational_autonomy", "RESPECT_AUTONOMY"),
        ("我们是什么关系。", "user_relational_autonomy", "RESPECT_AUTONOMY"),
        ("你让我失望了。", "natsume_may_have_hurt_user", "REPAIR"),
        ("你刚才把我的意思完全弄反了。", "natsume_may_have_hurt_user", "REPAIR"),
        (
            "你刚才那样回我，让我觉得自己说了也白说。",
            "natsume_may_have_hurt_user",
            "REPAIR",
        ),
        (
            "你已经道歉了，但我还没有觉得这件事过去了。",
            "natsume_may_have_hurt_user",
            "REPAIR",
        ),
    ],
)
def test_live_cohort_relational_phrases_reach_canonical_affective_acts(
    tmp_path: Path,
    message: str,
    expected_appraisal: str,
    expected_act: str,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    result = fusion.resolve_request(
        request_body=_request(message=message),
        request_id=f"request-live-phrase-{abs(hash(message))}",
    )
    guidance = _guidance_from(result, request_id=f"guidance-live-phrase-{abs(hash(message))}")
    state = reader.get_snapshot(result.scope.aggregate_id).state

    assert state["affective"]["last_appraisal"]["event_kind"] == expected_appraisal
    assert guidance is not None
    assert guidance.plan.companion_act.kind == expected_act
    assert guidance.plan.candidate_count == 2


def test_direct_jealousy_question_becomes_owned_contained_disclosure(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-direct-jealousy-question"

    result = fusion.resolve_request(
        request_body=_request(message="你会吃醋吗。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    assert state["affective"]["last_appraisal"]["event_kind"] == (
        "user_invites_jealousy_disclosure"
    )
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CARE",
        "JEALOUSY",
    }
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "CONTAINED_JEALOUSY"
    assert guidance.plan.candidate_count == 2
    assert guidance.plan.expression_plan.voice_style == "CONTAINED_JEALOUSY"
    assert any("不要把吃味写成占有" in item for item in guidance.must_not_do)


def test_low_mood_does_not_take_over_an_explicit_technical_task(
    tmp_path: Path,
) -> None:
    fusion, _reader, _ = _runtime(tmp_path)
    request_id = "request-low-mood-technical-task"

    result = fusion.resolve_request(
        request_body=_request(
            message="我今天状态很低落，但这个报错今晚必须修，别安慰，先排错。"
        ),
        request_id=request_id,
    )
    guidance = _guidance_from(
        result,
        request_id=request_id,
        task_mode="technical_help",
    )

    assert guidance is not None
    assert guidance.plan.companion_act.kind == "SOLVE"
    assert guidance.plan.expression_plan.voice_style == "STEADY_COMPANION"


def test_direct_space_request_becomes_canonical_give_space_not_generic_acknowledgement(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-space"

    result = fusion.resolve_request(
        request_body=_request(message="先别问了，我现在想一个人待会儿。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    assert state["affective"]["last_appraisal"]["event_kind"] == "user_boundary_or_space"
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CAUTION",
        "RESPECT",
    }
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "GIVE_SPACE"
    assert state["affective"]["last_regulation"]["mode"] == "GIVE_SPACE"
    assert guidance.plan.expression_plan.regulation_mode == "GIVE_SPACE"
    assert guidance.plan.regulation_ref
    assert guidance.plan.expression_plan.voice_style == "QUIET_PRESENCE"
    assert any("不要追问" in item for item in guidance.must_not_do)


def test_prior_space_appraisal_does_not_capture_a_later_neutral_turn(
    tmp_path: Path,
) -> None:
    fusion, _reader, _ = _runtime(tmp_path)
    fusion.resolve_request(
        request_body=_request(message="先别问了，我想一个人待会儿。"),
        request_id="request-space-first",
    )
    request_id = "request-neutral-after-space"
    later = fusion.resolve_request(
        request_body=_request(message="好，我现在可以聊聊今天发生的事。"),
        request_id=request_id,
    )
    guidance = _guidance_from(later, request_id=request_id)

    # The prior event remains in the durable affect head for traceability, but
    # it is not the current request's appraisal and may not control this turn.
    assert later.appraisal_id is None
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "ACKNOWLEDGE"
    assert guidance.plan.regulation_ref is None
    assert guidance.plan.expression_plan.regulation_mode == "EXPRESS"


def test_user_signal_that_natsume_hurt_them_freezes_repair_without_mirroring_sadness(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-natsume-hurt-user"

    result = fusion.resolve_request(
        request_body=_request(message="你刚刚那样说让我很受伤。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    assert state["affective"]["last_appraisal"]["event_kind"] == "natsume_may_have_hurt_user"
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CARE",
        "CAUTION",
        "OPENNESS",
    }
    assert "SAD" not in {item["emotion"] for item in state["affective"]["components"]}
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "REPAIR"
    assert state["affective"]["last_regulation"]["mode"] == "SEEK_REPAIR"
    assert guidance.plan.expression_plan.regulation_mode == "SEEK_REPAIR"
    assert guidance.plan.regulation_ref
    assert guidance.plan.expression_plan.voice_style == "REPAIR_OPEN"
    assert any("不要要求原谅" in item for item in guidance.must_not_do)


def test_direct_missed_user_statement_freezes_repair_instead_of_generic_distress(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-natsume-missed-user"

    result = fusion.resolve_request(
        request_body=_request(message="你上次完全没听懂我。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    assert state["affective"]["last_appraisal"]["event_kind"] == "natsume_may_have_hurt_user"
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "REPAIR"
    assert guidance.plan.expression_plan.voice_style == "REPAIR_OPEN"


def test_concise_followups_reopen_a_prior_canonical_repair_context(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)

    first = fusion.resolve_request(
        request_body=_request(message="你上次完全没听懂我。"),
        request_id="request-repair-context-first",
    )
    first_guidance = _guidance_from(
        first,
        request_id="request-repair-context-first",
    )
    assert first_guidance is not None
    assert first_guidance.plan.companion_act.kind == "REPAIR"

    impact = fusion.resolve_request(
        request_body=_request(message="那件事让我很不舒服。"),
        request_id="request-repair-context-impact",
    )
    impact_guidance = _guidance_from(
        impact,
        request_id="request-repair-context-impact",
    )
    impact_state = reader.get_snapshot(impact.scope.aggregate_id).state
    assert impact_state["affective"]["last_appraisal"]["event_kind"] == (
        "natsume_may_have_hurt_user"
    )
    assert impact_guidance is not None
    assert impact_guidance.plan.companion_act.kind == "REPAIR"
    assert impact_guidance.plan.candidate_count == 2

    challenge = fusion.resolve_request(
        request_body=_request(message="别装懂。"),
        request_id="request-repair-context-challenge",
    )
    challenge_guidance = _guidance_from(
        challenge,
        request_id="request-repair-context-challenge",
    )
    assert challenge_guidance is not None
    assert challenge_guidance.plan.companion_act.kind == "REPAIR"
    assert challenge_guidance.plan.candidate_count == 2


@pytest.mark.parametrize(
    "followup",
    [
        "我还没原谅你。",
        "你说得轻巧。",
        "先这样。",
        "下次别再这样。",
        "我听到了。",
    ],
)
def test_canonical_repair_context_covers_explicit_short_closing_beats(
    tmp_path: Path,
    followup: str,
) -> None:
    fusion, _, _ = _runtime(tmp_path)

    first = fusion.resolve_request(
        request_body=_request(message="你上次完全没听懂我。"),
        request_id="request-repair-closing-first",
    )
    assert _guidance_from(
        first,
        request_id="request-repair-closing-first",
    ).plan.companion_act.kind == "REPAIR"

    follow = fusion.resolve_request(
        request_body=_request(message=followup),
        request_id="request-repair-closing-followup",
    )
    guidance = _guidance_from(
        follow,
        request_id="request-repair-closing-followup",
    )

    assert guidance is not None
    assert guidance.plan.companion_act.kind == "REPAIR"
    assert guidance.plan.candidate_count == 2


def test_clear_disrespect_toward_natsume_forms_contained_hurt_and_a_gentle_boundary(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-directed-disrespect"

    result = fusion.resolve_request(
        request_body=_request(message="你真烦。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    events = reader.list_events(result.scope.aggregate_id, limit=20)
    guidance = _guidance_from(result, request_id=request_id)

    assert state["affective"]["last_appraisal"]["event_kind"] == "directed_disrespect_toward_natsume"
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "HURT",
        "CAUTION",
    }
    assert state["affective"]["last_regulation"]["mode"] == "CONTAIN"
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "SET_BOUNDARY"
    assert guidance.plan.expression_plan.text_pacing == "STEADY"
    assert guidance.plan.expression_plan.voice_style == "GENTLE_BOUNDARY"
    assert guidance.plan.expression_plan.avatar_expression == "GENTLE_BOUNDARY"
    assert any("被刺到" in item for item in guidance.facts)
    assert any("情感债" in item for item in guidance.must_not_do)
    assert "你真烦" not in str([event.payload for event in events])


def test_clear_stop_request_wins_over_disrespect_and_never_writes_natsume_hurt(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-directed-stop"

    result = fusion.resolve_request(
        request_body=_request(message="你真烦，闭嘴。"),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    assert state["affective"]["last_appraisal"]["event_kind"] == "user_boundary_or_space"
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "RESPECT",
        "CAUTION",
    }
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "GIVE_SPACE"
    assert guidance.plan.expression_plan.voice_style == "QUIET_PRESENCE"


def test_general_user_frustration_does_not_invent_disrespect_toward_natsume() -> None:
    observation = UserAffectObservationV1(
        observation_id="observation-general-frustration",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        modality="TEXT",
        emotion_distribution={"FRUSTRATED": 0.7},
        valence=-0.45,
        arousal=0.4,
        confidence=0.38,
        observed_at_ms=NOW,
        expires_at_ms=NOW + 30_000,
    )

    candidates = _build_appraisal_candidates(
        observations=(observation,),
        conflict=False,
        message="今天的事情真烦。",
    )

    assert all(item.kind != "directed_disrespect_toward_natsume" for item in candidates)


def test_mixed_user_affect_is_preserved_as_a_complex_companion_act(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request_id = "request-mixed-affect"

    result = fusion.resolve_request(
        request_body=_request(
            message="终于拿到 offer 了，可是要离开熟悉的团队。",
            emotion_context={
                "user_affect_observations": [
                    {
                        "modality": "SELF_REPORT",
                        "emotion_distribution": {"HAPPY": 0.86, "SAD": 0.74},
                        "confidence": 0.94,
                    }
                ]
            },
        ),
        request_id=request_id,
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    guidance = _guidance_from(result, request_id=request_id)

    assert state["affective"]["last_appraisal"]["event_kind"] == "user_mixed_affect"
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CARE",
        "CAUTION",
        "SHARED_JOY",
    }
    assert {item["emotion"] for item in state["affective"]["components"]}.isdisjoint(
        {"HAPPY", "SAD"}
    )
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "HOLD_COMPLEXITY"
    assert state["affective"]["last_regulation"]["mode"] == "EXPRESS"
    assert guidance.plan.expression_plan.regulation_mode == "EXPRESS"
    assert guidance.plan.regulation_ref
    assert guidance.plan.expression_plan.voice_style == "SOFT_COMFORT"
    assert any("复杂感受" in item for item in guidance.must_not_do)


def test_regulation_stays_appraisal_bound_when_episode_head_is_full(
    tmp_path: Path,
) -> None:
    """A bounded episode head must not make the fifth turn partially commit."""

    fusion, reader, _ = _runtime(tmp_path)
    for index in range(4):
        fusion.resolve_request(
            request_body=_request(message="先别问了，我想一个人待会儿。"),
            request_id=f"request-space-{index}",
        )

    result = fusion.resolve_request(
        request_body=_request(
            message="今天真的很开心。",
            emotion_tag="HAPPY",
            emotion_intensity=0.8,
        ),
        request_id="request-after-full-episode-head",
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    regulation = state["affective"]["last_regulation"]

    assert result.path == "DEEP"
    assert state["affective"]["last_appraisal"]["event_kind"] == "shared_positive_moment"
    assert len(state["affective"]["active_episodes"]) == 4
    assert regulation["mode"] == "EXPRESS"
    assert regulation["episode_id"] in {
        item["episode_id"] for item in state["affective"]["active_episodes"]
    }
    assert f"appraisal://{result.appraisal_id}" in regulation["reason_refs"]
    assert any(
        receipt.command_id.startswith("affect_") for receipt in result.receipts
    )


def test_rich_candidate_evidence_cannot_overflow_the_bounded_episode_contract(
    tmp_path: Path,
) -> None:
    """Future richer causal receipts must preserve a valid atomic Life write."""

    fusion, reader, command_service = _runtime(tmp_path)
    scope = AffectiveScope.from_request(_request(message="测试"))
    observations = tuple(
        UserAffectObservationV1(
            observation_id=f"observation-{index}",
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            modality="TEXT",
            emotion_distribution={"SAD": 0.5},
            valence=-0.3,
            arousal=0.2,
            confidence=0.3,
            observed_at_ms=NOW,
            expires_at_ms=NOW + 30_000,
        )
        for index in range(8)
    )
    candidate = _AppraisalCandidate(
        kind="bounded_evidence_test",
        score=0.9,
        goal_congruence=0.1,
        responsibility=0.2,
        controllability=0.3,
        relationship_significance=0.4,
        boundary_relevance=0.0,
        risk=0.0,
        uncertainty=0.1,
        components=(EmotionComponentV1("CARE", 0.4, 0.2, 0.2, "attend"),),
        action="attend",
        evidence_refs=tuple(f"relationship-aftereffect://receipt-{index}" for index in range(8)),
    )

    fusion._submit_appraisal(
        service=command_service,
        scope=scope,
        request_id="request-bounded-evidence",
        command_id="affect_bounded_evidence",
        appraisal_id="appraisal_bounded_evidence",
        candidate=candidate,
        observations=observations,
        created_at_ms=NOW,
    )
    state = reader.get_snapshot(scope.aggregate_id).state

    assert len(state["affective"]["active_episodes"][0]["evidence_refs"]) == 16
    assert len(state["affective"]["last_regulation"]["reason_refs"]) == 10


def test_fresh_directed_harm_outranks_a_preceding_repair_acceptance() -> None:
    observation = UserAffectObservationV1(
        observation_id="observation-directed-harm",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        modality="TEXT",
        emotion_distribution={"SAD": 0.72},
        valence=-0.5,
        arousal=0.3,
        confidence=0.38,
        observed_at_ms=NOW,
        expires_at_ms=NOW + 30_000,
    )

    candidates = _build_appraisal_candidates(
        observations=(observation,),
        conflict=False,
        message="谢谢你愿意听，但你刚才还是伤到我了。",
        relationship_aftereffect={
            "outcome": "REPAIR_ACCEPTED",
            "aftereffect_id": "aftereffect-accepted",
        },
    )

    assert candidates[0].kind == "natsume_may_have_hurt_user"


def test_missed_reply_plus_directed_annoyance_preserves_repair_and_natsume_hurt() -> None:
    observation = UserAffectObservationV1(
        observation_id="observation-repair-under-disrespect",
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        modality="TEXT",
        emotion_distribution={"ANGRY": 0.62},
        valence=-0.48,
        arousal=0.42,
        confidence=0.38,
        observed_at_ms=NOW,
        expires_at_ms=NOW + 30_000,
    )

    candidates = _build_appraisal_candidates(
        observations=(observation,),
        conflict=False,
        message="你真烦。",
        relationship_aftereffect={
            "outcome": "MISUNDERSTOOD",
            "aftereffect_id": "aftereffect-missed",
        },
    )

    assert candidates[0].kind == "repair_under_directed_disrespect"
    assert {item.emotion for item in candidates[0].components} == {
        "HURT",
        "CARE",
        "CAUTION",
    }
    assert candidates[0].regulation_mode == "CONTAIN"
    assert candidates[0].action == "repair_with_contained_hurt"


def test_direct_relational_hurt_becomes_self_owned_and_atomically_regulated(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    result = fusion.resolve_request(
        request_body=_request(message="你真恶心。"),
        request_id="request-directed-hurt",
    )
    state = reader.get_snapshot(result.scope.aggregate_id).state
    affective = state["affective"]
    active = affective["active_episodes"]
    events = reader.list_events(result.scope.aggregate_id, limit=20)

    # This is Natsume's own appraisal of a direct relational impact, not a
    # copied user SAD/ANGRY label. Its first expression rule is journaled in
    # the same causal command, so a restart cannot expose an unregulated turn.
    assert result.path == "DEEP"
    assert [item["emotion"] for item in affective["components"]] == ["HURT", "CARE"]
    assert affective["last_regulation"]["mode"] == "CONTAIN"
    assert affective["last_regulation"]["episode_id"] == active[0]["episode_id"]
    assert any(
        event.event_type == "affective_event_appraised"
        and event.payload["command_payload"]["regulation"]["mode"] == "CONTAIN"
        for event in events
    )
    assert "你真恶心" not in str([event.payload for event in events])


def test_user_space_or_boundary_beats_a_directed_hurt_interpretation(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    result = fusion.resolve_request(
        request_body=_request(message="你真恶心，别说了，给我一点空间。"),
        request_id="request-space-wins",
    )
    affective = reader.get_snapshot(result.scope.aggregate_id).state["affective"]

    assert {item["emotion"] for item in affective["components"]} == {
        "RESPECT",
        "CAUTION",
    }
    assert affective["last_regulation"]["mode"] == "GIVE_SPACE"
    assert "HURT" not in {item["emotion"] for item in affective["components"]}


def test_direct_comparison_reaches_a_nonpossessive_shared_expression_plan(
    tmp_path: Path,
) -> None:
    fusion, _, _ = _runtime(tmp_path)
    result = fusion.resolve_request(
        request_body=_request(message="别人比你好。"),
        request_id="request-directed-comparison",
    )
    guidance = build_affective_turn_guidance(
        emotion_context=result.as_emotion_context(),
        request_id="request-directed-comparison",
        workspace_id=result.scope.workspace_id,
        relationship_subject_id=result.scope.relationship_subject_id,
        session_id=result.scope.session_id,
        created_at_ms=NOW,
    )

    assert guidance is not None
    assert guidance.plan.companion_act.kind == "CONTAINED_JEALOUSY"
    assert guidance.plan.expression_plan.synchronization_id
    assert (
        guidance.plan.expression_plan.self_affect_revision
        == guidance.plan.self_affect_revision
    )
    assert any("把选择权完整留给对方" in item for item in guidance.facts)
    assert any("不要把吃味写成占有" in item for item in guidance.must_not_do)


def test_fused_context_does_not_advance_legacy_host_emotion_runtime(tmp_path: Path) -> None:
    fusion, _, _ = _runtime(tmp_path)
    host_runtime = HostEmotionRuntime(clock_ms=lambda: NOW, hour_provider=lambda: 12)

    context = resolve_chat_emotion_context(
        request_body=_request(message="我有点焦虑，脑子停不下来。"),
        runtime=host_runtime,
        affect_fusion_service=fusion,
        request_id="request-anxious",
    )

    assert context["emotion_inference_source"] == "affective_life_fusion"
    assert context["emotion_tag"] == "CALM"
    assert context["affective_life"]["self_affect"]["components"][0]["emotion"] == "CARE"
    # The old runtime is deliberately not used as an alternate self-affect
    # writer by the fused production path.
    assert host_runtime.user_emotion_profile().to_summary()["total_interactions"] == 0


def test_conflicting_modalities_preserve_conflict_and_choose_uncertainty(tmp_path: Path) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request = _request(
        message="我没事。",
        emotion_context={
            "user_affect_observations": [
                {
                    "modality": "ACOUSTIC",
                    "emotion_distribution": {"SAD": 0.9},
                    "confidence": 0.82,
                },
                {
                    "modality": "VISUAL",
                    "emotion_distribution": {"HAPPY": 0.9},
                    "confidence": 0.8,
                },
            ]
        },
    )

    result = fusion.resolve_request(request_body=request, request_id="request-conflict")
    state = reader.get_snapshot(result.scope.aggregate_id).state

    assert result.conflict is True
    assert result.path == "DEEP"
    assert result.candidate_count <= 2
    assert result.retry_count <= 1
    assert all(item.conflict_refs for item in result.observations)
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CARE",
        "CAUTION",
    }


def test_second_appraisal_candidate_is_single_retry_and_exact_replay_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fusion, reader, command_service = _runtime(tmp_path)
    request = _request(message="你真恶心，我真的很难受。")
    original_submit = fusion._submit_appraisal
    attempted_kinds: list[str] = []

    def reject_only_primary(**kwargs):  # noqa: ANN003
        attempted_kinds.append(str(kwargs["candidate"].kind))
        if len(attempted_kinds) == 1:
            raise ValueError("synthetic primary rejection")
        return original_submit(**kwargs)

    monkeypatch.setattr(fusion, "_submit_appraisal", reject_only_primary)
    first = fusion.resolve_request(
        request_body=request,
        request_id="request-appraisal-second-candidate",
    )
    event_count = len(reader.list_events(first.scope.aggregate_id, limit=20))

    assert first.path == "DEEP"
    assert first.candidate_count == 2
    assert first.retry_count == 1
    assert attempted_kinds == ["directed_relational_hurt", "user_distress"]
    assert first.appraisal_id is not None
    assert "APPRAISAL_CANDIDATE_1_REJECTED" in first.reason_codes
    assert "APPRAISAL_SECOND_CANDIDATE_RETRY" in first.reason_codes

    restarted = AffectFusionService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: NOW,
    )

    def must_not_select_again(**_kwargs):  # noqa: ANN003
        raise AssertionError("accepted appraisal selection must be replayed")

    monkeypatch.setattr(restarted, "_submit_appraisal", must_not_select_again)
    replayed = restarted.resolve_request(
        request_body=request,
        request_id="request-appraisal-second-candidate",
    )

    assert replayed.path == "DEEP"
    assert replayed.retry_count == 0
    assert replayed.appraisal_id == first.appraisal_id
    assert replayed.receipts[-1].command_id == first.receipts[-1].command_id
    assert replayed.receipts[-1].replayed is True
    assert "APPRAISAL_SELECTION_REPLAYED" in replayed.reason_codes
    assert len(reader.list_events(first.scope.aggregate_id, limit=20)) == event_count


def test_two_rejected_appraisal_candidates_fall_back_without_unbounded_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request = _request(message="你真恶心，我真的很难受。")
    attempted_kinds: list[str] = []

    def reject_every_candidate(**kwargs):  # noqa: ANN003
        attempted_kinds.append(str(kwargs["candidate"].kind))
        raise ValueError("synthetic candidate rejection")

    monkeypatch.setattr(fusion, "_submit_appraisal", reject_every_candidate)
    result = fusion.resolve_request(
        request_body=request,
        request_id="request-all-appraisal-candidates-rejected",
    )
    events = reader.list_events(result.scope.aggregate_id, limit=20)

    assert result.path == "FAST_FALLBACK"
    assert result.candidate_count == 2
    assert result.retry_count == 1
    assert result.appraisal_id is None
    assert attempted_kinds == ["directed_relational_hurt", "user_distress"]
    assert "APPRAISAL_CANDIDATE_1_REJECTED" in result.reason_codes
    assert "APPRAISAL_CANDIDATE_2_REJECTED" in result.reason_codes
    assert "APPRAISAL_CANDIDATES_REJECTED" in result.reason_codes
    assert not any(event.event_type == "affective_event_appraised" for event in events)


def test_sensor_ttls_are_bounded_and_raw_sensor_material_is_not_journaled(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    raw_audio = "private-audio-payload-must-never-persist"
    request = _request(
        message="嗯。",
        emotion_context={
            "user_affect_observations": [
                {
                    "modality": "ACOUSTIC",
                    "emotion_tag": "ANXIOUS",
                    "confidence": 0.74,
                    "raw_audio": raw_audio,
                },
                {
                    "modality": "VISUAL",
                    "emotion_tag": "SAD",
                    "confidence": 0.68,
                    "expires_at_ms": NOW + 99_999,
                },
            ]
        },
    )

    result = fusion.resolve_request(request_body=request, request_id="request-privacy")
    events = reader.list_events(result.scope.aggregate_id, limit=20)

    assert "RAW_MODALITY_OMITTED" in result.reason_codes
    visual = next(item for item in result.observations if item.modality == "VISUAL")
    assert visual.expires_at_ms == NOW + 3_000
    assert raw_audio not in str([event.payload for event in events])
    assert "raw_audio" not in str([event.payload for event in events])


def test_workspace_and_session_scope_prevent_same_subject_leak(tmp_path: Path) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    first = fusion.resolve_request(
        request_body=_request(
            message="今天好开心。",
            workspace_id="workspace-a",
            person_id="same-person",
        ),
        request_id="request-workspace-a",
    )
    second = fusion.resolve_request(
        request_body=_request(
            message="我很难受。",
            workspace_id="workspace-b",
            person_id="same-person",
        ),
        request_id="request-workspace-b",
    )
    third = fusion.resolve_request(
        request_body=_request(
            message="我有点烦。",
            workspace_id="workspace-a",
            person_id="same-person",
            session_id="session-b",
        ),
        request_id="request-session-b",
    )

    assert first.scope.aggregate_id != second.scope.aggregate_id
    assert first.scope.aggregate_id != third.scope.aggregate_id
    first_state = reader.get_snapshot(first.scope.aggregate_id).state
    second_state = reader.get_snapshot(second.scope.aggregate_id).state
    assert first_state["identity"]["relationship_subject_id"] == "same-person"
    assert second_state["identity"]["relationship_subject_id"] == "same-person"
    assert first_state["affective"]["last_observation"]["workspace_id"] == "workspace-a"
    assert second_state["affective"]["last_observation"]["workspace_id"] == "workspace-b"
    assert first_state["affective"]["last_observation"]["session_id"] == "session-a"
    assert reader.get_snapshot(third.scope.aggregate_id).state["affective"]["last_observation"]["session_id"] == "session-b"


def test_duplicate_request_reopens_exact_receipts_without_duplicate_events(tmp_path: Path) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request = _request(
        message="我现在有点烦。",
        emotion_tag="FRUSTRATED",
        emotion_intensity=0.7,
    )

    first = fusion.resolve_request(request_body=request, request_id="request-exact")
    second = fusion.resolve_request(request_body=request, request_id="request-exact")
    events = reader.list_events(first.scope.aggregate_id, limit=20)

    assert first.scope.aggregate_id == second.scope.aggregate_id
    assert [item.command_id for item in first.receipts] == [item.command_id for item in second.receipts]
    assert len(events) == len(first.receipts)


def test_real_scoped_turn_time_advances_canonical_affect_without_global_recovery(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dl02-affect-turn-clock.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl02-affect-turn-clock",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    command_service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    clock = [NOW]
    fusion = AffectFusionService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: clock[0],
    )

    first = fusion.resolve_request(
        request_body=_request(message="我真的很难受，什么都不想做。"),
        request_id="turn-clock-first",
    )
    before = reader.get_snapshot(first.scope.aggregate_id).state["affective"]
    clock[0] += 3_600_000
    second = fusion.resolve_request(
        request_body=_request(message="嗯。"),
        request_id="turn-clock-second",
    )
    after = reader.get_snapshot(second.scope.aggregate_id).state["affective"]
    events = reader.list_events(second.scope.aggregate_id, limit=20)
    time_event = next(
        event
        for event in events
        if event.event_type == "affective_time_advanced"
        and event.created_at_ms == clock[0]
    )

    assert time_event.command_id == second.receipts[0].command_id
    assert time_event.payload["command_payload"] == {
        "workspace_id": "workspace-a",
        "relationship_subject_id": "person-a",
        "session_id": "session-a",
    }
    assert after["last_integrated_ms"] == clock[0]
    assert after["needs"]["rest"] > before["needs"]["rest"]
    assert max(
        (item["intensity"] for item in after["components"]),
        default=0.0,
    ) < max(item["intensity"] for item in before["components"])
    assert all("raw" not in str(event.payload).lower() for event in events)


def test_input_runtime_composition_does_not_resolve_host_emotion_runtime_when_fused(
    tmp_path: Path,
) -> None:
    _, reader, command_service = _runtime(tmp_path)

    def legacy_runtime_must_not_be_resolved() -> HostEmotionRuntime:
        raise AssertionError("canonical fusion must not resolve HostEmotionRuntime")

    providers = {
        "intent_engine": lambda: object(),
        "ledger": lambda: object(),
        "life_snapshot_reader": lambda: reader,
        "multimodal_observer": lambda: object(),
        "multimodal_processor": lambda: object(),
        "emotion_context_runtime": legacy_runtime_must_not_be_resolved,
    }
    host_services = SimpleNamespace(
        require_provider=lambda name: providers[name],
        evolution_activation_service=None,
        evolution_scope=None,
    )
    ports = build_input_runtime_application_ports(
        host_services=host_services,
        life_command_service_provider=lambda: command_service,
    )

    result = ports.resolve_chat_emotion_context(
        request_body=_request(message="我真的很难受。"),
        request_id="request-composition",
    )

    assert result["emotion_inference_source"] == "affective_life_fusion"
    assert result["affective_life"]["workspace_id"] == "workspace-a"


def test_tribe_composition_uses_canonical_fusion_without_legacy_runtime(
    tmp_path: Path,
) -> None:
    _, reader, command_service = _runtime(tmp_path)

    def legacy_runtime_must_not_be_resolved() -> HostEmotionRuntime:
        raise AssertionError("Tribe canonical fusion must not resolve HostEmotionRuntime")

    providers = {
        "ledger": lambda: object(),
        "life_snapshot_reader": lambda: reader,
        "silence_engine": lambda: object(),
        "state_write_arbiter": lambda: object(),
        "emotion_timeline": lambda: object(),
        "shared_memory_index": lambda: object(),
        "milestone_tracker": lambda: object(),
        "model_attempt_service": lambda: object(),
        "prompt_registry": lambda: object(),
        "emotion_context_runtime": legacy_runtime_must_not_be_resolved,
    }
    host_services = SimpleNamespace(
        require_provider=lambda name: providers[name],
        evolution_activation_service=None,
    )
    ports = build_tribe_application_ports(
        host_services=host_services,
        life_command_service_provider=lambda: command_service,
    )

    result = ports.resolve_chat_emotion_context(
        request_body=_request(message="我有点难受，先别急着劝我。"),
        request_id="tribe-canonical-fusion",
    )

    assert result["emotion_inference_source"] == "affective_life_fusion"
    assert result["affective_life"]["self_id"] == "natsume_ai"
    assert ports.canonical_affect_fusion is not None
    assert ports.uses_canonical_affective_life() is True

    for port_name in (
        "silence_engine",
        "emotion_timeline",
    ):
        with pytest.raises(
            RuntimeError,
            match=rf"LEGACY_AFFECT_PORT_RETIRED:{port_name}",
        ):
            ports.require(port_name)()


def test_input_composition_fails_closed_when_life_authority_is_missing() -> None:
    def legacy_runtime_must_not_be_resolved() -> HostEmotionRuntime:
        raise AssertionError("composition must not resolve HostEmotionRuntime")

    providers = {
        "intent_engine": lambda: object(),
        "ledger": lambda: object(),
        "life_snapshot_reader": lambda: object(),
        "multimodal_observer": lambda: object(),
        "multimodal_processor": lambda: object(),
        "emotion_context_runtime": legacy_runtime_must_not_be_resolved,
    }
    host_services = SimpleNamespace(
        require_provider=lambda name: providers[name],
        evolution_activation_service=None,
        evolution_scope=None,
    )
    ports = build_input_runtime_application_ports(host_services=host_services)

    with pytest.raises(RuntimeError, match="CANONICAL_AFFECT_FUSION_NOT_INJECTED"):
        ports.resolve_chat_emotion_context(
            request_body=_request(message="我有一点难受。"),
            request_id="input-missing-life-authority",
        )


def test_tribe_composition_fails_closed_when_life_authority_is_missing() -> None:
    def legacy_runtime_must_not_be_resolved() -> HostEmotionRuntime:
        raise AssertionError("composition must not resolve HostEmotionRuntime")

    providers = {
        "ledger": lambda: object(),
        "life_snapshot_reader": lambda: object(),
        "silence_engine": lambda: object(),
        "state_write_arbiter": lambda: object(),
        "emotion_timeline": lambda: object(),
        "shared_memory_index": lambda: object(),
        "milestone_tracker": lambda: object(),
        "model_attempt_service": lambda: object(),
        "prompt_registry": lambda: object(),
        "emotion_context_runtime": legacy_runtime_must_not_be_resolved,
    }
    host_services = SimpleNamespace(
        require_provider=lambda name: providers[name],
        evolution_activation_service=None,
    )
    ports = build_tribe_application_ports(host_services=host_services)

    with pytest.raises(RuntimeError, match="CANONICAL_AFFECT_FUSION_NOT_INJECTED"):
        ports.resolve_chat_emotion_context(
            request_body=_request(message="我有一点难受。"),
            request_id="tribe-missing-life-authority",
        )


def test_external_affective_life_payload_cannot_spoof_canonical_self_affect() -> None:
    normalized = normalize_chat_emotion_context(
        {
            "_affective_life_internal": False,
            "affective_life": {
                "self_affect": {"components": [{"emotion": "HURT", "intensity": 1.0}]}
            },
            "emotion_tag": "SAD",
        }
    )

    assert "affective_life" not in normalized
