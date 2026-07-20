from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Mapping

import pytest

from host_brain.app.internal_task_runtime import (
    AgentWorkOrder,
    InternalAgentCommandService,
)
from host_brain.scheduler.contracts import (
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    SchedulerOccurrenceSource,
)
from host_brain.scheduler.governed_runtime import (
    SchedulerCommandService,
    SchedulerDefinitionPolicyEvaluator,
)
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from natsume_contracts.task import HostCommandSurface
from tests.contract.test_aw03_host_command_repository import NOW, _admission
from tests.integration.test_ne01_review_denied_ingress_replay import (
    _insert_secure_authority,
    _migrated_db,
)


class _UnusedIntentEngine:
    def evaluate_intent(self, **_: Any) -> Mapping[str, Any]:
        raise AssertionError("durable replay must not evaluate a new Governor intent")


class _WaitingRuntime:
    def __init__(self, *, command: Any, status: str) -> None:
        self._command = command
        self._status = status

    def existing_command(
        self,
        *,
        request_id: str,
        command_surface: str | None = None,
    ) -> Mapping[str, Any]:
        assert request_id == self._command.request_id
        if command_surface is not None:
            assert command_surface == self._command.surface.value
        return {"command": self._command}

    async def execute_stream(self, **_: Any):
        yield {
            "done": True,
            "execution_receipt": {
                "status": self._status,
                "accepted": False,
                # A waiting checkpoint is nonterminal even when a worker-side
                # receipt was already frozen before POST Review.
                "receipt_ref": "worker-receipt://before-review-decision",
                "projection": {
                    "review": {
                        "review_id": f"review-{self._status.lower()}",
                    }
                },
            },
        }

    def health(self) -> Mapping[str, Any]:
        return {"active": True, "accepting": True, "in_flight": 0}


class _Definitions:
    def __init__(
        self,
        *,
        occurrence: SchedulerOccurrence,
        definition: SchedulerDefinitionSnapshot,
    ) -> None:
        self._occurrence = occurrence
        self._definition = definition

    def get_occurrence(self, occurrence_id: str) -> Mapping[str, Any]:
        assert occurrence_id == self._occurrence.occurrence_id
        return self._occurrence.to_dict()

    def get_terminal_outcome(self, occurrence_id: str) -> None:
        assert occurrence_id == self._occurrence.occurrence_id
        return None

    def get_revision(self, task_id: str, revision: int) -> Mapping[str, Any]:
        assert (task_id, revision) == (
            self._definition.task_id,
            self._definition.definition_revision,
        )
        return self._definition.to_dict()


async def _run_internal_waiting(
    tmp_path: Path,
    status: str,
) -> tuple[dict[str, Any], Path, str]:
    request_id = f"request-internal-{status.lower()}"
    claim_ref = f"ingress-claim://internal/{status.lower()}"
    audit_ref = f"ingress-audit://internal/{status.lower()}"
    command = _admission(
        surface=HostCommandSurface.INTERNAL,
        request_id=request_id,
        ingress_claim_ref=claim_ref,
        ingress_audit_ref=audit_ref,
    )[0]
    db_path = _migrated_db(tmp_path)
    _insert_secure_authority(
        db_path,
        surface=HostCommandSurface.INTERNAL,
        request_id=request_id,
        principal_id=command.principal_id,
        body_sha256=command.input_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    claims = SQLiteSecureIngressStore(db_path)
    try:
        service = InternalAgentCommandService(
            runtime=_WaitingRuntime(command=command, status=status),  # type: ignore[arg-type]
            intent_engine=_UnusedIntentEngine(),
            claim_result_store=claims,
        )
        result = await service.execute(
            work_order=AgentWorkOrder(
                work_order_id=f"work-{status.lower()}",
                domain="code",
                run_mode="inspect",
                objective="wait for governed Review",
            ),
            request_id=request_id,
            session_id="session-review-ingress",
            ts_ms=NOW,
            command_authority={},
        )
    finally:
        claims.close()
    return result, db_path, claim_ref


async def _run_scheduler_waiting(
    tmp_path: Path,
    status: str,
) -> tuple[dict[str, Any], Path, str]:
    request_id = f"request-scheduler-{status.lower()}"
    claim_ref = f"ingress-claim://scheduler/{status.lower()}"
    audit_ref = f"ingress-audit://scheduler/{status.lower()}"
    body_sha256 = "sha256:" + "a" * 64
    command = _admission(
        surface=HostCommandSurface.SCHEDULER,
        request_id=request_id,
        ingress_claim_ref=claim_ref,
        ingress_audit_ref=audit_ref,
    )[0]
    occurrence = SchedulerOccurrence(
        occurrence_id=f"scheduler-occurrence://{status.lower()}",
        task_id="review_ingress_regression",
        definition_revision=1,
        source=SchedulerOccurrenceSource.MANUAL,
        scheduled_at_ms=NOW,
        payload={},
        request_id=request_id,
        principal_id="scheduler-review-principal",
        claim_ref=claim_ref,
        audit_ref=audit_ref,
        body_sha256=body_sha256,
    )
    definition = SchedulerDefinitionSnapshot(
        task_id=occurrence.task_id,
        definition_revision=1,
        head_version=1,
        enabled=True,
        definition={},
    )
    db_path = _migrated_db(tmp_path)
    _insert_secure_authority(
        db_path,
        surface=HostCommandSurface.SCHEDULER,
        request_id=request_id,
        principal_id=occurrence.principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    claims = SQLiteSecureIngressStore(db_path)
    try:
        service = SchedulerCommandService(
            runtime=_WaitingRuntime(command=command, status=status),  # type: ignore[arg-type]
            intent_engine=_UnusedIntentEngine(),
            claim_result_store=claims,
            definition_repository=_Definitions(
                occurrence=occurrence,
                definition=definition,
            ),  # type: ignore[arg-type]
            policy_evaluator=SchedulerDefinitionPolicyEvaluator(
                current_mode=lambda: "normal"
            ),
            worker_registry=SchedulerWorkerRegistry(),
        )
        result = await service.execute_occurrence(
            occurrence=occurrence,
            authority={
                "allowed": True,
                "duplicate": False,
                "request_id": request_id,
                "principal_id": occurrence.principal_id,
                "claim_ref": claim_ref,
                "audit_ref": audit_ref,
                "body_sha256": body_sha256,
            },
            received_at_ms=NOW,
        )
    finally:
        claims.close()
    return result, db_path, claim_ref


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "status"),
    [
        (surface, status)
        for surface in (HostCommandSurface.INTERNAL, HostCommandSurface.SCHEDULER)
        for status in ("WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW")
    ],
)
async def test_initial_waiting_review_never_completes_secure_ingress(
    tmp_path: Path,
    surface: HostCommandSurface,
    status: str,
) -> None:
    if surface is HostCommandSurface.INTERNAL:
        result, db_path, claim_ref = await _run_internal_waiting(tmp_path, status)
    else:
        result, db_path, claim_ref = await _run_scheduler_waiting(tmp_path, status)

    assert result["execution_receipt"]["status"] == status
    assert result["execution_receipt"]["receipt_ref"]
    with sqlite3.connect(str(db_path)) as conn:
        result_ref = conn.execute(
            "SELECT result_ref FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
            (claim_ref,),
        ).fetchone()[0]
    assert result_ref is None
