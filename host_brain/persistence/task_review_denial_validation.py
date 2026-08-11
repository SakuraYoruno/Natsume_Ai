"""Integrity checks shared by Review-denial commit, replay, and readers."""

from __future__ import annotations

import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.parallel_settlement import (
    normalize_parallel_settlement_decision,
)
from host_brain.contracts.agent_runtime.review_policy import ReviewResumePhase
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.persistence.review_continuation_store import (
    parse_task_review_subject,
)
from host_brain.persistence.review_pending_uow import (
    ReviewPendingUowError,
    review_record_from_row,
)
from host_brain.persistence.task_decision_integrity import (
    _assert_parallel_settlement_decision_outbox_binding,
)
from host_brain.persistence.task_record_codec import (
    _outbox_record,
    _parallel_settlement_decision_record,
)
from host_brain.persistence.task_review_denial_common import (
    REVIEW_DENIAL_SETTLEMENT_SCHEMA_VERSION,
    TERMINAL_REVIEW_STATUSES,
    build_review_denial_settlement_ref,
    sha256_json,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TaskStoreError,
)


def targets_for_phase(
    phase: str,
) -> tuple[TaskNodeStatus, TaskGraphStatus]:
    if phase == ReviewResumePhase.PRE_EXECUTION.value:
        return TaskNodeStatus.CANCELLED, TaskGraphStatus.CANCELLED
    if phase == ReviewResumePhase.POST_EXECUTION_ACCEPTANCE.value:
        return TaskNodeStatus.FAILED, TaskGraphStatus.FAILED
    raise TaskStoreError(
        TASK_STORE_INVALID,
        "ReviewItem resume_phase is unsupported",
    )


def review_denial_authority(
    conn: sqlite3.Connection,
    *,
    review_id: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    node_version: int,
) -> tuple[dict[str, Any], str]:
    row = conn.execute(
        "SELECT * FROM agent_runtime_review WHERE review_id = ?",
        (review_id,),
    ).fetchone()
    if row is None:
        raise TaskStoreError(
            TASK_STORE_NOT_FOUND,
            f"ReviewItem not found: {review_id}",
        )
    try:
        record = review_record_from_row(row)
    except ReviewPendingUowError as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            exc.detail,
        ) from exc
    review = dict(record["review"])
    binding = parse_task_review_subject(review.get("subject_ref"))
    if (
        str(review.get("status")) not in TERMINAL_REVIEW_STATUSES
        or int(record["status_version"]) < 1
        or binding is None
        or binding.graph_id != graph_id
        or binding.graph_revision != graph_revision
        or binding.node_id != node_id
        or int(review.get("subject_version") or -1) != node_version
        or str(record.get("task_graph_id") or "") != graph_id
        or str(record.get("task_node_id") or "") != node_id
    ):
        raise TaskStoreError(
            TASK_STORE_CAS_CONFLICT,
            "terminal ReviewItem binding drifted",
        )
    phase = str(review.get("resume_phase") or "")
    if (
        phase == ReviewResumePhase.PRE_EXECUTION.value
        and binding.attempt_id is not None
    ) or (
        phase == ReviewResumePhase.POST_EXECUTION_ACCEPTANCE.value
        and not binding.attempt_id
    ):
        raise TaskStoreError(
            TASK_STORE_CAS_CONFLICT,
            "terminal ReviewItem Attempt binding drifted",
        )
    return record, sha256_json(record)


def assert_post_review_acceptance(
    owner: Any,
    conn: sqlite3.Connection,
    *,
    acceptance_ref: str,
    review_id: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    node_version: int,
    attempt_id: str,
) -> None:
    row = conn.execute(
        """
        SELECT * FROM task_runtime_node_acceptance_decision
        WHERE acceptance_ref = ?
        """,
        (acceptance_ref,),
    ).fetchone()
    attempt = owner._require_attempt(conn, attempt_id)
    if (
        row is None
        or str(row["review_id"] or "") != review_id
        or str(row["graph_id"]) != graph_id
        or int(row["graph_revision"]) != graph_revision
        or str(row["node_id"]) != node_id
        or int(row["node_version_after"]) != node_version
        or str(row["target_status"]) != "WAITING_ACCEPTANCE_REVIEW"
        or str(row["attempt_id"]) != attempt_id
        or str(attempt["graph_id"]) != graph_id
        or int(attempt["graph_revision"]) != graph_revision
        or str(attempt["node_id"]) != node_id
        or str(attempt["status"]) != AttemptStatus.SUCCEEDED.value
    ):
        raise TaskStoreError(
            TASK_STORE_CAS_CONFLICT,
            "post-execution Review lost its Acceptance/Attempt lineage",
        )
    owner._node_acceptance_record(conn, row)


