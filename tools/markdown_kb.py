"""Build and query a local Markdown knowledge base.

The default storage is a SQLite database with an FTS5 index. Chroma sync is
available as an explicit follow-up step so the KB remains useful even when the
local vector service is offline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_OUTPUT_DIR = Path("runtime") / "knowledge_bases"
DEFAULT_KB_ID = "agent_guide"
DEFAULT_CHROMA_COLLECTION = "agent_guide_kb"
DEFAULT_CHROMA_ENDPOINT = "http://127.0.0.1:18000"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)


@dataclass(frozen=True)
class Section:
    heading_path: tuple[str, ...]
    heading_level: int
    line_start: int
    line_end: int
    lines: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    chunk_index: int
    title: str
    heading_path: str
    heading_level: int
    line_start: int
    line_end: int
    text: str
    source_relpath: str
    asset_refs: tuple[str, ...]
    token_estimate: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_rooted(path: str | Path) -> Path:
    raw = Path(path)
    return raw if raw.is_absolute() else (_project_root() / raw).resolve()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x20000 <= cp <= 0x2A6DF
        or 0x2A700 <= cp <= 0x2CEAF
    )


def _split_for_fts(text: str) -> str:
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
    return " ".join(t for t in tokens if t)


def _fts_query(text: str) -> str:
    return _split_for_fts(text)


def _clean_heading(raw: str) -> str:
    title = re.sub(r"\s+#*$", "", str(raw or "").strip())
    title = re.sub(r"\[[^\]]+\]\([^)]+\)", lambda m: m.group(0).split("](", 1)[0].lstrip("["), title)
    return title.strip()


def _is_toc_section(section: Section) -> bool:
    if not section.heading_path:
        return False
    normalized = re.sub(r"\s+", "", section.heading_path[-1]).lower()
    return normalized in {"目录", "tableofcontents", "toc"}


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_resolved = target_dir.resolve()
    first_parts: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.filename.endswith("/"):
                continue
            first = Path(info.filename).parts[0] if Path(info.filename).parts else ""
            if first:
                first_parts.append(first)
            destination = (target_dir / info.filename).resolve()
            if target_resolved not in destination.parents and destination != target_resolved:
                raise RuntimeError(f"unsafe zip member path: {info.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    common_root = Counter(first_parts).most_common(1)
    return target_dir / common_root[0][0] if common_root else target_dir


def _load_package_manifest(package_root: Path) -> dict[str, Any]:
    manifest_path = package_root / "manifest.json"
    if not manifest_path.exists():
        matches = list(package_root.rglob("manifest.json"))
        if not matches:
            return {}
        manifest_path = matches[0]
    try:
        data = json.loads(_read_text(manifest_path))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid package manifest: {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"package manifest must be a JSON object: {manifest_path}")
    data["_manifest_path"] = str(manifest_path)
    return data


def _find_main_markdown(package_root: Path, manifest: dict[str, Any], explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(package_root / explicit)
        candidates.append(package_root / Path(explicit).name)
    main = str(manifest.get("main_markdown") or "").strip()
    if main:
        candidates.append(package_root / main)
        candidates.append(package_root / Path(main).name)
    for rel in manifest.get("markdown_files_for_kb_import") or []:
        if rel:
            candidates.append(package_root / str(rel))
    candidates.extend(sorted(package_root.rglob("*.md")))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    raise RuntimeError(f"no markdown file found under {package_root}")


def _parse_sections(markdown: str) -> list[Section]:
    numbered_lines = list(enumerate(markdown.splitlines(), 1))
    sections: list[Section] = []
    heading_stack: list[tuple[int, str]] = []
    current_lines: list[tuple[int, str]] = []
    current_path: tuple[str, ...] = ()
    current_level = 0
    current_start = 1
    in_fence = False
    fence_marker = ""

    def flush(end_line: int) -> None:
        nonlocal current_lines
        if not current_lines:
            return
        sections.append(
            Section(
                heading_path=current_path,
                heading_level=current_level,
                line_start=current_start,
                line_end=end_line,
                lines=tuple(current_lines),
            )
        )
        current_lines = []

    for line_no, line in numbered_lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""

        match = None if in_fence else HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            title = _clean_heading(match.group(2))
            heading_stack = [(lvl, txt) for lvl, txt in heading_stack if lvl < level]
            heading_stack.append((level, title))
            if level <= 3:
                flush(line_no - 1)
                current_path = tuple(txt for _, txt in heading_stack)
                current_level = level
                current_start = line_no
                current_lines = [(line_no, line)]
                continue
        if not current_lines:
            current_path = tuple(txt for _, txt in heading_stack)
            current_level = heading_stack[-1][0] if heading_stack else 0
            current_start = line_no
        current_lines.append((line_no, line))

    flush(numbered_lines[-1][0] if numbered_lines else 0)
    return sections


def _take_tail_overlap(lines: list[tuple[int, str]], overlap_chars: int) -> list[tuple[int, str]]:
    if overlap_chars <= 0 or not lines:
        return []
    selected: list[tuple[int, str]] = []
    total = 0
    for item in reversed(lines):
        selected.append(item)
        total += len(item[1]) + 1
        if total >= overlap_chars:
            break
    return list(reversed(selected))


def _split_section_lines(section: Section, *, max_chars: int, overlap_chars: int) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    current: list[tuple[int, str]] = []
    current_len = 0

    def flush() -> None:
        if not current:
            return
        text = "\n".join(line for _, line in current).strip()
        if text:
            blocks.append((current[0][0], current[-1][0], text))

    for line_no, line in section.lines:
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            flush()
            current = _take_tail_overlap(current, overlap_chars)
            current_len = sum(len(item[1]) + 1 for item in current)
        current.append((line_no, line))
        current_len += line_len
    flush()
    return blocks


def _extract_asset_refs(text: str) -> tuple[str, ...]:
    refs: list[str] = []
    for pattern in (IMAGE_RE, HTML_IMAGE_RE):
        for match in pattern.finditer(text):
            raw = match.group(1).strip().strip("<>")
            if not raw or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw):
                continue
            refs.append(raw.replace("\\", "/"))
    return tuple(dict.fromkeys(refs))


def _build_chunks(
    *,
    markdown: str,
    doc_id: str,
    source_relpath: str,
    max_chars: int,
    overlap_chars: int,
    include_toc: bool,
) -> list[Chunk]:
    sections = _parse_sections(markdown)
    chunks: list[Chunk] = []
    index = 0
    for section in sections:
        if not include_toc and _is_toc_section(section):
            continue
        for line_start, line_end, text in _split_section_lines(
            section,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        ):
            if len(text.strip()) < 40:
                continue
            index += 1
            heading_path = " > ".join(section.heading_path) if section.heading_path else "Document"
            title = section.heading_path[-1] if section.heading_path else "Document"
            digest = hashlib.sha1(f"{doc_id}:{index}:{heading_path}:{text}".encode("utf-8")).hexdigest()[:12]
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}:{index:05d}:{digest}",
                    doc_id=doc_id,
                    chunk_index=index,
                    title=title,
                    heading_path=heading_path,
                    heading_level=section.heading_level,
                    line_start=line_start,
                    line_end=line_end,
                    text=text,
                    source_relpath=source_relpath,
                    asset_refs=_extract_asset_refs(text),
                    token_estimate=max(1, len(text) // 3),
                )
            )
    return chunks


def _create_database(
    *,
    db_path: Path,
    kb_id: str,
    source_zip: Path,
    package_root: Path,
    markdown_path: Path,
    manifest: dict[str, Any],
    chunks: list[Chunk],
    include_toc: bool,
    max_chars: int,
    overlap_chars: int,
) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    doc_id = kb_id
    md_sha = _sha256_file(markdown_path)
    expected_sha = str(manifest.get("main_markdown_sha256") or "").strip()
    sha_valid = (not expected_sha) or expected_sha.lower() == md_sha.lower()
    created_at = _utc_now()

    asset_counter: Counter[str] = Counter()
    for chunk in chunks:
        asset_counter.update(chunk.asset_refs)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(
            """
            CREATE TABLE kb_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_zip TEXT NOT NULL,
                package_root TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                package_manifest_json TEXT NOT NULL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                title TEXT NOT NULL,
                heading_path TEXT NOT NULL,
                heading_level INTEGER NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                text TEXT NOT NULL,
                source_relpath TEXT NOT NULL,
                asset_refs_json TEXT NOT NULL,
                token_estimate INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_id UNINDEXED,
                title,
                heading_path,
                text,
                tokenize = "unicode61"
            );
            CREATE TABLE assets (
                asset_path TEXT PRIMARY KEY,
                exists_on_disk INTEGER NOT NULL,
                size_bytes INTEGER,
                sha256 TEXT,
                referenced_count INTEGER NOT NULL
            );
            CREATE INDEX idx_chunks_doc_index ON chunks(doc_id, chunk_index);
            CREATE INDEX idx_chunks_heading ON chunks(heading_path);
            """
        )

        meta = {
            "kb_id": kb_id,
            "created_at": created_at,
            "source_zip": str(source_zip),
            "package_root": str(package_root),
            "main_markdown": str(markdown_path),
            "main_markdown_sha256": md_sha,
            "main_markdown_sha256_valid": sha_valid,
            "chunk_count": len(chunks),
            "include_toc": include_toc,
            "max_chars": max_chars,
            "overlap_chars": overlap_chars,
        }
        conn.executemany("INSERT INTO kb_meta(key, value) VALUES (?, ?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])

        markdown_text = _read_text(markdown_path)
        conn.execute(
            """
            INSERT INTO documents(
                doc_id, title, source_path, source_zip, package_root, sha256,
                char_count, created_at, package_manifest_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                Path(markdown_path).stem,
                str(markdown_path),
                str(source_zip),
                str(package_root),
                md_sha,
                len(markdown_text),
                created_at,
                json.dumps({k: v for k, v in manifest.items() if not k.startswith("_")}, ensure_ascii=False, sort_keys=True),
            ),
        )

        chunk_rows = [
            (
                chunk.chunk_id,
                chunk.doc_id,
                chunk.chunk_index,
                chunk.title,
                chunk.heading_path,
                chunk.heading_level,
                chunk.line_start,
                chunk.line_end,
                chunk.text,
                chunk.source_relpath,
                json.dumps(list(chunk.asset_refs), ensure_ascii=False),
                chunk.token_estimate,
            )
            for chunk in chunks
        ]
        conn.executemany(
            """
            INSERT INTO chunks(
                chunk_id, doc_id, chunk_index, title, heading_path, heading_level,
                line_start, line_end, text, source_relpath, asset_refs_json,
                token_estimate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            chunk_rows,
        )

        fts_rows = [
            (
                chunk.chunk_id,
                _split_for_fts(chunk.title),
                _split_for_fts(chunk.heading_path),
                _split_for_fts(f"{chunk.heading_path}\n{chunk.text}"),
            )
            for chunk in chunks
        ]
        conn.executemany(
            "INSERT INTO chunks_fts(chunk_id, title, heading_path, text) VALUES (?, ?, ?, ?)",
            fts_rows,
        )

        asset_rows = []
        for asset_path, count in sorted(asset_counter.items()):
            resolved = (markdown_path.parent / asset_path).resolve()
            exists = resolved.exists() and resolved.is_file()
            size = resolved.stat().st_size if exists else None
            digest = _sha256_file(resolved) if exists else None
            asset_rows.append((asset_path, 1 if exists else 0, size, digest, int(count)))
        conn.executemany(
            "INSERT INTO assets(asset_path, exists_on_disk, size_bytes, sha256, referenced_count) VALUES (?, ?, ?, ?, ?)",
            asset_rows,
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "kb_id": kb_id,
        "db_path": str(db_path),
        "source_root": str(package_root),
        "main_markdown": str(markdown_path),
        "main_markdown_sha256": md_sha,
        "main_markdown_sha256_valid": sha_valid,
        "chunk_count": len(chunks),
        "asset_ref_count": sum(asset_counter.values()),
        "unique_asset_count": len(asset_counter),
        "missing_asset_count": sum(1 for path in asset_counter if not (markdown_path.parent / path).exists()),
        "created_at": created_at,
    }


def _db_for_args(args: argparse.Namespace) -> Path:
    if getattr(args, "db", None):
        return _resolve_rooted(args.db)
    return _resolve_rooted(args.output_dir) / args.kb_id / "kb.sqlite3"


def _fetch_stats(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        meta = {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key, value FROM kb_meta")}
        doc = conn.execute("SELECT * FROM documents LIMIT 1").fetchone()
        counts = {
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "assets": conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
            "missing_assets": conn.execute("SELECT COUNT(*) FROM assets WHERE exists_on_disk = 0").fetchone()[0],
            "tokens_estimated": conn.execute("SELECT COALESCE(SUM(token_estimate), 0) FROM chunks").fetchone()[0],
        }
        top_headings = [
            {"heading_path": row["heading_path"], "chunks": row["n"]}
            for row in conn.execute(
                """
                SELECT heading_path, COUNT(*) AS n
                FROM chunks
                GROUP BY heading_path
                ORDER BY n DESC, heading_path
                LIMIT 12
                """
            )
        ]
        return {
            "db_path": str(db_path),
            "meta": meta,
            "document": dict(doc) if doc else None,
            "counts": counts,
            "top_headings": top_headings,
        }
    finally:
        conn.close()


def _preview_text(text: str, query: str, *, max_chars: int = 520) -> str:
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned

    lower = cleaned.lower()
    candidates = [query.strip().lower()]
    candidates.extend(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_-]{3,}", query.lower()))
    position = -1
    for candidate in candidates:
        if not candidate:
            continue
        position = lower.find(candidate)
        if position >= 0:
            break
    if position < 0:
        position = 0
    start = max(0, position - max_chars // 4)
    end = min(len(cleaned), start + max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(cleaned) else ""
    return f"{prefix}{cleaned[start:end]}{suffix}"


def _normalize_scores(rows: list[sqlite3.Row]) -> list[float]:
    if not rows:
        return []
    raw_scores = [float(row["bm25_score"]) for row in rows]
    min_score = min(raw_scores)
    max_score = max(raw_scores)
    score_range = abs(max_score - min_score)
    if score_range < 1e-9:
        return [1.0 for _ in rows]
    return [max(0.0, min(1.0, round((score - max_score) / (-score_range), 6))) for score in raw_scores]


def query_kb(db_path: Path, query: str, *, top_k: int) -> list[dict[str, Any]]:
    fts = _fts_query(query)
    if not fts:
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                """
                SELECT
                    chunks_fts.chunk_id AS chunk_id,
                    bm25(chunks_fts) AS bm25_score,
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
                (fts, max(1, int(top_k))),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        if not rows:
            like = f"%{query.strip()}%"
            rows = conn.execute(
                """
                SELECT
                    chunk_id,
                    0.0 AS bm25_score,
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
                WHERE text LIKE ? OR heading_path LIKE ? OR title LIKE ?
                ORDER BY chunk_index
                LIMIT ?
                """,
                (like, like, like, max(1, int(top_k))),
            ).fetchall()

        scores = _normalize_scores(rows)
        results: list[dict[str, Any]] = []
        for row, score in zip(rows, scores):
            assets = json.loads(row["asset_refs_json"] or "[]")
            results.append(
                {
                    "chunk_id": row["chunk_id"],
                    "chunk_index": int(row["chunk_index"]),
                    "score": float(score),
                    "title": row["title"],
                    "heading_path": row["heading_path"],
                    "line_start": int(row["line_start"]),
                    "line_end": int(row["line_end"]),
                    "source_relpath": row["source_relpath"],
                    "asset_refs": assets,
                    "token_estimate": int(row["token_estimate"]),
                    "preview": _preview_text(str(row["text"]), query),
                }
            )
        return results
    finally:
        conn.close()


def cmd_build(args: argparse.Namespace) -> int:
    zip_path = _resolve_rooted(args.zip)
    if not zip_path.exists():
        raise RuntimeError(f"zip not found: {zip_path}")

    kb_root = _resolve_rooted(args.output_dir) / args.kb_id
    package_root = _safe_extract_zip(zip_path, kb_root / "source")
    manifest = _load_package_manifest(package_root)
    markdown_path = _find_main_markdown(package_root, manifest, args.markdown)

    source_relpath = str(markdown_path.relative_to(package_root)).replace("\\", "/")
    markdown = _read_text(markdown_path)
    chunks = _build_chunks(
        markdown=markdown,
        doc_id=args.kb_id,
        source_relpath=source_relpath,
        max_chars=int(args.max_chars),
        overlap_chars=int(args.overlap_chars),
        include_toc=bool(args.include_toc),
    )
    if not chunks:
        raise RuntimeError("no chunks were produced from the markdown source")

    summary = _create_database(
        db_path=kb_root / "kb.sqlite3",
        kb_id=args.kb_id,
        source_zip=zip_path,
        package_root=package_root,
        markdown_path=markdown_path,
        manifest=manifest,
        chunks=chunks,
        include_toc=bool(args.include_toc),
        max_chars=int(args.max_chars),
        overlap_chars=int(args.overlap_chars),
    )

    _write_json(kb_root / "manifest.generated.json", summary)
    readme = (
        f"# {args.kb_id} knowledge base\n\n"
        f"- Source zip: `{zip_path}`\n"
        f"- Main markdown: `{markdown_path}`\n"
        f"- SQLite DB: `{kb_root / 'kb.sqlite3'}`\n"
        f"- Chunks: {summary['chunk_count']}\n"
        f"- Unique assets: {summary['unique_asset_count']}\n"
        f"- Missing assets: {summary['missing_asset_count']}\n\n"
        "Query example:\n\n"
        f"```powershell\npython tools/markdown_kb.py query \"RAG 检索\" --kb-id {args.kb_id}\n```\n"
    )
    (kb_root / "README.md").write_text(readme, encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    db_path = _db_for_args(args)
    if not db_path.exists():
        raise RuntimeError(f"KB database not found: {db_path}")
    results = query_kb(db_path, args.query, top_k=int(args.top_k))
    if args.json:
        print(json.dumps({"query": args.query, "results": results}, ensure_ascii=False, indent=2))
        return 0
    if not results:
        print(f"No results for: {args.query}")
        return 0
    for idx, item in enumerate(results, 1):
        print(f"[{idx}] score={item['score']:.3f} chunk={item['chunk_id']}")
        print(f"    heading: {item['heading_path']}")
        print(f"    source: {item['source_relpath']}:{item['line_start']}-{item['line_end']}")
        if item["asset_refs"]:
            print(f"    assets: {', '.join(item['asset_refs'][:3])}")
        print(f"    {item['preview']}")
        print()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db_path = _db_for_args(args)
    if not db_path.exists():
        raise RuntimeError(f"KB database not found: {db_path}")
    print(json.dumps(_fetch_stats(db_path), ensure_ascii=False, indent=2))
    return 0


def _load_chroma_rows(db_path: Path, limit: int | None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM chunks ORDER BY chunk_index"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def cmd_sync_chroma(args: argparse.Namespace) -> int:
    db_path = _db_for_args(args)
    if not db_path.exists():
        raise RuntimeError(f"KB database not found: {db_path}")

    try:
        import chromadb  # noqa: PLC0415
        from chromadb.config import Settings  # noqa: PLC0415
        from host_brain.memory.chroma_client import embed_text  # noqa: PLC0415
    except Exception as exc:
        raise RuntimeError(f"Chroma sync dependencies unavailable: {exc}") from exc

    parsed = urlparse(args.endpoint)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)
    ssl = parsed.scheme.lower() == "https"
    client = chromadb.HttpClient(
        host=host,
        port=port,
        ssl=ssl,
        tenant=args.tenant,
        database=args.database,
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=args.collection,
        metadata={"hnsw:space": "cosine", "vector_dim": int(args.dim), "kb_id": args.kb_id},
    )
    rows = _load_chroma_rows(db_path, args.limit)
    total = len(rows)
    if total == 0:
        print("No chunks to sync.")
        return 0

    start = time.time()
    batch_size = max(1, int(args.batch_size))
    for offset in range(0, total, batch_size):
        batch = rows[offset : offset + batch_size]
        ids: list[str] = []
        docs: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict[str, Any]] = []
        for row in batch:
            text = f"{row['heading_path']}\n{row['text']}"
            embedding, model_id = embed_text(text[: int(args.embed_chars)], dim=int(args.dim))
            ids.append(str(row["chunk_id"]))
            docs.append(str(row["text"]))
            embeddings.append(embedding)
            metadatas.append(
                {
                    "kb_id": args.kb_id,
                    "doc_id": row["doc_id"],
                    "chunk_index": int(row["chunk_index"]),
                    "title": row["title"],
                    "heading_path": row["heading_path"],
                    "source_relpath": row["source_relpath"],
                    "line_start": int(row["line_start"]),
                    "line_end": int(row["line_end"]),
                    "embedding_model": model_id,
                }
            )
        collection.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metadatas)
        print(f"synced {min(offset + batch_size, total)}/{total}")

    print(
        json.dumps(
            {
                "collection": args.collection,
                "count": collection.count(),
                "synced_chunks": total,
                "elapsed_s": round(time.time() - start, 2),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="extract a Markdown package and build a SQLite FTS KB")
    build.add_argument("--zip", required=True, help="source zip package")
    build.add_argument("--kb-id", default=DEFAULT_KB_ID)
    build.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    build.add_argument("--markdown", help="explicit markdown path inside the package")
    build.add_argument("--max-chars", type=int, default=4200)
    build.add_argument("--overlap-chars", type=int, default=350)
    build.add_argument("--include-toc", action="store_true")
    build.set_defaults(func=cmd_build)

    query = sub.add_parser("query", help="query a built KB")
    query.add_argument("query")
    query.add_argument("--kb-id", default=DEFAULT_KB_ID)
    query.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    query.add_argument("--db", help="explicit kb.sqlite3 path")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--json", action="store_true")
    query.set_defaults(func=cmd_query)

    stats = sub.add_parser("stats", help="show KB stats")
    stats.add_argument("--kb-id", default=DEFAULT_KB_ID)
    stats.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    stats.add_argument("--db", help="explicit kb.sqlite3 path")
    stats.set_defaults(func=cmd_stats)

    sync = sub.add_parser("sync-chroma", help="sync built chunks into a Chroma collection")
    sync.add_argument("--kb-id", default=DEFAULT_KB_ID)
    sync.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    sync.add_argument("--db", help="explicit kb.sqlite3 path")
    sync.add_argument("--endpoint", default=os.environ.get("CHROMA_ENDPOINT") or DEFAULT_CHROMA_ENDPOINT)
    sync.add_argument("--tenant", default=os.environ.get("CHROMA_TENANT") or "default_tenant")
    sync.add_argument("--database", default=os.environ.get("CHROMA_DATABASE") or "default_database")
    sync.add_argument("--collection", default=DEFAULT_CHROMA_COLLECTION)
    sync.add_argument("--dim", type=int, default=1024)
    sync.add_argument("--batch-size", type=int, default=32)
    sync.add_argument("--embed-chars", type=int, default=3200)
    sync.add_argument("--limit", type=int)
    sync.set_defaults(func=cmd_sync_chroma)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
