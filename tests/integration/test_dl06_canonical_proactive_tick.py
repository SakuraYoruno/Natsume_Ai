from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.outbox import LifeOutboxDispatcher
from host_brain.digital_life.policy import build_life_tick_payload
from host_brain.digital_life.companion_proactive import decide_companion_proactive
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from host_brain.runtime.context_contracts import SceneDecisionV2
from host_brain.runtime.scene_consumer_projection import SceneIdentityV2
from natsume_contracts.life import (
    EmotionComponentV1,
    EmotionEpisodeV1,
    EventAppraisalV1,
    LifeCommand,
    LifeCommandStatus,
)


NOW = 1_900_010_000_000
_IDLE_MS = 5 * 60 * 60 * 1_000
_SILENCE_GRACE_MS = 24 * 60 * 60 * 1_000
_DELIVERY_AUTHORIZATION = {
    "request_id": "dl06-canonical-proactive-tick-request",
    "governor_grant_ref": "governor-decision://sha256/" + "c" * 64,
    "authority_kind": "scheduler_host_command",
    "authority_principal_id": "scheduler-service",
}


class _Clock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _runtime(
    tmp_path: Path,
) -> tuple[
    Path,
    LifeCommandRepository,
    LifeCommandService,
    LifeSnapshotReader,
    RelationshipLifecycleService,
    _Clock,
]:
    database = tmp_path / "dl06-canonical-proactive-tick.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl06-canonical-proactive-tick-integration",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    clock = _Clock(NOW)
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=clock)
    reader = LifeSnapshotReader(repository)
    lifecycle = RelationshipLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=clock,
    )
    return database, repository, service, reader, lifecycle, clock


def _provision_target(
    lifecycle: RelationshipLifecycleService,
    reader: LifeSnapshotReader,
) -> RelationshipScope:
    lifecycle.provision_accepted_response(
        workspace_id="workspace-dl06-canonical",
        relationship_subject_id="person-dl06-canonical",
        session_id="session-origin",
        request_id="dl06-canonical-foreground",
        response_receipt={
            "status": "APPLIED",
            "event_id": "dl06-canonical-foreground-event",
            "command_id": "dl06-canonical-foreground-command",
            "created_at_ms": NOW,
        },
        response_text="我会在这里。",
        proactive_target={
            "platform": "qq",
            "conversation_id": "conversation-dl06-canonical",
        },
    )
    lifecycle.record_proactive_permission(
        workspace_id="workspace-dl06-canonical",
        relationship_subject_id="person-dl06-canonical",
        session_id="session-origin",
        request_id="dl06-canonical-permission",
        proactive_permission="ALLOWED",
        source_ref="relationship-consent://sha256/dl06-canonical",
    )
    closed = lifecycle.record_followup(
        request_body=SimpleNamespace(
            workspace_id="workspace-dl06-canonical",
            person_id="person-dl06-canonical",
            session_id="session-followup",
            emotion_context={},
            message="好，我知道了。",
        ),
        request_id="dl06-canonical-foreground-followup",
    )
    assert closed.recorded is True
    scope = RelationshipScope.from_values(
        workspace_id="workspace-dl06-canonical",
        relationship_subject_id="person-dl06-canonical",
        session_id="session-tick",
    )
    target = reader.get_snapshot(scope.aggregate_id).state["companion_proactive_target"]
    assert target["conversation_id"] == "conversation-dl06-canonical"
    return scope


def _occurrence_payload(scope: RelationshipScope) -> dict[str, object]:
    scene = SceneDecisionV2(
        workspace_id=scope.workspace_id,
        subject_id=scope.relationship_subject_id,
        scene_id="scene.casual-chat",
        revision=7,
        world_snapshot_digest="a" * 64,
        context_digest="b" * 64,
        guardian_snapshot_digest="c" * 64,
        constraints=(),
    )
    return {
        "timezone": "Asia/Shanghai",
        "delivery_authorization": _DELIVERY_AUTHORIZATION,
        "scene_decision_v2": scene.to_dict(),
        "scene_identity_v2": SceneIdentityV2.from_decision(scene).to_dict(),
    }


