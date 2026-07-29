from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.host_life_scheduler_wiring import _select_life_tick_person_id
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository


NOW = 1_900_000_000_000


def _runtime(tmp_path: Path) -> tuple[RelationshipLifecycleService, LifeSnapshotReader]:
    database = tmp_path / "dl06-target-registry.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl06-target-registry-unit",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    lifecycle = RelationshipLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=lambda: NOW,
    )
    return lifecycle, reader


def _accepted_foreground() -> dict[str, object]:
    return {
        "status": "APPLIED",
        "event_id": "foreground-event-target",
        "command_id": "foreground-command-target",
        "created_at_ms": NOW,
    }


def test_accepted_reply_registers_scope_bound_target_and_explicit_permission_exposes_it(
    tmp_path: Path,
) -> None:
    lifecycle, reader = _runtime(tmp_path)
    episode_receipt = lifecycle.provision_accepted_response(
        workspace_id="workspace-target",
        relationship_subject_id="person-target",
        session_id="session-target",
        request_id="reply-target",
        response_receipt=_accepted_foreground(),
        response_text="我会在这里。",
        proactive_target={"platform": "QQ", "conversation_id": "conversation-target"},
    )
    scope = RelationshipScope.from_values(
        workspace_id="workspace-target",
        relationship_subject_id="person-target",
        session_id="other-session",
    )
    snapshot = reader.get_snapshot(scope.aggregate_id)
    target = snapshot.state["companion_proactive_target"]

    assert episode_receipt.status.value == "APPLIED"
    assert target["platform"] == "qq"
    assert target["conversation_id"] == "conversation-target"
    assert target["workspace_id"] == "workspace-target"
    assert target["relationship_subject_id"] == "person-target"
    assert snapshot.state["clock"]["last_user_seen_ms"] == NOW
    assert reader.list_companion_proactive_targets(limit=10) == []

    permission = lifecycle.record_proactive_permission(
        workspace_id="workspace-target",
        relationship_subject_id="person-target",
        session_id="session-target",
        request_id="allow-target",
        proactive_permission="ALLOWED",
        source_ref="relationship-consent://sha256/allowed-target",
    )
    aftereffect = lifecycle.record_followup(
        request_body=SimpleNamespace(
            workspace_id="workspace-target",
            person_id="person-target",
            session_id="session-next",
            emotion_context={},
            message="好，我知道了。",
        ),
        request_id="target-followup",
    )
    targets = reader.list_companion_proactive_targets(limit=10)

    assert permission.status.value == "APPLIED"
    assert aftereffect.recorded is True
    assert reader.get_snapshot(scope.aggregate_id).state["clock"]["last_user_seen_ms"] == NOW
    assert targets == [
        {
            "aggregate_id": scope.aggregate_id,
            "state_version": 3,
            "updated_at_ms": NOW,
            "target_id": target["target_id"],
            "workspace_id": "workspace-target",
            "relationship_subject_id": "person-target",
            "session_id": "session-target",
            "platform": "qq",
            "conversation_id": "conversation-target",
            "registered_at_ms": NOW,
            "relationship_revision": 3,
            "self_affect_revision": 0,
        }
    ]


def test_target_registration_rejects_partial_or_cross_scope_delivery_metadata(
    tmp_path: Path,
) -> None:
    lifecycle, _reader = _runtime(tmp_path)

    with pytest.raises(ValueError, match="platform and conversation_id"):
        lifecycle.provision_accepted_response(
            workspace_id="workspace-target",
            relationship_subject_id="person-target",
            session_id="session-target",
            request_id="partial-target",
            response_receipt=_accepted_foreground(),
            response_text="我会在这里。",
            proactive_target={"platform": "qq"},
        )

    with pytest.raises(ValueError, match="opaque source_ref"):
        lifecycle.record_proactive_permission(
            workspace_id="workspace-target",
            relationship_subject_id="person-target",
            session_id="session-target",
            request_id="raw-consent",
            proactive_permission="ALLOWED",
            source_ref="我允许你主动联系我",
        )


def test_pre_registry_episode_reopens_without_backfill_or_duplicate_episode(
    tmp_path: Path,
) -> None:
    lifecycle, reader = _runtime(tmp_path)
    legacy = lifecycle.provision_accepted_response(
        workspace_id="workspace-target",
        relationship_subject_id="person-target",
        session_id="session-target",
        request_id="legacy-reply",
        response_receipt=_accepted_foreground(),
        response_text="我会在这里。",
    )
    resumed = lifecycle.provision_accepted_response(
        workspace_id="workspace-target",
        relationship_subject_id="person-target",
        session_id="session-target",
        request_id="legacy-reply",
        response_receipt=_accepted_foreground(),
        response_text="我会在这里。",
        proactive_target={"platform": "qq", "conversation_id": "new-route"},
    )
    reconciled = lifecycle.reconcile_accepted_response(
        workspace_id="workspace-target",
        relationship_subject_id="person-target",
        session_id="session-target",
        request_id="legacy-reply",
        response_receipt=_accepted_foreground(),
        response_text="我会在这里。",
        proactive_target={"platform": "qq", "conversation_id": "new-route"},
    )
    scope = RelationshipScope.from_values(
        workspace_id="workspace-target",
        relationship_subject_id="person-target",
        session_id="other-session",
    )

    assert resumed.command_id == legacy.command_id
    assert resumed.replayed is True
    assert reconciled.command_id == legacy.command_id
    assert reader.get_snapshot(scope.aggregate_id).state["companion_proactive_target"] is None


def test_scheduler_selection_is_deterministic_and_scene_scope_bound() -> None:
    class Reader:
        def list_companion_proactive_targets(self, *, limit: int):
            assert limit == 1_000
            return [
                {
                    "aggregate_id": "relationship::b",
                    "workspace_id": "workspace-a",
                    "relationship_subject_id": "person-a",
                },
                {
                    "aggregate_id": "relationship::a",
                    "workspace_id": "workspace-a",
                    "relationship_subject_id": "person-a",
                },
                {
                    "aggregate_id": "relationship::other",
                    "workspace_id": "workspace-b",
                    "relationship_subject_id": "person-b",
                },
            ]

    frozen = {
        "scene_decision_v2": {
            "workspace_id": "workspace-a",
            "subject_id": "person-a",
        }
    }
    first = _select_life_tick_person_id(
        args={"timezone": "Asia/Shanghai"},
        occurrence_id="occurrence-target",
        frozen_runtime_context=frozen,
        life_snapshot_reader=Reader(),
    )
    second = _select_life_tick_person_id(
        args={"timezone": "Asia/Shanghai"},
        occurrence_id="occurrence-target",
        frozen_runtime_context=frozen,
        life_snapshot_reader=Reader(),
    )
    incompatible = _select_life_tick_person_id(
        args={"timezone": "Asia/Shanghai"},
        occurrence_id="occurrence-target",
        frozen_runtime_context={
            "scene_decision_v2": {
                "workspace_id": "workspace-z",
                "subject_id": "person-z",
            }
        },
        life_snapshot_reader=Reader(),
    )

    assert first == second
    assert first[0] in {"relationship::a", "relationship::b"}
    assert first[1] == "CANONICAL_REGISTERED_TARGET"
    assert incompatible == ("default", "NO_SCENE_COMPATIBLE_REGISTERED_TARGET")
