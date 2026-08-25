"""Attempt-bound child budget authority for the canonical Task Runtime.

The graph BudgetReservation remains the only aggregate ceiling.  This mixin
adds one durable child envelope per Attempt and records every parent
BudgetUsage against that child in the same SQLite transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount
from host_brain.persistence.task_record_codec import (
    _add_amounts,
    _amount_from_json,
    _dumps,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TaskStoreError,
    build_outbox_payload_ref,
)


NODE_BUDGET_SCHEMA_VERSION = "Natsume.task_runtime.node_budget_reservation.v1"
NODE_BUDGET_REQUIRED_COLUMNS = frozenset(
    {
        "node_budget_id",
        "schema_version",
        "parent_budget_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "scope",
        "limits_json",
        "reserved_json",
        "consumed_json",
        "attempted_consumed_json",
        "exceeded_dimensions_json",
        "usage_version",
        "parent_version_at_claim",
        "claim_outbox_id",
        "claim_origin",
        "created_ts_ms",
        "updated_ts_ms",
    }
)
NODE_BUDGET_USAGE_REQUIRED_COLUMNS = frozenset(
    {
        "node_budget_id",
        "parent_budget_id",
        "usage_id",
        "attempt_id",
        "accepted",
        "child_version_before",
        "child_version_after",
        "parent_outbox_id",
        "created_ts_ms",
    }
)
NODE_BUDGET_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_task_runtime_node_budget_identity_update",
        "trg_task_runtime_node_budget_no_delete",
        "trg_task_runtime_node_budget_usage_no_update",
        "trg_task_runtime_node_budget_usage_no_delete",
    }
)


def node_budget_id(
    *,
    parent_budget_id: str,
    node_id: str,
    attempt_id: str,
) -> str:
    material = (
        f"{str(parent_budget_id).strip()}\n"
        f"{str(node_id).strip()}\n"
        f"{str(attempt_id).strip()}"
    )
    return "node-budget::" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class TaskNodeBudgetStoreMixin:
    """Own child reservation creation, projection, and usage linkage."""

    @staticmethod
    def _node_budget_ready(conn: sqlite3.Connection) -> bool:
        return (
            conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'task_runtime_node_budget_reservation'
                """
            ).fetchone()
            is not None
        )

    def assert_node_budget_ready(self) -> None:
        conn = self._connect()
        try:
            reservation_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_node_budget_reservation)"
                ).fetchall()
            }
            usage_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_node_budget_usage)"
                ).fetchall()
            }
            trigger_names = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'trigger' AND name LIKE
                          'trg_task_runtime_node_budget%'
                    """
                ).fetchall()
            }
        finally:
            conn.close()
        missing_reservation = sorted(
            NODE_BUDGET_REQUIRED_COLUMNS - reservation_columns
        )
        missing_usage = sorted(
            NODE_BUDGET_USAGE_REQUIRED_COLUMNS - usage_columns
        )
        missing_triggers = sorted(
            NODE_BUDGET_REQUIRED_TRIGGERS - trigger_names
        )
        if missing_reservation or missing_usage or missing_triggers:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task node budget migration 0132 is not applied: "
                f"reservation={missing_reservation},usage={missing_usage},"
                f"triggers={missing_triggers}",
            )

    def get_node_budget_reservation(
        self,
        *,
        parent_budget_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = self._require_node_budget(
                conn,
                parent_budget_id=str(parent_budget_id),
                attempt_id=str(attempt_id),
            )
            return self._node_budget_record(conn, row)
        finally:
            conn.close()

    def get_node_budget_usage(
        self,
        *,
        parent_budget_id: str,
        usage_id: str,
    ) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT child_usage.*, parent_usage.charge_kind,
                       parent_usage.delta_json, parent_usage.delta_digest,
                       parent_usage.reason_code,
                       parent_usage.exceeded_dimensions_json
                FROM task_runtime_node_budget_usage AS child_usage
                JOIN task_runtime_budget_usage AS parent_usage
                  ON parent_usage.budget_id = child_usage.parent_budget_id
                 AND parent_usage.usage_id = child_usage.usage_id
                WHERE child_usage.parent_budget_id = ?
                  AND child_usage.usage_id = ?
                """,
                (str(parent_budget_id), str(usage_id)),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"NodeBudgetUsage not found: {usage_id}",
                )
            return {
                **dict(row),
                "accepted": bool(row["accepted"]),
                "delta": json.loads(str(row["delta_json"])),
                "exceeded_dimensions": tuple(
                    json.loads(str(row["exceeded_dimensions_json"]))
                ),
            }
        finally:
            conn.close()

    def _reserve_node_budget_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        parent_budget: sqlite3.Row,
        attempt: sqlite3.Row,
        claim_origin: str,
        ts_ms: int,
    ) -> sqlite3.Row:
        parent_ref = str(parent_budget["budget_id"])
        attempt_ref = str(attempt["attempt_id"])
        node_ref = str(attempt["node_id"])
        child_ref = node_budget_id(
            parent_budget_id=parent_ref,
            node_id=node_ref,
            attempt_id=attempt_ref,
        )
        existing = conn.execute(
            """
            SELECT * FROM task_runtime_node_budget_reservation
            WHERE attempt_id = ?
            """,
            (attempt_ref,),
        ).fetchone()
        if existing is not None:
            self._assert_node_budget_binding(
                existing,
                parent_budget=parent_budget,
                attempt=attempt,
            )
            return existing

        if claim_origin not in {
            "ATOMIC_READY_CLAIM",
            "LEGACY_CREATE_ATTEMPT",
        }:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "live node budget claim origin is invalid",
            )
        reserved = _remaining_child_envelope(parent_budget)
        zero = BudgetAmount()
        digest = hashlib.sha256(child_ref.encode("utf-8")).hexdigest()
        claim_outbox = OutboxMessage.create(
            aggregate_type="NODE_BUDGET_RESERVATION",
            aggregate_id=child_ref,
            aggregate_version=0,
            event_type="NODE_BUDGET_RESERVED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="NODE_BUDGET_RESERVATION",
                aggregate_id=child_ref,
                aggregate_version=0,
            ),
            idempotency_key=f"node-budget-reserved:{digest}",
            outbox_id=f"outbox::node-budget-reserved::{digest}",
            attempt_id=attempt_ref,
        )
        self._insert_outbox(
            conn,
            claim_outbox,
            ts_ms=int(ts_ms),
            expected_aggregate=("NODE_BUDGET_RESERVATION", child_ref, 0),
            expected_event_type="NODE_BUDGET_RESERVED",
            expected_attempt_id=attempt_ref,
        )
        try:
            conn.execute(
                """
                INSERT INTO task_runtime_node_budget_reservation(
                    node_budget_id, schema_version, parent_budget_id,
                    graph_id, graph_revision, node_id, attempt_id, scope,
                    limits_json, reserved_json, consumed_json,
                    attempted_consumed_json, exceeded_dimensions_json,
                    usage_version, parent_version_at_claim, claim_outbox_id,
                    claim_origin, created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]',
                          0, ?, ?, ?, ?, ?)
                """,
                (
                    child_ref,
                    NODE_BUDGET_SCHEMA_VERSION,
                    parent_ref,
                    str(attempt["graph_id"]),
                    int(attempt["graph_revision"]),
                    node_ref,
                    attempt_ref,
                    str(parent_budget["scope"]),
                    str(parent_budget["limits_json"]),
                    _dumps(reserved.to_dict()),
                    _dumps(zero.to_dict()),
                    _dumps(zero.to_dict()),
                    int(parent_budget["status_version"]),
                    claim_outbox.outbox_id,
                    claim_origin,
                    int(ts_ms),
                    int(ts_ms),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"node budget claim conflict for Attempt {attempt_ref}",
            ) from exc
        return self._require_node_budget(
            conn,
            parent_budget_id=parent_ref,
            attempt_id=attempt_ref,
        )

    def _apply_node_budget_usage_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        parent_budget: sqlite3.Row,
        attempt_id: str,
        usage_id: str,
        delta: BudgetAmount,
        accepted: bool,
        exceeded_dimensions: tuple[str, ...],
        parent_outbox_id: str,
        ts_ms: int,
    ) -> sqlite3.Row:
        child = self._require_node_budget(
            conn,
            parent_budget_id=str(parent_budget["budget_id"]),
            attempt_id=str(attempt_id),
        )
        existing = conn.execute(
            """
            SELECT * FROM task_runtime_node_budget_usage
            WHERE parent_budget_id = ? AND usage_id = ?
            """,
            (str(parent_budget["budget_id"]), str(usage_id)),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["node_budget_id"]) != str(child["node_budget_id"])
                or str(existing["attempt_id"]) != str(attempt_id)
                or bool(existing["accepted"]) is not bool(accepted)
                or str(existing["parent_outbox_id"]) != str(parent_outbox_id)
            ):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    f"node budget usage replay drift: {usage_id}",
                )
            return child

        current_version = int(child["usage_version"])
        next_version = current_version + 1
        consumed = _amount_from_json(
            child["consumed_json"],
            field_name="node_budget.consumed_json",
        )
        attempted_before = _amount_from_json(
            child["attempted_consumed_json"],
            field_name="node_budget.attempted_consumed_json",
        )
        next_consumed = _add_amounts(consumed, delta) if accepted else consumed
        next_attempted = _add_amounts(attempted_before, delta)
        cursor = conn.execute(
            """
            UPDATE task_runtime_node_budget_reservation
            SET consumed_json = ?, attempted_consumed_json = ?,
                exceeded_dimensions_json = ?, usage_version = ?,
                updated_ts_ms = ?
            WHERE node_budget_id = ? AND usage_version = ?
            """,
            (
                _dumps(next_consumed.to_dict()),
                _dumps(next_attempted.to_dict()),
                _dumps(list(exceeded_dimensions)),
                next_version,
                int(ts_ms),
                str(child["node_budget_id"]),
                current_version,
            ),
        )
        self._require_cas(
            cursor,
            subject=f"NodeBudgetReservation {child['node_budget_id']}",
        )
        conn.execute(
            """
            INSERT INTO task_runtime_node_budget_usage(
                node_budget_id, parent_budget_id, usage_id, attempt_id,
                accepted, child_version_before, child_version_after,
                parent_outbox_id, created_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(child["node_budget_id"]),
                str(parent_budget["budget_id"]),
                str(usage_id),
                str(attempt_id),
                1 if accepted else 0,
                current_version,
                next_version,
                str(parent_outbox_id),
                int(ts_ms),
            ),
        )
        return self._require_node_budget(
            conn,
            parent_budget_id=str(parent_budget["budget_id"]),
            attempt_id=str(attempt_id),
        )

    def _assert_node_budget_usage_replay_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        parent_budget_id: str,
        attempt_id: str,
        usage_id: str,
        accepted: bool,
        parent_outbox_id: str,
    ) -> None:
        child = self._require_node_budget(
            conn,
            parent_budget_id=str(parent_budget_id),
            attempt_id=str(attempt_id),
        )
        binding = conn.execute(
            """
            SELECT * FROM task_runtime_node_budget_usage
            WHERE parent_budget_id = ? AND usage_id = ?
            """,
            (str(parent_budget_id), str(usage_id)),
        ).fetchone()
        if (
            binding is None
            or str(binding["node_budget_id"]) != str(child["node_budget_id"])
            or str(binding["attempt_id"]) != str(attempt_id)
            or bool(binding["accepted"]) is not bool(accepted)
            or str(binding["parent_outbox_id"]) != str(parent_outbox_id)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"BudgetUsage lacks exact node reservation lineage: {usage_id}",
            )

    @staticmethod
    def _node_budget_exceeded_dimensions(
        child: sqlite3.Row,
        delta: BudgetAmount,
    ) -> tuple[str, ...]:
        consumed = _amount_from_json(
            child["consumed_json"],
            field_name="node_budget.consumed_json",
        )
        reserved = _amount_from_json(
            child["reserved_json"],
            field_name="node_budget.reserved_json",
        )
        attempted = _add_amounts(consumed, delta)
        return tuple(
            key
            for key, value in attempted.to_dict().items()
            if value > reserved.to_dict()[key]
        )

    @staticmethod
    def _require_node_budget(
        conn: sqlite3.Connection,
        *,
        parent_budget_id: str,
        attempt_id: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_node_budget_reservation
            WHERE parent_budget_id = ? AND attempt_id = ?
            """,
            (str(parent_budget_id), str(attempt_id)),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                "Attempt has no durable node BudgetReservation: "
                f"{attempt_id}",
            )
        return row

    def _node_budget_record(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        attempt = self._require_attempt(conn, str(row["attempt_id"]))
        parent = self._require_budget(conn, str(row["parent_budget_id"]))
        self._assert_node_budget_binding(
            row,
            parent_budget=parent,
            attempt=attempt,
        )
        reserved = _amount_from_json(
            row["reserved_json"],
            field_name="node_budget.reserved_json",
        )
        consumed = _amount_from_json(
            row["consumed_json"],
            field_name="node_budget.consumed_json",
        )
        attempted = _amount_from_json(
            row["attempted_consumed_json"],
            field_name="node_budget.attempted_consumed_json",
        )
        self._assert_node_budget_projection(
            conn,
            row=row,
            consumed=consumed,
            attempted=attempted,
        )
        attempt_status = str(attempt["status"])
        if str(parent["status"]) == "EXCEEDED" or attempted.exceeds(reserved):
            status = "EXCEEDED"
        elif attempt_status == AttemptStatus.SUCCEEDED.value:
            status = "CONSUMED"
        elif attempt_status in {
            AttemptStatus.FAILED.value,
            AttemptStatus.CANCELLED.value,
        }:
            status = "RELEASED"
        elif attempt_status == AttemptStatus.CANCEL_REQUESTED.value:
            status = "CANCEL_REQUESTED"
        else:
            status = "ACTIVE"
        remaining = {
            key: max(0, reserved.to_dict()[key] - consumed.to_dict()[key])
            for key in reserved.to_dict()
        }
        return {
            "schema_version": str(row["schema_version"]),
            "node_budget_id": str(row["node_budget_id"]),
            "parent_budget_id": str(row["parent_budget_id"]),
            "graph_id": str(row["graph_id"]),
            "graph_revision": int(row["graph_revision"]),
            "node_id": str(row["node_id"]),
            "attempt_id": str(row["attempt_id"]),
            "scope": str(row["scope"]),
            "limits": json.loads(str(row["limits_json"])),
            "reserved": reserved.to_dict(),
            "consumed": consumed.to_dict(),
            "attempted_consumed": attempted.to_dict(),
            "remaining": remaining,
            "exceeded_dimensions": tuple(
                json.loads(str(row["exceeded_dimensions_json"]))
            ),
            "usage_version": int(row["usage_version"]),
            "parent_version_at_claim": int(row["parent_version_at_claim"]),
            "claim_outbox_id": (
                str(row["claim_outbox_id"])
                if row["claim_outbox_id"] is not None
                else None
            ),
            "claim_origin": str(row["claim_origin"]),
            "attempt_status": attempt_status,
            "status": status,
            "created_ts_ms": int(row["created_ts_ms"]),
            "updated_ts_ms": int(row["updated_ts_ms"]),
        }

    @staticmethod
    def _assert_node_budget_projection(
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        consumed: BudgetAmount,
        attempted: BudgetAmount,
    ) -> None:
        usage_rows = conn.execute(
            """
            SELECT child_usage.*, parent_usage.delta_json,
                   parent_usage.accepted AS parent_accepted,
                   parent_usage.outbox_id AS actual_parent_outbox_id
            FROM task_runtime_node_budget_usage AS child_usage
            JOIN task_runtime_budget_usage AS parent_usage
              ON parent_usage.budget_id = child_usage.parent_budget_id
             AND parent_usage.usage_id = child_usage.usage_id
            WHERE child_usage.node_budget_id = ?
            ORDER BY child_usage.child_version_after, child_usage.usage_id
            """,
            (str(row["node_budget_id"]),),
        ).fetchall()
        if len(usage_rows) != int(row["usage_version"]):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "node BudgetReservation usage version drifted",
            )
        expected_consumed = BudgetAmount()
        expected_attempted = BudgetAmount()
        for index, usage in enumerate(usage_rows):
            if (
                int(usage["child_version_before"]) != index
                or int(usage["child_version_after"]) != index + 1
                or bool(usage["accepted"]) is not bool(usage["parent_accepted"])
                or str(usage["parent_outbox_id"])
                != str(usage["actual_parent_outbox_id"])
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "node BudgetUsage lineage or version chain drifted",
                )
            delta = _amount_from_json(
                usage["delta_json"],
                field_name="node_budget_usage.delta_json",
            )
            expected_attempted = _add_amounts(expected_attempted, delta)
            if bool(usage["accepted"]):
                expected_consumed = _add_amounts(expected_consumed, delta)
        if (
            expected_consumed.to_dict() != consumed.to_dict()
            or expected_attempted.to_dict() != attempted.to_dict()
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "node BudgetReservation projection differs from its usage ledger",
            )

    @staticmethod
    def _assert_node_budget_binding(
        row: sqlite3.Row,
        *,
        parent_budget: sqlite3.Row,
        attempt: sqlite3.Row,
    ) -> None:
        if (
            str(row["schema_version"]) != NODE_BUDGET_SCHEMA_VERSION
            or str(row["node_budget_id"]) != node_budget_id(
                parent_budget_id=str(parent_budget["budget_id"]),
                node_id=str(attempt["node_id"]),
                attempt_id=str(attempt["attempt_id"]),
            )
            or str(row["parent_budget_id"]) != str(parent_budget["budget_id"])
            or str(row["graph_id"]) != str(attempt["graph_id"])
            or int(row["graph_revision"]) != int(attempt["graph_revision"])
            or str(row["node_id"]) != str(attempt["node_id"])
            or str(row["attempt_id"]) != str(attempt["attempt_id"])
            or str(row["graph_id"]) != str(parent_budget["graph_id"])
            or int(row["graph_revision"])
            != int(parent_budget["graph_revision"])
            or parent_budget["node_id"] is not None
            or parent_budget["attempt_id"] is not None
            or str(row["scope"]) != str(parent_budget["scope"])
            or str(row["limits_json"]) != str(parent_budget["limits_json"])
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "node BudgetReservation lineage drifted",
            )


def _remaining_child_envelope(parent_budget: sqlite3.Row) -> BudgetAmount:
    reserved = _amount_from_json(
        parent_budget["reserved_json"],
        field_name="budget.reserved_json",
    )
    consumed = _amount_from_json(
        parent_budget["consumed_json"],
        field_name="budget.consumed_json",
    )
    values = {
        key: max(0, reserved.to_dict()[key] - consumed.to_dict()[key])
        for key in reserved.to_dict()
    }
    # A child owns one execution slot.  The graph-level claim transaction still
    # enforces the aggregate concurrency ceiling under BEGIN IMMEDIATE.
    values["concurrency"] = 1 if reserved.concurrency > 0 else 0
    return BudgetAmount.from_mapping(values)


__all__ = [
    "NODE_BUDGET_REQUIRED_COLUMNS",
    "NODE_BUDGET_REQUIRED_TRIGGERS",
    "NODE_BUDGET_SCHEMA_VERSION",
    "NODE_BUDGET_USAGE_REQUIRED_COLUMNS",
    "TaskNodeBudgetStoreMixin",
    "node_budget_id",
]
