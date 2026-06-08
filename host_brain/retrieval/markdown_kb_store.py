"""SQLite-backed Markdown knowledge base lookup.

This module reads the local KB databases produced by ``tools/markdown_kb.py``.
It is intentionally small and read-only so runtime recall can use it as an
offline fallback when Chroma is unavailable or empty.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence


DEFAULT_KB_ROOT = Path("data") / "knowledge_bases"
DEFAULT_KB_DB_NAME = "kb.sqlite3"
ENV_ENABLED = "SEMANTIC_RECALL_MARKDOWN_KB_ENABLED"
ENV_DB_PATHS = "SEMANTIC_RECALL_MARKDOWN_KB_DBS"
ENV_DB_PATH = "SEMANTIC_RECALL_MARKDOWN_KB_DB"
ENV_KB_IDS = "SEMANTIC_RECALL_MARKDOWN_KB_IDS"


@dataclass(frozen=True)
class MarkdownKbHit:
    doc_id: str
    content: str
    score: float
    metadata: dict[str, Any]
    source: str = "markdown_kb"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else (_project_root() / path).resolve()


def _parse_bool(value: Any, *, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def markdown_kb_enabled(env: Mapping[str, Any] | None = None) -> bool:
    values = env if env is not None else os.environ
    return _parse_bool(values.get(ENV_ENABLED), default=True)


def discover_markdown_kb_dbs(env: Mapping[str, Any] | None = None) -> list[Path]:
    values = env if env is not None else os.environ
    if not markdown_kb_enabled(values):
        return []

    explicit_paths: list[str] = []
    for key in (ENV_DB_PATH, ENV_DB_PATHS):
        raw = str(values.get(key) or "").strip()
        if raw:
            explicit_paths.extend(part.strip() for part in re.split(r"[;,]", raw) if part.strip())
    if explicit_paths:
        return [path for path in (_resolve_path(item) for item in explicit_paths) if path.exists()]

    root = _resolve_path(DEFAULT_KB_ROOT)
    raw_ids = str(values.get(ENV_KB_IDS) or "").strip()
    if raw_ids:
        ids = [part.strip() for part in re.split(r"[;,]", raw_ids) if part.strip()]
        return [path for path in (root / kb_id / DEFAULT_KB_DB_NAME for kb_id in ids) if path.exists()]

    if not root.exists():
        return []
    return sorted(path for path in root.glob(f"*/{DEFAULT_KB_DB_NAME}") if path.exists())


def query_markdown_kbs(
    query: str,
    *,
    top_k: int = 5,
    env: Mapping[str, Any] | None = None,
    db_paths: Sequence[str | Path] | None = None,
) -> list[MarkdownKbHit]:
    text = str(query or "").strip()
    if not text:
        return []
    paths = [_resolve_path(path) for path in db_paths] if db_paths is not None else discover_markdown_kb_dbs(env)
    if not paths:
        return []

    per_db_limit = max(1, int(top_k))
    hits: list[MarkdownKbHit] = []
    for db_path in paths:
        hits.extend(_query_one_db(db_path, text, top_k=per_db_limit))

    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[: max(1, int(top_k))]


def _query_one_db(db_path: Path, query: str, *, top_k: int) -> list[MarkdownKbHit]:
    fts_query = _fts_query(query)
    if not fts_query:
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        kb_id = _read_kb_id(conn, db_path)
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
                (fts_query, max(1, int(top_k))),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        if not rows:
            like = f"%{query}%"
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
                (like, like, like, max(1, int(top_k))),
            ).fetchall()

        scores = _normalize_scores(rows)
        return [_row_to_hit(row, score=score, kb_id=kb_id, db_path=db_path) for row, score in zip(rows, scores)]
    finally:
        conn.close()


def _read_kb_id(conn: sqlite3.Connection, db_path: Path) -> str:
    try:
        row = conn.execute("SELECT value FROM kb_meta WHERE key = 'kb_id'").fetchone()
        if row:
            return str(json.loads(row["value"]))
    except Exception:
        pass
    return db_path.parent.name


def _row_to_hit(sql_row: sqlite3.Row, *, score: float, kb_id: str, db_path: Path) -> MarkdownKbHit:
    asset_refs = _load_json_list(str(sql_row["asset_refs_json"] or "[]"))
    metadata = {
        "kb_id": kb_id,
        "db_path": str(db_path),
        "chunk_id": str(sql_row["chunk_id"]),
        "doc_id": str(sql_row["doc_id"]),
        "chunk_index": int(sql_row["chunk_index"]),
        "title": str(sql_row["title"]),
        "heading_path": str(sql_row["heading_path"]),
        "line_start": int(sql_row["line_start"]),
        "line_end": int(sql_row["line_end"]),
        "source_relpath": str(sql_row["source_relpath"]),
        "asset_refs": asset_refs,
        "token_estimate": int(sql_row["token_estimate"]),
    }
    content = _format_content(
        title=str(sql_row["title"]),
        heading_path=str(sql_row["heading_path"]),
        text=str(sql_row["text"]),
        line_start=int(sql_row["line_start"]),
        line_end=int(sql_row["line_end"]),
    )
    return MarkdownKbHit(
        doc_id=str(sql_row["chunk_id"]),
        content=content,
        score=float(score),
        metadata=metadata,
    )


def _format_content(*, title: str, heading_path: str, text: str, line_start: int, line_end: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) > 1200:
        cleaned = cleaned[:1200].rstrip() + "..."
    return f"{heading_path} [{line_start}-{line_end}]\n{cleaned}"


def _load_json_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _normalize_scores(rows: Sequence[sqlite3.Row]) -> list[float]:
    if not rows:
        return []
    raw_scores = [float(row["bm25_score"]) for row in rows]
    min_score = min(raw_scores)
    max_score = max(raw_scores)
    score_range = abs(max_score - min_score)
    if score_range < 1e-9:
        return [1.0 for _ in rows]
    return [max(0.0, min(1.0, round((score - max_score) / (-score_range), 6))) for score in raw_scores]


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x20000 <= cp <= 0x2A6DF
        or 0x2A700 <= cp <= 0x2CEAF
    )


def _fts_query(text: str) -> str:
    tokens: list[str] = []
    current: list[str] = []
    for ch in str(text or ""):
        if _is_cjk(ch):
            if current:
                tokens.append("".join(current).lower())
                current = []
            tokens.append(ch)
            continue
        if ch.isalnum() or ch in {"_", "-"}:
            current.append(ch)
            continue
        if current:
            tokens.append("".join(current).lower())
            current = []
    if current:
        tokens.append("".join(current).lower())
    return " ".join(token for token in tokens if token)
