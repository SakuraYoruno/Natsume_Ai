from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

from ..schemas.recall_pack import (
    RECALL_PACK_TOKEN_BUDGET,
    RECALL_SLOT_PRIORITY,
    RECALL_SLOT_TOKEN_BUDGET,
    RecallPack,
    RecallSlot,
)

logger = logging.getLogger(__name__)


def estimate_token_count(text: str) -> int:
    """Cheap token estimate without external tokenizer dependency."""
    if not text:
        return 0
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    non_chinese = re.sub(r"[\u4e00-\u9fff]", " ", text)
    english_tokens = len(non_chinese.split())
    return chinese_chars + english_tokens


def truncate_to_token_limit(text: str, max_tokens: int) -> tuple[str, int, bool]:
    """Trim text to the requested token budget."""
    total = estimate_token_count(text)
    if total <= max_tokens:
        return text, total, False

    ratio = max_tokens / max(total, 1)
    cut_pos = int(len(text) * ratio)
    truncated = text[:cut_pos].rstrip()
    actual = estimate_token_count(truncated)
    return truncated, actual, True


def detect_semantic_conflict(
    world_canon_text: str,
    episodic_text: str,
    *,
    overlap_threshold: float = 0.3,
) -> bool:
    """Detect a simple world-canon vs episodic contradiction."""
    if not world_canon_text or not episodic_text:
        return False

    def _extract_keywords(text: str) -> set[str]:
        return set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", text.lower()))

    canon_words = _extract_keywords(world_canon_text)
    episodic_words = _extract_keywords(episodic_text)
    if not canon_words or not episodic_words:
        return False

    intersection = canon_words & episodic_words
    union = canon_words | episodic_words
    similarity = len(intersection) / len(union) if union else 0.0
    negation_patterns = [
        r"不是",
        r"没有",
        r"从未",
        r"不再",
        r"never",
        r"not ",
        r"no longer",
    ]
    has_negation = any(re.search(pattern, episodic_text, re.IGNORECASE) for pattern in negation_patterns)
    return similarity < overlap_threshold and has_negation