def _seed_warm_self_affect(
    service: LifeCommandService,
    scope: RelationshipScope,
    clock: _Clock,
) -> None:
    """Create a causal self-affect event instead of patching a test snapshot."""

    appraisal = EventAppraisalV1(
        appraisal_id="dl06-canonical-proactive-appraisal",
        workspace_id=scope.workspace_id,
        relationship_subject_id=scope.relationship_subject_id,
        session_id=scope.session_id,
        event_kind="relationship_reunion",
        target_ref="event://sha256/dl06-canonical-reunion",
        goal_congruence=0.82,
        responsibility=0.1,
        controllability=0.65,
        relationship_significance=0.92,
        boundary_relevance=0.05,
        risk=0.02,
        uncertainty=0.08,
        evidence_refs=("appraisal://sha256/dl06-canonical-reunion",),
    )
    episode = EmotionEpisodeV1(
        episode_id="dl06-canonical-proactive-affect-episode",
        appraisal_id=appraisal.appraisal_id,
        relationship_subject_id=scope.relationship_subject_id,
        trigger_ref=appraisal.target_ref,
        components=(
            EmotionComponentV1("CARE", 0.84, 0.65, 0.34, "comfort"),
            EmotionComponentV1("SHARED_JOY", 0.8, 0.82, 0.52, "play"),
        ),
        opened_at_ms=clock(),
        decay_half_life_ms=3_600_000,
        evidence_refs=("episode://sha256/dl06-canonical-reunion",),
    )

    def build(snapshot):  # noqa: ANN001, ANN202
        return LifeCommand(
            command_id="dl06-canonical-proactive-appraised",
            person_id=scope.aggregate_id,
            command_type="affective_event_appraised",
            payload={"appraisal": appraisal.as_dict(), "episode": episode.as_dict()},
            expected_version=snapshot.version,
            created_at_ms=clock(),
            correlation_id="dl06-canonical-proactive-appraised",
            causation_id="dl06-canonical-proactive-appraised",
            metadata={"producer": "dl06-local-integration"},
        )

    receipt = service.submit_next(scope.aggregate_id, build)
    assert receipt.status is LifeCommandStatus.APPLIED


def _submit_tick(
    service: LifeCommandService,
    scope: RelationshipScope,
    clock: _Clock,
    *,
    command_id: str,
    occurrence_payload: dict[str, object],
) -> object:
    def build(snapshot):  # noqa: ANN001, ANN202
        return LifeCommand(
            command_id=command_id,
            person_id=scope.aggregate_id,
            command_type="tick",
            payload=build_life_tick_payload(
                snapshot,
                scheduled_at_ms=clock(),
                occurrence_payload=occurrence_payload,
                task_id="life_tick",
                definition_revision=1,
            ),
            expected_version=snapshot.version,
            created_at_ms=clock(),
            correlation_id=command_id,
            causation_id=command_id,
            metadata={"producer": "dl06-local-integration"},
        )

    return service.submit_next(scope.aggregate_id, build)


