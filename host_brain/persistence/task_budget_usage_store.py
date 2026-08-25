"""Durable, idempotent BudgetUsage charging and terminal contention handling.

The owning :class:`DurableTaskStore` supplies the SQLite transaction, trusted
clock, record codecs, and Outbox insertion authority.  This mixin keeps every
BudgetUsage write inside that single unit of work.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservationStatus,
)
from host_brain.persistence.task_record_codec import (
    _add_amounts,
    _amount_from_json,
    _budget_usage_record,
    _dumps,
    _exceeded_dimensions,
    _outbox_record,
    _validated_budget_record,
)
from host_brain.persistence.task_store_types import (
    BudgetConsumptionResult,
    OutboxMessage,
    TASK_STORE_BUDGET_ALREADY_EXCEEDED,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OUTBOX_CONFLICT,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)
from host_brain.persistence.task_budget_terminal_sibling import (
    record_already_exceeded_usage,
)


class TaskBudgetUsageStoreMixin:
    """Persist exactly-once charges and one canonical terminal overage."""

    def consume_budget_once(
        self,
        budget_id: str,
        *,
        usage_id: str,
        charge_kind: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        delta: BudgetAmount,
        expected_version: int,
        outbox: OutboxMessage,
    ) -> BudgetConsumptionResult:
        """Journal and apply one stable charge under a bounded CAS protocol.

        The first over-limit charge is the only reservation transition and
        owns the only ``BUDGET_EXCEEDED`` Outbox.  Once callers have refreshed
        to that terminal reservation version, every distinct sibling charge is
        durably rejected as ``ALREADY_EXCEEDED`` without another reservation
        transition.  A caller still holding the prior ACTIVE version receives
        a CAS conflict and therefore cannot silently skip the refresh boundary.
        """

        (
            budget_ref,
            usage_ref,
            kind,
            graph_ref,
            node_ref,
            attempt_ref,
        ) = self._normalize_budget_usage_binding(
            budget_id=budget_id,
            usage_id=usage_id,
            charge_kind=charge_kind,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
        )
        if not isinstance(delta, BudgetAmount):
            raise TaskStoreError(TASK_STORE_INVALID, "delta must be a BudgetAmount")
        normalized_delta = BudgetAmount.from_mapping(delta.to_dict())
        delta_json = _dumps(normalized_delta.to_dict())
        delta_digest = "sha256:" + hashlib.sha256(delta_json.encode("utf-8")).hexdigest()
        ts_ms = self._now()

        with self._transaction() as conn:
            child_ledger_enabled = self._node_budget_ready(conn)
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
            )
            existing = conn.execute(
                """
                SELECT * FROM task_runtime_budget_usage
                WHERE budget_id = ? AND usage_id = ?
                """,
                (budget_ref, usage_ref),
            ).fetchone()
            if existing is not None:
                return self._replay_budget_usage(
                    conn,
                    existing=existing,
                    budget_ref=budget_ref,
                    usage_ref=usage_ref,
                    kind=kind,
                    graph_ref=graph_ref,
                    graph_revision=graph_revision,
                    node_ref=node_ref,
                    attempt_ref=attempt_ref,
                    delta_json=delta_json,
                    delta_digest=delta_digest,
                )

            self._assert_no_terminal_reconciliation_fence(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
                action="Budget usage journal",
            )

            row = self._require_budget(conn, budget_ref)
            self._assert_shared_budget_usage_binding(
                conn,
                row=row,
                graph_ref=graph_ref,
                graph_revision=graph_revision,
                node_ref=node_ref,
                attempt_ref=attempt_ref,
            )
            # This check intentionally precedes the terminal-status branch.
            # A loser holding the old ACTIVE snapshot must refresh once; only a
            # caller already bound to the current EXCEEDED version may journal
            # an ALREADY_EXCEEDED observation.
            self._require_version(
                row,
                expected_version,
                subject=f"BudgetReservation {budget_ref}",
            )
            status = str(row["status"])
            if status == BudgetReservationStatus.EXCEEDED.value:
                return record_already_exceeded_usage(
                    self,
                    conn,
                    row=row,
                    budget_ref=budget_ref,
                    usage_ref=usage_ref,
                    kind=kind,
                    graph_ref=graph_ref,
                    graph_revision=graph_revision,
                    node_ref=node_ref,
                    attempt_ref=attempt_ref,
                    normalized_delta=normalized_delta,
                    delta_json=delta_json,
                    delta_digest=delta_digest,
                    outbox=outbox,
                    ts_ms=ts_ms,
                )
            if status != BudgetReservationStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"budget {budget_ref} must be ACTIVE before consumption",
                )

            consumed = _amount_from_json(row["consumed_json"], field_name="consumed_json")
            reserved = _amount_from_json(row["reserved_json"], field_name="reserved_json")
            attempted = _add_amounts(consumed, normalized_delta)
            parent_exceeded_dimensions = _exceeded_dimensions(
                attempted,
                reserved,
            )
            child_exceeded_dimensions: tuple[str, ...] = ()
            if child_ledger_enabled:
                child_budget = self._require_node_budget(
                    conn,
                    parent_budget_id=budget_ref,
                    attempt_id=attempt_ref,
                )
                child_exceeded_dimensions = self._node_budget_exceeded_dimensions(
                    child_budget,
                    normalized_delta,
                )
            exceeded_dimensions = tuple(
                key
                for key in reserved.to_dict()
                if key in {
                    *parent_exceeded_dimensions,
                    *child_exceeded_dimensions,
                }
            )
            accepted = not exceeded_dimensions
            target_status = (
                BudgetReservationStatus.ACTIVE.value
                if accepted
                else BudgetReservationStatus.EXCEEDED.value
            )
            next_consumed = attempted if accepted else consumed
            next_version = int(expected_version) + 1
            cursor = conn.execute(
                """
                UPDATE task_runtime_budget_reservation
                SET consumed_json = ?, attempted_consumed_json = ?,
                    exceeded_dimensions_json = ?, status = ?,
                    status_version = status_version + 1, updated_ts_ms = ?
                WHERE budget_id = ? AND status_version = ? AND status = 'ACTIVE'
                """,
                (
                    _dumps(next_consumed.to_dict()),
                    _dumps(attempted.to_dict()),
                    _dumps(list(exceeded_dimensions)),
                    target_status,
                    ts_ms,
                    budget_ref,
                    int(expected_version),
                ),
            )
            self._require_cas(cursor, subject=f"BudgetReservation {budget_ref}")
            reason_code = TASK_STORE_BUDGET_EXCEEDED if exceeded_dimensions else None
            conn.execute(
                """
                INSERT INTO task_runtime_budget_usage(
                    budget_id, usage_id, graph_id, graph_revision, node_id,
                    attempt_id, charge_kind, delta_json, delta_digest, accepted,
                    reason_code, exceeded_dimensions_json,
                    reservation_version_before, reservation_version_after,
                    outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    budget_ref,
                    usage_ref,
                    graph_ref,
                    graph_revision,
                    node_ref,
                    attempt_ref,
                    kind,
                    delta_json,
                    delta_digest,
                    1 if accepted else 0,
                    reason_code,
                    _dumps(list(exceeded_dimensions)),
                    int(expected_version),
                    next_version,
                    outbox.outbox_id,
                    ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=("BUDGET_RESERVATION", budget_ref, next_version),
                expected_event_type=("BUDGET_CONSUMED" if accepted else "BUDGET_EXCEEDED"),
                expected_attempt_id=attempt_ref,
            )
            if child_ledger_enabled:
                self._apply_node_budget_usage_in_transaction(
                    conn,
                    parent_budget=row,
                    attempt_id=attempt_ref,
                    usage_id=usage_ref,
                    delta=normalized_delta,
                    accepted=accepted,
                    exceeded_dimensions=exceeded_dimensions,
                    parent_outbox_id=outbox.outbox_id,
                    ts_ms=ts_ms,
                )
            updated = _validated_budget_record(self._require_budget(conn, budget_ref))
            usage_row = conn.execute(
                """
                SELECT * FROM task_runtime_budget_usage
                WHERE budget_id = ? AND usage_id = ?
                """,
                (budget_ref, usage_ref),
            ).fetchone()
            return BudgetConsumptionResult(
                accepted=accepted,
                reason_code=reason_code,
                reservation=updated,
                exceeded_dimensions=exceeded_dimensions,
                usage_id=usage_ref,
                replayed=False,
                usage=_budget_usage_record(usage_row),
            )

    def get_budget_usage(self, budget_id: str, usage_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_budget_usage
                WHERE budget_id = ? AND usage_id = ?
                """,
                (str(budget_id), str(usage_id)),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"BudgetUsage not found: {usage_id}",
                )
            usage = _budget_usage_record(row)
            if usage.get("reason_code") in {
                TASK_STORE_BUDGET_EXCEEDED,
                TASK_STORE_BUDGET_ALREADY_EXCEEDED,
            }:
                budget = self._require_budget(conn, str(budget_id))
                self._terminal_budget_authority(
                    conn,
                    budget_row=budget,
                    terminal_version=int(usage["reservation_version_after"]),
                )
            return usage
        finally:
            conn.close()

    def reconcile_budget(self, budget_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            record = _validated_budget_record(self._require_budget(conn, budget_id))
            if (
                record["status"] == BudgetReservationStatus.EXCEEDED.value
                and record.get("graph_id") is not None
                and record.get("node_id") is None
                and record.get("attempt_id") is None
                and conn.execute(
                    "SELECT 1 FROM task_runtime_budget_usage WHERE budget_id = ? LIMIT 1",
                    (str(budget_id),),
                ).fetchone()
                is not None
            ):
                self._terminal_budget_authority(
                    conn,
                    budget_row=self._require_budget(conn, budget_id),
                    terminal_version=int(record["status_version"]),
                )
        finally:
            conn.close()
        reserved = BudgetAmount.from_mapping(record["reserved"])
        consumed = BudgetAmount.from_mapping(record["consumed"])
        remaining = {
            key: max(0, reserved.to_dict()[key] - consumed.to_dict()[key])
            for key in reserved.to_dict()
        }
        return {
            **record,
            "remaining": remaining,
            "balanced": not consumed.exceeds(reserved),
            "denied_usage_preserved": bool(record["exceeded_dimensions"]),
        }

    @staticmethod
    def _normalize_budget_usage_binding(
        *,
        budget_id: str,
        usage_id: str,
        charge_kind: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
    ) -> tuple[str, str, str, str, str, str]:
        budget_ref = str(budget_id or "").strip()
        usage_ref = str(usage_id or "").strip()
        kind = str(charge_kind or "").strip().upper()
        graph_ref = str(graph_id or "").strip()
        node_ref = str(node_id or "").strip()
        attempt_ref = str(attempt_id or "").strip()
        if (
            not budget_ref
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(usage_ref)
            or not kind
            or len(kind) > 64
            or not graph_ref
            or not node_ref
            or not attempt_ref
            or isinstance(graph_revision, bool)
            or not isinstance(graph_revision, int)
            or graph_revision < 1
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "budget usage requires a durable usage_id and complete graph/attempt binding",
            )
        return budget_ref, usage_ref, kind, graph_ref, node_ref, attempt_ref

    def _assert_shared_budget_usage_binding(
        self,
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        graph_ref: str,
        graph_revision: int,
        node_ref: str,
        attempt_ref: str,
    ) -> None:
        if (
            str(row["graph_id"] or "") != graph_ref
            or int(row["graph_revision"] or 0) != graph_revision
            or row["node_id"] is not None
            or row["attempt_id"] is not None
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "shared budget reservation graph binding drifted",
            )
        attempt = self._require_attempt(conn, attempt_ref)
        if (
            str(attempt["graph_id"]) != graph_ref
            or int(attempt["graph_revision"]) != graph_revision
            or str(attempt["node_id"]) != node_ref
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "budget usage attempt is outside the reservation graph revision",
            )

    def _replay_budget_usage(
        self,
        conn: sqlite3.Connection,
        *,
        existing: sqlite3.Row,
        budget_ref: str,
        usage_ref: str,
        kind: str,
        graph_ref: str,
        graph_revision: int,
        node_ref: str,
        attempt_ref: str,
        delta_json: str,
        delta_digest: str,
    ) -> BudgetConsumptionResult:
        drift = (
            str(existing["charge_kind"]) != kind
            or str(existing["graph_id"]) != graph_ref
            or int(existing["graph_revision"]) != graph_revision
            or str(existing["node_id"]) != node_ref
            or str(existing["attempt_id"]) != attempt_ref
            or str(existing["delta_digest"]) != delta_digest
            or str(existing["delta_json"]) != delta_json
        )
        if drift:
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                f"budget usage replay drift: {usage_ref}",
            )
        if self._node_budget_ready(conn):
            self._assert_node_budget_usage_replay_in_transaction(
                conn,
                parent_budget_id=budget_ref,
                attempt_id=attempt_ref,
                usage_id=usage_ref,
                accepted=bool(existing["accepted"]),
                parent_outbox_id=str(existing["outbox_id"]),
            )
        budget_row = self._require_budget(conn, budget_ref)
        reservation = _validated_budget_record(budget_row)
        usage = _budget_usage_record(existing)
        if usage.get("reason_code") in {
            TASK_STORE_BUDGET_EXCEEDED,
            TASK_STORE_BUDGET_ALREADY_EXCEEDED,
        }:
            self._terminal_budget_authority(
                conn,
                budget_row=budget_row,
                terminal_version=int(usage["reservation_version_after"]),
            )
        dimensions = tuple(str(item) for item in usage["exceeded_dimensions"])
        return BudgetConsumptionResult(
            accepted=bool(usage["accepted"]),
            reason_code=(str(usage["reason_code"]) if usage.get("reason_code") else None),
            reservation=reservation,
            exceeded_dimensions=dimensions,
            usage_id=usage_ref,
            replayed=True,
            usage=usage,
        )

    @staticmethod
    def _validate_terminal_sibling_outbox(
        message: OutboxMessage,
        *,
        budget_ref: str,
        terminal_version: int,
        attempt_ref: str,
        usage_ref: str,
        would_exceed: bool,
    ) -> None:
        if not isinstance(message, OutboxMessage):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "terminal budget sibling requires an OutboxMessage candidate",
            )
        message.validate()
        expected_event_type = "BUDGET_EXCEEDED" if would_exceed else "BUDGET_CONSUMED"
        if (
            message.available_ts_ms is not None
            or message.aggregate_type != "BUDGET_RESERVATION"
            or message.aggregate_id != budget_ref
            or int(message.aggregate_version) != terminal_version + 1
            or message.event_type != expected_event_type
            or message.attempt_id != attempt_ref
            or usage_ref not in message.evidence_refs
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "terminal budget sibling Outbox candidate is not bound to the current request",
            )

    def _terminal_budget_authority(
        self,
        conn: sqlite3.Connection,
        *,
        budget_row: sqlite3.Row,
        terminal_version: int,
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        return _assert_terminal_budget_authority(
            conn,
            budget_row=budget_row,
            terminal_version=terminal_version,
        )
def _assert_terminal_budget_authority(
    conn: sqlite3.Connection,
    *,
    budget_row: sqlite3.Row,
    terminal_version: int,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Validate the single winner and every passive terminal sibling."""

    budget = _validated_budget_record(budget_row)
    budget_ref = str(budget["budget_id"])
    graph_ref = str(budget.get("graph_id") or "")
    graph_revision = int(budget.get("graph_revision") or 0)
    dimensions = tuple(str(item) for item in budget["exceeded_dimensions"])
    status = str(budget["status"])
    expected_status_version = (
        int(terminal_version) + 1
        if status == BudgetReservationStatus.RELEASED.value
        else int(terminal_version)
    )
    if (
        int(budget["status_version"]) != expected_status_version
        or not dimensions
        or status
        not in {
            BudgetReservationStatus.EXCEEDED.value,
            BudgetReservationStatus.RELEASED.value,
        }
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal BudgetReservation has no stable exceeded authority",
        )
    winner_rows = conn.execute(
        """
        SELECT * FROM task_runtime_budget_usage
        WHERE budget_id = ? AND accepted = 0 AND reason_code = ?
        ORDER BY reservation_version_after, created_ts_ms, usage_id
        """,
        (budget_ref, TASK_STORE_BUDGET_EXCEEDED),
    ).fetchall()
    if len(winner_rows) != 1:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "EXCEEDED budget requires exactly one canonical winning usage",
        )
    winner = _budget_usage_record(winner_rows[0])
    if (
        int(winner["reservation_version_before"]) + 1 != int(terminal_version)
        or int(winner["reservation_version_after"]) != int(terminal_version)
        or tuple(winner["exceeded_dimensions"]) != dimensions
        or str(winner["graph_id"]) != graph_ref
        or int(winner["graph_revision"]) != graph_revision
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "canonical exceeded BudgetUsage does not bind the terminal reservation",
        )
    consumed = BudgetAmount.from_mapping(budget["consumed"])
    attempted = _add_amounts(consumed, BudgetAmount.from_mapping(winner["delta"]))
    reserved = BudgetAmount.from_mapping(budget["reserved"])
    if (
        attempted.to_dict() != dict(budget["attempted_consumed"])
        or _exceeded_dimensions(attempted, reserved) != dimensions
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "canonical exceeded BudgetUsage delta does not reproduce terminal overage",
        )
    _assert_usage_attempt_lineage(conn, usage=winner)
    outbox_rows = conn.execute(
        """
        SELECT * FROM task_runtime_outbox
        WHERE aggregate_type = 'BUDGET_RESERVATION'
          AND aggregate_id = ? AND aggregate_version = ?
        ORDER BY outbox_id
        """,
        (budget_ref, int(terminal_version)),
    ).fetchall()
    if len(outbox_rows) != 1:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "EXCEEDED budget requires exactly one terminal Outbox authority",
        )
    terminal_outbox = _outbox_record(outbox_rows[0])
    if (
        str(terminal_outbox["outbox_id"]) != str(winner["outbox_id"])
        or terminal_outbox["event_type"] != "BUDGET_EXCEEDED"
        or terminal_outbox["attempt_id"] != str(winner["attempt_id"])
        or str(winner["usage_id"]) not in terminal_outbox["evidence_refs"]
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "canonical exceeded BudgetUsage and terminal Outbox drifted",
        )
    rejected_rows = conn.execute(
        """
        SELECT * FROM task_runtime_budget_usage
        WHERE budget_id = ? AND accepted = 0
        ORDER BY reservation_version_after, created_ts_ms, usage_id
        """,
        (budget_ref,),
    ).fetchall()
    for rejected_row in rejected_rows:
        usage = _budget_usage_record(rejected_row)
        if usage["reason_code"] == TASK_STORE_BUDGET_EXCEEDED:
            if usage["usage_id"] != winner["usage_id"]:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "multiple terminal BudgetUsage authorities exist",
                )
            continue
        if (
            usage["reason_code"] != TASK_STORE_BUDGET_ALREADY_EXCEEDED
            or int(usage["reservation_version_before"]) != int(terminal_version)
            or int(usage["reservation_version_after"]) != int(terminal_version)
            or tuple(usage["exceeded_dimensions"]) != dimensions
            or str(usage["outbox_id"]) != str(winner["outbox_id"])
            or str(usage["graph_id"]) != graph_ref
            or int(usage["graph_revision"]) != graph_revision
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal BudgetUsage sibling lineage drifted",
            )
        _assert_usage_attempt_lineage(conn, usage=usage)
    return winner, dimensions


def _assert_usage_attempt_lineage(
    conn: sqlite3.Connection,
    *,
    usage: dict[str, Any],
) -> None:
    attempt = conn.execute(
        "SELECT * FROM task_runtime_attempt WHERE attempt_id = ?",
        (str(usage["attempt_id"]),),
    ).fetchone()
    if (
        attempt is None
        or str(attempt["graph_id"]) != str(usage["graph_id"])
        or int(attempt["graph_revision"]) != int(usage["graph_revision"])
        or str(attempt["node_id"]) != str(usage["node_id"])
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "terminal BudgetUsage Attempt lineage drifted",
        )


__all__ = ["TaskBudgetUsageStoreMixin", "_assert_terminal_budget_authority"]
