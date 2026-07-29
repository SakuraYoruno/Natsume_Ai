from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.affect_fusion import AffectFusionService
from host_brain.digital_life.affective_turn_planner import (
    build_affective_turn_guidance,
)
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository


NOW = 1_900_000_000_000


def test_fusion_journal_restart_replays_same_workspace_scoped_self_affect(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dl02-restart.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl02-restart",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    fusion = AffectFusionService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: NOW,
    )
    request = SimpleNamespace(
        message="对不起，我刚才说话太重了。",
        workspace_id="workspace-restart",
        person_id="person-restart",
        session_id="session-restart",
        emotion_context={},
        emotion_tag=None,
        emotion_intensity=None,
        multimodal_observations={},
    )

    result = fusion.resolve_request(request_body=request, request_id="restart-request")
    before = reader.get_snapshot(result.scope.aggregate_id)
    restarted_repository = LifeCommandRepository(database)
    restarted_service = LifeCommandService(restarted_repository, clock=lambda: NOW)
    replayed = restarted_service.replay(result.scope.aggregate_id)
    restarted_reader = LifeSnapshotReader(restarted_repository)

    assert replayed.state_digest == before.state_digest
    assert replayed.state["identity"] == {
        "self_id": "natsume_ai",
        "relationship_subject_id": "person-restart",
    }
    assert replayed.state["affective"]["last_regulation"]["mode"] == "SEEK_REPAIR"
    assert replayed.state["affective"]["last_regulation"]["episode_id"] == replayed.state[
        "affective"
    ]["active_episodes"][0]["episode_id"]
    assert restarted_reader.list_affective_event_heads(
        result.scope.aggregate_id,
        limit=20,
    )


def test_restart_reopens_the_same_direct_space_appraisal_and_turn_plan(
    tmp_path: Path,
) -> None:
    """A direct boundary must not disappear when the Host restarts.

    The durable event is enough to recreate the frozen ``GIVE_SPACE`` plan;
    no volatile relationship queue or request-body emotion override is needed.
    """

    database = tmp_path / "dl02-space-restart.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl02-space-restart",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    fusion = AffectFusionService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: NOW,
    )
    request = SimpleNamespace(
        message="先别问了，我想一个人待会儿。",
        workspace_id="workspace-restart",
        person_id="person-restart",
        session_id="session-restart",
        emotion_context={},
        emotion_tag=None,
        emotion_intensity=None,
        multimodal_observations={},
    )
    request_id = "restart-space-request"

    first = fusion.resolve_request(request_body=request, request_id=request_id)
    before = reader.get_snapshot(first.scope.aggregate_id)
    first_guidance = build_affective_turn_guidance(
        emotion_context=first.as_emotion_context(),
        request_id=request_id,
        workspace_id=first.scope.workspace_id,
        relationship_subject_id=first.scope.relationship_subject_id,
        session_id=first.scope.session_id,
        created_at_ms=NOW,
    )

    restarted_repository = LifeCommandRepository(database)
    restarted_service = LifeCommandService(restarted_repository, clock=lambda: NOW)
    restarted_reader = LifeSnapshotReader(restarted_repository)
    restarted_fusion = AffectFusionService(
        command_service_provider=lambda: restarted_service,
        snapshot_reader_provider=lambda: restarted_reader,
        clock_ms=lambda: NOW,
    )
    replayed = restarted_fusion.resolve_request(
        request_body=request,
        request_id=request_id,
    )
    after = restarted_reader.get_snapshot(replayed.scope.aggregate_id)
    replayed_guidance = build_affective_turn_guidance(
        emotion_context=replayed.as_emotion_context(),
        request_id=request_id,
        workspace_id=replayed.scope.workspace_id,
        relationship_subject_id=replayed.scope.relationship_subject_id,
        session_id=replayed.scope.session_id,
        created_at_ms=NOW,
    )

    assert before.state_digest == after.state_digest
    assert first_guidance is not None and replayed_guidance is not None
    assert first_guidance.plan.companion_act.kind == "GIVE_SPACE"
    assert replayed_guidance.plan.companion_act.kind == "GIVE_SPACE"
    assert first_guidance.plan.expression_plan.regulation_mode == "GIVE_SPACE"
    assert replayed_guidance.plan.expression_plan.regulation_mode == "GIVE_SPACE"
    assert first_guidance.plan.regulation_ref == replayed_guidance.plan.regulation_ref
    assert first_guidance.plan.plan_id == replayed_guidance.plan.plan_id
    assert "想一个人待会" not in str(after.state)
