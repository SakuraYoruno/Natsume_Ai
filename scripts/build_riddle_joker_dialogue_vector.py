from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_TSV = Path(r"D:\HF_Datasets\Limour\riddle_joker_dialogue\RIDDLE_JOKER_zh_dialogue.tsv")
DEFAULT_COLLECTION = "riddle_joker_dialogue_zh"
DEFAULT_DIM = 1024
SKIP_SPEAKERS = {"旁白", "地文"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _metadata_for_line(*, line_no: str, speaker: str, text: str, tsv_path: Path) -> dict[str, Any]:
    tags = ["daily"]
    moves = ["quick_reaction"]
    if any(marker in text for marker in ("不是", "才不是", "哈？", "什么嘛", "笨蛋", "随便", "稀罕")):
        tags.append("annoyed")
        moves.extend(["quick_rebuttal", "familiar_pushback"])
    if any(marker in text for marker in ("早上好", "早啊", "迟到", "早饭", "睡", "等一下", "重来", "再给")):
        moves.append("question_chain")
    quote_id = f"riddle_joker_tsv::{line_no}"
    return {
        "quote_id": quote_id,
        "source_ref": f"{tsv_path.name}:{line_no}:{speaker}",
        "source_digest": quote_id,
        "prompt_text": text,
        "speaker": speaker,
        "line_no": line_no,
        "source_score": 1.0,
        "confidence": 0.86,
        "character_confidence": 1.0 if speaker == "七海" else 0.62,
        "risk_tier": "safe_full_line",
        "intent_tags": "|".join(dict.fromkeys(tags)),
        "style_moves": "|".join(dict.fromkeys(moves)),
        "relationship_stages": "familiar|trusted_friend|close_friend|close_but_not_romantic",
        "source_dataset": "Limour/riddle_joker_dialogue",
    }


def _iter_rows(tsv_path: Path, *, speakers: set[str], limit: int) -> list[tuple[str, str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, str, dict[str, Any]]] = []
    with tsv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for raw in reader:
            line_no = str(raw.get("line_no") or "").strip()
            speaker = str(raw.get("speaker") or "").strip()
            text = _clean_text(str(raw.get("text") or ""))
            if not line_no or not text or speaker in SKIP_SPEAKERS:
                continue
            if speakers and speaker not in speakers:
                continue
            metadata = _metadata_for_line(line_no=line_no, speaker=speaker, text=text, tsv_path=tsv_path)
            rows.append((f"riddle_joker_tsv::{line_no}", f"{speaker}：{text}", text, metadata))
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def _raw_chroma_client(cfg: Any) -> Any:
    import chromadb

    parsed = urlparse(str(cfg.endpoint))
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)
    ssl = parsed.scheme.lower() == "https"
    try:
        return chromadb.HttpClient(host=host, port=port, ssl=ssl, tenant=cfg.tenant, database=cfg.database)
    except Exception:
        return chromadb.HttpClient(host=host, port=port, ssl=ssl)


def _ensure_collection(cfg: Any, *, collection: str, reset: bool) -> None:
    raw_client = _raw_chroma_client(cfg)
    if reset:
        try:
            raw_client.delete_collection(name=str(collection))
        except Exception:
            pass
    raw_client.get_or_create_collection(name=str(collection), metadata={"hnsw:space": "cosine"})


def _existing_ids(cfg: Any, *, collection: str) -> set[str]:
    raw_client = _raw_chroma_client(cfg)
    coll = raw_client.get_or_create_collection(name=str(collection), metadata={"hnsw:space": "cosine"})
    try:
        result = coll.get(include=[])
    except Exception:
        result = coll.get()
    return {str(item) for item in (result.get("ids") or []) if str(item).strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Riddle Joker Chinese dialogue vector collection.")
    parser.add_argument("--tsv", default=str(DEFAULT_TSV), help="Path to RIDDLE_JOKER_zh_dialogue.tsv")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Chroma collection name")
    parser.add_argument("--dim", type=int, default=DEFAULT_DIM, help="Embedding dimension/profile")
    parser.add_argument("--batch-size", type=int, default=128, help="Progress batch size")
    parser.add_argument("--speaker", action="append", default=[], help="Only index this speaker; repeatable")
    parser.add_argument("--limit", type=int, default=0, help="Limit indexed rows for smoke runs")
    parser.add_argument("--no-resume", action="store_true", help="Do not skip existing ids")
    parser.add_argument("--reset-collection", action="store_true", help="Delete and recreate the target collection with cosine metric before indexing")
    parser.add_argument("--quiet", action="store_true", help="Only print final summary")
    args = parser.parse_args()

    sys.path.insert(0, str(_repo_root()))
    from host_brain.memory.chroma_client import PersistentChromaClient, embed_text, load_chroma_runtime_config

    tsv_path = Path(args.tsv)
    if not tsv_path.exists():
        raise FileNotFoundError(str(tsv_path))
    cfg_result = load_chroma_runtime_config()
    if not cfg_result.success or cfg_result.config is None or not cfg_result.config.enabled:
        raise RuntimeError(f"chroma config invalid: {cfg_result.reason_code}")
    cfg = cfg_result.config
    _ensure_collection(cfg, collection=str(args.collection), reset=bool(args.reset_collection))
    profiles = dict(cfg.collection_profiles)
    profiles[str(args.collection)] = int(args.dim)
    client = PersistentChromaClient(
        endpoint=cfg.endpoint,
        tenant=cfg.tenant,
        database=cfg.database,
        collection_profiles=profiles,
        default_collection=cfg.default_collection,
    )

    rows = _iter_rows(tsv_path, speakers={str(s) for s in args.speaker if str(s).strip()}, limit=max(0, int(args.limit)))
    existing: set[str] = set()
    if not bool(args.no_resume) and not bool(args.reset_collection):
        existing = _existing_ids(cfg, collection=str(args.collection))
        if existing:
            rows = [row for row in rows if row[0] not in existing]
    total = len(rows)
    if total <= 0:
        print({"indexed": 0, "collection": str(args.collection), "reason": "no_missing_rows", "existing": len(existing)})
        return 0

    model_ids: dict[str, int] = {}
    for idx, (memory_id, document, text, metadata) in enumerate(rows, start=1):
        embedding, model_id = embed_text(document, dim=int(args.dim))
        model_ids[str(model_id)] = model_ids.get(str(model_id), 0) + 1
        metadata = dict(metadata)
        metadata["embedding_model_id"] = str(model_id)
        client.upsert(
            memory_id=memory_id,
            text=text,
            embedding=list(embedding or []),
            metadata=metadata,
            collection=str(args.collection),
        )
        if not bool(args.quiet) and (idx == 1 or idx % max(1, int(args.batch_size)) == 0 or idx == total):
            print(f"indexed {idx}/{total} into {args.collection}", flush=True)

    print({"indexed": total, "collection": str(args.collection), "dim": int(args.dim), "models": model_ids, "skipped_existing": len(existing)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
