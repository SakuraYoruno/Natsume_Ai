"""Read-only persistence boundary for Bond replay and growth statistics."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
import sqlite3
from typing import Any, Iterator


class BondHistoryRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        borrowed_connection: sqlite3.Connection | None = None,
        borrowed_lock: Any | None = None,
    ) -> None:
        self.db_path = str(db_path or "").strip()
        self._borrowed_connection = borrowed_connection
        self._borrowed_lock = borrowed_lock

    @classmethod
    def from_ledger(cls, ledger: Any) -> "BondHistoryRepository | None":
        path = str(
            getattr(ledger, "canonical_db_path", "")
            or getattr(ledger, "db_path", "")
            or getattr(ledger, "_db_path", "")
        ).strip()
        if not path:
            return None
        if path != ":memory:":
            return cls(path)
        connection = getattr(ledger, "_conn", None)
        if connection is None:
            return None
        return cls(
            path,
            borrowed_connection=connection,
            borrowed_lock=getattr(ledger, "_write_lock", None),
        )

    @contextmanager
    def _scope(self) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with (self._borrowed_lock or nullcontext()):
                yield self._borrowed_connection
            return
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def load_restore_snapshot(self, *, user_id: str) -> dict[str, Any]:
        user = str(user_id or "").strip()
        with self._scope() as conn:
            transition = None
            try:
                transition = conn.execute(
                    """
                    SELECT payload, ts_ms FROM bond_stage_transition_log
                    WHERE transition_status IN ('PROMOTED','DEMOTED','STABLE')
                      AND user_id = ? ORDER BY ts_ms DESC LIMIT 1
                    """,
                    (user,),
                ).fetchone()
            except sqlite3.DatabaseError:
                transition = None
            if transition is None:
                transition = conn.execute(
                    """
                    SELECT payload, ts_ms FROM bond_update_log
                    WHERE update_kind = 'STAGE_TRANSITION' AND status = 'SUCCESS'
                      AND user_id = ? ORDER BY ts_ms DESC LIMIT 1
                    """,
                    (user,),
                ).fetchone()
            transition_ts = int(transition[1] or 0) if transition else 0
            delta_rows = conn.execute(
                """
                SELECT update_kind, payload, ts_ms FROM bond_update_log
                WHERE update_kind NOT IN ('INIT','STAGE_TRANSITION')
                  AND status IN ('OK','SUCCESS') AND ts_ms > ? AND user_id = ?
                ORDER BY ts_ms ASC
                """,
                (transition_ts, user),
            ).fetchall()
            count_row = conn.execute(
                """
                SELECT COUNT(*), MAX(ts_ms) FROM bond_update_log
                WHERE update_kind NOT IN ('INIT','STAGE_TRANSITION')
                  AND status IN ('OK','SUCCESS') AND user_id = ?
                """,
                (user,),
            ).fetchone()
            fallback_rows = conn.execute(
                """
                SELECT update_kind, payload, ts_ms FROM bond_update_log
                WHERE update_kind NOT IN ('INIT','STAGE_TRANSITION','IDLE_DECAY')
                  AND status IN ('OK','SUCCESS') AND user_id = ?
                ORDER BY ts_ms ASC
                """,
                (user,),
            ).fetchall()
        return {
            "transition": tuple(transition) if transition is not None else None,
            "delta_rows": [tuple(row) for row in delta_rows],
            "event_count": int(count_row[0] or 0) if count_row else 0,
            "last_event_ts_ms": int(count_row[1] or 0) if count_row else 0,
            "fallback_rows": [tuple(row) for row in fallback_rows],
        }

    def interaction_counts(
        self,
        *,
        user_id: str,
        cutoff_ts_ms: int,
    ) -> dict[str, int]:
        with self._scope() as conn:
            rows = conn.execute(
                """
                SELECT update_kind, COUNT(*) FROM bond_update_log
                WHERE update_kind NOT IN ('INIT', 'STAGE_TRANSITION')
                  AND status IN ('OK', 'SUCCESS') AND user_id = ? AND ts_ms >= ?
                GROUP BY update_kind
                """,
                (str(user_id or "").strip(), int(cutoff_ts_ms)),
            ).fetchall()
        return {str(kind or "").strip().upper(): int(count) for kind, count in rows}


__all__ = ["BondHistoryRepository"]
