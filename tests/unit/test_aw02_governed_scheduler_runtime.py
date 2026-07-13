from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.runtime_events import RuntimeEventStore, RuntimeOutboxPublisher
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
)
from host_brain.scheduler.contracts import (
    SchedulerContractError,
    occurrence_work_order_id,
    validate_definition_payload,
)
from host_brain.scheduler.governed_runtime import (
    SCHEDULER_COMMAND_REPLAY,
    SCHEDULER_RUNTIME_MODE_BLOCKED,
    GovernedSchedulerRuntimeError,
    SchedulerCommandService,
    SchedulerDefinitionPolicyEvaluator,
    build_governed_scheduler_runtime,
)
from host_brain.scheduler.worker_registry import (
    SCHEDULER_HANDLER_NOT_REGISTERED,
    GovernedSchedulerWorker,
    SchedulerWorkerError,
    SchedulerWorkerRegistry,
    SchedulerWorkerResolver,
)


NOW = 1_800_000_000_000


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _definition(*, worker_key: str = "intent_dispatch", marker: str = "v1") -> dict:
    return {
        "trigger_mode": "interval",
        "trigger_config": {"seconds": 60},
        "intent_template": {"skill_name": "proactive_talk", "args": {"marker": marker}},
        "worker_key": worker_key,
        "priority": "P1",
        "mode_allowlist": ["normal"],
        "resource_profile": {"uses_llm": True},
        "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
        "enabled": True,
    }


def _insert_scheduler_authority(
    db_path: Path,
    *,
    request_id: str,
    principal_id: str,
    body_sha256: str,
    claim_ref: str,
    audit_ref: str,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_idempotency_claim(
                principal_id, surface, idempotency_digest, action,
                required_scopes_json, request_id, body_sha256, claim_ref,
                result_ref, claim_status, provisional_expires_at_ms,
                claimed_at_ms, updated_at_ms, expires_at_ms
            ) VALUES (?, 'scheduler.admin', ?, 'scheduler:admin',
                      '["scheduler:admin"]', ?, ?, ?, NULL,
                      'COMMITTED', NULL, ?, ?, ?)
            """,
            (
                principal_id,
                f"sha256:{_sha256('idem-' + request_id)}",
                request_id,
                body_sha256,
                claim_ref,
                NOW,
                NOW,
                NOW + 60_000,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_audit(
                audit_ref, request_id, correlation_id, surface, action, method,
                normalized_path, principal_id, tenant_id, key_id,
                credential_kind, credential_ref_digest, requested_scopes_json,
                required_scopes_json, body_sha256, idempotency_digest,
                nonce_digest, transport_key_digest, decision, reason_code,
                stage, claim_ref, rate_snapshot_json, side_effects_executed, ts_ms
            ) VALUES (?, ?, NULL, 'scheduler.admin', 'scheduler:admin', 'POST',
                      '/scheduler/trigger', ?, 'tenant-scheduler', 'key-scheduler',
                      'HMAC_SHA256', ?, '["scheduler:admin"]', '["scheduler:admin"]',
                      ?, ?, ?, ?, 'ALLOW', 'SECURE_INGRESS_ALLOWED', 'COMPLETE',
                      ?, '{}', 0, ?)
            """,
            (
                audit_ref,
                request_id,
                principal_id,
                f"sha256:{_sha256('credential-' + request_id)}",
                body_sha256,
                f"sha256:{_sha256('idem-' + request_id)}",
                f"sha256:{_sha256('nonce-' + request_id)}",
                f"sha256:{_sha256('transport-' + request_id)}",
                claim_ref,
                NOW,
            ),
        )


class _IntentEngine:
    def __init__(self, ledger: SQLiteLedger) -> None:
        self.ledger = ledger
        self.calls = 0

    def evaluate_intent(self, **kwargs):
        self.calls += 1
        intent = kwargs["raw_intent"]
        decision = {
            "request_id": kwargs["request_id"],
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "reason_code": None,
            "tokens": {},
            "ts_ms": intent["ts_ms"],
        }
        self.ledger.log_governor(decision)
        return decision


