from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.affect_fusion import AffectFusionService
from host_brain.digital_life.affective_turn_planner import (
    build_affective_turn_guidance,
)
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from natsume_contracts.life import RelationshipEpisodeV1, RelationalAftereffectV1


NOW = 1_900_000_000_000


def _runtime(
    tmp_path: Path,
) -> tuple[RelationshipLifecycleService, AffectFusionService, LifeSnapshotReader]:
    database = tmp_path / "dl03-relationship.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl03-relationship-unit",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    command_service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    relationship = RelationshipLifecycleService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: NOW,
    )
    fusion = AffectFusionService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        relationship_lifecycle_service_provider=lambda: relationship,
        clock_ms=lambda: NOW,
    )
    return relationship, fusion, reader


def _request(
    message: str,
    *,
    workspace_id: str = "workspace-a",
    person_id: str = "person-a",
    session_id: str = "session-a",
) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        workspace_id=workspace_id,
        person_id=person_id,
        session_id=session_id,
        emotion_context={},
        emotion_tag=None,
        emotion_intensity=None,
        multimodal_observations={},
        mood_state=None,
        emotion_range_hint=None,
    )


def _accepted_foreground(*, command_id: str = "foreground-command") -> dict[str, object]:
    return {
        "status": "APPLIED",
        "event_id": f"event-{command_id}",
        "command_id": command_id,
        "created_at_ms": NOW,
    }


def test_response_episode_then_followup_creates_durable_boundary_aftereffect(
    tmp_path: Path,
) -> None:
    relationship, fusion, reader = _runtime(tmp_path)
    episode_receipt = relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        request_id="accepted-reply",
        response_receipt=_accepted_foreground(),
        response_text="我会在这里陪你。",
    )

    result = fusion.resolve_request(
        request_body=_request("别再这样问了，给我一点空间。", session_id="session-b"),
        request_id="user-boundary",
    )
    relationship_context = result.as_emotion_context()["affective_life"][
        "relationship_life"
    ]
    state = relationship_context["state"]

    assert episode_receipt.event_id
    assert relationship_context["aftereffect"]["outcome"] == "BOUNDARY"
    assert state["repair_required"] is True
    assert state["proactive_permission"] == "DENIED"
    assert state["rupture_load"] > 0
    assert state["trust"] < 0.5
    assert "别再这样问" not in str(state)
    assert "RELATIONAL_AFTEREFFECT_RECORDED" in result.reason_codes
    scope = RelationshipScope.from_values(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="different-session",
    )
    assert reader.list_relationship_event_heads(scope.aggregate_id, limit=10)[-1][
        "relationship_kind"
    ] == "AFTEREFFECT"


def test_explicit_stop_persists_a_boundary_even_when_it_contains_disrespect(
    tmp_path: Path,
) -> None:
    """A stop request is a durable boundary, not evidence to guilt the user."""

    relationship, fusion, reader = _runtime(tmp_path)
    relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        request_id="accepted-before-stop",
        response_receipt=_accepted_foreground(command_id="before-stop"),
        response_text="我听见你了。",
    )

    result = fusion.resolve_request(
        request_body=_request("你真烦，闭嘴。"),
        request_id="user-explicit-stop",
    )
    relationship_context = result.as_emotion_context()["affective_life"][
        "relationship_life"
    ]
    affective = reader.get_snapshot(result.scope.aggregate_id).state["affective"]

    assert relationship_context["aftereffect"]["outcome"] == "BOUNDARY"
    assert relationship_context["state"]["proactive_permission"] == "DENIED"
    assert affective["last_appraisal"]["event_kind"] == "user_boundary_or_space"
    assert {item["emotion"] for item in affective["components"]} == {
        "RESPECT",
        "CAUTION",
    }


def test_annoyance_after_an_accepted_reply_is_feedback_and_keeps_repair_open(
    tmp_path: Path,
) -> None:
    relationship, fusion, reader = _runtime(tmp_path)
    relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        request_id="accepted-before-annoyance",
        response_receipt=_accepted_foreground(command_id="before-annoyance"),
        response_text="我明白，不舒服的话不用马上说清楚。",
    )

    result = fusion.resolve_request(
        request_body=_request("你真烦。"),
        request_id="user-annoyed-after-reply",
    )
    relationship_context = result.as_emotion_context()["affective_life"][
        "relationship_life"
    ]
    affective = reader.get_snapshot(result.scope.aggregate_id).state["affective"]
    guidance = build_affective_turn_guidance(
        emotion_context=result.as_emotion_context(),
        request_id="user-annoyed-after-reply",
        workspace_id=result.scope.workspace_id,
        relationship_subject_id=result.scope.relationship_subject_id,
        session_id=result.scope.session_id,
        created_at_ms=NOW,
    )

    assert relationship_context["aftereffect"]["outcome"] == "MISUNDERSTOOD"
    assert relationship_context["state"]["repair_required"] is True
    assert affective["last_appraisal"]["event_kind"] == "natsume_may_have_hurt_user"
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "REPAIR"
    assert guidance.plan.expression_plan.voice_style == "REPAIR_OPEN"


