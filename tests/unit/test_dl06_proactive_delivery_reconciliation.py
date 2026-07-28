from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.outbox import LifeOutboxDispatcher
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from natsume_contracts.life import LifeCommand


NOW = 1_900_000_100_000
_DELIVERY_AUTHORIZATION = {
    "request_id": "dl06-proactive-delivery-request",
    "governor_grant_ref": "governor-decision://sha256/" + "a" * 64,
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
    LifeCommandRepository,
    LifeCommandService,
    LifeSnapshotReader,
    RelationshipLifecycleService,
    _Clock,
]:
    database = tmp_path / "dl06-proactive-delivery.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl06-proactive-delivery-unit",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    clock = _Clock(NOW)
    repository = LifeCommandRepository(database)
    command_service = LifeCommandService(repository, clock=clock)
    reader = LifeSnapshotReader(repository)
    lifecycle = RelationshipLifecycleService(
        command_service_provider=lambda: command_service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=clock,
    )
    return repository, command_service, reader, lifecycle, clock


def _registered_target(
    lifecycle: RelationshipLifecycleService,
    reader: LifeSnapshotReader,
) -> tuple[RelationshipScope, dict[str, object]]:
    lifecycle.provision_accepted_response(
        workspace_id="workspace-delivery",
        relationship_subject_id="person-delivery",
        session_id="session-origin",
        request_id="foreground-target",
        response_receipt={
            "status": "APPLIED",
            "event_id": "foreground-target-event",
            "command_id": "foreground-target-command",
            "created_at_ms": NOW,
        },
        response_text="我会在这里。",
        proactive_target={
            "platform": "qq",
            "conversation_id": "conversation-delivery",
        },
    )
    scope = RelationshipScope.from_values(
        workspace_id="workspace-delivery",
        relationship_subject_id="person-delivery",
        session_id="session-other",
    )
    target = dict(
        reader.get_snapshot(scope.aggregate_id).state["companion_proactive_target"]
    )
    # The initial foreground episode receives a normal user aftereffect before
    # a proactive message becomes eligible.  This keeps the setup faithful to
    # DL-06 rather than bypassing the no-active-episode guard.
    lifecycle.record_proactive_permission(
        workspace_id="workspace-delivery",
        relationship_subject_id="person-delivery",
        session_id="session-origin",
        request_id="permission-target",
        proactive_permission="ALLOWED",
        source_ref="relationship-consent://sha256/delivery-target",
    )
    closed = lifecycle.record_followup(
        request_body=SimpleNamespace(
            workspace_id="workspace-delivery",
            person_id="person-delivery",
            session_id="session-followup",
            emotion_context={},
            message="好，我知道了。",
        ),
        request_id="foreground-followup",
    )
    assert closed.recorded is True
    return scope, target


def _queue_companion_delivery(
    command_service: LifeCommandService,
    scope: RelationshipScope,
    target: dict[str, object],
    clock: _Clock,
    *,
    command_id: str,
) -> None:
    context = {
        "schema_version": "companion_proactive_delivery_context.v1",
        "target_id": target["target_id"],
        "workspace_id": scope.workspace_id,
        "relationship_subject_id": scope.relationship_subject_id,
        "session_id": target["session_id"],
    }

    def build(snapshot):  # noqa: ANN001, ANN202
        return LifeCommand(
            command_id=command_id,
            person_id=scope.aggregate_id,
            command_type="tick",
            payload={
                "state_patch": {},
                "deliveries": [
                    {
                        "destination": "proactive.gateway",
                        "authorization": _DELIVERY_AUTHORIZATION,
                        "payload": {
                            "platform": "qq",
                            "conversation_id": "conversation-delivery",
                            "content": "突然想起你了，不用特意回我。",
                            "msg_type": "text",
                            "_companion_relationship_context": context,
                        },
                    }
                ],
            },
            expected_version=snapshot.version,
            created_at_ms=clock(),
        )

    command_service.submit_next(scope.aggregate_id, build)


