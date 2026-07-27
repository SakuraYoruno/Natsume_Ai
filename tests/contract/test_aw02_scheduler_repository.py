from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.contracts.scheduler_clock_batch import (
    SchedulerClockBatchError,
    validate_scheduler_clock_batch,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.host_command_repository import (
    HOST_COMMAND_INGRESS_CONFLICT,
    HostCommandRepository,
    HostCommandRepositoryError,
)
from host_brain.persistence.scheduler_repository import (
    SCHEDULER_DEFINITION_CONFLICT,
    SCHEDULER_CLOCK_RETRY_CONFLICT,
    SCHEDULER_CLOCK_RETRY_MIGRATION_ASSET_KEY,
    SCHEDULER_CLOCK_RETRY_MIGRATION_SHA256,
    SCHEDULER_EXECUTION_RESULT_CONFLICT,
    SCHEDULER_EXECUTION_RESULT_INVALID,
    SCHEDULER_MIGRATION_ASSET_KEY,
    SCHEDULER_MIGRATION_SHA256,
    SCHEDULER_OCCURRENCE_CONFLICT,
    SCHEDULER_SCHEMA_NOT_READY,
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
    SchedulerRepositoryError,
)
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
    SecureIngressStoreError,
)
from host_brain.scheduler.clock import SchedulerClock
from host_brain.secure_ingress import (
    ClaimStatus,
    IngressAuditEvent,
    SecureIngressAuthority,
)
from natsume_contracts.task import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_800_000_000_000


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "scheduler-runtime.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw02-scheduler-runtime",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _definition(*, enabled: bool = True, seconds: int = 60) -> dict:
    return {
        "task_id": "scheduler.test.task",
        "trigger_mode": "interval",
        "trigger_config": {"seconds": seconds},
        "intent_template": {
            "intent_type": "scheduler_test",
            "payload": {"scope": "contract"},
        },
        "priority": "MEDIUM",
        "enabled": enabled,
    }


def _publish(repository: SchedulerDefinitionRepository) -> dict:
    return repository.publish_definition(
        task_id="scheduler.test.task",
        definition=_definition(),
        expected_head_version=0,
        next_scheduled_at_ms=NOW + 1_000,
    )


