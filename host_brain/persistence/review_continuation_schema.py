"""Schema readiness probes for durable Review continuation."""

from __future__ import annotations

import sqlite3

from host_brain.persistence.review_continuation_binding import (
    REVIEW_CONTINUATION_TABLE,
)


def continuation_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (REVIEW_CONTINUATION_TABLE,),
    ).fetchone()
    return row is not None


__all__ = ["continuation_table_exists"]