@pytest.mark.asyncio
async def test_scheduler_occurrence_uses_frozen_definition_real_handler_and_replays_once(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scheduler-runtime.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw02-scheduler-runtime",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()

    definitions = SchedulerDefinitionRepository(db_path)
    results = SchedulerExecutionResultRepository(db_path)
    revision_one = definitions.publish_definition(
        task_id="proactive_tick",
        definition=_definition(marker="revision-one"),
        expected_head_version=0,
        next_scheduled_at_ms=NOW + 60_000,
    )
    request_id = "request-scheduler-manual-one"
    body_sha = _sha256("scheduler-manual-body")
    claim_ref = "secure-ingress-claim://scheduler/manual-one"
    audit_ref = "audit://secure-ingress/scheduler/manual-one"
    principal_id = "scheduler-service-principal"
    _insert_scheduler_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    occurrence_authority = {
        "allowed": True,
        "duplicate": False,
        "request_id": request_id,
        "principal_id": principal_id,
        "claim_ref": claim_ref,
        "audit_ref": audit_ref,
        "body_sha256": body_sha,
    }
    occurrence = definitions.admit_manual_occurrence(
        task_id="proactive_tick",
        definition_revision=revision_one["definition_revision"],
        request_id=request_id,
        scheduled_at_ms=NOW,
        payload={"reason": "unit-test"},
        authority=occurrence_authority,
    )
    # A newer head must not invalidate an occurrence already admitted on rev1.
    definitions.publish_definition(
        task_id="proactive_tick",
        definition=_definition(marker="revision-two"),
        expected_head_version=revision_one["head_version"],
        next_scheduled_at_ms=NOW + 120_000,
    )

    ledger = SQLiteLedger(str(db_path))
    claim_store = SQLiteSecureIngressStore(db_path)
    runtime = None
    try:
        task_store = DurableTaskStore(db_path)
        infrastructure = SimpleNamespace(
            task_store=task_store,
            command_repository=HostCommandRepository(db_path),
            outbox_publisher=RuntimeOutboxPublisher(
                task_store,
                RuntimeEventStore(db_path),
            ),
            scheduler_definition_repository=definitions,
        )
        handler_calls: list[tuple[str, int, str]] = []

        async def handler(*, occurrence, definition, **_):
            handler_calls.append(
                (
                    occurrence.occurrence_id,
                    definition.definition_revision,
                    definition.intent_template["args"]["marker"],
                )
            )
            return {"status": "OK", "summary": "real governed handler ran"}

        handlers = SchedulerWorkerRegistry()
        handlers.register("intent_dispatch", handler)
        runtime = build_governed_scheduler_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            result_repository=results,
            runtime_router=CanonicalRuntimeRouter(),
            worker_registry=handlers,
            owner_id="scheduler-owner",
        )
        await runtime.activate()
        engine = _IntentEngine(ledger)
        service = SchedulerCommandService(
            runtime=runtime,
            intent_engine=engine,
            claim_result_store=claim_store,
            definition_repository=definitions,
            policy_evaluator=SchedulerDefinitionPolicyEvaluator(
                current_mode=lambda: "normal"
            ),
            worker_registry=handlers,
        )
        authority = {
            "allowed": True,
            "duplicate": False,
            "request_id": request_id,
            "principal_id": principal_id,
            "claim_ref": claim_ref,
            "audit_ref": audit_ref,
            "body_sha256": body_sha,
        }
        first = await service.execute_occurrence(
            occurrence=occurrence,
            authority=authority,
            received_at_ms=NOW,
        )
        replay = await service.trigger_manual(
            task_id="proactive_tick",
            payload={"caller": "must-not-rebuild-current-revision"},
            authority={
                **authority,
                "allowed": False,
                "duplicate": True,
                "existing_result_ref": first["execution_receipt"]["receipt_ref"],
            },
            received_at_ms=NOW + 5_000,
        )

        assert first["decision"] == "APPROVE"
        assert first["execution_receipt"]["executor"] == "canonical"
        assert first["result"] == {"status": "SUCCEEDED", "summary": "real governed handler ran"}
        assert replay["replayed"] is True
        for key in ("occurrence_id", "task_id", "result", "receipt_ref", "narrative"):
            assert replay[key] == first[key]
        assert handler_calls == [(occurrence["occurrence_id"], 1, "revision-one")]
        assert engine.calls == 1
        durable = results.get_by_occurrence(occurrence["occurrence_id"])
        assert durable is not None
        assert durable["command_id"] == first["execution_receipt"]["command_id"]
        assert task_store.list_pending_outbox(limit=100) == []
        with sqlite3.connect(str(db_path)) as conn:
            claim_result = conn.execute(
                "SELECT result_ref FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
                (claim_ref,),
            ).fetchone()
        assert claim_result == (first["execution_receipt"]["receipt_ref"],)

        with pytest.raises(GovernedSchedulerRuntimeError) as authority_exc:
            await service.execute_occurrence(
                occurrence=occurrence,
                authority={**authority, "principal_id": "different-principal"},
                received_at_ms=NOW + 6_000,
            )
        assert authority_exc.value.reason_code == "SCHEDULER_RUNTIME_AUTHORITY_INVALID"

        # Durable policy blocks before Governor/handler.
        blocked_request = "request-scheduler-blocked"
        blocked_body = _sha256("scheduler-blocked-body")
        blocked_claim = "secure-ingress-claim://scheduler/blocked"
        blocked_audit = "audit://secure-ingress/scheduler/blocked"
        _insert_scheduler_authority(
            db_path,
            request_id=blocked_request,
            principal_id=principal_id,
            body_sha256=blocked_body,
            claim_ref=blocked_claim,
            audit_ref=blocked_audit,
        )
        blocked_head = definitions.publish_definition(
            task_id="blocked_tick",
            definition=_definition(marker="blocked"),
            expected_head_version=0,
            next_scheduled_at_ms=None,
        )
        blocked_occurrence = definitions.admit_manual_occurrence(
            task_id="blocked_tick",
            definition_revision=blocked_head["definition_revision"],
            request_id=blocked_request,
            scheduled_at_ms=NOW,
            payload={},
            authority={
                "allowed": True,
                "duplicate": False,
                "request_id": blocked_request,
                "principal_id": principal_id,
                "claim_ref": blocked_claim,
                "audit_ref": blocked_audit,
                "body_sha256": blocked_body,
            },
        )
        blocked_service = SchedulerCommandService(
            runtime=runtime,
            intent_engine=engine,
            claim_result_store=claim_store,
            definition_repository=definitions,
            policy_evaluator=SchedulerDefinitionPolicyEvaluator(
                current_mode=lambda: "locked"
            ),
            worker_registry=handlers,
        )
        with pytest.raises(GovernedSchedulerRuntimeError) as exc_info:
            await blocked_service.execute_occurrence(
                occurrence=blocked_occurrence,
                authority={
                    "allowed": True,
                    "duplicate": False,
                    "request_id": blocked_request,
                    "principal_id": principal_id,
                    "claim_ref": blocked_claim,
                    "audit_ref": blocked_audit,
                    "body_sha256": blocked_body,
                },
                received_at_ms=NOW,
            )
        assert exc_info.value.reason_code == SCHEDULER_RUNTIME_MODE_BLOCKED
        assert engine.calls == 1
        assert len(handler_calls) == 1
    finally:
        if runtime is not None:
            await runtime.aclose()
        claim_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_duplicate_admin_publish_fails_closed_without_mutating_new_head() -> None:
    class Definitions:
        publish_calls = 0

        def publish_definition(self, **_):
            self.publish_calls += 1
            raise AssertionError("duplicate admin request must not write")

    definitions = Definitions()
    workers = SchedulerWorkerRegistry()
    workers.register("intent_dispatch", lambda **_: {"status": "SUCCEEDED"})
    service = SchedulerCommandService(
        runtime=SimpleNamespace(health=lambda: {"active": True}),
        intent_engine=SimpleNamespace(evaluate_intent=lambda **_: {}),
        claim_result_store=SimpleNamespace(complete_claim=lambda **_: None),
        definition_repository=definitions,
        policy_evaluator=SchedulerDefinitionPolicyEvaluator(current_mode=lambda: "normal"),
        worker_registry=workers,
    )
    with pytest.raises(GovernedSchedulerRuntimeError) as exc_info:
        await service.publish_definition(
            task_id="proactive_tick",
            definition=_definition(marker="stale-duplicate"),
            authority={
                "allowed": False,
                "duplicate": True,
                "request_id": "request-admin-replay",
                "principal_id": "scheduler-admin",
                "claim_ref": "secure-ingress-claim://scheduler/admin-replay",
                "audit_ref": "audit://secure-ingress/scheduler/admin-replay",
                "body_sha256": _sha256("admin-replay"),
                "existing_result_ref": "scheduler-definition://proactive_tick/1",
            },
            expected_head_version=9,
            next_scheduled_at_ms=None,
            received_at_ms=NOW,
        )
    assert exc_info.value.reason_code == SCHEDULER_COMMAND_REPLAY
    assert definitions.publish_calls == 0


