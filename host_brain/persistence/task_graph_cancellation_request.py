"""Graph-cancellation 0092 request and durable fence transaction."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    GraphCancellationContractError,
    normalize_graph_cancellation_intent,
)
from host_brain.persistence.task_decision_integrity import (
    _assert_graph_cancellation_decision_bindings,
)
from host_brain.persistence.task_record_codec import (
    _acceptance_effect_record,
    _dumps,
    _graph_cancellation_decision_record,
    _graph_cancellation_intent_record,
    _graph_record,
    _loads_object,
    _node_record,
    _validated_node_payload,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
)


def _cancellation_effect_resolution(effect: Mapping[str, Any]) -> str:
    """Classify one fully validated Effect without guessing dispatch history.

    Current claim authority makes a retry-PENDING row fenceable only through
    legacy or corrupt cross-table state: NODE delivery requires ACCEPTED and
    is excluded below, while GRAPH delivery requires a terminal COMPLETED
    Graph.  The retry branch is nevertheless fail-closed so a new request
    cannot mistake imported or pre-correction durable state for a virgin
    intent.  Existing finalized evidence remains an explicit repair boundary.
    """

    record = _acceptance_effect_record(effect)
    status = str(record["status"])
    if status == "PENDING":
        # 0087 deliberately has two legal PENDING epochs.  Only the initial
        # epoch proves that no handler has ever received dispatch authority;
        # a retry PENDING row already crossed a handler/transport boundary and
        # can be just as uncertain as FAILED.
        virgin = (
            int(record["attempt_count"]) == 0
            and int(record["fencing_token"]) == 0
            and int(record["status_version"]) == 0
            and record.get("reason_code") is None
            and record.get("last_error") is None
        )
        return "CANCELLED" if virgin else "RECONCILIATION_REQUIRED"
    if status == "CLAIMED":
        return "RECONCILIATION_REQUIRED"
    if status == "COMMITTED":
        return "COMMITTED_EVIDENCE"
    if status == "FAILED":
        # A terminal handler/transport failure does not prove that the
        # external side effect never happened.  The 0087 intent row has no
        # attested no-side-effect provenance.
        return "RECONCILIATION_REQUIRED"
    raise TaskStoreError(
        TASK_STORE_DURABLE_DATA_INVALID,
        f"unsupported AcceptanceEffectIntent status: {status}",
    )


class TaskGraphCancellationRequestMixin:
    """Persist one cancellation intent and all first-phase execution fences."""

    def request_graph_cancellation(
        self,
        graph_id: str,
        revision: int,
        *,
        budget_id: str | None,
        expected_graph_version: int,
        expected_budget_version: int | None,
        expected_node_states: Mapping[str, Mapping[str, Any]],
        intent_ref: str,
        intent_payload: Mapping[str, Any],
        graph_outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Atomically install the graph-wide cancellation fence.

        ``request_identity`` is bound and digested by the contract, but its
        authentication remains the caller's authority-boundary responsibility.
        """

        self.assert_graph_cancellation_ready()
        graph_ref = str(graph_id or "").strip()
        budget_ref = str(budget_id or "").strip() or None
        normalized_states = self._normalize_parallel_node_states(expected_node_states)
        try:
            intent = normalize_graph_cancellation_intent(intent_payload)
        except GraphCancellationContractError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"graph cancellation intent is invalid: {exc}",
            ) from exc
        reference = str(intent_ref or "").strip()
        expected_before = {
            node_id: {"status": status, "status_version": version}
            for node_id, (status, version) in normalized_states.items()
        }
        if (
            not graph_ref
            or int(revision) < 1
            or intent.intent_ref != reference
            or intent.payload["graph_id"] != graph_ref
            or intent.payload["graph_revision"] != int(revision)
            or intent.payload["graph_status_version_before"] != int(expected_graph_version)
            or intent.payload["budget_id"] != budget_ref
            or intent.payload["budget_status_version_before"] != expected_budget_version
            or intent.payload["node_states_before"] != expected_before
            or intent.payload["graph_outbox_id"] != graph_outbox.outbox_id
            or graph_outbox.graph_revision != int(revision)
            or reference not in set(graph_outbox.evidence_refs)
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "graph cancellation intent does not bind the requested mutation",
            )

        ts_ms = self._now()
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT * FROM task_runtime_graph_cancellation_intent
                WHERE graph_id = ? AND graph_revision = ? OR intent_ref = ? OR request_id = ?
                ORDER BY intent_ref LIMIT 1
                """,
                (graph_ref, int(revision), reference, intent.payload["request_id"]),
            ).fetchone()
            if existing is not None:
                record = _graph_cancellation_intent_record(existing)
                if (
                    record["intent_ref"] != reference
                    or record["payload_json"] != intent.payload_json
                    or record["payload_sha256"] != intent.payload_sha256
                ):
                    raise TaskStoreError(
                        TASK_STORE_CANCELLATION_CONFLICT,
                        "graph cancellation request identity or snapshot drifted",
                    )
                graph = self._require_graph(conn, graph_ref, int(revision))
                node_rows = conn.execute(
                    """
                    SELECT * FROM task_runtime_node
                    WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
                    """,
                    (graph_ref, int(revision)),
                ).fetchall()
                if str(record["status"]) == "FINALIZED":
                    decision_row = conn.execute(
                        """
                        SELECT * FROM task_runtime_graph_cancellation_decision
                        WHERE intent_ref = ?
                        """,
                        (reference,),
                    ).fetchone()
                    if decision_row is None:
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "finalized cancellation replay is missing its decision",
                        )
                    decision_record = _graph_cancellation_decision_record(decision_row)
                    _assert_graph_cancellation_decision_bindings(
                        conn,
                        decision_record,
                        evidence_refs_builder=self._graph_cancellation_evidence_refs,
                    )
                    self._assert_parallel_node_states(
                        node_rows,
                        {
                            node_id: (state["status"], state["status_version"])
                            for node_id, state in decision_record["payload"][
                                "node_states_after"
                            ].items()
                        },
                        graph_id=graph_ref,
                        revision=int(revision),
                    )
                    if str(graph["status"]) != "CANCELLED":
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "finalized cancellation replay Graph is not CANCELLED",
                        )
                    self._assert_mutation_outbox_replay(
                        conn,
                        graph_outbox,
                        expected_aggregate=(
                            "TASK_GRAPH",
                            graph_ref,
                            int(record["graph_version_after"]),
                        ),
                        expected_event_type="TASK_GRAPH_CANCELLATION_REQUESTED",
                        expected_attempt_id=None,
                        expected_graph_revision=int(revision),
                    )
                    budget = (
                        self._budget_record(self._require_budget(conn, budget_ref))
                        if budget_ref is not None
                        else None
                    )
                    return {
                        "graph": _graph_record(graph, node_rows),
                        "budget": budget,
                        "nodes": tuple(_node_record(row) for row in node_rows),
                        "intent": record,
                        "decision": decision_record,
                        "replayed": True,
                    }
                request_after = intent.payload["node_states_after"]
                for node_row in node_rows:
                    node_id = str(node_row["node_id"])
                    expected = request_after.get(node_id)
                    actual = {
                        "status": str(node_row["status"]),
                        "status_version": int(node_row["status_version"]),
                    }
                    allowed = [expected]
                    if expected is not None and expected["status"] == "CANCEL_REQUESTED":
                        allowed.append(
                            {
                                "status": "CANCELLED",
                                "status_version": expected["status_version"] + 1,
                            }
                        )
                    if actual not in allowed:
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            f"cancellation replay Node drifted: {node_id}",
                        )
                if (
                    str(graph["status"]) != "BLOCKED"
                    or int(graph["status_version"]) != int(record["graph_version_after"])
                ):
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "cancellation replay Graph state drifted",
                    )
                self._assert_mutation_outbox_replay(
                    conn,
                    graph_outbox,
                    expected_aggregate=(
                        "TASK_GRAPH",
                        graph_ref,
                        int(record["graph_version_after"]),
                    ),
                    expected_event_type="TASK_GRAPH_CANCELLATION_REQUESTED",
                    expected_attempt_id=None,
                    expected_graph_revision=int(revision),
                )
                budget = (
                    self._budget_record(self._require_budget(conn, budget_ref))
                    if budget_ref is not None
                    else None
                )
                active_lease = conn.execute(
                    """
                    SELECT lease.lease_id FROM task_runtime_lease AS lease
                    JOIN task_runtime_attempt AS attempt
                      ON attempt.attempt_id = lease.attempt_id
                    WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                      AND lease.status = 'ACTIVE' LIMIT 1
                    """,
                    (graph_ref, int(revision)),
                ).fetchone()
                reserved_operation = conn.execute(
                    """
                    SELECT reservation.reservation_id
                    FROM task_runtime_idempotency_reservation AS reservation
                    JOIN task_runtime_attempt AS attempt
                      ON attempt.attempt_id = reservation.attempt_id
                    WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                      AND reservation.status = 'RESERVED' LIMIT 1
                    """,
                    (graph_ref, int(revision)),
                ).fetchone()
                unfenced_attempt = conn.execute(
                    """
                    SELECT attempt_id FROM task_runtime_attempt
                    WHERE graph_id = ? AND graph_revision = ?
                      AND status IN ('CREATED','LEASED','RUNNING','RECOVERABLE')
                    LIMIT 1
                    """,
                    (graph_ref, int(revision)),
                ).fetchone()
                prefix = (
                    f"task-node://{quote(graph_ref, safe='')}/{int(revision)}/"
                )
                live_review = conn.execute(
                    """
                    SELECT review_id FROM agent_runtime_review
                    WHERE task_graph_id = ?
                      AND substr(subject_ref, 1, length(?)) = ?
                      AND status IN ('PENDING','APPROVED') LIMIT 1
                    """,
                    (graph_ref, prefix, prefix),
                ).fetchone()
                live_token = conn.execute(
                    """
                    SELECT token.token_digest
                    FROM agent_runtime_review_token AS token
                    JOIN agent_runtime_review AS review
                      ON review.review_id = token.review_id
                    WHERE review.task_graph_id = ?
                      AND substr(review.subject_ref, 1, length(?)) = ?
                      AND token.state = 'AVAILABLE' LIMIT 1
                    """,
                    (graph_ref, prefix, prefix),
                ).fetchone()
                unfenced_effect = conn.execute(
                    """
                    SELECT effect.effect_id
                    FROM task_runtime_acceptance_effect_intent AS effect
                    JOIN task_runtime_node AS node
                      ON node.graph_id = effect.graph_id
                     AND node.graph_revision = effect.graph_revision
                     AND node.node_id = effect.node_id
                    JOIN task_runtime_attempt AS attempt
                      ON attempt.attempt_id = effect.attempt_id
                    LEFT JOIN task_runtime_graph_cancellation_effect_fence AS fence
                      ON fence.effect_id = effect.effect_id
                     AND fence.intent_ref = ?
                    WHERE effect.graph_id = ? AND effect.graph_revision = ?
                      AND NOT (
                        effect.commit_scope = 'NODE'
                        AND node.status = 'ACCEPTED'
                        AND attempt.status = 'SUCCEEDED'
                      )
                      AND fence.effect_id IS NULL
                    LIMIT 1
                    """,
                    (reference, graph_ref, int(revision)),
                ).fetchone()
                if any(
                    value is not None
                    for value in (
                        active_lease,
                        reserved_operation,
                        unfenced_attempt,
                        live_review,
                        live_token,
                        unfenced_effect,
                    )
                ):
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "cancellation request replay lost a worker/operation/review/effect fence",
                    )
                return {
                    "graph": _graph_record(graph, node_rows),
                    "budget": budget,
                    "nodes": tuple(_node_record(row) for row in node_rows),
                    "intent": record,
                    "replayed": True,
                }

            success_row = conn.execute(
                """
                SELECT decision_ref FROM task_runtime_parallel_success_decision
                WHERE graph_id = ? AND graph_revision = ?
                """,
                (graph_ref, int(revision)),
            ).fetchone()
            if success_row is not None:
                raise TaskStoreError(
                    TASK_STORE_CANCELLATION_CONFLICT,
                    "completed Graph success authority wins the cancellation race",
                )
            graph = self._require_graph(conn, graph_ref, int(revision))
            self._require_version(
                graph,
                expected_graph_version,
                subject=f"TaskGraph {graph_ref}@{int(revision)}",
            )
            if str(graph["status"]) != intent.payload["graph_status_before"]:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "cancellation Graph source state drifted",
                )
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
                """,
                (graph_ref, int(revision)),
            ).fetchall()
            self._assert_parallel_node_states(
                node_rows,
                normalized_states,
                graph_id=graph_ref,
                revision=int(revision),
            )
            budget_row = None
            if budget_ref is not None:
                budget_row = self._require_budget(conn, budget_ref)
                self._require_version(
                    budget_row,
                    int(expected_budget_version),
                    subject=f"BudgetReservation {budget_ref}",
                )
                if (
                    str(budget_row["graph_id"] or "") != graph_ref
                    or int(budget_row["graph_revision"] or 0) != int(revision)
                    or budget_row["node_id"] is not None
                    or budget_row["attempt_id"] is not None
                    or str(budget_row["status"]) != intent.payload["budget_status_before"]
                ):
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "cancellation Budget is not the expected shared Graph reservation",
                    )

            graph_after_version = int(expected_graph_version) + 1
            graph_payload = _graph_record(graph, node_rows)["graph"]
            graph_payload["status"] = "BLOCKED"
            cursor = conn.execute(
                """
                UPDATE task_runtime_graph
                SET status = 'BLOCKED', status_version = status_version + 1,
                    graph_json = ?, updated_ts_ms = ?
                WHERE graph_id = ? AND revision = ? AND status_version = ?
                  AND status IN ('CREATED', 'ACTIVE', 'BLOCKED')
                """,
                (
                    _dumps(graph_payload),
                    ts_ms,
                    graph_ref,
                    int(revision),
                    int(expected_graph_version),
                ),
            )
            self._require_cas(cursor, subject=f"TaskGraph {graph_ref}@{revision}")
            expected_graph_event = "TASK_GRAPH_CANCELLATION_REQUESTED"
            self._insert_outbox(
                conn,
                graph_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_GRAPH", graph_ref, graph_after_version),
                expected_event_type=expected_graph_event,
                expected_attempt_id=None,
                expected_graph_revision=int(revision),
            )

            for row in node_rows:
                node_id = str(row["node_id"])
                target = intent.payload["node_states_after"][node_id]
                if (
                    str(row["status"]) == target["status"]
                    and int(row["status_version"]) == target["status_version"]
                ):
                    continue
                node_payload = _validated_node_payload(row)
                node_payload["status"] = target["status"]
                node_payload["status_version"] = target["status_version"]
                retry_reset = (
                    ", retry_not_before_ms = NULL"
                    if "retry_not_before_ms" in row.keys()
                    else ""
                )
                cursor = conn.execute(
                    f"""
                    UPDATE task_runtime_node
                    SET status = ?, status_version = status_version + 1,
                        node_json = ?, cancellation_requested_ts_ms = ?,
                        updated_ts_ms = ? {retry_reset}
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                      AND status_version = ?
                    """,
                    (
                        target["status"],
                        _dumps(node_payload),
                        ts_ms,
                        ts_ms,
                        graph_ref,
                        int(revision),
                        node_id,
                        int(row["status_version"]),
                    ),
                )
                self._require_cas(cursor, subject=f"TaskNode {node_id}")
                node_outbox = self._cancellation_outbox(
                    intent_ref=reference,
                    aggregate_type="TASK_NODE",
                    aggregate_id=f"{graph_ref}@{int(revision)}/{node_id}",
                    aggregate_version=int(target["status_version"]),
                    event_type=f"TASK_NODE_{target['status']}",
                )
                self._insert_outbox(
                    conn,
                    node_outbox,
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "TASK_NODE",
                        f"{graph_ref}@{int(revision)}/{node_id}",
                        int(target["status_version"]),
                    ),
                    expected_event_type=f"TASK_NODE_{target['status']}",
                    expected_attempt_id=None,
                )

            attempts = conn.execute(
                """
                SELECT * FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ?
                  AND status IN ('CREATED','LEASED','RUNNING','RECOVERABLE','CANCEL_REQUESTED')
                ORDER BY attempt_id
                """,
                (graph_ref, int(revision)),
            ).fetchall()
            for attempt in attempts:
                if str(attempt["status"]) != "CANCEL_REQUESTED":
                    version = int(attempt["status_version"]) + 1
                    cursor = conn.execute(
                        """
                        UPDATE task_runtime_attempt
                        SET status = 'CANCEL_REQUESTED', owner_id = NULL,
                            status_version = status_version + 1, updated_ts_ms = ?
                        WHERE attempt_id = ? AND status_version = ?
                          AND status IN ('CREATED','LEASED','RUNNING','RECOVERABLE')
                        """,
                        (ts_ms, str(attempt["attempt_id"]), int(attempt["status_version"])),
                    )
                    self._require_cas(cursor, subject=f"Attempt {attempt['attempt_id']}")
                    attempt_outbox = self._cancellation_outbox(
                        intent_ref=reference,
                        aggregate_type="TASK_ATTEMPT",
                        aggregate_id=str(attempt["attempt_id"]),
                        aggregate_version=version,
                        event_type="TASK_ATTEMPT_CANCEL_REQUESTED",
                        attempt_id=str(attempt["attempt_id"]),
                    )
                    self._insert_outbox(
                        conn,
                        attempt_outbox,
                        ts_ms=ts_ms,
                        expected_aggregate=("TASK_ATTEMPT", str(attempt["attempt_id"]), version),
                        expected_event_type="TASK_ATTEMPT_CANCEL_REQUESTED",
                        expected_attempt_id=str(attempt["attempt_id"]),
                    )
                leases = conn.execute(
                    "SELECT * FROM task_runtime_lease WHERE attempt_id = ? AND status = 'ACTIVE'",
                    (str(attempt["attempt_id"]),),
                ).fetchall()
                for lease in leases:
                    version = int(lease["status_version"]) + 1
                    cursor = conn.execute(
                        """
                        UPDATE task_runtime_lease
                        SET status = 'CANCELLED', released_ts_ms = ?,
                            status_version = status_version + 1
                        WHERE lease_id = ? AND status = 'ACTIVE' AND status_version = ?
                        """,
                        (ts_ms, str(lease["lease_id"]), int(lease["status_version"])),
                    )
                    self._require_cas(cursor, subject=f"Lease {lease['lease_id']}")
                    lease_outbox = self._cancellation_outbox(
                        intent_ref=reference,
                        aggregate_type="TASK_LEASE",
                        aggregate_id=str(lease["lease_id"]),
                        aggregate_version=version,
                        event_type="TASK_LEASE_CANCELLED",
                        attempt_id=str(attempt["attempt_id"]),
                    )
                    self._insert_outbox(
                        conn,
                        lease_outbox,
                        ts_ms=ts_ms,
                        expected_aggregate=("TASK_LEASE", str(lease["lease_id"]), version),
                        expected_event_type="TASK_LEASE_CANCELLED",
                        expected_attempt_id=str(attempt["attempt_id"]),
                    )
                reservations = conn.execute(
                    """
                    SELECT * FROM task_runtime_idempotency_reservation
                    WHERE attempt_id = ? AND status = 'RESERVED'
                    """,
                    (str(attempt["attempt_id"]),),
                ).fetchall()
                for reservation in reservations:
                    self._mark_operation_recovery_required(
                        conn,
                        reservation,
                        reason_code="GRAPH_CANCEL_REQUESTED",
                        ts_ms=ts_ms,
                    )

            # Review approval/resume authority is revoked in the same UoW.
            # APPROVED->CANCELLED is a deliberate B5C contract expansion; an
            # AVAILABLE token can no longer resume a cancelled Graph.
            review_subject_prefix = (
                f"task-node://{quote(graph_ref, safe='')}/{int(revision)}/"
            )
            review_rows = conn.execute(
                """
                SELECT * FROM agent_runtime_review
                WHERE task_graph_id = ?
                  AND substr(subject_ref, 1, length(?)) = ?
                  AND status IN ('PENDING', 'APPROVED')
                ORDER BY review_id
                """,
                (graph_ref, review_subject_prefix, review_subject_prefix),
            ).fetchall()
            for review in review_rows:
                review_payload = _loads_object(
                    review["review_json"], field_name="review_json"
                )
                review_payload["status"] = "CANCELLED"
                cursor = conn.execute(
                    """
                    UPDATE agent_runtime_review
                    SET status = 'CANCELLED', status_version = status_version + 1,
                        review_json = ?, updated_ts_ms = ?
                    WHERE review_id = ? AND status_version = ?
                      AND status IN ('PENDING', 'APPROVED')
                    """,
                    (
                        _dumps(review_payload),
                        ts_ms,
                        str(review["review_id"]),
                        int(review["status_version"]),
                    ),
                )
                self._require_cas(cursor, subject=f"ReviewItem {review['review_id']}")
                conn.execute(
                    """
                    UPDATE agent_runtime_review_token
                    SET state = 'REVOKED', updated_ts_ms = ?
                    WHERE review_id = ? AND state = 'AVAILABLE'
                    """,
                    (ts_ms, str(review["review_id"])),
                )

            conn.execute(
                """
                INSERT INTO task_runtime_graph_cancellation_intent(
                    intent_ref, schema_version, request_id, request_identity,
                    source, reason_code, graph_id, graph_revision,
                    graph_version_before, graph_version_after, budget_id,
                    budget_version_at_request, payload_json, payload_sha256,
                    graph_outbox_id, status, status_version,
                    requested_ts_ms, finalized_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REQUESTED', 0, ?, NULL)
                """,
                (
                    reference,
                    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
                    intent.payload["request_id"],
                    intent.payload["request_identity"],
                    intent.payload["source"],
                    intent.payload["reason_code"],
                    graph_ref,
                    int(revision),
                    int(expected_graph_version),
                    graph_after_version,
                    budget_ref,
                    expected_budget_version,
                    intent.payload_json,
                    intent.payload_sha256,
                    graph_outbox.outbox_id,
                    ts_ms,
                ),
            )
            effects = conn.execute(
                """
                SELECT effect.*, node.status AS node_status,
                       attempt.status AS attempt_status
                FROM task_runtime_acceptance_effect_intent AS effect
                JOIN task_runtime_node AS node
                  ON node.graph_id = effect.graph_id
                 AND node.graph_revision = effect.graph_revision
                 AND node.node_id = effect.node_id
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = effect.attempt_id
                WHERE effect.graph_id = ? AND effect.graph_revision = ?
                ORDER BY effect.effect_id
                """,
                (graph_ref, int(revision)),
            ).fetchall()
            for effect in effects:
                resolution = _cancellation_effect_resolution(effect)
                node_effect_authorized = (
                    str(effect["commit_scope"]) == "NODE"
                    and str(effect["node_status"]) == "ACCEPTED"
                    and str(effect["attempt_status"]) == "SUCCEEDED"
                )
                if node_effect_authorized:
                    # Node-scope acceptance authority committed before the
                    # cancellation and remains independently deliverable.
                    continue
                conn.execute(
                    """
                    INSERT INTO task_runtime_graph_cancellation_effect_fence(
                        effect_id, intent_ref, observed_status, resolution_status,
                        observed_status_version, created_ts_ms, resolved_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(effect["effect_id"]),
                        reference,
                        str(effect["status"]),
                        resolution,
                        int(effect["status_version"]),
                        ts_ms,
                        None if resolution == "RECONCILIATION_REQUIRED" else ts_ms,
                    ),
                )
            updated_graph = self._require_graph(conn, graph_ref, int(revision))
            updated_nodes = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
                """,
                (graph_ref, int(revision)),
            ).fetchall()
            intent_row = conn.execute(
                "SELECT * FROM task_runtime_graph_cancellation_intent WHERE intent_ref = ?",
                (reference,),
            ).fetchone()
            return {
                "graph": _graph_record(updated_graph, updated_nodes),
                "budget": self._budget_record(budget_row) if budget_row is not None else None,
                "nodes": tuple(_node_record(row) for row in updated_nodes),
                "intent": _graph_cancellation_intent_record(intent_row),
                "replayed": False,
            }
