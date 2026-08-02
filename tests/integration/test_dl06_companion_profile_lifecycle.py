from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.affect_fusion import AffectFusionService
from host_brain.digital_life.affective_expression_delivery import (
    build_sentence_expression_delivery,
)
from host_brain.digital_life.affective_turn_planner import (
    build_affective_turn_guidance,
)
from host_brain.digital_life.companion_profile_lifecycle import (
    CompanionProfileLifecycleService,
)
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from natsume_contracts.life import (
    CompanionProfilePreferencesV1,
    CompanionProfileStateV1,
)


NOW = 1_900_000_000_000
DAY_MS = 86_400_000


def _request(*, session_id: str, message: str = "谢谢你，我觉得这样挺舒服的。") -> SimpleNamespace:
    return SimpleNamespace(
        workspace_id="workspace-profile",
        person_id="person-profile",
        session_id=session_id,
        emotion_context={},
        message=message,
        emotion_tag=None,
        emotion_intensity=None,
        multimodal_observations={},
        mood_state=None,
        emotion_range_hint=None,
    )


def _runtime(tmp_path: Path):  # noqa: ANN201
    database = tmp_path / "dl06-companion-profile.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl06-companion-profile",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    clock = {"now": NOW}
    command_service = LifeCommandService(repository, clock=lambda: int(clock["now"]))
    reader = LifeSnapshotReader(repository)
    profile_holder: dict[str, CompanionProfileLifecycleService] = {}
    relationship = RelationshipLifecycleService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        companion_profile_lifecycle_service_provider=lambda: profile_holder.get("service"),
        clock_ms=lambda: int(clock["now"]),
    )
    profile = CompanionProfileLifecycleService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: int(clock["now"]),
    )
    profile_holder["service"] = profile
    fusion = AffectFusionService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        relationship_lifecycle_service_provider=lambda: relationship,
        clock_ms=lambda: int(clock["now"]),
    )
    return database, clock, reader, relationship, profile, fusion


def _accepted_response(*, ordinal: int, created_at_ms: int) -> dict[str, object]:
    return {
        "status": "APPLIED",
        "event_id": f"foreground-profile-{ordinal}",
        "command_id": f"foreground-profile-command-{ordinal}",
        "created_at_ms": created_at_ms,
    }


def _strong_helpful_aftereffect(
    *,
    relationship: RelationshipLifecycleService,
    clock: dict[str, int],
    ordinal: int,
) -> str:
    response = _accepted_response(ordinal=ordinal, created_at_ms=clock["now"])
    relationship.provision_accepted_response(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id=f"session-profile-{ordinal}",
        request_id=f"response-profile-{ordinal}",
        response_receipt=response,
        response_text="我在，慢一点也没关系。",
    )
    clock["now"] += 1_000
    result = relationship.record_explicit_response_feedback(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id=f"session-profile-{ordinal}",
        request_id=f"aftereffect-profile-{ordinal}",
        response_receipt_ref=f"life-event://{response['event_id']}",
        feedback="HELPFUL",
        source_ref=f"secure-ingress-audit://profile-aftereffect-{ordinal}",
    )
    assert result.aftereffect is not None
    assert result.aftereffect.outcome == "HELPFUL"
    assert result.aftereffect.helpfulness == 0.95
    return result.aftereffect.aftereffect_id


def _low_confidence_text_aftereffect(
    *,
    relationship: RelationshipLifecycleService,
    clock: dict[str, int],
    ordinal: int,
) -> str:
    relationship.provision_accepted_response(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id=f"session-profile-weak-{ordinal}",
        request_id=f"response-profile-weak-{ordinal}",
        response_receipt=_accepted_response(ordinal=ordinal, created_at_ms=clock["now"]),
        response_text="我在，慢一点也没关系。",
    )
    clock["now"] += 1_000
    result = relationship.record_followup(
        request_body=_request(session_id=f"session-profile-weak-{ordinal}"),
        request_id=f"aftereffect-profile-weak-{ordinal}",
    )
    assert result.aftereffect is not None
    assert result.aftereffect.outcome == "HELPFUL"
    assert result.aftereffect.helpfulness == 0.72
    return result.aftereffect.aftereffect_id


