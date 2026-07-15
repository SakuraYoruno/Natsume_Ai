"""Rebuild Chroma so the vector DB only contains long-term memory.

The current surface main chain uses `episodic_vector` as the long-term memory
route. This tool removes style/canon/persona collections from Chroma and keeps
only the configured episodic collection.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any, Iterable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CHROMA_DIR = ROOT / "runtime" / "chroma" / "data"
DEFAULT_LEDGER = ROOT / "host_brain" / "data" / "audit.sqlite3"
DEFAULT_COLLECTION = "episodic"
DEFAULT_DIM = 1024
DEFAULT_BACKEND = "http"


@dataclass(frozen=True)
class MemorySeed:
    memory_id: str
    text: str
    metadata: dict[str, Any]


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_inside_root(path: Path) -> Path:
    resolved = path.resolve()
    root = ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path is outside workspace: {resolved}")
    return resolved


def _parse_json_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [str(parsed).strip()] if str(parsed).strip() else []


def _extract_written_memory_text(detail: Any) -> str:
    text = str(detail or "").strip()
    prefix = "memory written:"
    if text.lower().startswith(prefix):
        return text[len(prefix):].strip()
    return ""


def _looks_like_test_memory(text: str, row: sqlite3.Row) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    request_id = str(row["request_id"] or "").strip()
    intent_id = str(row["intent_id"] or "").strip()
    evidence_refs = str(row["evidence_refs"] or "").strip()
    if not request_id and not intent_id:
        return True
    route_text = f"{request_id} {intent_id} {evidence_refs}".lower()
    if any(marker in route_text for marker in ("acceptance", "unit-", "test", "tool_execution_matrix")):
        return True
    test_markers = (
        "tool execution acceptance",
        "closed loop sentinel",
        "recent episode boundary sentinel",
        "metrics test",
        "测试",
        "普通记忆",
        "重要的情感记忆",
        "高情感强度记忆",
        "低情感强度记忆",
        "快速遗忘记忆",
        "永久记忆",
        "快乐的记忆",
        "悲伤的记忆",
        "开心的事情",
        "悲伤的事情",
        "今天天气很好",
    )
    return any(marker in normalized for marker in test_markers)


def load_long_term_memory_seeds(
    ledger_path: Path,
    *,
    include_requestless_audit_rows: bool = False,
) -> tuple[list[MemorySeed], dict[str, int]]:
    if not ledger_path.exists() or ledger_path.stat().st_size <= 0:
        return [], {"semantic_memory": 0, "memory_ops_log": 0, "skipped_test_or_requestless": 0}

    conn = sqlite3.connect(str(ledger_path))
    conn.row_factory = sqlite3.Row
    seeds: list[MemorySeed] = []
    stats = {"semantic_memory": 0, "memory_ops_log": 0, "skipped_test_or_requestless": 0}

    try:
        semantic_rows = conn.execute(
            "SELECT semantic_id, summary, source_memory_ids, tags, confidence, ts_ms "
            "FROM semantic_memory WHERE deleted_at IS NULL ORDER BY ts_ms ASC"
        ).fetchall()
    except sqlite3.Error:
        semantic_rows = []
    for row in semantic_rows:
        text = str(row["summary"] or "").strip()
        semantic_id = str(row["semantic_id"] or "").strip()
        if not text or not semantic_id:
            continue
        seeds.append(
            MemorySeed(
                memory_id=f"semantic::{semantic_id}",
                text=text,
                metadata={
                    "store_id": "episodic_vector",
                    "memory_layer": "long_term",
                    "source_table": "semantic_memory",
                    "semantic_id": semantic_id,
                    "source_memory_ids": str(row["source_memory_ids"] or "[]"),
                    "tags": str(row["tags"] or "[]"),
                    "confidence": float(row["confidence"] or 0.0),
                    "ts_ms": int(row["ts_ms"] or 0),
                    "source_ref": f"ledger://semantic_memory/{semantic_id}",
                },
            )
        )
        stats["semantic_memory"] += 1

    try:
        memory_rows = conn.execute(
            "SELECT id, request_id, intent_id, memory_id, evidence_refs, status, "
            "op_kind, detail, decay_profile_id, visibility, ts_ms "
            "FROM memory_ops_log WHERE op_kind='WRITE' AND status='SUCCESS' "
            "ORDER BY ts_ms ASC, id ASC"
        ).fetchall()
    except sqlite3.Error:
        memory_rows = []
    for row in memory_rows:
        text = _extract_written_memory_text(row["detail"])
        if not include_requestless_audit_rows and _looks_like_test_memory(text, row):
            stats["skipped_test_or_requestless"] += 1
            continue
        memory_id = str(row["memory_id"] or f"memory_ops_{row['id']}").strip()
        if not text or not memory_id:
            continue
        refs = _parse_json_list(row["evidence_refs"])
        seeds.append(
            MemorySeed(
                memory_id=f"audit::{memory_id}",
                text=text,
                metadata={
                    "store_id": "episodic_vector",
                    "memory_layer": "long_term",
                    "source_table": "memory_ops_log",
                    "memory_id": memory_id,
                    "request_id": str(row["request_id"] or ""),
                    "intent_id": str(row["intent_id"] or ""),
                    "evidence_refs": json.dumps(refs, ensure_ascii=False),
                    "decay_profile_id": str(row["decay_profile_id"] or ""),
                    "visibility": str(row["visibility"] or ""),
                    "ts_ms": int(row["ts_ms"] or 0),
                    "source_ref": f"ledger://memory_ops_log/{memory_id}",
                },
            )
        )
        stats["memory_ops_log"] += 1

    return seeds, stats


def _backup_chroma_dir(chroma_dir: Path) -> Path | None:
    if not chroma_dir.exists():
        return None
    backup_root = ROOT / "artifacts" / "chroma_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"{chroma_dir.name}_{_now_stamp()}"
    shutil.copytree(chroma_dir, backup_path)
    return backup_path


def _reset_chroma_dir(chroma_dir: Path) -> None:
    chroma_dir = _ensure_inside_root(chroma_dir)
    if chroma_dir.exists():
        shutil.rmtree(chroma_dir)
    chroma_dir.mkdir(parents=True, exist_ok=True)


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
        else:
            sanitized[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return sanitized


def _embed(text: str, *, dim: int) -> tuple[list[float], str]:
    from host_brain.memory.chroma_client import embed_text

    values, model_id = embed_text(text, dim=dim)
    return list(values or []), str(model_id or "")


def _collection_names(client: Any) -> list[str]:
    names: list[str] = []
    for row in client.list_collections():
        name = getattr(row, "name", row)
        normalized = str(name or "").strip()
        if normalized:
            names.append(normalized)
    return sorted(set(names))


def _build_http_chroma_client(*, endpoint: str | None = None) -> tuple[Any, dict[str, Any]]:
    from host_brain.memory.chroma_client import load_chroma_runtime_config

    overrides = {"CHROMA_ENDPOINT": endpoint} if str(endpoint or "").strip() else None
    cfg_result = load_chroma_runtime_config(local_overrides=overrides)
    if not cfg_result.success or cfg_result.config is None or not cfg_result.config.enabled:
        raise RuntimeError(f"chroma runtime config invalid: {cfg_result.as_dict()}")

    cfg = cfg_result.config
    parsed = urlparse(str(cfg.endpoint or "http://127.0.0.1:8000").strip())
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)
    ssl = parsed.scheme.lower() == "https"

    import chromadb

    try:
        client = chromadb.HttpClient(
            host=host,
            port=port,
            ssl=ssl,
            tenant=cfg.tenant,
            database=cfg.database,
        )
    except Exception:
        client = chromadb.HttpClient(host=host, port=port, ssl=ssl)

    return client, {
        "endpoint": cfg.endpoint,
        "tenant": cfg.tenant,
        "database": cfg.database,
        "default_collection": cfg.default_collection,
        "collection_profiles": dict(cfg.collection_profiles),
    }


def _delete_all_collections(client: Any) -> list[str]:
    deleted: list[str] = []
    for name in _collection_names(client):
        client.delete_collection(name=name)
        deleted.append(name)
    return deleted


def rebuild_vector_db(
    *,
    backend: str = DEFAULT_BACKEND,
    chroma_dir: Path = DEFAULT_CHROMA_DIR,
    chroma_endpoint: str | None = None,
    ledger_path: Path,
    collection: str,
    dim: int,
    include_requestless_audit_rows: bool = False,
    dry_run: bool = False,
    reset_all_collections: bool = True,
) -> dict[str, Any]:
    normalized_backend = str(backend or DEFAULT_BACKEND).strip().lower()
    if normalized_backend not in {"http", "persistent"}:
        raise ValueError(f"unsupported chroma backend: {backend!r}")

    seeds, seed_stats = load_long_term_memory_seeds(
        ledger_path,
        include_requestless_audit_rows=include_requestless_audit_rows,
    )
    if dry_run:
        return {
            "dry_run": True,
            "backend": normalized_backend,
            "chroma_dir": str(chroma_dir),
            "chroma_endpoint": str(chroma_endpoint or ""),
            "collection": collection,
            "vector_dim": dim,
            "seed_count": len(seeds),
            "seed_stats": seed_stats,
        }

    import chromadb
    from chromadb.config import Settings

    backup_path = None
    target: dict[str, Any]
    if normalized_backend == "persistent":
        backup_path = _backup_chroma_dir(chroma_dir)
        _reset_chroma_dir(chroma_dir)
        client = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        target = {"path": str(chroma_dir)}
        deleted_collections: list[str] = []
    else:
        client, target = _build_http_chroma_client(endpoint=chroma_endpoint)
        deleted_collections = _delete_all_collections(client) if reset_all_collections else []

    coll = client.get_or_create_collection(
        name=collection,
        metadata={"hnsw:space": "cosine", "vector_dim": dim, "memory_layer": "long_term"},
    )
    removed_long_term_ids: list[str] = []
    if not reset_all_collections:
        try:
            existing = coll.get(where={"memory_layer": "long_term"}, include=[])
            removed_long_term_ids = [str(item) for item in list(existing.get("ids") or []) if str(item).strip()]
            if removed_long_term_ids:
                coll.delete(ids=removed_long_term_ids)
        except Exception:
            removed_long_term_ids = []
    inserted = 0
    for seed in seeds:
        embedding, model_id = _embed(seed.text, dim=dim)
        metadata = {
            **seed.metadata,
            "embedding_model_id": model_id,
            "vector_dim": dim,
        }
        coll.upsert(
            ids=[seed.memory_id],
            documents=[seed.text],
            embeddings=[embedding],
            metadatas=[_sanitize_metadata(metadata)],
        )
        inserted += 1

    collections = _collection_names(client)
    return {
        "dry_run": False,
        "backend": normalized_backend,
        "chroma_dir": str(chroma_dir),
        "target": target,
        "backup_path": str(backup_path) if backup_path else None,
        "deleted_collections": deleted_collections,
        "removed_long_term_count": len(removed_long_term_ids),
        "collections": collections,
        "collection": collection,
        "vector_dim": dim,
        "seed_count": len(seeds),
        "inserted_count": inserted,
        "seed_stats": seed_stats,
    }


def _write_summary(summary: dict[str, Any]) -> Path:
    out_dir = ROOT / "artifacts" / "chroma_rebuild"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "long_term_vector_db_latest.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("http", "persistent"),
        default=DEFAULT_BACKEND,
        help="Target Chroma backend. Default uses the runtime Docker/HTTP endpoint from .env.",
    )
    parser.add_argument("--chroma-dir", type=Path, default=DEFAULT_CHROMA_DIR)
    parser.add_argument(
        "--chroma-endpoint",
        default="",
        help="Optional CHROMA_ENDPOINT override for --backend=http.",
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--dim", type=int, default=DEFAULT_DIM)
    parser.add_argument(
        "--include-requestless-audit-rows",
        action="store_true",
        help="Include requestless memory_ops_log rows; default skips likely unit-test/demo memories.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--keep-existing-collections",
        action="store_true",
        help="Do not delete existing Chroma collections before rebuilding.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    summary = rebuild_vector_db(
        backend=str(args.backend or DEFAULT_BACKEND),
        chroma_dir=args.chroma_dir,
        chroma_endpoint=(str(args.chroma_endpoint).strip() or None),
        ledger_path=args.ledger,
        collection=str(args.collection or DEFAULT_COLLECTION),
        dim=max(1, int(args.dim or DEFAULT_DIM)),
        include_requestless_audit_rows=bool(args.include_requestless_audit_rows),
        dry_run=bool(args.dry_run),
        reset_all_collections=not bool(args.keep_existing_collections),
    )
    out_path = _write_summary(summary)
    print(json.dumps({**summary, "summary_path": str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
