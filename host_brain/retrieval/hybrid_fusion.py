"""召回链的向量/全文候选融合策略。

本模块位于检索读路径，只对已返回的候选做确定性打分和排序，不读取或写入
记忆事实，也不授予任何执行权限。全文索引缺失、查询失败或配置非法时，调用
方保留原向量候选，以避免可选召回通道影响主检索的可用性。
"""
from __future__ import annotations

from typing import Any

DEFAULT_VECTOR_WEIGHT: float = 0.7
DEFAULT_FTS_WEIGHT: float = 0.3

class HybridFusionStrategy:
    """向量检索 + 全文检索混合融合策略。

    融合公式：final_score = vector_score × vector_weight + fts_score × fts_weight
    要求：vector_weight + fts_weight == 1.0（否则抛出 ValueError）。
    """

    def __init__(
        self,
        *,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        fts_weight: float = DEFAULT_FTS_WEIGHT,
    ) -> None:
        """初始化混合融合策略。

        Args:
            vector_weight: 向量检索权重（默认 0.7）。
            fts_weight: 全文检索权重（默认 0.3）。

        Raises:
            ValueError: 如果 vector_weight + fts_weight != 1.0。
        """
        total = round(float(vector_weight) + float(fts_weight), 6)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"vector_weight + fts_weight 必须等于 1.0，当前为 {total}"
            )
        self.vector_weight = float(vector_weight)
        self.fts_weight = float(fts_weight)

    def fuse(
        self,
        *,
        vector_candidates: list[dict[str, Any]],
        fts_candidates: list[dict[str, Any]],
        # M29-O（OPT-?）：情感强度加权
        emotion_context: str | None = None,
        emotion_boost_factor: float = 1.2,
    ) -> list[dict[str, Any]]:
        """融合向量检索和全文检索结果，且只重排已有候选。

        M29-O（OPT-?）：支持情感强度加权——emotion_context 匹配的候选项分数加权。

        Args:
            vector_candidates: 向量检索候选列表（含 doc_id / score 字段）。
            fts_candidates: 全文检索候选列表（含 doc_id / fts_score 字段）。
            emotion_context: 当前情感上下文标签（如 "HAPPY"），匹配时加权
            emotion_boost_factor: 情感匹配加权系数（默认 1.2x）

        Returns:
        融合后的候选列表，按 final_score 降序排列，同 doc_id 去重保留最高分。

        同一 ``doc_id`` 的候选先合并两个独立检索分数，再以 doc_id 作为并列
        排序键，保证同样输入在多 worker/重试下产生相同 recall 次序。情感加权
        只调整已召回条目的排序，不生成新记忆或改变其 canonical 置信事实。
        """
        # 构建 doc_id → 候选 映射
        fused: dict[str, dict[str, Any]] = {}

        # 处理向量检索结果
        for cand in vector_candidates:
            doc_id = str(cand.get("doc_id") or "")
            if not doc_id:
                continue
            vector_score = float(cand.get("score") or 0.0)
            entry = dict(cand)
            entry["vector_score"] = vector_score
            entry["fts_score"] = 0.0
            entry["score"] = round(vector_score * self.vector_weight, 6)
            fused[doc_id] = entry

        # 处理全文检索结果，与向量结果融合
        for fts_cand in fts_candidates:
            doc_id = str(fts_cand.get("doc_id") or "")
            if not doc_id:
                continue
            fts_score = float(fts_cand.get("fts_score") or 0.0)
            if doc_id in fused:
                # 已有向量结果，叠加全文分数
                existing = fused[doc_id]
                vector_score = float(existing.get("vector_score") or 0.0)
                final_score = round(
                    vector_score * self.vector_weight + fts_score * self.fts_weight,
                    6,
                )
                existing["fts_score"] = fts_score
                existing["score"] = final_score
                # 更新 metadata 中的融合信息
                metadata = dict(existing.get("metadata") or {})
                metadata["fts_score"] = fts_score
                metadata["hybrid_fused"] = True
                existing["metadata"] = metadata
            else:
                # 仅全文命中，无向量结果
                snippet = str(fts_cand.get("snippet") or "")
                entry = {
                    "doc_id": doc_id,
                    "source_ref": f"memory://episodic/{doc_id}",
                    "snippet": snippet,
                    "score": round(fts_score * self.fts_weight, 6),
                    "confidence": round(fts_score * self.fts_weight, 6),
                    "vector_score": 0.0,
                    "fts_score": fts_score,
                    "metadata": {
                        "fts_score": fts_score,
                        "hybrid_fused": True,
                        "matched_routes": ["fts"],
                    },
                    "evidence_refs": f"evd://episodic/{doc_id}",
                }
                fused[doc_id] = entry

        # M29-O：情感强度加权——emotion_context 匹配的候选项分数加权
        if emotion_context:
            for entry in fused.values():
                meta = entry.get("metadata") or {}
                if meta.get("emotion_tag") == str(emotion_context).strip().upper():
                    entry["score"] = min(1.0, round(float(entry["score"]) * emotion_boost_factor, 6))

        # 按 final_score 降序排列
        return sorted(
            fused.values(),
            key=lambda row: (-float(row["score"]), str(row["doc_id"])),
        )
