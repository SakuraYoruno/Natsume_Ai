"""Episodic memory store with evidence-gated lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable
import uuid

import logging

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CHROMA_VECTOR_DIM_MISMATCH,
    MEMORY_EVIDENCE_REQUIRED,
    MEMORY_MERGE_INVALID,
    MEMORY_NOT_FOUND,
)
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import (
    ChromaClientProtocol,
    build_runtime_chroma_client,
    embed_text,
    hash_embed_text,
    resolve_collection_vector_dim,
)

_logger = logging.getLogger("Natsume.episodic")


@dataclass
class EpisodicRecord:
    memory_id: str
    text: str
    ts_ms: int
    text_hash: str
    decay_profile_id: str
    evidence_refs: list[str]
    visibility: str
    embedding_model_id: str
    merged_into: str | None = None
    deleted_at: int | None = None
    # M27-D：情感标注字段（OPT-25）
    emotion_tag: str = "NEUTRAL"
    emotion_valence: float = 0.0
    emotion_arousal: float = 0.3
    # Phase XVII：记忆人性化字段
    emotion_color_tag: str = "NEUTRAL"
    """回忆时的情绪染色标签——影响叙述方式（与写入时 emotion_tag 可不同，随当前情绪重新着色）"""
    suppression_flag: bool = False
    """压制标志——痛苦/高冲突记忆降低检索概率（不删除，仅降权）"""
    distortion_level: float = 0.0
    """失真度 [0.0, 1.0]——普通记忆随时间细节模糊；高情绪强度记忆豁免失真"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "text": self.text,
            "ts_ms": self.ts_ms,
            "text_hash": self.text_hash,
            "decay_profile_id": self.decay_profile_id,
            "evidence_refs": list(self.evidence_refs),
            "visibility": self.visibility,
            "embedding_model_id": self.embedding_model_id,
            "merged_into": self.merged_into,
            "deleted_at": self.deleted_at,
            "emotion_tag": self.emotion_tag,
            "emotion_valence": self.emotion_valence,
            "emotion_arousal": self.emotion_arousal,
            "emotion_color_tag": self.emotion_color_tag,
            "suppression_flag": self.suppression_flag,
            "distortion_level": self.distortion_level,
        }


@dataclass(frozen=True)
class MemoryOpResult:
    success: bool
    reason_code: str | None
    detail: str
    memory_id: str | None = None


