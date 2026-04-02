"""长期记忆蒸馏强化引擎（M13-B）。

DevSpec §26：长期记忆蒸馏强化。

职责：
1. 从 EpisodicStore 召回 top-k 记忆，调用 MemoryDistillationEngine 生成语义记忆
2. 将语义记忆自动生成 World Canon 提案（status=pending，等待人工/自动审核）
3. 提供 run_distillation_cycle() 单入口，可被 NatsumeScheduler 调度
4. 全程写入审计链（distillation_cycle_log）

设计约束：
- 蒸馏结果不直接写入 Canon SSOT，只生成 pending 提案（FR-MEM-DISTILL-001）
- 每次蒸馏循环最多生成 MAX_PROPOSALS_PER_CYCLE 个提案（防止爆炸）
- 幂等：相同 source_hash 的语义记忆不重复生成提案
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id, now_ms
from host_brain.memory.distillation import MemoryDistillationEngine, LlmSummaryClient
from host_brain.memory.canon_world_store import WorldCanonStore

_logger = logging.getLogger("Natsume.long_term_distiller")

# 每次蒸馏循环最多生成的 Canon 提案数（FR-MEM-DISTILL-002）
MAX_PROPOSALS_PER_CYCLE = 5

# 蒸馏查询主题列表（可配置）
DEFAULT_DISTILL_TOPICS: list[str] = [
    "用户日常习惯与偏好",
    "重要事件与里程碑",
    "情感状态与心理变化",
    "用户目标与计划",
    "关系与社交互动",
    # M28-J（OPT-29）：情感关系蒸馏主题
    "情感关系史",
]

# M28-J：情感关系蒸馏 prompt 模板
EMOTION_RELATIONSHIP_PROMPT_TEMPLATE = """
请根据以下对话记忆片段，蒸馏出用户与 AI 之间的情感关系演变摘要。
重点关注：
1. 情感关系的发展阶段（陌生→相识→朋友→挚友）
2. 关键情感转折点（第一次深度对话、情感支持事件等）
3. 用户对 AI 的情感态度变化
4. AI 对用户情感需求的响应模式

记忆片段：
{memories}

