"""Semantic snapshot reads for the runtime World Model projection."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator, Mapping


WORLD_MODEL_QUERY_INVALID = "WORLD_MODEL_QUERY_INVALID"
WORLD_MODEL_QUERY_UNAVAILABLE = "WORLD_MODEL_QUERY_UNAVAILABLE"


class WorldModelQueryRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or WORLD_MODEL_QUERY_UNAVAILABLE)
        self.detail = str(detail or "world model snapshot read failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class WorkEnvironmentEventRecord:
    event_id: str
    event_type: str
    work_id: str
    summary: str
    urgency: str
    emotional_effect: Mapping[str, Any]
    created_at_ms: int
    active: bool


@dataclass(frozen=True, slots=True)
class WorldModelPersistenceSnapshot:
    life_state: Mapping[str, Any] | None
    work_events: tuple[WorkEnvironmentEventRecord, ...]
    life_state_source_available: bool
    work_event_source_available: bool


def _decode_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


class WorldModelQueryRepository:
    """Read both durable World Model sources in one operation-scoped snapshot."""

    def __init__(
        self,
        canonical_db_path: str | Path,
        *,
        timeout_ms: int = 3_000,
        _borrowed_connection: sqlite3.Connection | None = None,
        _borrowed_lock: Any | None = None,
    ) -> None:
        raw_path = str(canonical_db_path or "").strip()
        if not raw_path or raw_path.lower().startswith("file:"):
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_INVALID,
                "canonical database path is required",
            )
        if raw_path == ":memory:" and _borrowed_connection is None:
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_INVALID,
                "in-memory reads require an adapter-owned borrowed connection",
            )
        self.canonical_db_path: str | Path = (
            raw_path if raw_path == ":memory:" else Path(raw_path).expanduser().resolve()
        )
        self._timeout_ms = max(1, int(timeout_ms))
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock or threading.RLock()
        if isinstance(self.canonical_db_path, Path) and not self.canonical_db_path.is_file():
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_UNAVAILABLE,
                f"canonical database does not exist: {self.canonical_db_path}",
            )

    @classmethod
    def from_ledger(cls, ledger: Any) -> WorldModelQueryRepository:
        canonical_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not canonical_path:
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_INVALID,
                "ledger does not expose canonical_db_path",
            )
        if canonical_path != ":memory:":
            return cls(canonical_path)
        connection = getattr(ledger, "_conn", None)
        if connection is None:
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_INVALID,
                "in-memory ledger does not expose a compatibility connection",
            )
        lock = getattr(ledger, "_write_lock", None) or threading.RLock()
        return cls(
            canonical_path,
            _borrowed_connection=connection,
            _borrowed_lock=lock,
        )

    def _connect_read_only(self) -> sqlite3.Connection:
        assert isinstance(self.canonical_db_path, Path)
        uri = self.canonical_db_path.as_uri() + "?mode=ro"
        try:
            conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=self._timeout_ms / 1000.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
            return conn
        except sqlite3.DatabaseError as exc:
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_UNAVAILABLE,
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
                finally:
                    if started and self._borrowed_connection.in_transaction:
                        self._borrowed_connection.rollback()
            return
        conn = self._connect_read_only()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def read_snapshot(
        self,
        *,
        person_id: str,
        work_event_limit: int = 3,
    ) -> WorldModelPersistenceSnapshot:
        normalized_person_id = str(person_id or "").strip()
        try:
            normalized_limit = max(1, min(int(work_event_limit), 10))
        except (TypeError, ValueError) as exc:
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_INVALID,
                "work event limit must be an integer",
            ) from exc
        if not normalized_person_id:
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_INVALID,
                "person_id is required",
            )

        life_state: dict[str, Any] | None = None
        work_events: tuple[WorkEnvironmentEventRecord, ...] = ()
        life_available = True
        work_available = True
        try:
            with self._snapshot() as conn:
                try:
                    state_row = conn.execute(
                        """
                        SELECT version, state_json, updated_at_ms
                        FROM digital_life_state
                        WHERE person_id = ?
                        """,
                        (normalized_person_id,),
                    ).fetchone()
                except sqlite3.DatabaseError:
                    state_row = None
                    life_available = False
                if state_row is not None:
                    decoded = _decode_object(state_row["state_json"])
                    if decoded:
                        decoded["person_id"] = normalized_person_id
                        decoded["version"] = int(
                            state_row["version"] or decoded.get("version") or 1
                        )
                        decoded.setdefault(
                            "_row_updated_at_ms",
                            int(state_row["updated_at_ms"] or 0),
                        )
                        life_state = decoded

                try:
                    work_rows = conn.execute(
                        """
                        SELECT event_id, event_type, work_id, summary, urgency,
                               emotional_effect_json, created_at_ms, active
                        FROM work_environment_event
                        WHERE person_id = ?
                        ORDER BY created_at_ms DESC, event_id DESC
                        LIMIT ?
                        """,
                        (normalized_person_id, normalized_limit),
                    ).fetchall()
                except sqlite3.DatabaseError:
                    work_rows = []
                    work_available = False
                work_events = tuple(
                    WorkEnvironmentEventRecord(
                        event_id=str(row["event_id"] or ""),
                        event_type=str(row["event_type"] or ""),
                        work_id=str(row["work_id"] or ""),
                        summary=str(row["summary"] or ""),
                        urgency=str(row["urgency"] or "medium"),
                        emotional_effect=_decode_object(row["emotional_effect_json"]),
                        created_at_ms=int(row["created_at_ms"] or 0),
                        active=bool(row["active"]),
                    )
                    for row in work_rows
                )
        except WorldModelQueryRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise WorldModelQueryRepositoryError(
                WORLD_MODEL_QUERY_UNAVAILABLE,
                "world model snapshot query failed",
            ) from exc

        return WorldModelPersistenceSnapshot(
            life_state=life_state,
            work_events=work_events,
            life_state_source_available=life_available,
            work_event_source_available=work_available,
        )


__all__ = [
    "WORLD_MODEL_QUERY_INVALID",
    "WORLD_MODEL_QUERY_UNAVAILABLE",
    "WorkEnvironmentEventRecord",
    "WorldModelPersistenceSnapshot",
    "WorldModelQueryRepository",
    "WorldModelQueryRepositoryError",
]