def test_profile_canary_activation_changes_next_turn_and_exact_rollback(
    tmp_path: Path,
) -> None:
    database, clock, reader, relationship, profile, fusion = _runtime(tmp_path)
    evidence_ids: list[str] = []
    for ordinal in range(5):
        evidence_ids.append(
            _strong_helpful_aftereffect(
                relationship=relationship,
                clock=clock,
                ordinal=ordinal,
            )
        )
        clock["now"] += DAY_MS

    started = profile.start_canary(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id="session-profile-canary",
        request_id="profile-canary-start",
        preferences=CompanionProfilePreferencesV1(
            addressing_style="GENTLE_DIRECT",
            response_length="COMPACT",
            humor_level="LOW",
            followup_rate="NONE",
            comfort_mode="QUIET_FIRST",
            proactive_window="EVENING",
            tts_rate_scale=1.15,
            avatar_motion_intensity=0.8,
        ),
        evidence_aftereffect_ids=evidence_ids,
    )
    candidate = started.state.canary_profile
    assert candidate is not None
    assert started.state.active_profile.is_baseline is True

    scope = RelationshipScope.from_values(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id="any-session",
    )

    for ordinal in range(5, 10):
        clock["now"] += DAY_MS
        _strong_helpful_aftereffect(
            relationship=relationship,
            clock=clock,
            ordinal=ordinal,
        )
        state = CompanionProfileStateV1.from_mapping(
            reader.get_snapshot(scope.aggregate_id).state["companion_profile"]
        )
        if ordinal < 9:
            assert state.canary_profile is not None
            assert len(state.canary_accepted_interaction_ids) == ordinal - 4
        else:
            assert state.active_profile.profile_id == candidate.profile_id
            assert state.canary_profile is None

    # A normal Host fusion reads the relationship-scoped active profile. The
    # next frozen plan carries the same profile identity into text, voice and
    # avatar rather than treating it as a separate frontend setting.
    fused = fusion.resolve_request(
        request_body=_request(session_id="session-profile-live", message="今天有点累。"),
        request_id="profile-live-turn",
    )
    guidance = build_affective_turn_guidance(
        emotion_context=fused.as_emotion_context(),
        request_id="profile-live-turn",
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id="session-profile-live",
        created_at_ms=clock["now"],
    )
    assert guidance is not None
    plan = guidance.plan
    assert plan.companion_profile_id == candidate.profile_id
    assert plan.companion_profile_digest == candidate.as_dict()["profile_digest"]
    assert plan.companion_profile_stage == "ACTIVE"
    assert plan.expression_plan.companion_profile_id == candidate.profile_id
    assert plan.expression_plan.voice_rate > 1.0
    assert plan.expression_plan.avatar_motion_intensity == 0.8
    assert any("不要追加追问" in item for item in guidance.style_anchor)
    intents, _receipts = build_sentence_expression_delivery(
        affective_turn_plan=plan.as_dict(),
        sentence="先歇一会儿，我在这儿。",
        sentence_index=0,
        created_at_ms=clock["now"],
    )
    voice = next(item for item in intents if item.channel == "VOICE")
    avatar = next(item for item in intents if item.channel == "AVATAR")
    assert voice.payload["companion_profile_id"] == candidate.profile_id
    assert avatar.payload["companion_profile_id"] == candidate.profile_id
    assert avatar.payload["motion_intensity"] == 0.8

    rolled_back = profile.rollback_active(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id="session-profile-rollback",
        request_id="profile-rollback",
    )
    assert rolled_back.state.active_profile.is_baseline is True
    assert rolled_back.state.active_profile.profile_id != candidate.profile_id

    rollback_fused = fusion.resolve_request(
        request_body=_request(
            session_id="session-profile-after-rollback",
            message="我想继续慢慢说。",
        ),
        request_id="profile-after-rollback-turn",
    )
    rollback_guidance = build_affective_turn_guidance(
        emotion_context=rollback_fused.as_emotion_context(),
        request_id="profile-after-rollback-turn",
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id="session-profile-after-rollback",
        created_at_ms=clock["now"],
    )
    assert rollback_guidance is not None
    rollback_plan = rollback_guidance.plan
    assert rollback_plan.companion_profile_id == rolled_back.state.active_profile.profile_id
    assert rollback_plan.companion_profile_digest == rolled_back.state.active_profile.profile_digest
    assert rollback_plan.companion_profile_id != plan.companion_profile_id
    assert rollback_plan.expression_plan.voice_rate != plan.expression_plan.voice_rate
    assert (
        rollback_plan.expression_plan.avatar_motion_intensity
        != plan.expression_plan.avatar_motion_intensity
    )
    rollback_intents, _rollback_receipts = build_sentence_expression_delivery(
        affective_turn_plan=rollback_plan.as_dict(),
        sentence="那我们就继续慢一点。",
        sentence_index=0,
        created_at_ms=clock["now"],
    )
    rollback_voice = next(item for item in rollback_intents if item.channel == "VOICE")
    rollback_avatar = next(item for item in rollback_intents if item.channel == "AVATAR")
    assert rollback_voice.payload["companion_profile_id"] == rollback_plan.companion_profile_id
    assert rollback_avatar.payload["companion_profile_id"] == rollback_plan.companion_profile_id

    projection = reader.list_companion_profile_event_heads(scope.aggregate_id, limit=20)
    assert [row["profile_kind"] for row in projection] == [
        "CANARY_STARTED",
        "CANARY_INTERACTION",
        "CANARY_INTERACTION",
        "CANARY_INTERACTION",
        "CANARY_INTERACTION",
        "CANARY_INTERACTION",
        "ACTIVATED",
        "ROLLED_BACK",
    ]
    assert all("今天有点累" not in str(row) for row in projection)

    durable = reader.get_snapshot(scope.aggregate_id)
    replayed = LifeCommandService(
        LifeCommandRepository(database),
        clock=lambda: int(clock["now"]),
    ).replay(scope.aggregate_id)
    assert replayed.as_dict() == durable.as_dict()
    with sqlite3.connect(database) as connection:
        connection.create_function(
            "natsume_sha256_hex",
            1,
            lambda value: hashlib.sha256(
                value if isinstance(value, bytes) else str(value).encode("utf-8")
            ).hexdigest(),
            deterministic=True,
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE companion_profile_life_event_v1 SET profile_kind = 'ACTIVATED'"
            )


