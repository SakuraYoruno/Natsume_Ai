"""Rebuild the isolated original-quote Chroma collection.

This collection is a style resource for the surface generator. It is deliberately
separate from user memory collections such as episodic/semantic memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.memory.chroma_client import (  # noqa: E402
    EMBEDDING_MODEL_ID,
    embed_text,
    hash_embed_text,
    load_chroma_runtime_config,
    _get_sentence_transformer_model,
    _resolve_semantic_embedding_model_id,
)
from host_brain.persona.surface_original_quote_bank import (  # noqa: E402
    DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION,
    DEFAULT_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM,
    to_prompt_style_text,
)
from tools.build_surface_original_quote_bank import (  # noqa: E402
    ASCII_WORD_RE,
    KANA_RE,
    PROMPT_POLLUTION_MARKERS,
    SCENE_POLLUTION_MARKERS,
    UNSAFE_MARKERS,
    _classify_quote_tags,
    _clean_quote,
    _ordered_unique,
    _quote_character_confidence,
    _relationship_stages_for,
)


DEFAULT_SOURCE = ROOT / "artifacts" / "style_source" / "original_cn_all.jsonl"
DEFAULT_OUT_DIR = ROOT / "artifacts" / "surface_original_quote_vector"
EXTRA_PLOT_MARKERS = (
    "新海君",
    "翔先生",
    "莲夜",
    "Artifact",
    "喷枪",
    "模型套装",
    "精神病院",
    "这座城市",
    "金拱门",
    "意大利面",
    "沐浴露",
    "使用者",
    "战斗",
    "伙伴",
    "一个房间",
    "睡一个房间",
    "被大家遗忘",
    "别人忘了我",
    "石化",
    "连累哥哥",
    "世界消失",
    "要死了",
    "春风",
    "学姐",
    "放学",
    "小天",
    "都学姐",
    "最痛苦的是天",
    "断绝关系",
    "被讨厌",
    "拒绝了",
    "那个人",
    "搭话",
    "哥哥他呀",
    "都会帮你解决",
    "勇气可嘉",
    "感动得都要哭",
    "帮助哥哥",
    "回报哥哥",
    "哥哥的依靠",
    "迁就我的任性",
    "材料费",
    "帮你洗",
    "一起洗",
    "身上这么多东西",
    "结巴个头",
    "好消息",
    "去去就回",
    "在这等我",
    "完～全都不回来",
    "连累了哥哥",
    "肆无忌惮",
    "引人注目",
    "看她",
    "爸妈",
    "哥哥大人",
    "人家还不困",
    "不还回来也没关系",
    "每周回来一次",
    "周末回家",
    "回家住一晚",
    "一心一意",
    "别人怎么想",
    "不管发生什么",
    "现在这样的话",
    "喜欢上哥哥",
    "以前很喜欢",
    "模仿一下",
    "万人迷美少女",
    "忘记了我",
    "忘记掉",
    "没有忘记我",
    "告白",
    "消失",
    "脆弱",
    "隐藏了起来",
    "松了一口气",
    "非常害怕",
    "任何人发现",
    "这很恶心",
    "请我一顿",
    "地方史志",
    "零花钱",
    "自己生活",
    "自己扎",
    "风尘",
    "拎清",
    "赚的钱",
    "家里给",
    "大哥哥",
    "大哥",
    "天的",
)
EXTRA_UNSAFE_MARKERS = (
    "哈啊",
    "舒服吗",
    "我的里面",
    "射出来",
)


@dataclass(frozen=True)
class QuoteSeed:
    quote_id: str
    text: str
    metadata: dict[str, Any]


class QuoteEmbedder:
    def __init__(self, *, mode: str, dim: int) -> None:
        self.mode = str(mode or "auto").strip().lower()
        self.dim = max(1, int(dim))
        self.model_id = ""

    def embed(self, text: str) -> tuple[list[float], str]:
        if self.mode == "hash":
            values, model_id = hash_embed_text(text, dim=self.dim)
            self.model_id = model_id
            return values, model_id
        values, model_id = embed_text(text, dim=self.dim)
        self.model_id = model_id
        if model_id == EMBEDDING_MODEL_ID:
            self.mode = "hash"
        return list(values or []), str(model_id or "")

    def embed_many(self, texts: list[str]) -> tuple[list[list[float]], list[str]]:
        if not texts:
            return [], []
        if self.mode == "hash":
            pairs = [hash_embed_text(text, dim=self.dim) for text in texts]
            self.model_id = EMBEDDING_MODEL_ID
            return [list(values) for values, _ in pairs], [model_id for _, model_id in pairs]
        try:
            model = _get_sentence_transformer_model()
            encoded = model.encode(texts, normalize_embeddings=True)
            vectors = [[round(float(value), 6) for value in embedding] for embedding in encoded]
            if any(len(vector) != self.dim for vector in vectors):
                pairs = [hash_embed_text(text, dim=self.dim) for text in texts]
                self.mode = "hash"
                self.model_id = EMBEDDING_MODEL_ID
                return [list(values) for values, _ in pairs], [model_id for _, model_id in pairs]
            model_id = _resolve_semantic_embedding_model_id()
            self.model_id = model_id
            return vectors, [model_id] * len(vectors)
        except Exception:
            pairs = [hash_embed_text(text, dim=self.dim) for text in texts]
            self.mode = "hash"
            self.model_id = EMBEDDING_MODEL_ID
            return [list(values) for values, _ in pairs], [model_id for _, model_id in pairs]


def _read_jsonl(path: Path) -> Iterable[Mapping[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as file:
        for lineno, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, Mapping):
                raise ValueError(f"{path}:{lineno}: expected JSON object")
            yield payload


def _source_ref(row: Mapping[str, Any]) -> str:
    raw_file = str(row.get("file") or "").replace("\\", "/")
    raw_index = str(row.get("index") if row.get("index") is not None else "").strip()
    return f"{raw_file}#{raw_index}" if raw_index else raw_file


def _risk_tier(text: str, row: Mapping[str, Any]) -> str:
    if any(marker in text for marker in UNSAFE_MARKERS) or any(marker in text for marker in EXTRA_UNSAFE_MARKERS):
        return "explicit_unsafe"
    if KANA_RE.search(text) or ASCII_WORD_RE.search(text):
        return "unsafe_plot_line"
    if any(marker in text for marker in SCENE_POLLUTION_MARKERS) or any(
        marker in to_prompt_style_text(text) for marker in PROMPT_POLLUTION_MARKERS
    ) or any(marker in text for marker in EXTRA_PLOT_MARKERS):
        return "unsafe_plot_line"
    if not bool(row.get("daily_style_safe", True)):
        return "rhythm_fragment"
    if int(row.get("sora_candidate_score") or 0) <= 0:
        return "rhythm_fragment"
    if len(text) > 54:
        return "rhythm_fragment"
    return "safe_full_line"


def _metadata_from_row(row: Mapping[str, Any], *, text: str, risk_tier: str) -> dict[str, Any]:
    source_ref = _source_ref(row)
    digest = hashlib.sha1(f"{source_ref}\n{text}".encode("utf-8", errors="ignore")).hexdigest()[:20]
    source_score = int(row.get("sora_candidate_score") or 0)
    character_confidence, evidence = _quote_character_confidence(text, source_score)
    intents, emotions, moves, rhythms, cues = _classify_quote_tags(text)
    confidence = min(
        1.0,
        0.22
        + float(character_confidence) * 0.5
        + min(len(set(intents)) * 0.04, 0.16)
        + min(len(set(moves)) * 0.04, 0.16)
        + (0.08 if source_score >= 3 else 0.0),
    )
    return {
        "quote_id": f"surface_original_quote::{digest}",
        "source_ref": source_ref,
        "source_digest": digest,
        "source_score": source_score,
        "confidence": round(float(confidence), 4),
        "character_confidence": round(float(character_confidence), 4),
        "intent_tags": json.dumps(_ordered_unique(intents), ensure_ascii=False),
        "emotion_tags": json.dumps(_ordered_unique(emotions), ensure_ascii=False),
        "style_moves": json.dumps(_ordered_unique(moves), ensure_ascii=False),
        "rhythm_tags": json.dumps(_ordered_unique(rhythms), ensure_ascii=False),
        "lexical_cues": json.dumps(_ordered_unique(cues), ensure_ascii=False),
        "relationship_stages": json.dumps(_relationship_stages_for(intents, emotions), ensure_ascii=False),
        "risk_tier": risk_tier,
        "source_kind": "original_cn_all",
        "source_file": str(row.get("file") or ""),
        "source_index": int(row.get("index") or 0),
        "row_kind": str(row.get("kind") or ""),
        "daily_style_safe": bool(row.get("daily_style_safe", True)),
        "evidence": json.dumps(_ordered_unique(evidence), ensure_ascii=False),
    }


def load_quote_seeds(
    source: Path,
    *,
    include_narration: bool = False,
    include_explicit_unsafe: bool = True,
    max_rows: int = 0,
) -> tuple[list[QuoteSeed], dict[str, Any]]:
    seeds: dict[str, QuoteSeed] = {}
    stats = {
        "rows": 0,
        "dialogue_rows": 0,
        "narration_rows": 0,
        "insertable": 0,
        "skipped_non_dialogue": 0,
        "skipped_empty": 0,
        "skipped_explicit_unsafe": 0,
        "risk_tiers": {},
    }
    for row in _read_jsonl(source):
        stats["rows"] += 1
        row_kind = str(row.get("kind") or "").strip().lower()
        if row_kind == "dialogue":
            stats["dialogue_rows"] += 1
        elif row_kind == "narration":
            stats["narration_rows"] += 1
        if row_kind != "dialogue" and not include_narration:
            stats["skipped_non_dialogue"] += 1
            continue
        text = _clean_quote(row.get("text"))
        prompt_text = to_prompt_style_text(text)
        if not prompt_text:
            stats["skipped_empty"] += 1
            continue
        risk_tier = _risk_tier(prompt_text, row)
        risk_tiers = stats["risk_tiers"]
        if isinstance(risk_tiers, dict):
            risk_tiers[risk_tier] = int(risk_tiers.get(risk_tier) or 0) + 1
        if risk_tier == "explicit_unsafe" and not include_explicit_unsafe:
            stats["skipped_explicit_unsafe"] += 1
            continue
        metadata = _metadata_from_row(row, text=prompt_text, risk_tier=risk_tier)
        metadata["prompt_text"] = prompt_text
        quote_id = str(metadata["quote_id"])
        seeds[quote_id] = QuoteSeed(quote_id=quote_id, text=prompt_text, metadata=metadata)
        stats["insertable"] = len(seeds)
        if max_rows > 0 and len(seeds) >= max_rows:
            break
    return list(seeds.values()), stats


def _sanitize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in dict(metadata).items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
        else:
            sanitized[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return sanitized


def _collection_names(client: Any) -> list[str]:
    names: list[str] = []
    for row in client.list_collections():
        name = getattr(row, "name", row)
        normalized = str(name or "").strip()
        if normalized:
            names.append(normalized)
    return sorted(set(names))


def _build_chroma_client(endpoint: str | None = None) -> tuple[Any, dict[str, Any]]:
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
    }


def rebuild_surface_original_quote_collection(
    *,
    source: Path = DEFAULT_SOURCE,
    collection: str = DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION,
    dim: int = DEFAULT_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM,
    chroma_endpoint: str | None = None,
    embedding_mode: str = "auto",
    batch_size: int = 128,
    reset_collection: bool = False,
    include_narration: bool = False,
    include_explicit_unsafe: bool = True,
    max_rows: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    seeds, seed_stats = load_quote_seeds(
        source,
        include_narration=include_narration,
        include_explicit_unsafe=include_explicit_unsafe,
        max_rows=max_rows,
    )
    summary: dict[str, Any] = {
        "source": str(source),
        "collection": collection,
        "vector_dim": int(dim),
        "seed_count": len(seeds),
        "seed_stats": seed_stats,
        "dry_run": bool(dry_run),
        "embedding_mode": embedding_mode,
    }
    if dry_run:
        return summary

    client, target = _build_chroma_client(chroma_endpoint)
    if reset_collection and collection in _collection_names(client):
        client.delete_collection(name=collection)
    coll = client.get_or_create_collection(
        name=collection,
        metadata={
            "hnsw:space": "cosine",
            "vector_dim": int(dim),
            "resource_kind": "surface_original_quotes",
            "rebuilt_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    embedder = QuoteEmbedder(mode=embedding_mode, dim=int(dim))
    inserted = 0
    for start in range(0, len(seeds), max(1, int(batch_size))):
        batch = seeds[start : start + max(1, int(batch_size))]
        batch_texts = [seed.text for seed in batch]
        batch_embeddings, batch_model_ids = embedder.embed_many(batch_texts)
        ids: list[str] = []
        docs: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict[str, Any]] = []
        for index, seed in enumerate(batch):
            embedding = batch_embeddings[index]
            model_id = batch_model_ids[index]
            ids.append(seed.quote_id)
            docs.append(seed.text)
            metadatas.append(
                _sanitize_metadata(
                    {
                        **seed.metadata,
                        "embedding_model_id": model_id,
                        "vector_dim": int(dim),
                    }
                )
            )
            embeddings.append(embedding)
        coll.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metadatas)
        inserted += len(batch)
    summary.update(
        {
            "target": target,
            "reset_collection": bool(reset_collection),
            "inserted_count": inserted,
            "collection_count": coll.count(),
            "embedding_model_id": embedder.model_id,
            "collections": _collection_names(client),
        }
    )
    return summary


def _write_summary(summary: Mapping[str, Any]) -> Path:
    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = DEFAULT_OUT_DIR / "surface_original_quote_vector_latest.json"
    out.write_text(json.dumps(dict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--collection", default=DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION)
    parser.add_argument("--dim", type=int, default=DEFAULT_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM)
    parser.add_argument("--chroma-endpoint", default="")
    parser.add_argument("--embedding-mode", choices=("auto", "hash"), default="auto")
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--include-narration", action="store_true")
    parser.add_argument("--skip-explicit-unsafe", action="store_true")
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    summary = rebuild_surface_original_quote_collection(
        source=args.source,
        collection=str(args.collection or DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION),
        dim=max(1, int(args.dim or DEFAULT_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM)),
        chroma_endpoint=str(args.chroma_endpoint or "").strip() or None,
        embedding_mode=str(args.embedding_mode or "auto"),
        batch_size=max(1, int(args.batch_size or 128)),
        reset_collection=bool(args.reset),
        include_narration=bool(args.include_narration),
        include_explicit_unsafe=not bool(args.skip_explicit_unsafe),
        max_rows=max(0, int(args.max_rows or 0)),
        dry_run=bool(args.dry_run),
    )
    out_path = _write_summary(summary)
    print(json.dumps({**summary, "summary_path": str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
