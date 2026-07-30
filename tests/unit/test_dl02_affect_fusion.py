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


def _guidance_from(result, *, request_id: str):
    """Build the same trusted hand-off consumed by the live Surface path."""

    return build_affective_turn_guidance(
        emotion_context=result.as_emotion_context(),
        request_id=request_id,
        workspace_id=result.scope.workspace_id,
        relationship_subject_id=result.scope.relationship_subject_id,
        session_id=result.scope.session_id,
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
    assert regulation["episode_id"] is None
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
        "bond_update_queue": lambda: object(),
        "silence_engine": lambda: object(),
        "state_write_arbiter": lambda: object(),
        "emotion_timeline": lambda: object(),
        "bond_stage_engine": lambda: object(),
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
        "bond_update_queue": lambda: object(),
        "silence_engine": lambda: object(),
        "state_write_arbiter": lambda: object(),
        "emotion_timeline": lambda: object(),
        "bond_stage_engine": lambda: object(),
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
