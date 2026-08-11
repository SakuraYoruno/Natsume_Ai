from __future__ import annotations

from typing import Any

import pytest

from host_brain.app.accepted_terminal_reconciliation_handler import (
    AcceptedTerminalReconciliationHandler,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_SIGNATURE = "ed25519:" + "A" * 88


def _payload() -> dict[str, Any]:
    return {
        "operator_command_id": "task_terminal.reconcile",
        "case_id": "terminal-case-1",
        "expected_case_snapshot_sha256": _DIGEST_A,
        "expected_subject_snapshot_sha256": _DIGEST_B,
        "action": "RELEASE_BUDGET",
        "target_graph_status": "CANCELLED",
        "target_budget_status": "RELEASED",
        "reconciler_actor_id": "operator.ne01",
        "issued_ts_ms": 1_785_000_000_000,
        "attestation_ref": "attestation://ne01/terminal-case-1",
        "attestation_key_id": "operator-key-v1",
        "attestation_signature": _SIGNATURE,
    }


def _effect_payload() -> dict[str, Any]:
    return {
        "operator_command_id": "task_terminal.reconcile",
        "case_id": "terminal-case-1",
        "signed_command_ref": "terminal-command://sha256/" + "d" * 64,
        "signed_command_sha256": "sha256:" + "e" * 64,
        "request_id": "request-terminal-case-1",
    }


def _proof() -> dict[str, Any]:
    payload = _payload()
    return {
        "proof_id": "terminal-proof://case-1",
        "proof_digest": "sha256:" + "c" * 64,
        "authority_kind": "SIGNED_OPERATOR",
        "case_snapshot_sha256": payload["expected_case_snapshot_sha256"],
        "subject_snapshot_sha256": payload[
            "expected_subject_snapshot_sha256"
        ],
        "action": payload["action"],
        "target_graph_status": payload["target_graph_status"],
        "target_budget_status": payload["target_budget_status"],
        "reconciler_actor_id": payload["reconciler_actor_id"],
        "issued_ts_ms": payload["issued_ts_ms"],
        "attestation_ref": payload["attestation_ref"],
        "attestation_key_id": payload["attestation_key_id"],
        "attestation_signature": payload["attestation_signature"],
    }


class _Store:
    def __init__(self, proof: dict[str, Any] | None = None) -> None:
        self.proof = proof

    def read_terminal_reconciliation_case(self, case_id: str) -> dict[str, Any]:
        assert case_id == "terminal-case-1"
        return {"proof": self.proof}


class _CommandStore:
    def read(self, bundle_ref: str) -> dict[str, Any]:
        assert bundle_ref == _effect_payload()["signed_command_ref"]
        return {
            "bundle_ref": bundle_ref,
            "request_id": "request-terminal-case-1",
            "case_id": "terminal-case-1",
            "command_sha256": _effect_payload()["signed_command_sha256"],
            "command": _payload(),
        }


class _Service:
    def __init__(self, store: _Store, *, lose_response: bool = False) -> None:
        self.store = store
        self.lose_response = lose_response
        self.calls: list[dict[str, Any]] = []

    def reconcile_terminal_state(self, **request: Any) -> dict[str, Any]:
        self.calls.append(dict(request))
        self.store.proof = _proof()
        if self.lose_response:
            raise OSError("response lost after durable commit")
        return {"status": "RECONCILED", "replayed": False}


def test_handler_commits_then_reopens_exact_signed_proof() -> None:
    store = _Store()
    service = _Service(store)
    handler = AcceptedTerminalReconciliationHandler(
        service=service,
        task_store=store,
        command_store=_CommandStore(),
    )

    result = handler(_effect_payload())

    assert len(service.calls) == 1
    assert result == {
        "status": "RECONCILED",
        "receipt_ref": "terminal-proof://case-1",
        "proof_digest": "sha256:" + "c" * 64,
        "authority_kind": "SIGNED_OPERATOR",
    }


def test_handler_recovers_response_loss_from_durable_proof() -> None:
    store = _Store()
    handler = AcceptedTerminalReconciliationHandler(
        service=_Service(store, lose_response=True),
        task_store=store,
        command_store=_CommandStore(),
    )

    result = handler(_effect_payload())

    assert result["receipt_ref"] == "terminal-proof://case-1"
    assert result["status"] == "RECONCILED"


def test_handler_fails_unknown_when_proof_binding_drifts() -> None:
    proof = _proof()
    proof["reconciler_actor_id"] = "operator.forged"
    handler = AcceptedTerminalReconciliationHandler(
        service=_Service(_Store(proof), lose_response=True),
        task_store=_Store(proof),
        command_store=_CommandStore(),
    )

    with pytest.raises(
        AcceptanceEffectOutcomeUnknown,
        match="outcome is unknown",
    ):
        handler(_effect_payload())
