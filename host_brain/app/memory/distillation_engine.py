"""distillation_engine.py —— 情节记忆蒸馏引擎

从 EpisodicStore 批量蒸馏满足条件的情节记忆为语义记忆（需求 3.3）：
- 蒸馏条件：频次 ≥ 3 OR MILESTONE 级别（接入 MemoryGuard.check_distillation_eligibility）
- 蒸馏后清理 SESSION_OVERRIDE（接入 StateArbiter.clear_session_override）
- 蒸馏结果写入 Canon 提案队列（FACT_CANDIDATE / PREFERENCE_CANDIDATE）
- 支持批量蒸馏和单条蒸馏两种模式
- 蒸馏过程写入 audit_log（DISTILL_STARTED / DISTILL_DONE / DISTILL_SKIPPED）
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from ..schemas.memory_types import (
    CanonProposal,
    EpisodicRecord,
    EvidenceLevel,
    ProposalType,
    ProposerClass,
)
from .memory_guard import MemoryGuard, MemoryGuardError
from .episodic_store import EpisodicStore

if TYPE_CHECKING:
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    from .state_arbiter import StateArbiter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 蒸馏结果
# ---------------------------------------------------------------------------

@dataclass
class DistillationResult:
    """单条记录的蒸馏结果。"""
    record_id: str
    success: bool
    proposal: Optional[CanonProposal] = None
    """生成的 Canon 提案（成功时）"""
    skipped: bool = False
    skip_reason: Optional[str] = None
    """跳过原因（NOT_ELIGIBLE / GUARD_REJECTED / DREAM_BLOCKED）"""
    error: Optional[str] = None


@dataclass
class BatchDistillationReport:
    """批量蒸馏报告。"""
    total_candidates: int = 0
    distilled_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    proposals: List[CanonProposal] = field(default_factory=list)
    results: List[DistillationResult] = field(default_factory=list)
    duration_ms: int = 0
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))


# ---------------------------------------------------------------------------
# 蒸馏引擎
# ---------------------------------------------------------------------------

class DistillationEngine:
    """
    情节记忆蒸馏引擎。

    从 EpisodicStore 中提取满足蒸馏条件的记录，
    生成 Canon 提案并清理 SESSION_OVERRIDE。

    使用方式：
        engine = DistillationEngine(
            episodic_store=store,
            memory_guard=guard,
            state_arbiter=arbiter,
        )
        report = await engine.run_batch()
        # report.proposals 即为待提交的 Canon 提案列表
    """

    def __init__(
        self,
        episodic_store: EpisodicStore,
        memory_guard: Optional[MemoryGuard] = None,
        state_arbiter: Optional["StateArbiter"] = None,
        ledger: Optional["SQLiteLedger"] = None,
        audit_writer: Optional[Callable] = None,
        min_occurrence: int = 3,
        max_batch_size: int = 50,
    ) -> None:
        """
        Args:
            episodic_store: EpisodicStore 实例
            memory_guard: MemoryGuard 实例（None 时自动创建）
            state_arbiter: StateArbiter 实例（用于清理 SESSION_OVERRIDE）
            ledger: SQLiteLedger 实例（用于写入审计日志）
            audit_writer: 审计写入回调
            min_occurrence: 蒸馏最小出现频次（默认 3）
            max_batch_size: 单次批量蒸馏最大条数（默认 50）
        """
        self._store = episodic_store
        self._guard = memory_guard or MemoryGuard(audit_writer=audit_writer)
        self._arbiter = state_arbiter
        self._ledger = ledger
        self._audit_writer = audit_writer
        self._min_occurrence = min_occurrence
        self._max_batch_size = max_batch_size
        # 并发安全：防止定时任务和手动触发同时执行蒸馏
        self._batch_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 批量蒸馏
    # ------------------------------------------------------------------

    async def run_batch(
        self,
        session_ids_to_clear: Optional[List[str]] = None,
    ) -> BatchDistillationReport:
        """
        批量蒸馏：从 EpisodicStore 提取候选记录，生成 Canon 提案。

        Args:
            session_ids_to_clear: 蒸馏完成后需要清理 SESSION_OVERRIDE 的 session ID 列表

        Returns:
            BatchDistillationReport
        """
        t0 = int(time.time() * 1000)
        report = BatchDistillationReport()

        # 互斥：防止定时任务和手动触发并发执行
        if self._batch_lock.locked():
            logger.info("DistillationEngine: run_batch 跳过（另一蒸馏任务正在执行）")
            report.duration_ms = int(time.time() * 1000) - t0
            return report

        async with self._batch_lock:
            # 获取蒸馏候选
            candidates = self._store.query_distill_candidates(
                min_occurrence=self._min_occurrence,
                limit=self._max_batch_size,
            )
            report.total_candidates = len(candidates)

            self._write_audit_sync("DISTILL_STARTED", {
                "total_candidates": len(candidates),
                "min_occurrence": self._min_occurrence,
            })

            for record in candidates:
                result = self._distill_one(record)
                report.results.append(result)

                if result.success and result.proposal:
                    report.distilled_count += 1
                    report.proposals.append(result.proposal)
                elif result.skipped:
                    report.skipped_count += 1
                else:
                    report.failed_count += 1

            # 清理 SESSION_OVERRIDE
            if self._arbiter and session_ids_to_clear:
                for sid in session_ids_to_clear:
                    try:
                        self._arbiter.clear_session_override(sid)
                        logger.debug("DistillationEngine: 已清理 SESSION_OVERRIDE（session=%s）", sid)
                    except Exception as exc:
                        logger.warning("DistillationEngine: 清理 SESSION_OVERRIDE 失败（session=%s）: %s", sid, exc)

            report.duration_ms = int(time.time() * 1000) - t0

            self._write_audit_sync("DISTILL_DONE", {
                "total_candidates": report.total_candidates,
                "distilled_count": report.distilled_count,
                "skipped_count": report.skipped_count,
                "failed_count": report.failed_count,
                "duration_ms": report.duration_ms,
            })

            logger.info(
            "DistillationEngine: 批量蒸馏完成（候选=%d, 成功=%d, 跳过=%d, 失败=%d, 耗时=%dms）",
                report.total_candidates, report.distilled_count,
                report.skipped_count, report.failed_count, report.duration_ms,
            )
            return report

    # ------------------------------------------------------------------
    # 单条蒸馏
    # ------------------------------------------------------------------

    def _distill_one(self, record: EpisodicRecord) -> DistillationResult:
        """蒸馏单条 EpisodicRecord 为 Canon 提案。"""
        record_id = record.source_hash or MemoryGuard.compute_source_hash(record.content)

        # 拦截 NARRATIVE_ONLY（dream 内容）
        if record.evidence_level == EvidenceLevel.NARRATIVE_ONLY:
            return DistillationResult(
                record_id=record_id,
                success=False,
                skipped=True,
                skip_reason="DREAM_BLOCKED",
            )

        # 确定提案类型
        proposal_type = self._infer_proposal_type(record)

        # 构建 Canon 提案
        proposal = CanonProposal(
            proposal_type=proposal_type,
            content=record.content,
            source_hash=record_id,
            proposer_class=ProposerClass.SCHEDULER,
            evidence_level=record.evidence_level,
            metadata={
                "session_id": record.session_id,
                "turn_count": record.turn_count,
                "emotion_tags": record.emotion_tags,
                "fact_tags": record.fact_tags,
                "preference_tags": record.preference_tags,
                "distilled_at_ms": int(time.time() * 1000),
            },
        )

        # MemoryGuard 校验（dream 内容拦截 + 幂等保护）
        try:
            self._guard.check_canon_proposal(proposal)
            self._guard.check_dream_canon_proposal(proposal)
        except MemoryGuardError as exc:
            return DistillationResult(
                record_id=record_id,
                success=False,
                skipped=True,
                skip_reason="GUARD_REJECTED",
                error=exc.reason,
            )

        # 标记已处理
        self._guard.mark_proposal_processed(record_id)

        return DistillationResult(
            record_id=record_id,
            success=True,
            proposal=proposal,
        )

    # ------------------------------------------------------------------
    # Ref #111: 单条强制蒸馏（跳过频次门槛）
    # ------------------------------------------------------------------

    async def distill_single(
        self,
        record: EpisodicRecord,
        *,
        force: bool = False,
        clear_session_override: bool = False,
    ) -> DistillationResult:
        """
        单条记忆强制蒸馏（Ref #111）。

        与 run_batch() 不同，此方法跳过频次门槛（min_occurrence），
        直接对指定记录执行蒸馏，适用于：
        - MILESTONE 级别记录的即时蒸馏
        - 用户手动触发的单条蒸馏
        - 测试/调试场景

        Args:
            record: 要蒸馏的 EpisodicRecord
            force: True 时跳过 MemoryGuard 的幂等保护（允许重复蒸馏）
            clear_session_override: True 时蒸馏完成后清理该 session 的 SESSION_OVERRIDE

        Returns:
            DistillationResult
        """
        record_id = record.source_hash or MemoryGuard.compute_source_hash(record.content)

        self._write_audit_sync("DISTILL_SINGLE_STARTED", {
            "record_id": record_id,
            "session_id": record.session_id,
            "evidence_level": record.evidence_level.value if record.evidence_level else None,
            "force": force,
        })

        # 拦截 NARRATIVE_ONLY（dream 内容）—— 即使 force=True 也不允许
        if record.evidence_level == EvidenceLevel.NARRATIVE_ONLY:
            result = DistillationResult(
                record_id=record_id,
                success=False,
                skipped=True,
                skip_reason="DREAM_BLOCKED",
            )
            self._write_audit_sync("DISTILL_SKIPPED", {
                "record_id": record_id,
                "reason": "DREAM_BLOCKED",
            })
            return result

        # 确定提案类型
        proposal_type = self._infer_proposal_type(record)

        # 构建 Canon 提案
        proposal = CanonProposal(
            proposal_type=proposal_type,
            content=record.content,
            source_hash=record_id,
            proposer_class=ProposerClass.SCHEDULER,
            evidence_level=record.evidence_level,
            metadata={
                "session_id": record.session_id,
                "turn_count": record.turn_count,
                "emotion_tags": record.emotion_tags,
                "fact_tags": record.fact_tags,
                "preference_tags": record.preference_tags,
                "distilled_at_ms": int(time.time() * 1000),
                "forced": force,
                "single_distill": True,
            },
        )

        # MemoryGuard 校验
        try:
            if not force:
                # 正常模式：执行幂等保护
                self._guard.check_canon_proposal(proposal)
            self._guard.check_dream_canon_proposal(proposal)
        except MemoryGuardError as exc:
            result = DistillationResult(
                record_id=record_id,
                success=False,
                skipped=True,
                skip_reason="GUARD_REJECTED",
                error=exc.reason,
            )
            self._write_audit_sync("DISTILL_SKIPPED", {
                "record_id": record_id,
                "reason": "GUARD_REJECTED",
                "error": exc.reason,
            })
            return result

        # 标记已处理（force 模式下也标记，防止后续批量蒸馏重复处理）
        self._guard.mark_proposal_processed(record_id)

        # 清理 SESSION_OVERRIDE
        if clear_session_override and self._arbiter and record.session_id:
            try:
                self._arbiter.clear_session_override(record.session_id)
                logger.debug(
                    "distill_single: 已清理 SESSION_OVERRIDE（session=%s）",
                    record.session_id,
                )
            except Exception as exc:
                logger.warning(
                    "distill_single: 清理 SESSION_OVERRIDE 失败（session=%s）: %s",
                    record.session_id, exc,
                )

        result = DistillationResult(
            record_id=record_id,
            success=True,
            proposal=proposal,
        )

        self._write_audit_sync("DISTILL_DONE", {
            "record_id": record_id,
            "proposal_type": proposal_type.value,
            "forced": force,
        })

        logger.info(
            "distill_single: 蒸馏完成（record_id=%s, type=%s, forced=%s）",
            record_id, proposal_type.value, force,
        )
        return result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_proposal_type(record: EpisodicRecord) -> ProposalType:
        """根据记录的标签推断 Canon 提案类型。"""
        if record.preference_tags:
            return ProposalType.PREFERENCE_CANDIDATE
        if record.fact_tags:
            return ProposalType.FACT_CANDIDATE
        if record.emotion_tags:
            return ProposalType.FACT_CANDIDATE  # 情绪标签归入事实候选
        return ProposalType.FACT_CANDIDATE

    def _write_audit_sync(self, event: str, detail: Dict[str, Any]) -> None:
        """同步写入审计日志（不阻塞主流程）。"""
        if self._ledger is None and self._audit_writer is None:
            return
        try:
            if self._ledger is not None:
                # 使用 SQLiteLedger 实际存在的 log_error 方法写入审计日志
                self._ledger.log_error(
                    request_id="distillation_engine",
                    stage=event,
                    reason_code=event,
                    detail=json.dumps(detail, ensure_ascii=False),
                )
            elif self._audit_writer is not None:
                self._audit_writer(event, detail)
        except Exception as exc:
            logger.warning("DistillationEngine: 审计写入失败: %s", exc)