def test_profile_cannot_start_from_low_confidence_legacy_text_aftereffects(
    tmp_path: Path,
) -> None:
    _database, clock, _reader, relationship, profile, _fusion = _runtime(tmp_path)
    ids = [
        _low_confidence_text_aftereffect(
            relationship=relationship,
            clock=clock,
            ordinal=index,
        )
        for index in range(5)
    ]
    with pytest.raises(ValueError, match="AFTEREFFECT_NOT_ELIGIBLE"):
        profile.start_canary(
            workspace_id="workspace-profile",
            relationship_subject_id="person-profile",
            session_id="session-profile-low-confidence",
            request_id="profile-low-confidence",
            preferences=CompanionProfilePreferencesV1(),
            evidence_aftereffect_ids=ids,
        )


def test_composed_relationship_lifecycle_counts_only_strong_canary_aftereffects(
    tmp_path: Path,
) -> None:
    _database, clock, reader, relationship, profile, _fusion = _runtime(tmp_path)
    evidence_ids: list[str] = []
    for ordinal in range(5):
        evidence_ids.append(
            _strong_helpful_aftereffect(
                relationship=relationship,
                clock=clock,
                ordinal=ordinal,
            )
        )
        clock["now"] += DAY_MS
    profile.start_canary(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id="session-profile-compose",
        request_id="profile-compose-start",
        preferences=CompanionProfilePreferencesV1(),
        evidence_aftereffect_ids=evidence_ids,
    )
    clock["now"] += DAY_MS
    _strong_helpful_aftereffect(
        relationship=relationship,
        clock=clock,
        ordinal=99,
    )
    scope = RelationshipScope.from_values(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id="other-session",
    )
    state = CompanionProfileStateV1.from_mapping(
        reader.get_snapshot(scope.aggregate_id).state["companion_profile"]
    )
    assert state.canary_profile is not None
    assert len(state.canary_accepted_interaction_ids) == 1
    assert reader.list_companion_profile_event_heads(scope.aggregate_id, limit=20)[-1][
        "profile_kind"
    ] == "CANARY_INTERACTION"