def test_clock_batch_is_one_content_addressed_repository_snapshot(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = SchedulerDefinitionRepository(db_path, clock=lambda: NOW)
    repository.publish_definition(
        task_id="scheduler.test.first",
        definition={
            **_definition(),
            "task_id": "scheduler.test.first",
        },
        expected_head_version=0,
        next_scheduled_at_ms=NOW,
    )
    repository.publish_definition(
        task_id="scheduler.test.second",
        definition={
            **_definition(),
            "task_id": "scheduler.test.second",
        },
        expected_head_version=0,
        next_scheduled_at_ms=NOW,
    )

    first = repository.read_clock_batch(due_at_ms=NOW, limit=2)
    reopened = validate_scheduler_clock_batch(
        first,
        expected_captured_at_ms=NOW,
        expected_limit=2,
    )
    assert [row["task_id"] for row in reopened["due_definitions"]] == [
        "scheduler.test.first",
        "scheduler.test.second",
    ]
    assert reopened["pending_occurrences"] == []

    restarted = SchedulerDefinitionRepository(
        db_path,
        clock=lambda: NOW,
    ).read_clock_batch(due_at_ms=NOW, limit=2)
    assert restarted["batch_ref"] == first["batch_ref"]

    first["due_definitions"][0]["definition"]["worker_key"] = "drifted"
    with pytest.raises(SchedulerClockBatchError, match="digest"):
        validate_scheduler_clock_batch(first)


def _claim_due(
    repository: SchedulerDefinitionRepository,
    *,
    task_id: str,
    definition_revision: int,
    scheduled_at_ms: int,
    owner_id: str = "contract-clock-owner",
    at_ms: int = NOW,
    lease_duration_ms: int = 120_000,
) -> dict[str, object]:
    return repository.claim_clock_retry(
        retry_key=f"due:{task_id}:{definition_revision}:{scheduled_at_ms}",
        retry_kind="DUE",
        task_id=task_id,
        occurrence_id=None,
        owner_id=owner_id,
        at_ms=at_ms,
        lease_duration_ms=lease_duration_ms,
    )


def _admission_lease(claim: dict[str, object]) -> dict[str, object]:
    return {
        "due_retry_key": str(claim["retry_key"]),
        "lease_owner_id": str(claim["lease_owner_id"]),
        "lease_fencing_token": int(claim["lease_fencing_token"]),
        "lease_duration_ms": 120_000,
    }


def test_clock_retry_backoff_survives_restart_and_fences_two_owners(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = SchedulerDefinitionRepository(db_path, clock=lambda: NOW)
    key = "due:scheduler.test.task:1:1800000001000"

    first = repository.claim_clock_retry(
        retry_key=key,
        retry_kind="DUE",
        task_id="scheduler.test.task",
        occurrence_id=None,
        owner_id="clock-owner-a",
        at_ms=NOW,
        lease_duration_ms=10_000,
    )
    assert first["claimed"] is True
    assert first["lease_fencing_token"] == 1

    competing = SchedulerDefinitionRepository(
        db_path,
        clock=lambda: NOW + 1,
    ).claim_clock_retry(
        retry_key=key,
        retry_kind="DUE",
        task_id="scheduler.test.task",
        occurrence_id=None,
        owner_id="clock-owner-b",
        at_ms=NOW + 1,
        lease_duration_ms=10_000,
    )
    assert competing["claimed"] is False
    assert competing["lease_owner_id"] == "clock-owner-a"

    deferred = repository.defer_clock_retry(
        retry_key=key,
        owner_id="clock-owner-a",
        fencing_token=1,
        at_ms=NOW + 2,
        next_retry_at_ms=NOW + 5_000,
        reason_code="SCHEDULER_CLOCK_OCCURRENCE_FAILED",
    )
    assert deferred["failure_count"] == 1

    restarted = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 2)
    too_early = restarted.claim_clock_retry(
        retry_key=key,
        retry_kind="DUE",
        task_id="scheduler.test.task",
        occurrence_id=None,
        owner_id="clock-owner-b",
        at_ms=NOW + 4_999,
        lease_duration_ms=10_000,
    )
    assert too_early["claimed"] is False

    reclaimed = restarted.claim_clock_retry(
        retry_key=key,
        retry_kind="DUE",
        task_id="scheduler.test.task",
        occurrence_id=None,
        owner_id="clock-owner-b",
        at_ms=NOW + 5_000,
        lease_duration_ms=10_000,
    )
    assert reclaimed["claimed"] is True
    assert reclaimed["lease_fencing_token"] == 2
    with pytest.raises(SchedulerRepositoryError) as stale:
        repository.clear_clock_retry(
            retry_key=key,
            owner_id="clock-owner-a",
            fencing_token=1,
            at_ms=NOW + 5_000,
        )
    assert stale.value.reason_code == SCHEDULER_CLOCK_RETRY_CONFLICT
    assert restarted.clear_clock_retry(
        retry_key=key,
        owner_id="clock-owner-b",
        fencing_token=2,
        at_ms=NOW + 5_000,
    )
    assert restarted.clock_retry_summary() == {
        "retry_count": 0,
        "next_retry_at_ms": None,
    }
    next_generation = restarted.claim_clock_retry(
        retry_key=key,
        retry_kind="DUE",
        task_id="scheduler.test.task",
        occurrence_id=None,
        owner_id="clock-owner-a",
        at_ms=NOW + 5_001,
        lease_duration_ms=10_000,
    )
    assert next_generation["claimed"] is True
    assert next_generation["lease_fencing_token"] == 3
    with pytest.raises(SchedulerRepositoryError) as aba:
        restarted.clear_clock_retry(
            retry_key=key,
            owner_id="clock-owner-b",
            fencing_token=2,
            at_ms=NOW + 5_001,
        )
    assert aba.value.reason_code == SCHEDULER_CLOCK_RETRY_CONFLICT
    assert restarted.clear_clock_retry(
        retry_key=key,
        owner_id="clock-owner-a",
        fencing_token=3,
        at_ms=NOW + 5_001,
    )


def test_0117_scheduler_clock_retry_asset_is_checksum_bound(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
            (SCHEDULER_CLOCK_RETRY_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert row == (SCHEDULER_CLOCK_RETRY_MIGRATION_SHA256, "APPLIED")


def test_clock_retry_renewal_blocks_takeover_and_expired_fence_fails_closed(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    first = SchedulerDefinitionRepository(db_path, clock=lambda: NOW)
    key = "due:scheduler.test.task:1:1800000000000"
    claim = first.claim_clock_retry(
        retry_key=key,
        retry_kind="DUE",
        task_id="scheduler.test.task",
        occurrence_id=None,
        owner_id="renew-owner-a",
        at_ms=NOW,
        lease_duration_ms=1_000,
    )
    renewed = first.renew_clock_retry(
        retry_key=key,
        owner_id="renew-owner-a",
        fencing_token=int(claim["lease_fencing_token"]),
        at_ms=NOW + 900,
        lease_duration_ms=1_000,
    )
    assert renewed["lease_expires_at_ms"] == NOW + 1_900

    second = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 1_001)
    blocked = second.claim_clock_retry(
        retry_key=key,
        retry_kind="DUE",
        task_id="scheduler.test.task",
        occurrence_id=None,
        owner_id="renew-owner-b",
        at_ms=NOW + 1_001,
        lease_duration_ms=1_000,
    )
    assert blocked["claimed"] is False
    assert blocked["lease_owner_id"] == "renew-owner-a"

    with pytest.raises(SchedulerRepositoryError) as expired:
        first.clear_clock_retry(
            retry_key=key,
            owner_id="renew-owner-a",
            fencing_token=int(claim["lease_fencing_token"]),
            at_ms=NOW + 1_900,
        )
    assert expired.value.reason_code == SCHEDULER_CLOCK_RETRY_CONFLICT
    takeover = second.claim_clock_retry(
        retry_key=key,
        retry_kind="DUE",
        task_id="scheduler.test.task",
        occurrence_id=None,
        owner_id="renew-owner-b",
        at_ms=NOW + 1_900,
        lease_duration_ms=1_000,
    )
    assert takeover["claimed"] is True
    assert takeover["lease_fencing_token"] == 2
    with pytest.raises(SchedulerRepositoryError) as stale:
        first.defer_clock_retry(
            retry_key=key,
            owner_id="renew-owner-a",
            fencing_token=int(claim["lease_fencing_token"]),
            at_ms=NOW + 1_901,
            next_retry_at_ms=NOW + 5_000,
            reason_code="STALE_OWNER",
        )
    assert stale.value.reason_code == SCHEDULER_CLOCK_RETRY_CONFLICT


def _scheduler_claim(
    db_path: Path,
    *,
    request_id: str = "request-scheduler-result",
    suffix: str = "result",
    body_material: str = "scheduler-result-input",
) -> tuple[SQLiteSecureIngressStore, dict[str, object]]:
    body_sha256 = _sha256(body_material).removeprefix("sha256:")
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    decision = store.try_claim(
        principal_id="service-scheduler-clock",
        surface="scheduler.admin",
        action="scheduler:admin",
        required_scopes=("scheduler:admin",),
        idempotency_key=f"scheduler-{suffix}-idempotency",
        nonce=f"scheduler-{suffix}-nonce",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=NOW,
        expires_at_ms=NOW + 60_000,
    )
    assert decision.status is ClaimStatus.NEW
    assert decision.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=decision.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"scheduler-{suffix}-correlation",
            surface="scheduler.admin",
            action="scheduler:admin",
            method="POST",
            normalized_path="/internal/scheduler/clock",
            principal_id="service-scheduler-clock",
            tenant_id="scheduler-runtime",
            key_id="scheduler-clock-key",
            credential_kind="hmac-sha256",
            credential_ref_digest=_sha256("scheduler-clock-credential"),
            requested_scopes=("scheduler:admin",),
            required_scopes=("scheduler:admin",),
            body_sha256=body_sha256,
            idempotency_digest=_sha256(f"scheduler-{suffix}-idempotency"),
            nonce_digest=_sha256(f"scheduler-{suffix}-nonce"),
            transport_key_digest=_sha256(f"scheduler-{suffix}-transport"),
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=decision.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=NOW,
        ),
    )
    return store, {
        "allowed": True,
        "duplicate": False,
        "reason_code": "SECURE_INGRESS_ALLOWED",
        "request_id": request_id,
        "principal_id": "service-scheduler-clock",
        "claim_ref": decision.claim_ref,
        "audit_ref": audit_ref,
        "body_sha256": body_sha256,
        "existing_result_ref": None,
    }


def _durable_authority(
    db_path: Path,
    *,
    request_id: str,
    suffix: str,
    body_material: str,
) -> dict[str, object]:
    store, authority = _scheduler_claim(
        db_path,
        request_id=request_id,
        suffix=suffix,
        body_material=body_material,
    )
    store.close()
    return authority


def _clock_request_id(task_id: str, revision: int, scheduled_at_ms: int) -> str:
    material = json.dumps(
        {
            "definition_revision": revision,
            "scheduled_at_epoch_ms": scheduled_at_ms,
            "schema_version": "natsume.scheduler.clock-authority.v1",
            "task_id": task_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]
    return f"scheduler.clock.request.{digest}"


def _admit_command(
    db_path: Path,
    *,
    occurrence_id: str,
    ingress_claim_ref: str | None = None,
    ingress_audit_ref: str | None = None,
) -> tuple[str, str]:
    work_order = WorkOrderV3(
        work_order_id="work-order-scheduler-result",
        domain="organ_ops",
        run_mode="worker",
        objective="prepare one governed scheduler operation",
        input_pack={"input_ref": occurrence_id},
        allowed_actions=(),
        forbidden_actions=("bypass_task_runtime",),
        acceptance_criteria=("operation plan accepted before effect dispatch",),
        root_work_order_id="work-order-scheduler-result",
        task_graph_id="graph-scheduler-result",
        task_node_id="node-scheduler-result",
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="work-order:work-order-scheduler-result",
            limits=BudgetAmount(
                token_count=10_000,
                tool_call_count=10,
                time_ms=60_000,
                concurrency=1,
                retry_count=3,
                model_cost_micros=1_000_000,
            ),
        ),
        business_idempotency_scope="scheduler:production",
        operation_fingerprint=_sha256("scheduler-result-operation"),
    )
    node = TaskNode(
        node_id="node-scheduler-result",
        dependencies=(),
        route="delegate:scheduler_production",
        input_refs=(occurrence_id,),
        output_contract={"type": "scheduler_operation_plan"},
        retry_policy={"max_attempts": 1},
        risk_level=1,
        acceptance_criteria=("operation plan accepted before effect dispatch",),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id="graph-scheduler-result",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://scheduler/runtime",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(node,),
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://work-order-scheduler-result/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    command = build_host_command(
        surface=HostCommandSurface.SCHEDULER,
        request_id="request-scheduler-result",
        session_id="session-scheduler-result",
        principal_id="service-scheduler-clock",
        ingress_claim_ref=ingress_claim_ref,
        ingress_audit_ref=ingress_audit_ref,
        input_ref=occurrence_id,
        input_sha256=_sha256("scheduler-result-input"),
        root_work_order_id=work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=1,
        route_type="delegate",
        target_id="scheduler.test.task",
        business_scope="scheduler:production",
        operation_fingerprint=_sha256("scheduler-result-operation"),
        created_at_ms=NOW,
    )
    HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    return command.command_id, work_order.work_order_id


def test_definition_revision_head_cas_restart_and_soft_delete(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    repository = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 10)

    first = _publish(repository)
    restarted = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 20)
    replay = _publish(restarted)
    changed = restarted.publish_definition(
        task_id="scheduler.test.task",
        definition=_definition(seconds=120),
        expected_head_version=first["head_version"],
        next_scheduled_at_ms=NOW + 2_000,
    )

    assert first["definition_revision"] == 1
    assert replay["replayed"] is True
    assert replay["next_scheduled_at_ms"] == NOW + 1_000
    assert changed["definition_revision"] == 2
    assert changed["head_version"] == 2
    assert restarted.get_revision("scheduler.test.task", 1)["definition"] == _definition()
    assert len(restarted.list_definitions()) == 1
    with pytest.raises(SchedulerRepositoryError) as stale:
        restarted.set_enabled(
            task_id="scheduler.test.task",
            enabled=False,
            expected_head_version=1,
        )
    assert stale.value.reason_code == SCHEDULER_DEFINITION_CONFLICT

    deleted = restarted.delete_definition(
        task_id="scheduler.test.task",
        expected_head_version=2,
    )
    assert deleted["retired"] is True
    assert restarted.list_definitions() == []
    assert restarted.list_definitions(include_retired=True)[0]["task_id"] == (
        "scheduler.test.task"
    )
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute(
                """
                UPDATE scheduler_definition_revision
                SET definition_digest = definition_digest
                WHERE task_id = 'scheduler.test.task' AND definition_revision = 1
                """
            )


