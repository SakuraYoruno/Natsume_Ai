"""Neutral contracts for cancellation-bound Acceptance Effect evidence.

The application and persistence layers share the rejection type and canonical
attestation encoding.  A definite transactional rejection must stay distinct
from an unknown write result, and Effect proof signatures must never be valid
for the older Operation-reconciliation domain.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION = (
    "Natsume.task_runtime.acceptance_effect_reconciliation.case.v1"
)
EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION = (
    "Natsume.task_runtime.acceptance_effect_reconciliation.proof.v1"
)
EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION = (
    EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION
)
EFFECT_RECONCILIATION_BINDING_DOMAIN = (
    "Natsume.task_runtime.acceptance_effect_reconciliation"
)

_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "domain",
        "subject_snapshot_sha256",
        "case",
        "intent",
        "effect",
        "fence",
        "attempt",
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
            "intent_ref",
            "effect_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "purpose",
            "classification",
            "case_kind",
            "reason_code",
            "legacy_decision_ref",
            "legacy_decision_payload_sha256",
            "snapshot_sha256",
        }
    ),
    "intent": frozenset(
        {
            "intent_ref",
            "request_id",
            "payload_sha256",
            "status",
            "status_version",
            "graph_id",
            "graph_revision",
        }
    ),
    "effect": frozenset(
        {
            "effect_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "commit_scope",
            "effect_type",
            "payload_ref",
            "payload_sha256",
            "idempotency_key",
            "status",
            "status_version",
            "attempt_count",
            "fencing_token",
            "available_ts_ms",
            "committed_ts_ms",
            "created_ts_ms",
            "failed_ts_ms",
            "lease_owner",
            "lease_expires_ts_ms",
            "receipt_ref",
            "reason_code",
            "last_error_sha256",
            "updated_ts_ms",
        }
    ),
    "fence": frozenset(
        {
            "effect_id",
            "intent_ref",
            "observed_status",
            "observed_status_version",
            "resolution_status",
        }
    ),
    "attempt": frozenset(
        {
            "attempt_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "status",
            "status_version",
        }
    ),
    "resolution": frozenset(
        {
            "purpose",
            "outcome",
            "target_resolution_status",
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
            "graph_revision",
            "attempt_id",
            "evidence_refs",
            "event_type",
            "payload_ref",
            "idempotency_key",
            "outbox_id",
        }
    ),
}


class GraphCancellationEffectStoreRejected(RuntimeError):
    """A fail-closed Store rejection whose transaction did not commit."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "EFFECT_RECONCILIATION_REJECTED")
        self.detail = str(detail or "effect reconciliation proof was rejected")
        super().__init__(f"{self.reason_code}: {self.detail}")


def build_graph_cancellation_effect_reconciliation_binding(
    value: Mapping[str, Any],
) -> bytes:
    """Return exact domain-separated bytes signed by the Effect authority.

    ``attestation_signature`` is intentionally absent: it is verified *over*
    these bytes and is compared byte-for-byte during exact proof replay.  The
    key id is inside the signed payload, preventing cross-key substitution.
    Exact key sets make silently dropping a state or authority field a
    contract failure rather than a compatible encoding change.
    """

    if not isinstance(value, Mapping) or frozenset(value) != _BINDING_KEYS:
        raise ValueError("effect reconciliation binding has an invalid field set")
    payload = dict(value)
    if payload.get("schema_version") != EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION:
        raise ValueError("effect reconciliation binding schema_version is invalid")
    if payload.get("domain") != EFFECT_RECONCILIATION_BINDING_DOMAIN:
        raise ValueError("effect reconciliation binding domain is invalid")
    subject_digest = payload.get("subject_snapshot_sha256")
    if not isinstance(subject_digest, str):
        raise ValueError("effect reconciliation subject snapshot digest is invalid")
    for field_name, expected_keys in _NESTED_KEYS.items():
        nested = payload.get(field_name)
        if not isinstance(nested, Mapping) or frozenset(nested) != expected_keys:
            raise ValueError(
                f"effect reconciliation binding {field_name} field set is invalid"
            )
        payload[field_name] = dict(nested)
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("effect reconciliation binding is not canonical JSON") from exc
    return canonical


__all__ = [
    "EFFECT_RECONCILIATION_BINDING_DOMAIN",
    "EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION",
    "EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION",
    "EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION",
    "GraphCancellationEffectStoreRejected",
    "build_graph_cancellation_effect_reconciliation_binding",
]
