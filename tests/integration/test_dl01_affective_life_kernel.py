from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from natsume_contracts.life import (
    EmotionComponentV1,
    EmotionEpisodeV1,
    EventAppraisalV1,
    LifeCommand,
    LifeCommandStatus,
)


NOW = 1_900_000_000_000
PERSON_ID = "person-dl01-integration"


def _command(
    *,
    command_id: str,
    expected_version: int,
    command_type: str,
    payload: dict,
    created_at_ms: int,
) -> LifeCommand:
    return LifeCommand(
        command_id=command_id,
        person_id=PERSON_ID,
        command_type=command_type,
        payload=payload,
        expected_version=expected_version,
        created_at_ms=created_at_ms,
    )


def _appraisal_payload(*, opened_at_ms: int) -> dict:
    appraisal = EventAppraisalV1(
        appraisal_id="appraisal-integration",
        workspace_id="workspace-integration",
        relationship_subject_id=PERSON_ID,
        session_id="session-integration",
        event_kind="relationship_misunderstanding",
        target_ref="event://sha256/integration",
        goal_congruence=-0.65,
        responsibility=0.5,
        controllability=0.4,
        relationship_significance=0.8,
        boundary_relevance=0.6,
        risk=0.2,
        uncertainty=0.15,
        evidence_refs=("appraisal://sha256/integration",),
    )
    episode = EmotionEpisodeV1(
        episode_id="episode-integration",
        appraisal_id=appraisal.appraisal_id,
        relationship_subject_id=PERSON_ID,
        trigger_ref=appraisal.target_ref,
        components=(
            EmotionComponentV1("HURT", 0.7, -0.8, 0.5, "repair"),
            EmotionComponentV1("CARE", 0.4, 0.5, 0.3, "comfort"),
        ),
        opened_at_ms=opened_at_ms,
        decay_half_life_ms=600_000,
        evidence_refs=("episode://sha256/integration",),
    )
    return {"appraisal": appraisal.as_dict(), "episode": episode.as_dict()}


def _service(tmp_path: Path) -> tuple[Path, LifeCommandService, LifeSnapshotReader]:
    database = tmp_path / "dl01-affective.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl01-affective-kernel",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    return database, LifeCommandService(repository, clock=lambda: NOW), LifeSnapshotReader(repository)


def test_affective_projection_is_journal_derived_immutable_and_restart_replayable(
    tmp_path: Path,
) -> None:
    database, service, reader = _service(tmp_path)
    first = _command(
        command_id="dl01-appraisal",
        expected_version=0,
        command_type="affective_event_appraised",
        payload=_appraisal_payload(opened_at_ms=NOW),
        created_at_ms=NOW,
    )
    accepted = service.submit(first)
    replayed_command = service.submit(first)

    assert accepted.status is LifeCommandStatus.APPLIED
    assert replayed_command.as_dict() == {**accepted.as_dict(), "replayed": True}
    heads = reader.list_affective_event_heads(PERSON_ID)
    assert len(heads) == 1
    assert heads[0]["affective_kind"] == "APPRAISAL"
    assert heads[0]["self_id"] == "natsume_ai"
    assert heads[0]["relationship_subject_id"] == PERSON_ID
    assert heads[0]["appraisal_id"] == "appraisal-integration"
    assert heads[0]["episode_id"] == "episode-integration"

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
                "UPDATE affective_life_event_v1 SET episode_id = 'tampered'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="requires an exact"):
            connection.execute(
                """
                INSERT INTO affective_life_event_v1(
                    event_id, person_id, self_id, relationship_subject_id,
                    affective_kind, appraisal_id, episode_id, state_revision,
                    event_digest, payload_digest, created_at_ms
                ) VALUES (
                    'fake', ?, 'natsume_ai', ?, 'APPRAISAL', '', '', 0,
                    ?, ?, ?
                )
                """,
                (PERSON_ID, PERSON_ID, "a" * 64, "b" * 64, NOW),
            )

    durable = reader.get_snapshot(PERSON_ID)
    restarted_repository = LifeCommandRepository(database)
    restarted = LifeCommandService(restarted_repository, clock=lambda: NOW)
    assert restarted.replay(PERSON_ID).as_dict() == durable.as_dict()


def test_1001_affective_events_replay_to_the_same_restart_digest(tmp_path: Path) -> None:
    database, service, reader = _service(tmp_path)
    first = _command(
        command_id="dl01-0000",
        expected_version=0,
        command_type="affective_event_appraised",
        payload=_appraisal_payload(opened_at_ms=NOW),
        created_at_ms=NOW,
    )
    assert service.submit(first).status is LifeCommandStatus.APPLIED

    for index in range(1, 1002):
        command = _command(
            command_id=f"dl01-{index:04d}",
            expected_version=index,
            command_type="affective_time_advanced",
            payload={},
            created_at_ms=NOW + index * 1_000,
        )
        assert service.submit(command).status is LifeCommandStatus.APPLIED

    durable = reader.get_snapshot(PERSON_ID)
    assert durable.version == 1002
    assert len(reader.list_affective_event_heads(PERSON_ID, limit=1_000)) == 1_000
    cursor = reader.list_affective_event_heads(
        PERSON_ID,
        after_created_at_ms=NOW + 999 * 1_000,
        after_event_id="",
        limit=10,
    )
    assert len(cursor) == 3

    restarted = LifeCommandService(
        LifeCommandRepository(database),
        clock=lambda: NOW + 1_002_000,
    )
    replayed = restarted.replay(PERSON_ID)
    assert replayed.state_digest == durable.state_digest
    assert replayed.as_dict() == durable.as_dict()
