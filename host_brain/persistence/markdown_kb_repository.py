"""Read-only operation-scoped access to tool-produced Markdown KB databases."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any


class MarkdownKbRepository:
    """Query one external KB without making it a Host schema authority."""

    def query(
        self,
        db_path: str | Path,
        *,
        fts_query: str,
        literal_query: str,
        limit: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        path = Path(db_path).resolve()
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            kb_id = self._read_kb_id(conn, path)
            try:
                rows = conn.execute(
                    """
                    SELECT
                        chunks_fts.chunk_id AS chunk_id,
                        bm25(chunks_fts) AS bm25_score,
                        c.doc_id,
                        c.chunk_index,
                        c.title,
                        c.heading_path,
                        c.line_start,
                        c.line_end,
                        c.text,
                        c.source_relpath,
                        c.asset_refs_json,
                        c.token_estimate
                    FROM chunks_fts
                    JOIN chunks AS c ON c.chunk_id = chunks_fts.chunk_id
                    WHERE chunks_fts MATCH ?
                    ORDER BY bm25_score
                    LIMIT ?
                    """,
                    (str(fts_query), max(1, int(limit))),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                like = f"%{literal_query}%"
                rows = conn.execute(
                    """
                    SELECT
                        chunk_id,
                        0.0 AS bm25_score,
                        doc_id,
                        chunk_index,
                        title,
                        heading_path,
                        line_start,
                        line_end,
                        text,
                        source_relpath,
                        asset_refs_json,
                        token_estimate
                    FROM chunks
                    WHERE text LIKE ? OR title LIKE ? OR heading_path LIKE ?
                    ORDER BY chunk_index
                    LIMIT ?
                    """,
                    (like, like, like, max(1, int(limit))),
                ).fetchall()
            return kb_id, [dict(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _read_kb_id(conn: sqlite3.Connection, db_path: Path) -> str:
        try:
            row = conn.execute(
                "SELECT value FROM kb_meta WHERE key = 'kb_id'"
            ).fetchone()
            if row:
                return str(json.loads(row["value"]))
        except Exception:
            pass
        return db_path.parent.name


__all__ = ["MarkdownKbRepository"]
