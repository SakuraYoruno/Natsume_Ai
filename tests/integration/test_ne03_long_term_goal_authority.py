from __future__ import annotations

from datetime import datetime
import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.long_term_goal_repository import (
    LONG_TERM_GOAL_MIGRATION_ASSET_KEY,
    LongTermGoalRepositoryError,
    SQLiteLongTermGoalAuthority,
    derive_long_term_goal_scope,
)


def _authority(tmp_path: Path):
    migration_ts_ms = int(datetime.now().astimezone().timestamp() * 1000)
    db_path = tmp_path / "long-term-goal.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne03-long-term-goal",
        ts_ms=migration_ts_ms,
    )
    assert migrated.success is True, migrated.as_dict()
    now = int(datetime.now().astimezone().timestamp() * 1000)
    runtime_scope_id = "runtime-scope-long-term-goal"
    owner_id = "runtime-owner-long-term-goal"
    leases = EmbodimentOutboxRepository(
        db_path,
        runtime_scope_id=runtime_scope_id,
    )
    lease = leases.acquire_runtime_lease(
        worker_id=owner_id,
        lease_ms=120_000,
        now_ms=now,
    )
    assert lease is not None
    clock = [now]

    def runtime_authority():
        current = leases.validate_runtime_lease(
            worker_id=owner_id,
            lease_id=str(lease["lease_id"]),
            fencing_token=int(lease["fencing_token"]),
            now_ms=clock[0],
        )
        return (
            {**current, "runtime_scope_id": runtime_scope_id}
            if current is not None
            else None
        )

    repository = SQLiteLongTermGoalAuthority(
        db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority,
        clock=lambda: clock[0],
    )
    return db_path, repository, runtime_authority, clock


def _ingress(command_id: str) -> dict[str, object]:
    return {
        "allowed": True,
        "operator_command_id": command_id,
        "audit_ref": f"secure-ingress://audit/{command_id}",
        "principal_id": "person-ne03",
        "tenant_id": "",
    }


def test_long_term_goal_activate_replay_restart_and_revoke(
    tmp_path: Path,
) -> None:
    db_path, repository, runtime_authority, clock = _authority(tmp_path)
    workspace, subject = derive_long_term_goal_scope(
        principal_id="person-ne03",
        tenant_id=None,
    )
    activated = repository.apply_accepted_transition(
        authority=_ingress("long_term_goal.activate"),
        command_id="long_term_goal.activate",
        operation_id="operator-state-control://goal/activate-one",
        workspace_id=workspace,
        subject_id=subject,
        goal_id="reliable-assistant",
        expected_version=0,
        constraints=(
            {
                "code": "GOAL_RELIABILITY_REVIEW",
                "disposition": "REVIEW",
                "reason": "长期可靠性要求人工复核高风险变更",
            },
        ),
        observed_at_ms=clock[0],
    )
    assert activated.status == "ACTIVE"
    assert activated.version == 1
    assert activated.constraints[0].source_ref == activated.goal_ref

    replay = repository.apply_accepted_transition(
        authority=_ingress("long_term_goal.activate"),
        command_id="long_term_goal.activate",
        operation_id="operator-state-control://goal/activate-one",
        workspace_id=workspace,
        subject_id=subject,
        goal_id="reliable-assistant",
        expected_version=0,
        constraints=(
            {
                "code": "GOAL_RELIABILITY_REVIEW",
                "disposition": "REVIEW",
                "reason": "长期可靠性要求人工复核高风险变更",
            },
        ),
        observed_at_ms=clock[0],
    )
    assert replay == activated

    restarted = SQLiteLongTermGoalAuthority(
        db_path,
        runtime_scope_id="runtime-scope-long-term-goal",
        runtime_authority_provider=runtime_authority,
        clock=lambda: clock[0],
    )
    assert restarted.list_active(
        workspace_id=workspace,
        subject_id=subject,
    ) == (activated,)
    identity = restarted.active_identity(
        workspace_id=workspace,
        subject_id=subject,
    )
    assert identity["goal_refs"] == [activated.goal_ref]
    assert identity["constraints"] == [
        activated.constraints[0].to_dict()
    ]
    facts = restarted.list_active_world_facts(
        workspace_id=workspace,
        subject_id=subject,
        observed_at_ms=clock[0],
        source_ref="world-observation://pending/source",
    )
    assert [item.key for item in facts] == [
        "goal.long_term.ref",
        "goal.long_term.constraint",
    ]

    clock[0] += 1
    revoked = restarted.apply_accepted_transition(
        authority=_ingress("long_term_goal.revoke"),
        command_id="long_term_goal.revoke",
        operation_id="operator-state-control://goal/revoke-one",
        workspace_id=workspace,
        subject_id=subject,
        goal_id="reliable-assistant",
        expected_version=1,
        observed_at_ms=clock[0],
    )
    assert revoked.status == "REVOKED"
    assert revoked.version == 2
    assert revoked.constraints == ()
    assert restarted.list_active(
        workspace_id=workspace,
        subject_id=subject,
    ) == ()


def test_long_term_goal_fails_closed_on_scope_stale_and_tamper(
    tmp_path: Path,
) -> None:
    db_path, repository, _runtime_authority, clock = _authority(tmp_path)
    workspace, subject = derive_long_term_goal_scope(
        principal_id="person-ne03",
        tenant_id=None,
    )
    with pytest.raises(
        LongTermGoalRepositoryError,
        match="INGRESS_SCOPE_DRIFT",
    ):
        repository.apply_accepted_transition(
            authority=_ingress("long_term_goal.activate"),
            command_id="long_term_goal.activate",
            operation_id="operator-state-control://goal/wrong-scope",
            workspace_id="personal:other",
            subject_id=subject,
            goal_id="reliable-assistant",
            expected_version=0,
            constraints=(
                {
                    "code": "GOAL_RELIABILITY_REVIEW",
                    "disposition": "REVIEW",
                    "reason": "",
                },
            ),
            observed_at_ms=clock[0],
        )

    repository.apply_accepted_transition(
        authority=_ingress("long_term_goal.activate"),
        command_id="long_term_goal.activate",
        operation_id="operator-state-control://goal/activate-two",
        workspace_id=workspace,
        subject_id=subject,
        goal_id="reliable-assistant",
        expected_version=0,
        constraints=(
            {
                "code": "GOAL_RELIABILITY_BLOCK",
                "disposition": "BLOCK",
                "reason": "",
            },
        ),
        observed_at_ms=clock[0],
    )
    with pytest.raises(
        LongTermGoalRepositoryError,
        match="EXPECTED_VERSION_STALE",
    ):
        repository.apply_accepted_transition(
            authority=_ingress("long_term_goal.activate"),
            command_id="long_term_goal.activate",
            operation_id="operator-state-control://goal/stale",
            workspace_id=workspace,
            subject_id=subject,
            goal_id="reliable-assistant",
            expected_version=0,
            constraints=(
                {
                    "code": "GOAL_RELIABILITY_REVIEW",
                    "disposition": "REVIEW",
                    "reason": "",
                },
            ),
            observed_at_ms=clock[0],
        )

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                """
                UPDATE long_term_goal_event
                SET event_digest = ?
                WHERE operation_id = ?
                """,
                (
                    "sha256:" + "0" * 64,
                    "operator-state-control://goal/activate-two",
                ),
            )
        asset = connection.execute(
            """
            SELECT disposition FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (LONG_TERM_GOAL_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert asset is not None and asset[0] in {
        "APPLIED",
        "APPLIED_WITH_COMPAT",
        "LEGACY_BACKFILL",
    }