def test_identical_definition_reschedules_due_pointer_with_head_cas(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 10)
    first = _publish(repository)

    rescheduled = repository.publish_definition(
        task_id="scheduler.test.task",
        definition=_definition(),
        expected_head_version=first["head_version"],
        next_scheduled_at_ms=NOW + 5_000,
    )

    assert rescheduled["definition_revision"] == first["definition_revision"] == 1
    assert rescheduled["head_version"] == first["head_version"] + 1
    assert rescheduled["next_scheduled_at_ms"] == NOW + 5_000
    assert rescheduled["rescheduled"] is True
    assert rescheduled["replayed"] is False
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM scheduler_definition_revision WHERE task_id = ?",
            ("scheduler.test.task",),
        ).fetchone() == (1,)

    with pytest.raises(SchedulerRepositoryError) as stale:
        repository.publish_definition(
            task_id="scheduler.test.task",
            definition=_definition(),
            expected_head_version=first["head_version"],
            next_scheduled_at_ms=NOW + 9_000,
        )
    assert stale.value.reason_code == SCHEDULER_DEFINITION_CONFLICT
    durable = repository.get("scheduler.test.task")
    assert durable is not None
    assert durable["head_version"] == rescheduled["head_version"]
    assert durable["next_scheduled_at_ms"] == NOW + 5_000


