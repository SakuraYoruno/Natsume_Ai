"""Neutral contracts for historical TaskGraph terminal-state repair.

The signature domain is deliberately separate from Operation and Acceptance
Effect reconciliation.  A proof for either older domain must never authorize
a Graph/Budget repair.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


TERMINAL_RECONCILIATION_CASE_SCHEMA_VERSION = (
    "Natsume.task_runtime.terminal_reconciliation.case.v1"
)
TERMINAL_RECONCILIATION_PROOF_SCHEMA_VERSION = (
    "Natsume.task_runtime.terminal_reconciliation.proof.v1"
)
TERMINAL_RECONCILIATION_BINDING_DOMAIN = (
    "Natsume.task_runtime.terminal_reconciliation"
)

_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "domain",
        "subject_snapshot_sha256",
        "case",
        "resolution",
        "authority",
        "outboxes",
    }
)
_NESTED_KEYS = {
    "case": frozenset(
        {
            "schema_version",
            "case_id",
            "case_kind",
            "classification",
            "reason_code",
            "graph_id",
            "graph_revision",
            "graph_status",
            "graph_status_version",
            "budget_id",
            "budget_status",
            "budget_status_version",
            "node_id",
            "attempt_id",
            "recommended_action",
            "snapshot_sha256",
        }
    ),
    "resolution": frozenset(
        {
            "action",
            "target_graph_status",
            "target_budget_status",
        }
    ),
    "authority": frozenset(
        {
            "authority_kind",
            "reconciler_actor_id",
            "attestation_ref",
            "attestation_key_id",
            "issued_ts_ms",
        }
    ),
    "outboxes": frozenset({"graph", "budget"}),
}
_OUTBOX_KEYS = frozenset(
    {
        "aggregate_type",
        "aggregate_id",
        "aggregate_version",
        "graph_revision",
        "event_type",
        "payload_ref",
        "attempt_id",
        "evidence_refs",
        "idempotency_key",
        "outbox_id",
    }
)


class TerminalReconciliationStoreRejected(RuntimeError):
    """A fail-closed Store rejection whose transaction did not commit."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "TERMINAL_RECONCILIATION_REJECTED")
        self.detail = str(detail or "terminal reconciliation was rejected")
        super().__init__(f"{self.reason_code}: {self.detail}")


def build_terminal_reconciliation_binding(value: Mapping[str, Any]) -> bytes:
    """Return canonical, exact-field bytes for Ed25519 verification."""

    if not isinstance(value, Mapping) or frozenset(value) != _BINDING_KEYS:
        raise ValueError("terminal reconciliation binding has an invalid field set")
    payload = dict(value)
    if payload.get("schema_version") != TERMINAL_RECONCILIATION_PROOF_SCHEMA_VERSION:
        raise ValueError("terminal reconciliation binding schema_version is invalid")
    if payload.get("domain") != TERMINAL_RECONCILIATION_BINDING_DOMAIN:
        raise ValueError("terminal reconciliation binding domain is invalid")
    digest = payload.get("subject_snapshot_sha256")
    if not isinstance(digest, str):
        raise ValueError("terminal reconciliation subject digest is invalid")
    for field_name, expected_keys in _NESTED_KEYS.items():
        nested = payload.get(field_name)
        if not isinstance(nested, Mapping) or frozenset(nested) != expected_keys:
            raise ValueError(
                f"terminal reconciliation binding {field_name} field set is invalid"
            )
        payload[field_name] = dict(nested)
    outboxes = payload["outboxes"]
    for field_name in ("graph", "budget"):
        outbox = outboxes[field_name]
        if outbox is not None:
            if not isinstance(outbox, Mapping) or frozenset(outbox) != _OUTBOX_KEYS:
                raise ValueError(
                    f"terminal reconciliation {field_name} Outbox is invalid"
                )
            outboxes[field_name] = dict(outbox)
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
            "terminal reconciliation binding is not canonical JSON"
        ) from exc


def build_terminal_reconciliation_attestation_binding(
    *,
    case: Mapping[str, Any],
    subject_snapshot_sha256: str,
    action: str,
    target_graph_status: str | None,
    target_budget_status: str | None,
    authority_kind: str,
    reconciler_actor_id: str,
    attestation_ref: str | None,
    attestation_key_id: str | None,
    issued_ts_ms: int,
    graph_outbox: Any | None,
    budget_outbox: Any | None,
) -> tuple[dict[str, Any], bytes]:
    """Build the one domain-separated terminal attestation payload.

    This neutral pure function is the sole mapping constructor used by
    signing challenges and Store verification.  ``attestation_signature`` is
    intentionally absent: the caller signs the returned bytes.
    """

    binding = {
        "schema_version": TERMINAL_RECONCILIATION_PROOF_SCHEMA_VERSION,
        "domain": TERMINAL_RECONCILIATION_BINDING_DOMAIN,
        "subject_snapshot_sha256": str(subject_snapshot_sha256),
        "case": {
            key: case.get(key)
            for key in (
                "schema_version",
                "case_id",
                "case_kind",
                "classification",
                "reason_code",
                "graph_id",
                "graph_revision",
                "graph_status",
                "graph_status_version",
                "budget_id",
                "budget_status",
                "budget_status_version",
                "node_id",
                "attempt_id",
                "recommended_action",
                "snapshot_sha256",
            )
        },
        "resolution": {
            "action": str(action),
            "target_graph_status": target_graph_status,
            "target_budget_status": target_budget_status,
        },
        "authority": {
            "authority_kind": str(authority_kind),
            "reconciler_actor_id": str(reconciler_actor_id),
            "attestation_ref": attestation_ref,
            "attestation_key_id": attestation_key_id,
            "issued_ts_ms": int(issued_ts_ms),
        },
        "outboxes": {
            "graph": _terminal_outbox_binding(graph_outbox),
            "budget": _terminal_outbox_binding(budget_outbox),
        },
    }
    return binding, build_terminal_reconciliation_binding(binding)


def _terminal_outbox_binding(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    getter = value.get if isinstance(value, Mapping) else (
        lambda key, default=None: getattr(value, key, default)
    )
    return {
        "aggregate_type": str(getter("aggregate_type") or ""),
        "aggregate_id": str(getter("aggregate_id") or ""),
        "aggregate_version": int(getter("aggregate_version")),
        "graph_revision": getter("graph_revision"),
        "event_type": str(getter("event_type") or ""),
        "payload_ref": str(getter("payload_ref") or ""),
        "attempt_id": str(getter("attempt_id") or "").strip() or None,
        "evidence_refs": list(getter("evidence_refs", ()) or ()),
        "idempotency_key": str(getter("idempotency_key") or ""),
        "outbox_id": str(getter("outbox_id") or ""),
    }


__all__ = [
    "TERMINAL_RECONCILIATION_BINDING_DOMAIN",
    "TERMINAL_RECONCILIATION_CASE_SCHEMA_VERSION",
    "TERMINAL_RECONCILIATION_PROOF_SCHEMA_VERSION",
    "TerminalReconciliationStoreRejected",
    "build_terminal_reconciliation_attestation_binding",
    "build_terminal_reconciliation_binding",
]