请输出简洁的情感关系演变摘要（200字以内）：
"""

# Canon 提案目标文档 ID（蒸馏结果写入的 World Canon 文档）
DISTILL_TARGET_DOC_ID = "wc-distilled-long-term-memory"

S_DISTILL_CYCLE = "S_DISTILL_CYCLE"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DistillationCycleResult:
    """一次蒸馏循环的结果摘要。"""
    cycle_id: str
    topics_processed: int
    semantic_memories_created: int
    proposals_submitted: int
    proposals_skipped: int
    errors: list[str]
    ts_ms: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "topics_processed": self.topics_processed,
            "semantic_memories_created": self.semantic_memories_created,
            "proposals_submitted": self.proposals_submitted,
            "proposals_skipped": self.proposals_skipped,
            "errors": list(self.errors),
            "ts_ms": self.ts_ms,
            "detail": self.detail,
        }


@dataclass
class ProposalRecord:
    """Canon 提案记录（蒸馏生成）。"""
    proposal_id: str
    semantic_id: str
    topic: str
    summary: str
    source_count: int
    status: str  # submitted / skipped / error
    detail: str


# ---------------------------------------------------------------------------
# LongTermMemoryDistiller
# ---------------------------------------------------------------------------

class LongTermMemoryDistiller:
    """长期记忆蒸馏强化引擎。

    职责：
    1. 按主题列表依次召回 top-k 记忆并蒸馏为语义记忆
    2. 将语义记忆自动生成 World Canon pending 提案
    3. 写入 distillation_cycle_log 审计链
    4. 提供 run_distillation_cycle() 单入口（可被调度器调用）
    """

    def __init__(
        self,
        *,
        distillation_engine: MemoryDistillationEngine,
        canon_store: WorldCanonStore,
        ledger: SQLiteLedger | None = None,
        topics: list[str] | None = None,
        top_k: int = 5,
        max_proposals_per_cycle: int = MAX_PROPOSALS_PER_CYCLE,
        llm_client: LlmSummaryClient | None = None,
        target_doc_id: str = DISTILL_TARGET_DOC_ID,
    ) -> None:
        self._engine = distillation_engine
        self._canon = canon_store
        self._ledger = ledger
        self._topics = list(topics or DEFAULT_DISTILL_TOPICS)
        self._top_k = max(1, min(top_k, 20))
        self._max_proposals = max(1, min(max_proposals_per_cycle, MAX_PROPOSALS_PER_CYCLE))
        self._llm_client = llm_client
        self._target_doc_id = str(target_doc_id).strip() or DISTILL_TARGET_DOC_ID

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def run_distillation_cycle(
        self,
        *,
        request_id: str | None = None,
        ts_ms: int | None = None,
        topics: list[str] | None = None,
    ) -> DistillationCycleResult:
        """执行一次完整的蒸馏循环。

        1. 按主题列表依次蒸馏
        2. 将新生成的语义记忆提交为 Canon pending 提案
        3. 写入审计链

        Args:
            request_id: 关联请求 ID（可选）。
            ts_ms: 时间戳（毫秒，可选）。
            topics: 覆盖默认主题列表（可选）。

        Returns:
            DistillationCycleResult 摘要。
        """
        req_id = request_id or new_request_id()
        ts = int(ts_ms if ts_ms is not None else now_ms())
        cycle_id = f"distill_cycle_{req_id}"
        active_topics = list(topics or self._topics)

        _logger.info(
            "Distillation cycle started: cycle_id=%s topics=%d",
            cycle_id, len(active_topics),
            extra={"request_id": req_id},
        )

        topics_processed = 0
        semantic_created = 0
        proposals_submitted = 0
        proposals_skipped = 0
        errors: list[str] = []
        proposal_records: list[ProposalRecord] = []

        for topic in active_topics:
            if proposals_submitted >= self._max_proposals:
                _logger.info(
                    "Distillation cycle: max proposals reached (%d), stopping",
                    self._max_proposals,
                    extra={"request_id": req_id},
                )
                break

            try:
                distill_result = self._engine.distill_top_k(
                    query_text=topic,
                    top_k=self._top_k,
                    tags=["long_term_distill", topic[:20]],
                    operator_node_id="long_term_distiller",
                    ts_ms=ts,
                    llm_client=self._llm_client,
                )
                topics_processed += 1

                if not distill_result.success:
                    _logger.debug(
                        "Distillation skipped for topic=%r: %s",
                        topic, distill_result.detail,
                        extra={"request_id": req_id},
                    )
                    continue

                if not distill_result.skipped:
                    semantic_created += 1

                # 生成 Canon 提案
                proposal_rec = self._submit_canon_proposal(
                    semantic_id=distill_result.semantic_id,  # type: ignore[arg-type]
                    topic=topic,
                    source_count=distill_result.source_count,
                    request_id=req_id,
                    ts_ms=ts,
                )
                proposal_records.append(proposal_rec)

                if proposal_rec.status == "submitted":
                    proposals_submitted += 1
                elif proposal_rec.status == "skipped":
                    proposals_skipped += 1
                else:
                    errors.append(f"topic={topic!r}: {proposal_rec.detail}")

            except Exception as exc:
                err_msg = f"topic={topic!r}: {exc}"
                _logger.error(
                    "Distillation cycle error: %s",
                    err_msg,
                    exc_info=True,
                    extra={"request_id": req_id},
                )
                errors.append(err_msg)
                topics_processed += 1

        detail = (
            f"cycle_id={cycle_id} topics={topics_processed} "
            f"semantic_created={semantic_created} "
            f"proposals_submitted={proposals_submitted} "
            f"proposals_skipped={proposals_skipped} "
            f"errors={len(errors)}"
        )

        result = DistillationCycleResult(
            cycle_id=cycle_id,
            topics_processed=topics_processed,
            semantic_memories_created=semantic_created,
            proposals_submitted=proposals_submitted,
            proposals_skipped=proposals_skipped,
            errors=errors,
            ts_ms=ts,
            detail=detail,
        )

        # 写入审计链
        self._log_cycle(result, req_id)

        _logger.info(
            "Distillation cycle completed: %s",
            detail,
            extra={"request_id": req_id},
        )
        return result

    def get_pending_proposals(self) -> list[dict[str, Any]]:
        """获取所有 pending 状态的 Canon 提案（蒸馏生成）。"""
        if self._ledger is None:
            return []
        # 修复：使用正确的方法名 list_proposals（list_canon_proposals 不存在）
        if not hasattr(self._ledger, "list_proposals"):
            return []
        try:
            proposals, _ = self._ledger.list_proposals(
                status="pending",
                limit=50,
                offset=0,
            )
            # 过滤出由 long_term_distiller 提交的提案
            return [
                p for p in proposals
                if str(p.get("proposer_node_id", "")) == "long_term_distiller"
            ]
        except Exception as exc:
            _logger.warning("get_pending_proposals failed: %s", exc)
            return []

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _submit_canon_proposal(
        self,
        *,
        semantic_id: str,
        topic: str,
        source_count: int,
        request_id: str,
        ts_ms: int,
    ) -> ProposalRecord:
        """将语义记忆提交为 World Canon pending 提案。"""
        proposal_id = f"distill_prop_{semantic_id[:16]}_{uuid.uuid4().hex[:8]}"

        # 从 ledger 获取语义记忆内容
        summary_text = self._get_semantic_summary(semantic_id)
        if summary_text is None:
            return ProposalRecord(
                proposal_id=proposal_id,
                semantic_id=semantic_id,
                topic=topic,
                summary="",
                source_count=source_count,
                status="skipped",
                detail=f"semantic memory not found: {semantic_id}",
            )

        # 检查是否已有相同 semantic_id 的 pending 提案（幂等）
        if self._proposal_exists_for_semantic(semantic_id):
            return ProposalRecord(
                proposal_id=proposal_id,
                semantic_id=semantic_id,
                topic=topic,
                summary=summary_text,
                source_count=source_count,
                status="skipped",
                detail=f"proposal already exists for semantic_id={semantic_id}",
            )

        # 构建提案正文（不含完成性表述）
        proposed_body = self._build_proposal_body(
            topic=topic,
            summary=summary_text,
            semantic_id=semantic_id,
            source_count=source_count,
        )

        try:
            self._canon.submit_proposal(
                proposal_id=proposal_id,
                target_doc_id=self._target_doc_id,
                proposed_title=f"长期记忆蒸馏：{topic[:30]}",
                proposed_tags=["long_term_distill", "auto_generated", topic[:20]],
                proposed_body=proposed_body,
                proposer_node_id="long_term_distiller",
                source_intent_id=request_id,
                summary=f"蒸馏主题={topic!r} semantic_id={semantic_id} source_count={source_count}",
                ts_ms=ts_ms,
            )
            return ProposalRecord(
                proposal_id=proposal_id,
                semantic_id=semantic_id,
                topic=topic,
                summary=summary_text,
                source_count=source_count,
                status="submitted",
                detail=f"proposal submitted: {proposal_id}",
            )
        except Exception as exc:
            _logger.error(
                "Canon proposal submission failed: semantic_id=%s error=%s",
                semantic_id, exc,
                extra={"request_id": request_id},
            )
            return ProposalRecord(
                proposal_id=proposal_id,
                semantic_id=semantic_id,
                topic=topic,
                summary=summary_text,
                source_count=source_count,
                status="error",
                detail=str(exc),
            )

    def _get_semantic_summary(self, semantic_id: str) -> str | None:
        """从 ledger 获取语义记忆摘要文本（按 semantic_id 精确查询，O(1)）。"""
        if self._ledger is None:
            return f"[语义记忆 {semantic_id}]"
        # 优先使用精确查询接口（避免全量扫描）
        if hasattr(self._ledger, "get_semantic_memory_by_id"):
            try:
                mem = self._ledger.get_semantic_memory_by_id(semantic_id)
                if mem is not None:
                    return str(mem.get("summary", ""))
                return f"[语义记忆 {semantic_id}]"
            except Exception:
                return f"[语义记忆 {semantic_id}]"
        # 降级：旧版 ledger 不支持精确查询时，分页扫描（最多 200 条）
        if not hasattr(self._ledger, "list_semantic_memories"):
            return f"[语义记忆 {semantic_id}]"
        try:
            memories, _ = self._ledger.list_semantic_memories(limit=200, offset=0)
            for mem in memories:
                if mem.get("semantic_id") == semantic_id:
                    return str(mem.get("summary", ""))
            return f"[语义记忆 {semantic_id}]"
        except Exception:
            return f"[语义记忆 {semantic_id}]"

    def _proposal_exists_for_semantic(self, semantic_id: str) -> bool:
        """检查是否已有相同 semantic_id 的 pending 提案（幂等保护）。

        优先通过 proposal_id 前缀精确匹配，避免全量扫描所有 pending 提案。
        proposal_id 格式：distill_prop_{semantic_id[:16]}_{uuid8}
        """
        if self._ledger is None:
            return False
        if not hasattr(self._ledger, "list_proposals"):
            return False
        # 构造前缀，只拉取匹配该前缀的提案（若 ledger 支持 prefix 过滤则更优）
        id_prefix = f"distill_prop_{semantic_id[:16]}_"
        try:
            proposals, _ = self._ledger.list_proposals(
                status="pending",
                limit=100,
                offset=0,
            )
            for p in proposals:
                proposal_id = str(p.get("proposal_id", ""))
                if proposal_id.startswith(id_prefix):
                    return True
                # 备用：summary 中精确匹配完整 semantic_id
                if f"semantic_id={semantic_id}" in str(p.get("summary", "")):
                    return True
            return False
        except Exception:
            return False

    def _build_proposal_body(
        self,
        *,
        topic: str,
        summary: str,
        semantic_id: str,
        source_count: int,
    ) -> str:
        """构建 Canon 提案正文（不含完成性表述）。

        M28-J（OPT-29）：情感关系史主题额外输出 emotional_relationship_summary 字段。
        """
        # M28-J：情感关系史主题特殊处理
        emotion_section = ""
        if topic == "情感关系史":
            emotion_section = (
                f"\n**emotional_relationship_summary**: {summary}\n"
            )

        return (
            f"## 长期记忆蒸馏提案\n\n"
            f"**主题**: {topic}\n\n"
            f"**语义记忆 ID**: {semantic_id}\n\n"
            f"**来源记忆数量**: {source_count}\n\n"
            f"**摘要内容**:\n\n{summary}\n"
            f"{emotion_section}\n"
            f"---\n"
            f"*本提案由长期记忆蒸馏引擎自动生成，需人工审核后方可应用。*"
        )

    def _log_cycle(
        self,
        result: DistillationCycleResult,
        request_id: str,
    ) -> None:
        """写入 distillation_cycle_log 审计链。"""
        if self._ledger is None:
            return
        record = {
            "request_id": request_id,
            "cycle_id": result.cycle_id,
            "topics_processed": result.topics_processed,
            "semantic_memories_created": result.semantic_memories_created,
            "proposals_submitted": result.proposals_submitted,
            "proposals_skipped": result.proposals_skipped,
            "errors": result.errors,
            "stage": S_DISTILL_CYCLE,
            "detail": result.detail,
            "ts_ms": result.ts_ms,
        }
        if hasattr(self._ledger, "log_distillation_cycle"):
            self._ledger.log_distillation_cycle(record)
        elif hasattr(self._ledger, "log_system_event"):
            self._ledger.log_system_event(
                request_id=request_id,
                event_type="DISTILLATION_CYCLE_COMPLETED",
                payload=record,
            )


# ---------------------------------------------------------------------------
# 调度器任务工厂
# ---------------------------------------------------------------------------

def make_distillation_task_config() -> "SchedulerTaskConfig":
    """创建蒸馏调度任务配置（每日 03:00 CRON）。

    Returns:
        SchedulerTaskConfig 实例，可直接注册到 SchedulerTaskRegistry。
    """
    from host_brain.scheduler.task_registry import (
        SchedulerTaskConfig,
        TaskPriority,
        TriggerMode,
    )
    return SchedulerTaskConfig(
        task_id="long_term_memory_distillation",
        trigger_mode=TriggerMode.CRON,
        trigger_config={"hour": 3, "minute": 0},
        intent_template={
            "skill_name": "long_term_memory_distillation",
            "args": {"top_k": 5, "max_proposals": MAX_PROPOSALS_PER_CYCLE},
        },
        priority=TaskPriority.LOW,
        mode_allowlist=["normal", "sleep"],
        enabled=True,
        max_consecutive_failures=3,
        description="每日 03:00 执行长期记忆蒸馏，将 EpisodicMemory 蒸馏为 Canon 提案（M13-B）",
    )
