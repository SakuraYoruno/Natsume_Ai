from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import pytest

from host_brain.persistence.task_repository import OutboxMessage
from host_brain.task_runtime import AdmittedExecution
from host_brain.task_runtime.operation_checkpoint_reconciliation import (
    OperationCheckpointReconciliationError,
    OperationCheckpointReconciliationService,
)
from tests.integration.test_ne01_operation_online_takeover import _contracts


SIGNATURE = "ed25519:" + base64.urlsafe_b64encode(b"s" * 64).decode("ascii")


def _projection(
    *,
    command: Any,
    admitted: AdmittedExecution,
    attempt_id: str,
) -> dict[str, Any]:
    route_type, _, target_id = admitted.worker_route.partition(":")
    body = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "narrative": "durable checkpoint",
        "route_type": route_type,
        "target_id": target_id or command.target_id,
        "graph_id": admitted.graph_id,
        "graph_revision": admitted.graph_revision,
        "node_id": admitted.node_id,
        "attempt_id": attempt_id,
        "node_grant_ref": "governor-grant://checkpoint/unit",
        "audit_refs": {"checkpoint": "audit://checkpoint/unit"},
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    projection_id = f"worker-projection://unit/{digest}"
    return {
        **body,
        "projection_id": projection_id,
        "payload_digest": f"sha256:{digest}",
        "schema_version": "Natsume.task_runtime.worker_projection.v1",
    }


