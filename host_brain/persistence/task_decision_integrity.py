"""Read-only durable decision binding checks for TaskStore.

Every function receives the caller's existing SQLite connection and executes
only ``SELECT`` statements against the same transaction snapshot.  This module
does not create connections, begin or commit transactions, write rows, or own a
clock.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable, Mapping

from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.persistence.task_record_codec import (
    _acceptance_effect_record,
    _budget_usage_record,
    _graph_cancellation_intent_record,
    _graph_record,
    _outbox_record,
    _validated_budget_record,
)
from host_brain.persistence.task_budget_usage_store import (
    _assert_terminal_budget_authority,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_BUDGET_ALREADY_EXCEEDED,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


def _assert_graph_cancellation_decision_bindings(
    conn: sqlite3.Connection,
    record: Mapping[str, Any],
    *,
    evidence_refs_builder: Callable[..., tuple[str, ...]],
) -> None:
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "cancellation decision payload is missing",
        )
    graph_id = str(record["graph_id"])
    revision = int(record["graph_revision"])
    intent = conn.execute(
        """
        SELECT * FROM task_runtime_graph_cancellation_intent
        WHERE intent_ref = ? AND graph_id = ? AND graph_revision = ?
        """,
        (str(record["intent_ref"]), graph_id, revision),
    ).fetchone()
    graph = conn.execute(
        "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
        (graph_id, revision),
    ).fetchone()
    nodes = conn.execute(
        """
        SELECT node_id, status, status_version FROM task_runtime_node
        WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
        """,
        (graph_id, revision),
    ).fetchall()
    if intent is None or graph is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "cancellation decision is missing its intent or terminal Graph",
        )
    intent_record = _graph_cancellation_intent_record(intent)
    if (
        str(intent_record["status"]) != "FINALIZED"
        or int(intent_record["status_version"]) != 1
        or str(graph["status"]) != "CANCELLED"
        or int(graph["status_version"]) != int(record["graph_version_after"])
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "cancellation terminal intent/Graph binding drifted",
        )
    actual_states = {
        str(row["node_id"]): {
            "status": str(row["status"]),
            "status_version": int(row["status_version"]),
        }
        for row in nodes
    }
    if actual_states != payload["node_states_after"]:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "cancellation terminal Node snapshot drifted",
        )
    budget_id = record.get("budget_id")
    if budget_id is not None:
        budget = conn.execute(
            "SELECT * FROM task_runtime_budget_reservation WHERE budget_id = ?",
            (str(budget_id),),
        ).fetchone()
        if (
            budget is None
            or str(budget["graph_id"] or "") != graph_id
            or int(budget["graph_revision"] or 0) != revision
            or str(budget["status"]) != payload["budget_target_status"]
            or int(budget["status_version"]) != int(record["budget_version_after"])
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "cancellation terminal Budget binding drifted",
            )
    graph_outbox_row = conn.execute(
        "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
        (str(record["graph_outbox_id"]),),
    ).fetchone()
    budget_outbox_row = (
        conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (str(record["budget_outbox_id"]),),
        ).fetchone()
        if record.get("budget_outbox_id") is not None
        else None
    )
    if graph_outbox_row is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "cancellation terminal Graph Outbox is missing",
        )
    graph_outbox = _outbox_record(graph_outbox_row)
    required_evidence = {
        str(record["decision_ref"]),
        str(record["intent_ref"]),
        str(payload["request_identity"]),
    }
    if (
        graph_outbox["aggregate_type"] != "TASK_GRAPH"
        or graph_outbox["aggregate_id"] != graph_id
        or int(graph_outbox.get("graph_revision") or 0) != revision
        or int(graph_outbox["aggregate_version"]) != int(record["graph_version_after"])
        or graph_outbox["event_type"] != "TASK_GRAPH_CANCELLED"
        or not required_evidence.issubset(set(graph_outbox["evidence_refs"]))
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "cancellation terminal Graph Outbox binding drifted",
        )
    if record.get("budget_outbox_id") is not None:
        if budget_outbox_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "cancellation terminal Budget Outbox is missing",
            )
        budget_outbox = _outbox_record(budget_outbox_row)
        if (
            budget_outbox["aggregate_type"] != "BUDGET_RESERVATION"
            or budget_outbox["aggregate_id"] != str(budget_id)
            or int(budget_outbox["aggregate_version"])
            != int(record["budget_version_after"])
            or budget_outbox["event_type"]
            != f"BUDGET_RESERVATION_{payload['budget_target_status']}"
            or not required_evidence.issubset(set(budget_outbox["evidence_refs"]))
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "cancellation terminal Budget Outbox binding drifted",
            )
    evidence = evidence_refs_builder(
        conn,
        intent_ref=str(record["intent_ref"]),
        graph_id=graph_id,
        revision=revision,
    )
    if list(evidence) != payload["reconciliation_evidence_refs"]:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "cancellation reconciliation evidence drifted",
        )


def _assert_parallel_success_decision_bindings(
    conn: sqlite3.Connection,
    record: Mapping[str, Any],
) -> None:
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success decision payload is missing",
        )
    decision_ref = str(record.get("decision_ref") or "")
    graph_row = conn.execute(
        """
        SELECT * FROM task_runtime_graph
        WHERE graph_id = ? AND revision = ?
        """,
        (str(record["graph_id"]), int(record["graph_revision"])),
    ).fetchone()
    node_rows = conn.execute(
        """
        SELECT * FROM task_runtime_node
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY node_id
        """,
        (str(record["graph_id"]), int(record["graph_revision"])),
    ).fetchall()
    budget_state_row = conn.execute(
        "SELECT * FROM task_runtime_budget_reservation WHERE budget_id = ?",
        (str(record["budget_id"]),),
    ).fetchone()
    if graph_row is None or budget_state_row is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success is missing its terminal Graph or Budget",
        )
    graph = _graph_record(graph_row, node_rows)
    budget = _validated_budget_record(budget_state_row)
    if (
        str(graph_row["status"]) != TaskGraphStatus.COMPLETED.value
        or int(graph_row["status_version"]) != int(record["graph_version_after"])
        or str(graph["graph"].get("result_node_id") or "")
        != str(record["result_node_id"])
        or str(graph["graph"].get("completion_policy") or "")
        != str(payload["completion_policy"])
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success terminal Graph binding drifted",
        )
    expected_node_states = payload["node_states"]
    actual_node_states = {
        str(row["node_id"]): {
            "status": str(row["status"]),
            "status_version": int(row["status_version"]),
        }
        for row in node_rows
    }
    if actual_node_states != expected_node_states:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success terminal Node snapshot drifted",
        )
    if (
        budget["status"] != BudgetReservationStatus.CONSUMED.value
        or int(budget["status_version"]) != int(record["budget_version_after"])
        or str(budget.get("graph_id") or "") != str(record["graph_id"])
        or int(budget.get("graph_revision") or 0) != int(record["graph_revision"])
        or budget.get("node_id") is not None
        or budget.get("attempt_id") is not None
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success terminal Budget binding drifted",
        )
    required_evidence = {
        decision_ref,
        str(record.get("result_receipt_ref") or ""),
        *(str(item["effect_id"]) for item in payload["effect_intents"]),
    }
    graph_outbox_row = conn.execute(
        "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
        (str(record.get("graph_outbox_id") or ""),),
    ).fetchone()
    budget_outbox_row = conn.execute(
        "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
        (str(record.get("budget_outbox_id") or ""),),
    ).fetchone()
    if graph_outbox_row is None or budget_outbox_row is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success decision is missing a state Outbox",
        )
    graph_outbox = _outbox_record(graph_outbox_row)
    budget_outbox = _outbox_record(budget_outbox_row)
    if (
        graph_outbox["aggregate_type"] != "TASK_GRAPH"
        or graph_outbox["aggregate_id"] != record["graph_id"]
        or int(graph_outbox.get("graph_revision") or 0)
        != int(record["graph_revision"])
        or int(graph_outbox["aggregate_version"])
        != int(record["graph_version_after"])
        or graph_outbox["event_type"] != "TASK_GRAPH_COMPLETED"
        or graph_outbox["attempt_id"] is not None
        or not required_evidence.issubset(set(graph_outbox["evidence_refs"]))
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success Graph Outbox binding drifted",
        )
    if (
        budget_outbox["aggregate_type"] != "BUDGET_RESERVATION"
        or budget_outbox["aggregate_id"] != record["budget_id"]
        or int(budget_outbox["aggregate_version"])
        != int(record["budget_version_after"])
        or budget_outbox["event_type"] != "BUDGET_RESERVATION_CONSUMED"
        or budget_outbox["attempt_id"] is not None
        or not required_evidence.issubset(set(budget_outbox["evidence_refs"]))
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success Budget Outbox binding drifted",
        )
    expected_effect_ids = {
        str(expected["effect_id"]) for expected in payload["effect_intents"]
    }
    actual_effect_ids = {
        str(row["effect_id"])
        for row in conn.execute(
            """
            SELECT effect_id FROM task_runtime_acceptance_effect_intent
            WHERE graph_id = ? AND graph_revision = ? AND commit_scope = 'GRAPH'
            """,
            (str(record["graph_id"]), int(record["graph_revision"])),
        ).fetchall()
    }
    if actual_effect_ids != expected_effect_ids:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel success Graph effect set drifted",
        )
    for expected in payload["effect_intents"]:
        effect_row = conn.execute(
            """
            SELECT * FROM task_runtime_acceptance_effect_intent
            WHERE effect_id = ?
            """,
            (str(expected["effect_id"]),),
        ).fetchone()
        if effect_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"parallel success effect is missing: {expected['effect_id']}",
            )
        effect = _acceptance_effect_record(effect_row)
        if (
            effect["graph_id"] != record["graph_id"]
            or int(effect["graph_revision"]) != int(record["graph_revision"])
            or effect["node_id"] != expected["node_id"]
            or effect["attempt_id"] != expected["attempt_id"]
            or effect["commit_scope"] != expected["commit_scope"]
            or effect["effect_type"] != expected["effect_type"]
            or effect["payload_ref"] != expected["payload_ref"]
            or effect["payload_sha256"] != expected["payload_sha256"]
            or effect["idempotency_key"] != expected["idempotency_key"]
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"parallel success effect binding drifted: {expected['effect_id']}",
            )


def _assert_parallel_settlement_decision_outbox_binding(
    conn: sqlite3.Connection,
    record: Mapping[str, Any],
) -> None:
    decision_ref = str(record.get("decision_ref") or "")
    graph_row = conn.execute(
        "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
        (str(record.get("graph_outbox_id") or ""),),
    ).fetchone()
    if graph_row is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel settlement decision is missing its Graph Outbox",
        )
    graph_outbox = _outbox_record(graph_row)
    if (
        graph_outbox["aggregate_type"] != "TASK_GRAPH"
        or graph_outbox["aggregate_id"] != record["graph_id"]
        or int(graph_outbox.get("graph_revision") or 0)
        != int(record["graph_revision"])
        or int(graph_outbox["aggregate_version"])
        != int(record["graph_version_after"])
        or graph_outbox["event_type"]
        != f"TASK_GRAPH_{record['graph_target_status']}"
        or graph_outbox["attempt_id"] is not None
        or decision_ref not in graph_outbox["evidence_refs"]
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel settlement Graph Outbox binding drifted",
        )

    budget_outbox_id = str(record.get("budget_outbox_id") or "").strip() or None
    if record.get("budget_target_status") is None:
        if budget_outbox_id is not None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "review settlement unexpectedly binds a Budget Outbox",
            )
        return
    budget_row = conn.execute(
        "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
        (budget_outbox_id,),
    ).fetchone()
    if budget_row is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel settlement decision is missing its Budget Outbox",
        )
    budget_outbox = _outbox_record(budget_row)
    if (
        budget_outbox["aggregate_type"] != "BUDGET_RESERVATION"
        or budget_outbox["aggregate_id"] != record["budget_id"]
        or int(budget_outbox["aggregate_version"])
        != int(record["budget_version_after"])
        or budget_outbox["event_type"] != "BUDGET_RESERVATION_RELEASED"
        or budget_outbox["attempt_id"] is not None
        or decision_ref not in budget_outbox["evidence_refs"]
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel settlement Budget Outbox binding drifted",
        )
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "parallel settlement decision payload is missing",
        )
    _assert_parallel_budget_usage_lineage(
        conn,
        payload,
        reason_code=TASK_STORE_DURABLE_DATA_INVALID,
    )


def _assert_parallel_budget_usage_lineage(
    conn: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    reason_code: str,
) -> None:
    if str(payload.get("decision_kind") or "") != "BUDGET":
        return
    selected_node_id = str(payload.get("selected_node_id") or "")
    outcomes = payload.get("outcomes")
    selected = next(
        (
            outcome
            for outcome in outcomes
            if isinstance(outcome, Mapping)
            and str(outcome.get("node_id") or "") == selected_node_id
        ),
        None,
    ) if isinstance(outcomes, list) else None
    if selected is None:
        raise TaskStoreError(
            reason_code,
            "parallel Budget decision selected outcome is missing",
        )
    evidence_refs = {str(item) for item in selected.get("evidence_refs") or ()}
    usage_rows = conn.execute(
        """
        SELECT * FROM task_runtime_budget_usage
        WHERE budget_id = ? AND graph_id = ? AND graph_revision = ?
          AND accepted = 0
        ORDER BY reservation_version_after, created_ts_ms, usage_id
        """,
        (
            str(payload.get("budget_id") or ""),
            str(payload.get("graph_id") or ""),
            int(payload.get("graph_revision") or 0),
        ),
    ).fetchall()
    usages = tuple(_budget_usage_record(row) for row in usage_rows)
    winners = tuple(
        usage
        for usage in usages
        if usage["reason_code"] == TASK_STORE_BUDGET_EXCEEDED
    )
    siblings = tuple(
        usage
        for usage in usages
        if usage["reason_code"] == TASK_STORE_BUDGET_ALREADY_EXCEEDED
    )
    if len(winners) != 1 or len(winners) + len(siblings) != len(usages):
        raise TaskStoreError(
            reason_code,
            "parallel Budget decision lacks one unique terminal usage authority",
        )
    winner = winners[0]
    if (
        str(winner["node_id"]) != selected_node_id
        or str(winner["attempt_id"]) != str(selected.get("attempt_id") or "")
        or str(winner["usage_id"]) not in evidence_refs
        or not bool(winner["exceeded_dimensions"])
        or int(winner["reservation_version_after"])
        != int(payload.get("budget_status_version_before") or -1)
        or any(str(sibling["usage_id"]) in evidence_refs for sibling in siblings)
    ):
        raise TaskStoreError(
            reason_code,
            "parallel Budget decision selected a passive sibling instead of the winner",
        )
    budget_row = conn.execute(
        "SELECT * FROM task_runtime_budget_reservation WHERE budget_id = ?",
        (str(payload.get("budget_id") or ""),),
    ).fetchone()
    if budget_row is None:
        raise TaskStoreError(
            reason_code,
            "parallel Budget decision is missing its terminal reservation",
        )
    try:
        authority, _ = _assert_terminal_budget_authority(
            conn,
            budget_row=budget_row,
            terminal_version=int(winner["reservation_version_after"]),
        )
    except TaskStoreError as exc:
        raise TaskStoreError(reason_code, exc.detail) from exc
    if str(authority["usage_id"]) != str(winner["usage_id"]):
        raise TaskStoreError(
            reason_code,
            "parallel Budget decision winner differs from terminal authority",
        )


def _find_invalid_declared_parallel_settlement_decision_ref(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
) -> str | None:
    """Find a Graph Outbox declaration with no correctly bound decision row.

    Migration 0095 binds every TaskGraph Outbox to an explicit revision.  Every
    ``taskbatch://sha256/`` marker in the requested revision must therefore
    resolve to a decision bound to that same revision and exact Graph Outbox.
    """

    graph_row = conn.execute(
        """
        SELECT 1 FROM task_runtime_graph
        WHERE graph_id = ? AND revision = ?
        """,
        (graph_id, int(graph_revision)),
    ).fetchone()
    if graph_row is None:
        return None
    rows = conn.execute(
        """
        SELECT * FROM task_runtime_outbox
        WHERE aggregate_type = 'TASK_GRAPH'
          AND aggregate_id = ?
          AND graph_revision = ?
          AND event_type IN ('TASK_GRAPH_FAILED', 'TASK_GRAPH_BLOCKED')
        ORDER BY aggregate_version DESC, outbox_id DESC
        """,
        (graph_id, int(graph_revision)),
    ).fetchall()
    for row in rows:
        outbox = _outbox_record(row)
        for reference in outbox["evidence_refs"]:
            if reference.startswith("taskbatch://sha256/"):
                decision_row = conn.execute(
                    """
                    SELECT graph_id, graph_revision, graph_outbox_id
                    FROM task_runtime_parallel_settlement_decision
                    WHERE decision_ref = ?
                    """,
                    (reference,),
                ).fetchone()
                if decision_row is None:
                    return reference
                same_revision = (
                    str(decision_row["graph_id"]) == graph_id
                    and int(decision_row["graph_revision"]) == int(graph_revision)
                )
                exact_outbox = (
                    str(decision_row["graph_outbox_id"]) == str(outbox["outbox_id"])
                )
                if not same_revision or not exact_outbox:
                    return reference
    return None
