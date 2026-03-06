"""Governor 分层治理策略（M42-B）。

将 SkillMeta.risk_level(1-5) 映射到审批通道（ApprovalTier）和审计级别（AuditTier），
实现"高风险更严、低风险更快"的分层治理，防止大量 Skills 接入后体验崩溃。

分层规则：
  risk_level=1 → CACHE（Policy Cache 直接放行，~0ms）
  risk_level=2 → RULE（规则引擎审批，~5ms）
  risk_level=3 → FULL（全量审批，~300ms）
  risk_level=4 → FULL（全量审批 + require_confirm）
  risk_level=5 → AIRLOCK（全量审批 + 人工确认）
  不可逆（reversible=False）且 risk_level>=3 → 升一级（FULL→AIRLOCK）
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from host_brain.skills.registry import SkillMeta


class ApprovalTier(Enum):
    """审批通道枚举。

    - CACHE：Policy Cache 命中直接放行，不调用规则引擎
    - RULE：规则引擎审批（R1-R7 通过即可），不调用 LLM
    - FULL：全量审批（现有 Governor 逻辑）
    - AIRLOCK：全量审批 + 人工确认（不可逆/高风险）
    """

    CACHE = "cache"
    RULE = "rule"
    FULL = "full"
    AIRLOCK = "airlock"


class AuditTier(Enum):
    """审计级别枚举。

    - LIGHT：轻量审计，只写 skill_exec_log 基础字段
    - STANDARD：标准审计（现有完整字段）
    - FULL_AUDIT：完整审计 + 额外 manifest/undo 记录
    """

    LIGHT = "light"
    STANDARD = "standard"
    FULL_AUDIT = "full_audit"


# ApprovalTier → AuditTier 映射
_AUDIT_TIER_MAP: dict[ApprovalTier, AuditTier] = {
    ApprovalTier.CACHE: AuditTier.LIGHT,
    ApprovalTier.RULE: AuditTier.STANDARD,
    ApprovalTier.FULL: AuditTier.STANDARD,
    ApprovalTier.AIRLOCK: AuditTier.FULL_AUDIT,
}


class GovernorTierPolicy:
    """Governor 分层治理策略。

    根据 SkillMeta 的 risk_level / reversible / clearance 字段，
    决定该 Skill 应走哪个审批通道和审计级别。
    """

    def get_approval_tier(self, meta: "SkillMeta") -> ApprovalTier:
        """根据 SkillMeta 返回审批通道。

        Args:
            meta: Skill 元数据

        Returns:
            ApprovalTier 枚举值
        """
        risk = meta.risk_level

        # 基础分层
        if risk <= 1:
            base = ApprovalTier.CACHE
        elif risk == 2:
            base = ApprovalTier.RULE
        elif risk <= 4:
            base = ApprovalTier.FULL
        else:
            base = ApprovalTier.AIRLOCK

        # 不可逆升级规则：reversible=False 且 risk_level>=3 → 升一级
        if not meta.reversible and risk >= 3:
            if base == ApprovalTier.FULL:
                base = ApprovalTier.AIRLOCK
            # AIRLOCK 已是最高级，不再升级

        return base

    def get_audit_tier(self, meta: "SkillMeta") -> AuditTier:
        """根据 SkillMeta 返回审计级别。

        Args:
            meta: Skill 元数据

        Returns:
            AuditTier 枚举值
        """
        approval_tier = self.get_approval_tier(meta)
        return _AUDIT_TIER_MAP[approval_tier]

    def get_audit_tier_from_approval(self, approval_tier: ApprovalTier) -> AuditTier:
        """根据 ApprovalTier 直接返回审计级别（供 SkillLoader 使用）。"""
        return _AUDIT_TIER_MAP.get(approval_tier, AuditTier.STANDARD)
