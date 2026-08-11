from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.scheduler_repository import SchedulerDefinitionRepository
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.scheduler.governed_runtime import (
    SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED,
    GovernedSchedulerRuntimeError,
    SchedulerCommandService,
    SchedulerDefinitionPolicyEvaluator,
)
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry
from host_brain.secure_ingress import (
    ClaimStatus,
    IngressAuditEvent,
)


NOW = 1_800_000_000_000


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "scheduler-uow-runtime.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-scheduler-durable-uow-runtime",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    return db_path


def _authority(
    store: SQLiteSecureIngressStore,
    *,
    request_id: str,
    suffix: str,
    body_material: str,
) -> dict[str, object]:
    body_sha256 = _sha256(body_material)
    claim = store.try_claim(
        principal_id="scheduler-service-principal",
        surface="scheduler.admin",
        action="scheduler:admin",
        required_scopes=("scheduler:admin",),
        idempotency_key=f"scheduler-runtime-{suffix}",
        nonce=f"scheduler-runtime-nonce-{suffix}",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=NOW,
        expires_at_ms=NOW + 60_000,
    )
    assert claim.status is ClaimStatus.NEW
    assert claim.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"scheduler-runtime-{suffix}",
            surface="scheduler.admin",
            action="scheduler:admin",
            method="POST",
            normalized_path="/api/scheduler/tasks",
            principal_id="scheduler-service-principal",
            tenant_id="scheduler-runtime",
            key_id="scheduler-runtime-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + _sha256("credential-" + suffix),
            requested_scopes=("scheduler:admin",),
            required_scopes=("scheduler:admin",),
            body_sha256=body_sha256,
            idempotency_digest="sha256:" + _sha256("idempotency-" + suffix),
            nonce_digest="sha256:" + _sha256("nonce-" + suffix),
            transport_key_digest="sha256:" + _sha256("transport-" + suffix),
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=claim.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=NOW,
        ),
    )
    return {
        "allowed": True,
        "duplicate": False,
        "request_id": request_id,
        "principal_id": "scheduler-service-principal",
        "claim_ref": claim.claim_ref,
        "audit_ref": audit_ref,
        "body_sha256": body_sha256,
        "existing_result_ref": None,
    }


def _definition() -> dict[str, object]:
    return {
        "trigger_mode": "interval",
        "trigger_config": {"seconds": 60},
        "intent_template": {"intent_type": "durable_uow"},
        "worker_key": "intent_dispatch",
        "priority": "P1",
        "mode_allowlist": ["normal"],
        "resource_profile": {},
        "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
        "enabled": True,
    }


