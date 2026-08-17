"""对工具生成 Markdown KB 数据库的只读、操作级访问。

KB 是外部工具产物，并非 Host schema 权威；本仓储只用 SQLite 只读 URI 查询一次，
不迁移、不修复也不创建任何表。FTS 不可用时才用有限 LIKE 回退，保证离线知识库
增强不会取得写入权限或阻断主召回链。
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any


class MarkdownKbRepository:
    """查询单个外部 KB，但不将其视为 Host 持久化 schema 的组成部分。"""

    def query(
        self,
        db_path: str | Path,
        *,
        fts_query: str,
        literal_query: str,
        limit: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        """以只读连接执行 FTS 查询，必要时使用文本匹配回退并在操作结束关闭连接。"""
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
        """读取 KB 自描述身份；缺失/异常时仅退回目录名，不影响查询候选。"""
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
