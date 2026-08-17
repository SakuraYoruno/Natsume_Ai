"""能力成熟度记分卡的 Governor 适配器：只能抬升风险，不能给予折扣。

记分卡是外部/联邦输入时，适配器先验证其绑定、只读属性和静态上限；缺失、异常或跨能力
错配均按最高风险处理，防止成熟度数据成为绕过既有静态策略的授权来源。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from typing import Any


CAPABILITY_POLICY_DECISION_SCHEMA_VERSION = "Natsume.capability.policy_decision.v1"
CAPABILITY_MATURITY_POLICY_ENV = "HOST_CAPABILITY_MATURITY_POLICY_ENABLED"
_MISSING = object()


def _env_bool(name: str, *, default: bool) -> bool:
    """读取显式特性开关；未设置时使用调用方给定的安全默认值。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _risk(value: Any) -> int:
    """将无效风险归为 5，避免宽松解析意外降低能力限制。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return 5
    return value if 1 <= value <= 5 else 5


def _maturity(value: Any, *, allow_label: bool = False) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        resolved = value
    elif allow_label and isinstance(value, str):
        normalized = value.strip().upper()
        if len(normalized) != 2 or normalized[0] != "L" or normalized[1] not in "012345":
            return None
        resolved = int(normalized[1])
    else:
        return None
    return resolved if 0 <= resolved <= 5 else None


@dataclass(frozen=True, slots=True)
class CapabilityPolicyDecision:
    """静态风险与成熟度约束合成后的只读裁决快照。"""

    capability_id: str
    enabled: bool
    static_risk: int
    maturity_level: int | None
    maturity_risk_floor: int
    effective_risk: int
    tightened: bool
    reasons: tuple[str, ...]
    schema_version: str = CAPABILITY_POLICY_DECISION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "capability_id": self.capability_id,
            "enabled": self.enabled,
            "static_risk": self.static_risk,
            "maturity_level": None if self.maturity_level is None else f"L{self.maturity_level}",
            "maturity_risk_floor": self.maturity_risk_floor,
            "effective_risk": self.effective_risk,
            "tightened": self.tightened,
            "reasons": list(self.reasons),
            "can_relax_static_policy": False,
            "read_only": True,
        }


class CapabilityGovernorPolicyAdapter:
    """将成熟度翻译为附加风险下限，绝不作为静态策略的折扣。

    低成熟度能力会进入最高风险/AIRLOCK 路径；高成熟度最多保留现有静态风险，不能自行
    扩大权限。该不变量也体现在返回对象的 ``can_relax_static_policy=False``。
    """

    # L0 不可用、L1 仅实验室、L2 需人工监督，因此进入 AIRLOCK；L3+ 可使用既有
    # 静态策略，但绝不能比它更宽松。
    _MATURITY_RISK_FLOOR = {0: 5, 1: 5, 2: 5, 3: 1, 4: 1, 5: 1}

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        scorecard_provider: Callable[[str], Any | None] | None = None,
    ) -> None:
        self.enabled = _env_bool(CAPABILITY_MATURITY_POLICY_ENV, default=False) if enabled is None else bool(enabled)
        self.scorecard_provider = scorecard_provider

    def get_scorecard(self, capability_id: str) -> Any | None:
        """安全读取能力记分卡；提供方异常由 ``evaluate`` 按缺失记分卡失败关闭。"""
        if self.scorecard_provider is None:
            return None
        try:
            return self.scorecard_provider(str(capability_id or "").strip())
        except Exception:
            # 成熟度策略启用后，提供方异常不能恢复更宽松的静态路由。
            return None

    def evaluate(self, meta: Any, *, scorecard: Any | None = None) -> CapabilityPolicyDecision:
        """验证记分卡并计算单调不减的有效风险。"""
        capability_id = str(_value(meta, "skill_name", _value(meta, "capability_id", "")) or "").strip()
        static_risk = _risk(_value(meta, "risk_level", 5))
        if not self.enabled:
            return CapabilityPolicyDecision(
                capability_id=capability_id,
                enabled=False,
                static_risk=static_risk,
                maturity_level=None,
                maturity_risk_floor=static_risk,
                effective_risk=static_risk,
                tightened=False,
                reasons=("FEATURE_DISABLED_STATIC_POLICY_PRESERVED",),
            )

        resolved = scorecard if scorecard is not None else self.get_scorecard(capability_id)
        if resolved is None:
            return CapabilityPolicyDecision(
                capability_id=capability_id,
                enabled=True,
                static_risk=static_risk,
                maturity_level=0,
                maturity_risk_floor=5,
                effective_risk=5,
                tightened=static_risk < 5,
                reasons=("NO_SCORECARD_FAIL_CLOSED",),
            )

        scorecard_capability = str(_value(resolved, "capability_id", "") or "").strip()
        schema_version = str(_value(resolved, "schema_version", "") or "").strip()
        policy_effect = str(_value(resolved, "policy_effect", "") or "").strip().upper()
        read_only = _value(resolved, "read_only", False) is True
        if (
            not capability_id
            or scorecard_capability != capability_id
            or schema_version != "Natsume.capability.scorecard.v1"
            or policy_effect != "RESTRICT_ONLY"
            or not read_only
        ):
            # 错配记分卡不构成权威：采用最高风险失败关闭，而非信任跨能力证据。
            return CapabilityPolicyDecision(
                capability_id=capability_id,
                enabled=True,
                static_risk=static_risk,
                maturity_level=0,
                maturity_risk_floor=5,
                effective_risk=5,
                tightened=static_risk < 5,
                reasons=("SCORECARD_BINDING_INVALID_FAIL_CLOSED",),
            )

        raw_maturity_value = _value(resolved, "maturity_level_value", _MISSING)
        maturity = (
            _maturity(_value(resolved, "maturity_level", _MISSING), allow_label=True)
            if raw_maturity_value is _MISSING
            else _maturity(raw_maturity_value)
        )
        scorecard_static_risk = _risk(
            _value(resolved, "canonical_risk", _value(resolved, "static_risk_floor", static_risk))
        )
        declared_ceiling = _maturity(_value(resolved, "static_maturity_ceiling", _MISSING))
        expected_ceiling = {1: 5, 2: 5, 3: 4, 4: 2, 5: 1}[scorecard_static_risk]
        if (
            maturity is None
            or declared_ceiling is None
            or declared_ceiling > expected_ceiling
            or maturity > declared_ceiling
        ):
            return CapabilityPolicyDecision(
                capability_id=capability_id,
                enabled=True,
                static_risk=static_risk,
                maturity_level=0,
                maturity_risk_floor=5,
                effective_risk=5,
                tightened=static_risk < 5,
                reasons=("SCORECARD_STATIC_CEILING_INVALID_FAIL_CLOSED",),
            )
        maturity_floor = self._MATURITY_RISK_FLOOR[maturity]
        effective = max(static_risk, scorecard_static_risk, maturity_floor)
        reasons = [f"MATURITY_L{maturity}_RISK_FLOOR_{maturity_floor}"]
        if scorecard_static_risk > static_risk:
            reasons.append("FEDERATED_STATIC_RISK_TIGHTENED")
        if effective == static_risk:
            reasons.append("STATIC_POLICY_PRESERVED")
        return CapabilityPolicyDecision(
            capability_id=capability_id,
            enabled=True,
            static_risk=static_risk,
            maturity_level=maturity,
            maturity_risk_floor=maturity_floor,
            effective_risk=effective,
            tightened=effective > static_risk,
            reasons=tuple(reasons),
        )

    def effective_risk(self, meta: Any, *, scorecard: Any | None = None) -> int:
        """仅返回裁决后的风险数值，供审批分层选择通道。"""
        return self.evaluate(meta, scorecard=scorecard).effective_risk


__all__ = [
    "CAPABILITY_MATURITY_POLICY_ENV",
    "CAPABILITY_POLICY_DECISION_SCHEMA_VERSION",
    "CapabilityGovernorPolicyAdapter",
    "CapabilityPolicyDecision",
]