@pytest.mark.asyncio
async def test_unregistered_worker_is_rejected_before_publish_or_admission() -> None:
    class Definitions:
        publish_calls = 0

        def publish_definition(self, **_):
            self.publish_calls += 1
            raise AssertionError("unregistered worker must not reach durable publish")

    definitions = Definitions()
    workers = SchedulerWorkerRegistry()
    workers.register("intent_dispatch", lambda **_: {"status": "SUCCEEDED"})
    service = SchedulerCommandService(
        runtime=SimpleNamespace(health=lambda: {"active": True}),
        intent_engine=SimpleNamespace(evaluate_intent=lambda **_: {}),
        claim_result_store=SimpleNamespace(complete_claim=lambda **_: None),
        definition_repository=definitions,
        policy_evaluator=SchedulerDefinitionPolicyEvaluator(current_mode=lambda: "normal"),
        worker_registry=workers,
    )
    unknown = _definition(worker_key="unregistered_worker")
    snapshot = {
        "task_id": "unknown_worker_tick",
        "definition_revision": 1,
        "head_version": 1,
        "enabled": True,
        "next_scheduled_at_ms": NOW,
        "definition": {**unknown, "task_id": "unknown_worker_tick"},
    }

    with pytest.raises(GovernedSchedulerRuntimeError) as preflight_exc:
        service.preflight_definition(snapshot)
    assert preflight_exc.value.reason_code == "SCHEDULER_DEFINITION_INVALID"

    with pytest.raises(GovernedSchedulerRuntimeError) as publish_exc:
        await service.publish_definition(
            task_id="unknown_worker_tick",
            definition=unknown,
            authority={
                "allowed": True,
                "duplicate": False,
                "request_id": "request-unknown-worker",
                "principal_id": "scheduler-admin",
                "claim_ref": "secure-ingress-claim://scheduler/unknown-worker",
                "audit_ref": "audit://secure-ingress/scheduler/unknown-worker",
                "body_sha256": _sha256("unknown-worker"),
            },
            expected_head_version=0,
            next_scheduled_at_ms=NOW,
            received_at_ms=NOW,
        )
    assert publish_exc.value.reason_code == "SCHEDULER_DEFINITION_INVALID"
    assert definitions.publish_calls == 0


