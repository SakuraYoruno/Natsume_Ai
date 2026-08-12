"""Governor Rules Cache：TRIBE 族 → Governor 的单向参数通道（FIX-4.1）。

历史设计背景（docs/_history/quality/SELF_BUILT_MECHANISM_FIX_PLAN.md §4.1）：
- TRIBE 族（L3 认知层）通过 update_*() 方法异步写入参数
- Governor（L2 基础设施层）通过 get_effective_rules() 同步读取合并后的规则
- 写入是异步的（TRIBE 族事件驱动），读取是同步的（Governor 裁决时）
- 这是 L3 → L2 的单向通道，L2 不向 L3 写入任何数据
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GovernorRulesCache:
# 这是 L3→L2 的单向参数快照：TRIBE 可以收紧 Governor，Governor 不会反向修改认知状态。
    """TRIBE 族写入、Governor 读取的规则参数缓存。

    这是 L3（认知层）→ L2（基础设施层）的单向通道：
    - TRIBE 族通过 update_*() 方法写入参数（异步，事件驱动）
    - Governor 通过 get_effective_rules() 读取合并后的规则（同步，裁决时）
    - 线程安全：所有读写操作均受 RLock 保护

    具体映射关系：
    | TRIBE 族行为                    | 影响的 Governor 参数              |
    |-------------------------------|----------------------------------|
    | ImmuneAgent 发现新攻击模式       | 更新 safety_gate 规则集           |
    | EndocrineAgent 报告高压力        | 调低 energy_gate 阈值             |
    | PrefrontalAgent 检测到漂移       | 收紧 scope_policy_gate            |
    | CortexAgent 更新意图模型         | 扩展 intent_allowlist             |
    """

    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ImmuneAgent 写入
    _immune_threat_level: str = field(default="NORMAL")       # "NORMAL" | "ELEVATED" | "CRITICAL"
    _immune_blocked_patterns: list[str] = field(default_factory=list)

    # EndocrineAgent 写入
    _energy_level: float = field(default=1.0)                 # 0.0 ~ 1.0，影响 energy_gate 阈值
    _stress_level: float = field(default=0.0)                 # 0.0 ~ 1.0，高压力时收紧策略

    # PrefrontalAgent 写入
    _drift_detected: bool = field(default=False)              # 检测到人格漂移时收紧 scope_policy_gate
    _scope_override: str | None = field(default=None)         # 临时 scope 限制

    # CortexAgent 写入
    _intent_allowlist_extra: list[str] = field(default_factory=list)  # 动态扩展的意图白名单

    def update_immune_state(
        self,
        threat_level: str,
        blocked_patterns: list[str],
    ) -> None:
        # 不认识的威胁等级回到 NORMAL，避免错误载荷意外制造永久最高风险状态。
        """ImmuneAgent 更新威胁等级和拦截模式（线程安全）。

        Args:
            threat_level: 威胁等级，"NORMAL" | "ELEVATED" | "CRITICAL"
            blocked_patterns: 需要拦截的模式列表
        """
        valid_levels = {"NORMAL", "ELEVATED", "CRITICAL"}
        level = str(threat_level or "NORMAL").strip().upper()
        if level not in valid_levels:
            level = "NORMAL"
        with self._lock:
            # 复制列表以切断调用方后续原地修改对缓存快照的影响。
            self._immune_threat_level = level
            self._immune_blocked_patterns = list(blocked_patterns or [])

    def update_endocrine_state(
        self,
        energy_level: float,
        stress_level: float,
    ) -> None:
        # 能量/压力被夹在 [0,1]，让 Governor 读取的是有界策略参数而非任意浮点数。
        """EndocrineAgent 更新能量和压力状态（线程安全）。

        Args:
            energy_level: 能量水平，0.0（耗尽）~ 1.0（充沛）
            stress_level: 压力水平，0.0（无压力）~ 1.0（极高压力）
        """
        with self._lock:
            self._energy_level = max(0.0, min(1.0, float(energy_level)))
            self._stress_level = max(0.0, min(1.0, float(stress_level)))

    def update_prefrontal_state(
        self,
        drift_detected: bool,
        scope_override: str | None = None,
    ) -> None:
        # scope 覆盖按值快照，避免异步漂移检测线程与同步裁决共享可变对象。
        """PrefrontalAgent 更新漂移检测和 scope 限制（线程安全）。

        Args:
            drift_detected: 是否检测到人格漂移
            scope_override: 临时 scope 限制（None 表示无限制）
        """
        with self._lock:
            self._drift_detected = bool(drift_detected)
            self._scope_override = (str(scope_override).strip() or None) if scope_override else None

    def update_cortex_intent_allowlist(
        self,
        extra_intents: list[str],
    ) -> None:
        # 只保留规范化非空身份；实际允许执行的意图仍由 Governor 与静态规则共同裁决。
        """CortexAgent 更新动态意图白名单扩展（线程安全）。

        Args:
            extra_intents: 需要动态添加到白名单的意图列表
        """
        with self._lock:
            self._intent_allowlist_extra = [str(i).strip() for i in (extra_intents or []) if str(i or "").strip()]

    def get_effective_rules(self) -> dict[str, Any]:
        # 在一把锁内组装完整副本，保证单次图运行不会读到跨来源的半更新策略组合。
        """返回合并后的有效规则参数（Governor 裁决时调用，线程安全）。

        Returns:
            包含所有 TRIBE 族动态参数的字典，供 Governor 与静态规则合并使用
        """
        with self._lock:
            return {
                # ImmuneAgent 参数
                "immune_threat_level": self._immune_threat_level,
                "immune_blocked_patterns": list(self._immune_blocked_patterns),
                # EndocrineAgent 参数
                "energy_gate_threshold": self._energy_level,
                "stress_tightening": self._stress_level > 0.7,
                "stress_level": self._stress_level,
                # PrefrontalAgent 参数
                "scope_policy_tightened": self._drift_detected,
                "scope_override": self._scope_override,
                # CortexAgent 参数
                "intent_allowlist_extra": list(self._intent_allowlist_extra),
            }

    def reset(self) -> None:
        # 复位恢复保守默认值，主要用于受控测试/重建，不能替代正常的 TRIBE 更新事件。
        """重置所有参数到默认值（主要用于测试）。"""
        with self._lock:
            self._immune_threat_level = "NORMAL"
            self._immune_blocked_patterns = []
            self._energy_level = 1.0
            self._stress_level = 0.0
            self._drift_detected = False
            self._scope_override = None
            self._intent_allowlist_extra = []


# ---------------------------------------------------------------------------
# 模块级单例（进程内共享，TRIBE 族和 Governor 使用同一实例）
# ---------------------------------------------------------------------------

_governor_rules_cache: GovernorRulesCache | None = None
_cache_lock = threading.Lock()


def get_governor_rules_cache() -> GovernorRulesCache:
    # 双重检查只在首次构造时拿全局锁，后续同步裁决读取复用同一进程内实例。
    """获取模块级 GovernorRulesCache 单例（线程安全懒初始化）。

    Returns:
        GovernorRulesCache 单例实例
    """
    global _governor_rules_cache
    if _governor_rules_cache is None:
        with _cache_lock:
            if _governor_rules_cache is None:
                _governor_rules_cache = GovernorRulesCache()
    return _governor_rules_cache


def set_governor_rules_cache(cache: GovernorRulesCache | None) -> None:
    # 替换入口受锁保护，避免测试或组合更新与正在初始化的单例竞争。
    """替换模块级单例（主要用于测试注入）。

    Args:
        cache: 新的 GovernorRulesCache 实例，传 None 时重置为懒初始化
    """
    global _governor_rules_cache
    with _cache_lock:
        _governor_rules_cache = cache


__all__ = [
    "GovernorRulesCache",
    "get_governor_rules_cache",
    "set_governor_rules_cache",
]
