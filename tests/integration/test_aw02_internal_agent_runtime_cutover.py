from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.task_store import DurableTaskStore
from host_brain.agent_runtime.work_order import AgentWorkOrder
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
from host_brain.secure_ingress import SQLiteSecureIngressStore


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
                f"sha256:{_sha256('internal-idempotency')}",
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
                f"sha256:{_sha256('internal-credential')}",
                body_sha256,
                f"sha256:{_sha256('internal-idempotency')}",
                f"sha256:{_sha256('internal-nonce')}",
                f"sha256:{_sha256('internal-transport')}",
                claim_ref,
                NOW,
            ),
        )


class _Executor:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def execute(self, order: AgentWorkOrder) -> dict:
        self._calls.append(order.work_order_id)
        return {
            "status": "EXECUTED",
            "work_order_id": order.work_order_id,
            "summary": "internal governed worker completed",
            "result_text": "durable internal result",
            "verification": {"status": "PASSED"},
            "risk_flags": [],
            "review_required": False,
            "metadata": {"executor_probe": "canonical"},
        }


class _IntentEngine:
    def __init__(self, ledger: SQLiteLedger) -> None:
        self._ledger = ledger
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
        infrastructure = SimpleNamespace(
            task_store=task_store,
            command_repository=HostCommandRepository(db_path),
            outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
        )
        result_repository = AgentExecutionResultRepository(db_path)
        calls: list[str] = []
        executor = _Executor(calls)
        runtime = build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(
                mode="canonical",
                canary_percent=100,
                canonical_owner="internal-owner",
                confirmed_owner="internal-owner",
            ),
            owner_id="internal-owner",
            canonical_executor=executor,
            legacy_executor=_Executor([]),
        )
        await runtime.activate()
        intent_engine = _IntentEngine(ledger)
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
        assert "runtime_assignment" not in admitted["work_order"].metadata
        assert "confirmed_owner" not in admitted["work_order"].metadata
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
    finally:
        if runtime is not None:
            await runtime.aclose()
        claim_store.close()
        ledger.close()
