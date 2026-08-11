from __future__ import annotations

import hashlib
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.contracts.agent_runtime.budget_policy import BudgetExceededAction
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.runtime_events import RuntimeEventStore, RuntimeOutboxPublisher
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.internal_agent_checkpoint_uow import (
    DurableInternalAgentCheckpointUnitOfWork,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.review_continuation_store import ReviewContinuationStore
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
)
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.runtime.context_application import FrozenRuntimeContextV1
from host_brain.runtime.context_contracts import WorldSnapshotV2
from host_brain.scheduler.accepted_operation import SchedulerAcceptedOperationHandler
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
    SchedulerWorkerError,
    SchedulerWorkerRegistry,
    SchedulerWorkerResolver,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from host_brain.task_runtime.errors import (
    GOVERNED_EXECUTION_IN_PROGRESS,
    GovernedRuntimeError,
)


NOW = 1_800_000_000_000


class _RuntimeContextAuthority:
    def __init__(self) -> None:
        workspace_id = "tenant:scheduler-runtime-unit"
        subject_id = "scheduler-runtime-unit"
        world = WorldSnapshotV2(
            workspace_id=workspace_id,
            subject_id=subject_id,
            revision=1,
            facts=(),
        )
        self._payload = {
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "runtime_context_observed_at_ms": NOW,
            "runtime_context_principal_id": "scheduler-service-principal",
            "workspace_id": workspace_id,
            "subject_id": subject_id,
            "world_snapshot_v2": world.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": workspace_id,
                "subject_id": subject_id,
                "scene_id": "scene.scheduler",
                "revision": 1,
                "decision_digest": hashlib.sha256(
                    b"scheduler-runtime-unit-scene"
                ).hexdigest(),
            },
        }

    def freeze_request(self, **_kwargs: Any) -> FrozenRuntimeContextV1:
        return FrozenRuntimeContextV1(payload=deepcopy(self._payload))

    def require_frozen(self, value: Any) -> FrozenRuntimeContextV1:
        source = dict(value or {})
        if any(source.get(key) != expected for key, expected in self._payload.items()):
            raise ValueError("Scheduler Runtime Context drifted")
        return FrozenRuntimeContextV1(payload=deepcopy(self._payload))


