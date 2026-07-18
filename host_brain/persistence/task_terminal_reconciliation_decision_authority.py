"""Canonical terminal decision authority checks for migration 0099.

Every helper validates against the caller-owned SQLite snapshot.  A malformed,
duplicated, or cross-Graph decision is treated as absent authority so the 0099
classifier can quarantine the terminal aggregate without trusting a latest row.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from host_brain.contracts.agent_runtime.parallel_success import (
    normalize_parallel_success_decision,
)
from host_brain.contracts.agent_runtime.parallel_settlement import (
    normalize_parallel_settlement_decision,
)
from host_brain.persistence.task_decision_integrity import (
    _assert_parallel_settlement_decision_outbox_binding,
    _assert_parallel_success_decision_bindings,
)
from host_brain.persistence.task_effect_reconciliation_read_store import (
    TaskEffectReconciliationReadMixin,
)
from host_brain.persistence.task_graph_cancellation_effect_overlay_store import (
    TaskGraphCancellationEffectOverlayMixin,
)
from host_brain.persistence.task_parallel_success_store import (
    TaskParallelSuccessStoreMixin,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _graph_cancellation_decision_record,
    _parallel_settlement_decision_record,
    _parallel_success_decision_record,
)
from host_brain.persistence.task_store_types import (
    _DURABLE_REFERENCE_PATTERN,
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


class _QueryCancellationAuthority(
    TaskGraphCancellationEffectOverlayMixin,
    TaskEffectReconciliationReadMixin,
):
    """Run the production 0092 trusted validator without a writable Store."""

    def __init__(self, owner: Any) -> None:
        self._reconciliation_proof_verifier = getattr(
            owner,
            "_reconciliation_proof_verifier",
            None,
        )

    def _graph_cancellation_evidence_refs(
        self,
        conn: sqlite3.Connection,
        *,
        intent_ref: str,
        graph_id: str,
        revision: int,
    ) -> tuple[str, ...]:
        refs: set[str] = {str(intent_ref)}
        operations = conn.execute(
            """
            SELECT reservation.* FROM task_runtime_idempotency_reservation AS reservation
            JOIN task_runtime_attempt AS attempt ON attempt.attempt_id = reservation.attempt_id
            WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
            ORDER BY reservation.reservation_id
            """,
            (str(graph_id), int(revision)),
        ).fetchall()
        for row in operations:
            durable = next(
                (
                    str(row[key])
                    for key in ("receipt_ref", "reconciliation_proof_ref")
                    if row[key] is not None
                    and _DURABLE_REFERENCE_PATTERN.fullmatch(str(row[key]))
                ),
                None,
            )
            if durable is not None:
                refs.add(durable)
            else:
                material = _dumps(
                    {
                        "reservation_id": str(row["reservation_id"]),
                        "status": str(row["status"]),
                        "status_version": int(row["status_version"]),
                    }
                )
                refs.add(
                    "taskcancel-operation://sha256/"
                    + hashlib.sha256(material.encode("utf-8")).hexdigest()
                )
        effect_rows = conn.execute(
            """
            SELECT effect.effect_id, effect.status, effect.status_version,
                   fence.resolution_status
            FROM task_runtime_acceptance_effect_intent AS effect
            LEFT JOIN task_runtime_graph_cancellation_effect_fence AS fence
              ON fence.effect_id = effect.effect_id AND fence.intent_ref = ?
            WHERE effect.graph_id = ? AND effect.graph_revision = ?
            ORDER BY effect.effect_id
            """,
            (str(intent_ref), str(graph_id), int(revision)),
        ).fetchall()
        for row in effect_rows:
            effect_id = str(row["effect_id"])
            refs.add(effect_id)
            proof = self._read_validated_effect_reconciliation_proof_in_transaction(
                conn,
                intent_ref=str(intent_ref),
                effect_id=effect_id,
                purpose="PENDING_CANCELLATION",
            )
            if proof is not None:
                refs.add(str(proof["proof_id"]))
                receipt_ref = str(proof.get("receipt_ref") or "").strip()
                if _DURABLE_REFERENCE_PATTERN.fullmatch(receipt_ref):
                    refs.add(receipt_ref)
            if row["resolution_status"] is not None:
                material = _dumps(
                    {
                        "effect_id": effect_id,
                        "status": str(row["status"]),
                        "status_version": int(row["status_version"]),
                        "resolution_status": row["resolution_status"],
                    }
                )
                refs.add(
                    "taskcancel-effect://sha256/"
                    + hashlib.sha256(material.encode("utf-8")).hexdigest()
                )
        prefix = f"task-node://{quote(graph_id, safe='')}/{int(revision)}/"
        reviews = conn.execute(
            """
            SELECT review_id, status, status_version, token_ref
            FROM agent_runtime_review
            WHERE task_graph_id = ? AND substr(subject_ref, 1, length(?)) = ?
            ORDER BY review_id
            """,
            (str(graph_id), prefix, prefix),
        ).fetchall()
        for row in reviews:
            material = _dumps(
                {
                    "review_id": str(row["review_id"]),
                    "status": str(row["status"]),
                    "status_version": int(row["status_version"]),
                }
            )
            refs.add(
                "taskcancel-review://sha256/"
                + hashlib.sha256(material.encode("utf-8")).hexdigest()
            )
            token_ref = str(row["token_ref"] or "").strip()
            if _DURABLE_REFERENCE_PATTERN.fullmatch(token_ref):
                refs.add(token_ref)
        return tuple(sorted(refs))


def canonical_parallel_terminal_decision(
    conn: sqlite3.Connection,
    graph: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Return one exact 0090 authority; never select a latest conflicting row."""

    rows = conn.execute(
        """
        SELECT * FROM task_runtime_parallel_settlement_decision
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY graph_version_after, decision_ref
        """,
        (str(graph["graph_id"]), int(graph["revision"])),
    ).fetchall()
    if len(rows) != 1:
        return None
    try:
        record = _parallel_settlement_decision_record(rows[0])
        decision = normalize_parallel_settlement_decision(record["payload"])
        _assert_parallel_settlement_decision_outbox_binding(conn, record)
        payload = decision.payload
        if (
            payload["graph_id"] != str(graph["graph_id"])
            or int(payload["graph_revision"]) != int(graph["revision"])
            or payload["graph_target_status"] != str(graph["status"])
            or int(payload["graph_status_version_before"]) + 1
            != int(graph["status_version"])
        ):
            return None
        return dict(payload)
    except (TaskStoreError, KeyError, TypeError, ValueError):
        return None


