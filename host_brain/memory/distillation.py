"""记忆蒸馏引擎（M10-C/D）。

将情节记忆（EpisodicStore）中的高频/高权重片段蒸馏为语义记忆（SemanticMemory），
写入 SQLite `semantic_memory` 表，供 context_pack.semantic_slot 分槽注入。

设计原则：
- 蒸馏是只读扫描 + 写入语义记忆，不修改情节记忆原始数据。
- 蒸馏结果幂等：相同 source_memory_ids 组合的蒸馏结果不重复写入。
- 每次蒸馏写 system_event_log 审计记录。
- M10-D：_merge_texts() 支持接入 LLM 摘要 API（LlmSummaryClient 协议）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.memory.episodic import EpisodicStore


# ---------------------------------------------------------------------------
# M10-D：LLM 摘要客户端协议（F-40）
# ---------------------------------------------------------------------------

@runtime_checkable
class LlmSummaryClient(Protocol):
    """LLM 摘要客户端协议。

    任何实现了 summarize() 方法的对象均可作为 LLM 摘要客户端传入。
    典型实现：OpenAI / LocalLLM / FallbackLLM 适配器。
    """

    def summarize(
        self,
        texts: list[str],
        *,
        query_text: str,
        max_tokens: int = 200,
    ) -> str:
        """\u5c06多条文本摘要为一条语义摘要。

        Args:
            texts: 待摘要的文本列表。
            query_text: 查询主题（用于引导摘要方向）。
            max_tokens: 摘要最大 token 数（默认 200）。

        Returns:
            摘要文本字符串。失败时应抛出异常，由调用方降级处理。
        """
        ...


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticMemoryRecord:
    """蒸馏后的语义记忆条目。"""
    semantic_id: str
    summary: str
    source_memory_ids: list[str]
    source_hash: str          # SHA-256(sorted source_memory_ids)，用于幂等检查
    distill_strategy: str     # "merge_top_k" | "keyword_extract" | "manual"
    confidence: float         # 0.0 ~ 1.0
    tags: list[str]
    ts_ms: int
    operator_node_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_id": self.semantic_id,
            "summary": self.summary,
            "source_memory_ids": list(self.source_memory_ids),
            "source_hash": self.source_hash,
            "distill_strategy": self.distill_strategy,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "ts_ms": self.ts_ms,
            "operator_node_id": self.operator_node_id,
        }


@dataclass(frozen=True)
class DistillResult:
    """蒸馏操作结果。"""
    success: bool
    semantic_id: str | None
    source_count: int
    detail: str
    ts_ms: int
    skipped: bool = False     # True 表示幂等跳过（已存在相同 source_hash）


# ---------------------------------------------------------------------------
# 蒸馏引擎
# ---------------------------------------------------------------------------

class MemoryDistillationEngine:
    """从情节记忆中蒸馏语义记忆。

    Args:
        episodic_store: 情节记忆存储（只读扫描）。
        ledger: SQLite 审计账本（写 semantic_memory + system_event_log）。
    """

    def __init__(
        self,
        *,
        episodic_store: EpisodicStore,
        ledger: SQLiteLedger,
    ) -> None:
        self.episodic = episodic_store
        self.ledger = ledger

    def distill_top_k(
        self,
        *,
        query_text: str,
        top_k: int = 5,
        distill_strategy: str = "merge_top_k",
        tags: list[str] | None = None,
        operator_node_id: str = "distillation_engine",
        ts_ms: int | None = None,
        llm_client: LlmSummaryClient | None = None,
    ) -> DistillResult:
        """从情节记忆中召回 Top-K 条目，合并蒸馏为一条语义记忆。

        Args:
            query_text: 召回查询文本（语义相似度检索）。
            top_k: 召回条目数（默认 5，最大 20）。
            distill_strategy: 蒸馏策略标识（默认 "merge_top_k"）。
            tags: 语义记忆标签列表（可选）。
            operator_node_id: 操作节点 ID（审计用）。
            ts_ms: 可选时间戳（毫秒）。
            llm_client: 可选 LLM 摘要客户端（M10-D F-40）。
                传入时使用 LLM 生成摘要；不传入时降级为文本拼接。

        Returns:
            DistillResult，含 semantic_id 和蒸馏详情。
        """
        distill_ts = int(ts_ms if ts_ms is not None else now_ms())
        top_k = min(max(1, top_k), 20)

        # 1. 召回 Top-K 情节记忆
        recalled = self.episodic.recall(query_text=query_text, top_k=top_k)
        if not recalled:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=0,
                detail="no episodic memories found for query",
                ts_ms=distill_ts,
            )

        source_ids = [r["memory_id"] for r in recalled]
        source_hash = _compute_source_hash(source_ids)

        # 2. 幂等检查：相同 source_hash 已存在则跳过
        existing = self.ledger.get_semantic_memory_by_source_hash(source_hash)
        if existing is not None:
            return DistillResult(
                success=True,
                semantic_id=existing["semantic_id"],
                source_count=len(source_ids),
                detail=f"skipped: semantic memory already exists for source_hash={source_hash[:12]}",
                ts_ms=distill_ts,
                skipped=True,
            )

        # 3. 合并蒸馏：优先使用 LLM 摘要，降级为文本拼接（M10-D F-40）
        texts = [r["text"] for r in recalled]
        if llm_client is not None:
            try:
                summary = llm_client.summarize(
                    texts,
                    query_text=query_text,
                    max_tokens=200,
                )
                distill_strategy = "llm_summary"
            except Exception:
                # LLM 摘要失败时降级为文本拼接
                summary = _merge_texts(texts, query_text=query_text)
        else:
            summary = _merge_texts(texts, query_text=query_text)

        # 4. 生成 semantic_id
        import uuid
        semantic_id = f"sem_{uuid.uuid4().hex}"

        record = SemanticMemoryRecord(
            semantic_id=semantic_id,
            summary=summary,
            source_memory_ids=source_ids,
            source_hash=source_hash,
            distill_strategy=distill_strategy,
            confidence=_compute_confidence(recalled),
            tags=list(tags or []),
            ts_ms=distill_ts,
            operator_node_id=operator_node_id,
        )

        # 5. 写入 semantic_memory 表
        self.ledger.log_semantic_memory(record.to_dict())

        # 6. 写 system_event_log 审计记录
        try:
            self.ledger.log_system_event({
                "request_id": semantic_id,
                "event_type": "MEMORY_DISTILLED",
                "source": operator_node_id,
                "detail": (
                    f"semantic_id={semantic_id} "
                    f"source_count={len(source_ids)} "
                    f"strategy={distill_strategy} "
                    f"confidence={record.confidence:.3f}"
                ),
                "ts_ms": distill_ts,
            })
        except Exception:
            pass  # 审计失败不影响主流程

        return DistillResult(
            success=True,
            semantic_id=semantic_id,
            source_count=len(source_ids),
            detail=f"distilled {len(source_ids)} episodic memories into semantic_id={semantic_id}",
            ts_ms=distill_ts,
        )

    def list_semantic_memories(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """列出语义记忆（分页）。

        Returns:
            (items, total) 元组。
        """
        return self.ledger.list_semantic_memories(limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _compute_source_hash(source_ids: list[str]) -> str:
    """计算 source_memory_ids 的 SHA-256 哈希（排序后，用于幂等检查）。"""
    sorted_ids = sorted(source_ids)
    payload = json.dumps(sorted_ids, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _merge_texts(texts: list[str], *, query_text: str) -> str:
    """将多条情节记忆文本合并为语义摘要。

    当前实现：简单拼接（用换行分隔），前缀查询主题。
    未来可替换为 LLM 摘要调用。
    """
    header = f"[语义摘要 | 查询: {query_text[:50]}]"
    body = "\n---\n".join(f"• {t.strip()}" for t in texts if t.strip())
    return f"{header}\n{body}"


def _compute_confidence(recalled: list[dict[str, Any]]) -> float:
    """根据召回结果的相似度分数计算置信度（0.0~1.0）。"""
    if not recalled:
        return 0.0
    scores = [float(r.get("score", 0.0)) for r in recalled]
    avg = sum(scores) / len(scores)
    return round(min(max(avg, 0.0), 1.0), 4)