def blocking_decision_ref(
    owner: Any,
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    graph_status: str,
    graph_version: int,
) -> str | None:
    if graph_status != TaskGraphStatus.BLOCKED.value:
        return None
    row = conn.execute(
        """
        SELECT * FROM task_runtime_parallel_settlement_decision
        WHERE graph_id = ? AND graph_revision = ?
          AND graph_version_after = ?
          AND graph_target_status = 'BLOCKED'
        LIMIT 1
        """,
        (graph_id, graph_revision, graph_version),
    ).fetchone()
    if row is None:
        return None
    record = _parallel_settlement_decision_record(row)
    _assert_parallel_settlement_decision_outbox_binding(conn, record)
    owner._validate_parallel_decision_attempt_lineage(
        conn,
        decision=normalize_parallel_settlement_decision(record["payload"]),
        graph_id=graph_id,
        revision=graph_revision,
        require_latest=False,
        reason_code=TASK_STORE_DURABLE_DATA_INVALID,
    )
    return str(row["decision_ref"])


def validated_review_denial_record(
    owner: Any,
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, Any]:
    record = {key: row[key] for key in row.keys()}
    review_record, review_sha256 = review_denial_authority(
        conn,
        review_id=str(row["review_id"]),
        graph_id=str(row["graph_id"]),
        graph_revision=int(row["graph_revision"]),
        node_id=str(row["node_id"]),
        node_version=int(row["node_version_before"]),
    )
    review = dict(review_record["review"])
    expected_ref = build_review_denial_settlement_ref(
        review_id=str(row["review_id"]),
        review_status=str(row["review_status"]),
        review_status_version=int(row["review_status_version"]),
        resume_phase=str(row["resume_phase"]),
        graph_id=str(row["graph_id"]),
        graph_revision=int(row["graph_revision"]),
        node_id=str(row["node_id"]),
        node_version_before=int(row["node_version_before"]),
        node_target_status=str(row["node_target_status"]),
        graph_status_before=str(row["graph_status_before"]),
        graph_version_before=int(row["graph_version_before"]),
        graph_target_status=str(row["graph_target_status"]),
        budget_id=str(row["budget_id"]),
        budget_version_before=int(row["budget_version_before"]),
        acceptance_ref=row["acceptance_ref"],
        attempt_id=row["attempt_id"],
    )
    if (
        str(row["schema_version"]) != REVIEW_DENIAL_SETTLEMENT_SCHEMA_VERSION
        or str(row["settlement_ref"]) != expected_ref
        or str(row["review_status"]) != str(review["status"])
        or int(row["review_status_version"]) != int(review_record["status_version"])
        or str(row["review_sha256"]) != review_sha256
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "review denial settlement decision drifted",
        )
    node = owner._require_node(
        conn,
        str(row["graph_id"]),
        int(row["graph_revision"]),
        str(row["node_id"]),
    )
    graph = owner._require_graph(
        conn,
        str(row["graph_id"]),
        int(row["graph_revision"]),
    )
    budget = owner._require_budget(conn, str(row["budget_id"]))
    if (
        int(row["node_version_after"]) != int(row["node_version_before"]) + 1
        or str(node["status"]) != str(row["node_target_status"])
        or int(node["status_version"]) != int(row["node_version_after"])
        or int(row["graph_version_after"]) != int(row["graph_version_before"]) + 1
        or str(graph["status"]) != str(row["graph_target_status"])
        or int(graph["status_version"]) != int(row["graph_version_after"])
        or int(row["budget_version_after"])
        != int(row["budget_version_before"]) + 1
        or str(budget["status"]) != BudgetReservationStatus.RELEASED.value
        or int(budget["status_version"]) != int(row["budget_version_after"])
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "review denial terminal aggregate drifted",
        )
    if row["acceptance_ref"] is not None:
        assert_post_review_acceptance(
            owner,
            conn,
            acceptance_ref=str(row["acceptance_ref"]),
            review_id=str(row["review_id"]),
            graph_id=str(row["graph_id"]),
            graph_revision=int(row["graph_revision"]),
            node_id=str(row["node_id"]),
            node_version=int(row["node_version_before"]),
            attempt_id=str(row["attempt_id"]),
        )
    _assert_review_denial_outbox_bindings(conn, record)
    _assert_review_denial_blocking_decision(owner, conn, record)
    return record


