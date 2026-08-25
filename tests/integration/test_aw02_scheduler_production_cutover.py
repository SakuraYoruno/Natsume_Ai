from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from pathlib import Path
import sqlite3
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
from host_brain.composition.command_ingress import (
    build_process_command_ingress_container,
)
from host_brain.composition.scheduler import (
    SchedulerClockIdentityConfig,
    build_process_scheduler_container,
)
from host_brain.integrations.runtime_events import (
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.review_continuation_store import ReviewContinuationStore
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
    SchedulerRepositoryError,
)
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.runtime.context_application import FrozenRuntimeContextV1
from host_brain.contracts.runtime_context import WorldSnapshotV2
from host_brain.scheduler.accepted_operation import SchedulerAcceptedOperationHandler
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry
from host_brain.secure_ingress import (
    IngressSurface,
    SECURE_INGRESS_SURFACE_POLICIES,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)


NOW = 1_800_000_000_000
PRINCIPAL_ID = "service.scheduler.clock.integration"
TENANT_ID = "tenant.scheduler.integration"
KEY_ID = "key.scheduler.clock.integration"
SECRET = "scheduler-clock-integration-secret"


class _RuntimeContextAuthority:
    def __init__(self) -> None:
        workspace_id = "tenant:scheduler-integration"
        subject_id = "scheduler-clock-integration"
        world = WorldSnapshotV2(
            workspace_id=workspace_id,
            subject_id=subject_id,
            revision=1,
            facts=(),
        )
        self._payload = {
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "runtime_context_observed_at_ms": NOW,
            "runtime_context_principal_id": PRINCIPAL_ID,
            "workspace_id": workspace_id,
            "subject_id": subject_id,
            "world_snapshot_v2": world.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": workspace_id,
                "subject_id": subject_id,
                "scene_id": "scene.scheduler-clock",
                "revision": 1,
                "decision_digest": hashlib.sha256(
                    b"scheduler-clock-integration-scene"
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

    def issue_receipt(self, **kwargs: Any) -> dict[str, Any]:
        index = len(self._records) + 1
        event_id = f"runtimeevent://scheduler-cutover/{index}"
        receipt_ref = f"action-preview://scheduler-cutover/{index}"
        authority = dict(kwargs.get("authority_context") or {})
        authority_kind = (
            "ACCEPTANCE_EFFECT"
            if authority.get("schema_version")
            == "Natsume.action_preview.acceptance_effect_authority_request.v1"
            else "TASK_NODE"
        )
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

    @property
    def authority_kinds(self) -> tuple[str, ...]:
        return tuple(
            str(record.payload["authority_kind"])
            for _, record in self._records.values()
        )


def _register_clock_authority(db_path: Path) -> None:
    policy = SECURE_INGRESS_SURFACE_POLICIES[IngressSurface.SCHEDULER_ADMIN]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms,
                updated_at_ms
            ) VALUES (?, 'service', ?, 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                TENANT_ID,
                json.dumps(list(policy.required_scopes)),
                json.dumps([policy.audience]),
                json.dumps([policy.action]),
                NOW - 1_000,
                NOW + 60_000,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm, secret_ref,
                status, version, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?,
                      'ACTIVE', 1, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                KEY_ID,
                "test-secret-ref://scheduler-clock-integration",
                NOW - 1_000,
                NOW + 60_000,
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
async def test_clock_to_task_runtime_to_durable_result_is_one_real_chain(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "governed-scheduler.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw02-scheduler-production-cutover",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    _register_clock_authority(db_path)

    ledger = SQLiteLedger(str(db_path))
    command_ingress = build_process_command_ingress_container(
        canonical_db_path=db_path,
        mode="ENFORCE",
        hmac_secrets={KEY_ID: SECRET},
    )
    task_store = DurableTaskStore(db_path)
    event_store = RuntimeEventStore(db_path)
    definitions = SchedulerDefinitionRepository(db_path, clock=lambda: NOW)
    results = SchedulerExecutionResultRepository(db_path, clock=lambda: NOW)
    infrastructure = SimpleNamespace(
        task_store=task_store,
        review_store=ReviewStore(db_path, clock=lambda: NOW),
        review_continuation_store=ReviewContinuationStore(
            db_path,
            clock=lambda: NOW,
        ),
        command_repository=HostCommandRepository(db_path),
        outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
        scheduler_definition_repository=definitions,
        scheduler_result_repository=results,
        worker_projection_store=WorkerProjectionStore(db_path, clock=lambda: NOW),
        internal_agent_checkpoint_uow=None,
    )
    worker_calls: list[str] = []
    worker_contexts: list[dict] = []

    async def worker(**kwargs):
        claimed_effects = task_store.list_acceptance_effect_intents(
            status="CLAIMED",
            limit=10,
        )
        assert len(claimed_effects) == 1
        assert claimed_effects[0]["effect_type"] == "scheduler.operation.execute"
        assert worker_calls == []
        worker_calls.append(kwargs["occurrence"].occurrence_id)
        worker_contexts.append(dict(kwargs["execution_context"]))
        return {
            "status": "SUCCEEDED",
            "summary": "governed scheduler production leaf completed",
        }

    token_service = TokenService(approval_ttl_ms=180_000)
    intent_engine = _IntentEngine(ledger, token_service)
    workers = SchedulerWorkerRegistry()
    runtime_context = _RuntimeContextAuthority()
    preview_authority = _ExactPreviewAuthority()
    accepted_operation = SchedulerAcceptedOperationHandler(
        worker_registry=workers,
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
        owner_id="scheduler-integration-acceptance-effect-owner",
        action_preview_service=preview_authority,
    )
    scheduler = build_process_scheduler_container(
        task_runtime_container=infrastructure,
        ledger=ledger,
        token_service=token_service,
        evaluator=command_ingress.evaluator,
        claim_result_store=command_ingress.store,
        intent_engine=intent_engine,
        runtime_router=CanonicalRuntimeRouter(),
        owner_id="scheduler-integration-owner",
        current_mode=lambda: "normal",
        default_handler=worker,
        worker_registry=workers,
        runtime_context_application=runtime_context,
        action_preview_service=preview_authority,
        acceptance_effect_dispatcher=dispatcher,
        seed_definitions=(
            {
                "task_id": "scheduler.integration.task",
                "next_scheduled_at_ms": NOW,
                "definition": {
                    "task_id": "scheduler.integration.task",
                    "trigger_mode": "interval",
                    "trigger_config": {"seconds": 60},
                    "intent_template": {"skill_name": "integration", "args": {}},
                    "priority": "HIGH",
                    "enabled": True,
                    "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
                    "resource_profile": {},
                    "worker_key": "scheduler_production",
                },
            },
        ),
        clock_enabled=True,
        clock_identity=SchedulerClockIdentityConfig(
            principal_id=PRINCIPAL_ID,
            tenant_id=TENANT_ID,
            key_id=KEY_ID,
            secret=SECRET,
        ),
        now_ms=lambda: NOW,
    )
    try:
        await scheduler.runtime.activate()
        tick = await scheduler.clock.run_once()

        assert tick == {
            "batch_ref": tick["batch_ref"],
            "received_at_ms": NOW,
            "recovered": 0,
            "admitted": 1,
            "executed": 1,
            "deferred": 0,
            "failed": 0,
            "failures": [],
        }
        assert tick["batch_ref"].startswith(
            "scheduler-clock-batch://sha256/"
        )
        assert len(worker_calls) == 1
        assert scheduler.worker_registry is workers
        assert intent_engine.calls == 1
        occurrence = definitions.list_pending_occurrences(limit=10)
        assert occurrence == []
        durable = results.get_by_occurrence(worker_calls[0])
        assert durable is not None
        assert durable["result_status"] == "SUCCEEDED"
        command = infrastructure.command_repository.get_by_request(
            surface="scheduler",
            request_id=(
                definitions.get_occurrence(worker_calls[0])["request_id"]
            ),
        )
        assert command is not None
        assert command["command"].surface.value == "scheduler"
        assert isinstance(command["work_order"], WorkOrderV3)
        assert command["work_order"].budget_policy is not None
        assert (
            command["work_order"].budget_policy.on_exceeded
            is BudgetExceededAction.BLOCK
        )
        assert all(
            value > 0
            for value in command["work_order"].budget_policy.limits.to_dict().values()
        )
        assert command["graph"].nodes[0].route == "delegate:scheduler_production"
        assert len(worker_contexts) == 1
        assert "verified_node_grant" not in worker_contexts[0]
        private_preview = worker_contexts[0]["_action_preview_context"]
        assert private_preview["schema_version"] == (
            "Natsume.scheduler.action_preview_context.v2"
        )
        assert private_preview["origin_authority_kind"] == "ACCEPTANCE_EFFECT"
        assert private_preview["origin_boundary"][
            "parent_governor_grant_ref"
        ] == command["command"].governor_grant_ref
        assert preview_authority.authority_kinds == (
            "TASK_NODE",
            "ACCEPTANCE_EFFECT",
        )
        snapshot = task_store.read_scheduler_snapshot(
            command["command"].graph_id,
            command["command"].graph_revision,
        )
        assert snapshot["graph_record"]["status"] == "COMPLETED"
        assert snapshot["graph_record"]["nodes"][0]["status"] == "ACCEPTED"
        assert len(snapshot["attempts"]) == 1
        assert snapshot["budgets"][0]["status"] == "CONSUMED"
        assert snapshot["budgets"][0]["consumed"]["tool_call_count"] == 1
        effects = task_store.list_acceptance_effect_intents(
            graph_id=command["command"].graph_id,
            graph_revision=command["command"].graph_revision,
            limit=10,
        )
        assert len(effects) == 1
        assert effects[0]["effect_type"] == "scheduler.operation.execute"
        assert effects[0]["status"] == "COMMITTED"
        assert effects[0]["receipt_ref"] == durable["receipt_ref"]

        replay = results.append(
            occurrence_id=durable["occurrence_id"],
            command_id=durable["command_id"],
            work_order_id=durable["work_order_id"],
            result=durable["result"],
        )
        assert replay["replayed"] is True
        assert replay["receipt_ref"] == durable["receipt_ref"]
        with pytest.raises(SchedulerRepositoryError):
            results.append(
                occurrence_id=durable["occurrence_id"],
                command_id=durable["command_id"],
                work_order_id=durable["work_order_id"],
                result={
                    **durable["result"],
                    "summary": "forged replacement result",
                },
            )
        with sqlite3.connect(db_path) as conn:
            projection_rows = conn.execute(
                "SELECT projection_json FROM task_runtime_worker_projection"
            ).fetchall()
            claim_row = conn.execute(
                "SELECT claim_status, result_ref "
                "FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
                (
                    definitions.get_occurrence(worker_calls[0])[
                        "ingress_claim_ref"
                    ],
                ),
            ).fetchone()
            node_events = {
                row[0]
                for row in conn.execute(
                    "SELECT event_type FROM task_runtime_outbox "
                    "WHERE aggregate_type = 'TASK_NODE'"
                ).fetchall()
            }
        assert len(projection_rows) == 1
        plan_projection = json.loads(projection_rows[0][0])
        assert plan_projection["decision"] == "APPROVE"
        assert plan_projection["acceptance_effect_intents"][0][
            "effect_type"
        ] == "scheduler.operation.execute"
        assert claim_row == ("COMMITTED", durable["receipt_ref"])
        assert "TASK_NODE_VERIFIED" in node_events
        assert "TASK_NODE_ACCEPTED" in node_events
        assert task_store.list_pending_outbox(limit=100) == []
    finally:
        await scheduler.runtime.aclose()
        await command_ingress.aclose()
        ledger.close()
