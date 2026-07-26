"""Query-only evidence reader for Universal Governed Runtime acceptance.

The reader deliberately exposes only bounded identifiers, status/version
facts, digests, counters and receipt references.  It never returns Host
command payloads, WorkOrders, model output, conversation text or Effect
payloads.  Release policy remains in ``host_brain.release``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote


UNIVERSAL_ROUTE_ACCEPTANCE_READ_INVALID = (
    "UNIVERSAL_ROUTE_ACCEPTANCE_READ_INVALID"
)
UNIVERSAL_ROUTE_ACCEPTANCE_READ_UNAVAILABLE = (
    "UNIVERSAL_ROUTE_ACCEPTANCE_READ_UNAVAILABLE"
)
UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_NOT_READY = (
    "UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_NOT_READY"
)

_REQUIRED_TABLE_COLUMNS: Mapping[str, frozenset[str]] = {
    "schema_migrations": frozenset({"migration_id", "name"}),
    "host_command_journal": frozenset(
        {
            "command_id",
            "request_id",
            "surface",
            "route_type",
            "graph_id",
            "graph_revision",
            "created_at_ms",
        }
    ),
    "task_runtime_graph": frozenset(
        {"graph_id", "revision", "status", "status_version"}
    ),
    "task_runtime_node": frozenset(
        {"graph_id", "graph_revision", "node_id", "status", "status_version"}
    ),
    "task_runtime_attempt": frozenset(
        {
            "attempt_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_no",
            "status",
            "status_version",
        }
    ),
    "task_runtime_lease": frozenset(
        {"lease_id", "attempt_id", "status", "status_version"}
    ),
    "task_runtime_idempotency_reservation": frozenset(
        {
            "reservation_id",
            "attempt_id",
            "status",
            "status_version",
            "receipt_ref",
        }
    ),
    "task_runtime_budget_reservation": frozenset(
        {
            "budget_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "limits_json",
            "reserved_json",
            "consumed_json",
            "attempted_consumed_json",
            "exceeded_dimensions_json",
            "status",
            "status_version",
        }
    ),
    "task_runtime_budget_usage": frozenset(
        {
            "budget_id",
            "usage_id",
            "attempt_id",
            "delta_json",
            "accepted",
            "exceeded_dimensions_json",
            "reservation_version_before",
            "reservation_version_after",
            "outbox_id",
        }
    ),
    "task_runtime_node_budget_reservation": frozenset(
        {
            "node_budget_id",
            "parent_budget_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "limits_json",
            "reserved_json",
            "consumed_json",
            "attempted_consumed_json",
            "exceeded_dimensions_json",
            "usage_version",
            "claim_outbox_id",
            "claim_origin",
        }
    ),
    "task_runtime_node_budget_usage": frozenset(
        {
            "node_budget_id",
            "parent_budget_id",
            "usage_id",
            "attempt_id",
            "accepted",
            "child_version_before",
            "child_version_after",
            "parent_outbox_id",
        }
    ),
    "task_runtime_node_verification_decision": frozenset(
        {
            "verification_ref",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "target_status",
            "result_sha256",
            "node_outbox_id",
        }
    ),
    "task_runtime_node_acceptance_decision": frozenset(
        {
            "acceptance_ref",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "target_status",
            "result_sha256",
            "node_outbox_id",
        }
    ),
    "task_runtime_node_artifact": frozenset(
        {
            "artifact_ref",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "receipt_ref",
            "output_contract_sha256",
            "payload_sha256",
        }
    ),
    "task_runtime_worker_projection": frozenset(
        {
            "projection_id",
            "command_id",
            "request_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "route_type",
            "decision",
            "payload_digest",
        }
    ),
    "task_runtime_acceptance_effect_intent": frozenset(
        {
            "effect_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "effect_type",
            "status",
            "status_version",
            "receipt_ref",
        }
    ),
    "task_runtime_outbox": frozenset(
        {
            "outbox_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_type",
            "attempt_id",
            "status",
            "status_version",
            "graph_revision",
        }
    ),
    "task_runtime_operation_recovery_hold": frozenset(
        {
            "case_ref",
            "graph_id",
            "graph_revision",
            "reservation_id",
            "operation_status",
            "operation_status_version",
            "cancellation_intent_ref",
            "cancellation_status",
            "cancellation_status_version",
        }
    ),
    "task_runtime_effect_reconciliation_case": frozenset(
        {
            "case_id",
            "graph_id",
            "graph_revision",
            "effect_id",
            "intent_ref",
            "classification",
        }
    ),
    "task_runtime_effect_reconciliation_proof": frozenset(
        {"proof_id", "case_id", "target_resolution_status", "receipt_ref"}
    ),
    "task_runtime_graph_cancellation_intent": frozenset(
        {
            "intent_ref",
            "graph_id",
            "graph_revision",
            "status",
            "status_version",
        }
    ),
}


class UniversalRouteAcceptanceReadError(RuntimeError):
    """Structured failure from the query-only evidence boundary."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(
            reason_code or UNIVERSAL_ROUTE_ACCEPTANCE_READ_UNAVAILABLE
        )
        self.detail = str(detail or "acceptance evidence read failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class UniversalRouteAcceptanceReader:
    """Capture one transactionally consistent, privacy-bounded DB snapshot."""

    def __init__(self, db_path: str | Path, *, timeout_ms: int = 10_000) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise UniversalRouteAcceptanceReadError(
                UNIVERSAL_ROUTE_ACCEPTANCE_READ_INVALID,
                "a file-backed canonical SQLite path is required",
            )
        self.db_path = str(Path(raw).expanduser().resolve())
        self._timeout_ms = max(1, int(timeout_ms))
        if not Path(self.db_path).is_file():
            raise UniversalRouteAcceptanceReadError(
                UNIVERSAL_ROUTE_ACCEPTANCE_READ_UNAVAILABLE,
                "canonical SQLite database does not exist",
            )

    def capture(
        self,
        request_ids: Mapping[str, str],
    ) -> dict[str, Any]:
        selected = _request_id_mapping(request_ids)
        try:
            with self._read_operation() as connection:
                self._assert_schema_ready(connection)
                migration = connection.execute(
                    "SELECT migration_id, name FROM schema_migrations "
                    "ORDER BY migration_id DESC LIMIT 1"
                ).fetchone()
                routes = {
                    route: self._capture_route(
                        connection,
                        request_id=request_id,
                    )
                    for route, request_id in selected.items()
                }
        except UniversalRouteAcceptanceReadError:
            raise
        except sqlite3.Error as exc:
            raise UniversalRouteAcceptanceReadError(
                UNIVERSAL_ROUTE_ACCEPTANCE_READ_UNAVAILABLE,
                "canonical acceptance evidence could not be read",
            ) from exc
        return {
            "database_kind": "FILE_BACKED_CANONICAL_SQLITE",
            "read_mode": "SQLITE_MODE_RO_QUERY_ONLY_SINGLE_TRANSACTION",
            "migration": {
                "migration_id": int(migration["migration_id"]),
                "name": str(migration["name"]),
            },
            "routes": routes,
        }

    @contextmanager
    def _read_operation(self) -> Iterator[sqlite3.Connection]:
        path = Path(self.db_path).resolve().as_posix()
        uri = f"file:{quote(path, safe='/:')}?mode=ro"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=self._timeout_ms / 1000.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
            if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise UniversalRouteAcceptanceReadError(
                    UNIVERSAL_ROUTE_ACCEPTANCE_READ_UNAVAILABLE,
                    "SQLite query_only mode could not be enabled",
                )
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except UniversalRouteAcceptanceReadError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise UniversalRouteAcceptanceReadError(
                UNIVERSAL_ROUTE_ACCEPTANCE_READ_UNAVAILABLE,
                "canonical SQLite database cannot be opened read-only",
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _assert_schema_ready(connection: sqlite3.Connection) -> None:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = sorted(set(_REQUIRED_TABLE_COLUMNS) - tables)
        drifted: list[str] = []
        for table, required in _REQUIRED_TABLE_COLUMNS.items():
            if table not in tables:
                continue
            actual = {
                str(row["name"])
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            missing = sorted(required - actual)
            if missing:
                drifted.append(f"{table}:{','.join(missing)}")
        if missing_tables or drifted:
            detail = []
            if missing_tables:
                detail.append("missing=" + ",".join(missing_tables))
            if drifted:
                detail.append("columns=" + ";".join(drifted))
            raise UniversalRouteAcceptanceReadError(
                UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_NOT_READY,
                "acceptance schema is not ready: " + " ".join(detail),
            )

    def _capture_route(
        self,
        connection: sqlite3.Connection,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        command_rows = _rows(
            connection,
            """
            SELECT command_id, request_id, surface, route_type, graph_id,
                   graph_revision, created_at_ms
            FROM host_command_journal
            WHERE request_id = ?
            ORDER BY created_at_ms, command_id
            """,
            (request_id,),
        )
        if len(command_rows) != 1:
            return {
                "request_id": request_id,
                "commands": command_rows,
            }
        command = command_rows[0]
        graph_id = str(command["graph_id"])
        revision = int(command["graph_revision"])
        return {
            "request_id": request_id,
            "commands": command_rows,
            "graphs": _rows(
                connection,
                """
                SELECT graph_id, revision, status, status_version
                FROM task_runtime_graph
                WHERE graph_id = ? AND revision = ?
                """,
                (graph_id, revision),
            ),
            "nodes": _rows(
                connection,
                """
                SELECT node_id, status, status_version
                FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (graph_id, revision),
            ),
            "attempts": _rows(
                connection,
                """
                SELECT attempt_id, node_id, attempt_no, status, status_version
                FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id, attempt_no, attempt_id
                """,
                (graph_id, revision),
            ),
            "leases": _rows(
                connection,
                """
                SELECT lease.lease_id, lease.attempt_id, lease.status,
                       lease.status_version
                FROM task_runtime_lease AS lease
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = lease.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                ORDER BY lease.attempt_id, lease.fencing_token, lease.lease_id
                """,
                (graph_id, revision),
            ),
            "operations": _rows(
                connection,
                """
                SELECT operation.reservation_id, operation.attempt_id,
                       operation.status, operation.status_version,
                       operation.receipt_ref
                FROM task_runtime_idempotency_reservation AS operation
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = operation.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                ORDER BY operation.attempt_id, operation.reservation_id
                """,
                (graph_id, revision),
            ),
            "graph_budgets": _rows(
                connection,
                """
                SELECT budget_id, status, status_version, limits_json,
                       reserved_json, consumed_json, attempted_consumed_json,
                       exceeded_dimensions_json
                FROM task_runtime_budget_reservation
                WHERE graph_id = ? AND graph_revision = ?
                  AND node_id IS NULL AND attempt_id IS NULL
                ORDER BY budget_id
                """,
                (graph_id, revision),
                json_fields={
                    "limits_json",
                    "reserved_json",
                    "consumed_json",
                    "attempted_consumed_json",
                    "exceeded_dimensions_json",
                },
            ),
            "budget_usage": _rows(
                connection,
                """
                SELECT usage.budget_id, usage.usage_id, usage.attempt_id,
                       usage.delta_json, usage.accepted,
                       usage.exceeded_dimensions_json,
                       usage.reservation_version_before,
                       usage.reservation_version_after, usage.outbox_id
                FROM task_runtime_budget_usage AS usage
                WHERE usage.graph_id = ? AND usage.graph_revision = ?
                ORDER BY usage.attempt_id, usage.created_ts_ms, usage.usage_id
                """,
                (graph_id, revision),
                json_fields={"delta_json", "exceeded_dimensions_json"},
            ),
            "node_budgets": _rows(
                connection,
                """
                SELECT node_budget_id, parent_budget_id, node_id, attempt_id,
                       limits_json, reserved_json, consumed_json,
                       attempted_consumed_json, exceeded_dimensions_json,
                       usage_version, claim_outbox_id, claim_origin
                FROM task_runtime_node_budget_reservation
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id, attempt_id
                """,
                (graph_id, revision),
                json_fields={
                    "limits_json",
                    "reserved_json",
                    "consumed_json",
                    "attempted_consumed_json",
                    "exceeded_dimensions_json",
                },
            ),
            "node_budget_usage": _rows(
                connection,
                """
                SELECT child.node_budget_id, child.parent_budget_id,
                       child.usage_id, child.attempt_id, child.accepted,
                       child.child_version_before, child.child_version_after,
                       child.parent_outbox_id
                FROM task_runtime_node_budget_usage AS child
                JOIN task_runtime_node_budget_reservation AS reservation
                  ON reservation.node_budget_id = child.node_budget_id
                 AND reservation.parent_budget_id = child.parent_budget_id
                WHERE reservation.graph_id = ?
                  AND reservation.graph_revision = ?
                ORDER BY child.attempt_id, child.child_version_after,
                         child.usage_id
                """,
                (graph_id, revision),
            ),
            "verifications": _rows(
                connection,
                """
                SELECT verification_ref, node_id, attempt_id, target_status,
                       result_sha256, node_outbox_id
                FROM task_runtime_node_verification_decision
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id, created_ts_ms, verification_ref
                """,
                (graph_id, revision),
            ),
            "acceptances": _rows(
                connection,
                """
                SELECT acceptance_ref, node_id, attempt_id, target_status,
                       result_sha256, node_outbox_id
                FROM task_runtime_node_acceptance_decision
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id, created_ts_ms, acceptance_ref
                """,
                (graph_id, revision),
            ),
            "artifacts": _rows(
                connection,
                """
                SELECT artifact_ref, node_id, attempt_id, receipt_ref,
                       output_contract_sha256, payload_sha256
                FROM task_runtime_node_artifact
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id, artifact_ref
                """,
                (graph_id, revision),
            ),
            "worker_projections": _rows(
                connection,
                """
                SELECT projection_id, command_id, request_id, node_id,
                       attempt_id, route_type, decision, payload_digest
                FROM task_runtime_worker_projection
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id, projection_id
                """,
                (graph_id, revision),
            ),
            "effects": _rows(
                connection,
                """
                SELECT effect_id, node_id, attempt_id, effect_type, status,
                       status_version, receipt_ref
                FROM task_runtime_acceptance_effect_intent
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id, effect_type, effect_id
                """,
                (graph_id, revision),
            ),
            "outboxes": _rows(
                connection,
                """
                SELECT outbox_id, aggregate_type, aggregate_id,
                       aggregate_version, event_type, attempt_id, status,
                       status_version, graph_revision
                FROM task_runtime_outbox
                WHERE attempt_id IN (
                    SELECT attempt_id FROM task_runtime_attempt
                    WHERE graph_id = ? AND graph_revision = ?
                )
                   OR (
                       aggregate_type = 'TASK_GRAPH'
                       AND aggregate_id = ?
                       AND graph_revision = ?
                   )
                ORDER BY created_ts_ms, outbox_id
                """,
                (graph_id, revision, graph_id, revision),
            ),
            "current_recovery_holds": _rows(
                connection,
                """
                SELECT hold.case_ref, hold.reservation_id,
                       hold.operation_status, hold.operation_status_version,
                       hold.cancellation_intent_ref
                FROM task_runtime_operation_recovery_hold AS hold
                JOIN task_runtime_idempotency_reservation AS operation
                  ON operation.reservation_id = hold.reservation_id
                 AND operation.status = hold.operation_status
                 AND operation.status_version =
                     hold.operation_status_version
                LEFT JOIN task_runtime_graph_cancellation_intent AS intent
                  ON intent.intent_ref = hold.cancellation_intent_ref
                WHERE hold.graph_id = ? AND hold.graph_revision = ?
                  AND (
                      hold.cancellation_intent_ref IS NULL
                      OR (
                          intent.status = hold.cancellation_status
                          AND intent.status_version =
                              hold.cancellation_status_version
                      )
                  )
                ORDER BY hold.created_ts_ms, hold.case_ref
                """,
                (graph_id, revision),
            ),
            "effect_reconciliation_cases": _rows(
                connection,
                """
                SELECT item.case_id, item.effect_id, item.intent_ref,
                       item.classification,
                       COUNT(proof.proof_id) AS proof_count
                FROM task_runtime_effect_reconciliation_case AS item
                LEFT JOIN task_runtime_effect_reconciliation_proof AS proof
                  ON proof.case_id = item.case_id
                WHERE item.graph_id = ? AND item.graph_revision = ?
                GROUP BY item.case_id, item.effect_id, item.intent_ref,
                         item.classification
                ORDER BY item.created_ts_ms, item.case_id
                """,
                (graph_id, revision),
            ),
            "cancellation_intents": _rows(
                connection,
                """
                SELECT intent_ref, status, status_version
                FROM task_runtime_graph_cancellation_intent
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY requested_ts_ms, intent_ref
                """,
                (graph_id, revision),
            ),
        }


def _request_id_mapping(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise UniversalRouteAcceptanceReadError(
            UNIVERSAL_ROUTE_ACCEPTANCE_READ_INVALID,
            "request_ids must be a mapping",
        )
    normalized: dict[str, str] = {}
    for raw_route, raw_request_id in value.items():
        route = str(raw_route or "").strip().lower()
        request_id = str(raw_request_id or "").strip()
        if not route or not request_id or len(request_id) > 240:
            raise UniversalRouteAcceptanceReadError(
                UNIVERSAL_ROUTE_ACCEPTANCE_READ_INVALID,
                "route and request_id must be bounded non-empty strings",
            )
        normalized[route] = request_id
    if len(set(normalized.values())) != len(normalized):
        raise UniversalRouteAcceptanceReadError(
            UNIVERSAL_ROUTE_ACCEPTANCE_READ_INVALID,
            "one request_id cannot prove multiple logical routes",
        )
    return normalized


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...],
    *,
    json_fields: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for row in connection.execute(sql, parameters).fetchall():
        item = dict(row)
        for field in json_fields:
            raw = item.get(field)
            try:
                import json

                item[field.removesuffix("_json")] = json.loads(str(raw))
            except (TypeError, ValueError) as exc:
                raise UniversalRouteAcceptanceReadError(
                    UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_NOT_READY,
                    f"{field} contains invalid JSON",
                ) from exc
            del item[field]
        values.append(item)
    return values


__all__ = [
    "UNIVERSAL_ROUTE_ACCEPTANCE_READ_INVALID",
    "UNIVERSAL_ROUTE_ACCEPTANCE_READ_UNAVAILABLE",
    "UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_NOT_READY",
    "UniversalRouteAcceptanceReadError",
    "UniversalRouteAcceptanceReader",
]
