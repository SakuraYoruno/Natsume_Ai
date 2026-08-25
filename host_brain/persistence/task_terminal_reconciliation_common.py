"""Canonical codecs shared by terminal-reconciliation readers and writers."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.terminal_reconciliation import (
    TERMINAL_RECONCILIATION_CASE_SCHEMA_VERSION,
    TERMINAL_RECONCILIATION_PROOF_SCHEMA_VERSION,
    build_terminal_reconciliation_attestation_binding as _build_terminal_binding,
    build_terminal_reconciliation_binding,
)
from host_brain.persistence.task_record_codec import _dumps, _loads_list, _loads_object
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
    build_outbox_payload_ref,
)

from .task_terminal_reconciliation_codec_validation import (
    selected_binding_matches as _selected_binding_matches,
    terminal_case_matrix_valid as _terminal_case_matrix_valid,
    terminal_case_snapshot_valid,
    terminal_proof_matrix_valid as _terminal_proof_matrix_valid,
    terminal_proof_subject_valid,
)


TERMINAL_RECONCILIATION_SUPERSESSION_SCHEMA = (
    "Natsume.task_runtime.terminal_reconciliation.supersession.v1"
)

_CASE_ID_DOMAIN = b"NATSUME_TASK_RUNTIME_TERMINAL_RECONCILIATION_CASE_V1\x00"
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")

_CASE_COLUMNS = frozenset(
    {
        "case_id",
        "schema_version",
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
        "node_status",
        "node_status_version",
        "attempt_id",
        "attempt_status",
        "attempt_status_version",
        "recommended_action",
        "snapshot_json",
        "snapshot_sha256",
        "created_ts_ms",
    }
)
_PROOF_COLUMNS = frozenset(
    {
        "proof_id",
        "schema_version",
        "case_id",
        "case_snapshot_sha256",
        "authority_kind",
        "action",
        "target_graph_status",
        "target_budget_status",
        "subject_snapshot_json",
        "subject_snapshot_sha256",
        "reconciler_actor_id",
        "attestation_ref",
        "attestation_key_id",
        "attestation_signature",
        "issued_ts_ms",
        "proof_binding_json",
        "proof_digest",
        "graph_outbox_id",
        "budget_outbox_id",
        "created_ts_ms",
    }
)
_SUPERSESSION_COLUMNS = frozenset(
    {
        "supersession_id",
        "schema_version",
        "prior_case_id",
        "replacement_case_id",
        "graph_id",
        "graph_revision",
        "prior_snapshot_sha256",
        "replacement_snapshot_sha256",
        "reason_code",
        "created_ts_ms",
    }
)
_CASE_KINDS = frozenset(
    {
        "FAILED_UNSETTLED_BUDGET",
        "BLOCKED_FAILURE_NODE",
        "CANCELLED_UNSETTLED_BUDGET",
        "COMPLETED_INCONSISTENT",
        "TERMINAL_STRUCTURAL_ANOMALY",
        "TERMINAL_HEALTHY_CLOSURE",
    }
)
_CLASSIFICATIONS = frozenset(
    {"EXACT_REPAIRABLE", "SIGNED_REPAIR_REQUIRED", "QUARANTINED_STRUCTURAL"}
)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return _dumps(dict(value))


def _canonical_digest(value: Mapping[str, Any]) -> tuple[str, str]:
    encoded = _canonical_json(value)
    return encoded, "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _value_digest(value: Any) -> str:
    encoded = _dumps(value)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _terminal_case_id(
    *, graph_id: str, graph_revision: int, case_kind: str, snapshot_sha256: str
) -> str:
    identity = (
        _CASE_ID_DOMAIN
        + str(graph_id).encode("utf-8")
        + b"\n"
        + str(int(graph_revision)).encode("ascii")
        + b"\n"
        + str(case_kind).encode("ascii")
        + b"\n"
        + str(snapshot_sha256).encode("ascii")
    )
    return "terminal-reconciliation-case://sha256/" + hashlib.sha256(identity).hexdigest()


def _terminal_supersession_id(
    *,
    prior_case_id: str,
    replacement_case_id: str,
    prior_snapshot_sha256: str,
    replacement_snapshot_sha256: str,
) -> str:
    material = "\n".join(
        (
            "Natsume.task_runtime.terminal_reconciliation.supersession.v1",
            str(prior_case_id),
            str(replacement_case_id),
            str(prior_snapshot_sha256),
            str(replacement_snapshot_sha256),
        )
    )
    return "terminal-reconciliation-supersession://sha256/" + hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def _terminal_reconciliation_supersession_record(
    conn: sqlite3.Connection,
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    record = {key: row[key] for key in _SUPERSESSION_COLUMNS}
    prior_row = conn.execute(
        "SELECT * FROM task_runtime_terminal_reconciliation_case WHERE case_id = ?",
        (str(record["prior_case_id"]),),
    ).fetchone()
    replacement_row = conn.execute(
        "SELECT * FROM task_runtime_terminal_reconciliation_case WHERE case_id = ?",
        (str(record["replacement_case_id"]),),
    ).fetchone()
    if prior_row is None or replacement_row is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal reconciliation supersession case disappeared",
        )
    prior = _terminal_reconciliation_case_record(prior_row)
    replacement = _terminal_reconciliation_case_record(replacement_row)
    expected_id = _terminal_supersession_id(
        prior_case_id=str(prior["case_id"]),
        replacement_case_id=str(replacement["case_id"]),
        prior_snapshot_sha256=str(prior["snapshot_sha256"]),
        replacement_snapshot_sha256=str(replacement["snapshot_sha256"]),
    )
    proof = conn.execute(
        "SELECT 1 FROM task_runtime_terminal_reconciliation_proof WHERE case_id = ?",
        (str(prior["case_id"]),),
    ).fetchone()
    if (
        str(record["schema_version"])
        != TERMINAL_RECONCILIATION_SUPERSESSION_SCHEMA
        or str(record["reason_code"]) != "CURRENT_CASE_RECLASSIFIED"
        or str(record["supersession_id"]) != expected_id
        or str(record["prior_case_id"]) == str(record["replacement_case_id"])
        or str(record["graph_id"]) != str(prior["graph_id"])
        or str(record["graph_id"]) != str(replacement["graph_id"])
        or int(record["graph_revision"]) != int(prior["graph_revision"])
        or int(record["graph_revision"]) != int(replacement["graph_revision"])
        or str(record["prior_snapshot_sha256"])
        != str(prior["snapshot_sha256"])
        or str(record["replacement_snapshot_sha256"])
        != str(replacement["snapshot_sha256"])
        or proof is not None
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal reconciliation supersession authority drifted",
        )
    record["graph_revision"] = int(record["graph_revision"])
    record["created_ts_ms"] = int(record["created_ts_ms"])
    return record


def _terminal_reconciliation_case_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    record = dict(row)
    try:
        snapshot = _loads_object(
            str(record["snapshot_json"]),
            field_name="terminal_reconciliation.case.snapshot_json",
        )
    except (KeyError, TaskStoreError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal reconciliation case snapshot is invalid",
        ) from exc
    canonical, digest = _canonical_digest(snapshot)
    expected_case_id = _terminal_case_id(
        graph_id=str(record.get("graph_id") or ""),
        graph_revision=int(record.get("graph_revision") or 0),
        case_kind=str(record.get("case_kind") or ""),
        snapshot_sha256=digest,
    )
    scalar_pairs = (
        ("case_kind", "case_kind"),
        ("classification", "classification"),
        ("reason_code", "reason_code"),
        ("recommended_action", "recommended_action"),
    )
    graph = snapshot.get("graph")
    selected_budget = snapshot.get("selected_budget")
    selected_node = snapshot.get("selected_node")
    selected_attempt = snapshot.get("selected_attempt")
    invalid = (
        str(record.get("schema_version") or "")
        != TERMINAL_RECONCILIATION_CASE_SCHEMA_VERSION
        or str(record.get("case_kind") or "") not in _CASE_KINDS
        or str(record.get("classification") or "") not in _CLASSIFICATIONS
        or canonical != str(record.get("snapshot_json") or "")
        or digest != str(record.get("snapshot_sha256") or "")
        or str(record.get("case_id") or "") != expected_case_id
        or not _terminal_case_matrix_valid(record)
        or not terminal_case_snapshot_valid(
            snapshot,
            record,
            case_schema=TERMINAL_RECONCILIATION_CASE_SCHEMA_VERSION,
        )
        or not isinstance(graph, Mapping)
        or str(graph.get("graph_id") or "") != str(record.get("graph_id") or "")
        or int(graph.get("revision") or 0) != int(record.get("graph_revision") or 0)
        or str(graph.get("status") or "") != str(record.get("graph_status") or "")
        or int(graph.get("status_version", -1))
        != int(record.get("graph_status_version", -2))
        or any(
            str(snapshot.get(snapshot_key) or "") != str(record.get(column) or "")
            for snapshot_key, column in scalar_pairs
        )
        or not _selected_binding_matches(
            selected_budget,
            record,
            identity_key="budget_id",
            status_key="budget_status",
            version_key="budget_status_version",
        )
        or not _selected_binding_matches(
            selected_node,
            record,
            identity_key="node_id",
            status_key="node_status",
            version_key="node_status_version",
        )
        or not _selected_binding_matches(
            selected_attempt,
            record,
            identity_key="attempt_id",
            status_key="attempt_status",
            version_key="attempt_status_version",
        )
    )
    if invalid:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal reconciliation case columns or canonical snapshot drifted",
        )
    return {
        key: (
            int(record[key])
            if key
            in {
                "graph_revision",
                "graph_status_version",
                "created_ts_ms",
            }
            else record[key]
        )
        for key in _CASE_COLUMNS - {"snapshot_json"}
    } | {"snapshot": snapshot}


def _terminal_reconciliation_proof_record(
    conn: sqlite3.Connection,
    row: sqlite3.Row | Mapping[str, Any],
    *,
    verifier: Any | None,
) -> dict[str, Any]:
    record = dict(row)
    try:
        subject = _loads_object(
            str(record["subject_snapshot_json"]),
            field_name="terminal_reconciliation.proof.subject_snapshot_json",
        )
        binding = _loads_object(
            str(record["proof_binding_json"]),
            field_name="terminal_reconciliation.proof.proof_binding_json",
        )
        binding_bytes = build_terminal_reconciliation_binding(binding)
    except (KeyError, TaskStoreError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal reconciliation proof payload is invalid",
        ) from exc
    subject_json, subject_digest = _canonical_digest(subject)
    binding_json = binding_bytes.decode("utf-8")
    proof_digest = "sha256:" + hashlib.sha256(binding_bytes).hexdigest()
    case_row = conn.execute(
        "SELECT * FROM task_runtime_terminal_reconciliation_case WHERE case_id = ?",
        (str(record.get("case_id") or ""),),
    ).fetchone()
    if case_row is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal reconciliation proof has no immutable case",
        )
    case = _terminal_reconciliation_case_record(case_row)
    authority_kind = str(record.get("authority_kind") or "")
    signature = str(record.get("attestation_signature") or "").strip() or None
    key_id = str(record.get("attestation_key_id") or "").strip() or None
    graph_outbox, budget_outbox = _validated_proof_outboxes(
        conn,
        record,
        case=case,
    )
    try:
        expected_binding, expected_binding_bytes = _build_terminal_binding(
            case=case,
            subject_snapshot_sha256=subject_digest,
            action=str(record.get("action") or ""),
            target_graph_status=(
                str(record.get("target_graph_status") or "").strip() or None
            ),
            target_budget_status=(
                str(record.get("target_budget_status") or "").strip() or None
            ),
            authority_kind=authority_kind,
            reconciler_actor_id=str(record.get("reconciler_actor_id") or ""),
            attestation_ref=(
                str(record.get("attestation_ref") or "").strip() or None
            ),
            attestation_key_id=key_id,
            issued_ts_ms=int(record.get("issued_ts_ms") or 0),
            graph_outbox=graph_outbox,
            budget_outbox=budget_outbox,
        )
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"terminal reconciliation attestation binding is invalid: {exc}",
        ) from exc
    expected_identity = "\n".join(
        (
            str(case["case_id"]),
            subject_digest,
            str(record.get("action") or ""),
            authority_kind,
            str(record.get("attestation_ref") or "").strip()
            or str(record.get("reconciler_actor_id") or ""),
            str(int(record.get("issued_ts_ms") or 0)),
        )
    )
    expected_proof_id = "terminal-reconciliation-proof://sha256/" + hashlib.sha256(
        expected_identity.encode("utf-8")
    ).hexdigest()
    signature_valid = authority_kind == "SYSTEM_DURABLE_EVIDENCE" and signature is None and key_id is None
    if authority_kind == "SIGNED_OPERATOR":
        signature_valid = bool(
            signature
            and _SIGNATURE_PATTERN.fullmatch(signature)
            and key_id
            and verifier is not None
            and verifier.has_key(key_id)
            and verifier.verify(
                key_id=key_id,
                message=expected_binding_bytes,
                signature=signature,
            )
        )
    if (
        str(record.get("schema_version") or "")
        != TERMINAL_RECONCILIATION_PROOF_SCHEMA_VERSION
        or str(record.get("case_snapshot_sha256") or "")
        != str(case["snapshot_sha256"])
        or subject_json != str(record.get("subject_snapshot_json") or "")
        or subject_digest != str(record.get("subject_snapshot_sha256") or "")
        or binding_json != str(record.get("proof_binding_json") or "")
        or proof_digest != str(record.get("proof_digest") or "")
        or binding.get("subject_snapshot_sha256") != subject_digest
        or binding != expected_binding
        or binding_bytes != expected_binding_bytes
        or str(record.get("proof_id") or "") != expected_proof_id
        or not _terminal_proof_matrix_valid(record, case=case)
        or not terminal_proof_subject_valid(subject, case=case)
        or not signature_valid
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal reconciliation proof digest, signature, or case binding drifted",
        )
    result = {key: record.get(key) for key in _PROOF_COLUMNS - {"subject_snapshot_json", "proof_binding_json"}}
    result["subject_snapshot"] = subject
    result["proof_binding"] = binding
    return result


def _validated_proof_outboxes(
    conn: sqlite3.Connection,
    proof: Mapping[str, Any],
    *,
    case: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    action = str(proof.get("action") or "")
    expected = {
        "graph_outbox_id": (
            "TASK_GRAPH",
            str(case["graph_id"]),
            int(case["graph_status_version"]) + 1,
            "TASK_GRAPH_FAILED",
            int(case["graph_revision"]),
        ),
        "budget_outbox_id": (
            "BUDGET_RESERVATION",
            str(case.get("budget_id") or ""),
            int(case.get("budget_status_version") or 0) + 1,
            "BUDGET_RESERVATION_RELEASED",
            None,
        ),
    }
    projections: dict[str, dict[str, Any] | None] = {}
    expected_presence = {
        "graph_outbox_id": action in {"FAIL_GRAPH", "FAIL_GRAPH_AND_RELEASE_BUDGET"},
        "budget_outbox_id": action in {
            "RELEASE_BUDGET",
            "FAIL_GRAPH_AND_RELEASE_BUDGET",
        },
    }
    proof_id = str(proof.get("proof_id") or "")
    attestation = str(proof.get("attestation_ref") or "").strip() or None
    expected_evidence = [str(case["case_id"]), proof_id]
    if attestation is not None:
        expected_evidence.append(attestation)
    digest = proof_id.rsplit("/", 1)[-1]
    for field_name, binding in expected.items():
        reference = str(proof.get(field_name) or "").strip() or None
        if (reference is not None) != expected_presence[field_name]:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"terminal reconciliation {field_name} presence is invalid",
            )
        if reference is None:
            projections[field_name] = None
            continue
        row = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (reference,),
        ).fetchone()
        if row is None or (
            str(row["aggregate_type"]),
            str(row["aggregate_id"]),
            int(row["aggregate_version"]),
            str(row["event_type"]),
            (int(row["graph_revision"]) if row["graph_revision"] is not None else None),
        ) != binding:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"terminal reconciliation {field_name} binding drifted",
            )
        projection = _outbox_projection(row)
        kind = "graph" if field_name == "graph_outbox_id" else "budget"
        expected_key = f"terminal-reconciliation:{kind}:{digest}"
        expected_id = f"outbox::terminal-reconciliation::{kind}::{digest}"
        expected_payload = (
            build_outbox_payload_ref(
                aggregate_type=projection["aggregate_type"],
                aggregate_id=projection["aggregate_id"],
                aggregate_version=projection["aggregate_version"],
                graph_revision=projection["graph_revision"],
            )
        )
        if (
            projection["attempt_id"] is not None
            or list(projection["evidence_refs"]) != expected_evidence
            or projection["idempotency_key"] != expected_key
            or projection["outbox_id"] != expected_id
            or projection["payload_ref"] != expected_payload
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"terminal reconciliation {field_name} authority drifted",
            )
        projections[field_name] = projection
    return (
        projections["graph_outbox_id"],
        projections["budget_outbox_id"],
    )


def _outbox_projection(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    return {
        "outbox_id": str(row["outbox_id"]),
        "aggregate_type": str(row["aggregate_type"]),
        "aggregate_id": str(row["aggregate_id"]),
        "aggregate_version": int(row["aggregate_version"]),
        "graph_revision": (
            int(row["graph_revision"]) if row["graph_revision"] is not None else None
        ),
        "event_type": str(row["event_type"]),
        "payload_ref": str(row["payload_ref"]),
        "attempt_id": str(row["attempt_id"] or "").strip() or None,
        "evidence_refs": _loads_list(
            str(row["evidence_refs_json"]), field_name="terminal_reconciliation.outbox.evidence_refs"
        ),
        "idempotency_key": str(row["idempotency_key"]),
        "created_ts_ms": int(row["created_ts_ms"]),
    }


__all__ = [
    "TERMINAL_RECONCILIATION_SUPERSESSION_SCHEMA",
    "_CASE_COLUMNS",
    "_PROOF_COLUMNS",
    "_SUPERSESSION_COLUMNS",
    "_canonical_digest",
    "_canonical_json",
    "_outbox_projection",
    "_terminal_case_id",
    "_terminal_reconciliation_supersession_record",
    "_terminal_supersession_id",
    "_terminal_reconciliation_case_record",
    "_terminal_reconciliation_proof_record",
    "_value_digest",
]
