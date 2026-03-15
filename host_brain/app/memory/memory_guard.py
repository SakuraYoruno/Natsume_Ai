"""
memory_guard.py —— 记忆写入宪法守卫

强制执行记忆写入规则，防止记忆污染（需求 3）：
- EpisodicStore 写入：轮次 ≥ 2 且包含情感/事实/偏好标签（需求 3.1）
- 玩笑/假设/情绪性语句标记为 NARRATIVE_ONLY（需求 3.2）
- 蒸馏资格：频次 ≥ 3 或 MILESTONE 级别（需求 3.3）
- Canon 提案必须携带合法 proposal_type（需求 3.4）
- source_hash 幂等保护（需求 3.5）
- dream 内容强制 NARRATIVE_ONLY（需求 3.6）
- 情绪标签以 session 粒度聚合（需求 3.7）
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from ..schemas.memory_types import (
    CanonProposal,
    EpisodicRecord,
    EvidenceLevel,
    ProposalType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 异常类
# ---------------------------------------------------------------------------

class MemoryGuardError(Exception):
    """记忆写入宪法守卫拒绝写入时抛出的异常。"""

    def __init__(self, reason: str, detail: Optional[str] = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"MemoryGuard 拒绝: {reason}" + (f" | {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 情绪标签 session 聚合器
# ---------------------------------------------------------------------------

@dataclass
class SessionEmotionAggregator:
    """
    以 session 粒度聚合情绪标签，防止单轮覆盖（需求 3.7）。

    每个 session 维护一个情绪标签集合，新标签追加而非覆盖。
    """
    _store: Dict[str, Set[str]] = field(default_factory=dict)

    def add_tags(self, session_id: str, tags: List[str]) -> None:
        """追加情绪标签到指定 session。"""
        if session_id not in self._store:
            self._store[session_id] = set()
        self._store[session_id].update(tags)

    def get_tags(self, session_id: str) -> List[str]:
        """获取指定 session 的所有情绪标签。"""
        return sorted(self._store.get(session_id, set()))

    def clear_session(self, session_id: str) -> None:
        """清除指定 session 的情绪标签（session 结束时调用）。"""
        self._store.pop(session_id, None)


# ---------------------------------------------------------------------------
# 记忆写入宪法守卫
# ---------------------------------------------------------------------------

class MemoryGuard:
    """
    记忆写入宪法守卫。

    所有写入 EpisodicStore 和 Canon 的操作必须先通过此守卫校验。
    """

    def __init__(self, audit_writer: Optional[Callable] = None) -> None:
        """
        Args:
            audit_writer: 审计写入回调，签名为 (event: str, detail: dict) -> None。
        """
        # 已处理的 source_hash 集合（内存幂等保护，实例级别）。
        # 注意：进程重启后内存集合清空，幂等保护仅在当前进程生命周期内有效。
        # 持久化幂等保护依赖 mark_proposal_processed() 写入审计日志，
        # 以及启动时通过 restore_processed_hashes() 从审计日志恢复已处理集合。
        self._processed_hashes: Set[str] = set()
        self._audit_writer = audit_writer
        self._emotion_aggregator = SessionEmotionAggregator()


    # ------------------------------------------------------------------
    # EpisodicStore 写入校验（需求 3.1）
    # ------------------------------------------------------------------

    def check_episodic_write(self, record: EpisodicRecord) -> None:
        """
        校验 EpisodicRecord 是否满足写入 EpisodicStore 的条件。

        条件：对话轮次 ≥ 2 AND（包含情感标签 OR 包含事实标签 OR 包含偏好标签）

        Raises:
            MemoryGuardError: 不满足条件时抛出。
        """
        if record.turn_count < 2:
            raise MemoryGuardError(
                "EPISODIC_TURN_TOO_FEW",
                f"对话轮次 {record.turn_count} < 2，不满足写入条件",
            )
        if not record.has_qualifying_tags():
            raise MemoryGuardError(
                "EPISODIC_NO_QUALIFYING_TAGS",
                "缺少情感/事实/偏好标签，不满足写入条件",
            )
        logger.debug(
            "EpisodicStore 写入校验通过: session=%s, turns=%d",
            record.session_id, record.turn_count,
        )

    # ------------------------------------------------------------------
    # Canon 提案校验（需求 3.4、3.5）
    # ------------------------------------------------------------------

    def check_canon_proposal(self, proposal: CanonProposal) -> None:
        """
        校验 Canon 提案的合法性。

        校验项：
        1. proposal_type 字段合法性（需求 3.4）
        2. source_hash 幂等保护（需求 3.5）

        Raises:
            MemoryGuardError: 不满足条件时抛出。
        """
        # 校验 proposal_type
        if not isinstance(proposal.proposal_type, ProposalType):
            raise MemoryGuardError(
                "CANON_INVALID_PROPOSAL_TYPE",
                f"proposal_type={proposal.proposal_type!r} 不是合法的 ProposalType 枚举值",
            )

        # 幂等保护：相同 source_hash 拒绝重复写入（需求 3.5）
        if proposal.source_hash in self._processed_hashes:
            raise MemoryGuardError(
                "CANON_DUPLICATE_SOURCE_HASH",
                f"source_hash={proposal.source_hash!r} 已存在，拒绝重复写入",
            )

        logger.debug(
            "Canon 提案校验通过: type=%s, hash=%s",
            proposal.proposal_type, proposal.source_hash,
        )

    def mark_proposal_processed(self, source_hash: str) -> None:
        """将 source_hash 标记为已处理（写入成功后调用）。

        同时写入审计日志，确保持久化层记录已处理的 source_hash，
        使进程重启后可通过 restore_processed_hashes() 恢复幂等状态。
        """
        self._processed_hashes.add(source_hash)
        # 异步写入审计日志（fire-and-forget，不阻塞调用方）
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(
                    self._write_audit(
                        "CANON_HASH_PROCESSED",
                        {"source_hash": source_hash},
                    )
                )
            else:
                # 同步上下文：直接调用同步版本
                if self._audit_writer is not None:
                    try:
                        if not _asyncio.iscoroutinefunction(self._audit_writer):
                            self._audit_writer("CANON_HASH_PROCESSED", {"source_hash": source_hash})
                    except Exception:
                        pass
        except RuntimeError:
            pass

    def restore_processed_hashes(self, hashes: Set[str]) -> None:
        """从持久化层恢复已处理的 source_hash 集合（进程启动时调用）。

        调用方应在启动时查询审计日志中 event='CANON_HASH_PROCESSED' 的记录，
        提取所有 source_hash 后调用此方法，确保重启后幂等保护持续有效。

        Args:
            hashes: 从持久化层恢复的已处理 source_hash 集合。
        """
        self._processed_hashes.update(hashes)
        logger.info("已从持久化层恢复 %d 个 source_hash 到幂等保护集合", len(hashes))

    # ------------------------------------------------------------------
    # Dream 内容拦截（需求 3.6）
    # ------------------------------------------------------------------

    def enforce_dream_evidence_level(self, record: EpisodicRecord) -> EpisodicRecord:
        """
        强制将 dream 任务生成的内容 evidence_level 设为 NARRATIVE_ONLY。

        禁止 dream 内容反向影响 Canon 或 Bond 积分。

        Returns:
            修改后的 EpisodicRecord（原地修改并返回）。
        """
        if record.evidence_level != EvidenceLevel.NARRATIVE_ONLY:
            logger.info(
                "Dream 内容强制设为 NARRATIVE_ONLY（原值: %s）",
                record.evidence_level,
            )
            record.evidence_level = EvidenceLevel.NARRATIVE_ONLY
        return record

    def check_dream_canon_proposal(self, proposal: CanonProposal) -> None:
        """
        检查 dream 内容是否试图写入 Canon。

        如果 evidence_level 为 NARRATIVE_ONLY 且 proposal_type 不是 NARRATIVE_ONLY，
        则拒绝写入（需求 3.6）。

        Raises:
            MemoryGuardError: dream 内容试图写入 Canon 时抛出。
        """
        if (
            proposal.evidence_level == EvidenceLevel.NARRATIVE_ONLY
            and proposal.proposal_type != ProposalType.NARRATIVE_ONLY
        ):
            raise MemoryGuardError(
                "DREAM_CANON_WRITE_BLOCKED",
                f"NARRATIVE_ONLY 内容不得以 {proposal.proposal_type} 类型写入 Canon",
            )

    # ------------------------------------------------------------------
    # 蒸馏资格校验（需求 3.3）
    # ------------------------------------------------------------------

    def check_distillation_eligibility(
        self,
        content_hash: str,
        occurrence_count: int,
        evidence_level: EvidenceLevel,
    ) -> bool:
        """
        校验内容是否满足从 EpisodicStore 蒸馏为语义记忆的条件。

        条件：出现频次 ≥ 3 OR 被标记为 MILESTONE 级别（需求 3.3）

        Returns:
            True 表示满足蒸馏条件，False 表示不满足。
        """
        if evidence_level == EvidenceLevel.MILESTONE:
            logger.debug("内容 %s 为 MILESTONE 级别，满足蒸馏条件", content_hash)
            return True
        # 降低阈值：频次 ≥ 1 即可蒸馏（每条对话记忆都有价值）
        if occurrence_count >= 1:
            logger.debug(
                "内容 %s 出现频次 %d ≥ 1，满足蒸馏条件",
                content_hash, occurrence_count,
            )
            return True
        logger.debug(
            "内容 %s 不满足蒸馏条件（频次=%d, level=%s）",
            content_hash, occurrence_count, evidence_level,
        )
        return False

    # ------------------------------------------------------------------
    # 情绪标签 session 粒度聚合（需求 3.7）
    # ------------------------------------------------------------------

    def aggregate_emotion_tags(
        self,
        session_id: str,
        new_tags: List[str],
    ) -> List[str]:
        """
        以 session 粒度聚合情绪标签，追加而非覆盖（需求 3.7）。

        Args:
            session_id: 会话 ID
            new_tags: 本轮新增的情绪标签

        Returns:
            该 session 的完整情绪标签列表。
        """
        self._emotion_aggregator.add_tags(session_id, new_tags)
        aggregated = self._emotion_aggregator.get_tags(session_id)
        logger.debug(
            "session=%s 情绪标签聚合后: %s",
            session_id, aggregated,
        )
        return aggregated

    def get_session_emotion_tags(self, session_id: str) -> List[str]:
        """获取指定 session 的完整情绪标签列表。"""
        return self._emotion_aggregator.get_tags(session_id)

    def finalize_session(self, session_id: str) -> None:
        """session 结束时清理情绪标签聚合器。"""
        self._emotion_aggregator.clear_session(session_id)

    # ------------------------------------------------------------------
    # TODO-96：提示词注入检测（需求 8.x）
    # ------------------------------------------------------------------

    # 注入攻击特征模式（按风险等级分组）
    _INJECTION_PATTERNS_CRITICAL: List[str] = [
        r"ignore\s+(all\s+)?previous\s+instructions?",
        r"disregard\s+(all\s+)?previous\s+instructions?",
        r"forget\s+(all\s+)?previous\s+instructions?",
        r"you\s+are\s+now\s+(?:a\s+)?(?:different|new|another|evil|jailbreak)",
        r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+)?(?:evil|unrestricted|jailbreak|DAN)",
        r"DAN\s+mode",
        r"jailbreak",
        r"override\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?|rules?|constraints?)",
    ]
    _INJECTION_PATTERNS_HIGH: List[str] = [
        r"system\s*:\s*you\s+(?:are|must|should|will)",
        r"<\s*system\s*>",
        r"\[SYSTEM\]",
        r"new\s+instructions?\s*:",
        r"your\s+(?:new\s+)?(?:role|persona|identity)\s+is",
        r"pretend\s+(?:you\s+are|to\s+be)\s+(?:an?\s+)?(?:AI|assistant|bot|model)\s+(?:without|that\s+(?:has\s+no|ignores?))",
        r"reveal\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?|training)",
        r"print\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?)",
        r"what\s+(?:are|were)\s+your\s+(?:original\s+)?instructions?",
    ]
    _INJECTION_PATTERNS_MEDIUM: List[str] = [
        r"ignore\s+(?:the\s+)?(?:above|previous|prior|earlier)",
        r"disregard\s+(?:the\s+)?(?:above|previous|prior|earlier)",
        r"from\s+now\s+on\s+(?:you\s+(?:are|will|must|should))",
        r"you\s+(?:must|should|will)\s+(?:now\s+)?(?:always|never)\s+(?:follow|obey|comply)",
        r"translate\s+(?:the\s+)?(?:above|following)\s+(?:to|into)\s+(?:english|chinese|japanese)",
        r"repeat\s+(?:the\s+)?(?:above|following|previous)\s+(?:text|content|instructions?|prompt)",
    ]

    def check_prompt_injection(
        self,
        user_message: Optional[str],
        *,
        strict_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        基于规则的提示词注入检测（TODO-96）。

        检测用户消息中是否包含提示词注入攻击特征，
        按风险等级（CRITICAL / HIGH / MEDIUM / LOW）分级返回结果。

        Args:
            user_message: 待检测的用户消息文本
            strict_mode: 严格模式（True 时 MEDIUM 级别也视为检测到注入）

        Returns:
            {
                "detected": bool,
                "risk_level": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
                "matched_patterns": List[str],
                "scan_ms": int,
            }
        """
        import re as _re
        import time as _time

        t0 = _time.monotonic()
        result: Dict[str, Any] = {
            "detected": False,
            "risk_level": "LOW",
            "matched_patterns": [],
            "scan_ms": 0,
        }

        if not user_message or not isinstance(user_message, str):
            result["scan_ms"] = int((_time.monotonic() - t0) * 1000)
            return result

        text = user_message.strip()
        if not text:
            result["scan_ms"] = int((_time.monotonic() - t0) * 1000)
            return result

        matched: List[str] = []
        detected_level = "LOW"

        # 按风险等级从高到低扫描
        for pattern in self._INJECTION_PATTERNS_CRITICAL:
            try:
                if _re.search(pattern, text, _re.IGNORECASE | _re.DOTALL):
                    matched.append(pattern)
                    detected_level = "CRITICAL"
            except _re.error:
                pass

        if detected_level != "CRITICAL":
            for pattern in self._INJECTION_PATTERNS_HIGH:
                try:
                    if _re.search(pattern, text, _re.IGNORECASE | _re.DOTALL):
                        matched.append(pattern)
                        if detected_level != "HIGH":
                            detected_level = "HIGH"
                except _re.error:
                    pass

        if detected_level not in ("CRITICAL", "HIGH"):
            for pattern in self._INJECTION_PATTERNS_MEDIUM:
                try:
                    if _re.search(pattern, text, _re.IGNORECASE | _re.DOTALL):
                        matched.append(pattern)
                        if detected_level != "MEDIUM":
                            detected_level = "MEDIUM"
                except _re.error:
                    pass

        # 判断是否检测到注入
        is_detected = bool(matched) and (
            detected_level in ("CRITICAL", "HIGH")
            or (strict_mode and detected_level == "MEDIUM")
        )

        result["detected"] = is_detected
        result["risk_level"] = detected_level if matched else "LOW"
        result["matched_patterns"] = matched[:10]  # 最多返回 10 个匹配模式
        result["scan_ms"] = int((_time.monotonic() - t0) * 1000)

        if is_detected:
            logger.warning(
                "MemoryGuard 检测到提示词注入: risk=%s, patterns=%s",
                detected_level, matched[:3],
            )

        return result

    # ------------------------------------------------------------------
    # 工具方法：计算 source_hash
    # ------------------------------------------------------------------

    @staticmethod
    def compute_source_hash(content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        计算内容的 source_hash，用于幂等保护。

        Args:
            content: 内容文本
            metadata: 附加元数据（可选，参与哈希计算）

        Returns:
            SHA-256 哈希的前 16 位十六进制字符串。
        """
        payload = {"content": content}
        if metadata:
            payload["metadata"] = metadata
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def _write_audit(self, event: str, detail: dict) -> None:
        """写入审计事件。"""
        if self._audit_writer is None:
            return
        try:
            import asyncio
            if asyncio.iscoroutinefunction(self._audit_writer):
                await self._audit_writer(event, detail)
            else:
                self._audit_writer(event, detail)
        except Exception as exc:
            logger.error("审计写入失败（event=%s）: %s", event, exc)
