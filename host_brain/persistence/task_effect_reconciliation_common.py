"""Shared schema and codecs for signed Acceptance Effect reconciliation.

The 0098 sidecar records evidence only.  It never mutates the 0087 Effect or
the 0092 cancellation fence/decision, including legacy rows that are known to
be unsafe evidence.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.effect_reconciliation import (
    EFFECT_RECONCILIATION_BINDING_DOMAIN,
    EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION,
    EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _loads_object,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)


EFFECT_RECONCILIATION_PENDING_PURPOSE = "PENDING_CANCELLATION"
EFFECT_RECONCILIATION_LEGACY_PURPOSE = "LEGACY_FINALIZED_CORRECTION"
EFFECT_RECONCILIATION_AGGREGATE_TYPE = (
    "GRAPH_CANCELLATION_EFFECT_RECONCILIATION"
)
EFFECT_RECONCILIATION_EVENT_TYPE = (
    "GRAPH_CANCELLATION_EFFECT_PROOF_RECORDED"
)

_RECONCILIATION_AUTHORITY_REQUIRED = "TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED"
_RECONCILIATION_PROOF_INVALID = "TASK_STORE_RECONCILIATION_PROOF_INVALID"
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

_CASE_COLUMNS = frozenset(
    {
        "case_id",
        "schema_version",
        "purpose",
        "case_kind",
        "classification",
        "reason_code",
        "intent_ref",
        "effect_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "legacy_decision_ref",
        "legacy_decision_payload_sha256",
        "intent_status",
        "intent_status_version",
        "effect_status",
        "effect_status_version",
        "fence_observed_status",
        "fence_observed_status_version",
        "fence_resolution_status",
        "snapshot_json",
        "snapshot_sha256",
        "created_ts_ms",
    }
)
_PROOF_COLUMNS = frozenset(
    {
        "proof_id",
        "schema_version",
        "purpose",
        "case_id",
        "intent_ref",
        "effect_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "case_snapshot_sha256",
        "subject_snapshot_json",
        "subject_snapshot_sha256",
        "outcome",
        "target_resolution_status",
        "receipt_ref",
        "executor_quiescence_ref",
        "reconciler_actor_id",
        "attestation_ref",
        "attestation_key_id",
        "attestation_signature",
        "issued_ts_ms",
        "proof_binding_json",
        "proof_digest",
        "outbox_id",
        "created_ts_ms",
    }
)
_REQUIRED_INDEXES = frozenset(
    {
        "idx_task_runtime_effect_reconciliation_case_class",
        "idx_task_runtime_effect_reconciliation_proof_case",
        "uq_task_runtime_cancellation_effect_fence_binding",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_task_effect_reconciliation_case_no_update",
        "trg_task_effect_reconciliation_case_no_delete",
        "trg_task_effect_reconciliation_proof_no_update",
        "trg_task_effect_reconciliation_proof_no_delete",
        "trg_task_effect_failed_evidence_forbidden_insert",
        "trg_task_effect_failed_evidence_forbidden_update",
    }
)
_TRIGGER_SQL_REQUIREMENTS = {
    "trg_task_effect_reconciliation_case_no_update": (
        "BEFORE UPDATE ON TASK_RUNTIME_EFFECT_RECONCILIATION_CASE",
        "RAISE(ABORT",
    ),
    "trg_task_effect_reconciliation_case_no_delete": (
        "BEFORE DELETE ON TASK_RUNTIME_EFFECT_RECONCILIATION_CASE",
        "RAISE(ABORT",
    ),
    "trg_task_effect_reconciliation_proof_no_update": (
        "BEFORE UPDATE ON TASK_RUNTIME_EFFECT_RECONCILIATION_PROOF",
        "RAISE(ABORT",
    ),
    "trg_task_effect_reconciliation_proof_no_delete": (
        "BEFORE DELETE ON TASK_RUNTIME_EFFECT_RECONCILIATION_PROOF",
        "RAISE(ABORT",
    ),
    "trg_task_effect_failed_evidence_forbidden_insert": (
        "BEFORE INSERT ON TASK_RUNTIME_GRAPH_CANCELLATION_EFFECT_FENCE",
        "NEW.RESOLUTION_STATUS = 'FAILED_EVIDENCE'",
        "RAISE(ABORT",
    ),
    "trg_task_effect_failed_evidence_forbidden_update": (
        "BEFORE UPDATE ON TASK_RUNTIME_GRAPH_CANCELLATION_EFFECT_FENCE",
        "OLD.RESOLUTION_STATUS = 'FAILED_EVIDENCE'",
        "NEW.RESOLUTION_STATUS = 'FAILED_EVIDENCE'",
        "RAISE(ABORT",
    ),
}
_CASE_FOREIGN_KEYS = frozenset(
    {
        ("task_runtime_graph_cancellation_intent", "intent_ref", "intent_ref"),
        ("task_runtime_graph_cancellation_intent", "graph_id", "graph_id"),
        ("task_runtime_graph_cancellation_intent", "graph_revision", "graph_revision"),
        ("task_runtime_acceptance_effect_intent", "effect_id", "effect_id"),
        ("task_runtime_graph_cancellation_effect_fence", "effect_id", "effect_id"),
        ("task_runtime_graph_cancellation_effect_fence", "intent_ref", "intent_ref"),
        ("task_runtime_graph_cancellation_decision", "legacy_decision_ref", "decision_ref"),
        ("task_runtime_node", "graph_id", "graph_id"),
        ("task_runtime_node", "graph_revision", "graph_revision"),
        ("task_runtime_node", "node_id", "node_id"),
        ("task_runtime_attempt", "attempt_id", "attempt_id"),
        ("task_runtime_attempt", "graph_id", "graph_id"),
        ("task_runtime_attempt", "graph_revision", "graph_revision"),
        ("task_runtime_attempt", "node_id", "node_id"),
    }
)
_PROOF_FOREIGN_KEYS = frozenset(
    {
        ("task_runtime_effect_reconciliation_case", "case_id", "case_id"),
        ("task_runtime_effect_reconciliation_case", "intent_ref", "intent_ref"),
        ("task_runtime_effect_reconciliation_case", "effect_id", "effect_id"),
        ("task_runtime_effect_reconciliation_case", "purpose", "purpose"),
        ("task_runtime_effect_reconciliation_case", "graph_id", "graph_id"),
        ("task_runtime_effect_reconciliation_case", "graph_revision", "graph_revision"),
        ("task_runtime_effect_reconciliation_case", "node_id", "node_id"),
        ("task_runtime_effect_reconciliation_case", "attempt_id", "attempt_id"),
        ("task_runtime_outbox", "outbox_id", "outbox_id"),
    }
)

_CASE_SNAPSHOT_KEYS = frozenset(
    {
        "attempt_id",
        "attempt_status",
        "attempt_status_version",
        "case_kind",
        "classification",
        "effect_attempt_count",
        "effect_available_ts_ms",
        "effect_commit_scope",
        "effect_committed_ts_ms",
        "effect_created_ts_ms",
        "effect_failed_ts_ms",
        "effect_fencing_token",
        "effect_id",
        "effect_idempotency_key",
        "effect_last_error_sha256",
        "effect_lease_expires_ts_ms",
        "effect_lease_owner",
        "effect_payload_ref",
        "effect_payload_sha256",
        "effect_reason_code",
        "effect_receipt_ref",
        "effect_status",
        "effect_status_version",
        "effect_type",
        "effect_updated_ts_ms",
        "fence_observed_status",
        "fence_observed_status_version",
        "fence_resolution_status",
        "graph_id",
        "graph_revision",
        "intent_payload_sha256",
        "intent_ref",
        "intent_request_id",
        "intent_status",
        "intent_status_version",
        "inventory_reason",
        "legacy_decision_payload_sha256",
        "legacy_decision_ref",
        "node_id",
        "purpose",
    }
)


def _require_current_legacy_decision_binding(
    conn: sqlite3.Connection,
    case: Mapping[str, Any],
) -> None:
    decision = conn.execute(
        """
        SELECT decision_ref, payload_sha256
        FROM task_runtime_graph_cancellation_decision
        WHERE intent_ref = ?
        """,
        (str(case["intent_ref"]),),
    ).fetchone()
    expected_ref = str(case.get("legacy_decision_ref") or "").strip() or None
    expected_digest = (
        str(case.get("legacy_decision_payload_sha256") or "").strip() or None
    )
    if (
        decision is None
        or expected_ref is None
        or expected_digest is None
        or str(decision["decision_ref"]) != expected_ref
        or str(decision["payload_sha256"]) != expected_digest
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "legacy Effect correction decision binding drifted",
        )


def _effect_reconciliation_case_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    record = dict(row)
    try:
        snapshot = _loads_object(
            str(record["snapshot_json"]),
            field_name="effect_reconciliation.case_snapshot_json",
        )
    except (KeyError, TaskStoreError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "effect reconciliation case snapshot is invalid",
        ) from exc
    canonical = _dumps(snapshot)
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    purpose = str(record.get("purpose") or "")
    case_kind = str(record.get("case_kind") or "")
    classification = str(record.get("classification") or "")
    intent_status = str(record.get("intent_status") or "")
    legacy_decision_ref = str(record.get("legacy_decision_ref") or "").strip() or None
    legacy_decision_digest = (
        str(record.get("legacy_decision_payload_sha256") or "").strip() or None
    )
    expected_case_id = "effect-reconciliation-case://sha256/" + hashlib.sha256(
        (
            f"{purpose}\n{record.get('intent_ref')}\n{record.get('effect_id')}"
        ).encode("utf-8")
    ).hexdigest()
    expected_resolution = {
        "RECONCILIATION_REQUIRED": "RECONCILIATION_REQUIRED",
        "LEGACY_FAILED_EVIDENCE": "FAILED_EVIDENCE",
        "LEGACY_RETRY_PENDING_CANCELLED": "CANCELLED",
    }.get(case_kind)
    classification_matrix_valid = (
        classification == "REPAIRABLE_REQUESTED"
        and purpose == EFFECT_RECONCILIATION_PENDING_PURPOSE
        and intent_status == "REQUESTED"
        and legacy_decision_ref is None
        and legacy_decision_digest is None
    ) or (
        classification == "QUARANTINED_FINALIZED"
        and purpose == EFFECT_RECONCILIATION_LEGACY_PURPOSE
        and intent_status == "FINALIZED"
        and legacy_decision_ref is not None
        and legacy_decision_digest is not None
    ) or (
        classification == "QUARANTINED_STRUCTURAL"
        and (
            (
                purpose == EFFECT_RECONCILIATION_PENDING_PURPOSE
                and intent_status == "REQUESTED"
                and legacy_decision_ref is None
                and legacy_decision_digest is None
            )
            or (
                purpose == EFFECT_RECONCILIATION_LEGACY_PURPOSE
                and intent_status == "FINALIZED"
            )
        )
    )
    case_kind_valid = expected_resolution == str(
        record.get("fence_resolution_status") or ""
    )
    if case_kind == "LEGACY_FAILED_EVIDENCE":
        case_kind_valid = case_kind_valid and snapshot.get("effect_status") == "FAILED"
    elif case_kind == "LEGACY_RETRY_PENDING_CANCELLED":
        case_kind_valid = case_kind_valid and (
            snapshot.get("fence_observed_status") == "PENDING"
            and snapshot.get("effect_status") == "PENDING"
            and int(snapshot.get("effect_status_version", -1))
            == int(snapshot.get("fence_observed_status_version", -2))
            and int(snapshot.get("effect_status_version", -1)) >= 2
            and int(snapshot.get("effect_attempt_count", -1)) >= 1
            and int(snapshot.get("effect_fencing_token", -1)) >= 1
            and snapshot.get("effect_lease_owner") is None
            and snapshot.get("effect_lease_expires_ts_ms") is None
        )
    if (
        set(snapshot) != _CASE_SNAPSHOT_KEYS
        or canonical != str(record.get("snapshot_json") or "")
        or digest != str(record.get("snapshot_sha256") or "")
        or str(record.get("case_id") or "") != expected_case_id
        or not classification_matrix_valid
        or not case_kind_valid
        or str(record.get("schema_version") or "") != EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION
        or str(snapshot.get("purpose") or "") != str(record.get("purpose") or "")
        or str(snapshot.get("case_kind") or "") != str(record.get("case_kind") or "")
        or str(snapshot.get("classification") or "")
        != str(record.get("classification") or "")
        or str(snapshot.get("inventory_reason") or "")
        != str(record.get("reason_code") or "")
        or (str(snapshot.get("legacy_decision_ref") or "").strip() or None)
        != (str(record.get("legacy_decision_ref") or "").strip() or None)
        or (
            str(snapshot.get("legacy_decision_payload_sha256") or "").strip()
            or None
        )
        != (
            str(record.get("legacy_decision_payload_sha256") or "").strip()
            or None
        )
        or str(snapshot.get("intent_ref") or "") != str(record.get("intent_ref") or "")
        or str(snapshot.get("effect_id") or "") != str(record.get("effect_id") or "")
        or str(snapshot.get("graph_id") or "") != str(record.get("graph_id") or "")
        or int(snapshot.get("graph_revision") or 0) != int(record.get("graph_revision") or 0)
        or str(snapshot.get("node_id") or "") != str(record.get("node_id") or "")
        or str(snapshot.get("attempt_id") or "") != str(record.get("attempt_id") or "")
        or str(snapshot.get("intent_status") or "") != str(record.get("intent_status") or "")
        or int(snapshot.get("intent_status_version", -1))
        != int(record.get("intent_status_version", -2))
        or str(snapshot.get("effect_status") or "") != str(record.get("effect_status") or "")
        or int(snapshot.get("effect_status_version", -1))
        != int(record.get("effect_status_version", -2))
        or str(snapshot.get("fence_observed_status") or "")
        != str(record.get("fence_observed_status") or "")
        or int(snapshot.get("fence_observed_status_version", -1))
        != int(record.get("fence_observed_status_version", -2))
        or str(snapshot.get("fence_resolution_status") or "")
        != str(record.get("fence_resolution_status") or "")
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "effect reconciliation case snapshot or columns drifted",
        )
    return {
        "schema_version": str(record["schema_version"]),
        "case_id": str(record["case_id"]),
        "purpose": str(record["purpose"]),
        "case_kind": str(record["case_kind"]),
        "classification": str(record["classification"]),
        "reason_code": str(record["reason_code"]),
        "intent_ref": str(record["intent_ref"]),
        "effect_id": str(record["effect_id"]),
        "graph_id": str(record["graph_id"]),
        "graph_revision": int(record["graph_revision"]),
        "node_id": str(record["node_id"]),
        "attempt_id": str(record["attempt_id"]),
        "legacy_decision_ref": legacy_decision_ref,
        "legacy_decision_payload_sha256": legacy_decision_digest,
        "intent_status": str(record["intent_status"]),
        "intent_status_version": int(record["intent_status_version"]),
        "effect_status": str(record["effect_status"]),
        "effect_status_version": int(record["effect_status_version"]),
        "fence_observed_status": str(record["fence_observed_status"]),
        "fence_observed_status_version": int(record["fence_observed_status_version"]),
        "fence_resolution_status": str(record["fence_resolution_status"]),
        "snapshot": snapshot,
        "snapshot_sha256": str(record["snapshot_sha256"]),
        "created_ts_ms": int(record["created_ts_ms"]),
    }


def _effect_projection(effect: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "effect_id": str(effect["effect_id"]),
        "graph_id": str(effect["graph_id"]),
        "graph_revision": int(effect["graph_revision"]),
        "node_id": str(effect["node_id"]),
        "attempt_id": str(effect["attempt_id"]),
        "attempt_count": int(effect["attempt_count"]),
        "available_ts_ms": int(effect["available_ts_ms"]),
        "committed_ts_ms": effect.get("committed_ts_ms"),
        "commit_scope": str(effect["commit_scope"]),
        "created_ts_ms": int(effect["created_ts_ms"]),
        "failed_ts_ms": effect.get("failed_ts_ms"),
        "fencing_token": int(effect["fencing_token"]),
        "idempotency_key": str(effect["idempotency_key"]),
        "last_error_sha256": _optional_value_digest(effect.get("last_error")),
        "lease_expires_ts_ms": effect.get("lease_expires_ts_ms"),
        "lease_owner": str(effect.get("lease_owner") or "").strip() or None,
        "payload_ref": str(effect["payload_ref"]),
        "payload_sha256": str(effect["payload_sha256"]),
        "reason_code": str(effect.get("reason_code") or "").strip() or None,
        "receipt_ref": str(effect.get("receipt_ref") or "").strip() or None,
        "status": str(effect["status"]),
        "status_version": int(effect["status_version"]),
        "effect_type": str(effect["effect_type"]),
        "updated_ts_ms": int(effect["updated_ts_ms"]),
    }


def _proof_binding_payload(
    *,
    case: Mapping[str, Any],
    subject: Mapping[str, Any],
    proof_id: str,
    purpose: str,
    outcome: str,
    target_resolution_status: str,
    receipt_ref: str | None,
    executor_quiescence_ref: str | None,
    reconciler_actor_id: str,
    attestation_ref: str,
    attestation_key_id: str,
    issued_ts_ms: int,
    outbox: OutboxMessage,
) -> dict[str, Any]:
    return {
        "schema_version": EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION,
        "domain": EFFECT_RECONCILIATION_BINDING_DOMAIN,
        "subject_snapshot_sha256": str(subject["subject_snapshot_sha256"]),
        "case": {
            key: case[key]
            for key in (
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
            )
        },
        "intent": dict(subject["intent"]),
        "effect": dict(subject["effect"]),
        "fence": dict(subject["fence"]),
        "attempt": dict(subject["attempt"]),
        "resolution": {
            "purpose": str(purpose or "").strip().upper(),
            "outcome": str(outcome or "").strip().upper(),
            "target_resolution_status": str(
                target_resolution_status or ""
            ).strip().upper(),
            "receipt_ref": str(receipt_ref or "").strip() or None,
            "executor_quiescence_ref": str(executor_quiescence_ref or "").strip()
            or None,
        },
        "authority": {
            "reconciler_actor_id": str(reconciler_actor_id or "").strip(),
            "attestation_ref": str(attestation_ref or "").strip(),
            "attestation_key_id": str(attestation_key_id or "").strip(),
            "issued_ts_ms": int(issued_ts_ms),
        },
        "outbox": {
            "proof_id": str(proof_id),
            "aggregate_type": outbox.aggregate_type,
            "aggregate_id": outbox.aggregate_id,
            "aggregate_version": outbox.aggregate_version,
            "graph_revision": outbox.graph_revision,
            "attempt_id": outbox.attempt_id,
            "evidence_refs": sorted(set(outbox.evidence_refs)),
            "event_type": outbox.event_type,
            "payload_ref": outbox.payload_ref,
            "idempotency_key": outbox.idempotency_key,
            "outbox_id": outbox.outbox_id,
        },
    }


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_foreign_keys(
    conn: sqlite3.Connection,
    table: str,
) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        (str(row[2]), str(row[3]), str(row[4]))
        for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    )


def _has_index(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    *,
    unique: bool,
) -> bool:
    for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if bool(row[2]) is not bool(unique):
            continue
        index_name = str(row[1]).replace("'", "''")
        actual = tuple(
            str(item[2])
            for item in conn.execute(
                f"PRAGMA index_info('{index_name}')"
            ).fetchall()
        )
        if actual == columns:
            return True
    return False


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 512:
        raise TaskStoreError(TASK_STORE_INVALID, f"{field_name} is invalid")
    return normalized


def _durable_ref(value: Any, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    if not _DURABLE_REFERENCE_PATTERN.fullmatch(normalized):
        raise TaskStoreError(TASK_STORE_INVALID, f"{field_name} must be a durable reference")
    return normalized


def _digest(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise TaskStoreError(TASK_STORE_INVALID, f"{field_name} must be a sha256 digest")
    return normalized


def _optional_value_digest(value: Any) -> str | None:
    normalized = str(value or "")
    if not normalized:
        return None
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
__all__ = [
    "EFFECT_RECONCILIATION_AGGREGATE_TYPE",
    "EFFECT_RECONCILIATION_EVENT_TYPE",
    "EFFECT_RECONCILIATION_LEGACY_PURPOSE",
    "EFFECT_RECONCILIATION_PENDING_PURPOSE",
]