def test_scheduler_contract_rejects_raw_secrets_but_allows_budget_tokens() -> None:
    safe = validate_definition_payload(
        task_id="safe_task",
        definition={
            **_definition(),
            "resource_profile": {"token_budget": 2_000, "credential_ref": "vault://worker/key"},
        },
    )
    assert safe["resource_profile"]["token_budget"] == 2_000
    with pytest.raises(SchedulerContractError):
        validate_definition_payload(
            task_id="unsafe_task",
            definition={**_definition(), "intent_template": {"api_key": "raw-secret"}},
        )


def test_executor_assignment_selects_only_canonical_scheduler_worker() -> None:
    repository = SimpleNamespace()
    canonical = GovernedSchedulerWorker(
        handlers=SchedulerWorkerRegistry(default_handler=lambda **_: {"status": "OK"}),
        definition_repository=repository,
        occurrence_repository=repository,
        result_repository=repository,
    )
    resolver = SchedulerWorkerResolver(canonical_worker=canonical)
    assert resolver.resolve(executor="canonical", worker_route="scheduler_production") is canonical
    with pytest.raises(SchedulerWorkerError):
        resolver.resolve(executor="legacy", worker_route="scheduler_production")

    strict = SchedulerWorkerRegistry(default_handler=lambda **_: {"status": "OK"})
    assert callable(strict.resolve(worker_key="scheduler_production", task_id="dynamic"))
    with pytest.raises(SchedulerWorkerError) as exc_info:
        strict.resolve(worker_key="unknown_worker", task_id="dynamic")
    assert exc_info.value.reason_code == SCHEDULER_HANDLER_NOT_REGISTERED


