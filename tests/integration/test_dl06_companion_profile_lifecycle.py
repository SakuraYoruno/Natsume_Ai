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
    relationship = RelationshipLifecycleService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: int(clock["now"]),
    )
    # The relationship classifier is intentionally replaced only in this
    # evidence fixture: production's conservative text classifier currently
    # has no direct preference-rating transport, so it cannot honestly claim a
    # >= .85 preference confidence by itself.
    relationship._classify_followup = lambda **_kwargs: (  # type: ignore[method-assign]
        "HELPFUL",
        0.91,
        True,
        False,
    )
    profile = CompanionProfileLifecycleService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: int(clock["now"]),
    )
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


def _helpful_aftereffect(
    *,
    relationship: RelationshipLifecycleService,
    clock: dict[str, int],
    ordinal: int,
) -> str:
    relationship.provision_accepted_response(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id=f"session-profile-{ordinal}",
        request_id=f"response-profile-{ordinal}",
        response_receipt=_accepted_response(ordinal=ordinal, created_at_ms=clock["now"]),
        response_text="我在，慢一点也没关系。",
    )
    clock["now"] += 1_000
    result = relationship.record_followup(
        request_body=_request(session_id=f"session-profile-{ordinal}"),
        request_id=f"aftereffect-profile-{ordinal}",
    )
    assert result.aftereffect is not None
    assert result.aftereffect.outcome == "HELPFUL"
    return result.aftereffect.aftereffect_id


def test_profile_canary_activation_changes_next_turn_and_exact_rollback(
    tmp_path: Path,
) -> None:
    database, clock, reader, relationship, profile, fusion = _runtime(tmp_path)
    evidence_ids: list[str] = []
    for ordinal in range(5):
        evidence_ids.append(
            _helpful_aftereffect(
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

    for ordinal in range(5, 10):
        clock["now"] += DAY_MS
        aftereffect_id = _helpful_aftereffect(
            relationship=relationship,
            clock=clock,
            ordinal=ordinal,
        )
        recorded = profile.record_eligible_canary_aftereffect(
            workspace_id="workspace-profile",
            relationship_subject_id="person-profile",
            session_id=f"session-profile-{ordinal}",
            request_id=f"profile-canary-observation-{ordinal}",
            aftereffect_id=aftereffect_id,
        )
        assert recorded.receipt is not None
        if ordinal < 9:
            assert len(recorded.state.canary_accepted_interaction_ids) == ordinal - 4
            assert (
                recorded.reason_code
                == "COMPANION_PROFILE_CANARY_INTERACTION_RECORDED"
            )
        else:
            assert (
                recorded.reason_code
                == "COMPANION_PROFILE_CANARY_AUTO_ACTIVATED"
            )
            assert recorded.state.active_profile.profile_id == candidate.profile_id
            assert recorded.state.canary_profile is None

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

    scope = RelationshipScope.from_values(
        workspace_id="workspace-profile",
        relationship_subject_id="person-profile",
        session_id="any-session",
    )
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
    # Restore the real conservative classifier: it is deliberately below the
    # automatic profile threshold until a future direct preference-feedback
    # transport supplies stronger evidence.
    relationship._classify_followup = RelationshipLifecycleService._classify_followup.__get__(
        relationship,
        RelationshipLifecycleService,
    )
    ids = [
        _helpful_aftereffect(relationship=relationship, clock=clock, ordinal=index)
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
            _helpful_aftereffect(
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
    relationship._companion_profile_lifecycle_service_provider = lambda: profile
    clock["now"] += DAY_MS
    _helpful_aftereffect(
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
