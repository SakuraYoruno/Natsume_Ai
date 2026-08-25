"""Graph-cancellation 0092 advance and final decision transactions."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from host_brain.contracts.agent_runtime.budget_policy import validate_budget_transition
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.persistence.task_record_codec import (
    _dumps,
    _graph_cancellation_decision_record,
    _graph_cancellation_intent_record,
    _graph_record,
    _node_record,
    _validated_node_payload,
    _validated_budget_record,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
)


class TaskGraphCancellationFinalizationMixin:
    """Advance stable fences and atomically commit the final cancellation."""

    def advance_graph_cancellation(self, intent_ref: str) -> dict[str, Any]:
        """Converge one requested Graph after local child cancellation/join."""

        self.assert_graph_cancellation_ready()
        self.assert_effect_reconciliation_ready()
        reference = str(intent_ref or "").strip()
        if not reference:
            raise TaskStoreError(TASK_STORE_INVALID, "cancellation intent_ref is required")
        ts_ms = self._now()
        cancelled_attempts: list[str] = []
        pending_attempts: list[str] = []
        pending_operations: list[str] = []
        pending_effects: list[str] = []
        with self._transaction() as conn:
            intent_row = conn.execute(
                "SELECT * FROM task_runtime_graph_cancellation_intent WHERE intent_ref = ?",
                (reference,),
            ).fetchone()
            if intent_row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"graph cancellation intent not found: {reference}",
                )
            intent = _graph_cancellation_intent_record(intent_row)
            graph_ref = str(intent["graph_id"])
            revision = int(intent["graph_revision"])
            if str(intent["status"]) == "FINALIZED":
                intent = self._validated_graph_cancellation_intent_record(
                    conn,
                    intent_row,
                )
                return {
                    "intent": intent,
                    "cancelled_attempt_ids": (),
                    "pending_attempt_ids": (),
                    "pending_operation_ids": (),
                    "pending_effect_ids": (),
                    "reconciliation_evidence_refs": self._graph_cancellation_evidence_refs(
                        conn,
                        intent_ref=reference,
                        graph_id=graph_ref,
                        revision=revision,
                    ),
                    "ready_to_finalize": True,
                    "replayed": True,
                }
            attempts = conn.execute(
                """
                SELECT * FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ?
                  AND status = 'CANCEL_REQUESTED'
                ORDER BY attempt_id
                """,
                (graph_ref, revision),
            ).fetchall()
            for attempt in attempts:
                attempt_id = str(attempt["attempt_id"])
                active_lease = conn.execute(
                    "SELECT lease_id FROM task_runtime_lease WHERE attempt_id = ? AND status = 'ACTIVE'",
                    (attempt_id,),
                ).fetchone()
                unresolved = conn.execute(
                    """
                    SELECT reservation_id FROM task_runtime_idempotency_reservation
                    WHERE attempt_id = ? AND status IN ('RESERVED','RECOVERY_REQUIRED')
                    ORDER BY reservation_id
                    """,
                    (attempt_id,),
                ).fetchall()
                if active_lease is not None or unresolved:
                    pending_attempts.append(attempt_id)
                    pending_operations.extend(str(row["reservation_id"]) for row in unresolved)
                    continue
                version = int(attempt["status_version"]) + 1
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_attempt
                    SET status = 'CANCELLED', owner_id = NULL,
                        status_version = status_version + 1,
                        completed_ts_ms = ?, updated_ts_ms = ?
                    WHERE attempt_id = ? AND status = 'CANCEL_REQUESTED'
                      AND status_version = ?
                    """,
                    (ts_ms, ts_ms, attempt_id, int(attempt["status_version"])),
                )
                self._require_cas(cursor, subject=f"Attempt {attempt_id}")
                outbox = self._cancellation_outbox(
                    intent_ref=reference,
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=version,
                    event_type="TASK_ATTEMPT_CANCELLED",
                    attempt_id=attempt_id,
                )
                self._insert_outbox(
                    conn,
                    outbox,
                    ts_ms=ts_ms,
                    expected_aggregate=("TASK_ATTEMPT", attempt_id, version),
                    expected_event_type="TASK_ATTEMPT_CANCELLED",
                    expected_attempt_id=attempt_id,
                )
                cancelled_attempts.append(attempt_id)
            fences = conn.execute(
                """
                SELECT fence.*, effect.status AS current_status
                FROM task_runtime_graph_cancellation_effect_fence AS fence
                JOIN task_runtime_acceptance_effect_intent AS effect
                  ON effect.effect_id = fence.effect_id
                WHERE fence.intent_ref = ?
                  AND fence.resolution_status IN (
                      'RECONCILIATION_REQUIRED', 'FAILED_EVIDENCE'
                  )
                ORDER BY fence.effect_id
                """,
                (reference,),
            ).fetchall()
            for fence in fences:
                current = str(fence["current_status"])
                resolution_status = str(fence["resolution_status"])
                if (
                    resolution_status == "RECONCILIATION_REQUIRED"
                    and current == "COMMITTED"
                ):
                    # A committed effect carries a durable receipt and is
                    # positive evidence that the side effect happened.  A
                    # FAILED effect is deliberately *not* symmetric: handler
                    # or transport failure may follow an already-performed
                    # side effect, and the current schema has no trusted
                    # no-side-effect attestation.  Such rows stay pending for
                    # explicit reconciliation.
                    conn.execute(
                        """
                        UPDATE task_runtime_graph_cancellation_effect_fence
                        SET resolution_status = 'COMMITTED_EVIDENCE', resolved_ts_ms = ?
                        WHERE effect_id = ? AND intent_ref = ?
                          AND resolution_status = 'RECONCILIATION_REQUIRED'
                        """,
                        (ts_ms, str(fence["effect_id"]), reference),
                    )
            pending_effects.extend(
                self._graph_cancellation_pending_effect_ids(
                    conn,
                    intent_ref=reference,
                )
            )
            evidence_refs = self._graph_cancellation_evidence_refs(
                conn,
                intent_ref=reference,
                graph_id=graph_ref,
                revision=revision,
            )
            return {
                "intent": intent,
                "cancelled_attempt_ids": tuple(cancelled_attempts),
                "pending_attempt_ids": tuple(sorted(set(pending_attempts))),
                "pending_operation_ids": tuple(sorted(set(pending_operations))),
                "pending_effect_ids": tuple(sorted(set(pending_effects))),
                "reconciliation_evidence_refs": evidence_refs,
                "ready_to_finalize": not (
                    pending_attempts or pending_operations or pending_effects
                ),
                "replayed": not cancelled_attempts,
            }

    def finalize_graph_cancellation(
        self,
        graph_id: str,
        revision: int,
        *,
        budget_id: str | None,
        expected_graph_version: int,
        expected_budget_version: int | None,
        expected_node_states: Mapping[str, Mapping[str, Any]],
        intent_ref: str,
        decision_ref: str,
        decision_payload: Mapping[str, Any],
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage | None,
    ) -> dict[str, Any]:
        """Atomically finalize a stable cancellation and its shared Budget."""

        self.assert_graph_cancellation_ready()
        self.assert_effect_reconciliation_ready()
        graph_ref = str(graph_id or "").strip()
        budget_ref = str(budget_id or "").strip() or None
        intent_reference = str(intent_ref or "").strip()
        decision_reference = str(decision_ref or "").strip()
        normalized_states = self._normalize_parallel_node_states(expected_node_states)
        try:
            decision = normalize_graph_cancellation_decision(decision_payload)
        except GraphCancellationContractError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"graph cancellation decision is invalid: {exc}",
            ) from exc
        expected_before = {
            node_id: {"status": status, "status_version": version}
            for node_id, (status, version) in normalized_states.items()
        }
        if (
            not graph_ref
            or int(revision) < 1
            or decision.decision_ref != decision_reference
            or decision.payload["intent_ref"] != intent_reference
            or decision.payload["graph_id"] != graph_ref
            or decision.payload["graph_revision"] != int(revision)
            or decision.payload["graph_status_version_before"] != int(expected_graph_version)
            or decision.payload["budget_id"] != budget_ref
            or decision.payload["budget_status_version_before"] != expected_budget_version
            or decision.payload["node_states_before"] != expected_before
            or decision.payload["graph_outbox_id"] != graph_outbox.outbox_id
            or graph_outbox.graph_revision != int(revision)
            or decision.payload["budget_outbox_id"]
            != (budget_outbox.outbox_id if budget_outbox is not None else None)
            or decision_reference not in set(graph_outbox.evidence_refs)
            or intent_reference not in set(graph_outbox.evidence_refs)
            or decision.payload["request_identity"]
            not in set(graph_outbox.evidence_refs)
            or (
                budget_outbox is not None
                and (
                    decision_reference not in set(budget_outbox.evidence_refs)
                    or intent_reference not in set(budget_outbox.evidence_refs)
                    or decision.payload["request_identity"]
                    not in set(budget_outbox.evidence_refs)
                )
            )
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "graph cancellation decision does not bind the final mutation",
            )

        ts_ms = self._now()
        with self._transaction() as conn:
            intent_row = conn.execute(
                "SELECT * FROM task_runtime_graph_cancellation_intent WHERE intent_ref = ?",
                (intent_reference,),
            ).fetchone()
            if intent_row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"graph cancellation intent not found: {intent_reference}",
                )
            intent = _graph_cancellation_intent_record(intent_row)
            if (
                intent["graph_id"] != graph_ref
                or intent["graph_revision"] != int(revision)
                or intent["payload"]["request_id"] != decision.payload["request_id"]
                or intent["payload"]["request_identity"]
                != decision.payload["request_identity"]
                or intent["budget_id"] != budget_ref
            ):
                raise TaskStoreError(
                    TASK_STORE_CANCELLATION_CONFLICT,
                    "cancellation decision conflicts with its durable intent",
                )
            graph = self._require_graph(conn, graph_ref, int(revision))
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
                """,
                (graph_ref, int(revision)),
            ).fetchall()
            budget_row = self._require_budget(conn, budget_ref) if budget_ref is not None else None
            existing_decision = conn.execute(
                """
                SELECT * FROM task_runtime_graph_cancellation_decision
                WHERE intent_ref = ? OR decision_ref = ?
                ORDER BY decision_ref LIMIT 1
                """,
                (intent_reference, decision_reference),
            ).fetchone()
            if existing_decision is not None or str(intent["status"]) == "FINALIZED":
                if existing_decision is None:
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "finalized cancellation intent is missing its decision",
                    )
                record = _graph_cancellation_decision_record(existing_decision)
                if (
                    record["decision_ref"] != decision_reference
                    or record["payload_json"] != decision.payload_json
                    or record["payload_sha256"] != decision.payload_sha256
                    or str(graph["status"]) != "CANCELLED"
                    or int(graph["status_version"]) != int(expected_graph_version) + 1
                ):
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "canonical cancellation decision replay drifted",
                    )
                self._assert_parallel_node_states(
                    node_rows,
                    {
                        node_id: (state["status"], state["status_version"])
                        for node_id, state in decision.payload["node_states_after"].items()
                    },
                    graph_id=graph_ref,
                    revision=int(revision),
                )
                self._assert_mutation_outbox_replay(
                    conn,
                    graph_outbox,
                    expected_aggregate=(
                        "TASK_GRAPH",
                        graph_ref,
                        int(expected_graph_version) + 1,
                    ),
                    expected_event_type="TASK_GRAPH_CANCELLED",
                    expected_attempt_id=None,
                    expected_graph_revision=int(revision),
                )
                if budget_ref is not None:
                    if budget_row is None:
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "cancellation replay is missing its Budget",
                        )
                    budget_changed = (
                        decision.payload["budget_status_before"]
                        != decision.payload["budget_target_status"]
                    )
                    expected_budget_after = int(expected_budget_version) + int(
                        budget_changed
                    )
                    if (
                        str(budget_row["status"])
                        != decision.payload["budget_target_status"]
                        or int(budget_row["status_version"]) != expected_budget_after
                    ):
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "cancellation replay Budget state drifted",
                        )
                    if budget_changed:
                        if budget_outbox is None:
                            raise TaskStoreError(
                                TASK_STORE_DURABLE_DATA_INVALID,
                                "cancellation replay is missing its Budget Outbox",
                            )
                        self._assert_mutation_outbox_replay(
                            conn,
                            budget_outbox,
                            expected_aggregate=(
                                "BUDGET_RESERVATION",
                                budget_ref,
                                expected_budget_after,
                            ),
                            expected_event_type=(
                                f"BUDGET_RESERVATION_{decision.payload['budget_target_status']}"
                            ),
                            expected_attempt_id=None,
                        )
                replay_evidence = self._graph_cancellation_evidence_refs(
                    conn,
                    intent_ref=intent_reference,
                    graph_id=graph_ref,
                    revision=int(revision),
                )
                if list(replay_evidence) != decision.payload[
                    "reconciliation_evidence_refs"
                ]:
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "cancellation replay reconciliation evidence drifted",
                    )
                self._assert_graph_cancellation_decision_trusted(conn, record)
                return {
                    "graph": _graph_record(graph, node_rows),
                    "budget": _validated_budget_record(budget_row) if budget_row is not None else None,
                    "nodes": tuple(_node_record(row) for row in node_rows),
                    "intent": intent,
                    "decision": record,
                    "replayed": True,
                }

            self._require_version(
                graph,
                expected_graph_version,
                subject=f"TaskGraph {graph_ref}@{int(revision)}",
            )
            if str(graph["status"]) != "BLOCKED":
                raise TaskStoreError(
                    TASK_STORE_CANCELLATION_CONFLICT,
                    "cancellation finalization requires its BLOCKED Graph epoch",
                )
            self._assert_parallel_node_states(
                node_rows,
                normalized_states,
                graph_id=graph_ref,
                revision=int(revision),
            )
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
            active_attempt = conn.execute(
                """
                SELECT attempt_id FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ?
                  AND status IN ('CREATED','LEASED','RUNNING','RECOVERABLE','CANCEL_REQUESTED')
                LIMIT 1
                """,
                (graph_ref, int(revision)),
            ).fetchone()
            active_lease = conn.execute(
                """
                SELECT lease.lease_id FROM task_runtime_lease AS lease
                JOIN task_runtime_attempt AS attempt ON attempt.attempt_id = lease.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                  AND lease.status = 'ACTIVE' LIMIT 1
                """,
                (graph_ref, int(revision)),
            ).fetchone()
            unresolved_operation = conn.execute(
                """
                SELECT reservation.reservation_id
                FROM task_runtime_idempotency_reservation AS reservation
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = reservation.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                  AND reservation.status IN ('RESERVED','RECOVERY_REQUIRED')
                LIMIT 1
                """,
                (graph_ref, int(revision)),
            ).fetchone()
            unresolved_effect_ids = self._graph_cancellation_pending_effect_ids(
                conn,
                intent_ref=intent_reference,
            )
            uncertain_effect = (
                unresolved_effect_ids[0] if unresolved_effect_ids else None
            )
            live_review = conn.execute(
                """
                SELECT review_id FROM agent_runtime_review
                WHERE task_graph_id = ?
                  AND substr(subject_ref, 1, length(?)) = ?
                  AND status IN ('PENDING','APPROVED') LIMIT 1
                """,
                (
                    graph_ref,
                    f"task-node://{quote(graph_ref, safe='')}/{int(revision)}/",
                    f"task-node://{quote(graph_ref, safe='')}/{int(revision)}/",
                ),
            ).fetchone()
            live_token = conn.execute(
                """
                SELECT token.token_digest
                FROM agent_runtime_review_token AS token
                JOIN agent_runtime_review AS review ON review.review_id = token.review_id
                WHERE review.task_graph_id = ?
                  AND substr(review.subject_ref, 1, length(?)) = ?
                  AND token.state = 'AVAILABLE' LIMIT 1
                """,
                (
                    graph_ref,
                    f"task-node://{quote(graph_ref, safe='')}/{int(revision)}/",
                    f"task-node://{quote(graph_ref, safe='')}/{int(revision)}/",
                ),
            ).fetchone()
            if any(
                item is not None
                for item in (
                    active_attempt,
                    active_lease,
                    unresolved_operation,
                    uncertain_effect,
                    live_review,
                    live_token,
                )
            ):
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    "cancellation awaits stable Attempts/Leases/operations/effects/reviews",
                )
            reconciliation_evidence = self._graph_cancellation_evidence_refs(
                conn,
                intent_ref=intent_reference,
                graph_id=graph_ref,
                revision=int(revision),
            )
            manifest_evidence = {
                decision.decision_ref,
                intent_reference,
                str(decision.payload["request_identity"]),
            }
            if (
                decision.payload["reconciliation_evidence_refs"]
                != list(reconciliation_evidence)
                or not manifest_evidence.issubset(
                    set(graph_outbox.evidence_refs)
                )
                or (
                    budget_outbox is not None
                    and not manifest_evidence.issubset(
                        set(budget_outbox.evidence_refs)
                    )
                )
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "cancellation decision/outboxes omit manifest authority evidence",
                )

            for row in node_rows:
                node_id = str(row["node_id"])
                target = decision.payload["node_states_after"][node_id]
                if target["status"] == str(row["status"]):
                    continue
                node_payload = _validated_node_payload(row)
                node_payload["status"] = target["status"]
                node_payload["status_version"] = target["status_version"]
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_node
                    SET status = 'CANCELLED', status_version = status_version + 1,
                        node_json = ?, updated_ts_ms = ?
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                      AND status = 'CANCEL_REQUESTED' AND status_version = ?
                    """,
                    (
                        _dumps(node_payload),
                        ts_ms,
                        graph_ref,
                        int(revision),
                        node_id,
                        int(row["status_version"]),
                    ),
                )
                self._require_cas(cursor, subject=f"TaskNode {node_id}")
                node_outbox = self._cancellation_outbox(
                    intent_ref=intent_reference,
                    aggregate_type="TASK_NODE",
                    aggregate_id=f"{graph_ref}@{int(revision)}/{node_id}",
                    aggregate_version=int(target["status_version"]),
                    event_type="TASK_NODE_CANCELLED",
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
                    expected_event_type="TASK_NODE_CANCELLED",
                    expected_attempt_id=None,
                )

            if budget_row is not None:
                self._require_version(
                    budget_row,
                    int(expected_budget_version),
                    subject=f"BudgetReservation {budget_ref}",
                )
                if (
                    str(budget_row["graph_id"] or "") != graph_ref
                    or int(budget_row["graph_revision"] or 0) != int(revision)
                    or str(budget_row["status"]) != decision.payload["budget_status_before"]
                ):
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "cancellation Budget snapshot drifted",
                    )
                budget_target = str(decision.payload["budget_target_status"])
                if budget_target != str(budget_row["status"]):
                    if budget_outbox is None:
                        raise TaskStoreError(
                            TASK_STORE_INVALID,
                            "Budget transition requires a cancellation Outbox",
                        )
                    validate_budget_transition(str(budget_row["status"]), budget_target)
                    cursor = conn.execute(
                        """
                        UPDATE task_runtime_budget_reservation
                        SET status = ?, status_version = status_version + 1,
                            updated_ts_ms = ?
                        WHERE budget_id = ? AND status_version = ? AND status = ?
                        """,
                        (
                            budget_target,
                            ts_ms,
                            budget_ref,
                            int(expected_budget_version),
                            str(budget_row["status"]),
                        ),
                    )
                    self._require_cas(cursor, subject=f"BudgetReservation {budget_ref}")
                    self._insert_outbox(
                        conn,
                        budget_outbox,
                        ts_ms=ts_ms,
                        expected_aggregate=(
                            "BUDGET_RESERVATION",
                            budget_ref,
                            int(expected_budget_version) + 1,
                        ),
                        expected_event_type=f"BUDGET_RESERVATION_{budget_target}",
                        expected_attempt_id=None,
                    )
            completed_graph = self._transition_graph_in_transaction(
                conn,
                graph_id=graph_ref,
                revision=int(revision),
                expected_version=int(expected_graph_version),
                target_status=TaskGraphStatus.CANCELLED,
                outbox=graph_outbox,
                ts_ms=ts_ms,
            )
            budget_version_after = (
                None
                if budget_row is None
                else int(expected_budget_version)
                + (
                    0
                    if decision.payload["budget_status_before"]
                    == decision.payload["budget_target_status"]
                    else 1
                )
            )
            conn.execute(
                """
                INSERT INTO task_runtime_graph_cancellation_decision(
                    decision_ref, schema_version, intent_ref, graph_id,
                    graph_revision, graph_version_before, graph_version_after,
                    budget_id, budget_version_before, budget_version_after,
                    payload_json, payload_sha256, graph_outbox_id,
                    budget_outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_reference,
                    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
                    intent_reference,
                    graph_ref,
                    int(revision),
                    int(expected_graph_version),
                    int(expected_graph_version) + 1,
                    budget_ref,
                    expected_budget_version,
                    budget_version_after,
                    decision.payload_json,
                    decision.payload_sha256,
                    graph_outbox.outbox_id,
                    budget_outbox.outbox_id if budget_outbox is not None else None,
                    ts_ms,
                ),
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_graph_cancellation_intent
                SET status = 'FINALIZED', status_version = status_version + 1,
                    finalized_ts_ms = ?
                WHERE intent_ref = ? AND status = 'REQUESTED' AND status_version = 0
                """,
                (ts_ms, intent_reference),
            )
            self._require_cas(cursor, subject=f"CancellationIntent {intent_reference}")
            final_nodes = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
                """,
                (graph_ref, int(revision)),
            ).fetchall()
            final_intent = conn.execute(
                "SELECT * FROM task_runtime_graph_cancellation_intent WHERE intent_ref = ?",
                (intent_reference,),
            ).fetchone()
            final_decision = conn.execute(
                "SELECT * FROM task_runtime_graph_cancellation_decision WHERE decision_ref = ?",
                (decision_reference,),
            ).fetchone()
            final_decision_record = _graph_cancellation_decision_record(
                final_decision
            )
            self._assert_graph_cancellation_decision_trusted(
                conn,
                final_decision_record,
            )
            return {
                "graph": completed_graph,
                "budget": (
                    _validated_budget_record(self._require_budget(conn, budget_ref))
                    if budget_ref is not None
                    else None
                ),
                "nodes": tuple(_node_record(row) for row in final_nodes),
                "intent": _graph_cancellation_intent_record(final_intent),
                "decision": final_decision_record,
                "replayed": False,
            }
