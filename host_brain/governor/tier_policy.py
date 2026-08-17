"""Governor 分层治理策略（M42-B）。

将 SkillMeta.risk_level(1-5) 映射到审批通道（ApprovalTier）和审计级别（AuditTier），
并允许 feature-gated capability scorecard 只增加风险下限。实现"高风险更严、低风险更快"
的分层治理，防止大量 Skills 接入后体验崩溃。

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
from typing import TYPE_CHECKING, Any

from host_brain.governor.capability_policy import CapabilityGovernorPolicyAdapter

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
    决定该 Skill 应走哪个审批通道和审计级别。成熟度适配器只能抬高有效风险，
    因而该映射始终朝向更严格的审批和更完整的审计，不能由外部记分卡降级。
    """

    def __init__(
        self,
        capability_policy: CapabilityGovernorPolicyAdapter | None = None,
    ) -> None:
        # 适配器默认特性关闭；只有显式启用 HOST_CAPABILITY_MATURITY_POLICY_ENABLED
        # 且提供记分卡后才参与裁决，从而保留既有审批行为并让上线是可控变更。
        self._capability_policy = capability_policy or CapabilityGovernorPolicyAdapter()

    def get_approval_tier(
        self,
        meta: "SkillMeta",
        *,
        scorecard: Any | None = None,
    ) -> ApprovalTier:
        """根据 SkillMeta 返回审批通道。

        Args:
            meta: Skill 元数据
            scorecard: 可选只读成熟度投影；只能收紧基础审批通道

        Returns:
            ApprovalTier 枚举值
        """
        risk = self._capability_policy.effective_risk(meta, scorecard=scorecard)

        # 基础分层只看已收紧的有效风险；低风险快路径仍由前序意图和门控验证约束。
        if risk <= 1:
            base = ApprovalTier.CACHE
        elif risk == 2:
            base = ApprovalTier.RULE
        elif risk <= 4:
            base = ApprovalTier.FULL
        else:
            base = ApprovalTier.AIRLOCK

        # 不可逆升级规则：reversible=False 且 risk_level>=3 → 升一级，防止仅凭同一
        # 风险数字把不可恢复效果送入无需人工确认的全量通道。
        if not meta.reversible and risk >= 3:
            if base == ApprovalTier.FULL:
                base = ApprovalTier.AIRLOCK
            # AIRLOCK 已是最高级，不再升级

        return base

    def get_audit_tier(
        self,
        meta: "SkillMeta",
        *,
        scorecard: Any | None = None,
    ) -> AuditTier:
        """根据 SkillMeta 返回审计级别。

        Args:
            meta: Skill 元数据

        Returns:
            AuditTier 枚举值
        """
        approval_tier = self.get_approval_tier(meta, scorecard=scorecard)
        return _AUDIT_TIER_MAP[approval_tier]

    def get_audit_tier_from_approval(self, approval_tier: ApprovalTier) -> AuditTier:
        """由已定审批通道反查审计级别，未知值回退标准审计而非轻量审计。"""
        return _AUDIT_TIER_MAP.get(approval_tier, AuditTier.STANDARD)
