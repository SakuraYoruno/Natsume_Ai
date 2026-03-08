"""Full-text searcher for recall pipeline (SQLite FTS5)."""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Mapping

logger = logging.getLogger("Natsume.retrieval.full_text_searcher")

class FullTextSearcher:
    """基于 SQLite FTS5 的全文检索器。

    使用 episodic_fts 虚拟表（由 0021_episodic_fts5.sql 迁移创建）进行全文检索。
    支持中文字符边界分割（unicode61 tokenizer）。

    TASK-0210 M23-A：修复中文词组检索失效问题。
    unicode61 tokenizer 按单字分词，多字中文词组（如"猫咪"）无法作为整体 token 命中。
    修复方案：对中文字符进行字符级分割，写入和查询时均使用空格分隔的单字序列，
    使 FTS5 MATCH 语法能正确匹配中文内容。
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        """初始化 FTS5 检索器。

        Args:
            db_path: SQLite 数据库路径。None 时使用内存数据库（仅用于测试）。

        Raises:
            RuntimeError: 如果 FTS5 虚拟表不可用。
        """
        self._db_path = db_path or ":memory:"
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_fts_table()

    def _ensure_fts_table(self) -> None:
        """确保 episodic_fts 虚拟表存在（若不存在则创建）。"""
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
                    memory_id UNINDEXED,
                    content,
                    tokenize = "unicode61"
                )
                """
            )
            self._conn.commit()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"FTS5 虚拟表创建失败（SQLite 可能不支持 FTS5）: {exc}") from exc

    @staticmethod
    def _split_chinese_query(text: str) -> str:
        """将文本中的中文字符进行字符级分割，非中文词保留原样。

        TASK-0210 M23-A：unicode61 tokenizer 对中文按单字分词，
        因此查询词"猫咪"需要拆分为"猫 咪"才能命中索引中的单字 token。
        非中文词（英文、数字等）保留原词，不做拆分。

        Args:
            text: 原始查询文本。

        Returns:
            处理后的 FTS5 查询字符串，中文字符以空格分隔。

        Examples:
            >>> FullTextSearcher._split_chinese_query("猫咪")
            '猫 咪'
            >>> FullTextSearcher._split_chinese_query("工作压力大")
            '工 作 压 力 大'
            >>> FullTextSearcher._split_chinese_query("hello world")
            'hello world'
            >>> FullTextSearcher._split_chinese_query("用户喜欢cats")
            '用 户 喜 欢 cats'
        """
        if not text:
            return text
        tokens: list[str] = []
        current_non_chinese: list[str] = []
        for ch in text:
            # 判断是否为中文字符（CJK 统一汉字基本区及扩展区）
            cp = ord(ch)
            is_chinese = (
                0x4E00 <= cp <= 0x9FFF   # CJK 统一汉字
                or 0x3400 <= cp <= 0x4DBF  # CJK 扩展 A
                or 0x20000 <= cp <= 0x2A6DF  # CJK 扩展 B
                or 0x2A700 <= cp <= 0x2CEAF  # CJK 扩展 C/D/E
                or 0xF900 <= cp <= 0xFAFF   # CJK 兼容汉字
            )
            if is_chinese:
                # 先把积累的非中文词 flush
                if current_non_chinese:
                    tokens.append("".join(current_non_chinese).strip())
                    current_non_chinese = []
                # 中文字符单独作为一个 token
                tokens.append(ch)
            else:
                current_non_chinese.append(ch)
        # flush 剩余非中文
        if current_non_chinese:
            tokens.append("".join(current_non_chinese).strip())
        # 过滤空串，用空格连接
        return " ".join(t for t in tokens if t)

    @staticmethod
    def _prepare_content_for_index(content: str) -> str:
        """将文本内容预处理为适合 FTS5 unicode61 索引的格式。

        中文字符进行字符级分割，确保写入索引时与查询时的分词策略一致。

        Args:
            content: 原始文本内容。

        Returns:
            处理后的文本，中文字符以空格分隔。
        """
        return FullTextSearcher._split_chinese_query(content)

    def index_memory(self, *, memory_id: str, content: str) -> None:
        """将一条 Episodic 记忆写入 FTS5 索引。

        TASK-0210 M23-A：写入时对中文内容进行字符级分割，
        确保索引与查询时的分词策略一致。

        Args:
            memory_id: 记忆唯一 ID。
            content: 记忆文本内容。
        """
        # 对中文内容进行字符级分割，与查询时保持一致
        indexed_content = self._prepare_content_for_index(str(content or ""))
        # 先删除旧记录（幂等写入）
        self._conn.execute("DELETE FROM episodic_fts WHERE memory_id = ?", (memory_id,))
        self._conn.execute(
            "INSERT INTO episodic_fts(memory_id, content) VALUES (?, ?)",
            (memory_id, indexed_content),
        )
        self._conn.commit()

    def search(self, query: str, *, top_k: int = 8) -> list[dict[str, Any]]:
        """全文检索，返回匹配的记忆列表。

        TASK-0210 M23-A：对中文查询词进行字符级分割，
        使 FTS5 MATCH 语法能正确匹配 unicode61 单字索引。

        Args:
            query: 检索查询文本。
            top_k: 最多返回条数。

        Returns:
            候选列表，每条含 doc_id / snippet / fts_score（归一化到 [0, 1]）。
        """
        if not str(query or "").strip():
            return []
        # 对中文查询词进行字符级分割，与索引写入时保持一致
        fts_query = self._split_chinese_query(str(query).strip())
        if not fts_query:
            return []
        try:
            # FTS5 使用 bm25() 函数计算相关性分数（负值，越小越相关）
            rows = self._conn.execute(
                """
                SELECT memory_id, content, bm25(episodic_fts) AS bm25_score
                FROM episodic_fts
                WHERE episodic_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                (fts_query, int(top_k)),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 查询语法错误或表不存在时降级返回空
            return []

        if not rows:
            return []

        # 将 bm25 负分归一化到 [0, 1]（最相关 → 1.0）
        raw_scores = [float(row["bm25_score"]) for row in rows]
        min_score = min(raw_scores)
        max_score = max(raw_scores)
        score_range = abs(max_score - min_score)

        results: list[dict[str, Any]] = []
        for row, raw_score in zip(rows, raw_scores):
            if score_range < 1e-9:
                # 所有结果分数相同（含单条命中），均视为最相关 → 1.0
                normalized = 1.0
            else:
                # bm25 分数为负，越小（绝对值越大）越相关
                # 最小分（最相关）→ 1.0，最大分（最不相关）→ 0.0
                normalized = round((raw_score - max_score) / (-score_range), 6)
                normalized = max(0.0, min(1.0, normalized))
            results.append({
                "doc_id": str(row["memory_id"]),
                "snippet": str(row["content"])[:512],
                "fts_score": normalized,
            })
        return results

    def close(self) -> None:
        """关闭数据库连接。"""
        try:
            self._conn.close()
        except Exception:
            pass


# ── HybridFusionStrategy（TASK-0202）────────────────────────────────────────

