"""Read-only checkpoint Budget authority for Workspace abandonment."""

from __future__ import annotations

from collections.abc import Mapping
import hmac
import json
import sqlite3

from host_brain.contracts.agent_runtime.internal_budget_claim_result import (
    build_internal_budget_rejection_result_ref,
)
from host_brain.persistence.task_budget_usage_store import (
    _assert_terminal_budget_authority,
)
from host_brain.persistence.task_terminal_reconciliation_decision_authority import (
    canonical_current_parallel_terminal_decision,
)
from host_brain.persistence.task_store_types import TaskStoreError
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_record,
)


def allows_abandonment(
    conn: sqlite3.Connection,
    task_binding: sqlite3.Row,
    reason_code: str,
) -> bool:
    """Adapt one lifecycle Task binding to the exact Budget proof."""

    if str(reason_code) != "TERMINAL_BUDGET_REJECTED":
        return False
    try:
        return finalized_checkpoint_budget_rejection_allows_workspace_abandonment(
            conn,
            graph_id=str(task_binding["graph_id"]),
            graph_revision=int(task_binding["graph_revision"]),
            node_id=str(task_binding["node_id"]),
            attempt_id=str(task_binding["attempt_id"]),
            historical_lease_id=str(task_binding["lease_id"]),
            historical_fencing_token=int(task_binding["fencing_token"]),
        )
    except (IndexError, KeyError, TypeError, ValueError):
        return False