class EpisodicStore:
    """Single-writer episodic memory store with lifecycle audit logs."""

    # 内存中最多保留的记忆条数（LRU 淘汰），防止长期运行 OOM
    _MAX_RECORDS = 10_000

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        chroma_client: ChromaClientProtocol | None = None,
        embedding_fn: Callable[[str], tuple[list[float], str]] | None = None,
        embedding_model_id_hint: str | None = None,
        embedding_provider_hint: str | None = None,
    ) -> None:
        self.ledger = ledger or SQLiteLedger()
        self.chroma = chroma_client or build_runtime_chroma_client()
        self._embedding_fn = embedding_fn
        self.embedding_model_id_hint = str(embedding_model_id_hint or "").strip() or None
        self.embedding_provider_hint = str(embedding_provider_hint or "").strip() or None
        # 使用 OrderedDict 实现 LRU：最近写入/访问的记录在末尾
        from collections import OrderedDict
        self._records: OrderedDict[str, EpisodicRecord] = OrderedDict()

    def _embed_text(self, text: str) -> tuple[list[float], str]:
        if self._embedding_fn is not None:
            return self._embedding_fn(str(text or "").strip())
        return embed_text(str(text or "").strip(), dim=self._resolve_embedding_dim())

    def _resolve_embedding_dim(self) -> int:
        return resolve_collection_vector_dim(self.chroma)

    def write_memory(
        self,
        *,
        text: str,
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        decay_profile_id: str = "default",
        visibility: str = "internal",
        ts_ms: int | None = None,
        emotion_tag: str = "NEUTRAL",
        emotion_valence: float = 0.0,
        emotion_arousal: float = 0.3,
    ) -> MemoryOpResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        if not refs:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="WRITE",
                memory_id=None,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=[],
                status="REJECTED",
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic write",
                decay_profile_id=decay_profile_id,
                visibility=visibility,
                ts_ms=ts_value,
            )
            return MemoryOpResult(
                success=False,
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic write",
            )

        if not str(text).strip():
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="WRITE",
                memory_id=None,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=refs,
                status="REJECTED",
                reason_code=MEMORY_MERGE_INVALID,
                detail="memory text cannot be empty",
                decay_profile_id=decay_profile_id,
                visibility=visibility,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_MERGE_INVALID, "memory text cannot be empty")

        record = self._create_record(
            text=text,
            evidence_refs=refs,
            ts_ms=ts_value,
            decay_profile_id=decay_profile_id,
            visibility=visibility,
            emotion_tag=emotion_tag,
            emotion_valence=emotion_valence,
            emotion_arousal=emotion_arousal,
        )
        self._log_memory_op(
            request_id=request_id,
            intent_id=intent_id,
            op_kind="WRITE",
            memory_id=record.memory_id,
            target_memory_id=None,
            source_memory_ids=None,
            evidence_refs=refs,
            status="SUCCESS",
            reason_code=None,
            detail=_build_memory_write_detail(record.text),
            decay_profile_id=record.decay_profile_id,
            visibility=record.visibility,
            ts_ms=ts_value,
        )
        return MemoryOpResult(True, None, "memory written", memory_id=record.memory_id)

    def merge_memories(
        self,
        *,
        source_memory_ids: list[str],
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        merged_text: str | None = None,
        decay_profile_id: str = "default",
        visibility: str = "internal",
        ts_ms: int | None = None,
    ) -> MemoryOpResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        source_ids = _normalize_memory_ids(source_memory_ids)

        if not refs:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="MERGE",
                memory_id=None,
                target_memory_id=None,
                source_memory_ids=source_ids,
                evidence_refs=[],
                status="REJECTED",
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic merge",
                decay_profile_id=decay_profile_id,
                visibility=visibility,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_EVIDENCE_REQUIRED, "evidence_refs is required for episodic merge")

        if len(source_ids) < 2:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="MERGE",
                memory_id=None,
                target_memory_id=None,
                source_memory_ids=source_ids,
                evidence_refs=refs,
                status="REJECTED",
                reason_code=MEMORY_MERGE_INVALID,
                detail="merge requires at least two source memory_ids",
                decay_profile_id=decay_profile_id,
                visibility=visibility,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_MERGE_INVALID, "merge requires at least two source memory_ids")

        sources: list[EpisodicRecord] = []
        for source_id in source_ids:
            source = self._records.get(source_id)
            if source is None or source.deleted_at is not None:
                self._log_memory_op(
                    request_id=request_id,
                    intent_id=intent_id,
                    op_kind="MERGE",
                    memory_id=None,
                    target_memory_id=None,
                    source_memory_ids=source_ids,
                    evidence_refs=refs,
                    status="REJECTED",
                    reason_code=MEMORY_NOT_FOUND,
                    detail=f"source memory not found: {source_id}",
                    decay_profile_id=decay_profile_id,
                    visibility=visibility,
                    ts_ms=ts_value,
                )
                return MemoryOpResult(False, MEMORY_NOT_FOUND, f"source memory not found: {source_id}")
            sources.append(source)

        merge_text = str(merged_text).strip() if merged_text is not None else "\n".join(
            source.text for source in sources
        )
        target = self._create_record(
            text=merge_text,
            evidence_refs=refs,
            ts_ms=ts_value,
            decay_profile_id=decay_profile_id,
            visibility=visibility,
        )

        for source in sources:
            source.merged_into = target.memory_id

        self._log_memory_op(
            request_id=request_id,
            intent_id=intent_id,
            op_kind="MERGE",
            memory_id=target.memory_id,
            target_memory_id=target.memory_id,
            source_memory_ids=source_ids,
            evidence_refs=refs,
            status="SUCCESS",
            reason_code=None,
            detail="memory merge completed",
            decay_profile_id=target.decay_profile_id,
            visibility=target.visibility,
            ts_ms=ts_value,
        )
        return MemoryOpResult(True, None, "memory merge completed", memory_id=target.memory_id)

    def decay_memory(
        self,
        *,
        memory_id: str,
        decay_profile_id: str,
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        ts_ms: int | None = None,
    ) -> MemoryOpResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        if not refs:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="DECAY",
                memory_id=memory_id,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=[],
                status="REJECTED",
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic decay",
                decay_profile_id=decay_profile_id,
                visibility=None,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_EVIDENCE_REQUIRED, "evidence_refs is required for episodic decay")

        record = self._records.get(memory_id)
        if record is None or record.deleted_at is not None:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="DECAY",
                memory_id=memory_id,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=refs,
                status="REJECTED",
                reason_code=MEMORY_NOT_FOUND,
                detail=f"memory not found: {memory_id}",
                decay_profile_id=decay_profile_id,
                visibility=None,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_NOT_FOUND, f"memory not found: {memory_id}")

        record.decay_profile_id = str(decay_profile_id).strip() or record.decay_profile_id
        self._log_memory_op(
            request_id=request_id,
            intent_id=intent_id,
            op_kind="DECAY",
            memory_id=record.memory_id,
            target_memory_id=None,
            source_memory_ids=None,
            evidence_refs=refs,
            status="SUCCESS",
            reason_code=None,
            detail="memory decay profile updated",
            decay_profile_id=record.decay_profile_id,
            visibility=record.visibility,
            ts_ms=ts_value,
        )
        return MemoryOpResult(True, None, "memory decay profile updated", memory_id=record.memory_id)

    def delete_memory(
        self,
        *,
        memory_id: str,
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        ts_ms: int | None = None,
    ) -> MemoryOpResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        if not refs:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="DELETE",
                memory_id=memory_id,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=[],
                status="REJECTED",
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic delete",
                decay_profile_id=None,
                visibility=None,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_EVIDENCE_REQUIRED, "evidence_refs is required for episodic delete")

        record = self._records.get(memory_id)
        if record is None or record.deleted_at is not None:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="DELETE",
                memory_id=memory_id,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=refs,
                status="REJECTED",
                reason_code=MEMORY_NOT_FOUND,
                detail=f"memory not found: {memory_id}",
                decay_profile_id=None,
                visibility=None,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_NOT_FOUND, f"memory not found: {memory_id}")

        record.deleted_at = ts_value
        self.chroma.delete(memory_id)
        self._log_memory_op(
            request_id=request_id,
            intent_id=intent_id,
            op_kind="DELETE",
            memory_id=record.memory_id,
            target_memory_id=None,
            source_memory_ids=None,
            evidence_refs=refs,
            status="SUCCESS",
            reason_code=None,
            detail="memory deleted",
            decay_profile_id=record.decay_profile_id,
            visibility=record.visibility,
            ts_ms=ts_value,
        )
        return MemoryOpResult(True, None, "memory deleted", memory_id=record.memory_id)

    def recall(
        self,
        *,
        query_text: str,
        top_k: int = 5,
        emotion_filter: str | None = None,
        # M29-B（OPT-3）：情感上下文加权召回
        emotion_context: str | None = None,
        emotion_boost_factor: float = 1.3,
        # Phase XVII：人性化记忆召回控制
        apply_decay: bool = True,
        apply_suppression: bool = True,
        apply_distortion: bool = True,
        now_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """情感感知记忆召回（M29-B OPT-3 + Phase XVII 人性化）。

        Phase XVII 新增三层权重融合：
        1. memory_decay_score：时间衰减，普通记忆随时间降权（高情绪强度豁免）
        2. suppression_flag：压制标志，痛苦记忆降权至 0.3x
        3. distortion_level：失真度，细节模糊的记忆轻微降权（1.0 - distortion * 0.2）

        emotion_context 匹配的记录分数加权 emotion_boost_factor（默认 1.3x）。
        emotion_filter 仍保留向后兼容（优先返回匹配情感的记录）。
        """
        embedding, _ = self._embed_text(query_text)
        # 多取一些候选，衰减后再截断到 top_k
        fetch_k = max(top_k * 3, top_k + 10)
        results = self.chroma.search(query_embedding=embedding, top_k=fetch_k)

        # 过滤 LRU 已淘汰的幽灵记录（Chroma 中存在但内存中已移除）
        results = [r for r in results if r.get("memory_id") in self._records]

        # Phase XVII：三层权重融合
        now_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
        processed: list[dict[str, Any]] = []
        for r in results:
            record = self._records.get(r.get("memory_id", ""))
            score = float(r.get("score") or 0.0)

            if record is not None:
                # 层1：时间衰减
                if apply_decay:
                    decay_score = self.compute_memory_decay_score(record, now_ts_ms=now_value)
                    # 衰减分作为乘法因子：score * decay_score（保留语义相关性的主导地位）
                    score = score * (0.4 + 0.6 * decay_score)  # 衰减最多降至 40%

                # 层2：压制标志
                if apply_suppression and record.suppression_flag:
                    score = score * 0.3  # 压制记忆降权至 30%

                # 层3：失真度（轻微降权，不影响主要排序）
                if apply_distortion and record.distortion_level > 0.0:
                    distortion_factor = 1.0 - float(record.distortion_level) * 0.2
                    score = score * distortion_factor

            r = dict(r)
            r["score"] = round(max(0.0, min(1.0, score)), 6)
            # 注入人性化字段到 metadata，供上层感知
            if record is not None:
                meta = dict(r.get("metadata") or {})
                meta["emotion_color_tag"] = record.emotion_color_tag
                meta["suppression_flag"] = record.suppression_flag
                meta["distortion_level"] = record.distortion_level
                r["metadata"] = meta
            processed.append(r)

        # M29-B：情感上下文加权
        if emotion_context:
            boosted: list[dict[str, Any]] = []
            for r in processed:
                meta = r.get("metadata") or {}
                score = float(r.get("score") or 0.0)
                if meta.get("emotion_tag") == str(emotion_context).strip().upper():
                    # 使用加法偏移而非乘法，避免高分区间被压缩到 1.0 失去区分度
                    boost_delta = (float(emotion_boost_factor) - 1.0) * score
                    score = min(1.0, score + boost_delta)
                    r = dict(r)
                    r["score"] = round(score, 6)
                boosted.append(r)
            processed = sorted(boosted, key=lambda x: float(x.get("score") or 0.0), reverse=True)
        else:
            processed = sorted(processed, key=lambda x: float(x.get("score") or 0.0), reverse=True)

        # 截断到 top_k
        processed = processed[:max(1, top_k)]

        if emotion_filter is not None:
            # 向后兼容：优先返回匹配情感标签的记录
            matched = [r for r in processed if (r.get("metadata") or {}).get("emotion_tag") == emotion_filter]
            unmatched = [r for r in processed if (r.get("metadata") or {}).get("emotion_tag") != emotion_filter]
            return matched + unmatched
        return processed

    # Phase XVII：记忆衰减分计算
    # 衰减分 [0.0, 1.0]：1.0 = 全新记忆，0.0 = 完全衰减
    # 高情绪强度（|valence| > 0.6 或 arousal > 0.7）豁免衰减
    _EMOTION_INTENSITY_EXEMPT_THRESHOLD_VALENCE: float = 0.6
    _EMOTION_INTENSITY_EXEMPT_THRESHOLD_AROUSAL: float = 0.7

    # M29-I（OPT-?）：decay_profile_id 实装——FAST/SLOW/PERMANENT 三档
    _DECAY_PROFILES: dict[str, dict[str, Any]] = {
        "FAST":      {"ttl_days": 7,   "decay_rate": 0.9, "label": "快速遗忘"},
        "SLOW":      {"ttl_days": 90,  "decay_rate": 0.3, "label": "缓慢遗忘"},
        "PERMANENT": {"ttl_days": None, "decay_rate": 0.0, "label": "永久保留"},
        "default":   {"ttl_days": 30,  "decay_rate": 0.5, "label": "默认"},
    }

    def get_decay_profile(self, decay_profile_id: str) -> dict[str, Any]:
        """M29-I：返回 decay_profile_id 对应的衰减配置。"""
        return self._DECAY_PROFILES.get(
            str(decay_profile_id).strip().upper(),
            self._DECAY_PROFILES["default"],
        )

    def is_expired(self, record: "EpisodicRecord", *, now_ts_ms: int | None = None) -> bool:
        """M29-I：根据 decay_profile_id 判断记忆是否已过期。"""
        profile = self.get_decay_profile(record.decay_profile_id)
        ttl_days = profile.get("ttl_days")
        if ttl_days is None:
            return False  # PERMANENT
        now_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
        age_days = (now_value - record.ts_ms) / (1000 * 86400)
        return age_days > ttl_days

    def compute_memory_decay_score(
        self,
        record: "EpisodicRecord",
        *,
        now_ts_ms: int | None = None,
    ) -> float:
        """Phase XVII：计算记忆衰减分 [0.0, 1.0]。

        规则：
        - PERMANENT profile → 1.0（永不衰减）
        - 高情绪强度（|valence| > 0.6 或 arousal > 0.7）→ 豁免衰减，返回 1.0
        - 其余：基于 age_days / ttl_days 的指数衰减，decay_rate 越高衰减越快
          score = exp(-decay_rate * age_days / ttl_days * 5)
          保底 0.05（记忆不会完全消失，只是极难被检索到）
        """
        profile = self.get_decay_profile(record.decay_profile_id)
        ttl_days = profile.get("ttl_days")
        if ttl_days is None:
            return 1.0  # PERMANENT

        # 高情绪强度豁免
        if (
            abs(float(record.emotion_valence)) > self._EMOTION_INTENSITY_EXEMPT_THRESHOLD_VALENCE
            or float(record.emotion_arousal) > self._EMOTION_INTENSITY_EXEMPT_THRESHOLD_AROUSAL
        ):
            return 1.0

        now_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
        age_days = max(0.0, (now_value - record.ts_ms) / (1000.0 * 86400.0))
        decay_rate = float(profile.get("decay_rate", 0.5))
        # 指数衰减：age_days = ttl_days 时，score ≈ exp(-5 * decay_rate)
        # FAST(0.9): score ≈ 0.011；SLOW(0.3): score ≈ 0.22；default(0.5): score ≈ 0.082
        import math as _math
        raw = _math.exp(-decay_rate * age_days / float(ttl_days) * 5.0)
        return round(max(0.05, min(1.0, raw)), 4)

    def update_emotion_color(
        self,
        memory_id: str,
        *,
        current_emotion_tag: str,
    ) -> bool:
        """Phase XVII：用当前情绪重新着色记忆的 emotion_color_tag。

        在 recall() 时调用，使回忆时的叙述方式受当前情绪影响。
        返回 True 表示成功更新，False 表示记忆不存在。
        """
        record = self._records.get(memory_id)
        if record is None or record.deleted_at is not None:
            return False
        record.emotion_color_tag = str(current_emotion_tag).strip().upper() or record.emotion_tag
        return True

    def suppress_memory(
        self,
        memory_id: str,
        *,
        suppress: bool = True,
    ) -> bool:
        """Phase XVII：设置/解除记忆压制标志。

        压制的记忆在检索时降权（乘以 0.3），但不删除。
        Returns True 表示成功，False 表示记忆不存在。
        """
        record = self._records.get(memory_id)
        if record is None or record.deleted_at is not None:
            return False
        record.suppression_flag = bool(suppress)
        return True

    def get_memory(self, memory_id: str) -> EpisodicRecord | None:
        return self._records.get(memory_id)

    def replay_ops(self, *, memory_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.ledger.memory_ops_log
        if memory_id is None:
            return rows

        filtered: list[dict[str, Any]] = []
        for row in rows:
            if row.get("memory_id") == memory_id:
                filtered.append(row)
                continue
            if row.get("target_memory_id") == memory_id:
                filtered.append(row)
                continue
            sources = row.get("source_memory_ids") or []
            if isinstance(sources, list) and memory_id in sources:
                filtered.append(row)
        return filtered

    def _create_record(
        self,
        *,
        text: str,
        evidence_refs: list[str],
        ts_ms: int,
        decay_profile_id: str,
        visibility: str,
        emotion_tag: str = "NEUTRAL",
        emotion_valence: float = 0.0,
        emotion_arousal: float = 0.3,
    ) -> EpisodicRecord:
        memory_id = f"mem_{uuid.uuid4().hex}"
        normalized_text = str(text).strip()
        text_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        embedding, model_id = self._embed_text(normalized_text)
        # 截断情感数值到合法范围
        valence = max(-1.0, min(1.0, float(emotion_valence)))
        arousal = max(0.0, min(1.0, float(emotion_arousal)))
        # Phase XVII：计算初始失真度（写入时为 0，随时间由 tick 更新）
        # 初始 emotion_color_tag 与 emotion_tag 相同（回忆时可被当前情绪重新着色）
        record = EpisodicRecord(
            memory_id=memory_id,
            text=normalized_text,
            ts_ms=ts_ms,
            text_hash=text_hash,
            decay_profile_id=str(decay_profile_id).strip() or "default",
            evidence_refs=list(evidence_refs),
            visibility=str(visibility).strip() or "internal",
            embedding_model_id=model_id,
            emotion_tag=str(emotion_tag).strip().upper() or "NEUTRAL",
            emotion_valence=valence,
            emotion_arousal=arousal,
            emotion_color_tag=str(emotion_tag).strip().upper() or "NEUTRAL",
            suppression_flag=False,
            distortion_level=0.0,
        )
        self._records[memory_id] = record
        self._records.move_to_end(memory_id)
        # LRU 淘汰：超过上限时移除最旧的记录（仅从内存中移除，Chroma 和 Ledger 保留）
        while len(self._records) > self._MAX_RECORDS:
            oldest_id, _ = self._records.popitem(last=False)
            _logger.debug("EpisodicStore LRU 淘汰内存记录: %s", oldest_id)
        self._upsert_record_with_fallback(
            record=record,
            memory_id=memory_id,
            text=normalized_text,
            embedding=embedding,
            metadata={
                "ts_ms": ts_ms,
                "text_hash": text_hash,
                "visibility": record.visibility,
                "evidence_refs": list(evidence_refs),
                "embedding_model_id": model_id,
                "emotion_tag": record.emotion_tag,
                "emotion_valence": record.emotion_valence,
                "emotion_arousal": record.emotion_arousal,
                # Phase XVII：人性化记忆字段
                "emotion_color_tag": record.emotion_color_tag,
                "suppression_flag": record.suppression_flag,
                "distortion_level": record.distortion_level,
            },
        )
        return record

    def _upsert_record_with_fallback(
        self,
        *,
        record: EpisodicRecord,
        memory_id: str,
        text: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        try:
            self.chroma.upsert(
                memory_id=memory_id,
                text=text,
                embedding=embedding,
                metadata=dict(metadata or {}),
            )
        except ValueError as exc:
            if str(exc) != CHROMA_VECTOR_DIM_MISMATCH:
                raise
            fallback_dim = self._resolve_embedding_dim()
            fallback_embedding, fallback_model_id = hash_embed_text(text, dim=fallback_dim)
            fallback_metadata = dict(metadata or {})
            fallback_metadata["embedding_model_id"] = fallback_model_id
            record.embedding_model_id = fallback_model_id
            _logger.warning(
                "EpisodicStore chroma vector dim mismatch; fallback to hash embedding dim=%s text=%s",
                fallback_dim,
                str(text or "")[:80],
            )
            self.chroma.upsert(
                memory_id=memory_id,
                text=text,
                embedding=fallback_embedding,
                metadata=fallback_metadata,
            )

    def _log_memory_op(
        self,
        *,
        request_id: str | None,
        intent_id: str | None,
        op_kind: str,
        memory_id: str | None,
        target_memory_id: str | None,
        source_memory_ids: list[str] | None,
        evidence_refs: list[str],
        status: str,
        reason_code: str | None,
        detail: str,
        decay_profile_id: str | None,
        visibility: str | None,
        ts_ms: int,
    ) -> None:
        self.ledger.log_memory_op(
            {
                "request_id": request_id,
                "intent_id": intent_id,
                "op_kind": op_kind,
                "memory_id": memory_id,
                "target_memory_id": target_memory_id,
                "source_memory_ids": source_memory_ids,
                "evidence_refs": evidence_refs,
                "status": status,
                "reason_code": reason_code,
                "detail": detail,
                "decay_profile_id": decay_profile_id,
                "visibility": visibility,
                "ts_ms": ts_ms,
            }
        )


def _normalize_evidence_refs(evidence_refs: list[str] | None) -> list[str]:
    if not evidence_refs:
        return []
    normalized: list[str] = []
    for value in evidence_refs:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _normalize_memory_ids(memory_ids: list[str] | None) -> list[str]:
    if not memory_ids:
        return []
    normalized: list[str] = []
    for value in memory_ids:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _build_memory_write_detail(text: str, *, limit: int = 240) -> str:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return "memory written"
    max_len = max(1, int(limit))
    if len(normalized) > max_len:
        normalized = normalized[: max(1, max_len - 3)] + "..."
    return f"memory written: {normalized}"
