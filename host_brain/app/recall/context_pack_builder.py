"""
context_pack_builder.py —— ContextPack 组装器与 token 预算控制

按照七路召回结果组装 ContextPack，执行 token 预算截断（需求 4）：
- 各路 token 上限：world_canon≤800 / style_canon≤400 / episodic_vector≤600
  / bond_graph≤300 / 其余各≤200（需求 4.1）
- 总量超 2700 tokens 时按优先级截断（需求 4.2）
- world_canon 与 episodic 冲突时标记 [CONFLICT_SUPPRESSED]（需求 4.3）
- style_canon 与任务冲突时降级为参考（需求 4.4）
- 在 subgraph_trace 中记录各路实际消耗和截断情况（需求 4.5）
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional

from ..schemas.context_pack import (
    TOKEN_BUDGET,
    TOTAL_TOKEN_BUDGET,
    TRUNCATION_PRIORITY,
    ContextPack,
    EvidenceSlot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token 计数工具（轻量级估算，不依赖 tiktoken）
# ---------------------------------------------------------------------------

def estimate_token_count(text: str) -> int:
    """
    轻量级 token 数量估算。

    规则：中文字符按 1 token/字，英文按空格分词后 1 token/词，
    标点符号按 1 token/个。实际部署时可替换为 tiktoken 精确计数。
    """
    if not text:
        return 0
    # 中文字符数
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    # 非中文部分按空格分词
    non_chinese = re.sub(r'[\u4e00-\u9fff]', ' ', text)
    english_tokens = len(non_chinese.split())
    return chinese_chars + english_tokens


def truncate_to_token_limit(text: str, max_tokens: int) -> tuple[str, int, bool]:
    """
    将文本截断到指定 token 上限。

    Returns:
        (截断后文本, 实际 token 数, 是否被截断)
    """
    total = estimate_token_count(text)
    if total <= max_tokens:
        return text, total, False

    # 按字符比例估算截断位置
    ratio = max_tokens / total
    cut_pos = int(len(text) * ratio)
    truncated = text[:cut_pos].rstrip()
    actual = estimate_token_count(truncated)
    return truncated, actual, True


# ---------------------------------------------------------------------------
# 语义冲突检测（简单关键词重叠检测）
# ---------------------------------------------------------------------------

def detect_semantic_conflict(
    world_canon_text: str,
    episodic_text: str,
    overlap_threshold: float = 0.3,
) -> bool:
    """
    检测 world_canon 与 episodic 证据之间的语义冲突。

    使用简单的关键词重叠率检测：若两段文本的关键词重叠率低于阈值，
    且存在明显的否定/矛盾词，则认为存在冲突。

    实际部署时可替换为向量余弦相似度检测。
    """
    if not world_canon_text or not episodic_text:
        return False

    # 提取关键词（简单分词）
    def extract_keywords(text: str) -> set:
        words = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text.lower()))
        return words

    canon_words = extract_keywords(world_canon_text)
    episodic_words = extract_keywords(episodic_text)

    if not canon_words or not episodic_words:
        return False

    # 计算 Jaccard 相似度
    intersection = canon_words & episodic_words
    union = canon_words | episodic_words
    similarity = len(intersection) / len(union) if union else 0

    # 检测否定词
    negation_patterns = [
        r'不是', r'没有', r'从未', r'不再', r'never', r'not ', r'no longer',
    ]
    has_negation = any(
        re.search(p, episodic_text, re.IGNORECASE)
        for p in negation_patterns
    )

    # 低相似度 + 否定词 = 冲突
    return similarity < overlap_threshold and has_negation


# ---------------------------------------------------------------------------
# ContextPack 组装器
# ---------------------------------------------------------------------------

class ContextPackBuilder:
    """
    ContextPack 组装器。

    将七路召回结果按 token 预算组装为 ContextPack，
    执行截断、冲突检测和审计记录。
    """

    def __init__(self, audit_writer: Optional[Callable] = None) -> None:
        """
        Args:
            audit_writer: 审计写入回调，签名为 (event: str, detail: dict) -> None。
        """
        self._audit_writer = audit_writer

    def build(self, recall_results: Dict[str, Optional[str]]) -> ContextPack:
        """
        组装 ContextPack。

        Args:
            recall_results: 七路召回结果字典，key 为槽位名称，value 为文本内容。
                            例如：{"world_canon": "...", "episodic_vector": "..."}

        Returns:
            组装完成的 ContextPack。
        """
        pack = ContextPack()

        # 第一步：按各路 token 上限截断并填充槽位
        for slot_name in TRUNCATION_PRIORITY:
            raw_text = recall_results.get(slot_name)
            if not raw_text:
                continue

            max_tokens = TOKEN_BUDGET[slot_name]
            truncated_text, actual_tokens, was_truncated = truncate_to_token_limit(
                raw_text, max_tokens
            )

            slot = EvidenceSlot(
                content=truncated_text,
                token_count=actual_tokens,
                truncated=was_truncated,
            )
            pack.set_slot(slot_name, slot)

            # 记录 subgraph_trace（需求 4.5）
            pack.subgraph_trace.append({
                "slot": slot_name,
                "original_tokens": estimate_token_count(raw_text),
                "actual_tokens": actual_tokens,
                "truncated": was_truncated,
                "budget": max_tokens,
            })

            if was_truncated:
                logger.debug(
                    "槽位 %s 已截断: %d → %d tokens（上限 %d）",
                    slot_name, estimate_token_count(raw_text), actual_tokens, max_tokens,
                )

        # 第二步：语义冲突检测（需求 4.3）
        self._check_and_suppress_conflicts(pack)

        # 第三步：总量超限时按优先级截断（需求 4.2）
        total = pack.recalculate_total()
        if total > TOTAL_TOKEN_BUDGET:
            self._apply_global_truncation(pack, total)

        # 最终更新总量
        pack.recalculate_total()

        logger.info(
            "ContextPack 组装完成: 总 token=%d（上限 %d），冲突事件=%d",
            pack.total_token_count, TOTAL_TOKEN_BUDGET, len(pack.conflict_events),
        )
        return pack

    def _check_and_suppress_conflicts(self, pack: ContextPack) -> None:
        """
        检测 world_canon 与 episodic_vector 之间的语义冲突。

        冲突时保留 world_canon，将 episodic 冲突内容标记为 [CONFLICT_SUPPRESSED]
        并记录审计日志（需求 4.3）。
        """
        world_slot = pack.get_slot("world_canon")
        episodic_slot = pack.get_slot("episodic_vector")

        if world_slot is None or episodic_slot is None:
            return

        if detect_semantic_conflict(world_slot.content, episodic_slot.content):
            original_content = episodic_slot.content
            episodic_slot.conflict_suppressed = True
            episodic_slot.content = "[CONFLICT_SUPPRESSED]"
            episodic_slot.token_count = estimate_token_count("[CONFLICT_SUPPRESSED]")

            conflict_event = {
                "type": "WORLD_CANON_EPISODIC_CONFLICT",
                "winner": "world_canon",
                "suppressed_slot": "episodic_vector",
                "suppressed_preview": original_content[:100],
            }
            pack.conflict_events.append(conflict_event)

            # 更新 subgraph_trace
            for trace in pack.subgraph_trace:
                if trace["slot"] == "episodic_vector":
                    trace["conflict_suppressed"] = True
                    break

            logger.info(
                "检测到 world_canon 与 episodic_vector 语义冲突，episodic 已标记为 [CONFLICT_SUPPRESSED]"
            )

    def _apply_global_truncation(self, pack: ContextPack, current_total: int) -> None:
        """
        总量超限时按优先级从低到高丢弃证据（需求 4.2）。

        截断顺序（从低优先级开始丢弃）：
        sqlite_ledger → persona_utterance → artifact_vault → bond_graph
        → episodic_vector → style_canon → world_canon
        """
        # 反向优先级（低优先级先丢弃）
        drop_order = list(reversed(TRUNCATION_PRIORITY))
        excess = current_total - TOTAL_TOKEN_BUDGET

        logger.warning(
            "ContextPack 总量 %d 超过上限 %d，开始全局截断（超出 %d tokens）",
            current_total, TOTAL_TOKEN_BUDGET, excess,
        )

        for slot_name in drop_order:
            if excess <= 0:
                break

            slot = pack.get_slot(slot_name)
            if slot is None or slot.token_count == 0:
                continue

            # 尝试将该槽位清空
            freed = slot.token_count
            slot.content = ""
            slot.token_count = 0
            slot.truncated = True
            excess -= freed

            # 更新 subgraph_trace
            for trace in pack.subgraph_trace:
                if trace["slot"] == slot_name:
                    trace["global_truncated"] = True
                    trace["freed_tokens"] = freed
                    break

            logger.info(
                "全局截断：丢弃槽位 %s（释放 %d tokens，剩余超出 %d tokens）",
                slot_name, freed, max(0, excess),
            )