def finalized_checkpoint_budget_rejection_allows_workspace_abandonment(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    historical_lease_id: str,
    historical_fencing_token: int,
) -> bool:
    """Prove one checkpoint Budget terminal inside the Workspace CAS."""

    if (
        not graph_id
        or type(graph_revision) is not int
        or graph_revision < 1
        or not node_id
        or not attempt_id
        or not historical_lease_id
        or type(historical_fencing_token) is not int
        or historical_fencing_token < 1
    ):
        return False
    rows = conn.execute(
        """
        SELECT graph.status AS graph_status,
               graph.status_version AS graph_status_version,
               node.status AS node_status,
               node.status_version AS node_status_version,
               node.node_json,
               attempt.status AS attempt_status,
               attempt.status_version AS attempt_status_version,
               attempt.error_ref AS attempt_error_ref,
               attempt.owner_id AS attempt_owner_id,
               attempt.operation_scope,
               attempt.operation_fingerprint AS attempt_fingerprint,
               operation.reservation_id,
               operation.business_scope,
               operation.operation_fingerprint,
               operation.lease_id AS operation_lease_id,
               operation.owner_id AS operation_owner_id,
               operation.fencing_token AS operation_fencing_token,
               operation.status AS operation_status,
               operation.receipt_ref,
               budget.budget_id AS budget_id,
               budget.status AS budget_status,
               budget.status_version AS budget_status_version
        FROM task_runtime_graph AS graph
        JOIN task_runtime_node AS node
          ON node.graph_id = graph.graph_id
         AND node.graph_revision = graph.revision
         AND node.node_id = ?
        JOIN task_runtime_attempt AS attempt
          ON attempt.graph_id = graph.graph_id
         AND attempt.graph_revision = graph.revision
         AND attempt.node_id = node.node_id
         AND attempt.attempt_id = ?
        JOIN task_runtime_idempotency_reservation AS operation
          ON operation.attempt_id = attempt.attempt_id
        JOIN task_runtime_budget_reservation AS budget
          ON budget.graph_id = graph.graph_id
         AND budget.graph_revision = graph.revision
         AND budget.node_id IS NULL
         AND budget.attempt_id IS NULL
        WHERE graph.graph_id = ? AND graph.revision = ?
        """,
        (node_id, attempt_id, graph_id, graph_revision),
    ).fetchall()
    row = rows[0] if len(rows) == 1 else None
    if row is None:
        return False
    node_rows = conn.execute(
        """
        SELECT node_id, status, status_version
        FROM task_runtime_node
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY node_id
        """,
        (graph_id, graph_revision),
    ).fetchall()
    leases = conn.execute(
        """
        SELECT * FROM task_runtime_lease
        WHERE attempt_id = ?
        ORDER BY fencing_token, acquired_ts_ms, lease_id
        """,
        (attempt_id,),
    ).fetchall()
    commands = conn.execute(
        """
        SELECT * FROM host_command_journal
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY command_id
        """,
        (graph_id, graph_revision),
    ).fetchall()
    command = commands[0] if len(commands) == 1 else None
    projection = conn.execute(
        """
        SELECT * FROM task_runtime_worker_projection
        WHERE projection_id = ?
        """,
        (str(row["receipt_ref"] or ""),),
    ).fetchone()
    try:
        node_payload = json.loads(str(row["node_json"]))
        projection_record = (
            validate_worker_projection_record(projection)
            if projection is not None
            else None
        )
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        WorkerProjectionStoreError,
    ):
        return False
    historical_index = next(
        (
            index
            for index, lease in enumerate(leases)
            if str(lease["lease_id"]) == historical_lease_id
            and int(lease["fencing_token"] or 0) == historical_fencing_token
        ),
        -1,
    )
    if historical_index < 0:
        return False
    if (
        command is None
        or projection_record is None
        or not isinstance(node_payload, Mapping)
    ):
        return False
    plan_route = bool(
        str(node_payload.get("route") or "").startswith("skill:")
        and str(command["route_type"]) == "plan"
        and str(command["target_id"]) == "planning_search"
        and len(node_rows) >= 2
    )
    internal_route = bool(
        str(node_payload.get("route") or "") == "delegate:agent_runtime"
        and str(command["surface"]) == "internal"
        and str(command["route_type"]) == "delegate"
        and str(command["target_id"]) == "agent_runtime"
        and str(projection_record.get("route_type") or "") == "delegate"
        and str(projection_record.get("target_id") or "") == "agent_runtime"
        and str(projection_record.get("command_id") or "")
        == str(command["command_id"])
        and str(projection_record.get("request_id") or "")
        == str(command["request_id"])
        and len(node_rows) == 1
    )
    if (
        str(row["graph_status"]) != "BLOCKED"
        or str(row["node_status"]) != "FAILED"
        or str(row["attempt_status"]) != "FAILED"
        or not str(row["attempt_error_ref"] or "").strip()
        or str(row["operation_status"]) != "COMMITTED"
        or not str(row["receipt_ref"] or "").startswith("worker-projection://")
        or str(row["operation_lease_id"]) != historical_lease_id
        or int(row["operation_fencing_token"] or 0)
        != historical_fencing_token
        or str(row["operation_owner_id"])
        != str(leases[historical_index]["owner_id"])
    ):
        return False
    if (
        command is None
        or projection_record is None
        or not isinstance(node_payload, Mapping)
        or str(node_payload.get("node_id") or "") != node_id
        or not (plan_route or internal_route)
        or str(command["graph_id"]) != graph_id
        or int(command["graph_revision"]) != graph_revision
        or str(command["business_scope"]) != str(row["business_scope"])
        or str(row["business_scope"]) != str(row["operation_scope"])
        or str(row["operation_fingerprint"]) != str(row["attempt_fingerprint"])
        or str(projection_record["graph_id"]) != graph_id
        or int(projection_record["graph_revision"]) != graph_revision
        or str(projection_record["node_id"]) != node_id
        or str(projection_record["attempt_id"]) != attempt_id
        or str(projection_record["projection_id"]) != str(row["receipt_ref"])
        or not _lease_lineage_is_terminal(
            leases,
            historical_index=historical_index,
            attempt_owner_id=str(row["attempt_owner_id"]),
        )
    ):
        return False
    actual_states = {
        str(item["node_id"]): {
            "status": str(item["status"]),
            "status_version": int(item["status_version"]),
        }
        for item in node_rows
    }
    decision = (
        canonical_current_parallel_terminal_decision(
            conn,
            {
                "graph_id": graph_id,
                "revision": graph_revision,
                "status": str(row["graph_status"]),
                "status_version": int(row["graph_status_version"]),
            },
        )
        if plan_route
        else None
    )
    selected: list[dict[str, object]] = []
    if plan_route:
        if not isinstance(decision, dict):
            return False
        selected = [
            dict(item)
            for item in decision.get("outcomes") or ()
            if isinstance(item, dict)
            and str(item.get("node_id") or "") == node_id
            and str(item.get("attempt_id") or "") == attempt_id
        ]
        if (
            decision.get("decision_kind") != "BUDGET"
            or decision.get("selected_node_id") != node_id
            or decision.get("graph_target_status") != "BLOCKED"
            or decision.get("budget_target_status") != "RELEASED"
            or decision.get("budget_id") != str(row["budget_id"])
            or type(decision.get("budget_status_version_before")) is not int
            or int(decision["budget_status_version_before"]) + 1
            != int(row["budget_status_version"])
            or str(row["budget_status"]) != "RELEASED"
            or decision.get("node_states") != actual_states
            or len(selected) != 1
            or selected[0].get("status") != "BUDGET_BLOCKED"
        ):
            return False
        terminal_version = int(decision["budget_status_version_before"])
    else:
        if str(row["budget_status"]) != "RELEASED":
            return False
        terminal_version = int(row["budget_status_version"]) - 1
        if terminal_version < 1:
            return False
    try:
        budget_row = conn.execute(
            """
            SELECT * FROM task_runtime_budget_reservation
            WHERE budget_id = ?
            """,
            (str(row["budget_id"]),),
        ).fetchone()
        if budget_row is None:
            return False
        winner, _dimensions = _assert_terminal_budget_authority(
            conn,
            budget_row=budget_row,
            terminal_version=terminal_version,
        )
    except (TaskStoreError, KeyError, TypeError, ValueError):
        return False
    usage_id = str(winner["usage_id"])
    route_evidence_is_exact = (
        usage_id in tuple(selected[0].get("evidence_refs") or ())
        if plan_route
        else _internal_terminal_evidence_is_exact(
            conn,
            command=command,
            projection=projection_record,
            graph_id=graph_id,
            graph_revision=graph_revision,
            graph_status_version=int(row["graph_status_version"]),
            node_id=node_id,
            attempt_id=attempt_id,
            reservation_id=str(row["reservation_id"]),
            budget_id=str(row["budget_id"]),
            error_ref=str(row["attempt_error_ref"]),
            usage_id=usage_id,
        )
    )
    if (
        str(winner["node_id"]) != node_id
        or str(winner["attempt_id"]) != attempt_id
        or not route_evidence_is_exact
        or not _node_budget_binding_is_exact(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            parent_budget_id=str(row["budget_id"]),
            usage_id=usage_id,
            parent_outbox_id=str(winner["outbox_id"]),
        )
        or not _child_terminal_outboxes_are_exact(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            attempt_version=int(row["attempt_status_version"]),
            node_version=int(row["node_status_version"]),
            lease=leases[-1],
            error_ref=str(row["attempt_error_ref"]),
            usage_id=usage_id,
            internal_route=internal_route,
        )
        or not _zero_downstream_authority(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
        )
    ):
        return False
    return True


