from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


NOW = 1_900_000_000_000


def _runtime(
    tmp_path: Path,
) -> tuple[
    AffectFusionService,
    RelationshipLifecycleService,
    LifeSnapshotReader,
    LifeCommandService,
    Path,
]:
    database = tmp_path / "dl03-affective-repair-bridge.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl03-affective-repair-bridge",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    relationship = RelationshipLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: NOW,
    )
    fusion = AffectFusionService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        relationship_lifecycle_service_provider=lambda: relationship,
        clock_ms=lambda: NOW,
    )
    return fusion, relationship, reader, service, database


def _request(message: str, *, session_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        workspace_id="workspace-repair",
        person_id="person-repair",
        session_id=session_id,
        emotion_context={},
        emotion_tag=None,
        emotion_intensity=None,
        multimodal_observations={},
        mood_state=None,
        emotion_range_hint=None,
    )


def _foreground(command_id: str) -> dict[str, object]:
    return {
        "status": "APPLIED",
        "event_id": f"event-{command_id}",
        "command_id": command_id,
        "created_at_ms": NOW,
    }


def test_acknowledged_repair_reaches_the_original_self_affect_across_sessions(
    tmp_path: Path,
) -> None:
    fusion, relationship, reader, _service, database = _runtime(tmp_path)

    direct = fusion.resolve_request(
        request_body=_request("你真恶心。", session_id="session-hurt"),
        request_id="direct-hurt",
    )
    direct_guidance = build_affective_turn_guidance(
        emotion_context=direct.as_emotion_context(),
        request_id="direct-hurt",
        workspace_id="workspace-repair",
        relationship_subject_id="person-repair",
        session_id="session-hurt",
        created_at_ms=NOW,
    )
    assert direct_guidance is not None
    target = direct_guidance.plan.affect_repair_target
    assert target is not None

    relationship.provision_accepted_response(
        workspace_id="workspace-repair",
        relationship_subject_id="person-repair",
        session_id="session-hurt",
        request_id="contained-hurt-reply",
        response_receipt=_foreground("contained-hurt"),
        response_text="这句话让我有一点受伤，不过我愿意先听你说。",
        affect_repair_target=target.as_dict(),
    )
    relationship_scope = RelationshipScope.from_values(
        workspace_id="workspace-repair",
        relationship_subject_id="person-repair",
        session_id="session-any",
    )
    before_apology = reader.get_snapshot(relationship_scope.aggregate_id).state[
        "relationship_v2"
    ]
    assert before_apology["repair_required"] is True
    assert before_apology["pending_affect_repair_targets"] == [target.as_dict()]

    # A bare apology is a repair signal, not a permission to erase what
    # happened. It leaves the original self-affect episode open.
    apology = fusion.resolve_request(
        request_body=_request("对不起，我刚才说重了。", session_id="session-repair"),
        request_id="apology",
    )
    assert apology.relationship_aftereffect is not None
    assert apology.relationship_aftereffect["outcome"] == "REPAIR_UNRESOLVED"
    hurt_before_confirmation = reader.get_snapshot(target.affect_aggregate_id).state[
        "affective"
    ]
    assert any(
        item["episode_id"] == target.affect_episode_id
        for item in hurt_before_confirmation["active_episodes"]
    )

    # The next accepted repair reply inherits the one bounded pending target;
    # it is allowed to cross the relationship session boundary, but not the
    # workspace or relationship-subject boundary.
    relationship.provision_accepted_response(
        workspace_id="workspace-repair",
        relationship_subject_id="person-repair",
        session_id="session-repair",
        request_id="repair-open-reply",
        response_receipt=_foreground("repair-open"),
        response_text="谢谢你愿意说出来，我们可以慢慢把它说清楚。",
    )
    inherited = reader.get_snapshot(relationship_scope.aggregate_id).state[
        "relationship_v2"
    ]["active_episodes"]
    assert inherited[-1]["affect_repair_target"] == target.as_dict()

    confirmed = fusion.resolve_request(
        request_body=_request("我理解了，我们继续。", session_id="session-repair"),
        request_id="repair-confirmed",
    )
    assert confirmed.relationship_aftereffect is not None
    assert confirmed.relationship_aftereffect["outcome"] == "REPAIR_ACCEPTED"
    repaired_affect = reader.get_snapshot(target.affect_aggregate_id)
    assert all(
        item["episode_id"] != target.affect_episode_id
        for item in repaired_affect.state["affective"]["active_episodes"]
    )
    assert repaired_affect.state["affective"]["last_resolution"][
        "episode_id"
    ] == target.affect_episode_id
    assert repaired_affect.state["affective"]["last_resolution"][
        "resolution_ref"
    ].startswith("relationship-aftereffect://sha256/")
    relationship_after = reader.get_snapshot(relationship_scope.aggregate_id).state[
        "relationship_v2"
    ]
    assert relationship_after["pending_affect_repair_targets"] == []
    assert relationship_after["repair_required"] is False

    repair_guidance = build_affective_turn_guidance(
        emotion_context=confirmed.as_emotion_context(),
        request_id="repair-confirmed",
        workspace_id="workspace-repair",
        relationship_subject_id="person-repair",
        session_id="session-repair",
        created_at_ms=NOW,
    )
    assert repair_guidance is not None
    assert repair_guidance.plan.companion_act.kind == "REPAIR"

    # A late acceptance retry must reopen the original relationship command
    # even though its target has now been resolved and is no longer active.
    replayed_episode = relationship.provision_accepted_response(
        workspace_id="workspace-repair",
        relationship_subject_id="person-repair",
        session_id="session-hurt",
        request_id="contained-hurt-reply",
        response_receipt=_foreground("contained-hurt"),
        response_text="这句话让我有一点受伤，不过我愿意先听你说。",
        affect_repair_target=target.as_dict(),
    )
    assert replayed_episode.replayed is True

    # The second command is independently idempotent. A restart replays the
    # same canonical affect state; no raw utterance entered either journal.
    restarted = LifeCommandService(
        LifeCommandRepository(database),
        clock=lambda: NOW,
    ).replay(target.affect_aggregate_id)
    assert restarted.state_digest == repaired_affect.state_digest
    serialized = str(
        [event.payload for event in reader.list_events(target.affect_aggregate_id)]
        + [event.payload for event in reader.list_events(relationship_scope.aggregate_id)]
    )
    assert "你真恶心" not in serialized
    assert "我理解了，我们继续" not in serialized