def test_canonical_proactive_tick_commits_plan_outbox_and_silence_backoff_across_restart(
    tmp_path: Path,
) -> None:
    """Exercise the durable scheduler path, not a reducer-only fixture.

    The transport handler here is intentionally local: DL-07 owns real
    Gateway/Edge validation.  What this integration proves is the current
    Host-side contract: scheduler tick -> Life UoW -> durable outbox ->
    receipt-backed episode -> restart -> cadence-only silence closure.
    """

    database, repository, service, reader, lifecycle, clock = _runtime(tmp_path)
    scope = _provision_target(lifecycle, reader)
    occurrence_payload = _occurrence_payload(scope)

    clock.value = NOW + 10
    _seed_warm_self_affect(service, scope, clock)
    clock.value = NOW + _IDLE_MS
    pre_tick = reader.get_snapshot(scope.aggregate_id)
    pre_tick_decision = decide_companion_proactive(
        pre_tick.state,
        now_ms=clock(),
        timezone_name="Asia/Shanghai",
    )
    assert pre_tick_decision.send_allowed is True, pre_tick_decision
    queued = _submit_tick(
        service,
        scope,
        clock,
        command_id="dl06-canonical-proactive-tick",
        occurrence_payload=occurrence_payload,
    )

    assert queued.status is LifeCommandStatus.APPLIED
    queued_snapshot = reader.get_snapshot(scope.aggregate_id)
    queued_head = queued_snapshot.state["companion_proactive"]
    queued_outbox = repository.list_outbox(command_id="dl06-canonical-proactive-tick")
    assert len(queued_outbox) == 1
    assert queued_outbox[0]["destination"] == "proactive.gateway"
    queued_payload = json.loads(str(queued_outbox[0]["payload_json"]))
    raw_extra = queued_payload["raw_extra"]
    decision = raw_extra["companion_proactive_decision"]
    plan = raw_extra["affective_turn_plan"]
    assert queued_head["last_decision_id"] == decision["decision_id"]
    assert queued_head["last_plan_id"] == plan["plan_id"]
    assert queued_head["last_synchronization_id"] == plan["expression_plan"][
        "synchronization_id"
    ]

    sent: list[str] = []
    dispatcher = LifeOutboxDispatcher(
        repository,
        lambda message: sent.append(message.delivery_key)
        or {"success": True, "status": "ACCEPTED", "gateway_receipt": "dl06-g-1"},
        clock=clock,
        relationship_lifecycle_service_provider=lambda: lifecycle,
    )
    delivered = dispatcher.dispatch_pending(limit=10, dispatch_at_ms=clock())
    delivered_snapshot = reader.get_snapshot(scope.aggregate_id)
    active_episodes = delivered_snapshot.state["relationship_v2"]["active_episodes"]

    assert delivered.as_dict() == {"claimed": 1, "delivered": 1, "failed": 0}
    assert sent == [queued_outbox[0]["delivery_key"]]
    assert len(active_episodes) == 1
    assert active_episodes[0]["source_kind"] == "PROACTIVE_MESSAGE"

    # A fresh repository/reader/service must see the same canonical state
    # before a scheduler observes the unanswered episode.
    restarted_repository = LifeCommandRepository(database)
    restarted_reader = LifeSnapshotReader(restarted_repository)
    restarted_service = LifeCommandService(restarted_repository, clock=clock)
    assert restarted_service.replay(scope.aggregate_id).as_dict() == delivered_snapshot.as_dict()

    prior_user_seen = int(delivered_snapshot.state["clock"]["last_user_seen_ms"])
    clock.value += _SILENCE_GRACE_MS + 1
    silent = _submit_tick(
        restarted_service,
        scope,
        clock,
        command_id="dl06-canonical-proactive-silence",
        occurrence_payload=occurrence_payload,
    )

    assert silent.status is LifeCommandStatus.APPLIED
    recovered = restarted_reader.get_snapshot(scope.aggregate_id)
    assert recovered.state["relationship_v2"]["active_episodes"] == []
    assert recovered.state["relationship_v2"]["last_aftereffect"]["outcome"] == "SILENCE"
    assert int(recovered.state["clock"]["last_user_seen_ms"]) == prior_user_seen
    assert recovered.state["companion_proactive"]["consecutive_unanswered"] == 1
    assert recovered.state["companion_proactive"]["next_allowed_at_ms"] > clock()
    assert repository.list_outbox(command_id="dl06-canonical-proactive-silence") == []
    assert restarted_service.replay(scope.aggregate_id).as_dict() == recovered.as_dict()
