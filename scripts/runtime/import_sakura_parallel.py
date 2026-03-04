"""Import CN/JP aligned .ast scripts into a Chroma collection.

Design goals:
- Pair files by relative path under CN/JP roots.
- Align records by `block_xxxxx` sections.
- Extract spoken text from `ja = {...}` segments first (fallback to block-level strings).
- Upsert deterministic IDs so re-running is idempotent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import chromadb

# Allow running as a standalone script from repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_brain.memory.chroma_client import embed_text


BLOCK_START_RE = re.compile(r"(?m)^\s*block_(\d+)\s*=\s*\{")
JA_ASSIGN_RE = re.compile(r"ja\s*=\s*\{")
STRING_LITERAL_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
CONTROL_TOKEN_RE = re.compile(r"^(?:rt\d+|[A-Za-z_][A-Za-z0-9_]*)$")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
PUNCT_RE = re.compile(r"[。！？…,.!?]")


@dataclass(frozen=True)
class ImportRecord:
    doc_id: str
    document: str
    metadata: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import sakura CN/JP .ast corpus into Chroma.")
    parser.add_argument("--cn-dir", required=True, help="Directory for translated CN .ast files.")
    parser.add_argument("--jp-dir", required=True, help="Directory for raw JP .ast files.")
    parser.add_argument(
        "--file-glob",
        default="*.ast",
        help="Glob used under each root (default: *.ast).",
    )
    parser.add_argument(
        "--backend",
        choices=("persistent", "http"),
        default="persistent",
        help="Chroma backend mode.",
    )
    parser.add_argument(
        "--chroma-path",
        default="runtime/chroma/data",
        help="Used when --backend=persistent.",
    )
    parser.add_argument(
        "--chroma-endpoint",
        default=os.environ.get("CHROMA_ENDPOINT", "http://127.0.0.1:8000"),
        help="Used when --backend=http.",
    )
    parser.add_argument(
        "--collection",
        default=os.environ.get("CHROMA_DEFAULT_COLLECTION", "episodic"),
        help="Target Chroma collection name.",
    )
    parser.add_argument(
        "--collection-dim",
        type=int,
        default=None,
        help="Vector dimension. If omitted, resolve from CHROMA_COLLECTION_PROFILES_JSON, fallback=16.",
    )
    parser.add_argument(
        "--collection-profiles-json",
        default=os.environ.get("CHROMA_COLLECTION_PROFILES_JSON"),
        help="Override collection profile JSON when resolving --collection-dim.",
    )
    parser.add_argument("--batch-size", type=int, default=128, help="Upsert batch size.")
    parser.add_argument("--max-files", type=int, default=0, help="Limit number of paired files (0=no limit).")
    parser.add_argument("--max-records", type=int, default=0, help="Limit total imported records (0=no limit).")
    parser.add_argument(
        "--source-id",
        default="sakura_parallel_cn_jp_v1",
        help="Source tag written to metadata.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse/build records only, do not upsert.")
    parser.add_argument(
        "--manifest-path",
        default="",
        help="Optional JSONL manifest output path.",
    )
    return parser.parse_args()


def resolve_collection_dim(args: argparse.Namespace) -> int:
    if args.collection_dim is not None:
        return max(1, int(args.collection_dim))

    profiles_text = str(args.collection_profiles_json or "").strip()
    if profiles_text:
        try:
            payload = json.loads(profiles_text)
            if isinstance(payload, dict):
                raw = payload.get(str(args.collection))
                if raw is not None:
                    return max(1, int(raw))
        except Exception:
            pass
    return 16


def collect_files(root: Path, file_glob: str) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for file_path in root.rglob(file_glob):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(root).as_posix().lower()
        mapping[rel] = file_path
    return mapping


def read_text(path: Path) -> str:
    encodings = ("utf-8", "utf-8-sig", "cp932", "gbk")
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def find_matching_brace(text: str, start_brace: int) -> int:
    if start_brace < 0 or start_brace >= len(text) or text[start_brace] != "{":
        return -1
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start_brace, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def extract_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    cursor = 0
    while True:
        match = BLOCK_START_RE.search(text, cursor)
        if match is None:
            break
        block_id = f"block_{match.group(1)}"
        brace_start = text.find("{", match.start())
        brace_end = find_matching_brace(text, brace_start)
        if brace_start < 0 or brace_end < 0:
            break
        blocks[block_id] = text[brace_start : brace_end + 1]
        cursor = brace_end + 1
    return blocks


def extract_ja_sections(block_text: str) -> list[str]:
    sections: list[str] = []
    cursor = 0
    while True:
        match = JA_ASSIGN_RE.search(block_text, cursor)
        if match is None:
            break
        brace_start = block_text.find("{", match.end() - 1)
        brace_end = find_matching_brace(block_text, brace_start)
        if brace_start < 0 or brace_end < 0:
            break
        sections.append(block_text[brace_start : brace_end + 1])
        cursor = brace_end + 1
    return sections


def decode_string_literal(raw_value: str) -> str:
    try:
        return json.loads(f'"{raw_value}"')
    except Exception:
        return (
            raw_value.replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
        )


def keep_dialogue_line(text: str) -> bool:
    value = str(text).strip()
    if not value:
        return False
    if CONTROL_TOKEN_RE.fullmatch(value):
        return False
    has_cjk = CJK_RE.search(value) is not None
    has_punct = PUNCT_RE.search(value) is not None
    if not has_cjk and not has_punct and len(value) < 8:
        return False
    return True


def extract_dialogue_lines(block_text: str) -> list[str]:
    targets = extract_ja_sections(block_text)
    if not targets:
        targets = [block_text]

    lines: list[str] = []
    for target in targets:
        for raw in STRING_LITERAL_RE.findall(target):
            decoded = decode_string_literal(raw).strip()
            if not keep_dialogue_line(decoded):
                continue
            lines.append(decoded)

    deduped: list[str] = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return deduped


def block_sort_key(block_id: str) -> tuple[int, str]:
    try:
        return int(block_id.split("_", 1)[1]), block_id
    except Exception:
        return 0, block_id


def build_records_for_file(
    *,
    rel_path: str,
    cn_text: str,
    jp_text: str,
    source_id: str,
) -> tuple[list[ImportRecord], int, int]:
    cn_blocks = extract_blocks(cn_text)
    jp_blocks = extract_blocks(jp_text)
    shared_block_ids = sorted(set(cn_blocks.keys()) & set(jp_blocks.keys()), key=block_sort_key)

    records: list[ImportRecord] = []
    skipped_empty = 0
    for block_id in shared_block_ids:
        cn_lines = extract_dialogue_lines(cn_blocks[block_id])
        jp_lines = extract_dialogue_lines(jp_blocks[block_id])
        if not cn_lines and not jp_lines:
            skipped_empty += 1
            continue

        source_ref = f"corpus://sakura/{rel_path}#{block_id}"
        evidence_ref = f"evd://sakura/{rel_path}#{block_id}"
        raw_id = f"{rel_path}#{block_id}"
        doc_id = f"sakura_{hashlib.sha1(raw_id.encode('utf-8')).hexdigest()[:24]}"

        jp_body = "\n".join(jp_lines).strip()
        cn_body = "\n".join(cn_lines).strip()
        document = (
            f"source_file: {rel_path}\n"
            f"block_id: {block_id}\n\n"
            f"[JP]\n{jp_body}\n\n"
            f"[CN]\n{cn_body}"
        ).strip()

        metadata = {
            "source_id": source_id,
            "source_ref": source_ref,
            "evidence_ref": evidence_ref,
            "file_rel_path": rel_path,
            "block_id": block_id,
            "has_jp": 1 if jp_lines else 0,
            "has_cn": 1 if cn_lines else 0,
            "jp_line_count": int(len(jp_lines)),
            "cn_line_count": int(len(cn_lines)),
            "lang_pair": "jp-cn",
            "series": "sakura_no_koku",
        }
        records.append(ImportRecord(doc_id=doc_id, document=document, metadata=metadata))

    return records, len(shared_block_ids), skipped_empty


def build_chroma_client(args: argparse.Namespace) -> Any:
    if args.backend == "persistent":
        return chromadb.PersistentClient(path=str(Path(args.chroma_path).resolve()))

    parsed = urlparse(str(args.chroma_endpoint).strip() or "http://127.0.0.1:8000")
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)
    ssl = parsed.scheme.lower() == "https"
    return chromadb.HttpClient(host=host, port=port, ssl=ssl)


def flush_batch(
    *,
    collection: Any,
    batch: list[ImportRecord],
    dim: int,
) -> int:
    if not batch:
        return 0
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    embeddings: list[list[float]] = []
    for row in batch:
        embedding, _ = embed_text(row.document, dim=dim)
        ids.append(row.doc_id)
        documents.append(row.document)
        metadatas.append(row.metadata)
        embeddings.append(embedding)
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
    return len(batch)


def main() -> int:
    args = parse_args()
    cn_root = Path(args.cn_dir).resolve()
    jp_root = Path(args.jp_dir).resolve()
    if not cn_root.exists() or not cn_root.is_dir():
        print(f"[error] CN directory not found: {cn_root}", file=sys.stderr)
        return 2
    if not jp_root.exists() or not jp_root.is_dir():
        print(f"[error] JP directory not found: {jp_root}", file=sys.stderr)
        return 2

    dim = resolve_collection_dim(args)
    cn_files = collect_files(cn_root, args.file_glob)
    jp_files = collect_files(jp_root, args.file_glob)

    shared = sorted(set(cn_files.keys()) & set(jp_files.keys()))
    missing_jp = sorted(set(cn_files.keys()) - set(jp_files.keys()))
    missing_cn = sorted(set(jp_files.keys()) - set(cn_files.keys()))
    if args.max_files > 0:
        shared = shared[: int(args.max_files)]

    if not shared:
        print("[error] no paired files found.", file=sys.stderr)
        return 3

    print(
        json.dumps(
            {
                "cn_files": len(cn_files),
                "jp_files": len(jp_files),
                "paired_files": len(shared),
                "missing_in_jp": len(missing_jp),
                "missing_in_cn": len(missing_cn),
                "collection": str(args.collection),
                "collection_dim": dim,
                "backend": args.backend,
                "dry_run": bool(args.dry_run),
            },
            ensure_ascii=False,
        )
    )

    manifest_fp = None
    if args.manifest_path:
        manifest_path = Path(args.manifest_path).resolve()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_fp = manifest_path.open("w", encoding="utf-8")

    collection = None
    before_count = None
    if not args.dry_run:
        client = build_chroma_client(args)
        collection = client.get_or_create_collection(name=str(args.collection))
        before_count = int(collection.count())

    total_files = 0
    total_blocks = 0
    total_records = 0
    total_skipped_empty = 0
    flushed_records = 0
    buffer: list[ImportRecord] = []
    stop_early = False

    try:
        for rel in shared:
            total_files += 1
            cn_text = read_text(cn_files[rel])
            jp_text = read_text(jp_files[rel])
            records, block_count, skipped_empty = build_records_for_file(
                rel_path=rel,
                cn_text=cn_text,
                jp_text=jp_text,
                source_id=str(args.source_id),
            )
            total_blocks += int(block_count)
            total_skipped_empty += int(skipped_empty)

            for rec in records:
                total_records += 1
                if manifest_fp is not None:
                    manifest_fp.write(
                        json.dumps(
                            {
                                "doc_id": rec.doc_id,
                                "source_ref": rec.metadata.get("source_ref"),
                                "evidence_ref": rec.metadata.get("evidence_ref"),
                                "file_rel_path": rec.metadata.get("file_rel_path"),
                                "block_id": rec.metadata.get("block_id"),
                                "jp_line_count": rec.metadata.get("jp_line_count"),
                                "cn_line_count": rec.metadata.get("cn_line_count"),
                                "document_sha256": hashlib.sha256(rec.document.encode("utf-8")).hexdigest(),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

                if args.max_records > 0 and total_records > int(args.max_records):
                    stop_early = True
                    break
                if args.dry_run:
                    continue
                buffer.append(rec)
                if len(buffer) >= int(args.batch_size):
                    flushed_records += flush_batch(collection=collection, batch=buffer, dim=dim)  # type: ignore[arg-type]
                    buffer.clear()

            if stop_early:
                break

            if total_files % 20 == 0:
                print(
                    json.dumps(
                        {
                            "progress_files": total_files,
                            "progress_records_built": total_records,
                            "progress_records_flushed": flushed_records,
                        },
                        ensure_ascii=False,
                    )
                )

        if not args.dry_run and buffer:
            flushed_records += flush_batch(collection=collection, batch=buffer, dim=dim)  # type: ignore[arg-type]
            buffer.clear()

        summary: dict[str, Any] = {
            "processed_files": total_files,
            "paired_blocks": total_blocks,
            "records_built": total_records,
            "records_skipped_empty": total_skipped_empty,
            "records_flushed": flushed_records if not args.dry_run else 0,
            "max_records_hit": bool(stop_early),
        }
        if not args.dry_run and collection is not None and before_count is not None:
            after_count = int(collection.count())
            summary["collection_count_before"] = before_count
            summary["collection_count_after"] = after_count
            summary["collection_count_delta"] = after_count - before_count

        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        if manifest_fp is not None:
            manifest_fp.close()


if __name__ == "__main__":
    raise SystemExit(main())