def _assert_review_denial_outbox_bindings(
    conn: sqlite3.Connection,
    record: dict[str, Any],
) -> None:
    settlement_ref = str(record["settlement_ref"])
    review_ref = f"review://{record['review_id']}"
    expected = (
        (
            "node_outbox_id",
            "TASK_NODE",
            (
                f"{record['graph_id']}@{record['graph_revision']}/"
                f"{record['node_id']}"
            ),
            int(record["node_version_after"]),
            f"TASK_NODE_{record['node_target_status']}",
            None,
        ),
        (
            "graph_outbox_id",
            "TASK_GRAPH",
            str(record["graph_id"]),
            int(record["graph_version_after"]),
            f"TASK_GRAPH_{record['graph_target_status']}",
            int(record["graph_revision"]),
        ),
        (
            "budget_outbox_id",
            "BUDGET_RESERVATION",
            str(record["budget_id"]),
            int(record["budget_version_after"]),
            "BUDGET_RESERVATION_RELEASED",
            None,
        ),
    )
    for field, kind, aggregate_id, version, event, revision in expected:
        row = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (str(record[field]),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"review denial settlement is missing {field}",
            )
        outbox = _outbox_record(row)
        expected_attempt = (
            record["attempt_id"]
            if kind in {"TASK_NODE", "TASK_GRAPH"}
            else None
        )
        if (
            outbox["aggregate_type"] != kind
            or outbox["aggregate_id"] != aggregate_id
            or int(outbox["aggregate_version"]) != version
            or outbox["event_type"] != event
            or outbox.get("graph_revision") != revision
            or outbox["attempt_id"] != expected_attempt
            or settlement_ref not in outbox["evidence_refs"]
            or review_ref not in outbox["evidence_refs"]
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"review denial settlement {field} binding drifted",
            )


def _assert_review_denial_blocking_decision(
    owner: Any,
    conn: sqlite3.Connection,
    record: dict[str, Any],
) -> None:
    reference = str(record.get("blocking_decision_ref") or "").strip()
    if not reference:
        return
    row = conn.execute(
        """
        SELECT * FROM task_runtime_parallel_settlement_decision
        WHERE decision_ref = ?
        """,
        (reference,),
    ).fetchone()
    if row is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "review denial settlement lost its blocking decision",
        )
    decision = _parallel_settlement_decision_record(row)
    _assert_parallel_settlement_decision_outbox_binding(conn, decision)
    if (
        decision["graph_id"] != record["graph_id"]
        or int(decision["graph_revision"]) != int(record["graph_revision"])
        or int(decision["graph_version_after"])
        != int(record["graph_version_before"])
        or decision["graph_target_status"] != TaskGraphStatus.BLOCKED.value
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "review denial blocking decision binding drifted",
        )
    owner._validate_parallel_decision_attempt_lineage(
        conn,
        decision=normalize_parallel_settlement_decision(decision["payload"]),
        graph_id=str(record["graph_id"]),
        revision=int(record["graph_revision"]),
        require_latest=False,
        reason_code=TASK_STORE_DURABLE_DATA_INVALID,
    )


def validate_review_denial_outboxes(
    *,
    settlement_ref: str,
    review_id: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    node_version: int,
    node_target: TaskNodeStatus,
    graph_version: int,
    graph_target: TaskGraphStatus,
    budget_id: str,
    budget_version: int,
    attempt_id: str | None,
    node_outbox: OutboxMessage,
    graph_outbox: OutboxMessage,
    budget_outbox: OutboxMessage,
) -> None:
    review_ref = f"review://{review_id}"
    expected = (
        (
            node_outbox,
            "TASK_NODE",
            f"{graph_id}@{graph_revision}/{node_id}",
            node_version + 1,
            f"TASK_NODE_{node_target.value}",
            None,
            attempt_id,
        ),
        (
            graph_outbox,
            "TASK_GRAPH",
            graph_id,
            graph_version + 1,
            f"TASK_GRAPH_{graph_target.value}",
            graph_revision,
            attempt_id,
        ),
        (
            budget_outbox,
            "BUDGET_RESERVATION",
            budget_id,
            budget_version + 1,
            "BUDGET_RESERVATION_RELEASED",
            None,
            None,
        ),
    )
    for outbox, kind, aggregate_id, version, event, revision, attempt in expected:
        if (
            outbox.aggregate_type != kind
            or outbox.aggregate_id != aggregate_id
            or outbox.aggregate_version != version
            or outbox.event_type != event
            or outbox.graph_revision != revision
            or outbox.attempt_id != attempt
            or settlement_ref not in outbox.evidence_refs
            or review_ref not in outbox.evidence_refs
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "review denial Outbox lineage drifted",
            )


__all__ = [
    "assert_post_review_acceptance",
    "blocking_decision_ref",
    "review_denial_authority",
    "targets_for_phase",
    "validate_review_denial_outboxes",
    "validated_review_denial_record",
]
