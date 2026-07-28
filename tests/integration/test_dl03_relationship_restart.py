from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.life import (
    PROCESS_LIFE_PROVIDER,
    build_process_life_container,
)
from host_brain.digital_life.projection import LifeProjectionDispatcher
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository


NOW = 1_900_000_000_000


def _request(message: str) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_id="workspace-restart",
        person_id="person-restart",
        session_id="session-followup",
        emotion_context={},
        message=message,
    )


def _foreground_payload() -> dict[str, object]:
    request_id = "dl03-accepted-foreground"
    return {
        "command_type": "foreground_completed",
        "person_id": "person-restart",
        "command_id": f"life::foreground_completed::{request_id}",
        "correlation_id": request_id,
        "payload": {
            "request_id": request_id,
            "workspace_id": "workspace-restart",
            "session_id": "session-origin",
            "person_id": "person-restart",
            "user_message": "我今天有点累。",
            "assistant_reply": "那就先靠一会儿，我陪你慢慢缓下来。",
            "completed_at_ms": NOW,
            "state_patch": {},
            "emotion_context": {},
            "bond_summary": {},
            "projection_intents": [
                {
                    "projection_id": "surface.post_process",
                    "payload": {"request_id": request_id},
                }
            ],
            "deliveries": [],
        },
        "created_at_ms": NOW,
        "metadata": {
            "source": "governed_task_acceptance",
            "reply_sendable": True,
            "reply_persistable": True,
            "delivery_authorized": False,
        },
    }


async def _ack_surface_projection(message):  # noqa: ANN001
    return {
        "status": "APPLIED",
        "target": message.target,
        "intent_id": message.intent_id,
        "request_id": str(message.payload.get("request_id") or ""),
    }


def test_relationship_projection_is_immutable_and_restarts_with_exact_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dl03-relationship-restart.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl03-relationship-restart",
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
    foreground = {
        "status": "APPLIED",
        "event_id": "foreground-event-restart",
        "command_id": "foreground-command-restart",
        "created_at_ms": NOW,
    }
    lifecycle.provision_accepted_response(
        workspace_id="workspace-restart",
        relationship_subject_id="person-restart",
        session_id="session-origin",
        request_id="accepted-reply-restart",
        response_receipt=foreground,
        response_text="我在，慢慢说。",
    )
    aftereffect = lifecycle.record_followup(
        request_body=_request("谢谢你，我感觉好一些了。"),
        request_id="followup-restart",
    )
    assert aftereffect.receipt is not None
    scope = RelationshipScope.from_request(_request(""))
    durable = reader.get_snapshot(scope.aggregate_id)
    heads = reader.list_relationship_event_heads(scope.aggregate_id, limit=10)

    assert [
        row["relationship_kind"]
        for row in sorted(heads, key=lambda row: int(row["state_revision"]))
    ] == [
        "RESPONSE_ACCEPTED",
        "AFTEREFFECT",
    ]
    assert all("我在" not in str(row) and "好一些" not in str(row) for row in heads)
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
                "UPDATE relationship_life_event_v1 SET episode_id = 'tampered'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="requires an exact"):
            connection.execute(
                """
                INSERT INTO relationship_life_event_v1(
                    event_id, person_id, self_id, workspace_id,
                    relationship_subject_id, relationship_kind, episode_id,
                    aftereffect_id, state_revision, event_digest,
                    payload_digest, created_at_ms
                ) VALUES (
                    'fake', ?, 'natsume_ai', 'workspace-restart', ?,
                    'AFTEREFFECT', '', '', 0, ?, ?, ?
                )
                """,
                (
                    scope.aggregate_id,
                    "person-restart",
                    "a" * 64,
                    "b" * 64,
                    NOW,
                ),
            )

    restarted = LifeCommandService(
        LifeCommandRepository(database),
        clock=lambda: NOW,
    )
    replayed = restarted.replay(scope.aggregate_id)
    assert replayed.state_digest == durable.state_digest
    assert replayed.as_dict() == durable.as_dict()


@pytest.mark.asyncio
async def test_accepted_foreground_provisions_and_reconciles_relationship_episode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The governed acceptance handler uses injected Life authority only."""

    database = tmp_path / "dl03-accepted-foreground.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl03-accepted-foreground",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    ledger = SQLiteLedger(str(database), check_same_thread=False)
    life = build_process_life_container(ledger=ledger, adopted_at_ms=1)
    relationship = RelationshipLifecycleService(
        command_service_provider=lambda: life.command_service,
        snapshot_reader_provider=lambda: life.snapshot_reader,
        clock_ms=lambda: NOW,
    )
    monkeypatch.setattr(
        PROCESS_LIFE_PROVIDER,
        "require",
        lambda: (_ for _ in ()).throw(
            AssertionError("production handler must use its app-local provider")
        ),
    )
    payload = _foreground_payload()
    handler = build_production_acceptance_effect_handlers(
        life_command_service_provider=lambda: life.command_service,
        life_receipt_reader_provider=lambda: life.snapshot_reader,
        life_projection_dispatcher=LifeProjectionDispatcher(
            life.repository,
            _ack_surface_projection,
            clock=lambda: NOW,
        ),
        relationship_lifecycle_service_provider=lambda: relationship,
    )["life.foreground_completed"]

    accepted = await handler(payload)

    episode = accepted["relationship_episode"]
    assert accepted["status"] == "APPLIED"
    assert episode["event_id"].startswith("life_evt_")
    assert episode["receipt_ref"].startswith("life-event://")
    ledger.close()

    restarted_repository = LifeCommandRepository(database)
    restarted_reader = LifeSnapshotReader(restarted_repository)
    restarted_life = LifeCommandService(restarted_repository, clock=lambda: NOW)
    restarted_relationship = RelationshipLifecycleService(
        command_service_provider=lambda: restarted_life,
        snapshot_reader_provider=lambda: restarted_reader,
        clock_ms=lambda: NOW,
    )
    restarted = build_production_acceptance_effect_handlers(
        life_command_service_provider=lambda: restarted_life,
        life_receipt_reader_provider=lambda: restarted_reader,
        relationship_lifecycle_service_provider=lambda: restarted_relationship,
    )["life.foreground_completed"]

    recovered = restarted.reconcile(payload)

    assert recovered["replayed"] is True
    assert recovered["relationship_episode"] == episode
