"""Contracts for signed, cancellation-independent Acceptance Effect recovery.

The older Effect reconciliation contract is intentionally bound to a Graph
cancellation intent.  This contract covers the other recovery window: a
committed Acceptance Effect may have crossed an external boundary, while its
durable queue row still cannot prove whether the effect happened.

Only the canonical binding returned by
``build_acceptance_effect_reconciliation_binding`` may be signed.  The
signature itself is excluded from the binding and is stored as verification
evidence, never as execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


ACCEPTANCE_EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION = (
    "Natsume.task_runtime.acceptance_effect_reconciliation.case.v2"
)
ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE_SCHEMA_VERSION = (
    "Natsume.task_runtime.acceptance_effect_reconciliation.evidence.v2"
)
ACCEPTANCE_EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION = (
    "Natsume.task_runtime.acceptance_effect_reconciliation.proof.v2"
)
ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION = (
    ACCEPTANCE_EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION
)
ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_DOMAIN = (
    "Natsume.task_runtime.acceptance_effect_reconciliation"
)
ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE = "GENERAL_UNKNOWN_EFFECT"

_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "domain",
        "subject_snapshot_sha256",
        "case",
        "effect",
        "task",
        "resolution",
        "authority",
        "outbox",
    }
)
_NESTED_KEYS = {
    "case": frozenset(
        {
            "schema_version",
            "case_id",
            "purpose",
            "case_kind",
            "classification",
            "effect_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "snapshot_sha256",
        }
    ),
    "effect": frozenset(
        {
            "effect_id",
            "commit_scope",
            "effect_type",
            "payload_ref",
            "payload_sha256",
            "idempotency_key",
            "status",
            "status_version",
            "available_ts_ms",
            "lease_owner",
            "lease_expires_ts_ms",
            "fencing_token",
            "attempt_count",
            "receipt_ref",
            "reason_code",
            "last_error_sha256",
            "created_ts_ms",
            "updated_ts_ms",
            "committed_ts_ms",
            "failed_ts_ms",
        }
    ),
    "task": frozenset(
        {
            "graph_id",
            "graph_revision",
            "graph_status",
            "graph_status_version",
            "node_id",
            "node_status",
            "node_status_version",
            "attempt_id",
            "attempt_status",
            "attempt_status_version",
        }
    ),
    "resolution": frozenset(
        {
            "purpose",
            "outcome",
            "target_effect_status",
            "receipt_ref",
            "executor_quiescence_ref",
        }
    ),
    "authority": frozenset(
        {
            "reconciler_actor_id",
            "attestation_ref",
            "attestation_key_id",
            "issued_ts_ms",
        }
    ),
    "outbox": frozenset(
        {
            "proof_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_type",
            "payload_ref",
            "idempotency_key",
            "outbox_id",
            "attempt_id",
            "evidence_refs",
        }
    ),
}


class AcceptanceEffectReconciliationStoreRejected(RuntimeError):
    """A fail-closed Store rejection whose transaction did not commit."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(
            reason_code or "ACCEPTANCE_EFFECT_RECONCILIATION_REJECTED"
        )
        self.detail = str(
            detail or "Acceptance Effect reconciliation proof was rejected"
        )
        super().__init__(f"{self.reason_code}: {self.detail}")


def build_acceptance_effect_reconciliation_binding(
    value: Mapping[str, Any],
) -> bytes:
    """Return exact domain-separated canonical bytes for Ed25519 signing."""

    if not isinstance(value, Mapping) or frozenset(value) != _BINDING_KEYS:
        raise ValueError(
            "Acceptance Effect reconciliation binding has an invalid field set"
        )
    payload = dict(value)
    if (
        payload.get("schema_version")
        != ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION
    ):
        raise ValueError(
            "Acceptance Effect reconciliation binding schema_version is invalid"
        )
    if (
        payload.get("domain")
        != ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_DOMAIN
    ):
        raise ValueError(
            "Acceptance Effect reconciliation binding domain is invalid"
        )
    digest = payload.get("subject_snapshot_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 71
        or not digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise ValueError(
            "Acceptance Effect reconciliation subject digest is invalid"
        )
    for field_name, expected_keys in _NESTED_KEYS.items():
        nested = payload.get(field_name)
        if not isinstance(nested, Mapping) or frozenset(nested) != expected_keys:
            raise ValueError(
                "Acceptance Effect reconciliation binding "
                f"{field_name} field set is invalid"
            )
        payload[field_name] = dict(nested)
    if (
        payload["case"].get("purpose")
        != ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
        or payload["resolution"].get("purpose")
        != ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
    ):
        raise ValueError(
            "Acceptance Effect reconciliation purpose is invalid"
        )
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Acceptance Effect reconciliation binding is not canonical JSON"
        ) from exc


def build_acceptance_effect_reconciliation_identity(
    *,
    case_id: str,
    subject_snapshot_sha256: str,
    outcome: str,
    target_effect_status: str,
    downstream_receipt_ref: str | None,
    executor_quiescence_ref: str | None,
    reconciler_actor_id: str,
    attestation_ref: str,
    attestation_key_id: str,
    issued_ts_ms: int,
) -> dict[str, str]:
    """Return deterministic proof and Outbox identities for one binding."""

    material = {
        "schema_version": (
            "Natsume.task_runtime."
            "acceptance_effect_reconciliation.identity.v2"
        ),
        "case_id": str(case_id),
        "subject_snapshot_sha256": str(subject_snapshot_sha256),
        "outcome": str(outcome),
        "target_effect_status": str(target_effect_status),
        "downstream_receipt_ref": downstream_receipt_ref,
        "executor_quiescence_ref": executor_quiescence_ref,
        "reconciler_actor_id": str(reconciler_actor_id),
        "attestation_ref": str(attestation_ref),
        "attestation_key_id": str(attestation_key_id),
        "issued_ts_ms": issued_ts_ms,
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "proof_id": "effect-recon-proof://sha256/" + digest,
        "outbox_id": (
            "outbox::acceptance-effect-reconciliation::" + digest
        ),
        "idempotency_key": (
            "idempotency::acceptance-effect-reconciliation::" + digest
        ),
    }


__all__ = [
    "ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_DOMAIN",
    "ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION",
    "ACCEPTANCE_EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION",
    "ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE_SCHEMA_VERSION",
    "ACCEPTANCE_EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION",
    "ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE",
    "AcceptanceEffectReconciliationStoreRejected",
    "build_acceptance_effect_reconciliation_binding",
    "build_acceptance_effect_reconciliation_identity",
]