def test_aftereffect_is_exactly_replayed_and_does_not_cross_workspace(
    tmp_path: Path,
) -> None:
    relationship, _fusion, reader = _runtime(tmp_path)
    episode_receipt = relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="same-user",
        session_id="session-a",
        request_id="reply-a",
        response_receipt=_accepted_foreground(command_id="foreground-a"),
        response_text="谢谢你告诉我。",
    )
    request = _request(
        "谢谢，这样说我舒服些。",
        workspace_id="workspace-a",
        person_id="same-user",
        session_id="session-b",
    )
    first = relationship.record_followup(request_body=request, request_id="followup-a")
    replayed = relationship.record_followup(request_body=request, request_id="followup-a")

    other_scope = RelationshipScope.from_values(
        workspace_id="workspace-b",
        relationship_subject_id="same-user",
        session_id="session-a",
    )
    same_scope = RelationshipScope.from_values(
        workspace_id="workspace-a",
        relationship_subject_id="same-user",
        session_id="session-z",
    )

    assert first.receipt is not None
    assert replayed.receipt is not None
    assert first.receipt.command_id == replayed.receipt.command_id
    assert replayed.reason_code == "RELATIONAL_AFTEREFFECT_REPLAYED"
    assert episode_receipt.person_id == same_scope.aggregate_id
    assert other_scope.aggregate_id != same_scope.aggregate_id
    assert reader.get_snapshot(other_scope.aggregate_id).version == 0


def test_repair_requires_later_user_acknowledgement_and_never_uses_raw_text(
    tmp_path: Path,
) -> None:
    relationship, _fusion, reader = _runtime(tmp_path)

    first_reply = relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        request_id="reply-one",
        response_receipt=_accepted_foreground(command_id="foreground-one"),
        response_text="我明白。",
    )
    boundary = relationship.record_followup(
        request_body=_request("你误会了，别问了"),
        request_id="boundary",
    )
    assert boundary.aftereffect is not None
    assert boundary.aftereffect.outcome == "BOUNDARY"

    relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        request_id="reply-two",
        response_receipt=_accepted_foreground(command_id="foreground-two"),
        response_text="对不起，我刚才没有听好。",
    )
    unresolved = relationship.record_followup(
        request_body=_request("对不起"),
        request_id="apology",
    )
    assert unresolved.aftereffect is not None
    assert unresolved.aftereffect.outcome == "REPAIR_UNRESOLVED"

    relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        request_id="reply-three",
        response_receipt=_accepted_foreground(command_id="foreground-three"),
        response_text="我会尊重你的节奏。",
    )
    repaired = relationship.record_followup(
        request_body=_request("谢谢你愿意听，我愿意继续。"),
        request_id="repair-accepted",
    )
    assert repaired.aftereffect is not None
    assert repaired.aftereffect.outcome == "REPAIR_ACCEPTED"
    scope = RelationshipScope.from_request(_request("", session_id="other-session"))
    events = reader.list_events(scope.aggregate_id, limit=20)
    serialized = str([event.payload for event in events])
    assert "别问了" not in serialized
    assert "愿意继续" not in serialized
    assert first_reply.event_id


def test_user_accepted_repair_reopens_foreground_without_restoring_proactive_permission(
    tmp_path: Path,
) -> None:
    """A user can re-engage now without implicitly granting future contact."""

    relationship, fusion, reader = _runtime(tmp_path)
    relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        request_id="repair-open-one",
        response_receipt=_accepted_foreground(command_id="repair-open-one"),
        response_text="我听见了。",
    )
    boundary = relationship.record_followup(
        request_body=_request("你误会了，别再问了"),
        request_id="repair-boundary",
    )
    assert boundary.aftereffect is not None
    assert boundary.aftereffect.outcome == "BOUNDARY"

    relationship.provision_accepted_response(
        workspace_id="workspace-a",
        relationship_subject_id="person-a",
        session_id="session-a",
        request_id="repair-open-two",
        response_receipt=_accepted_foreground(command_id="repair-open-two"),
        response_text="对不起，我刚才没有听好。",
    )
    request_id = "repair-user-accepted"
    result = fusion.resolve_request(
        request_body=_request("谢谢你愿意听，我愿意继续。"),
        request_id=request_id,
    )
    affective_state = reader.get_snapshot(result.scope.aggregate_id).state["affective"]
    guidance = build_affective_turn_guidance(
        emotion_context=result.as_emotion_context(),
        request_id=request_id,
        workspace_id=result.scope.workspace_id,
        relationship_subject_id=result.scope.relationship_subject_id,
        session_id=result.scope.session_id,
        created_at_ms=NOW,
    )

    assert result.relationship_aftereffect is not None
    assert result.relationship_aftereffect["outcome"] == "REPAIR_ACCEPTED"
    assert affective_state["last_appraisal"]["event_kind"] == "relationship_repair_accepted"
    assert {item["emotion"] for item in affective_state["components"]} == {
        "CARE",
        "RELIEF",
    }
    assert guidance is not None
    assert guidance.plan.companion_act.kind == "ACKNOWLEDGE"
    assert guidance.plan.expression_plan.regulation_mode == "EXPRESS"
    relationship_state = result.as_emotion_context()["affective_life"]["relationship_life"]["state"]
    assert relationship_state["proactive_permission"] == "DENIED"


def test_relationship_contracts_fail_closed_for_raw_or_unearned_repair() -> None:
    with pytest.raises(ValueError, match="silence or delivery failure"):
        RelationalAftereffectV1(
            aftereffect_id="aftereffect",
            episode_id="episode",
            workspace_id="workspace",
            relationship_subject_id="person",
            session_id="session",
            source_receipt_ref="life-event://one",
            user_input_ref="input://one",
            outcome="SILENCE",
            helpfulness="0.1",
            boundary_respected=True,
            repair_acknowledged=False,
            observed_at_ms=NOW,
        )
    with pytest.raises(ValueError, match="source_kind"):
        RelationshipEpisodeV1(
            episode_id="episode",
            workspace_id="workspace",
            relationship_subject_id="person",
            session_id="session",
            source_kind="RAW_REPLY",
            response_receipt_ref="life-event://one",
            response_digest="sha256:one",
            opened_at_ms=NOW,
        )
