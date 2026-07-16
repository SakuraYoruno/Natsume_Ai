from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.contracts.agent_runtime.budget_policy import BudgetExceededAction
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.app.internal_task_runtime import (
    InternalAgentCommandService,
    build_governed_internal_runtime,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.runtime_events import RuntimeEventStore, RuntimeOutboxPublisher
from host_brain.persistence.agent_execution_repository import (
    AgentExecutionResultRepository,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.review_continuation_store import ReviewContinuationStore
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore


NOW = 1_800_000_000_000


def _sha256(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _insert_secure_authority(
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
            ) VALUES (?, 'internal.agent', ?, 'internal:agent:execute',
                      '["internal:agent:execute"]', ?, ?, ?, NULL,
                      'COMMITTED', NULL, ?, ?, ?)
            """,
            (
                principal_id,
                f"sha256:{_sha256('internal-idempotency:' + request_id)}",
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
            ) VALUES (?, ?, NULL, 'internal.agent', 'internal:agent:execute', 'POST',
                      '/internal/codex/work-orders/execute', ?, 'tenant-internal',
                      'key-internal', 'HMAC_SHA256', ?,
                      '["internal:agent:execute"]', '["internal:agent:execute"]',
                      ?, ?, ?, ?, 'ALLOW', 'SECURE_INGRESS_ALLOWED', 'COMPLETE',
                      ?, '{}', 0, ?)
            """,
            (
                audit_ref,
                request_id,
                principal_id,
                f"sha256:{_sha256('internal-credential:' + request_id)}",
                body_sha256,
                f"sha256:{_sha256('internal-idempotency:' + request_id)}",
                f"sha256:{_sha256('internal-nonce:' + request_id)}",
                f"sha256:{_sha256('internal-transport:' + request_id)}",
                claim_ref,
                NOW,
            ),
        )


class _Executor:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self.review_required = False

    def execute(self, order: AgentWorkOrder) -> dict:
        self._calls.append(order.work_order_id)
        return {
            "status": "EXECUTED",
            "work_order_id": order.work_order_id,
            "summary": "internal governed worker completed",
            "result_text": "durable internal result",
            "verification": {"status": "PASSED"},
            "acceptance": {
                "decision": "REVIEW" if self.review_required else "ACCEPT"
            },
            "risk_flags": [],
            "review_required": self.review_required,
            "metadata": {"executor_probe": "canonical"},
        }


class _IntentEngine:
    def __init__(self, ledger: SQLiteLedger, token_service: TokenService) -> None:
        self._ledger = ledger
        self._token_service = token_service
        self.calls = 0

    def evaluate_intent(self, **kwargs):
        self.calls += 1
        intent = kwargs["raw_intent"]
        token_ref = self._token_service.issue_approval_token(
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
        self._ledger.log_governor(decision)
        return decision


@pytest.mark.asyncio
async def test_internal_work_order_is_governed_durable_and_replay_safe(tmp_path: Path) -> None:
    db_path = tmp_path / "internal-runtime.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw02-internal-runtime",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    request_id = "request-internal-runtime"
    principal_id = "principal-internal-runtime"
    body_sha256 = _sha256("internal-request-body")
    claim_ref = "ingress-claim://internal/runtime"
    audit_ref = "audit://secure-ingress/internal/runtime"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )

    ledger = SQLiteLedger(str(db_path))
    runtime = None
    claim_store = SQLiteSecureIngressStore(db_path)
    try:
        task_store = DurableTaskStore(db_path)
        event_store = RuntimeEventStore(db_path)
        review_store = ReviewStore(db_path)
        infrastructure = SimpleNamespace(
            task_store=task_store,
            review_store=review_store,
            review_continuation_store=ReviewContinuationStore(db_path),
            command_repository=HostCommandRepository(db_path),
            outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
        )
        result_repository = AgentExecutionResultRepository(db_path)
        calls: list[str] = []
        executor = _Executor(calls)
        token_service = TokenService(approval_ttl_ms=180_000)
        runtime = build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="internal-owner",
            canonical_executor=executor,
        )
        await runtime.activate()
        intent_engine = _IntentEngine(ledger, token_service)
        service = InternalAgentCommandService(
            runtime=runtime,
            intent_engine=intent_engine,
            claim_result_store=claim_store,
        )
        work_order = AgentWorkOrder(
            work_order_id="work-order-internal-runtime",
            domain="code",
            run_mode="inspect",
            objective="inspect governed internal runtime",
            input_pack={"input_ref": "input://internal/runtime"},
            allowed_actions=[],
            forbidden_actions=["write_files"],
            acceptance_criteria=["durable result exists"],
            metadata={
                "runtime_assignment": {"executor": "legacy"},
                "confirmed_owner": "caller-controlled",
            },
        )
        authority = {
            "principal_id": principal_id,
            "claim_ref": claim_ref,
            "audit_ref": audit_ref,
            "body_sha256": body_sha256,
        }
        first = await service.execute(
            work_order=work_order,
            request_id=request_id,
            session_id="session-internal-runtime",
            ts_ms=NOW,
            command_authority=authority,
        )
        replay = await service.execute(
            work_order=work_order,
            request_id=request_id,
            session_id="session-internal-runtime",
            ts_ms=NOW + 5_000,
            command_authority=authority,
        )

        assert first["decision"] == "APPROVE"
        assert first["execution_receipt"]["executor"] == "canonical"
        assert first["execution_receipt"]["worker_route"] == "delegate:agent_runtime"
        assert first["execution_receipt"]["status"] == "ACCEPTED"
        assert first["execution_receipt"]["verification_status"] == "PASS"
        assert first["execution_receipt"]["accepted"] is True
        assert first["result"]["result_text"] == "durable internal result"
        assert replay["replayed"] is True
        assert replay["result"]["result_text"] == "durable internal result"
        assert calls == ["work-order-internal-runtime"]
        assert intent_engine.calls == 1
        command_id = first["execution_receipt"]["command_id"]
        durable = result_repository.get_by_command(command_id)
        assert durable is not None
        assert durable["result_status"] == "EXECUTED"
        admitted = HostCommandRepository(db_path).get(command_id)
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
        assert admitted["graph"].nodes[0].route == "delegate:agent_runtime"
        assert "runtime_assignment" not in admitted["work_order"].metadata
        assert "confirmed_owner" not in admitted["work_order"].metadata
        snapshot = task_store.read_scheduler_snapshot(
            admitted["command"].graph_id,
            admitted["command"].graph_revision,
        )
        assert snapshot["graph_record"]["status"] == "COMPLETED"
        assert snapshot["graph_record"]["nodes"][0]["status"] == "ACCEPTED"
        assert len(snapshot["attempts"]) == 1
        assert snapshot["budgets"][0]["status"] == "CONSUMED"
        assert snapshot["budgets"][0]["consumed"]["tool_call_count"] == 1
        child_grants = [
            row
            for row in ledger.trace_request(request_id)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ]
        assert len(child_grants) == 1
        child_binding = child_grants[0]["snapshot_refs"]["node_grant"]
        assert child_binding["route"] == "delegate"
        assert child_binding["target_id"] == "agent_runtime"
        assert (
            child_grants[0]["tokens"]["approved_action_binding"]["principal_id"]
            == principal_id
        )
        with sqlite3.connect(db_path) as conn:
            node_events = {
                row[0]
                for row in conn.execute(
                    "SELECT event_type FROM task_runtime_outbox "
                    "WHERE aggregate_type = 'TASK_NODE'"
                ).fetchall()
            }
        assert "TASK_NODE_VERIFIED" in node_events
        assert "TASK_NODE_ACCEPTED" in node_events
        assert task_store.list_pending_outbox(limit=100) == []
        with sqlite3.connect(str(db_path)) as conn:
            completed = conn.execute(
                """
                SELECT request_id, result_ref
                FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (claim_ref,),
            ).fetchone()
        assert completed == (request_id, first["execution_receipt"]["receipt_ref"])

        review_request_id = "request-internal-runtime-review"
        review_body_sha256 = _sha256("internal-review-request-body")
        review_claim_ref = "ingress-claim://internal/runtime-review"
        review_audit_ref = "audit://secure-ingress/internal/runtime-review"
        _insert_secure_authority(
            db_path,
            request_id=review_request_id,
            principal_id=principal_id,
            body_sha256=review_body_sha256,
            claim_ref=review_claim_ref,
            audit_ref=review_audit_ref,
        )
        executor.review_required = True
        review_order = AgentWorkOrder(
            work_order_id="work-order-internal-runtime-review",
            domain="code",
            run_mode="inspect",
            objective="require durable post-execution review",
            acceptance_criteria=["durable result exists"],
            review_policy={"review_required": False},
        )
        review_terminal = await service.execute(
            work_order=review_order,
            request_id=review_request_id,
            session_id="session-internal-runtime-review",
            ts_ms=NOW + 10_000,
            command_authority={
                "principal_id": principal_id,
                "claim_ref": review_claim_ref,
                "audit_ref": review_audit_ref,
                "body_sha256": review_body_sha256,
            },
        )
        review_receipt = review_terminal["execution_receipt"]
        assert review_receipt["status"] == "WAITING_ACCEPTANCE_REVIEW"
        assert review_receipt["verification_status"] == "PASS"
        assert review_receipt["accepted"] is False
        assert review_receipt["projection"]["review"]["resume_phase"] == (
            "POST_EXECUTION_ACCEPTANCE"
        )
        review_command = HostCommandRepository(db_path).get(
            review_receipt["command_id"]
        )
        assert review_command is not None
        review_node = task_store.get_node(
            review_command["command"].graph_id,
            review_command["command"].graph_revision,
            review_command["work_order"].task_node_id,
        )
        assert review_node["status"] == "WAITING_ACCEPTANCE_REVIEW"
        open_reviews = review_store.list_open_reviews(limit=10)
        assert len(open_reviews) == 1
        assert (
            open_reviews[0]["review"]["review_type"]
            == "POST_EXECUTION_ACCEPTANCE"
        )
        assert calls == [
            "work-order-internal-runtime",
            "work-order-internal-runtime-review",
        ]
        assert intent_engine.calls == 2

        pre_request_id = "request-internal-runtime-pre-review"
        pre_body_sha256 = _sha256("internal-pre-review-request-body")
        pre_claim_ref = "ingress-claim://internal/runtime-pre-review"
        pre_audit_ref = "audit://secure-ingress/internal/runtime-pre-review"
        _insert_secure_authority(
            db_path,
            request_id=pre_request_id,
            principal_id=principal_id,
            body_sha256=pre_body_sha256,
            claim_ref=pre_claim_ref,
            audit_ref=pre_audit_ref,
        )
        executor.review_required = False
        pre_terminal = await service.execute(
            work_order=AgentWorkOrder(
                work_order_id="work-order-internal-runtime-pre-review",
                domain="code",
                run_mode="inspect",
                objective="wait for durable pre-execution review",
                review_policy={"review_required": True},
            ),
            request_id=pre_request_id,
            session_id="session-internal-runtime-pre-review",
            ts_ms=NOW + 20_000,
            command_authority={
                "principal_id": principal_id,
                "claim_ref": pre_claim_ref,
                "audit_ref": pre_audit_ref,
                "body_sha256": pre_body_sha256,
            },
        )
        pre_receipt = pre_terminal["execution_receipt"]
        assert pre_receipt["status"] == "WAITING_APPROVAL"
        assert pre_receipt["verification_status"] == "NOT_RUN"
        assert pre_receipt["accepted"] is False
        assert pre_receipt["projection"]["review"]["resume_phase"] == (
            "PRE_EXECUTION"
        )
        assert calls == [
            "work-order-internal-runtime",
            "work-order-internal-runtime-review",
        ]
        assert intent_engine.calls == 3
        open_review_types = {
            row["review"]["review_type"]
            for row in review_store.list_open_reviews(limit=10)
        }
        assert open_review_types == {
            "PRE_EXECUTION_APPROVAL",
            "POST_EXECUTION_ACCEPTANCE",
        }
        with sqlite3.connect(str(db_path)) as conn:
            pre_claim_result = conn.execute(
                "SELECT result_ref FROM secure_ingress_idempotency_claim "
                "WHERE claim_ref = ?",
                (pre_claim_ref,),
            ).fetchone()
        assert pre_claim_result == (None,)
        assert task_store.list_pending_outbox(limit=100) == []
    finally:
        if runtime is not None:
            await runtime.aclose()
        claim_store.close()
        ledger.close()