def test_delivery_receipt_opens_one_proactive_episode_and_next_user_turn_closes_it(
    tmp_path: Path,
) -> None:
    repository, command_service, reader, lifecycle, clock = _runtime(tmp_path)
    scope, target = _registered_target(lifecycle, reader)
    prior_user_seen = int(
        reader.get_snapshot(scope.aggregate_id).state["clock"]["last_user_seen_ms"]
    )
    _queue_companion_delivery(
        command_service,
        scope,
        target,
        clock,
        command_id="queue-proactive-delivery",
    )
    sent: list[str] = []
    clock.value = NOW + 10
    dispatcher = LifeOutboxDispatcher(
        repository,
        lambda message: sent.append(message.delivery_key)
        or {"success": True, "status": "ACCEPTED", "gateway_receipt": "g-1"},
        clock=clock,
        relationship_lifecycle_service_provider=lambda: lifecycle,
    )

    summary = dispatcher.dispatch_pending(limit=10, dispatch_at_ms=clock())
    snapshot = reader.get_snapshot(scope.aggregate_id)
    episodes = snapshot.state["relationship_v2"]["active_episodes"]
    delivery = repository.list_outbox(command_id="queue-proactive-delivery")[0]

    assert summary.as_dict() == {"claimed": 1, "delivered": 1, "failed": 0}
    assert sent == [delivery["delivery_key"]]
    assert len(episodes) == 1
    assert episodes[0]["source_kind"] == "PROACTIVE_MESSAGE"
    assert episodes[0]["response_receipt_ref"] == f"life-delivery://{delivery['outbox_id']}"
    assert int(snapshot.state["clock"]["last_user_seen_ms"]) == prior_user_seen
    assert reader.list_completed_companion_proactive_messages(limit=10) == []

    # A recovery pass sees no gap once the canonical relation event exists;
    # it has no transport claim and therefore cannot send the message twice.
    assert dispatcher.reconcile_completed_relationship_deliveries(limit=10) == 0
    replay = dispatcher.dispatch_pending(limit=10, dispatch_at_ms=clock())
    events = reader.list_events(scope.aggregate_id, limit=20)
    assert replay.as_dict() == {"claimed": 0, "delivered": 0, "failed": 0}
    assert sent == [delivery["delivery_key"]]
    assert [event.event_type for event in events].count(
        "relationship_proactive_delivered"
    ) == 1

    clock.value = NOW + 20
    aftereffect = lifecycle.record_followup(
        request_body=SimpleNamespace(
            workspace_id=scope.workspace_id,
            person_id=scope.relationship_subject_id,
            session_id="session-next",
            emotion_context={},
            message="谢谢你惦记我，我现在还好。",
        ),
        request_id="proactive-aftereffect",
    )
    assert aftereffect.recorded is True
    assert aftereffect.aftereffect is not None
    assert aftereffect.aftereffect.source_receipt_ref == episodes[0]["response_receipt_ref"]
    assert reader.get_snapshot(scope.aggregate_id).state["relationship_v2"]["active_episodes"] == []


def test_crash_after_delivery_receipt_reconciles_without_second_transport_call(
    tmp_path: Path,
) -> None:
    repository, command_service, reader, lifecycle, clock = _runtime(tmp_path)
    scope, target = _registered_target(lifecycle, reader)
    _queue_companion_delivery(
        command_service,
        scope,
        target,
        clock,
        command_id="queue-proactive-crash-gap",
    )
    sent: list[str] = []

    class BrokenReconciler:
        @staticmethod
        def reconcile_completed_proactive_deliveries(*, limit: int) -> int:
            assert limit > 0
            return 0

        @staticmethod
        def provision_delivered_proactive(**_kwargs) -> None:
            raise RuntimeError("injected relationship reconciliation crash")

    clock.value = NOW + 10
    interrupted = LifeOutboxDispatcher(
        repository,
        lambda message: sent.append(message.delivery_key)
        or {"success": True, "status": "ACCEPTED"},
        clock=clock,
        relationship_lifecycle_service_provider=lambda: BrokenReconciler(),
    )
    with pytest.raises(RuntimeError, match="injected relationship reconciliation crash"):
        interrupted.dispatch_pending(limit=1, dispatch_at_ms=clock())

    outbox = repository.list_outbox(command_id="queue-proactive-crash-gap")[0]
    assert outbox["status"] == "DELIVERED"
    assert repository.get_delivery_receipt(outbox["outbox_id"]) is not None
    assert sent == [outbox["delivery_key"]]

    recovered = LifeOutboxDispatcher(
        repository,
        lambda message: sent.append(message.delivery_key)
        or {"success": True, "status": "ACCEPTED"},
        clock=clock,
        relationship_lifecycle_service_provider=lambda: lifecycle,
    )
    assert recovered.reconcile_completed_relationship_deliveries(limit=10) == 1
    assert recovered.dispatch_pending(limit=10, dispatch_at_ms=clock()).as_dict() == {
        "claimed": 0,
        "delivered": 0,
        "failed": 0,
    }
    assert sent == [outbox["delivery_key"]]
    assert len(reader.get_snapshot(scope.aggregate_id).state["relationship_v2"]["active_episodes"]) == 1


def test_delivery_context_tamper_cannot_open_relationship_episode(tmp_path: Path) -> None:
    repository, command_service, reader, lifecycle, clock = _runtime(tmp_path)
    scope, target = _registered_target(lifecycle, reader)
    _queue_companion_delivery(
        command_service,
        scope,
        target,
        clock,
        command_id="queue-proactive-tamper",
    )
    clock.value = NOW + 10
    dispatcher = LifeOutboxDispatcher(
        repository,
        lambda _message: {"success": True, "status": "ACCEPTED"},
        clock=clock,
    )
    assert dispatcher.dispatch_pending(limit=1, dispatch_at_ms=clock()).delivered == 1
    outbox = repository.list_completed_companion_proactive_messages(limit=1)[0]
    receipt = repository.get_delivery_receipt(outbox.outbox_id)
    assert receipt is not None
    forged_payload = dict(outbox.payload)
    forged_context = dict(forged_payload["_companion_relationship_context"])
    forged_context["target_id"] = "companion_target_forged"
    forged_payload["_companion_relationship_context"] = forged_context

    with pytest.raises(ValueError, match="target identity drift"):
        lifecycle.provision_delivered_proactive(
            message=replace(outbox, payload=forged_payload),
            delivery_receipt=receipt,
        )
    assert reader.get_snapshot(scope.aggregate_id).state["relationship_v2"]["active_episodes"] == []