class _Commands:
    def __init__(self, contracts: tuple[Any, Any, Any, Any]) -> None:
        self._record = {
            "command": contracts[0],
            "work_order": contracts[1],
            "assignment": contracts[2],
            "graph": contracts[3],
        }

    def get_by_graph(
        self,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> Mapping[str, Any] | None:
        command = self._record["command"]
        if (
            command.graph_id == graph_id
            and command.graph_revision == graph_revision
        ):
            return dict(self._record)
        return None


class _Projections:
    def __init__(self, projection: Mapping[str, Any]) -> None:
        self.projection = dict(projection)

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None:
        if receipt_ref != self.projection["projection_id"]:
            return None
        return dict(self.projection)


class _Store:
    def __init__(
        self,
        *,
        admitted: AdmittedExecution,
        attempt_id: str,
        response_loss: bool = False,
        active_lease_expires_ts_ms: int = 99,
    ) -> None:
        self.admitted = admitted
        self.response_loss = response_loss
        self.calls = 0
        self.operation = {
            "reservation_id": admitted.reservation_id,
            "business_scope": admitted.business_scope,
            "operation_fingerprint": admitted.operation_fingerprint,
            "attempt_id": attempt_id,
            "status": "RECOVERY_REQUIRED",
            "status_version": 4,
        }
        self.attempt = {
            "attempt_id": attempt_id,
            "graph_id": admitted.graph_id,
            "graph_revision": admitted.graph_revision,
            "node_id": admitted.node_id,
            "operation_scope": admitted.business_scope,
            "operation_fingerprint": admitted.operation_fingerprint,
            "status": "RUNNING",
        }
        self.lease = {
            "attempt_id": attempt_id,
            "status": "ACTIVE",
            "expires_ts_ms": active_lease_expires_ts_ms,
        }
        self.outbox: Any | None = None

    def get_operation_reservation(self, reservation_id: str) -> Mapping[str, Any]:
        assert reservation_id == self.admitted.reservation_id
        return dict(self.operation)

    def get_attempt(self, attempt_id: str) -> Mapping[str, Any]:
        assert attempt_id == self.attempt["attempt_id"]
        return dict(self.attempt)

    def get_active_lease(self, attempt_id: str) -> Mapping[str, Any] | None:
        assert attempt_id == self.attempt["attempt_id"]
        return dict(self.lease)

    def get_graph(self, graph_id: str, revision: int) -> Mapping[str, Any]:
        assert (graph_id, revision) == (
            self.admitted.graph_id,
            self.admitted.graph_revision,
        )
        return {"status": "ACTIVE"}

    def get_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> Mapping[str, Any]:
        assert (graph_id, graph_revision, node_id) == (
            self.admitted.graph_id,
            self.admitted.graph_revision,
            self.admitted.node_id,
        )
        return {"status": "RUNNING"}

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> None:
        assert (graph_id, revision) == (
            self.admitted.graph_id,
            self.admitted.graph_revision,
        )
        return None

    def reconcile_operation_checkpoint(
        self,
        reservation_id: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        self.calls += 1
        self.outbox = kwargs["outbox"]
        self.operation.update(
            {
                "status": "COMMITTED",
                "status_version": kwargs["expected_version"] + 1,
                "receipt_ref": kwargs["downstream_receipt_ref"],
                "reconciliation_action": "COMMIT",
                "reconciliation_outcome": "SIDE_EFFECT_CONFIRMED",
                "reconciler_actor_id": kwargs["reconciler_actor_id"],
                "reconciliation_proof_ref": kwargs["attestation_ref"],
                "reconciliation_proof_key_id": kwargs["attestation_key_id"],
                "reconciliation_proof_signature": kwargs[
                    "attestation_signature"
                ],
                "reconciliation_recovery_version": kwargs[
                    "expected_version"
                ],
            }
        )
        if self.response_loss:
            raise TimeoutError("response lost after checkpoint COMMIT")
        return dict(self.operation)


def _fixture(
    *,
    response_loss: bool = False,
    active_lease_expires_ts_ms: int = 99,
    projection_attempt_id: str | None = None,
) -> tuple[
    OperationCheckpointReconciliationService,
    _Store,
    dict[str, Any],
]:
    contracts = _contracts((("root", ()),))
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    attempt_id = "attempt-checkpoint-unit"
    projection = _projection(
        command=contracts[0],
        admitted=admitted,
        attempt_id=projection_attempt_id or attempt_id,
    )
    store = _Store(
        admitted=admitted,
        attempt_id=attempt_id,
        response_loss=response_loss,
        active_lease_expires_ts_ms=active_lease_expires_ts_ms,
    )
    service = OperationCheckpointReconciliationService(
        task_store=store,
        command_repository=_Commands(contracts),
        projection_reader=_Projections(projection),
        outbox_factory=OutboxMessage,
        clock_ms=lambda: 100,
    )
    request = {
        "reservation_id": admitted.reservation_id,
        "expected_version": 4,
        "action": "COMMIT",
        "reconciler_actor_id": "reconciler://operator/checkpoint",
        "downstream_receipt_ref": projection["projection_id"],
        "attestation_ref": "proof://operation/checkpoint-unit",
        "attestation_key_id": "checkpoint-reconciliation-v1",
        "attestation_signature": SIGNATURE,
    }
    return service, store, request


def test_signed_checkpoint_commit_builds_exact_outbox_and_replay() -> None:
    service, store, request = _fixture()

    result = service.reconcile_operation_checkpoint(**request)
    replay = service.reconcile_operation_checkpoint(**request)

    assert result["replayed"] is False
    assert result["operation"]["status"] == "COMMITTED"
    assert result["checkpoint"]["projection_ref"] == request[
        "downstream_receipt_ref"
    ]
    assert replay["replayed"] is True
    assert store.calls == 1
    assert store.outbox.event_type == "OPERATION_RECOVERY_COMMIT"
    assert store.outbox.evidence_refs == (
        request["attestation_ref"],
        request["downstream_receipt_ref"],
    )


def test_response_loss_reopens_exact_terminal_checkpoint() -> None:
    service, store, request = _fixture(response_loss=True)

    result = service.reconcile_operation_checkpoint(**request)

    assert result["replayed"] is True
    assert result["operation"]["status"] == "COMMITTED"
    assert store.calls == 1


def test_live_former_lease_blocks_checkpoint_commit() -> None:
    service, store, request = _fixture(active_lease_expires_ts_ms=101)

    with pytest.raises(OperationCheckpointReconciliationError) as raised:
        service.reconcile_operation_checkpoint(**request)

    assert raised.value.side_effects_may_have_run is False
    assert "former Lease" in raised.value.detail
    assert store.calls == 0


def test_projection_from_another_attempt_is_rejected_before_mutation() -> None:
    service, store, request = _fixture(
        projection_attempt_id="attempt-checkpoint-other"
    )

    with pytest.raises(OperationCheckpointReconciliationError) as raised:
        service.reconcile_operation_checkpoint(**request)

    assert raised.value.side_effects_may_have_run is False
    assert store.calls == 0
