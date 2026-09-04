"""Deterministic claim result for an exact reconciled Internal cancellation.

The reference represents one narrowly proven pre-checkpoint terminal: the
Internal Agent operation was settled by a trusted reconciliation attestation
and the immutable graph cancellation reached its trusted final decision.
``RELEASE + NO_SIDE_EFFECT`` retains the original v1 digest.  A confirmed
external effect uses v2 and binds ``COMMIT + SIDE_EFFECT_CONFIRMED`` plus the
durable downstream receipt.  This is not an Agent result, an Acceptance
receipt, or authority to guess an unknown external effect.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from host_brain.contracts.agent_runtime._internal_claim_primitives import (
    positive_int as _positive_int,
    required_text as _required_text,
    sha256_digest as _sha256_digest,
)


INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX = (
    "task-terminal://internal-precheckpoint-cancellation/sha256/"
)
_SCHEMA_VERSION = "Natsume.internal_precheckpoint_cancellation_claim_result.v1"
_SIDE_EFFECT_SCHEMA_VERSION = (
    "Natsume.internal_precheckpoint_cancellation_claim_result.v2"
)


@dataclass(frozen=True, slots=True)
class InternalPrecheckpointCancellationTerminal:
    """One normalized terminal matrix accepted by cancellation recovery."""

    operation_status: str
    reconciliation_action: str
    reconciliation_outcome: str
    downstream_receipt_ref: str | None
    schema_version: str


def normalize_internal_precheckpoint_cancellation_terminal(
    *,
    reconciliation_action: Any,
    reconciliation_outcome: Any,
    downstream_receipt_ref: Any = None,
    operation_status: Any = None,
) -> InternalPrecheckpointCancellationTerminal:
    """Normalize the only two signed pre-checkpoint cancellation terminals."""

    action = _required_text(
        reconciliation_action,
        field_name="reconciliation_action",
    ).upper()
    outcome = _required_text(
        reconciliation_outcome,
        field_name="reconciliation_outcome",
    ).upper()
    receipt = (
        _required_text(
            downstream_receipt_ref,
            field_name="downstream_receipt_ref",
        )
        if downstream_receipt_ref is not None
        and str(downstream_receipt_ref).strip()
        else None
    )
    if action == "RELEASE" and outcome == "NO_SIDE_EFFECT" and receipt is None:
        expected_status = "RELEASED"
        schema_version = _SCHEMA_VERSION
    elif (
        action == "COMMIT"
        and outcome == "SIDE_EFFECT_CONFIRMED"
        and receipt is not None
    ):
        expected_status = "COMMITTED"
        schema_version = _SIDE_EFFECT_SCHEMA_VERSION
    else:
        raise ValueError(
            "pre-checkpoint cancellation reconciliation terminal is invalid"
        )
    if operation_status is not None:
        normalized_status = _required_text(
            operation_status,
            field_name="operation_status",
        ).upper()
        if normalized_status != expected_status:
            raise ValueError(
                "pre-checkpoint cancellation operation status is invalid"
            )
    return InternalPrecheckpointCancellationTerminal(
        operation_status=expected_status,
        reconciliation_action=action,
        reconciliation_outcome=outcome,
        downstream_receipt_ref=receipt,
        schema_version=schema_version,
    )


def build_internal_precheckpoint_cancellation_result_ref(
    *,
    command_id: str,
    ingress_claim_ref: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    lease_id: str,
    reservation_id: str,
    reconciliation_proof_ref: str,
    reconciliation_proof_digest: str,
    cancellation_intent_ref: str,
    cancellation_decision_ref: str,
    reconciliation_action: str = "RELEASE",
    reconciliation_outcome: str = "NO_SIDE_EFFECT",
    downstream_receipt_ref: str | None = None,
) -> str:
    """Build a replay-safe result from durable, independently reopened facts."""

    terminal = normalize_internal_precheckpoint_cancellation_terminal(
        reconciliation_action=reconciliation_action,
        reconciliation_outcome=reconciliation_outcome,
        downstream_receipt_ref=downstream_receipt_ref,
    )
    payload = {
        "schema_version": terminal.schema_version,
        "command_id": _required_text(command_id, field_name="command_id"),
        "ingress_claim_ref": _required_text(
            ingress_claim_ref,
            field_name="ingress_claim_ref",
        ),
        "graph_id": _required_text(graph_id, field_name="graph_id"),
        "graph_revision": _positive_int(
            graph_revision,
            field_name="graph_revision",
        ),
        "node_id": _required_text(node_id, field_name="node_id"),
        "attempt_id": _required_text(attempt_id, field_name="attempt_id"),
        "lease_id": _required_text(lease_id, field_name="lease_id"),
        "reservation_id": _required_text(
            reservation_id,
            field_name="reservation_id",
        ),
        "reconciliation_proof_ref": _required_text(
            reconciliation_proof_ref,
            field_name="reconciliation_proof_ref",
        ),
        "reconciliation_proof_digest": _sha256_digest(
            reconciliation_proof_digest,
            field_name="reconciliation_proof_digest",
        ),
        "cancellation_intent_ref": _required_text(
            cancellation_intent_ref,
            field_name="cancellation_intent_ref",
        ),
        "cancellation_decision_ref": _required_text(
            cancellation_decision_ref,
            field_name="cancellation_decision_ref",
        ),
    }
    if terminal.schema_version == _SIDE_EFFECT_SCHEMA_VERSION:
        payload.update(
            {
                "reconciliation_action": terminal.reconciliation_action,
                "reconciliation_outcome": terminal.reconciliation_outcome,
                "downstream_receipt_ref": terminal.downstream_receipt_ref,
            }
        )
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return (
        INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX
        + hashlib.sha256(raw).hexdigest()
    )


__all__ = [
    "INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX",
    "InternalPrecheckpointCancellationTerminal",
    "build_internal_precheckpoint_cancellation_result_ref",
    "normalize_internal_precheckpoint_cancellation_terminal",
]