class _ExactPreviewAuthority:
    def __init__(self) -> None:
        self._records: dict[str, tuple[dict[str, Any], SimpleNamespace]] = {}
        self.reject_task_node = False

    def issue_receipt(self, **kwargs: Any) -> dict[str, Any]:
        index = len(self._records) + 1
        event_id = f"runtimeevent://scheduler-runtime-unit/{index}"
        receipt_ref = f"action-preview://scheduler-runtime-unit/{index}"
        authority = dict(kwargs.get("authority_context") or {})
        authority_kind = (
            "ACCEPTANCE_EFFECT"
            if authority.get("schema_version")
            == "Natsume.action_preview.acceptance_effect_authority_request.v1"
            else "TASK_NODE"
        )
        if self.reject_task_node and authority_kind == "TASK_NODE":
            raise RuntimeError("injected pre-effect TaskNode Preview failure")
        boundary = {
            key: deepcopy(value)
            for key, value in kwargs.items()
            if key != "guardian_capability_id"
        }
        receipt = SimpleNamespace(
            receipt_ref=receipt_ref,
            payload={"authority_kind": authority_kind},
        )
        self._records[event_id] = (boundary, receipt)
        return {
            "event_id": event_id,
            "receipt_ref": receipt_ref,
            "receipt": {"authority_kind": authority_kind},
        }

    def require_execution(self, *, event_id: str, **kwargs: Any) -> Any:
        boundary, receipt = self._records[event_id]
        assert kwargs == boundary
        return receipt


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
    def __init__(self, ledger: SQLiteLedger, token_service: TokenService) -> None:
        self.ledger = ledger
        self.token_service = token_service
        self.calls = 0

    def evaluate_intent(self, **kwargs):
        self.calls += 1
        intent = kwargs["raw_intent"]
        token_ref = self.token_service.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=intent["ts_ms"],
        )
        binding = build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref=token_ref,
            risk_level=1,
            issued_at_ms=intent["ts_ms"],
            expires_at_ms=intent["ts_ms"] + 120_000,
        )
        decision = {
            "request_id": kwargs["request_id"],
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "reason_code": None,
            "tokens": {"approval_token_ref": token_ref},
            "approved_action_binding": binding,
            "snapshot_refs": {},
            "stage": "S_INTENT_OK",
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
    revision_two = definitions.publish_definition(
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
        worker_projection_store = WorkerProjectionStore(
            db_path,
            clock=lambda: NOW,
        )
        infrastructure = SimpleNamespace(
            task_store=task_store,
            internal_agent_checkpoint_uow=DurableInternalAgentCheckpointUnitOfWork(
                task_store=task_store,
                worker_projection_store=worker_projection_store,
            ),
            review_store=ReviewStore(db_path, clock=lambda: NOW),
            review_continuation_store=ReviewContinuationStore(
                db_path,
                clock=lambda: NOW,
            ),
            command_repository=HostCommandRepository(db_path),
            outbox_publisher=RuntimeOutboxPublisher(
                task_store,
                RuntimeEventStore(db_path),
            ),
            scheduler_definition_repository=definitions,
            worker_projection_store=worker_projection_store,
        )
        handler_calls: list[tuple[str, int, str]] = []
        handler_contexts: list[dict] = []

        async def handler(*, occurrence, definition, execution_context, **_):
            handler_calls.append(
                (
                    occurrence.occurrence_id,
                    definition.definition_revision,
                    definition.intent_template["args"]["marker"],
                )
            )
            handler_contexts.append(dict(execution_context))
            return {"status": "OK", "summary": "real governed handler ran"}

        handlers = SchedulerWorkerRegistry()
        handlers.register("intent_dispatch", handler)
        token_service = TokenService(approval_ttl_ms=180_000)
        runtime_context = _RuntimeContextAuthority()
        preview_authority = _ExactPreviewAuthority()
        accepted_operation = SchedulerAcceptedOperationHandler(
            worker_registry=handlers,
            definition_repository=definitions,
            occurrence_repository=definitions,
            command_repository=infrastructure.command_repository,
            result_repository=results,
        )
        dispatcher = AcceptanceEffectDispatcher(
            task_store=task_store,
            handlers=build_production_acceptance_effect_handlers(
                scheduler_operation_handler=accepted_operation,
            ),
            owner_id="scheduler-unit-acceptance-effect-owner",
            action_preview_service=preview_authority,
        )
        runtime = build_governed_scheduler_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=results,
            runtime_router=CanonicalRuntimeRouter(),
            worker_registry=handlers,
            owner_id="scheduler-owner",
            runtime_context_application=runtime_context,
            action_preview_service=preview_authority,
            acceptance_effect_dispatcher=dispatcher,
            # The test admits several parent decisions at NOW..NOW+6s. Keep
            # the child-grant clock after every parent issuance while still
            # remaining inside every frozen expiry window.
            clock=lambda: NOW + 10_000,
        )
        await runtime.activate()
        engine = _IntentEngine(ledger, token_service)
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
                "existing_result_ref": first["scheduler_result_receipt_ref"],
            },
            received_at_ms=NOW + 5_000,
        )

        assert first["decision"] == "APPROVE"
        assert first["execution_receipt"]["executor"] == "canonical"
        assert first["execution_receipt"]["worker_route"] == "delegate:intent_dispatch"
        assert first["execution_receipt"]["status"] == "ACCEPTED"
        assert first["execution_receipt"]["verification_status"] == "PASS"
        assert first["execution_receipt"]["accepted"] is True
        assert first["result"] == {"status": "SUCCEEDED", "summary": "real governed handler ran"}
        assert replay["execution_receipt"]["replayed"] is True
        for key in ("occurrence_id", "task_id", "result", "receipt_ref", "narrative"):
            assert replay[key] == first[key]
        assert handler_calls == [(occurrence["occurrence_id"], 1, "revision-one")]
        assert len(handler_contexts) == 1
        assert engine.calls == 1
        durable = results.get_by_occurrence(occurrence["occurrence_id"])
        assert durable is not None
        assert durable["command_id"] == first["execution_receipt"]["command_id"]
        admitted = infrastructure.command_repository.get(durable["command_id"])
        assert admitted is not None
        assert isinstance(admitted["work_order"], WorkOrderV3)
        assert admitted["work_order"].budget_policy is not None
        assert (
            admitted["work_order"].budget_policy.on_exceeded
            is BudgetExceededAction.BLOCK
        )
        assert all(
            value > 0
            for value in admitted["work_order"].budget_policy.limits.to_dict().values()
        )
        assert admitted["graph"].nodes[0].route == "delegate:intent_dispatch"
        private_preview = handler_contexts[0]["_action_preview_context"]
        assert private_preview["origin_authority_kind"] == "ACCEPTANCE_EFFECT"
        assert (
            private_preview["origin_boundary"]["parent_governor_grant_ref"]
            == admitted["command"].governor_grant_ref
        )
        snapshot = task_store.read_scheduler_snapshot(
            admitted["command"].graph_id,
            admitted["command"].graph_revision,
        )
        assert snapshot["graph_record"]["status"] == "COMPLETED"
        assert snapshot["graph_record"]["nodes"][0]["status"] == "ACCEPTED"
        assert len(snapshot["attempts"]) == 1
        assert snapshot["budgets"][0]["status"] == "CONSUMED"
        child_grants = [
            row
            for row in ledger.trace_request(request_id)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ]
        assert len(child_grants) == 1
        assert child_grants[0]["snapshot_refs"]["node_grant"]["route"] == "delegate"
        assert (
            child_grants[0]["snapshot_refs"]["node_grant"]["target_id"]
            == "intent_dispatch"
        )
        assert task_store.list_pending_outbox(limit=100) == []
        with sqlite3.connect(str(db_path)) as conn:
            claim_result = conn.execute(
                "SELECT result_ref FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
                (claim_ref,),
            ).fetchone()
        assert claim_result == (first["scheduler_result_receipt_ref"],)

        # A TaskGraph may stop before Acceptance has created a Scheduler
        # Effect.  Its Task receipt is not a Scheduler business result: keep
        # the native claim result NULL and reopen the immutable HostCommand on
        # duplicate delivery without touching the real handler.
        failed_request = "request-scheduler-pre-effect-failed"
        failed_body = _sha256("scheduler-pre-effect-failed-body")
        failed_claim = "secure-ingress-claim://scheduler/pre-effect-failed"
        failed_audit = "audit://secure-ingress/scheduler/pre-effect-failed"
        _insert_scheduler_authority(
            db_path,
            request_id=failed_request,
            principal_id=principal_id,
            body_sha256=failed_body,
            claim_ref=failed_claim,
            audit_ref=failed_audit,
        )
        failed_authority = {
            "allowed": True,
            "duplicate": False,
            "request_id": failed_request,
            "principal_id": principal_id,
            "claim_ref": failed_claim,
            "audit_ref": failed_audit,
            "body_sha256": failed_body,
        }
        failed_occurrence = definitions.admit_manual_occurrence(
            task_id="proactive_tick",
            definition_revision=revision_two["definition_revision"],
            request_id=failed_request,
            scheduled_at_ms=NOW + 1,
            payload={"reason": "pre-effect-failure"},
            authority=failed_authority,
        )
        preview_authority.reject_task_node = True
        failed = await service.execute_occurrence(
            occurrence=failed_occurrence,
            authority=failed_authority,
            received_at_ms=NOW + 1,
        )
        with pytest.raises(GovernedRuntimeError) as replay_exc:
            await service.execute_occurrence(
                occurrence=failed_occurrence,
                authority={
                    **failed_authority,
                    "allowed": False,
                    "duplicate": True,
                    "existing_result_ref": None,
                },
                received_at_ms=NOW + 2,
            )
        assert failed["execution_receipt"]["accepted"] is False
        assert failed["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
        assert "scheduler_effect_status" not in failed
        assert replay_exc.value.reason_code == GOVERNED_EXECUTION_IN_PROGRESS
        assert len(handler_calls) == 1
        with sqlite3.connect(str(db_path)) as conn:
            failed_claim_result = conn.execute(
                "SELECT result_ref FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
                (failed_claim,),
            ).fetchone()
        assert failed_claim_result == (None,)
        failed_command = infrastructure.command_repository.get_by_request(
            surface="scheduler",
            request_id=failed_request,
        )
        assert failed_command is not None
        assert (
            infrastructure.command_repository.validate_ingress_claim_result(
                failed_command["command_id"]
            )["command_id"]
            == failed_command["command_id"]
        )
        preview_authority.reject_task_node = False

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
        assert engine.calls == 2
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


def test_scheduler_contract_rejects_legacy_retry_backoff_alias() -> None:
    with pytest.raises(SchedulerContractError) as caught:
        validate_definition_payload(
            task_id="legacy_retry_task",
            definition={
                **_definition(),
                "retry_policy": {"max_attempts": 2, "base_delay_ms": 10},
            },
        )

    assert "unsupported fields: base_delay_ms" in caught.value.detail


def test_executor_assignment_selects_only_canonical_scheduler_worker() -> None:
    class CanonicalPlanWorker:
        async def execute_stream(self, **_kwargs):
            yield {"phase": "COMMIT", "operation_planned": True}

    canonical = CanonicalPlanWorker()
    resolver = SchedulerWorkerResolver(canonical_worker=canonical)
    assert resolver.resolve(executor="canonical", worker_route="delegate:scheduler_production") is canonical
    with pytest.raises(SchedulerWorkerError):
        resolver.resolve(executor="legacy", worker_route="delegate:scheduler_production")
    with pytest.raises(TypeError, match="execute_stream"):
        SchedulerWorkerResolver(canonical_worker=SimpleNamespace())

    strict = SchedulerWorkerRegistry(default_handler=lambda **_: {"status": "OK"})
    assert callable(strict.resolve(worker_key="scheduler_production", task_id="dynamic"))
    with pytest.raises(SchedulerWorkerError) as exc_info:
        strict.resolve(worker_key="unknown_worker", task_id="dynamic")
    assert exc_info.value.reason_code == SCHEDULER_HANDLER_NOT_REGISTERED


@pytest.mark.asyncio
async def test_executor_assignment_cannot_reach_post_acceptance_handler() -> None:
    calls: list[str] = []

    def handler(name):
        async def run(**_):
            calls.append(name)
            return {"status": "SUCCEEDED", "summary": name}

        return run

    canonical_registry = SchedulerWorkerRegistry()
    canonical_registry.register("intent_dispatch", handler("canonical"))
    class CanonicalPlanWorker:
        async def execute_stream(self, **_kwargs):
            yield {
                "phase": "COMMIT",
                "decision": "APPROVE",
                "result": {"summary": "immutable Scheduler operation planned"},
            }

    canonical = CanonicalPlanWorker()
    resolver = SchedulerWorkerResolver(canonical_worker=canonical)
    command = SimpleNamespace(target_id="intent_dispatch")
    work_order = SimpleNamespace()
    context = {"worker_route": "delegate:intent_dispatch"}

    worker = resolver.resolve(executor="canonical", worker_route="delegate:intent_dispatch")
    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=work_order,
            execution_context=context,
        )
    ]
    assert events[-1]["result"]["summary"] == "immutable Scheduler operation planned"
    assert calls == []

    command.target_id = "unknown_worker"
    context["worker_route"] = "delegate:unknown_worker"
    second_plan = [
        event
        async for event in resolver.resolve(
            executor="canonical",
            worker_route="delegate:unknown_worker",
        ).execute_stream(
            command=command,
            work_order=work_order,
            execution_context=context,
        )
    ]
    assert second_plan[-1]["decision"] == "APPROVE"
    assert calls == []

    accepted_handler = canonical_registry.resolve(
        worker_key="intent_dispatch",
        task_id="worker_selection",
    )
    await accepted_handler()
    assert calls == ["canonical"]
