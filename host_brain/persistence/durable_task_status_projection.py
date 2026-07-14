"""Read-only progress projection for the canonical governed Task Runtime.

The projection deliberately owns no lifecycle state.  Every answer is rebuilt
from one SQLite read snapshot spanning Host admission and the durable graph,
node, attempt, reservation, budget, and receipt records.  Recreating this
object after a process restart therefore produces the same answer for the same
database state.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
from urllib.parse import unquote

from host_brain.core.ids import now_ms


DURABLE_TASK_STATUS_INVALID = "DURABLE_TASK_STATUS_INVALID"
DURABLE_TASK_STATUS_UNAVAILABLE = "DURABLE_TASK_STATUS_UNAVAILABLE"
DURABLE_TASK_STATUS_SCHEMA_NOT_READY = "DURABLE_TASK_STATUS_SCHEMA_NOT_READY"

_REQUIRED_TABLES = frozenset(
    {
        "host_command_journal",
        "task_runtime_graph",
        "task_runtime_node",
        "task_runtime_attempt",
        "task_runtime_idempotency_reservation",
        "task_runtime_budget_reservation",
        "task_runtime_worker_projection",
    }
)
_TERMINAL_GRAPH_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
_ACTIVE_NODE_STATUSES = frozenset(
    {
        "READY",
        "WAITING_APPROVAL",
        "LEASED",
        "RUNNING",
        "EXECUTED",
        "VERIFYING",
        "VERIFIED",
        "WAITING_ACCEPTANCE_REVIEW",
        "RETRY_PENDING",
        "CANCEL_REQUESTED",
    }
)
_FAILED_NODE_STATUSES = frozenset(
    {"FAILED", "VERIFICATION_FAILED", "CANCELLED"}
)


class DurableTaskStatusProjectionError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or DURABLE_TASK_STATUS_UNAVAILABLE)
        self.detail = str(detail or "durable task status projection failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class DurableTaskStatusProjection:
    """Query-only replacement for ``RuntimeTaskRegistry`` progress reads."""

    def __init__(
        self,
        canonical_db_path: str | Path,
        *,
        timeout_ms: int = 3_000,
        clock: Any = now_ms,
        _borrowed_connection: sqlite3.Connection | None = None,
        _borrowed_lock: Any | None = None,
        verify_schema: bool = True,
    ) -> None:
        raw_path = str(canonical_db_path or "").strip()
        if not raw_path or raw_path.lower().startswith("file:"):
            raise DurableTaskStatusProjectionError(
                DURABLE_TASK_STATUS_INVALID,
                "canonical database path is required",
            )
        if raw_path == ":memory:" and _borrowed_connection is None:
            raise DurableTaskStatusProjectionError(
                DURABLE_TASK_STATUS_INVALID,
                "in-memory reads require the owning ledger connection",
            )
        self.canonical_db_path: str | Path = (
            raw_path if raw_path == ":memory:" else Path(raw_path).expanduser().resolve()
        )
        self._timeout_ms = max(1, int(timeout_ms))
        self._clock = clock
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock or threading.RLock()
        if (
            isinstance(self.canonical_db_path, Path)
            and not self.canonical_db_path.is_file()
        ):
            raise DurableTaskStatusProjectionError(
                DURABLE_TASK_STATUS_UNAVAILABLE,
                f"canonical database does not exist: {self.canonical_db_path}",
            )
        if verify_schema:
            self.assert_schema_ready()

    @classmethod
    def from_ledger(
        cls,
        ledger: Any,
        *,
        timeout_ms: int = 3_000,
        clock: Any = now_ms,
        verify_schema: bool = True,
    ) -> "DurableTaskStatusProjection":
        canonical_path = str(
            getattr(ledger, "canonical_db_path", "")
            or getattr(ledger, "db_path", "")
            or ""
        ).strip()
        if not canonical_path:
            raise DurableTaskStatusProjectionError(
                DURABLE_TASK_STATUS_INVALID,
                "ledger does not expose canonical_db_path",
            )
        if canonical_path != ":memory:":
            return cls(
                canonical_path,
                timeout_ms=timeout_ms,
                clock=clock,
                verify_schema=verify_schema,
            )
        connection = getattr(ledger, "_conn", None)
        if connection is None:
            raise DurableTaskStatusProjectionError(
                DURABLE_TASK_STATUS_INVALID,
                "in-memory ledger does not expose its owning connection",
            )
        return cls(
            canonical_path,
            timeout_ms=timeout_ms,
            clock=clock,
            _borrowed_connection=connection,
            _borrowed_lock=getattr(ledger, "_write_lock", None),
            verify_schema=verify_schema,
        )

    def close(self) -> None:
        """No-op: file-backed reads own one operation-scoped connection."""

    def assert_schema_ready(self) -> None:
        with self._snapshot() as conn:
            available = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        missing = sorted(_REQUIRED_TABLES - available)
        if missing:
            raise DurableTaskStatusProjectionError(
                DURABLE_TASK_STATUS_SCHEMA_NOT_READY,
                "durable status sources are missing: " + ",".join(missing),
            )

    def get_by_request_id(
        self,
        request_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        request_key = _required_text(request_id, field_name="request_id")
        session_key = str(session_id or "").strip()
        with self._snapshot() as conn:
            row = conn.execute(
                """
                SELECT
                    h.command_id, h.request_id, h.session_id, h.principal_id,
                    h.route_type, h.target_id, h.graph_id, h.graph_revision,
                    h.created_at_ms, g.root_work_order_id,
                    g.status AS graph_status, g.created_ts_ms,
                    g.updated_ts_ms
                FROM host_command_journal AS h
                JOIN task_runtime_graph AS g
                  ON g.graph_id = h.graph_id
                 AND g.revision = h.graph_revision
                WHERE h.request_id = ?
                  AND (? = '' OR h.session_id = ?)
                ORDER BY h.created_at_ms DESC, h.graph_revision DESC, h.command_id
                LIMIT 1
                """,
                (request_key, session_key, session_key),
            ).fetchone()
            if row is None:
                return None
            return self._build_summary(conn, row)

    def summarize_for_session(
        self,
        *,
        session_id: str,
        person_id: str | None = None,
        exclude_request_id: str | None = None,
    ) -> dict[str, Any] | None:
        session_key = _required_text(session_id, field_name="session_id")
        person_key = str(person_id or "").strip()
        exclude_key = str(exclude_request_id or "").strip()
        with self._snapshot() as conn:
            rows = conn.execute(
                """
                SELECT
                    h.command_id, h.request_id, h.session_id, h.principal_id,
                    h.route_type, h.target_id, h.graph_id, h.graph_revision,
                    h.created_at_ms, g.root_work_order_id,
                    g.status AS graph_status, g.created_ts_ms,
                    g.updated_ts_ms
                FROM host_command_journal AS h
                JOIN task_runtime_graph AS g
                  ON g.graph_id = h.graph_id
                 AND g.revision = h.graph_revision
                WHERE h.session_id = ?
                  AND (? = '' OR h.principal_id = ?)
                  AND (? = '' OR h.request_id <> ?)
                ORDER BY
                    CASE WHEN g.status IN ('CREATED', 'ACTIVE', 'BLOCKED')
                         THEN 0 ELSE 1 END,
                    g.updated_ts_ms DESC,
                    h.created_at_ms DESC,
                    h.command_id
                LIMIT 1
                """,
                (
                    session_key,
                    person_key,
                    person_key,
                    exclude_key,
                    exclude_key,
                ),
            ).fetchall()
            if not rows:
                return None
            return self._build_summary(conn, rows[0])

    def summarize_for_chat(
        self,
        *,
        person_id: str,
        session_id: str,
        exclude_request_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Compatibility spelling for progress renderers; remains read-only."""

        return self.summarize_for_session(
            session_id=session_id,
            person_id=person_id,
            exclude_request_id=exclude_request_id,
        )

    def _build_summary(
        self,
        conn: sqlite3.Connection,
        admission: sqlite3.Row,
    ) -> dict[str, Any]:
        graph_id = str(admission["graph_id"])
        revision = int(admission["graph_revision"])
        nodes = conn.execute(
            """
            SELECT node_id, status, node_json, created_ts_ms, updated_ts_ms
            FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY created_ts_ms, node_id
            """,
            (graph_id, revision),
        ).fetchall()
        attempts = conn.execute(
            """
            SELECT attempt_id, node_id, attempt_no, status, owner_id,
                   error_ref, created_ts_ms, updated_ts_ms, completed_ts_ms
            FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY attempt_no, created_ts_ms, attempt_id
            """,
            (graph_id, revision),
        ).fetchall()
        reservations = conn.execute(
            """
            SELECT r.reservation_id, r.attempt_id, r.status, r.receipt_ref,
                   r.recovery_reason_code, r.updated_ts_ms
            FROM task_runtime_idempotency_reservation AS r
            JOIN task_runtime_attempt AS a ON a.attempt_id = r.attempt_id
            WHERE a.graph_id = ? AND a.graph_revision = ?
            ORDER BY r.updated_ts_ms, r.reservation_id
            """,
            (graph_id, revision),
        ).fetchall()
        budgets = conn.execute(
            """
            SELECT budget_id, node_id, attempt_id, status, consumed_json,
                   attempted_consumed_json, exceeded_dimensions_json,
                   updated_ts_ms
            FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY updated_ts_ms, budget_id
            """,
            (graph_id, revision),
        ).fetchall()
        worker_receipts = conn.execute(
            """
            SELECT projection_id, node_id, attempt_id, decision,
                   target_id, projection_json, created_ts_ms
            FROM task_runtime_worker_projection
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY created_ts_ms, projection_id
            """,
            (graph_id, revision),
        ).fetchall()

        optional_tables = _table_names(conn)
        acceptance_receipts: list[sqlite3.Row] = []
        if "task_runtime_acceptance_effect_intent" in optional_tables:
            acceptance_receipts = conn.execute(
                """
                SELECT effect_id, status, receipt_ref, updated_ts_ms
                FROM task_runtime_acceptance_effect_intent
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY updated_ts_ms, effect_id
                """,
                (graph_id, revision),
            ).fetchall()
        agent_result: sqlite3.Row | None = None
        if "agent_execution_result" in optional_tables:
            agent_result = conn.execute(
                """
                SELECT result_status, result_json, receipt_ref, created_at_ms
                FROM agent_execution_result
                WHERE command_id = ?
                LIMIT 1
                """,
                (str(admission["command_id"]),),
            ).fetchone()

        graph_status = str(admission["graph_status"] or "CREATED").upper()
        node_counts = Counter(str(row["status"] or "UNKNOWN").upper() for row in nodes)
        attempt_counts = Counter(
            str(row["status"] or "UNKNOWN").upper() for row in attempts
        )
        reservation_counts = Counter(
            str(row["status"] or "UNKNOWN").upper() for row in reservations
        )
        budget_counts = Counter(
            str(row["status"] or "UNKNOWN").upper() for row in budgets
        )
        status = _public_status(
            graph_status=graph_status,
            node_counts=node_counts,
            attempt_counts=attempt_counts,
            reservation_counts=reservation_counts,
        )
        active_node = next(
            (
                row
                for row in reversed(nodes)
                if str(row["status"] or "").upper() in _ACTIVE_NODE_STATUSES
            ),
            None,
        )
        target_id = str(admission["target_id"] or "").strip()
        if active_node is not None:
            target_id = _node_target(active_node) or target_id

        created_at_ms = min(
            int(admission["created_at_ms"]),
            int(admission["created_ts_ms"]),
        )
        updated_at_ms = max(
            [int(admission["updated_ts_ms"])]
            + [int(row["updated_ts_ms"]) for row in nodes]
            + [int(row["updated_ts_ms"]) for row in attempts]
            + [int(row["updated_ts_ms"]) for row in reservations]
            + [int(row["updated_ts_ms"]) for row in budgets]
        )
        terminal_at_ms = updated_at_ms if graph_status in _TERMINAL_GRAPH_STATUSES else None
        elapsed_end = terminal_at_ms if terminal_at_ms is not None else int(self._clock())
        elapsed_ms = max(0, elapsed_end - created_at_ms)
        result_text = _result_text(
            graph_status=graph_status,
            node_count=len(nodes),
            worker_receipts=worker_receipts,
            agent_result=agent_result,
        )
        error = next(
            (
                str(row["error_ref"])
                for row in reversed(attempts)
                if str(row["error_ref"] or "").strip()
            ),
            None,
        )
        receipt_refs = _dedupe(
            [str(row["receipt_ref"] or "") for row in reservations]
            + [str(row["projection_id"] or "") for row in worker_receipts]
            + [str(row["receipt_ref"] or "") for row in acceptance_receipts]
            + ([str(agent_result["receipt_ref"] or "")] if agent_result else [])
        )
        progress_message = _progress_message(
            status=status,
            graph_status=graph_status,
            nodes=nodes,
            node_counts=node_counts,
            active_node=active_node,
            target_id=target_id,
            error=error,
        )
        detail = _detail(
            graph_id=graph_id,
            revision=revision,
            node_count=len(nodes),
            node_counts=node_counts,
        )
        node_summaries = tuple(_node_summary(row) for row in nodes)
        return {
            "found": True,
            "source": "durable_task_runtime",
            "task_id": f"taskgraph://{graph_id}/revisions/{revision}",
            "task_kind": str(admission["route_type"] or "task").strip() or "task",
            "route_type": str(admission["route_type"] or "").strip(),
            "target_id": target_id,
            "request_id": str(admission["request_id"]),
            "session_id": str(admission["session_id"]),
            "person_id": str(admission["principal_id"]),
            "command_id": str(admission["command_id"]),
            "root_work_order_id": str(admission["root_work_order_id"]),
            "graph_id": graph_id,
            "graph_revision": revision,
            "graph_status": graph_status,
            "status": status,
            "detail": detail,
            "progress_message": progress_message,
            "result_code": graph_status if graph_status in _TERMINAL_GRAPH_STATUSES else None,
            "error": error,
            "result_text": result_text,
            "created_ts_ms": created_at_ms,
            "updated_ts_ms": updated_at_ms,
            "finished_ts_ms": terminal_at_ms,
            "elapsed_ms": elapsed_ms,
            "node_count": len(nodes),
            "node_status_counts": dict(sorted(node_counts.items())),
            "attempt_count": len(attempts),
            "attempt_status_counts": dict(sorted(attempt_counts.items())),
            "reservation_status_counts": dict(sorted(reservation_counts.items())),
            "budget_status_counts": dict(sorted(budget_counts.items())),
            "receipt_refs": receipt_refs,
            "child_tasks": list(node_summaries),
        }

    def _connect_read_only(self) -> sqlite3.Connection:
        assert isinstance(self.canonical_db_path, Path)
        try:
            conn = sqlite3.connect(
                self.canonical_db_path.as_uri() + "?mode=ro",
                uri=True,
                timeout=self._timeout_ms / 1000.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
            return conn
        except sqlite3.DatabaseError as exc:
            raise DurableTaskStatusProjectionError(
                DURABLE_TASK_STATUS_UNAVAILABLE,
                "canonical database could not be opened read-only",
            ) from exc

    @contextmanager
    def _snapshot(self) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with self._borrowed_lock:
                started = not self._borrowed_connection.in_transaction
                if started:
                    self._borrowed_connection.execute("BEGIN")
                try:
                    yield self._borrowed_connection
                except sqlite3.DatabaseError as exc:
                    raise DurableTaskStatusProjectionError(
                        DURABLE_TASK_STATUS_UNAVAILABLE,
                        "durable status snapshot read failed",
                    ) from exc
                finally:
                    if started and self._borrowed_connection.in_transaction:
                        self._borrowed_connection.rollback()
            return
        conn = self._connect_read_only()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except DurableTaskStatusProjectionError:
            conn.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            raise DurableTaskStatusProjectionError(
                DURABLE_TASK_STATUS_UNAVAILABLE,
                "durable status snapshot read failed",
            ) from exc
        finally:
            conn.close()


def _required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DurableTaskStatusProjectionError(
            DURABLE_TASK_STATUS_INVALID,
            f"{field_name} is required",
        )
    return text


def _table_names(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _node_target(row: sqlite3.Row) -> str:
    route = str(_json_object(row["node_json"]).get("route") or "").strip()
    if ":" not in route:
        return route
    return unquote(route.split(":", 1)[1]).strip()


def _node_summary(row: sqlite3.Row) -> dict[str, Any]:
    node = _json_object(row["node_json"])
    return {
        "node_id": str(row["node_id"]),
        "status": str(row["status"]),
        "target_id": _node_target(row),
        "route": str(node.get("route") or ""),
        "dependencies": list(node.get("dependencies") or []),
        "created_ts_ms": int(row["created_ts_ms"]),
        "updated_ts_ms": int(row["updated_ts_ms"]),
    }


def _public_status(
    *,
    graph_status: str,
    node_counts: Counter[str],
    attempt_counts: Counter[str],
    reservation_counts: Counter[str],
) -> str:
    if graph_status == "COMPLETED":
        return "COMPLETED"
    if graph_status in {"FAILED", "CANCELLED"}:
        return "FAILED"
    recovering = bool(
        node_counts["RETRY_PENDING"]
        or attempt_counts["RECOVERABLE"]
        or reservation_counts["RECOVERY_REQUIRED"]
    )
    return "RECOVERING" if recovering else "RUNNING"


def _detail(
    *,
    graph_id: str,
    revision: int,
    node_count: int,
    node_counts: Counter[str],
) -> str:
    accepted = node_counts["ACCEPTED"]
    failed = sum(node_counts[status] for status in _FAILED_NODE_STATUSES)
    text = f"TaskGraph {graph_id} rev {revision}：{accepted}/{node_count} 个节点已验收"
    if failed:
        text += f"，{failed} 个节点失败"
    return text + "。"


def _progress_message(
    *,
    status: str,
    graph_status: str,
    nodes: list[sqlite3.Row],
    node_counts: Counter[str],
    active_node: sqlite3.Row | None,
    target_id: str,
    error: str | None,
) -> str:
    if status == "COMPLETED":
        return f"已完成并验收全部 {len(nodes)} 个节点。"
    if status == "FAILED":
        failed = sum(node_counts[value] for value in _FAILED_NODE_STATUSES)
        suffix = f"：{error}" if error else "。"
        return f"任务图以 {graph_status} 结束，失败节点 {failed} 个{suffix}"
    if active_node is not None:
        node_status = str(active_node["status"] or "RUNNING").upper()
        return (
            f"节点 {active_node['node_id']}（{target_id}）处于 {node_status}；"
            f"已验收 {node_counts['ACCEPTED']}/{len(nodes)} 个节点。"
        )
    return f"任务图处于 {graph_status}；已验收 {node_counts['ACCEPTED']}/{len(nodes)} 个节点。"


def _result_text(
    *,
    graph_status: str,
    node_count: int,
    worker_receipts: list[sqlite3.Row],
    agent_result: sqlite3.Row | None,
) -> str:
    if agent_result is not None:
        result = _json_object(agent_result["result_json"])
        text = str(result.get("result_text") or result.get("summary") or "").strip()
        if text:
            return text
    for row in reversed(worker_receipts):
        projection = _json_object(row["projection_json"])
        text = str(projection.get("narrative") or "").strip()
        if text:
            return text
    if graph_status == "COMPLETED":
        return f"任务图已完成，共 {node_count} 个节点。"
    return ""


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


__all__ = [
    "DURABLE_TASK_STATUS_INVALID",
    "DURABLE_TASK_STATUS_SCHEMA_NOT_READY",
    "DURABLE_TASK_STATUS_UNAVAILABLE",
    "DurableTaskStatusProjection",
    "DurableTaskStatusProjectionError",
]
