"""Parallel-success 0091 atomic persistence and replay authority.

The mixin is stateless.  It borrows the owning ``DurableTaskStore`` connection,
transaction, clock, Graph/Budget primitives, and Acceptance Effect staging so
all terminal assets remain in the caller's existing transaction.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Sequence

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
    validate_budget_transition,
)
from host_brain.contracts.agent_runtime.parallel_success import (
    CanonicalParallelSuccessDecision,
    PARALLEL_SUCCESS_SCHEMA_VERSION,
    ParallelSuccessContractError,
    normalize_parallel_success_decision,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.persistence.task_decision_integrity import (
    _assert_parallel_success_decision_bindings,
)
from host_brain.persistence.task_record_codec import (
    _graph_record,
    _parallel_success_decision_record,
)
from host_brain.persistence.task_store_types import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
    AttemptStatus,
    IdempotencyStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    _PARALLEL_SUCCESS_DECISION_REQUIRED_COLUMNS,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_record,
)


class TaskParallelSuccessStoreMixin:
    """Read, commit, and replay canonical parallel-success decisions."""

    def assert_parallel_success_decision_ready(self) -> None:
        """Require the NE-01 B5 canonical whole-graph success journal."""

        conn = self._connect()
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_parallel_success_decision)"
                ).fetchall()
            }
        finally:
            conn.close()
        missing = sorted(_PARALLEL_SUCCESS_DECISION_REQUIRED_COLUMNS - columns)
        if missing:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime parallel success migration 0091 is not applied: "
                f"{missing}",
            )

    def get_parallel_success_decision(self, decision_ref: str) -> dict[str, Any]:
        """Resolve and integrity-check one canonical whole-graph success."""

        self.assert_parallel_success_decision_ready()
        self.assert_acceptance_effect_intent_ready()
        reference = str(decision_ref or "").strip()
        if not reference:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel success decision_ref is required",
            )
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_parallel_success_decision
                WHERE decision_ref = ?
                """,
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"parallel success decision not found: {reference}",
                )
            record = _parallel_success_decision_record(row)
            _assert_parallel_success_decision_bindings(conn, record)
            self._validate_parallel_success_attempt_lineage(
                conn,
                decision=normalize_parallel_success_decision(record["payload"]),
                graph_id=str(record["graph_id"]),
                revision=int(record["graph_revision"]),
                require_latest=True,
                reason_code=TASK_STORE_DURABLE_DATA_INVALID,
            )
            return record
        finally:
            conn.close()

    def get_latest_parallel_success_decision(
        self,
        graph_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        """Return the success decision for a Graph revision, if present."""

        self.assert_parallel_success_decision_ready()
        self.assert_acceptance_effect_intent_ready()
        graph_ref = str(graph_id or "").strip()
        if (
            not graph_ref
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel success graph identity is invalid",
            )
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_parallel_success_decision
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY graph_version_after DESC, decision_ref DESC
                LIMIT 1
                """,
                (graph_ref, revision),
            ).fetchone()
            if row is None:
                return None
            record = _parallel_success_decision_record(row)
            _assert_parallel_success_decision_bindings(conn, record)
            self._validate_parallel_success_attempt_lineage(
                conn,
                decision=normalize_parallel_success_decision(record["payload"]),
                graph_id=graph_ref,
                revision=revision,
                require_latest=True,
                reason_code=TASK_STORE_DURABLE_DATA_INVALID,
            )
            return record
        finally:
            conn.close()

    def complete_parallel_graph_success(
        self,
        graph_id: str,
        revision: int,
        *,
        budget_id: str,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_node_states: Mapping[str, Mapping[str, Any]],
        decision_ref: str,
        decision_payload: Mapping[str, Any],
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage,
        effect_intents: Sequence[AcceptanceEffectIntentSpec | Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Atomically commit all-accepted Graph, Budget, effects, and decision.

        The method is the only B5 success authority.  It accepts a complete
        versioned Node snapshot and an explicit result-node lineage.  Exact
        response-loss replay is allowed; any identity, state, projection,
        effect, or Outbox drift fails closed without partial writes.
        """

        self.assert_parallel_success_decision_ready()
        self.assert_acceptance_effect_intent_ready()
        graph_ref = str(graph_id or "").strip()
        budget_ref = str(budget_id or "").strip()
        if (
            not graph_ref
            or not budget_ref
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel success requires graph, revision, and budget identity",
            )
        for value, field_name in (
            (expected_graph_version, "expected_graph_version"),
            (expected_budget_version, "expected_budget_version"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"parallel success {field_name} must be a non-negative integer",
                )
        normalized_states = self._normalize_parallel_node_states(expected_node_states)
        if any(status != TaskNodeStatus.ACCEPTED.value for status, _ in normalized_states.values()):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel success requires every TaskNode to be ACCEPTED",
            )
        specs = self._normalize_acceptance_effect_intent_specs(effect_intents)
        try:
            decision = normalize_parallel_success_decision(decision_payload)
        except ParallelSuccessContractError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"parallel success decision is invalid: {exc}",
            ) from exc
        decision_reference = str(decision_ref or "").strip()
        result_node_id = str(decision.payload["result_node_id"])
        result_attempt_id = str(decision.payload["result_attempt_id"])
        expected_state_payload = {
            node_id: {"status": status, "status_version": version}
            for node_id, (status, version) in normalized_states.items()
        }
        expected_effects = sorted(
            (
                {
                    "effect_id": spec.effect_id,
                    "node_id": spec.node_id,
                    "attempt_id": spec.attempt_id,
                    "commit_scope": spec.commit_scope.value,
                    "effect_type": spec.effect_type,
                    "payload_ref": spec.payload_ref,
                    "payload_sha256": spec.payload_sha256,
                    "idempotency_key": spec.idempotency_key,
                }
                for spec in specs
            ),
            key=lambda item: item["effect_id"],
        )
        if any(
            spec.graph_id != graph_ref
            or spec.graph_revision != revision
            or spec.commit_scope is not AcceptanceEffectCommitScope.GRAPH
            or spec.node_id != result_node_id
            or spec.attempt_id != result_attempt_id
            for spec in specs
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel success effects must bind the explicit result authority",
            )
        required_evidence = {
            decision_reference,
            str(decision.payload["result_receipt_ref"]),
            *(spec.effect_id for spec in specs),
        }
        if (
            decision.decision_ref != decision_reference
            or decision.payload["graph_id"] != graph_ref
            or decision.payload["graph_revision"] != revision
            or decision.payload["graph_status_version_before"]
            != expected_graph_version
            or decision.payload["budget_id"] != budget_ref
            or decision.payload["budget_status_version_before"]
            != expected_budget_version
            or decision.payload["node_states"] != expected_state_payload
            or decision.payload["effect_intents"] != expected_effects
            or decision.payload["graph_outbox_id"] != graph_outbox.outbox_id
            or graph_outbox.graph_revision != revision
            or decision.payload["budget_outbox_id"] != budget_outbox.outbox_id
            or not required_evidence.issubset(set(graph_outbox.evidence_refs))
            or not required_evidence.issubset(set(budget_outbox.evidence_refs))
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel success decision does not bind the requested mutation",
            )

        ts_ms = self._now()
        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
            )
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (graph_ref, revision),
            ).fetchall()
            self._assert_parallel_node_states(
                node_rows,
                normalized_states,
                graph_id=graph_ref,
                revision=revision,
            )
            active_lease = conn.execute(
                """
                SELECT lease.lease_id
                FROM task_runtime_lease AS lease
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = lease.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                  AND lease.status = 'ACTIVE'
                LIMIT 1
                """,
                (graph_ref, revision),
            ).fetchone()
            active_attempt = conn.execute(
                """
                SELECT attempt_id FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ?
                  AND status IN (
                    'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE',
                    'CANCEL_REQUESTED'
                  )
                LIMIT 1
                """,
                (graph_ref, revision),
            ).fetchone()
            unresolved_reservation = conn.execute(
                """
                SELECT reservation.reservation_id
                FROM task_runtime_idempotency_reservation AS reservation
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = reservation.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                  AND reservation.status IN ('RESERVED', 'RECOVERY_REQUIRED')
                LIMIT 1
                """,
                (graph_ref, revision),
            ).fetchone()
            if active_lease is not None or active_attempt is not None or unresolved_reservation is not None:
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    "parallel success requires stable Attempts, closed Leases, and reconciled operations",
                )
            self._validate_parallel_success_attempt_lineage(
                conn,
                decision=decision,
                graph_id=graph_ref,
                revision=revision,
            )
            expected_effect_ids = {spec.effect_id for spec in specs}
            existing_graph_effect_ids = {
                str(row["effect_id"])
                for row in conn.execute(
                    """
                    SELECT effect_id FROM task_runtime_acceptance_effect_intent
                    WHERE graph_id = ? AND graph_revision = ?
                      AND commit_scope = 'GRAPH'
                    """,
                    (graph_ref, revision),
                ).fetchall()
            }
            if not existing_graph_effect_ids.issubset(expected_effect_ids):
                raise TaskStoreError(
                    TASK_STORE_OUTBOX_CONFLICT,
                    "parallel success Graph contains an unbound effect intent",
                )

            graph = self._require_graph(conn, graph_ref, revision)
            graph_record = _graph_record(graph, node_rows)
            budget = self._require_budget(conn, budget_ref)
            if (
                str(graph_record["graph"].get("result_node_id") or "")
                != result_node_id
                or str(graph_record["graph"].get("completion_policy") or "")
                != TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
            ):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "durable TaskGraph completion or result authority differs from the success decision",
                )
            if (
                str(budget["graph_id"] or "") != graph_ref
                or int(budget["graph_revision"] or 0) != revision
                or budget["node_id"] is not None
                or budget["attempt_id"] is not None
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel success budget is not the shared Graph reservation",
                )

            graph_replayed = (
                str(graph["status"]) == TaskGraphStatus.COMPLETED.value
                and int(graph["status_version"]) == expected_graph_version + 1
            )
            budget_replayed = (
                str(budget["status"]) == BudgetReservationStatus.CONSUMED.value
                and int(budget["status_version"]) == expected_budget_version + 1
            )
            if graph_replayed or budget_replayed:
                if not (graph_replayed and budget_replayed):
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "parallel success terminal Graph/Budget state is partially committed",
                    )
                self._assert_mutation_outbox_replay(
                    conn,
                    graph_outbox,
                    expected_aggregate=(
                        "TASK_GRAPH",
                        graph_ref,
                        expected_graph_version + 1,
                    ),
                    expected_event_type="TASK_GRAPH_COMPLETED",
                    expected_attempt_id=None,
                    expected_graph_revision=int(revision),
                )
                self._assert_mutation_outbox_replay(
                    conn,
                    budget_outbox,
                    expected_aggregate=(
                        "BUDGET_RESERVATION",
                        budget_ref,
                        expected_budget_version + 1,
                    ),
                    expected_event_type="BUDGET_RESERVATION_CONSUMED",
                    expected_attempt_id=None,
                )
                staged = tuple(
                    self._stage_acceptance_effect_intent_in_transaction(
                        conn,
                        spec,
                        ts_ms=ts_ms,
                        require_succeeded_attempt=True,
                        allow_insert=False,
                    )
                    for spec in specs
                )
                decision_record = self._assert_parallel_success_decision_replay(
                    conn,
                    decision=decision,
                    graph_version_before=expected_graph_version,
                    budget_version_before=expected_budget_version,
                    graph_outbox=graph_outbox,
                    budget_outbox=budget_outbox,
                )
                _assert_parallel_success_decision_bindings(conn, decision_record)
                return {
                    "graph": graph_record,
                    "budget": self._budget_record(budget),
                    "effect_intents": staged,
                    "decision": decision_record,
                    "replayed": True,
                }

            self._require_version(
                graph,
                expected_graph_version,
                subject=f"TaskGraph {graph_ref}@{revision}",
            )
            self._require_version(
                budget,
                expected_budget_version,
                subject=f"BudgetReservation {budget_ref}",
            )
            if str(graph["status"]) != TaskGraphStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel success requires an ACTIVE TaskGraph",
                )
            if str(budget["status"]) != BudgetReservationStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel success requires an ACTIVE shared Budget",
                )
            validate_budget_transition(
                BudgetReservationStatus.ACTIVE,
                BudgetReservationStatus.CONSUMED,
            )
            budget_cursor = conn.execute(
                """
                UPDATE task_runtime_budget_reservation
                SET status = 'CONSUMED', status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE budget_id = ? AND status_version = ? AND status = 'ACTIVE'
                """,
                (ts_ms, budget_ref, expected_budget_version),
            )
            self._require_cas(
                budget_cursor,
                subject=f"BudgetReservation {budget_ref}",
            )
            self._insert_outbox(
                conn,
                budget_outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "BUDGET_RESERVATION",
                    budget_ref,
                    expected_budget_version + 1,
                ),
                expected_event_type="BUDGET_RESERVATION_CONSUMED",
                expected_attempt_id=None,
            )
            completed_graph = self._transition_graph_in_transaction(
                conn,
                graph_id=graph_ref,
                revision=revision,
                expected_version=expected_graph_version,
                target_status=TaskGraphStatus.COMPLETED,
                outbox=graph_outbox,
                ts_ms=ts_ms,
            )
            staged = tuple(
                self._stage_acceptance_effect_intent_in_transaction(
                    conn,
                    spec,
                    ts_ms=ts_ms,
                    require_succeeded_attempt=True,
                    allow_terminal_graph_insert=True,
                )
                for spec in specs
            )
            decision_record = self._insert_parallel_success_decision(
                conn,
                decision=decision,
                graph_version_before=expected_graph_version,
                budget_version_before=expected_budget_version,
                graph_outbox=graph_outbox,
                budget_outbox=budget_outbox,
                ts_ms=ts_ms,
            )
            _assert_parallel_success_decision_bindings(conn, decision_record)
            return {
                "graph": completed_graph,
                "budget": self._budget_record(self._require_budget(conn, budget_ref)),
                "effect_intents": staged,
                "decision": decision_record,
                "replayed": False,
            }

    @staticmethod
    def _validate_parallel_success_attempt_lineage(
        conn: sqlite3.Connection,
        *,
        decision: CanonicalParallelSuccessDecision,
        graph_id: str,
        revision: int,
        require_latest: bool = True,
        reason_code: str = TASK_STORE_CAS_CONFLICT,
    ) -> None:
        for outcome in decision.payload["accepted_outcomes"]:
            node_id = str(outcome["node_id"])
            attempt_id = str(outcome["attempt_id"])
            receipt_ref = str(outcome["receipt_ref"])
            attempt = conn.execute(
                "SELECT * FROM task_runtime_attempt WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            latest = conn.execute(
                """
                SELECT attempt_id FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                ORDER BY attempt_no DESC, attempt_id DESC
                LIMIT 1
                """,
                (str(graph_id), int(revision), node_id),
            ).fetchone()
            if (
                attempt is None
                or (
                    require_latest
                    and (
                        latest is None
                        or str(latest["attempt_id"]) != attempt_id
                    )
                )
                or str(attempt["graph_id"]) != str(graph_id)
                or int(attempt["graph_revision"]) != int(revision)
                or str(attempt["node_id"]) != node_id
                or str(attempt["status"]) != AttemptStatus.SUCCEEDED.value
            ):
                raise TaskStoreError(
                    reason_code,
                    f"parallel success Attempt lineage drifted: {node_id}",
                )
            reservations = conn.execute(
                """
                SELECT * FROM task_runtime_idempotency_reservation
                WHERE attempt_id = ?
                ORDER BY reservation_id
                """,
                (attempt_id,),
            ).fetchall()
            if (
                len(reservations) != 1
                or str(reservations[0]["status"]) != IdempotencyStatus.COMMITTED.value
                or str(reservations[0]["receipt_ref"] or "") != receipt_ref
            ):
                raise TaskStoreError(
                    reason_code,
                    f"parallel success operation receipt drifted: {node_id}",
                )
            projection_row = conn.execute(
                """
                SELECT * FROM task_runtime_worker_projection
                WHERE projection_id = ?
                """,
                (receipt_ref,),
            ).fetchone()
            if projection_row is None:
                raise TaskStoreError(
                    reason_code,
                    f"parallel success worker projection is missing: {node_id}",
                )
            try:
                projection = validate_worker_projection_record(projection_row)
            except WorkerProjectionStoreError as exc:
                raise TaskStoreError(
                    reason_code,
                    f"parallel success worker projection is invalid: {node_id}: {exc}",
                ) from exc
            if (
                str(projection.get("graph_id") or "") != str(graph_id)
                or int(projection.get("graph_revision") or 0) != int(revision)
                or str(projection.get("node_id") or "") != node_id
                or str(projection.get("attempt_id") or "") != attempt_id
                or str(projection.get("decision") or "") != "APPROVE"
                or str(projection.get("payload_digest") or "")
                != str(outcome["payload_sha256"])
            ):
                raise TaskStoreError(
                    reason_code,
                    f"parallel success projection authority drifted: {node_id}",
                )

    @staticmethod
    def _insert_parallel_success_decision(
        conn: sqlite3.Connection,
        *,
        decision: CanonicalParallelSuccessDecision,
        graph_version_before: int,
        budget_version_before: int,
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage,
        ts_ms: int,
    ) -> dict[str, Any]:
        payload = decision.payload
        try:
            conn.execute(
                """
                INSERT INTO task_runtime_parallel_success_decision(
                    decision_ref, schema_version, graph_id, graph_revision,
                    graph_version_before, graph_version_after, budget_id,
                    budget_version_before, budget_version_after,
                    result_node_id, result_attempt_id, result_receipt_ref,
                    payload_json, payload_sha256, graph_outbox_id,
                    budget_outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_ref,
                    PARALLEL_SUCCESS_SCHEMA_VERSION,
                    payload["graph_id"],
                    payload["graph_revision"],
                    int(graph_version_before),
                    int(graph_version_before) + 1,
                    payload["budget_id"],
                    int(budget_version_before),
                    int(budget_version_before) + 1,
                    payload["result_node_id"],
                    payload["result_attempt_id"],
                    payload["result_receipt_ref"],
                    decision.payload_json,
                    decision.payload_sha256,
                    graph_outbox.outbox_id,
                    budget_outbox.outbox_id,
                    int(ts_ms),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "parallel success decision identity already exists",
            ) from exc
        row = conn.execute(
            """
            SELECT * FROM task_runtime_parallel_success_decision
            WHERE decision_ref = ?
            """,
            (decision.decision_ref,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "parallel success decision insert was not durable",
            )
        return _parallel_success_decision_record(row)

    @staticmethod
    def _assert_parallel_success_decision_replay(
        conn: sqlite3.Connection,
        *,
        decision: CanonicalParallelSuccessDecision,
        graph_version_before: int,
        budget_version_before: int,
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_parallel_success_decision
            WHERE decision_ref = ?
            """,
            (decision.decision_ref,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal Graph replay is missing its canonical success decision",
            )
        record = _parallel_success_decision_record(row)
        if (
            record["payload_json"] != decision.payload_json
            or record["payload_sha256"] != decision.payload_sha256
            or record["graph_version_before"] != int(graph_version_before)
            or record["graph_version_after"] != int(graph_version_before) + 1
            or record["budget_version_before"] != int(budget_version_before)
            or record["budget_version_after"] != int(budget_version_before) + 1
            or record["graph_outbox_id"] != graph_outbox.outbox_id
            or record["budget_outbox_id"] != budget_outbox.outbox_id
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "canonical parallel success decision replay drifted",
            )
        return record