class RecallPackBuilder:
    """Assemble recall results into an internal recall pack with budget control."""

    def __init__(self, audit_writer: Optional[Callable[[str, dict[str, Any]], None]] = None) -> None:
        self._audit_writer = audit_writer

    def build(
        self,
        recall_results: dict[str, Optional[str]],
        task_type: Optional[str] = None,
        slot_budget_overrides: dict[str, int] | None = None,
        total_budget_override: int | None = None,
    ) -> RecallPack:
        pack = RecallPack()
        effective_total_budget = int(total_budget_override) if total_budget_override else RECALL_PACK_TOKEN_BUDGET
        resolved_slot_budget = {
            **RECALL_SLOT_TOKEN_BUDGET,
            **{
                str(slot_name): max(50, int(token_budget))
                for slot_name, token_budget in dict(slot_budget_overrides or {}).items()
                if str(slot_name) in RECALL_SLOT_TOKEN_BUDGET
            },
        }

        for slot_name in RECALL_SLOT_PRIORITY:
            raw_text = str(recall_results.get(slot_name) or "").strip()
            if not raw_text:
                continue

            max_tokens = resolved_slot_budget[slot_name]
            truncated_text, actual_tokens, was_truncated = truncate_to_token_limit(raw_text, max_tokens)
            pack.set_slot(
                slot_name,
                RecallSlot(
                    content=truncated_text,
                    token_count=actual_tokens,
                    truncated=was_truncated,
                ),
            )
            pack.subgraph_trace.append(
                {
                    "slot": slot_name,
                    "original_tokens": estimate_token_count(raw_text),
                    "actual_tokens": actual_tokens,
                    "truncated": was_truncated,
                    "budget": max_tokens,
                }
            )

        self._check_and_suppress_conflicts(pack)
        if task_type:
            self._check_style_task_conflict(pack, task_type)

        total = pack.recalculate_total()
        if total > effective_total_budget:
            self._apply_global_truncation(pack, total, effective_total_budget)
        pack.recalculate_total()

        logger.info(
            "RecallPack assembled: total_tokens=%d budget=%d conflict_events=%d",
            pack.total_token_count,
            effective_total_budget,
            len(pack.conflict_events),
        )
        return pack

    async def build_with_semantic_recall(
        self,
        recall_results: dict[str, Optional[str]],
        *,
        query: str,
        semantic_recaller: Optional[Any] = None,
        task_type: Optional[str] = None,
        semantic_recall_max_tokens: int = 600,
    ) -> RecallPack:
        if recall_results.get("episodic_vector"):
            return self.build(recall_results, task_type=task_type)

        if semantic_recaller is not None and query:
            try:
                recall_result = await semantic_recaller.recall(query=query)
                filtered_count = int(getattr(recall_result, "filtered_count", 0) or 0)
                degraded = bool(getattr(recall_result, "degraded", False))
                if filtered_count > 0:
                    episodic_text = recall_result.to_context_text(max_tokens=semantic_recall_max_tokens)
                    recall_results = dict(recall_results)
                    recall_results["episodic_vector"] = episodic_text
                    logger.debug(
                        "Semantic recall filled episodic_vector: hits=%d degraded=%s",
                        filtered_count,
                        degraded,
                    )
                elif degraded:
                    logger.warning(
                        "Semantic recall degraded: reason=%s",
                        str(getattr(recall_result, "degraded_reason", "") or "UNKNOWN"),
                    )
            except Exception as exc:
                logger.warning("Semantic recall failed without breaking main path: %s", exc)

        return self.build(recall_results, task_type=task_type)

    def _check_style_task_conflict(self, pack: RecallPack, task_type: str) -> None:
        style_slot = pack.get_slot("style_canon")
        if style_slot is None or not style_slot.content:
            return

        if str(task_type).strip().lower() not in {"code", "translate", "reasoning"}:
            return

        if not style_slot.content.startswith("[REFERENCE_ONLY]"):
            style_slot.content = f"[REFERENCE_ONLY]\n{style_slot.content}"
            style_slot.token_count = estimate_token_count(style_slot.content)

        for trace in pack.subgraph_trace:
            if trace.get("slot") == "style_canon":
                trace["reference_only"] = True
                trace["reference_reason"] = f"task_type={task_type}"
                break

        self._emit_audit(
            "style_task_conflict",
            {
                "task_type": task_type,
                "slot": "style_canon",
            },
        )
        logger.info("style_canon downgraded to reference-only for task_type=%s", task_type)

    def _check_and_suppress_conflicts(self, pack: RecallPack) -> None:
        world_slot = pack.get_slot("world_canon")
        episodic_slot = pack.get_slot("episodic_vector")
        if world_slot is None or episodic_slot is None:
            return

        if not detect_semantic_conflict(world_slot.content, episodic_slot.content):
            return

        original_content = episodic_slot.content
        episodic_slot.conflict_suppressed = True
        episodic_slot.content = "[CONFLICT_SUPPRESSED]"
        episodic_slot.token_count = estimate_token_count(episodic_slot.content)

        conflict_event = {
            "type": "WORLD_CANON_EPISODIC_CONFLICT",
            "winner": "world_canon",
            "suppressed_slot": "episodic_vector",
            "suppressed_preview": original_content[:100],
        }
        pack.conflict_events.append(conflict_event)
        for trace in pack.subgraph_trace:
            if trace.get("slot") == "episodic_vector":
                trace["conflict_suppressed"] = True
                break

        self._emit_audit("recall_conflict_suppressed", conflict_event)
        logger.info("world_canon and episodic_vector conflicted; episodic slot suppressed")

    def _apply_global_truncation(self, pack: RecallPack, current_total: int, total_budget: int | None = None) -> None:
        effective_budget = total_budget or RECALL_PACK_TOKEN_BUDGET
        drop_order = list(reversed(RECALL_SLOT_PRIORITY))
        excess = current_total - effective_budget

        logger.warning(
            "RecallPack exceeded total budget: total=%d budget=%d excess=%d",
            current_total,
            effective_budget,
            excess,
        )

        for slot_name in drop_order:
            if excess <= 0:
                break

            slot = pack.get_slot(slot_name)
            if slot is None or slot.token_count <= 0:
                continue

            freed = slot.token_count
            slot.content = ""
            slot.token_count = 0
            slot.truncated = True
            excess -= freed
            for trace in pack.subgraph_trace:
                if trace.get("slot") == slot_name:
                    trace["global_truncated"] = True
                    trace["freed_tokens"] = freed
                    break

            self._emit_audit(
                "recall_global_truncation",
                {
                    "slot": slot_name,
                    "freed_tokens": freed,
                    "remaining_excess": max(0, excess),
                },
            )
            logger.info(
                "Dropped recall slot during global truncation: slot=%s freed_tokens=%d remaining_excess=%d",
                slot_name,
                freed,
                max(0, excess),
            )

    def _emit_audit(self, event: str, detail: dict[str, Any]) -> None:
        if self._audit_writer is None:
            return
        try:
            self._audit_writer(event, detail)
        except Exception:
            logger.debug("RecallPackBuilder audit writer failed", exc_info=True)
