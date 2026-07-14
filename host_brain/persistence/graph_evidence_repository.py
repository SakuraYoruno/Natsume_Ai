"""Catalog-owned SQLite repository for the nine Graph Evidence record types."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any

from host_brain.core.ids import now_ms


GRAPH_EVIDENCE_INVALID = "GRAPH_EVIDENCE_INVALID"
GRAPH_EVIDENCE_SCHEMA_NOT_READY = "GRAPH_EVIDENCE_SCHEMA_NOT_READY"
GRAPH_EVIDENCE_PERSISTENCE_FAILED = "GRAPH_EVIDENCE_PERSISTENCE_FAILED"
GRAPH_EVIDENCE_MIGRATION_ASSET_KEY = "0074#graph_evidence_persistence"
GRAPH_EVIDENCE_MIGRATION_SHA256 = (
    "9c64bfce0c495a33350935eb31283b1b21af57692f949cc50208000dde0d9f4e"
)

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "graph_trace": frozenset(
        {"id", "request_id", "graph_id", "node_id", "status", "reason_code", "detail", "ts_ms"}
    ),
    "subgraph_summary": frozenset(
        {
            "id",
            "request_id",
            "subgraph_id",
            "node_count",
            "success_count",
            "fail_count",
            "committed_at_ms",
            "graph_trace_flat_json",
        }
    ),
    "clock_events": frozenset(
        {
            "id",
            "request_id",
            "graph_id",
            "event_type",
            "node_id",
            "elapsed_ms",
            "threshold_ms",
            "ts_ms",
        }
    ),
    "branch_manifest": frozenset(
        {
            "id",
            "request_id",
            "graph_id",
            "total_count",
            "success_count",
            "fail_count",
            "committed_at_ms",
            "branches_json",
        }
    ),
    "stream_events": frozenset(
        {"id", "request_id", "graph_id", "phase", "event_type", "node_id", "chunk", "ts_ms"}
    ),
    "checkpoints": frozenset(
        {
            "checkpoint_id",
            "request_id",
            "graph_id",
            "interrupt_reason",
            "resume_deadline_at",
            "created_at_ms",
        }
    ),
    "barrier_join_log": frozenset(
        {
            "id",
            "request_id",
            "graph_id",
            "passed",
            "failed_branches_json",
            "join_policy_ref",
            "reason",
            "ts_ms",
        }
    ),
    "subgraph_trace": frozenset(
        {
            "id",
            "run_id",
            "parent_path",
            "node_id",
            "input_digest",
            "output_digest",
            "latency_ms",
            "ts_ms",
        }
    ),
    "llm_call_log": frozenset(
        {
            "id",
            "run_id",
            "node_id",
            "model_id",
            "prompt_tokens",
            "completion_tokens",
            "latency_ms",
            "ts_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_graph_trace_request",
        "idx_subgraph_summary_request",
        "idx_clock_events_request",
        "idx_branch_manifest_request",
        "idx_stream_events_request",
        "idx_stream_events_time_type",
        "idx_checkpoints_request",
        "idx_bjl_run_id",
        "idx_bjl_ts",
        "idx_sgt_run_id",
        "idx_sgt_parent_path",
        "idx_lcl_run_id",
        "idx_lcl_node_id",
    }
)


class GraphEvidenceRepositoryError(RuntimeError):
    """Stable persistence failure returned to the integration facade."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GRAPH_EVIDENCE_PERSISTENCE_FAILED)
        self.detail = str(detail or "graph evidence persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class GraphEvidenceRepository:
    """Semantic Graph Evidence operations with one UoW per repository call."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 3_000,
        fault_injector: Callable[[str], None] | None = None,
        _borrowed_connection: sqlite3.Connection | None = None,
        _borrowed_lock: Any | None = None,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path.lower().startswith("file:"):
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_INVALID,
                "db_path must be a SQLite file path or :memory:",
            )
        if not callable(clock):
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_INVALID,
                "clock must be callable",
            )
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        self._fault_injector = fault_injector
        self._memory_connection: sqlite3.Connection | None = None
        self._memory_lock = threading.RLock()
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock

        if _borrowed_connection is not None:
            if raw_path != ":memory:" or _borrowed_lock is None:
                raise GraphEvidenceRepositoryError(
                    GRAPH_EVIDENCE_INVALID,
                    "borrowed graph evidence connection requires in-memory ledger lock",
                )
            self.db_path = raw_path
            self._configure(_borrowed_connection)
            self._apply_memory_asset()
            self.assert_schema_ready(require_catalog=False)
            return

        if raw_path == ":memory:":
            self.db_path: str | Path = raw_path
            self._memory_connection = sqlite3.connect(
                raw_path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._configure(self._memory_connection)
            try:
                self._apply_memory_asset()
                self.assert_schema_ready(require_catalog=False)
            except BaseException:
                self._memory_connection.close()
                self._memory_connection = None
                raise
        else:
            self.db_path = Path(raw_path).expanduser().resolve()
            self.assert_schema_ready(require_catalog=True)

    @classmethod
    def from_ledger(cls, ledger: Any) -> "GraphEvidenceRepository":
        """Compose the typed repository from the canonical ledger boundary."""

        canonical = str(
            getattr(ledger, "canonical_db_path", "")
            or getattr(ledger, "db_path", "")
            or ""
        ).strip()
        if not canonical:
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_INVALID,
                "ledger does not expose a canonical database path",
            )
        if canonical == ":memory:":
            connection = getattr(ledger, "_conn", None)
            lock = getattr(ledger, "_write_lock", None)
            if connection is None or lock is None:
                raise GraphEvidenceRepositoryError(
                    GRAPH_EVIDENCE_INVALID,
                    "in-memory ledger does not expose its compatibility connection",
                )
            return cls(
                canonical,
                _borrowed_connection=connection,
                _borrowed_lock=lock,
            )
        return cls(canonical)

    @staticmethod
    def _migration_asset() -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / "0074_graph_evidence_persistence.sql"
        )

    def _apply_memory_asset(self) -> None:
        asset = self._migration_asset()
        try:
            sql = asset.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_SCHEMA_NOT_READY,
                "0074 migration asset is unavailable",
            ) from exc
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if checksum != GRAPH_EVIDENCE_MIGRATION_SHA256:
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_SCHEMA_NOT_READY,
                "0074 migration checksum drifted",
            )
        connection = self._memory_connection or self._borrowed_connection
        assert connection is not None
        try:
            connection.executescript(sql)
        except sqlite3.DatabaseError as exc:
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_SCHEMA_NOT_READY,
                "0074 in-memory migration failed",
            ) from exc

    def _configure(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with self._borrowed_lock:
                yield self._borrowed_connection
            return
        if self._memory_connection is not None:
            with self._memory_lock:
                yield self._memory_connection
            return
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        self._configure(conn)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self, operation: str) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._fault(f"{operation}:begin")
                yield conn
                self._fault(f"{operation}:before_commit")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(str(stage))

    def _now(self) -> int:
        try:
            value = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_INVALID,
                "clock returned an invalid timestamp",
            ) from exc
        if value < 0:
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_INVALID,
                "clock returned a negative timestamp",
            )
        return value

    def assert_schema_ready(self, *, require_catalog: bool = True) -> None:
        if require_catalog and (
            not isinstance(self.db_path, Path) or not self.db_path.is_file()
        ):
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_SCHEMA_NOT_READY,
                f"graph evidence database does not exist: {self.db_path}",
            )
        try:
            with self._connection() as conn:
                columns = {
                    table: {
                        str(row[1])
                        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    }
                    for table in _REQUIRED_TABLE_COLUMNS
                }
                indexes = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                asset = None
                if require_catalog:
                    asset = conn.execute(
                        """
                        SELECT checksum, disposition
                        FROM schema_migration_assets
                        WHERE asset_key = ?
                        """,
                        (GRAPH_EVIDENCE_MIGRATION_ASSET_KEY,),
                    ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_SCHEMA_NOT_READY,
                "graph evidence schema or migration catalog is unavailable",
            ) from exc

        invalid_tables = [
            table
            for table, required in _REQUIRED_TABLE_COLUMNS.items()
            if not required.issubset(columns[table])
        ]
        if invalid_tables or not _REQUIRED_INDEXES.issubset(indexes):
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_SCHEMA_NOT_READY,
                "graph evidence schema does not match migration 0074",
            )
        if require_catalog and (
            asset is None
            or str(asset["checksum"]) != GRAPH_EVIDENCE_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
        ):
            raise GraphEvidenceRepositoryError(
                GRAPH_EVIDENCE_SCHEMA_NOT_READY,
                "graph evidence migration 0074 is not catalog-authorized",
            )

    def append_traces(
        self,
        *,
        request_id: str,
        graph_id: str,
        trace: Sequence[Mapping[str, Any]],
    ) -> int:
        default_ts = self._now()
        rows = [
            (
                str(request_id),
                str(graph_id),
                str(entry.get("node_id") or ""),
                str(entry.get("status") or "OK"),
                entry.get("reason_code"),
                str(entry.get("detail") or ""),
                int(entry.get("ts_ms") or default_ts),
            )
            for entry in trace
            if isinstance(entry, Mapping)
        ]
        if not rows:
            return 0
        with self._transaction("append_traces") as conn:
            conn.executemany(
                """
                INSERT INTO graph_trace(
                    request_id, graph_id, node_id, status, reason_code, detail, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def append_subgraph_summary(
        self,
        *,
        request_id: str,
        summary: Mapping[str, Any],
    ) -> None:
        with self._transaction("append_subgraph_summary") as conn:
            conn.execute(
                """
                INSERT INTO subgraph_summary(
                    request_id, subgraph_id, node_count, success_count, fail_count,
                    committed_at_ms, graph_trace_flat_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(request_id),
                    str(summary.get("subgraph_id") or ""),
                    int(summary.get("node_count") or 0),
                    int(summary.get("success_count") or 0),
                    int(summary.get("fail_count") or 0),
                    int(summary.get("committed_at_ms") or self._now()),
                    _json(summary.get("graph_trace_flat") or []),
                ),
            )

    def append_clock_events(
        self,
        *,
        request_id: str,
        graph_id: str,
        events: Sequence[Mapping[str, Any]],
    ) -> int:
        default_ts = self._now()
        rows = [
            (
                str(request_id),
                str(graph_id),
                str(event.get("event_type") or ""),
                str(event.get("node_id") or ""),
                int(event.get("elapsed_ms") or 0),
                int(event.get("threshold_ms") or 0),
                int(event.get("ts_ms") or default_ts),
            )
            for event in events
            if isinstance(event, Mapping)
        ]
        if not rows:
            return 0
        with self._transaction("append_clock_events") as conn:
            conn.executemany(
                """
                INSERT INTO clock_events(
                    request_id, graph_id, event_type, node_id, elapsed_ms,
                    threshold_ms, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def append_branch_manifest(
        self,
        *,
        request_id: str,
        graph_id: str,
        manifest: Mapping[str, Any],
    ) -> None:
        with self._transaction("append_branch_manifest") as conn:
            conn.execute(
                """
                INSERT INTO branch_manifest(
                    request_id, graph_id, total_count, success_count, fail_count,
                    committed_at_ms, branches_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(request_id),
                    str(graph_id),
                    int(manifest.get("total_count") or 0),
                    int(manifest.get("success_count") or 0),
                    int(manifest.get("fail_count") or 0),
                    int(manifest.get("committed_at_ms") or self._now()),
                    _json(manifest.get("branches") or []),
                ),
            )

    def append_stream_events(
        self,
        *,
        request_id: str,
        graph_id: str,
        events: Sequence[Mapping[str, Any]],
    ) -> int:
        default_ts = self._now()
        rows = [
            (
                str(request_id),
                str(graph_id),
                str(event.get("phase") or ""),
                str(event.get("event_type") or ""),
                str(event.get("node_id") or ""),
                str(event.get("chunk") or ""),
                int(event.get("ts_ms") or default_ts),
            )
            for event in events
            if isinstance(event, Mapping)
        ]
        if not rows:
            return 0
        with self._transaction("append_stream_events") as conn:
            conn.executemany(
                """
                INSERT INTO stream_events(
                    request_id, graph_id, phase, event_type, node_id, chunk, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def save_checkpoint(
        self,
        *,
        checkpoint_id: str,
        request_id: str,
        graph_id: str,
        interrupt_reason: str,
        resume_deadline_at: int,
    ) -> None:
        with self._transaction("save_checkpoint") as conn:
            conn.execute(
                """
                INSERT INTO checkpoints(
                    checkpoint_id, request_id, graph_id, interrupt_reason,
                    resume_deadline_at, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    request_id = excluded.request_id,
                    graph_id = excluded.graph_id,
                    interrupt_reason = excluded.interrupt_reason,
                    resume_deadline_at = excluded.resume_deadline_at,
                    created_at_ms = excluded.created_at_ms
                """,
                (
                    str(checkpoint_id),
                    str(request_id),
                    str(graph_id),
                    str(interrupt_reason),
                    int(resume_deadline_at),
                    self._now(),
                ),
            )

    def append_barrier_join(
        self,
        *,
        request_id: str,
        graph_id: str,
        result: Mapping[str, Any],
    ) -> None:
        with self._transaction("append_barrier_join") as conn:
            conn.execute(
                """
                INSERT INTO barrier_join_log(
                    request_id, graph_id, passed, failed_branches_json,
                    join_policy_ref, reason, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(request_id),
                    str(graph_id),
                    int(bool(result.get("passed"))),
                    json.dumps(
                        list(result.get("failed_branches") or []),
                        ensure_ascii=False,
                    ),
                    str(result.get("join_policy_ref") or ""),
                    str(result.get("reason") or ""),
                    self._now(),
                ),
            )

    def append_subgraph_traces(
        self,
        *,
        run_id: str,
        trace: Sequence[Mapping[str, Any]],
        parent_path: str | None = None,
    ) -> int:
        default_ts = self._now()
        rows = [
            (
                str(run_id),
                str(entry.get("parent_path") or parent_path or ""),
                str(entry.get("node_id") or ""),
                entry.get("input_digest"),
                entry.get("output_digest"),
                int(entry.get("latency_ms") or 0),
                int(entry.get("ts_ms") or default_ts),
            )
            for entry in trace
            if isinstance(entry, Mapping)
        ]
        if not rows:
            return 0
        with self._transaction("append_subgraph_traces") as conn:
            conn.executemany(
                """
                INSERT INTO subgraph_trace(
                    run_id, parent_path, node_id, input_digest, output_digest,
                    latency_ms, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def append_llm_call(
        self,
        *,
        run_id: str,
        node_id: str,
        call: Mapping[str, Any],
    ) -> None:
        with self._transaction("append_llm_call") as conn:
            conn.execute(
                """
                INSERT INTO llm_call_log(
                    run_id, node_id, model_id, prompt_tokens, completion_tokens,
                    latency_ms, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    str(node_id),
                    call.get("model_id"),
                    int(call.get("prompt_tokens") or 0),
                    int(call.get("completion_tokens") or 0),
                    int(call.get("latency_ms") or 0),
                    int(call.get("ts_ms") or self._now()),
                ),
            )

    def list_traces(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._list_by_key(
            """
            SELECT request_id, graph_id, node_id, status, reason_code, detail, ts_ms
            FROM graph_trace WHERE request_id = ? ORDER BY id
            """,
            request_id,
        )

    def list_subgraph_summaries(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._list_by_key(
            """
            SELECT request_id, subgraph_id, node_count, success_count, fail_count,
                   committed_at_ms, graph_trace_flat_json
            FROM subgraph_summary WHERE request_id = ? ORDER BY id
            """,
            request_id,
        )

    def list_clock_events(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._list_by_key(
            """
            SELECT request_id, graph_id, event_type, node_id, elapsed_ms,
                   threshold_ms, ts_ms
            FROM clock_events WHERE request_id = ? ORDER BY id
            """,
            request_id,
        )

    def list_branch_manifests(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._list_by_key(
            """
            SELECT request_id, graph_id, total_count, success_count, fail_count,
                   committed_at_ms, branches_json
            FROM branch_manifest WHERE request_id = ? ORDER BY id
            """,
            request_id,
        )

    def list_stream_events(self, *, request_id: str) -> list[dict[str, Any]]:
        return self._list_by_key(
            """
            SELECT request_id, graph_id, phase, event_type, node_id, chunk, ts_ms
            FROM stream_events WHERE request_id = ? ORDER BY id
            """,
            request_id,
        )

    def list_stream_events_by_time(
        self,
        *,
        since_ms: int,
        end_ms: int,
        event_types: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_types = tuple(str(item) for item in (event_types or ()) if str(item))
        parameters: tuple[Any, ...] = (int(since_ms), int(end_ms))
        sql = """
            SELECT request_id, graph_id, phase, event_type, node_id, chunk, ts_ms
            FROM stream_events WHERE ts_ms >= ? AND ts_ms <= ?
        """
        if normalized_types:
            placeholders = ",".join("?" for _ in normalized_types)
            sql += f" AND event_type IN ({placeholders})"
            parameters += normalized_types
        sql += " ORDER BY ts_ms"
        with self._connection() as conn:
            return [dict(row) for row in conn.execute(sql, parameters).fetchall()]

    def get_checkpoint(self, *, checkpoint_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT checkpoint_id, request_id, graph_id, interrupt_reason,
                       resume_deadline_at, created_at_ms
                FROM checkpoints WHERE checkpoint_id = ?
                """,
                (str(checkpoint_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_barrier_joins(self, *, request_id: str) -> list[dict[str, Any]]:
        rows = self._list_by_key(
            """
            SELECT id, request_id, graph_id, passed, failed_branches_json,
                   join_policy_ref, reason, ts_ms
            FROM barrier_join_log WHERE request_id = ? ORDER BY id
            """,
            request_id,
        )
        for row in rows:
            row["passed"] = bool(row["passed"])
            try:
                row["failed_branches"] = json.loads(
                    str(row.get("failed_branches_json") or "[]")
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                row["failed_branches"] = []
        return rows

    def list_subgraph_traces(self, *, run_id: str) -> list[dict[str, Any]]:
        return self._list_by_key(
            """
            SELECT id, run_id, parent_path, node_id, input_digest, output_digest,
                   latency_ms, ts_ms
            FROM subgraph_trace WHERE run_id = ? ORDER BY id
            """,
            run_id,
        )

    def list_llm_calls(self, *, run_id: str) -> list[dict[str, Any]]:
        return self._list_by_key(
            """
            SELECT id, run_id, node_id, model_id, prompt_tokens, completion_tokens,
                   latency_ms, ts_ms
            FROM llm_call_log WHERE run_id = ? ORDER BY id
            """,
            run_id,
        )

    def _list_by_key(
        self,
        sql: str,
        key: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(row) for row in conn.execute(sql, (str(key),)).fetchall()]

    def close(self) -> None:
        if self._borrowed_connection is not None:
            return
        with self._memory_lock:
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None


__all__ = [
    "GRAPH_EVIDENCE_INVALID",
    "GRAPH_EVIDENCE_MIGRATION_ASSET_KEY",
    "GRAPH_EVIDENCE_MIGRATION_SHA256",
    "GRAPH_EVIDENCE_PERSISTENCE_FAILED",
    "GRAPH_EVIDENCE_SCHEMA_NOT_READY",
    "GraphEvidenceRepository",
    "GraphEvidenceRepositoryError",
]
