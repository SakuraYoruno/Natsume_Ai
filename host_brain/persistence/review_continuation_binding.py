"""Authority binding for durable TaskNode Review continuation.

This module owns the canonical TaskNode subject codec and the same-transaction
Intent-admission receipt verification.  It intentionally owns no continuation
claim, lease, or delivery state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from host_brain.contracts.agent_runtime.review_policy import ReviewItem


REVIEW_CONTINUATION_SCHEMA_NOT_READY = "REVIEW_CONTINUATION_SCHEMA_NOT_READY"
REVIEW_CONTINUATION_INVALID = "REVIEW_CONTINUATION_INVALID"
REVIEW_CONTINUATION_CONFLICT = "REVIEW_CONTINUATION_CONFLICT"
REVIEW_CONTINUATION_CLAIM_LOST = "REVIEW_CONTINUATION_CLAIM_LOST"
REVIEW_CONTINUATION_INCOMPLETE = "REVIEW_CONTINUATION_INCOMPLETE"

REVIEW_CONTINUATION_TABLE = "task_runtime_review_continuation"

_INTENT_ADMISSION_AUTHORITY = "intent-admission"
_INTENT_ADMISSION_INITIAL_STATUSES = frozenset({"READY", "BLOCKED"})
_INTENT_ADMISSION_EVENT_TYPE = "IntentAdmissionArbitrated"
_INTENT_ADMISSION_AGGREGATE_TYPE = "TaskNodeIntentAdmission"
_INTENT_ADMISSION_RECEIPT_SCHEMA = "natsume.intent-admission-receipt.v1"
_INTENT_ADMISSION_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "command_id",
        "command_digest",
        "work_order_id",
        "work_order_digest",
        "task_graph_id",
        "graph_revision",
        "task_graph_digest",
        "task_node_id",
        "task_node_digest",
        "workspace_id",
        "subject_id",
        "scene_id",
        "scene_revision",
        "scene_decision_digest",
        "context_pack_digest",
        "intent_frame_digest",
        "arbitration_digest",
        "decision",
        "goal_digest",
        "requested_action_digest",
        "grant_ref",
        "grant_digest",
        "policy_ref",
        "policy_digest",
        "constraint_set_digest",
        "constraint_dispositions",
        "proceed_allowed",
        "receipt_digest",
    }
)


class ReviewContinuationStoreError(RuntimeError):
    """Fail-closed persistence error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or REVIEW_CONTINUATION_INVALID)
        self.detail = str(detail or "review continuation store operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class TaskReviewSubjectBinding:
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str | None


def parse_task_review_subject(value: Any) -> TaskReviewSubjectBinding | None:
    """Parse the exact URI emitted by ``TaskNodeReviewRuntime``."""

    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme != "task-node" or not parsed.netloc:
        return None
    parts = tuple(part for part in parsed.path.split("/") if part)
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    revision = int(parts[0])
    graph_id = unquote(parsed.netloc).strip()
    node_id = unquote(parts[1]).strip()
    if revision < 1 or not graph_id or not node_id:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    # There are exactly two canonical TaskNode subject shapes.  Rejecting
    # duplicate and additional query fields is authority-significant: an
    # otherwise valid ``attempt`` must not make a caller-invented query flag
    # disappear during parsing.
    query_fields = set(query)
    ordinary_subject = query_fields == {"root", "attempt"}
    intent_subject = query_fields == {"attempt", "authority", "initial_status"}
    if not ordinary_subject and not intent_subject:
        return None
    if ordinary_subject:
        roots = query.get("root", [])
        if len(roots) != 1 or not unquote(str(roots[0] or "")).strip():
            return None
    else:
        if query.get("authority") != [_INTENT_ADMISSION_AUTHORITY]:
            return None
        statuses = query.get("initial_status", [])
        if len(statuses) != 1 or unquote(str(statuses[0] or "")).strip().upper() not in (
            _INTENT_ADMISSION_INITIAL_STATUSES
        ):
            return None
    attempts = query.get("attempt", [])
    if len(attempts) != 1:
        return None
    attempt = unquote(str(attempts[0] or "")).strip()
    if not attempt:
        return None
    return TaskReviewSubjectBinding(
        graph_id=graph_id,
        graph_revision=revision,
        node_id=node_id,
        attempt_id=None if attempt == "not-started" else attempt,
    )


def intent_admission_initial_status(value: Any) -> str | None:
    """Return the explicitly bound pre-coordinator Intent checkpoint."""

    try:
        parsed = urlsplit(str(value or "").strip())
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return None
    authorities = query.get("authority", [])
    if authorities != [_INTENT_ADMISSION_AUTHORITY]:
        return None
    initial = query.get("initial_status", [])
    if len(initial) != 1:
        return None
    status = unquote(str(initial[0] or "")).strip().upper()
    if status not in _INTENT_ADMISSION_INITIAL_STATUSES:
        return None
    if set(query) != {"attempt", "authority", "initial_status"}:
        return None
    return status


def canonical_sha256_ref(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission evidence is not finite canonical JSON",
        ) from exc
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def require_intent_admission_receipt_in_transaction(
    conn: sqlite3.Connection,
    *,
    review: ReviewItem,
    binding: TaskReviewSubjectBinding,
) -> dict[str, Any]:
    """Re-open the exact final Intent receipt inside the Review UoW."""

    table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'runtime_event_timeline'
        """
    ).fetchone()
    if table is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_SCHEMA_NOT_READY,
            "Intent admission Review requires the RuntimeEvent authority table",
        )
    evidence_refs = tuple(str(item or "").strip() for item in review.evidence_refs)
    if len(evidence_refs) != 4 or any(not item for item in evidence_refs):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission Review evidence set is incomplete or contains extras",
        )
    event_id = evidence_refs[0]
    row = conn.execute(
        "SELECT * FROM runtime_event_timeline WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission Review has no exact final RuntimeEvent evidence",
        )
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission final RuntimeEvent payload is invalid JSON",
        ) from exc
    if not isinstance(payload, dict) or set(payload) != _INTENT_ADMISSION_PAYLOAD_FIELDS:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission final RuntimeEvent payload schema drifted",
        )

    admission_row = conn.execute(
        """
        SELECT command_json, command_digest, work_order_json, work_order_digest,
               graph_json, graph_digest
        FROM host_command_journal
        WHERE graph_id = ? AND graph_revision = ?
        """,
        (binding.graph_id, binding.graph_revision),
    ).fetchone()
    try:
        command_payload = (
            json.loads(str(admission_row["command_json"]))
            if admission_row is not None
            else None
        )
        work_order_payload = (
            json.loads(str(admission_row["work_order_json"]))
            if admission_row is not None
            else None
        )
        graph_payload = (
            json.loads(str(admission_row["graph_json"]))
            if admission_row is not None
            else None
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission immutable HostCommand record is invalid JSON",
        ) from exc
    if (
        not isinstance(command_payload, dict)
        or not isinstance(work_order_payload, dict)
        or not isinstance(graph_payload, dict)
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission immutable HostCommand record is missing",
        )
    frozen_nodes = graph_payload.get("nodes")
    node_payload = next(
        (
            item
            for item in frozen_nodes
            if isinstance(item, dict) and item.get("node_id") == binding.node_id
        ),
        None,
    ) if isinstance(frozen_nodes, list) else None
    if not isinstance(node_payload, dict):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission immutable TaskGraph lacks the bound TaskNode",
        )
    admission_digests_valid = (
        str(admission_row["command_digest"])
        == canonical_sha256_ref(command_payload)
        and str(admission_row["work_order_digest"])
        == canonical_sha256_ref(work_order_payload)
        and str(admission_row["graph_digest"])
        == canonical_sha256_ref(graph_payload)
    )
    if not admission_digests_valid:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission immutable HostCommand record digest drifted",
        )

    aggregate_id = canonical_sha256_ref(
        {
            "graph_id": binding.graph_id,
            "graph_revision": binding.graph_revision,
            "node_id": binding.node_id,
        }
    )
    aggregate_digest = aggregate_id.split(":", 1)[1]
    identity = (
        str(row["schema_version"]) == "Natsume.runtime.event.v1"
        and str(row["event_type"]) == _INTENT_ADMISSION_EVENT_TYPE
        and str(row["source_kind"]) == "TASK"
        and str(row["aggregate_type"]) == _INTENT_ADMISSION_AGGREGATE_TYPE
        and str(row["aggregate_id"]) == aggregate_id
        and int(row["aggregate_version"]) == binding.graph_revision
        and str(row["payload_ref"])
        == f"intentadmission://sha256/{aggregate_digest}"
        and str(row["redaction_level"]) == "INTERNAL_REDACTED"
    )
    if not identity:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission final RuntimeEvent envelope differs from TaskNode binding",
        )
    if (
        payload.get("schema") != _INTENT_ADMISSION_RECEIPT_SCHEMA
        or payload.get("command_id") != command_payload.get("command_id")
        or payload.get("command_digest") != admission_row["command_digest"]
        or payload.get("work_order_id") != work_order_payload.get("work_order_id")
        or payload.get("work_order_digest") != admission_row["work_order_digest"]
        or payload.get("task_graph_id") != binding.graph_id
        or payload.get("graph_revision") != binding.graph_revision
        or payload.get("task_node_id") != binding.node_id
        or payload.get("task_graph_digest") != admission_row["graph_digest"]
        or payload.get("task_node_digest") != canonical_sha256_ref(node_payload)
        or payload.get("decision") != "REVIEW"
        or payload.get("proceed_allowed") is not False
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission final receipt is not the exact REVIEW decision",
        )
    dispositions = payload.get("constraint_dispositions")
    if (
        not isinstance(dispositions, list)
        or dispositions != sorted(set(dispositions))
        or "REVIEW" not in dispositions
        or "BLOCK" in dispositions
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission final receipt constraints do not permit Review continuation",
        )
    receipt_body = dict(payload)
    receipt_digest = str(receipt_body.pop("receipt_digest", ""))
    if receipt_digest != canonical_sha256_ref(receipt_body):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission final receipt digest drifted",
        )
    if str(row["payload_digest"]) != canonical_sha256_ref(payload):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission RuntimeEvent payload digest drifted",
        )

    def _raw_digest(field: str) -> str:
        value = str(payload.get(field) or "").strip().removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ReviewContinuationStoreError(
                REVIEW_CONTINUATION_CONFLICT,
                f"Intent admission receipt {field} is invalid",
            )
        return value

    command_ref = f"hostcommand://sha256/{_raw_digest('command_digest')}"
    graph_ref = f"taskgraph://sha256/{_raw_digest('task_graph_digest')}"
    scene_ref = (
        "scene://sha256/"
        + canonical_sha256_ref(
            {
                "workspace_id": payload.get("workspace_id"),
                "subject_id": payload.get("subject_id"),
                "scene_id": payload.get("scene_id"),
                "revision": payload.get("scene_revision"),
                "decision_digest": payload.get("scene_decision_digest"),
            }
        ).split(":", 1)[1]
    )
    try:
        runtime_evidence = json.loads(str(row["evidence_refs_json"] or "[]"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission RuntimeEvent evidence is invalid JSON",
        ) from exc
    expected_runtime_evidence = [
        command_ref,
        graph_ref,
        scene_ref,
        str(payload.get("grant_ref") or "").strip(),
    ]
    dedupe_key = canonical_sha256_ref(
        {
            "aggregate_id": aggregate_id,
            "aggregate_type": _INTENT_ADMISSION_AGGREGATE_TYPE,
            "aggregate_version": binding.graph_revision,
            "event_type": _INTENT_ADMISSION_EVENT_TYPE,
            "source_kind": "TASK",
            "source_ref": command_ref,
        }
    )
    if (
        str(row["source_ref"]) != command_ref
        or runtime_evidence != expected_runtime_evidence
        or str(row["dedupe_key"]) != dedupe_key
        or event_id != f"runtimeevent://sha256/{dedupe_key.split(':', 1)[1]}"
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission RuntimeEvent authority envelope drifted",
        )

    def _digest_evidence(scheme: str, field: str) -> str:
        return f"{scheme}://sha256/{_raw_digest(field)}"

    expected_evidence = (
        event_id,
        _digest_evidence("intentframe", "intent_frame_digest"),
        _digest_evidence("intentarbitration", "arbitration_digest"),
        _digest_evidence("scene", "scene_decision_digest"),
    )
    constraint_digest = str(payload.get("constraint_set_digest") or "").strip()
    constraint_raw = constraint_digest.removeprefix("sha256:")
    if (
        evidence_refs != expected_evidence
        or not re.fullmatch(r"[0-9a-f]{64}", constraint_raw)
        or review.diff_ref != f"intent-constraints://sha256/{constraint_raw}"
    ):
        raise ReviewContinuationStoreError(
            REVIEW_CONTINUATION_CONFLICT,
            "Intent admission Review evidence or constraint binding drifted",
        )
    return payload


__all__ = [
    "REVIEW_CONTINUATION_CLAIM_LOST",
    "REVIEW_CONTINUATION_CONFLICT",
    "REVIEW_CONTINUATION_INCOMPLETE",
    "REVIEW_CONTINUATION_INVALID",
    "REVIEW_CONTINUATION_SCHEMA_NOT_READY",
    "REVIEW_CONTINUATION_TABLE",
    "ReviewContinuationStoreError",
    "TaskReviewSubjectBinding",
    "canonical_sha256_ref",
    "intent_admission_initial_status",
    "parse_task_review_subject",
    "require_intent_admission_receipt_in_transaction",
]