def parallel_success_authority_valid(
    owner: Any,
    conn: sqlite3.Connection,
    graph: Mapping[str, Any],
) -> bool:
    """Reuse the complete 0091 codec, bindings, and Attempt authority checks."""

    rows = conn.execute(
        """
        SELECT * FROM task_runtime_parallel_success_decision
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY graph_version_after, decision_ref
        """,
        (str(graph["graph_id"]), int(graph["revision"])),
    ).fetchall()
    if len(rows) != 1:
        return False
    try:
        record = _parallel_success_decision_record(rows[0])
        decision = normalize_parallel_success_decision(record["payload"])
        _assert_parallel_success_decision_bindings(conn, record)
        TaskParallelSuccessStoreMixin._validate_parallel_success_attempt_lineage(
            conn,
            decision=decision,
            graph_id=str(graph["graph_id"]),
            revision=int(graph["revision"]),
            require_latest=True,
            reason_code=TASK_STORE_DURABLE_DATA_INVALID,
        )
        return (
            str(record["graph_id"]) == str(graph["graph_id"])
            and int(record["graph_revision"]) == int(graph["revision"])
            and int(record["graph_version_after"])
            == int(graph["status_version"])
        )
    except (TaskStoreError, KeyError, TypeError, ValueError):
        return False


def graph_cancellation_authority_valid(
    owner: Any,
    conn: sqlite3.Connection,
    graph: Mapping[str, Any],
    budget: Mapping[str, Any],
) -> bool:
    """Reuse the 0092 trusted final-decision and Effect-overlay validator."""

    rows = conn.execute(
        """
        SELECT * FROM task_runtime_graph_cancellation_decision
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY graph_version_after, decision_ref
        """,
        (str(graph["graph_id"]), int(graph["revision"])),
    ).fetchall()
    if len(rows) != 1:
        return False
    try:
        record = _graph_cancellation_decision_record(rows[0])
        validator = getattr(
            owner,
            "_assert_graph_cancellation_decision_trusted",
            None,
        )
        if callable(validator):
            validator(conn, record)
        else:
            _QueryCancellationAuthority(owner)._assert_graph_cancellation_decision_trusted(
                conn,
                record,
            )
        return bool(
            str(record["graph_id"]) == str(graph["graph_id"])
            and int(record["graph_revision"]) == int(graph["revision"])
            and int(record["graph_version_after"])
            == int(graph["status_version"])
            and str(record.get("budget_id") or "") == str(budget["budget_id"])
            and int(record.get("budget_version_after") or -1)
            == int(budget["status_version"])
        )
    except (TaskStoreError, KeyError, TypeError, ValueError):
        return False


__all__ = [
    "canonical_parallel_terminal_decision",
    "graph_cancellation_authority_valid",
    "parallel_success_authority_valid",
]
