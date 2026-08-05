"""Pure terminal-state classification for reconciliation discovery."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from typing import Any

from host_brain.persistence.task_budget_usage_store import (
    _assert_terminal_budget_authority,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
    build_outbox_payload_ref,
)

from .task_terminal_reconciliation_common import _outbox_projection, _value_digest
from .task_terminal_cancellation_read import (
    cancelled_effects_are_exactly_fenced,
    cancelled_parallel_predecessor_is_exact,
)
from .task_terminal_reconciliation_decision_authority import (
    canonical_parallel_terminal_decision,
    graph_cancellation_authority_valid,
    parallel_success_authority_valid,
)
from .task_terminal_reconciliation_projection import (
    _ACTIVE_REVIEW_STATUSES,
    _has_exact_review_checkpoint,
    _one_outbox,
    _only_or_none,
    _quarantine,
    _selected_failure_lineage,
    _terminal_execution_lineage_valid,
)


_UNSETTLED_BUDGET_STATUSES = frozenset({"ACTIVE", "EXCEEDED"})
_FAILURE_NODE_STATUSES = frozenset(
    {"FAILED", "VERIFICATION_FAILED", "CANCELLED"}
)
_LIVE_ATTEMPT_STATUSES = frozenset(
    {"CREATED", "LEASED", "RUNNING", "RECOVERABLE", "CANCEL_REQUESTED"}
)
_UNRESOLVED_OPERATION_STATUSES = frozenset({"RESERVED", "RECOVERY_REQUIRED"})


class TaskTerminalReconciliationClassifierMixin:
    """Classify one already-opened canonical SQLite snapshot."""
    def _classify_terminal_observation(
        self,
        conn: sqlite3.Connection,
        observation: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        graph = observation["graph"]
        status = str(graph["status"])
        all_budgets = list(observation["budgets"])
        graph_budgets = [
            row
            for row in all_budgets
            if row.get("node_id") is None and row.get("attempt_id") is None
        ]
        child_budgets = [
            row
            for row in all_budgets
            if row.get("node_id") is not None or row.get("attempt_id") is not None
        ]
        nodes = list(observation["nodes"])
        attempts = list(observation["attempts"])
        leases = list(observation["leases"])
        operations = list(observation["operations"])
        reviews = list(observation["reviews"])
        effects = list(observation["effects"])
        failure_nodes = [row for row in nodes if row["status"] in _FAILURE_NODE_STATUSES]
        terminal_lineage_valid = _terminal_execution_lineage_valid(
            attempts,
            leases,
        )
        if child_budgets or len(graph_budgets) > 1:
            return _quarantine(
                "TERMINAL_GRAPH_BUDGET_BINDING_AMBIGUOUS",
                selected_budget=_only_or_none(child_budgets or graph_budgets),
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )
        if not graph_budgets:
            cancellation_rows = list(observation["cancellations"])
            cancellation_authority_valid = (
                status == "CANCELLED"
                and len(cancellation_rows) == 1
                and str(cancellation_rows[0].get("status") or "") == "FINALIZED"
                and graph_cancellation_authority_valid(
                    self,
                    conn,
                    graph,
                    None,
                )
            )
            prebudget_cancellation_exact = bool(
                cancellation_authority_valid
                and nodes
                and all(str(row.get("status") or "") == "CANCELLED" for row in nodes)
                and not attempts
                and not leases
                and not operations
                and not reviews
                and not effects
                and not observation["parallel_decisions"]
                and not observation["success_decisions"]
                and terminal_lineage_valid
                and cancelled_effects_are_exactly_fenced(
                    conn,
                    graph_id=str(graph["graph_id"]),
                    graph_revision=int(graph["revision"]),
                    intent_ref=str(cancellation_rows[0]["intent_ref"]),
                )
                and cancelled_parallel_predecessor_is_exact(
                    conn,
                    graph_id=str(graph["graph_id"]),
                    graph_revision=int(graph["revision"]),
                    intent_ref=str(cancellation_rows[0]["intent_ref"]),
                )
            )
            if prebudget_cancellation_exact:
                return None
            return _quarantine(
                "TERMINAL_GRAPH_BUDGET_BINDING_AMBIGUOUS",
                selected_budget=None,
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )
        budgets = graph_budgets
        budget = graph_budgets[0]
        unsettled = [
            row
            for row in budgets
            if row["status"] in _UNSETTLED_BUDGET_STATUSES
        ]
        live_conflict = (
            any(row["status"] in _LIVE_ATTEMPT_STATUSES for row in attempts)
            or any(row["status"] == "ACTIVE" for row in leases)
            or any(row["status"] in _UNRESOLVED_OPERATION_STATUSES for row in operations)
            or any(row["status"] in _ACTIVE_REVIEW_STATUSES for row in reviews)
            or bool(effects)
        )
        authority_conflict = bool(
            observation["parallel_decisions"]
            or observation["success_decisions"]
            or observation["cancellations"]
        )
        parallel_decision = canonical_parallel_terminal_decision(conn, graph)
        if status == "BLOCKED":
            if parallel_decision is not None:
                if (
                    parallel_decision["graph_target_status"] == "BLOCKED"
                    and self._parallel_terminal_snapshot_matches(
                        parallel_decision,
                        observation,
                    )
                ):
                    return None
                return _quarantine(
                    "BLOCKED_PARALLEL_DECISION_AFTER_STATE_DRIFTED",
                    selected_budget=budget,
                    selected_node=_only_or_none(failure_nodes),
                    selected_attempt=None,
                )
            if _has_exact_review_checkpoint(nodes, reviews) and not failure_nodes:
                non_review_live_conflict = (
                    any(row["status"] in _LIVE_ATTEMPT_STATUSES for row in attempts)
                    or any(row["status"] == "ACTIVE" for row in leases)
                    or any(
                        row["status"] in _UNRESOLVED_OPERATION_STATUSES
                        for row in operations
                    )
                    or bool(effects)
                )
                if (
                    not non_review_live_conflict
                    and not authority_conflict
                    and budget["status"] == "ACTIVE"
                    and terminal_lineage_valid
                ):
                    return None
                return _quarantine(
                    "BLOCKED_REVIEW_CHECKPOINT_HAS_CONFLICTING_AUTHORITY",
                    selected_budget=budget,
                    selected_node=_only_or_none(nodes),
                    selected_attempt=None,
                )
            if failure_nodes:
                selected_node, selected_attempt = _selected_failure_lineage(
                    failure_nodes,
                    attempts,
                )
                if (
                    selected_node is not None
                    and selected_attempt is not None
                    and not live_conflict
                    and not authority_conflict
                    and terminal_lineage_valid
                ):
                    if (
                        budget["status"] == "RELEASED"
                        and self._exact_checkpoint_budget_rejection_terminal(
                            conn,
                            observation=observation,
                            selected_node=selected_node,
                            selected_attempt=selected_attempt,
                            selected_budget=budget,
                        )
                    ):
                        return None
                    if budget["status"] in {"PENDING", "CONSUMED"}:
                        return _quarantine(
                            "BLOCKED_FAILURE_BUDGET_STATE_AMBIGUOUS",
                            selected_budget=budget,
                            selected_node=selected_node,
                            selected_attempt=selected_attempt,
                        )
                    release = budget["status"] in _UNSETTLED_BUDGET_STATUSES
                    if budget["status"] not in {
                        "ACTIVE",
                        "EXCEEDED",
                        "RELEASED",
                        "CANCELLED",
                    }:
                        return _quarantine(
                            "BLOCKED_FAILURE_BUDGET_STATE_AMBIGUOUS",
                            selected_budget=budget,
                            selected_node=selected_node,
                            selected_attempt=selected_attempt,
                        )
                    return {
                        "case_kind": "BLOCKED_FAILURE_NODE",
                        "classification": "SIGNED_REPAIR_REQUIRED",
                        "reason_code": "BLOCKED_GRAPH_HAS_DURABLE_FAILURE",
                        "recommended_action": (
                            "FAIL_GRAPH_AND_RELEASE_BUDGET" if release else "FAIL_GRAPH"
                        ),
                        "selected_budget": budget,
                        "selected_node": selected_node,
                        "selected_attempt": selected_attempt,
                        "failure_bundle": None,
                    }
            return _quarantine(
                "BLOCKED_TERMINAL_AUTHORITY_AMBIGUOUS",
                selected_budget=_only_or_none(unsettled or budgets),
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )

        if status == "FAILED":
            if not unsettled:
                parallel_valid = (
                    parallel_decision is not None
                    and parallel_decision["graph_target_status"] == "FAILED"
                    and self._parallel_terminal_snapshot_matches(
                        parallel_decision,
                        observation,
                    )
                )
                healthy_authority = (
                    not observation["parallel_decisions"] or parallel_valid
                ) and not observation["success_decisions"] and not observation[
                    "cancellations"
                ]
                if (
                    live_conflict
                    or not terminal_lineage_valid
                    or not healthy_authority
                    or budget["status"] != "RELEASED"
                    or not failure_nodes
                ):
                    return _quarantine(
                        "FAILED_TERMINAL_FACTS_AMBIGUOUS",
                        selected_budget=budget,
                        selected_node=_only_or_none(failure_nodes),
                        selected_attempt=None,
                    )
                return None
            selected_budget = _only_or_none(unsettled)
            bundle = self._exact_atomic_failure_bundle(conn, observation)
            exceeded_authority_valid = True
            if selected_budget is not None and selected_budget["status"] == "EXCEEDED":
                try:
                    budget_row = conn.execute(
                        "SELECT * FROM task_runtime_budget_reservation WHERE budget_id = ?",
                        (str(selected_budget["budget_id"]),),
                    ).fetchone()
                    if budget_row is None:
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "EXCEEDED Budget disappeared during terminal discovery",
                        )
                    _assert_terminal_budget_authority(
                        conn,
                        budget_row=budget_row,
                        terminal_version=int(selected_budget["status_version"]),
                    )
                except TaskStoreError:
                    exceeded_authority_valid = False
            if (
                selected_budget is not None
                and bundle is not None
                and not live_conflict
                and terminal_lineage_valid
                and not authority_conflict
                and not observation["operations"]
                and exceeded_authority_valid
            ):
                return {
                    "case_kind": "FAILED_UNSETTLED_BUDGET",
                    "classification": "EXACT_REPAIRABLE",
                    "reason_code": "EXACT_ATOMIC_FAILURE_BUNDLE_BUDGET_UNSETTLED",
                    "recommended_action": "RELEASE_BUDGET",
                    "selected_budget": selected_budget,
                    "selected_node": bundle["node"],
                    "selected_attempt": bundle["attempt"],
                    "failure_bundle": bundle,
                }
            return _quarantine(
                "FAILED_UNSETTLED_BUDGET_EVIDENCE_AMBIGUOUS",
                selected_budget=selected_budget,
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )

        if status == "CANCELLED":
            if not unsettled:
                cancellation_rows = list(observation["cancellations"])
                cancellation_authority_valid = (
                    len(cancellation_rows) == 1
                    and str(cancellation_rows[0].get("status") or "")
                    == "FINALIZED"
                    and graph_cancellation_authority_valid(
                        self,
                        conn,
                        graph,
                        budget,
                    )
                )
                cancellation_effects_fenced = (
                    cancellation_authority_valid
                    and cancelled_effects_are_exactly_fenced(
                        conn,
                        graph_id=str(graph["graph_id"]),
                        graph_revision=int(graph["revision"]),
                        intent_ref=str(cancellation_rows[0]["intent_ref"]),
                    )
                )
                cancellation_parallel_authority_valid = (
                    cancellation_authority_valid
                    and cancelled_parallel_predecessor_is_exact(
                        conn,
                        graph_id=str(graph["graph_id"]),
                        graph_revision=int(graph["revision"]),
                        intent_ref=str(cancellation_rows[0]["intent_ref"]),
                    )
                )
                cancellation_live_conflict = (
                    any(row["status"] in _LIVE_ATTEMPT_STATUSES for row in attempts)
                    or any(row["status"] == "ACTIVE" for row in leases)
                    or any(
                        row["status"] in _UNRESOLVED_OPERATION_STATUSES
                        for row in operations
                    )
                    or any(row["status"] in _ACTIVE_REVIEW_STATUSES for row in reviews)
                    or not cancellation_effects_fenced
                    or not cancellation_parallel_authority_valid
                )
                if (
                    cancellation_live_conflict
                    or not terminal_lineage_valid
                    or observation["success_decisions"]
                    or not cancellation_authority_valid
                    or budget["status"] not in {"RELEASED", "CANCELLED"}
                ):
                    return _quarantine(
                        "CANCELLED_TERMINAL_FACTS_AMBIGUOUS",
                        selected_budget=budget,
                        selected_node=_only_or_none(failure_nodes),
                        selected_attempt=None,
                    )
                return None
            selected_budget = _only_or_none(unsettled)
            if (
                selected_budget is not None
                and not observation["cancellations"]
                and not live_conflict
                and terminal_lineage_valid
                and not observation["parallel_decisions"]
                and not observation["success_decisions"]
            ):
                selected_node, selected_attempt = _selected_failure_lineage(
                    failure_nodes,
                    attempts,
                )
                return {
                    "case_kind": "CANCELLED_UNSETTLED_BUDGET",
                    "classification": "SIGNED_REPAIR_REQUIRED",
                    "reason_code": "LEGACY_CANCELLED_GRAPH_BUDGET_UNSETTLED",
                    "recommended_action": "RELEASE_BUDGET",
                    "selected_budget": selected_budget,
                    "selected_node": selected_node,
                    "selected_attempt": selected_attempt,
                    "failure_bundle": None,
                }
            return _quarantine(
                "CANCELLED_TERMINAL_AUTHORITY_AMBIGUOUS",
                selected_budget=selected_budget,
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )

        if status == "COMPLETED":
            completed_live_conflict = (
                any(row["status"] in _LIVE_ATTEMPT_STATUSES for row in attempts)
                or any(row["status"] == "ACTIVE" for row in leases)
                or any(
                    row["status"] in _UNRESOLVED_OPERATION_STATUSES
                    for row in operations
                )
                or any(row["status"] in _ACTIVE_REVIEW_STATUSES for row in reviews)
            )
            consistent = (
                budget["status"] == "CONSUMED"
                and bool(nodes)
                and all(row["status"] == "ACCEPTED" for row in nodes)
                and not completed_live_conflict
                and terminal_lineage_valid
                and not observation["parallel_decisions"]
                and not observation["cancellations"]
                and parallel_success_authority_valid(self, conn, graph)
            )
            if consistent:
                return None
            return {
                "case_kind": "COMPLETED_INCONSISTENT",
                "classification": "QUARANTINED_STRUCTURAL",
                "reason_code": "COMPLETED_GRAPH_TERMINAL_FACTS_INCONSISTENT",
                "recommended_action": "NONE",
                "selected_budget": _only_or_none(budgets),
                "selected_node": None,
                "selected_attempt": None,
                "failure_bundle": None,
            }
        return None

    def _exact_checkpoint_budget_rejection_terminal(
        self,
        conn: sqlite3.Connection,
        *,
        observation: Mapping[str, Any],
        selected_node: Mapping[str, Any],
        selected_attempt: Mapping[str, Any],
        selected_budget: Mapping[str, Any],
    ) -> bool:
        """Recognize any exact atomic checkpoint Budget terminal authority."""
        try:
            resolved = self._try_resolve_checkpoint_budget_rejection_terminal_uow(
                conn,
                str(selected_attempt["attempt_id"]),
            )
            if resolved is None:
                return False
            graph = resolved["graph"]
            node = resolved["node"]
            attempt = resolved["attempt"]
            budget = resolved["budget"]
            exact = (
                str(graph["graph_id"]) == str(observation["graph"]["graph_id"])
                and int(graph["revision"])
                == int(observation["graph"]["revision"])
                and str(node["node_id"]) == str(selected_node["node_id"])
                and int(node["status_version"])
                == int(selected_node["status_version"])
                and int(attempt["status_version"])
                == int(selected_attempt["status_version"])
                and str(budget["budget_id"])
                == str(selected_budget["budget_id"])
                and int(budget["status_version"])
                == int(selected_budget["status_version"])
            )
        except TaskStoreError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal projection is invalid",
            ) from exc
        if not exact:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal reconciliation identity drifted",
            )
        return True

    def _parallel_terminal_snapshot_matches(
        decision: Mapping[str, Any],
        observation: Mapping[str, Any],
    ) -> bool:
        budgets = [
            row
            for row in observation["budgets"]
            if row.get("node_id") is None and row.get("attempt_id") is None
        ]
        if len(budgets) != 1:
            return False
        budget = budgets[0]
        budget_target = decision.get("budget_target_status")
        expected_budget_status = (
            str(budget_target)
            if budget_target is not None
            else str(decision["budget_status_before"])
        )
        expected_budget_version = int(decision["budget_status_version_before"]) + (
            1 if budget_target is not None else 0
        )
        current_nodes = {
            str(row["node_id"]): {
                "status": str(row["status"]),
                "status_version": int(row["status_version"]),
            }
            for row in observation["nodes"]
        }
        expected_nodes = {
            str(node_id): {
                "status": str(value["status"]),
                "status_version": int(value["status_version"]),
            }
            for node_id, value in dict(decision["node_states"]).items()
        }
        live_execution = (
            any(
                row["status"] in _LIVE_ATTEMPT_STATUSES
                for row in observation["attempts"]
            )
            or any(row["status"] == "ACTIVE" for row in observation["leases"])
            or any(
                row["status"] in _UNRESOLVED_OPERATION_STATUSES
                for row in observation["operations"]
            )
            or bool(observation["effects"])
        )
        decision_kind = str(decision["decision_kind"])
        if decision_kind == "REVIEW":
            reviews_valid = _has_exact_review_checkpoint(
                list(observation["nodes"]),
                list(observation["reviews"]),
            )
        else:
            reviews_valid = not any(
                row["status"] in _ACTIVE_REVIEW_STATUSES
                for row in observation["reviews"]
            )
        return bool(
            str(budget["budget_id"]) == str(decision["budget_id"])
            and str(budget["status"]) == expected_budget_status
            and int(budget["status_version"]) == expected_budget_version
            and current_nodes == expected_nodes
            and not live_execution
            and _terminal_execution_lineage_valid(
                list(observation["attempts"]),
                list(observation["leases"]),
            )
            and reviews_valid
            and not observation["success_decisions"]
            and not observation["cancellations"]
        )

    def _exact_atomic_failure_bundle(
        self,
        conn: sqlite3.Connection,
        observation: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        graph = observation["graph"]
        failures = [
            row for row in observation["nodes"] if row["status"] == "FAILED"
        ]
        selected_node, selected_attempt = _selected_failure_lineage(
            failures,
            observation["attempts"],
        )
        if selected_node is None or selected_attempt is None:
            return None
        if selected_attempt["status"] != "FAILED" or not selected_attempt.get("error_ref"):
            return None
        attempt_id = str(selected_attempt["attempt_id"])
        terminal_ts = selected_attempt.get("completed_ts_ms")
        if terminal_ts is None or any(
            int(value) != int(terminal_ts)
            for value in (
                selected_attempt["updated_ts_ms"],
                selected_node["updated_ts_ms"],
                graph["updated_ts_ms"],
            )
        ):
            return None
        matched_leases = [
            row
            for row in observation["leases"]
            if row["attempt_id"] == attempt_id
            and row["owner_id"] == selected_attempt["owner_id"]
        ]
        if len(matched_leases) != 1:
            return None
        lease = matched_leases[0]
        if (
            lease["status"] != "RELEASED"
            or lease.get("released_ts_ms") is None
            or int(lease["released_ts_ms"]) != int(terminal_ts)
        ):
            return None
        if any(row["attempt_id"] == attempt_id for row in observation["operations"]):
            return None
        expected_rows = {
            "attempt": _one_outbox(
                conn,
                aggregate_type="TASK_ATTEMPT",
                aggregate_id=attempt_id,
                aggregate_version=int(selected_attempt["status_version"]),
                event_type="TASK_ATTEMPT_FAILED",
                graph_revision=None,
            ),
            "node": _one_outbox(
                conn,
                aggregate_type="TASK_NODE",
                aggregate_id=(
                    f"{graph['graph_id']}@{graph['revision']}/{selected_node['node_id']}"
                ),
                aggregate_version=int(selected_node["status_version"]),
                event_type="TASK_NODE_FAILED",
                graph_revision=None,
            ),
            "graph": _one_outbox(
                conn,
                aggregate_type="TASK_GRAPH",
                aggregate_id=str(graph["graph_id"]),
                aggregate_version=int(graph["status_version"]),
                event_type="TASK_GRAPH_FAILED",
                graph_revision=int(graph["revision"]),
            ),
            "lease": _one_outbox(
                conn,
                aggregate_type="TASK_LEASE",
                aggregate_id=str(lease["lease_id"]),
                aggregate_version=int(lease["status_version"]),
                event_type="TASK_LEASE_RELEASED_TERMINAL",
                graph_revision=None,
            ),
        }
        if any(value is None for value in expected_rows.values()):
            return None
        outboxes = {name: _outbox_projection(row) for name, row in expected_rows.items()}
        evidence = tuple(outboxes["attempt"]["evidence_refs"])
        if (
            str(selected_attempt["error_ref"]) not in evidence
            or any(tuple(item["evidence_refs"]) != evidence for item in outboxes.values())
            or any(item["attempt_id"] != attempt_id for item in outboxes.values())
            or any(int(item["created_ts_ms"]) != int(terminal_ts) for item in outboxes.values())
            or any(
                item["payload_ref"]
                != build_outbox_payload_ref(
                    aggregate_type=item["aggregate_type"],
                    aggregate_id=item["aggregate_id"],
                    aggregate_version=item["aggregate_version"],
                    graph_revision=item["graph_revision"],
                )
                for item in outboxes.values()
            )
        ):
            return None
        deterministic = {
            "node": (
                f"attempt-terminal-node:{attempt_id}:"
                f"{graph['graph_id']}@{graph['revision']}/{selected_node['node_id']}:"
                f"{selected_node['status_version']}"
            ),
            "graph": (
                f"attempt-terminal-graph:{attempt_id}:{graph['graph_id']}:"
                f"{graph['status_version']}"
            ),
            "lease": (
                f"attempt-terminal-lease:{attempt_id}:{lease['lease_id']}:"
                f"{lease['status_version']}"
            ),
        }
        for kind, key in deterministic.items():
            if (
                outboxes[kind]["idempotency_key"] != key
                or outboxes[kind]["outbox_id"] != f"outbox::{key}"
                or outboxes[kind]["payload_ref"]
                != build_outbox_payload_ref(
                    aggregate_type=outboxes[kind]["aggregate_type"],
                    aggregate_id=outboxes[kind]["aggregate_id"],
                    aggregate_version=outboxes[kind]["aggregate_version"],
                    graph_revision=outboxes[kind]["graph_revision"],
                )
            ):
                return None
        return {
            "node": selected_node,
            "attempt": selected_attempt,
            "lease": lease,
            "outboxes": outboxes,
            "evidence_refs": list(evidence),
            "bundle_digest": _value_digest(outboxes),
        }
