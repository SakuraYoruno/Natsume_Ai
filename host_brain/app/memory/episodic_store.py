"""episodic_store.py —— 情节记忆存储

负责 EpisodicRecord 的写入、查询和幂等保护（需求 3.1 ~ 3.5）：
- 写入前通过 MemoryGuard.check_episodic_write() 校验
- source_hash 幂等保护（相同内容拒绝重复写入）
- 按 session_id 查询最近 N 条记录
- 按 evidence_level 过滤查询
- 支持 SQLiteLedger 持久化（可选）
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from ..schemas.memory_types import EpisodicRecord, EvidenceLevel
from .memory_guard import MemoryGuard, MemoryGuardError

if TYPE_CHECKING:
    from host_brain.audit.sqlite_ledger import SQLiteLedger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 写入结果
# ---------------------------------------------------------------------------

@dataclass
class EpisodicWriteResult:
    """EpisodicStore.write() 的返回值。"""
    success: bool
    record_id: Optional[str] = None
    """写入成功时的记录 ID（source_hash 前 16 位）"""
    skipped: bool = False
    """是否因幂等保护跳过写入"""
    skip_reason: Optional[str] = None
    """跳过原因（DUPLICATE_HASH / GUARD_REJECTED）"""
    guard_error: Optional[str] = None
    """MemoryGuard 拒绝原因"""
    distill_eligible: bool = False
    """是否满足蒸馏资格（频次 ≥ 3 或 MILESTONE）"""
    occurrence_count: int = 0
    """该内容的历史出现次数"""


# ---------------------------------------------------------------------------
# EpisodicStore
# ---------------------------------------------------------------------------

class EpisodicStore:
    """
    情节记忆存储。

    所有写入操作必须先通过 MemoryGuard 校验，
    通过后写入内存存储（可选同步到 SQLiteLedger）。
    """

    def __init__(
        self,
        memory_guard: Optional[MemoryGuard] = None,
        ledger: Optional["SQLiteLedger"] = None,
        audit_writer: Optional[Callable] = None,
        max_in_memory: int = 1000,
        min_occurrence: int = 3,
    ) -> None:
        """
        Args:
            memory_guard: MemoryGuard 实例（None 时自动创建）
            ledger: SQLiteLedger 实例（None 时仅内存存储）
            audit_writer: 审计写入回调
            max_in_memory: 内存中最多保留的记录数（LRU 淘汰）
            min_occurrence: 蒸馏最小出现频次（默认 3，与 DistillationEngine 保持一致）
        """
        self._guard = memory_guard or MemoryGuard(audit_writer=audit_writer)
        self._ledger = ledger
        self._audit_writer = audit_writer
        self._max_in_memory = max_in_memory
        self._min_occurrence = min_occurrence

        # 内存存储：record_id → EpisodicRecord
        self._records: Dict[str, EpisodicRecord] = {}
        # 写入顺序（用于 LRU 淘汰）
        self._write_order: List[str] = []
        # 内容哈希 → 出现次数（用于蒸馏资格判断）
        self._occurrence_counts: Dict[str, int] = {}
        # 已处理的 source_hash 集合（幂等保护）
        self._processed_hashes: set[str] = set()

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def write(self, record: EpisodicRecord) -> EpisodicWriteResult:
        """
        写入情节记忆记录。

        流程：
        1. 计算 source_hash（若未提供）
        2. 幂等保护：相同 source_hash 拒绝重复写入
        3. MemoryGuard.check_episodic_write() 校验
        4. 写入内存存储（+ 可选 SQLiteLedger）
        5. 更新出现次数，判断蒸馏资格

        Returns:
            EpisodicWriteResult
        """
        # 步骤 1：计算 source_hash
        if not record.source_hash:
            record.source_hash = MemoryGuard.compute_source_hash(
                record.content,
                {"session_id": record.session_id, "turn_count": record.turn_count},
            )

        # 步骤 2：幂等保护
        if record.source_hash in self._processed_hashes:
            logger.debug(
                "EpisodicStore: 幂等保护跳过写入（source_hash=%s）",
                record.source_hash,
            )
            return EpisodicWriteResult(
                success=False,
                skipped=True,
                skip_reason="DUPLICATE_HASH",
                occurrence_count=self._occurrence_counts.get(record.source_hash, 1),
            )

        # 步骤 3：MemoryGuard 校验
        try:
            self._guard.check_episodic_write(record)
        except MemoryGuardError as exc:
            logger.info(
                "EpisodicStore: MemoryGuard 拒绝写入（reason=%s）",
                exc.reason,
            )
            return EpisodicWriteResult(
                success=False,
                skipped=True,
                skip_reason="GUARD_REJECTED",
                guard_error=exc.reason,
            )

        # 步骤 4：写入内存存储
        record_id = record.source_hash
        self._records[record_id] = record
        self._write_order.append(record_id)
        self._processed_hashes.add(record.source_hash)

        # LRU 淘汰
        if len(self._write_order) > self._max_in_memory:
            oldest_id = self._write_order.pop(0)
            self._records.pop(oldest_id, None)

        # 步骤 5：更新出现次数
        content_key = hashlib.md5(record.content.encode()).hexdigest()[:8]
        self._occurrence_counts[content_key] = self._occurrence_counts.get(content_key, 0) + 1
        occurrence_count = self._occurrence_counts[content_key]

        # 判断蒸馏资格（与 DistillationEngine.min_occurrence 保持一致）
        distill_eligible = self._guard.check_distillation_eligibility(
            content_hash=content_key,
            occurrence_count=occurrence_count,
            evidence_level=record.evidence_level,
            min_occurrence=self._min_occurrence,
        )

        # 可选：写入 SQLiteLedger
        self._persist_to_ledger(record, record_id, distill_eligible, occurrence_count)

        logger.info(
            "EpisodicStore: 写入成功（record_id=%s, session=%s, distill_eligible=%s）",
            record_id, record.session_id, distill_eligible,
        )
        return EpisodicWriteResult(
            success=True,
            record_id=record_id,
            distill_eligible=distill_eligible,
            occurrence_count=occurrence_count,
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def query_by_session(
        self,
        session_id: str,
        limit: int = 20,
        evidence_level: Optional[EvidenceLevel] = None,
    ) -> List[EpisodicRecord]:
        """
        按 session_id 查询最近 N 条记录。

        Args:
            session_id: 会话 ID
            limit: 最大返回条数
            evidence_level: 按证据等级过滤（None 表示不过滤）

        Returns:
            按写入时间倒序排列的记录列表。
        """
        results = [
            r for r in self._records.values()
            if r.session_id == session_id
            and (evidence_level is None or r.evidence_level == evidence_level)
        ]
        # 按 created_at 倒序
        results.sort(key=lambda r: r.created_at, reverse=True)
        return results[:limit]

    def query_distill_candidates(
        self,
        min_occurrence: int = 3,
        limit: int = 50,
    ) -> List[EpisodicRecord]:
        """
        查询满足蒸馏资格的候选记录（频次 ≥ min_occurrence 或 MILESTONE）。

        Returns:
            蒸馏候选记录列表。
        """
        candidates = []
        for record in self._records.values():
            if record.evidence_level == EvidenceLevel.MILESTONE:
                candidates.append(record)
                continue
            if record.evidence_level == EvidenceLevel.NARRATIVE_ONLY:
                continue
            content_key = hashlib.md5(record.content.encode()).hexdigest()[:8]
            if self._occurrence_counts.get(content_key, 0) >= min_occurrence:
                candidates.append(record)
        candidates.sort(key=lambda r: r.created_at, reverse=True)
        return candidates[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """返回存储统计信息。"""
        total = len(self._records)
        by_level: Dict[str, int] = {}
        for r in self._records.values():
            key = r.evidence_level.value
            by_level[key] = by_level.get(key, 0) + 1
        return {
            "total_records": total,
            "processed_hashes": len(self._processed_hashes),
            "by_evidence_level": by_level,
            "max_in_memory": self._max_in_memory,
        }

    # ------------------------------------------------------------------
    # 内部：持久化到 SQLiteLedger
    # ------------------------------------------------------------------

    def _persist_to_ledger(
        self,
        record: EpisodicRecord,
        record_id: str,
        distill_eligible: bool,
        occurrence_count: int,
    ) -> None:
        """安全写入 SQLiteLedger，失败不影响主流程。"""
        if self._ledger is None:
            return
        try:
            self._ledger.append_audit_log(
                request_id=record_id,
                session_id=record.session_id,
                stage="episodic_store",
                audit_tag="EPISODIC_WRITTEN",
                detail=json.dumps({
                    "source_hash": record.source_hash,
                    "evidence_level": record.evidence_level.value,
                    "turn_count": record.turn_count,
                    "emotion_tags": record.emotion_tags,
                    "fact_tags": record.fact_tags,
                    "preference_tags": record.preference_tags,
                    "distill_eligible": distill_eligible,
                    "occurrence_count": occurrence_count,
                }, ensure_ascii=False),
                ts_ms=int(time.time() * 1000),
            )
        except Exception as exc:
            logger.warning("EpisodicStore: 持久化写入失败（不影响主流程）: %s", exc)