def test_due_occurrence_and_reschedule_are_atomic_replayable_and_drift_safe(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    base = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 10)
    definition = _publish(base)
    clock_request = _clock_request_id(
        "scheduler.test.task",
        1,
        NOW + 1_000,
    )
    authority = _durable_authority(
        db_path,
        request_id=clock_request,
        suffix="due-1",
        body_material="clock-occurrence-1",
    )
    due_claim = _claim_due(
        base,
        task_id="scheduler.test.task",
        definition_revision=1,
        scheduled_at_ms=NOW + 1_000,
    )
    assert due_claim["claimed"] is True
    admission_lease = _admission_lease(due_claim)

    def crash(stage: str) -> None:
        if stage == "after_occurrence":
            raise RuntimeError("crash-before-reschedule")

    crashing = SchedulerDefinitionRepository(
        db_path,
        clock=lambda: NOW + 20,
        fault_injector=crash,
    )
    with pytest.raises(RuntimeError, match="crash-before-reschedule"):
        crashing.admit_due_occurrence(
            task_id="scheduler.test.task",
            definition_revision=1,
            expected_head_version=1,
            scheduled_at_ms=NOW + 1_000,
            next_scheduled_at_ms=NOW + 61_000,
            payload={"trigger": "clock"},
            authority=authority,
            **admission_lease,
        )
    assert base.get("scheduler.test.task")["head_version"] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM scheduler_occurrence").fetchone()[0] == 0

    # A manual request-owned gap must never consume the clock recovery LIMIT.
    manual_authority = _durable_authority(
        db_path,
        request_id="scheduler.manual.before-clock",
        suffix="manual-before-clock",
        body_material="manual-before-clock",
    )
    base.admit_manual_occurrence(
        task_id="scheduler.test.task",
        definition_revision=1,
        request_id="scheduler.manual.before-clock",
        scheduled_at_ms=NOW,
        payload={"trigger": "manual"},
        authority=manual_authority,
    )

    first = base.admit_due_occurrence(
        task_id="scheduler.test.task",
        definition_revision=1,
        expected_head_version=1,
        scheduled_at_ms=NOW + 1_000,
        next_scheduled_at_ms=NOW + 61_000,
        payload={"trigger": "clock"},
        authority=authority,
        **admission_lease,
    )
    with sqlite3.connect(db_path) as conn:
        retry_rows = conn.execute(
            """
            SELECT retry_key, retry_kind, task_id, occurrence_id,
                   lease_owner_id, lease_fencing_token
            FROM scheduler_clock_retry_state
            """
        ).fetchall()
    assert retry_rows == [
        (
            first["occurrence_id"],
            "OCCURRENCE",
            "scheduler.test.task",
            first["occurrence_id"],
            "contract-clock-owner",
            1,
        )
    ]
    competing_occurrence = SchedulerDefinitionRepository(
        db_path,
        clock=lambda: NOW + 11,
    ).claim_clock_retry(
        retry_key=str(first["occurrence_id"]),
        retry_kind="OCCURRENCE",
        task_id="scheduler.test.task",
        occurrence_id=str(first["occurrence_id"]),
        owner_id="contract-clock-owner-b",
        at_ms=NOW + 11,
        lease_duration_ms=120_000,
    )
    assert competing_occurrence["claimed"] is False
    assert competing_occurrence["lease_owner_id"] == "contract-clock-owner"
    replay = SchedulerDefinitionRepository(
        db_path,
        clock=lambda: NOW + 11,
    ).admit_due_occurrence(
        task_id="scheduler.test.task",
        definition_revision=1,
        expected_head_version=1,
        scheduled_at_ms=NOW + 1_000,
        next_scheduled_at_ms=NOW + 61_000,
        payload={"trigger": "clock"},
        authority=authority,
        **admission_lease,
    )

    assert first["head_version"] == 2
    assert replay["replayed"] is True
    assert replay["head_version"] == 2
    pending_rows = base.list_pending_occurrences(limit=1)
    assert len(pending_rows) == 1
    pending = pending_rows[0]
    assert pending["source"] == "CLOCK"
    assert pending["occurrence_id"] == first["occurrence_id"]
    assert pending["authority"] == {
        **authority,
        "body_sha256": str(authority["body_sha256"]).removeprefix("sha256:"),
    }
    assert base.list_due(due_at_ms=NOW + 60_999) == []
    assert base.list_due(due_at_ms=NOW + 61_000)[0]["task_id"] == (
        "scheduler.test.task"
    )
    with pytest.raises(SchedulerRepositoryError) as drift:
        base.admit_due_occurrence(
            task_id="scheduler.test.task",
            definition_revision=1,
            expected_head_version=1,
            scheduled_at_ms=NOW + 1_000,
            next_scheduled_at_ms=NOW + 61_000,
            payload={"trigger": "drifted"},
            authority=authority,
            **admission_lease,
        )
    assert drift.value.reason_code == SCHEDULER_OCCURRENCE_CONFLICT