def _internal_terminal_evidence_is_exact(
    conn: sqlite3.Connection,
    *,
    command: sqlite3.Row,
    projection: Mapping[str, object],
    graph_id: str,
    graph_revision: int,
    graph_status_version: int,
    node_id: str,
    attempt_id: str,
    reservation_id: str,
    budget_id: str,
    error_ref: str,
    usage_id: str,
) -> bool:
    """Bind single-node Internal cleanup to its Graph and ingress terminal."""

    graph_outboxes = conn.execute(
        """
        SELECT * FROM task_runtime_outbox
        WHERE aggregate_type = 'TASK_GRAPH' AND aggregate_id = ?
          AND aggregate_version = ? AND event_type = 'TASK_GRAPH_BLOCKED'
        """,
        (graph_id, graph_status_version),
    ).fetchall()
    claim_ref = str(command["ingress_claim_ref"] or "").strip()
    claims = conn.execute(
        """
        SELECT * FROM secure_ingress_idempotency_claim
        WHERE claim_ref = ?
        """,
        (claim_ref,),
    ).fetchall()
    if len(graph_outboxes) != 1 or len(claims) != 1 or not claim_ref:
        return False
    graph_outbox = graph_outboxes[0]
    claim = claims[0]
    try:
        evidence_refs = tuple(json.loads(str(graph_outbox["evidence_refs_json"])))
        expected_result_ref = build_internal_budget_rejection_result_ref(
            command_id=str(command["command_id"]),
            ingress_claim_ref=claim_ref,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            budget_id=budget_id,
            usage_id=usage_id,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False
    return bool(
        str(graph_outbox["attempt_id"] or "") == attempt_id
        and error_ref in evidence_refs
        and usage_id in evidence_refs
        and str(claim["claim_status"]) == "COMMITTED"
        and hmac.compare_digest(
            str(claim["result_ref"] or ""),
            expected_result_ref,
        )
        and hmac.compare_digest(
            f"sha256:{str(claim['body_sha256'] or '')}",
            str(command["input_sha256"] or ""),
        )
        and str(projection.get("graph_id") or "") == graph_id
        and projection.get("graph_revision") == graph_revision
        and str(projection.get("node_id") or "") == node_id
        and str(projection.get("attempt_id") or "") == attempt_id
    )


def _lease_lineage_is_terminal(
    leases: list[sqlite3.Row],
    *,
    historical_index: int,
    attempt_owner_id: str,
) -> bool:
    if historical_index < 0 or not leases:
        return False
    tokens = [int(lease["fencing_token"] or 0) for lease in leases]
    return bool(
        all(
            right == left + 1
            for left, right in zip(tokens, tokens[1:], strict=False)
        )
        and all(
            str(lease["status"]) == "EXPIRED"
            and lease["released_ts_ms"] is not None
            for lease in leases[:-1]
        )
        and str(leases[-1]["status"]) == "RELEASED"
        and leases[-1]["released_ts_ms"] is not None
        and str(leases[-1]["owner_id"]) == attempt_owner_id
        and not any(str(lease["status"]) == "ACTIVE" for lease in leases)
    )


def _node_budget_binding_is_exact(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    parent_budget_id: str,
    usage_id: str,
    parent_outbox_id: str,
) -> bool:
    children = conn.execute(
        """
        SELECT * FROM task_runtime_node_budget_reservation
        WHERE parent_budget_id = ? AND attempt_id = ?
        """,
        (parent_budget_id, attempt_id),
    ).fetchall()
    bindings = conn.execute(
        """
        SELECT * FROM task_runtime_node_budget_usage
        WHERE parent_budget_id = ? AND usage_id = ?
        """,
        (parent_budget_id, usage_id),
    ).fetchall()
    child = children[0] if len(children) == 1 else None
    binding = bindings[0] if len(bindings) == 1 else None
    return bool(
        child is not None
        and binding is not None
        and str(child["graph_id"]) == graph_id
        and int(child["graph_revision"]) == graph_revision
        and str(child["node_id"]) == node_id
        and str(child["attempt_id"]) == attempt_id
        and str(binding["node_budget_id"]) == str(child["node_budget_id"])
        and str(binding["attempt_id"]) == attempt_id
        and bool(binding["accepted"]) is False
        and str(binding["parent_outbox_id"]) == parent_outbox_id
    )


def _child_terminal_outboxes_are_exact(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    attempt_version: int,
    node_version: int,
    lease: sqlite3.Row,
    error_ref: str,
    usage_id: str,
    internal_route: bool,
) -> bool:
    identities = (
        (
            "TASK_ATTEMPT",
            attempt_id,
            attempt_version,
            "TASK_ATTEMPT_FAILED",
            attempt_id,
            True,
        ),
        (
            "TASK_NODE",
            f"{graph_id}@{graph_revision}/{node_id}",
            node_version,
            "TASK_NODE_FAILED",
            attempt_id if internal_route else None,
            True,
        ),
        (
            "TASK_LEASE",
            str(lease["lease_id"]),
            int(lease["status_version"]),
            "TASK_LEASE_RELEASED_TERMINAL",
            attempt_id,
            internal_route,
        ),
    )
    for (
        aggregate_type,
        aggregate_id,
        aggregate_version,
        event_type,
        expected_attempt_id,
        require_evidence,
    ) in identities:
        rows = conn.execute(
            """
            SELECT * FROM task_runtime_outbox
            WHERE aggregate_type = ? AND aggregate_id = ?
              AND aggregate_version = ? AND event_type = ?
            """,
            (
                aggregate_type,
                aggregate_id,
                aggregate_version,
                event_type,
            ),
        ).fetchall()
        if len(rows) != 1:
            return False
        row = rows[0]
        try:
            evidence = tuple(json.loads(str(row["evidence_refs_json"])))
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        if (
            (row["attempt_id"] or None) != expected_attempt_id
            or (
                require_evidence
                and (error_ref not in evidence or usage_id not in evidence)
            )
        ):
            return False
    return True


def _zero_downstream_authority(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
) -> bool:
    counts = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM task_runtime_node_verification_decision
           WHERE graph_id = ? AND graph_revision = ? AND node_id = ?) AS verification,
          (SELECT COUNT(*) FROM task_runtime_node_acceptance_decision
           WHERE graph_id = ? AND graph_revision = ? AND node_id = ?) AS acceptance,
          (SELECT COUNT(*) FROM task_runtime_node_artifact
           WHERE graph_id = ? AND graph_revision = ? AND node_id = ?) AS artifacts,
          (SELECT COUNT(*) FROM task_runtime_acceptance_effect_intent
           WHERE graph_id = ? AND graph_revision = ? AND node_id = ?) AS effects,
          (SELECT COUNT(*) FROM task_runtime_graph_cancellation_intent
           WHERE graph_id = ? AND graph_revision = ?) AS cancellations,
          (SELECT COUNT(*) FROM task_runtime_worker_projection
           WHERE attempt_id = ?) AS projections
        """,
        (
            graph_id,
            graph_revision,
            node_id,
            graph_id,
            graph_revision,
            node_id,
            graph_id,
            graph_revision,
            node_id,
            graph_id,
            graph_revision,
            node_id,
            graph_id,
            graph_revision,
            attempt_id,
        ),
    ).fetchone()
    return bool(
        counts is not None
        # The committed checkpoint owns exactly one immutable projection;
        # every later authority must remain absent.
        and int(counts["projections"]) == 1
        and all(
            int(counts[key]) == 0
            for key in (
                "verification",
                "acceptance",
                "artifacts",
                "effects",
                "cancellations",
            )
        )
    )


__all__ = [
    "allows_abandonment",
    "finalized_checkpoint_budget_rejection_allows_workspace_abandonment",
]
