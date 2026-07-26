"""Durable TaskNode acceptance decision and Review staging authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.acceptance_judge import (
    AcceptanceJudgement,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewType,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.persistence.review_pending_uow import (
    ReviewPendingUowError,
    insert_pending_review_in_transaction,
    require_review_record,
)
from host_brain.persistence.review_continuation_store import (
    parse_task_review_subject,
)
from host_brain.persistence.task_node_acceptance_common import (
    NODE_ACCEPTANCE_REQUIRED_COLUMNS,
    NODE_ACCEPTANCE_REQUIRED_TRIGGERS,
    NODE_ACCEPTANCE_SCHEMA_VERSION,
    acceptance_ref,
    acceptance_subject,
    acceptance_target,
    non_negative_version,
    required_text,
    validated_acceptance,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _graph_record,
    _node_record,
    _outbox_record,
    _validated_node_payload,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


class TaskNodeAcceptanceStoreMixin:
    """Persist Acceptance before output commit or human review."""

    def assert_node_acceptance_ready(self) -> None:
        conn = self._connect()
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_node_acceptance_decision)"
                ).fetchall()
            }
            triggers = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'trigger'
                      AND name LIKE 'trg_task_runtime_node_acceptance_%'
                    """
                ).fetchall()
            }
        finally:
            conn.close()
        missing_columns = sorted(NODE_ACCEPTANCE_REQUIRED_COLUMNS - columns)
        missing_triggers = sorted(NODE_ACCEPTANCE_REQUIRED_TRIGGERS - triggers)
        if missing_columns or missing_triggers:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task node acceptance migration 0134 is not applied: "
                f"columns={missing_columns},triggers={missing_triggers}",
            )

    def get_node_acceptance_decision(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        node_version_after: int | None = None,
    ) -> dict[str, Any]:
        self.assert_node_acceptance_ready()
        graph_ref, revision, node_ref = acceptance_subject(
            graph_id,
            graph_revision,
            node_id,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            if node_version_after is None:
                row = conn.execute(
                    """
                    SELECT * FROM task_runtime_node_acceptance_decision
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                    ORDER BY node_version_after DESC, acceptance_ref DESC
                    LIMIT 1
                    """,
                    (graph_ref, revision, node_ref),
                ).fetchone()
            else:
                version = non_negative_version(node_version_after)
                if version < 1:
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "node_version_after must be positive",
                    )
                row = conn.execute(
                    """
                    SELECT * FROM task_runtime_node_acceptance_decision
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                      AND node_version_after = ?
                    """,
                    (graph_ref, revision, node_ref, version),
                ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    "TaskNode acceptance decision not found: "
                    f"{graph_ref}@{revision}/{node_ref}",
                )
            return self._node_acceptance_record(conn, row)
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def commit_node_acceptance_review(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        attempt_id: str,
        expected_node_version: int,
        acceptance_judgement: AcceptanceJudgement | Mapping[str, Any],
        node_outbox: OutboxMessage,
        review_item: ReviewItem,
        work_order_id: str,
        expected_graph_version: int | None = None,
        graph_outbox: OutboxMessage | None = None,
    ) -> dict[str, Any]:
        """Atomically freeze REVIEW, Node wait, ReviewItem and direct Graph block."""

        self.assert_node_acceptance_ready()
        graph_ref, revision, node_ref = acceptance_subject(
            graph_id,
            graph_revision,
            node_id,
        )
        attempt_ref = required_text(attempt_id, field_name="attempt_id")
        node_version = non_negative_version(expected_node_version)
        if not isinstance(node_outbox, OutboxMessage):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "acceptance review requires a Node OutboxMessage",
            )
        node_outbox.validate()
        if not isinstance(review_item, ReviewItem):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "acceptance review requires a prepared ReviewItem",
            )
        if (graph_outbox is None) != (expected_graph_version is None):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Graph version and Outbox must be supplied together",
            )
        graph_version = (
            non_negative_version(expected_graph_version)
            if expected_graph_version is not None
            else None
        )
        if graph_outbox is not None:
            graph_outbox.validate()
        ts_ms = self._now()
        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
            )
            graph = self._require_graph(conn, graph_ref, revision)
            node = self._require_node(conn, graph_ref, revision, node_ref)
            node_payload = _validated_node_payload(node)
            judgement = validated_acceptance(
                acceptance_judgement,
                target=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW,
                graph_id=graph_ref,
                node_id=node_ref,
                root_work_order_id=str(graph["root_work_order_id"]),
                attempt_id=attempt_ref,
                node_acceptance_criteria=tuple(
                    node_payload.get("acceptance_criteria") or ()
                ),
                outbox=node_outbox,
            )
            existing = self._find_node_acceptance_decision(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                node_version_after=node_version + 1,
            )
            if existing is not None:
                decision = self._node_acceptance_record(conn, existing)
                self._assert_acceptance_replay(
                    decision,
                    judgement=judgement,
                    target=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW,
                    attempt_id=attempt_ref,
                    node_outbox=node_outbox,
                    review_id=review_item.review_id,
                    graph_outbox=graph_outbox,
                )
                review = self._require_review_for_task_store(
                    conn,
                    review_item.review_id,
                )
                return {
                    "node": _node_record(
                        self._require_node(
                            conn,
                            graph_ref,
                            revision,
                            node_ref,
                        )
                    ),
                    "graph": self._current_graph_record(
                        conn,
                        graph_id=graph_ref,
                        graph_revision=revision,
                    ),
                    "review": review,
                    "decision": decision,
                    "replayed": True,
                }
            self._assert_acceptance_attempt(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
            )
            self._validate_prepared_review(
                review_item,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                node_version_after=node_version + 1,
                judgement=judgement,
            )
            waiting_node = self._transition_node_in_transaction(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                expected_version=node_version,
                target_status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW,
                outbox=node_outbox,
                verification_result=None,
                acceptance_judgement=None,
                ts_ms=ts_ms,
                outbox_attempt_id=attempt_ref,
            )
            blocked_graph = self._current_graph_record(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
            )
            if graph_outbox is not None and graph_version is not None:
                blocked_graph = self._transition_graph_in_transaction(
                    conn,
                    graph_id=graph_ref,
                    revision=revision,
                    expected_version=graph_version,
                    target_status=TaskGraphStatus.BLOCKED,
                    outbox=graph_outbox,
                    ts_ms=ts_ms,
                )
            try:
                review = insert_pending_review_in_transaction(
                    conn,
                    review=review_item,
                    work_order_id=work_order_id,
                    task_graph_id=graph_ref,
                    task_node_id=node_ref,
                    ts_ms=ts_ms,
                )
            except ReviewPendingUowError as exc:
                raise TaskStoreError(exc.reason_code, exc.detail) from exc
            decision = self._insert_node_acceptance_decision_in_transaction(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                node_version_before=node_version,
                target=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW,
                judgement=judgement,
                node_outbox=node_outbox,
                review_id=review_item.review_id,
                graph_version_before=graph_version,
                graph_version_after=(
                    graph_version + 1
                    if graph_version is not None
                    else None
                ),
                graph_outbox_id=(
                    graph_outbox.outbox_id
                    if graph_outbox is not None
                    else None
                ),
                ts_ms=ts_ms,
            )
            return {
                "node": waiting_node,
                "graph": blocked_graph,
                "review": review,
                "decision": decision,
                "replayed": False,
            }

    def _insert_node_acceptance_decision_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        node_version_before: int,
        target: TaskNodeStatus,
        judgement: AcceptanceJudgement,
        node_outbox: OutboxMessage,
        review_id: str | None,
        graph_version_before: int | None,
        graph_version_after: int | None,
        graph_outbox_id: str | None,
        ts_ms: int,
    ) -> dict[str, Any]:
        result_json = _dumps(judgement.to_dict())
        result_sha256 = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        reference = acceptance_ref(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            node_version_after=node_version_before + 1,
            result_sha256=result_sha256,
            node_outbox_id=node_outbox.outbox_id,
            review_id=review_id,
            graph_version_before=graph_version_before,
            graph_version_after=graph_version_after,
            graph_outbox_id=graph_outbox_id,
        )
        try:
            conn.execute(
                """
                INSERT INTO task_runtime_node_acceptance_decision(
                    acceptance_ref, schema_version, judgement_id,
                    graph_id, graph_revision, node_id, attempt_id,
                    node_version_before, node_version_after, target_status,
                    result_json, result_sha256, evidence_refs_json,
                    node_outbox_id, review_id, graph_version_before,
                    graph_version_after, graph_outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reference,
                    NODE_ACCEPTANCE_SCHEMA_VERSION,
                    judgement.judgement_id,
                    graph_id,
                    graph_revision,
                    node_id,
                    attempt_id,
                    node_version_before,
                    node_version_before + 1,
                    target.value,
                    result_json,
                    result_sha256,
                    _dumps(list(judgement.evidence_refs)),
                    node_outbox.outbox_id,
                    review_id,
                    graph_version_before,
                    graph_version_after,
                    graph_outbox_id,
                    int(ts_ms),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "TaskNode acceptance decision identity already exists",
            ) from exc
        row = self._find_node_acceptance_decision(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            node_version_after=node_version_before + 1,
        )
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode acceptance decision was not durable",
            )
        return self._node_acceptance_record(conn, row)

    def _node_acceptance_record(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["result_json"]))
            judgement = AcceptanceJudgement.from_mapping(payload)
            evidence_refs = tuple(json.loads(str(row["evidence_refs_json"])))
        except (
            ContractViolation,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"durable AcceptanceJudgement is invalid: {exc}",
            ) from exc
        canonical = _dumps(judgement.to_dict())
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        target = acceptance_target(str(row["target_status"]))
        review_id = str(row["review_id"] or "").strip() or None
        graph_version_before = (
            int(row["graph_version_before"])
            if row["graph_version_before"] is not None
            else None
        )
        graph_version_after = (
            int(row["graph_version_after"])
            if row["graph_version_after"] is not None
            else None
        )
        graph_outbox_id = str(row["graph_outbox_id"] or "").strip() or None
        expected_ref = acceptance_ref(
            graph_id=str(row["graph_id"]),
            graph_revision=int(row["graph_revision"]),
            node_id=str(row["node_id"]),
            attempt_id=str(row["attempt_id"]),
            node_version_after=int(row["node_version_after"]),
            result_sha256=digest,
            node_outbox_id=str(row["node_outbox_id"]),
            review_id=review_id,
            graph_version_before=graph_version_before,
            graph_version_after=graph_version_after,
            graph_outbox_id=graph_outbox_id,
        )
        if (
            str(row["schema_version"]) != NODE_ACCEPTANCE_SCHEMA_VERSION
            or str(row["acceptance_ref"]) != expected_ref
            or str(row["judgement_id"]) != judgement.judgement_id
            or str(row["result_json"]) != canonical
            or str(row["result_sha256"]) != digest
            or tuple(judgement.evidence_refs) != evidence_refs
            or judgement.task_graph_id != str(row["graph_id"])
            or judgement.task_node_id != str(row["node_id"])
            or judgement.attempt_id != str(row["attempt_id"])
            or int(row["node_version_after"])
            != int(row["node_version_before"]) + 1
            or (
                target is TaskNodeStatus.ACCEPTED
                and (
                    review_id is not None
                    or graph_version_before is not None
                    or graph_version_after is not None
                    or graph_outbox_id is not None
                )
            )
            or (
                target is TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW
                and review_id is None
            )
            or (
                (graph_outbox_id is None)
                != (
                    graph_version_before is None
                    and graph_version_after is None
                )
            )
            or (
                graph_version_before is not None
                and graph_version_after != graph_version_before + 1
            )
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode acceptance decision lineage drifted",
            )
        node_outbox = self._require_acceptance_outbox(
            conn,
            outbox_id=str(row["node_outbox_id"]),
            aggregate_type="TASK_NODE",
            aggregate_id=(
                f"{row['graph_id']}@{int(row['graph_revision'])}/"
                f"{row['node_id']}"
            ),
            aggregate_version=int(row["node_version_after"]),
            event_type=f"TASK_NODE_{target.value}",
            attempt_id=str(row["attempt_id"]),
            graph_revision=None,
        )
        graph = self._require_graph(
            conn,
            str(row["graph_id"]),
            int(row["graph_revision"]),
        )
        node = self._require_node(
            conn,
            str(row["graph_id"]),
            int(row["graph_revision"]),
            str(row["node_id"]),
        )
        node_payload = _validated_node_payload(node)
        validated = validated_acceptance(
            judgement,
            target=target,
            graph_id=str(row["graph_id"]),
            node_id=str(row["node_id"]),
            root_work_order_id=str(graph["root_work_order_id"]),
            attempt_id=str(row["attempt_id"]),
            node_acceptance_criteria=tuple(
                node_payload.get("acceptance_criteria") or ()
            ),
            outbox=OutboxMessage(
                aggregate_type=str(node_outbox["aggregate_type"]),
                aggregate_id=str(node_outbox["aggregate_id"]),
                aggregate_version=int(node_outbox["aggregate_version"]),
                event_type=str(node_outbox["event_type"]),
                payload_ref=str(node_outbox["payload_ref"]),
                idempotency_key=str(node_outbox["idempotency_key"]),
                graph_revision=node_outbox.get("graph_revision"),
                attempt_id=str(node_outbox["attempt_id"]),
                evidence_refs=tuple(node_outbox.get("evidence_refs") or ()),
                outbox_id=str(node_outbox["outbox_id"]),
                available_ts_ms=node_outbox.get("available_ts_ms"),
            ),
        )
        attempt = self._require_attempt(conn, str(row["attempt_id"]))
        if (
            str(attempt["status"]) != AttemptStatus.SUCCEEDED.value
            or str(attempt["graph_id"]) != str(row["graph_id"])
            or int(attempt["graph_revision"]) != int(row["graph_revision"])
            or str(attempt["node_id"]) != str(row["node_id"])
            or int(node["status_version"]) < int(row["node_version_after"])
            or (
                int(node["status_version"]) == int(row["node_version_after"])
                and str(node["status"]) != target.value
            )
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode acceptance decision lost its durable state lineage",
            )
        review: dict[str, Any] | None = None
        if review_id is not None:
            review = self._require_review_for_task_store(conn, review_id)
            review_payload = dict(review.get("review") or {})
            if (
                review_payload.get("review_id") != review_id
                or review_payload.get("review_type")
                != ReviewType.POST_EXECUTION_ACCEPTANCE.value
                or review_payload.get("resume_phase")
                != ReviewResumePhase.POST_EXECUTION_ACCEPTANCE.value
                or int(review_payload.get("subject_version") or -1)
                != int(row["node_version_after"])
                or review.get("task_graph_id") != str(row["graph_id"])
                or review.get("task_node_id") != str(row["node_id"])
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "TaskNode acceptance decision ReviewItem lineage drifted",
                )
        if graph_outbox_id is not None:
            self._require_acceptance_outbox(
                conn,
                outbox_id=graph_outbox_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=str(row["graph_id"]),
                aggregate_version=graph_version_after,
                event_type="TASK_GRAPH_BLOCKED",
                attempt_id=None,
                graph_revision=int(row["graph_revision"]),
            )
        return {
            "acceptance_ref": str(row["acceptance_ref"]),
            "schema_version": str(row["schema_version"]),
            "judgement_id": validated.judgement_id,
            "graph_id": str(row["graph_id"]),
            "graph_revision": int(row["graph_revision"]),
            "node_id": str(row["node_id"]),
            "attempt_id": str(row["attempt_id"]),
            "node_version_before": int(row["node_version_before"]),
            "node_version_after": int(row["node_version_after"]),
            "target_status": target.value,
            "judgement": validated.to_dict(),
            "result_sha256": digest,
            "evidence_refs": evidence_refs,
            "node_outbox_id": str(row["node_outbox_id"]),
            "review_id": review_id,
            "graph_version_before": graph_version_before,
            "graph_version_after": graph_version_after,
            "graph_outbox_id": graph_outbox_id,
            "created_ts_ms": int(row["created_ts_ms"]),
        }

    @staticmethod
    def _find_node_acceptance_decision(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        node_version_after: int,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM task_runtime_node_acceptance_decision
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
              AND node_version_after = ?
            """,
            (
                graph_id,
                int(graph_revision),
                node_id,
                int(node_version_after),
            ),
        ).fetchone()

    def _require_acceptance_outbox(
        self,
        conn: sqlite3.Connection,
        *,
        outbox_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int | None,
        event_type: str,
        attempt_id: str | None,
        graph_revision: int | None,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (outbox_id,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode acceptance decision lost its Outbox",
            )
        record = _outbox_record(row)
        if (
            record.get("aggregate_type") != aggregate_type
            or record.get("aggregate_id") != aggregate_id
            or (
                aggregate_version is not None
                and int(record.get("aggregate_version") or -1)
                != aggregate_version
            )
            or record.get("event_type") != event_type
            or record.get("attempt_id") != attempt_id
            or (
                graph_revision is not None
                and int(record.get("graph_revision") or 0) != graph_revision
            )
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode acceptance decision Outbox lineage drifted",
            )
        return record

    @staticmethod
    def _assert_acceptance_replay(
        decision: Mapping[str, Any],
        *,
        judgement: AcceptanceJudgement,
        target: TaskNodeStatus,
        attempt_id: str,
        node_outbox: OutboxMessage,
        review_id: str | None,
        graph_outbox: OutboxMessage | None,
    ) -> None:
        if (
            decision.get("judgement") != judgement.to_dict()
            or decision.get("target_status") != target.value
            or decision.get("attempt_id") != attempt_id
            or decision.get("node_outbox_id") != node_outbox.outbox_id
            or decision.get("review_id") != review_id
            or decision.get("graph_version_before")
            != (
                graph_outbox.aggregate_version - 1
                if graph_outbox is not None
                else None
            )
            or decision.get("graph_version_after")
            != (
                graph_outbox.aggregate_version
                if graph_outbox is not None
                else None
            )
            or decision.get("graph_outbox_id")
            != (graph_outbox.outbox_id if graph_outbox is not None else None)
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "TaskNode acceptance response-loss replay drifted",
            )

    @staticmethod
    def _validate_prepared_review(
        review: ReviewItem,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        node_version_after: int,
        judgement: AcceptanceJudgement,
    ) -> None:
        binding = parse_task_review_subject(review.subject_ref)
        if (
            review.review_type
            is not ReviewType.POST_EXECUTION_ACCEPTANCE
            or review.resume_phase
            is not ReviewResumePhase.POST_EXECUTION_ACCEPTANCE
            or review.subject_version != node_version_after
            or binding is None
            or binding.graph_id != graph_id
            or binding.graph_revision != graph_revision
            or binding.node_id != node_id
            or binding.attempt_id != attempt_id
            or any(
                ref not in review.evidence_refs
                for ref in judgement.evidence_refs
            )
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "prepared ReviewItem does not bind the Acceptance decision",
            )

    def _assert_acceptance_attempt(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
    ) -> None:
        attempt = self._require_attempt(conn, attempt_id)
        if (
            str(attempt["graph_id"]) != graph_id
            or int(attempt["graph_revision"]) != graph_revision
            or str(attempt["node_id"]) != node_id
            or str(attempt["status"]) != AttemptStatus.SUCCEEDED.value
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "acceptance decision requires the exact SUCCEEDED Attempt",
            )

    @staticmethod
    def _require_review_for_task_store(
        conn: sqlite3.Connection,
        review_id: str,
    ) -> dict[str, Any]:
        try:
            return require_review_record(conn, review_id)
        except ReviewPendingUowError as exc:
            raise TaskStoreError(exc.reason_code, exc.detail) from exc

    def _current_graph_record(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any]:
        graph = self._require_graph(conn, graph_id, graph_revision)
        nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY node_id
            """,
            (graph_id, int(graph_revision)),
        ).fetchall()
        return _graph_record(graph, nodes)


__all__ = ["TaskNodeAcceptanceStoreMixin"]