def _clock_request_id(task_id: str, revision: int, scheduled_at_ms: int) -> str:
    material = json.dumps(
        {
            "definition_revision": revision,
            "scheduled_at_epoch_ms": scheduled_at_ms,
            "schema_version": "natsume.scheduler.clock-authority.v1",
            "task_id": task_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "scheduler.clock.request." + hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()[:48]


class _FailFirstCompletion:
    def __init__(self, store: SQLiteSecureIngressStore) -> None:
        self._store = store
        self.calls = 0

    def complete_claim(self, **kwargs) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated crash before claim completion")
        self._store.complete_claim(**kwargs)


class _RejectingIntentEngine:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate_intent(self, **kwargs) -> dict[str, object]:
        self.calls += 1
        return {
            "request_id": kwargs["request_id"],
            "decision": "REJECT",
            "reason_code": "GOVERNOR_POLICY_DENIED",
            "detail": "immutable policy rejection",
        }


class _NeverExecutesRuntime:
    def existing_command(self, *, request_id: str, command_surface=None):
        del request_id, command_surface
        return None

    def health(self) -> dict[str, bool]:
        return {"active": True}


def _service(
    *,
    definitions: SchedulerDefinitionRepository,
    claims: object,
    intent_engine: object,
) -> SchedulerCommandService:
    workers = SchedulerWorkerRegistry()
    workers.register(
        "intent_dispatch",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("Governor-rejected occurrence must not execute a worker")
        ),
    )
    return SchedulerCommandService(
        runtime=_NeverExecutesRuntime(),
        intent_engine=intent_engine,
        claim_result_store=claims,
        definition_repository=definitions,
        policy_evaluator=SchedulerDefinitionPolicyEvaluator(
            current_mode=lambda: "normal"
        ),
        worker_registry=workers,
    )


@pytest.mark.asyncio
async def test_governor_reject_is_terminal_recoverable_and_never_reevaluated(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    definitions = SchedulerDefinitionRepository(db_path)
    head = definitions.publish_definition(
        task_id="scheduler.reject.task",
        definition=_definition(),
        expected_head_version=0,
        next_scheduled_at_ms=NOW,
    )
    request_id = _clock_request_id(
        "scheduler.reject.task",
        head["definition_revision"],
        NOW,
    )
    claim_store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    authority = _authority(
        claim_store,
        request_id=request_id,
        suffix="governor-reject",
        body_material="governor-reject",
    )
    due_retry_key = (
        f"due:scheduler.reject.task:{head['definition_revision']}:{NOW}"
    )
    due_claim = definitions.claim_clock_retry(
        retry_key=due_retry_key,
        retry_kind="DUE",
        task_id="scheduler.reject.task",
        occurrence_id=None,
        owner_id="governor-reject-clock-owner",
        at_ms=NOW - 1,
        lease_duration_ms=120_000,
    )
    occurrence = definitions.admit_due_occurrence(
        task_id="scheduler.reject.task",
        definition_revision=head["definition_revision"],
        expected_head_version=head["head_version"],
        scheduled_at_ms=NOW,
        next_scheduled_at_ms=NOW + 60_000,
        payload={"source": "clock"},
        authority=authority,
        due_retry_key=due_retry_key,
        lease_owner_id="governor-reject-clock-owner",
        lease_fencing_token=int(due_claim["lease_fencing_token"]),
        lease_duration_ms=120_000,
    )
    engine = _RejectingIntentEngine()
    fail_once = _FailFirstCompletion(claim_store)
    service = _service(
        definitions=definitions,
        claims=fail_once,
        intent_engine=engine,
    )
    try:
        with pytest.raises(GovernedSchedulerRuntimeError) as first_failure:
            await service.execute_occurrence(
                occurrence=occurrence,
                authority=authority,
                received_at_ms=NOW,
            )
        assert (
            first_failure.value.reason_code
            == SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED
        )
        durable = definitions.get_terminal_outcome(occurrence["occurrence_id"])
        assert durable is not None
        assert durable["reason_code"] == "GOVERNOR_POLICY_DENIED"
        pending = definitions.list_pending_occurrences()
        assert [row["occurrence_id"] for row in pending] == [
            occurrence["occurrence_id"]
        ]
        assert pending[0]["existing_result_ref"] == durable["receipt_ref"]
        assert engine.calls == 1

        duplicate = {
            **authority,
            "allowed": False,
            "duplicate": True,
            "existing_result_ref": None,
        }
        recovered = await service.execute_occurrence(
            occurrence=occurrence,
            authority=duplicate,
            received_at_ms=NOW + 1_000,
        )
        replayed = await _service(
            definitions=SchedulerDefinitionRepository(db_path),
            claims=claim_store,
            intent_engine=engine,
        ).execute_occurrence(
            occurrence=occurrence,
            authority={
                **duplicate,
                "existing_result_ref": durable["receipt_ref"],
            },
            received_at_ms=NOW + 2_000,
        )
        assert recovered == replayed
        assert recovered["decision"] == "REJECT"
        assert recovered["execution_receipt"]["receipt_ref"] == durable["receipt_ref"]
        assert engine.calls == 1
        assert SchedulerDefinitionRepository(db_path).list_pending_occurrences() == []
        with sqlite3.connect(str(db_path)) as conn:
            command_count = conn.execute(
                "SELECT COUNT(*) FROM host_command_journal WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
                conn.execute(
                    "UPDATE scheduler_occurrence_terminal_outcome SET reason_code = 'drift'"
                )
        assert command_count == (0,)
    finally:
        claim_store.close()


@pytest.mark.asyncio
async def test_admin_commit_to_claim_crash_gap_recovers_exact_result_without_remutation(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    definitions = SchedulerDefinitionRepository(db_path)
    claim_store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    authority = _authority(
        claim_store,
        request_id="request-admin-crash-gap",
        suffix="admin-crash-gap",
        body_material="admin-crash-gap",
    )
    accepted_authority = {
        **authority,
        "ingress_surface": "scheduler.admin",
        "operator_command_id": "scheduler.task.publish",
        "action": "scheduler:admin",
        "required_scopes": ("scheduler:admin",),
        "method": "POST",
        "normalized_path": "/api/scheduler/tasks",
    }
    fail_once = _FailFirstCompletion(claim_store)
    service = _service(
        definitions=definitions,
        claims=fail_once,
        intent_engine=SimpleNamespace(evaluate_intent=lambda **_: {}),
    )
    try:
        operation = service.apply_accepted_publish_definition(
            task_id="scheduler.admin.crash",
            definition=_definition(),
            authority=accepted_authority,
            expected_head_version=0,
            next_scheduled_at_ms=NOW + 60_000,
        )
        with pytest.raises(GovernedSchedulerRuntimeError) as failure:
            await service.complete_accepted_admin_claim(
                authority=accepted_authority,
                result_ref=str(operation["receipt_ref"]),
                received_at_ms=NOW,
            )
        assert failure.value.reason_code == SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED
        head_after_crash = definitions.get("scheduler.admin.crash")
        assert head_after_crash is not None
        assert head_after_crash["head_version"] == 1
        with sqlite3.connect(str(db_path)) as conn:
            journal = conn.execute(
                """
                SELECT receipt_ref, result_json
                FROM scheduler_admin_operation_result
                WHERE request_id = ?
                """,
                (authority["request_id"],),
            ).fetchone()
            claim_result = conn.execute(
                """
                SELECT result_ref FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (authority["claim_ref"],),
            ).fetchone()
        assert journal is not None
        assert claim_result == (None,)

        restarted_service = _service(
            definitions=SchedulerDefinitionRepository(db_path),
            claims=claim_store,
            intent_engine=SimpleNamespace(evaluate_intent=lambda **_: {}),
        )
        recovered = restarted_service.resolve_accepted_publish_definition(
            task_id="scheduler.admin.crash",
            definition=_definition(),
            authority=accepted_authority,
            expected_head_version=0,
            next_scheduled_at_ms=NOW + 60_000,
        )
        replayed = restarted_service.apply_accepted_publish_definition(
            task_id="scheduler.admin.crash",
            definition=_definition(),
            authority=accepted_authority,
            expected_head_version=0,
            next_scheduled_at_ms=NOW + 60_000,
        )
        assert recovered is not None
        assert recovered == replayed
        assert recovered["result"]["head_version"] == 1
        assert definitions.get("scheduler.admin.crash")["head_version"] == 1
        await restarted_service.complete_accepted_admin_claim(
            authority=accepted_authority,
            result_ref=str(recovered["receipt_ref"]),
            received_at_ms=NOW + 1_000,
        )
        with sqlite3.connect(str(db_path)) as conn:
            claim_result = conn.execute(
                """
                SELECT result_ref FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (authority["claim_ref"],),
            ).fetchone()
        assert claim_result == (journal[0],)
        assert str(journal[0]).startswith("scheduleradminresult://sha256/")
    finally:
        claim_store.close()