@pytest.mark.asyncio
async def test_two_clock_instances_do_not_dispatch_across_a_slow_lease_duration(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    loop = asyncio.get_running_loop()
    epoch_started = loop.time()

    def live_clock() -> int:
        return NOW + int((loop.time() - epoch_started) * 1_000)

    first_repository = SchedulerDefinitionRepository(db_path, clock=live_clock)
    first_repository.publish_definition(
        task_id="scheduler.test.task",
        definition=_definition(seconds=60),
        expected_head_version=0,
        next_scheduled_at_ms=NOW,
    )
    request_id = _clock_request_id("scheduler.test.task", 1, NOW)
    authority_raw = _durable_authority(
        db_path,
        request_id=request_id,
        suffix="slow-dual-owner",
        body_material="slow-dual-owner",
    )
    authority = SecureIngressAuthority(**authority_raw)
    dispatches: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    class Ingress:
        def evaluate_clock_occurrence(self, **kwargs):
            assert kwargs["scheduled_at_epoch_ms"] == NOW
            return authority

    class Service:
        def __init__(self, owner: str) -> None:
            self.owner = owner

        def preflight_definition(self, definition):
            return "normal"

        async def execute_occurrence(self, **kwargs):
            dispatches.append(self.owner)
            started.set()
            await release.wait()

    first_clock = SchedulerClock(
        definition_repository=first_repository,
        command_ingress=Ingress(),
        command_service=Service("owner-a"),
        owner_id="slow-clock-owner-a",
        now_ms=live_clock,
        retry_lease_duration_s=1,
        poll_interval_s=60,
        limit=10,
    )
    second_clock = SchedulerClock(
        definition_repository=SchedulerDefinitionRepository(
            db_path,
            clock=live_clock,
        ),
        command_ingress=Ingress(),
        command_service=Service("owner-b"),
        owner_id="slow-clock-owner-b",
        now_ms=live_clock,
        retry_lease_duration_s=1,
        poll_interval_s=60,
        limit=10,
    )

    first_run = asyncio.create_task(first_clock.run_once())
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(1.2)
    second_result = await second_clock.run_once()

    assert dispatches == ["owner-a"]
    assert second_result["executed"] == 0
    assert second_result["deferred"] == 1
    release.set()
    first_result = await asyncio.wait_for(first_run, timeout=2)
    assert first_result["executed"] == 1
    assert dispatches == ["owner-a"]


def test_manual_occurrence_is_idempotent_and_never_reschedules_clock(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 10)
    _publish(repository)
    authority = _durable_authority(
        db_path,
        request_id="scheduler.manual.request.1",
        suffix="manual-1",
        body_material="manual-occurrence-1",
    )

    first = repository.admit_manual_occurrence(
        task_id="scheduler.test.task",
        definition_revision=1,
        request_id="scheduler.manual.request.1",
        scheduled_at_ms=NOW + 500,
        payload={"trigger": "operator"},
        authority=authority,
    )
    replay = SchedulerDefinitionRepository(db_path).admit_manual_occurrence(
        task_id="scheduler.test.task",
        definition_revision=1,
        request_id="scheduler.manual.request.1",
        scheduled_at_ms=NOW + 500,
        payload={"trigger": "operator"},
        authority=authority,
    )

    assert replay["occurrence_id"] == first["occurrence_id"]
    assert replay["replayed"] is True
    assert repository.get_occurrence_by_request(
        "scheduler.manual.request.1",
        source="manual",
    )["occurrence_id"] == first["occurrence_id"]
    assert repository.get("scheduler.test.task")["next_scheduled_at_ms"] == NOW + 1_000
    with pytest.raises(SchedulerRepositoryError) as drift:
        repository.admit_manual_occurrence(
            task_id="scheduler.test.task",
            definition_revision=1,
            request_id="scheduler.manual.request.1",
            scheduled_at_ms=NOW + 500,
            payload={"trigger": "changed"},
            authority=authority,
        )
    assert drift.value.reason_code == SCHEDULER_OCCURRENCE_CONFLICT


def test_execution_result_is_immutable_command_bound_and_exactly_replayable(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    definitions = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 10)
    _publish(definitions)
    authority = _durable_authority(
        db_path,
        request_id="scheduler.manual.result.1",
        suffix="manual-result",
        body_material="manual-result-occurrence",
    )
    occurrence = definitions.admit_manual_occurrence(
        task_id="scheduler.test.task",
        definition_revision=1,
        request_id="scheduler.manual.result.1",
        scheduled_at_ms=NOW + 500,
        payload={"trigger": "operator"},
        authority=authority,
    )
    command_id, work_order_id = _admit_command(
        db_path,
        occurrence_id=occurrence["occurrence_id"],
    )
    repository = SchedulerExecutionResultRepository(db_path, clock=lambda: NOW + 30)
    with pytest.raises(SchedulerRepositoryError) as secret:
        repository.append(
            occurrence_id=occurrence["occurrence_id"],
            command_id=command_id,
            work_order_id=work_order_id,
            result={"status": "SUCCEEDED", "access_token": "must-not-persist"},
        )
    assert secret.value.reason_code == SCHEDULER_EXECUTION_RESULT_INVALID

    result = {
        "status": "SUCCEEDED",
        "summary": "scheduler worker completed",
        "token_count": 42,
    }

    first = repository.append(
        occurrence_id=occurrence["occurrence_id"],
        command_id=command_id,
        work_order_id=work_order_id,
        result=result,
    )
    replay = SchedulerExecutionResultRepository(db_path).append(
        occurrence_id=occurrence["occurrence_id"],
        command_id=command_id,
        work_order_id=work_order_id,
        result=result,
    )

    assert first["receipt_ref"].startswith("schedulerresult://sha256/")
    assert replay["replayed"] is True
    assert repository.get(first["receipt_ref"])["result"] == result
    assert repository.get_by_occurrence(occurrence["occurrence_id"])["command_id"] == (
        command_id
    )
    # Manual gaps are recovered by the request lane, never by SchedulerClock.
    assert definitions.list_pending_occurrences() == []
    recovery_store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    recovery_store.complete_claim(
        claim_ref=str(authority["claim_ref"]),
        result_ref=first["receipt_ref"],
        at_ms=NOW + 40,
    )
    recovery_store.close()
    assert definitions.list_pending_occurrences() == []
    with pytest.raises(SchedulerRepositoryError) as conflict:
        repository.append(
            occurrence_id=occurrence["occurrence_id"],
            command_id=command_id,
            work_order_id=work_order_id,
            result={"status": "FAILED", "summary": "drifted"},
        )
    assert conflict.value.reason_code == SCHEDULER_EXECUTION_RESULT_CONFLICT
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute(
                """
                UPDATE scheduler_execution_result
                SET result_digest = result_digest
                WHERE receipt_ref = ?
                """,
                (first["receipt_ref"],),
            )


def test_occurrence_snapshot_preserves_committed_authority_without_blocking_new_claim(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    definitions = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 10)
    _publish(definitions)
    store, authority = _scheduler_claim(
        db_path,
        request_id="scheduler.manual.ttl.1",
        suffix="manual-ttl",
        body_material="manual-ttl-occurrence",
    )
    store.close()
    occurrence = definitions.admit_manual_occurrence(
        task_id="scheduler.test.task",
        definition_revision=1,
        request_id="scheduler.manual.ttl.1",
        scheduled_at_ms=NOW + 500,
        payload={"trigger": "operator"},
        authority=authority,
    )

    cleanup_store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    try:
        next_claim = cleanup_store.try_claim(
            principal_id="service-scheduler-clock",
            surface="scheduler.admin",
            action="scheduler:admin",
            required_scopes=("scheduler:admin",),
            idempotency_key="scheduler-after-ttl-idempotency",
            nonce="scheduler-after-ttl-nonce",
            body_sha256="a" * 64,
            request_id="scheduler.manual.after-ttl",
            at_ms=NOW + 60_001,
            expires_at_ms=NOW + 120_001,
        )
        assert next_claim.status is ClaimStatus.NEW
    finally:
        cleanup_store.close()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
            (authority["claim_ref"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM scheduler_occurrence WHERE occurrence_id = ?",
            (occurrence["occurrence_id"],),
        ).fetchone()[0] == 1
    recovered = definitions.get_occurrence(occurrence["occurrence_id"])
    assert recovered is not None
    assert recovered["authority"]["claim_ref"] == authority["claim_ref"]


def test_schema_is_checksum_bound_and_fails_closed_on_metadata_drift(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        asset = conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE asset_key = ?",
            (SCHEDULER_MIGRATION_ASSET_KEY,),
        ).fetchone()
        assert asset is not None
        assert asset[0] == SCHEDULER_MIGRATION_SHA256
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, SCHEDULER_MIGRATION_ASSET_KEY),
        )

    with pytest.raises(SchedulerRepositoryError) as drift:
        SchedulerDefinitionRepository(db_path)
    assert drift.value.reason_code == SCHEDULER_SCHEMA_NOT_READY