@pytest.mark.asyncio
async def test_executor_assignment_invokes_canonical_real_handler() -> None:
    calls: list[str] = []
    authority = {
        "request_id": "request-worker-selection",
        "principal_id": "scheduler-worker-principal",
        "ingress_claim_ref": "secure-ingress-claim://scheduler/worker-selection",
        "ingress_audit_ref": "audit://secure-ingress/scheduler/worker-selection",
        "body_sha256": "sha256:" + "a" * 64,
    }
    occurrence = {
        "occurrence_id": "scheduleroccurrence://sha256/" + "b" * 64,
        "task_id": "worker_selection",
        "definition_revision": 1,
        "source": "MANUAL",
        "scheduled_at_ms": NOW,
        "payload": {},
        **authority,
    }
    class Repository:
        worker_key = "intent_dispatch"

        def get_occurrence(self, occurrence_id):
            return occurrence

        def get_revision(self, task_id, revision):
            return {
                "task_id": "worker_selection",
                "definition_revision": 1,
                "definition": {
                    **_definition(worker_key=self.worker_key),
                    "task_id": "worker_selection",
                },
            }

        def append(self, *, occurrence_id, command_id, work_order_id, result):
            return {
                "receipt_ref": "schedulerresult://sha256/" + hashlib.sha256(
                    command_id.encode()
                ).hexdigest(),
                "occurrence_id": occurrence_id,
                "command_id": command_id,
                "work_order_id": work_order_id,
                "result_status": result["status"],
                "result": dict(result),
                "result_digest": "sha256:" + "c" * 64,
            }

    repository = Repository()

    def handler(name):
        async def run(**_):
            calls.append(name)
            return {"status": "SUCCEEDED", "summary": name}

        return run

    canonical_registry = SchedulerWorkerRegistry()
    canonical_registry.register("intent_dispatch", handler("canonical"))
    canonical = GovernedSchedulerWorker(
        handlers=canonical_registry,
        definition_repository=repository,
        occurrence_repository=repository,
        result_repository=repository,
    )
    resolver = SchedulerWorkerResolver(canonical_worker=canonical)
    command = SimpleNamespace(
        input_ref=occurrence["occurrence_id"],
        request_id=authority["request_id"],
        target_id="intent_dispatch",
        command_id="host-command://scheduler/" + "d" * 64,
    )
    work_order = SimpleNamespace(
        work_order_id=occurrence_work_order_id(occurrence["occurrence_id"]),
        input_pack={"occurrence_id": occurrence["occurrence_id"]},
    )
    context = {"probe": True}

    worker = resolver.resolve(executor="canonical", worker_route="scheduler_production")
    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=work_order,
            execution_context=context,
        )
    ]
    assert events[-1]["result"]["summary"] == "canonical"
    assert calls == ["canonical"]

    repository.worker_key = "unknown_worker"
    command.target_id = "unknown_worker"
    failed = [
        event
        async for event in canonical.execute_stream(
            command=command,
            work_order=work_order,
            execution_context=context,
        )
    ]
    assert failed[-1]["decision"] == "REJECT"
    assert failed[-1]["result"]["status"] == "FAILED"
    assert failed[-1]["result"]["reason_code"] == SCHEDULER_HANDLER_NOT_REGISTERED
    assert calls == ["canonical"]
