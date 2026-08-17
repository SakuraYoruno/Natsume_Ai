"""SQLite 支撑的 Markdown 知识库只读查询。

本模块读取 ``tools/markdown_kb.py`` 产出的本地 KB 数据库。它刻意保持小且只读，
使运行时能在 Chroma 不可用或没有命中时使用离线回退；返回的仍是候选投影，后续
召回权限和提示注入范围不由这里决定。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from host_brain.persistence.markdown_kb_repository import MarkdownKbRepository
from host_brain.retrieval.markdown_kb_settings import MarkdownKbSettings


DEFAULT_KB_ROOT = Path("runtime") / "knowledge_bases"
DEFAULT_KB_DB_NAME = "kb.sqlite3"
ENV_ENABLED = "SEMANTIC_RECALL_MARKDOWN_KB_ENABLED"
ENV_DB_PATHS = "SEMANTIC_RECALL_MARKDOWN_KB_DBS"
ENV_DB_PATH = "SEMANTIC_RECALL_MARKDOWN_KB_DB"
ENV_KB_IDS = "SEMANTIC_RECALL_MARKDOWN_KB_IDS"


@dataclass(frozen=True)
class MarkdownKbHit:
    """Markdown 查询命中及其可定位元数据；正文外的原始数据库对象不向上泄漏。"""
    doc_id: str
    content: str
    score: float
    metadata: dict[str, Any]
    source: str = "markdown_kb"


def _project_root() -> Path:
    """解析仓库根目录，供相对 KB 路径在不同工作目录下稳定定位。"""
    return Path(__file__).resolve().parents[2]


def _resolve_path(raw_path: str | Path) -> Path:
    """把配置的相对路径固定到项目根，避免调用者当前目录改变检索数据源。"""
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
    """读取 Markdown KB 开关；未声明时保持启用以兼容离线召回默认值。"""
    values = env if env is not None else MarkdownKbSettings().as_environment()
    return _parse_bool(values.get(ENV_ENABLED), default=True)


def discover_markdown_kb_dbs(env: Mapping[str, Any] | None = None) -> list[Path]:
    """按显式路径、指定 KB ID、默认根目录的顺序发现存在的数据库。

    缺失路径仅跳过，不在读取链自动创建数据库或目录，避免查询行为产生持久化副作用。
    """
    values = env if env is not None else MarkdownKbSettings().as_environment()
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
    """跨已发现 KB 查询并按分数汇总；空查询或无数据源安全返回空候选。"""
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
    """查询单个 KB 并把 BM25 分数归一化，保证不同库的候选可被统一排序。"""
    fts_query = _fts_query(query)
    if not fts_query:
        return []
    kb_id, rows = MarkdownKbRepository().query(
        db_path,
        fts_query=fts_query,
        literal_query=query,
        limit=top_k,
    )
    scores = _normalize_scores(rows)
    return [
        _row_to_hit(row, score=score, kb_id=kb_id, db_path=db_path)
        for row, score in zip(rows, scores)
    ]


def _row_to_hit(sql_row: Mapping[str, Any], *, score: float, kb_id: str, db_path: Path) -> MarkdownKbHit:
    """将仓储行缩减为可溯源命中，保留行号和资源引用以支持后续证据呈现。"""
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
    """生成带章节与行号的有限长度正文，控制单个文档块的上下文占用。"""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) > 1200:
        cleaned = cleaned[:1200].rstrip() + "..."
    return f"{heading_path} [{line_start}-{line_end}]\n{cleaned}"


def _load_json_list(raw: str) -> list[str]:
    """安全解析资产引用列表；错误或非列表元数据不阻断正文召回。"""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _normalize_scores(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    """将同库 BM25 负分映射到 [0, 1]；并列或单条命中均视为最高相关。"""
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
    """构造保守 FTS 词元：CJK 按字切分，其他字符仅保留字母数字安全片段。"""
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