def test_scheduler_claim_completes_only_with_command_bound_result_receipt(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    definitions = SchedulerDefinitionRepository(db_path, clock=lambda: NOW + 10)
    _publish(definitions)
    store, authority = _scheduler_claim(db_path)
    claim_ref = str(authority["claim_ref"])
    audit_ref = str(authority["audit_ref"])
    try:
        occurrence = definitions.admit_manual_occurrence(
            task_id="scheduler.test.task",
            definition_revision=1,
            request_id="request-scheduler-result",
            scheduled_at_ms=NOW + 500,
            payload={"trigger": "operator"},
            authority=authority,
        )
        command_id, work_order_id = _admit_command(
            db_path,
            occurrence_id=occurrence["occurrence_id"],
            ingress_claim_ref=claim_ref,
            ingress_audit_ref=audit_ref,
        )
        commands = HostCommandRepository(db_path, clock=lambda: NOW + 20)
        admitted = commands.get(command_id)
        assert admitted is not None
        with sqlite3.connect(db_path) as conn:
            result_ref = conn.execute(
                """
                SELECT result_ref FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (claim_ref,),
            ).fetchone()[0]
        assert result_ref is None

        result = SchedulerExecutionResultRepository(
            db_path,
            clock=lambda: NOW + 30,
        ).append(
            occurrence_id=occurrence["occurrence_id"],
            command_id=command_id,
            work_order_id=work_order_id,
            result={"status": "SUCCEEDED", "summary": "claim-safe completion"},
        )
        store.complete_claim(
            claim_ref=claim_ref,
            result_ref=result["receipt_ref"],
            at_ms=NOW + 40,
        )
        replay = commands.admit(
            admitted["command"],
            admitted["work_order"],
            admitted["assignment"],
            admitted["graph"],
        )
        assert replay["replayed"] is True

        with pytest.raises(SecureIngressStoreError, match="RESULT_CONFLICT"):
            store.complete_claim(
                claim_ref=claim_ref,
                result_ref="schedulerresult://sha256/" + "0" * 64,
                at_ms=NOW + 50,
            )
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                UPDATE secure_ingress_idempotency_claim
                SET result_ref = ? WHERE claim_ref = ?
                """,
                ("schedulerresult://sha256/" + "f" * 64, claim_ref),
            )
        with pytest.raises(HostCommandRepositoryError) as forged:
            commands.admit(
                admitted["command"],
                admitted["work_order"],
                admitted["assignment"],
                admitted["graph"],
            )
        assert forged.value.reason_code == HOST_COMMAND_INGRESS_CONFLICT
    finally:
        store.close()
