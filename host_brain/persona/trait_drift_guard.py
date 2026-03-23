"""TraitDriftGuard：人格成长安全机制——Phase IV。

职责：
  1. 幅度锁：单次变化 ≤ 0.05，每周累计 ≤ 0.1
  2. 趋势锁：连续同向变化 > 3 次时生成 Canon Evolution 提案
  3. 不可变核心保护：warmth 基线值 0.85 ± 0.15 范围外的变化被阻止
  4. 审计日志记录所有校验结果

设计原则：
  - 纯函数式校验，不持有人格状态
  - 所有拒绝理由均可追溯
  - 与 PersonaEngine 解耦，PersonaGrowthTracker 在调用 apply_growth_delta 前先过安全校验
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from host_brain.persona.personality_traits import (
    DEFAULT_BASELINE,
    TRAIT_NAMES,
    PersonalityTraits,
    TraitsDelta,
)

_logger = logging.getLogger("Natsume.trait_drift_guard")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 单次变化幅度限制（每维度绝对值）
MAX_SINGLE_CHANGE = 0.05

# 每周累计变化限制（所有维度之和的绝对值）
MAX_WEEKLY_CUMULATIVE = 0.1

# 不可变核心保护配置：{trait_name: (baseline, max_deviation)}
# warmth 基线 0.85，最大偏移 ±0.15 → 范围 [0.70, 1.00]
IMMUTABLE_CORE: dict[str, tuple[float, float]] = {
    "warmth": (DEFAULT_BASELINE.get("warmth", 0.85), 0.15),
}

# 趋势锁阈值：连续同向变化超过此次数时触发 Evolution 提案
TREND_LOCK_THRESHOLD = 3


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GuardCheckResult:
    """安全校验结果。"""

    allowed: bool                 # 是否允许此次变化
    violations: list[str] = field(default_factory=list)  # 违规原因列表
    warnings: list[str] = field(default_factory=list)    # 警告（不阻止）
    evolution_proposal: dict[str, Any] | None = None      # Canon Evolution 提案（趋势锁触发时）

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "allowed": self.allowed,
            "violations": list(self.violations),
            "warnings": list(self.warnings),
        }
        if self.evolution_proposal:
            result["evolution_proposal"] = dict(self.evolution_proposal)
        return result


@dataclass(frozen=True)
class EvolutionProposal:
    """Canon Evolution 提案——趋势锁触发时生成。"""

    trait_name: str
    direction: str          # "UP" / "DOWN"
    consecutive_count: int  # 连续同向变化次数
    cumulative_delta: float # 累计变化量
    reason: str
    proposed_action: str    # 建议动作

    def as_dict(self) -> dict[str, Any]:
        return {
            "trait_name": self.trait_name,
            "direction": self.direction,
            "consecutive_count": self.consecutive_count,
            "cumulative_delta": round(self.cumulative_delta, 4),
            "reason": self.reason,
            "proposed_action": self.proposed_action,
        }


# ---------------------------------------------------------------------------
# TraitDriftGuard — 安全校验器
# ---------------------------------------------------------------------------

class TraitDriftGuard:
    """人格成长安全校验器。

    在每次 apply_growth_delta 之前调用 check()，
    如果返回 allowed=False，则拒绝此次变化。

    用法：
        guard = TraitDriftGuard()
        result = guard.check(
            delta=delta,
            current_traits=current,
            change_history=history,
        )
        if not result.allowed:
            raise ValueError(f"变化被拒绝: {result.violations}")
    """

    def __init__(
        self,
        *,
        max_single_change: float = MAX_SINGLE_CHANGE,
        max_weekly_cumulative: float = MAX_WEEKLY_CUMULATIVE,
        immutable_core: dict[str, tuple[float, float]] | None = None,
        trend_lock_threshold: int = TREND_LOCK_THRESHOLD,
    ) -> None:
        self._max_single = float(max_single_change)
        self._max_weekly = float(max_weekly_cumulative)
        self._immutable_core = dict(immutable_core or IMMUTABLE_CORE)
        self._trend_lock_threshold = int(trend_lock_threshold)

    def check(
        self,
        *,
        delta: TraitsDelta,
        current_traits: PersonalityTraits,
        change_history: list[dict[str, float]] | None = None,
        weekly_cumulative: dict[str, float] | None = None,
    ) -> GuardCheckResult:
        """执行完整的安全校验。

        Args:
            delta: 待应用的变化量
            current_traits: 当前人格特质
            change_history: 历史变化记录（delta.as_dict() 列表，用于趋势锁检测）
            weekly_cumulative: 本周各维度累计变化量（用于每周限制检测）

        Returns:
            GuardCheckResult
        """
        violations: list[str] = []
        warnings: list[str] = []
        evolution_proposal: dict[str, Any] | None = None

        delta_dict = delta.as_dict()

        # ── 1. 幅度锁：单次变化限制 ─────────────────────────────────────
        for name in TRAIT_NAMES:
            d = abs(delta_dict.get(name, 0.0))
            if d > self._max_single:
                violations.append(
                    f"单次变化超限: {name}={delta_dict[name]:+.4f} "
                    f"(限制 ±{self._max_single})"
                )

        # ── 2. 幅度锁：每周累计限制 ──────────────────────────────────────
        if weekly_cumulative:
            for name in TRAIT_NAMES:
                existing = abs(float(weekly_cumulative.get(name, 0.0)))
                new_delta = abs(delta_dict.get(name, 0.0))
                total = existing + new_delta
                if total > self._max_weekly:
                    violations.append(
                        f"每周累计超限: {name} 本周已变化 {existing:.4f}，"
                        f"加上本次 {new_delta:.4f} = {total:.4f} "
                        f"(限制 {self._max_weekly})"
                    )

        # ── 3. 不可变核心保护 ────────────────────────────────────────────
        for name, (baseline, max_dev) in self._immutable_core.items():
            current_value = float(getattr(current_traits, name, baseline))
            new_value = current_value + float(delta_dict.get(name, 0.0))
            if abs(new_value - baseline) > max_dev:
                violations.append(
                    f"不可变核心超限: {name} 当前={current_value:.4f}，"
                    f"变化后={new_value:.4f}，"
                    f"基线={baseline}±{max_dev} "
                    f"(范围 [{baseline - max_dev:.2f}, {baseline + max_dev:.2f}])"
                )

        # ── 4. 趋势锁：连续同向变化检测 ──────────────────────────────────
        if change_history and len(change_history) >= self._trend_lock_threshold:
            proposal = self._check_trend_lock(
                delta_dict, change_history,
            )
            if proposal is not None:
                warnings.append(
                    f"趋势锁触发: {proposal['trait_name']} 连续 "
                    f"{proposal['consecutive_count']} 次{proposal['direction']}方向变化，"
                    f"已生成 Canon Evolution 提案"
                )
                evolution_proposal = proposal

        allowed = len(violations) == 0
        result = GuardCheckResult(
            allowed=allowed,
            violations=violations,
            warnings=warnings,
            evolution_proposal=evolution_proposal,
        )

        if not allowed:
            _logger.warning("人格变化被拒绝: %s", violations)
        elif warnings:
            _logger.info("人格变化警告: %s", warnings)

        return result

    def _check_trend_lock(
        self,
        current_delta: dict[str, float],
        history: list[dict[str, float]],
    ) -> dict[str, Any] | None:
        """检测连续同向变化，返回 Evolution 提案或 None。"""
        recent = list(history[-self._trend_lock_threshold:])

        for name in TRAIT_NAMES:
            current_d = float(current_delta.get(name, 0.0))
            if abs(current_d) < 0.001:
                continue  # 本次无变化，跳过

            direction = "UP" if current_d > 0 else "DOWN"
            consecutive = 0
            cumulative = current_d

            for past in reversed(recent):
                past_d = float(past.get(name, 0.0))
                if abs(past_d) < 0.001:
                    break  # 无变化，中断连续性
                past_dir = "UP" if past_d > 0 else "DOWN"
                if past_dir != direction:
                    break  # 方向不同，中断连续性
                consecutive += 1
                cumulative += past_d

            # 包括本次变化，总次数 = consecutive + 1
            total_count = consecutive + 1
            if total_count > self._trend_lock_threshold:
                _CN_NAMES = {
                    "warmth": "温柔度", "curiosity": "好奇心",
                    "humor": "幽默感", "sensitivity": "敏感度",
                    "assertiveness": "自信度", "playfulness": "俏皮度",
                    "resilience": "韧性",
                }
                cn_name = _CN_NAMES.get(name, name)
                dir_cn = "上升" if direction == "UP" else "下降"
                proposal = EvolutionProposal(
                    trait_name=name,
                    direction=direction,
                    consecutive_count=total_count,
                    cumulative_delta=cumulative,
                    reason=f"{cn_name}连续{total_count}次{dir_cn}，需要审查",
                    proposed_action=(
                        f"建议 Host 审查 {cn_name} 的持续{dir_cn}趋势，"
                        f"累计变化 {cumulative:+.4f}。"
                        f"若符合预期，请确认；否则请回滚。"
                    ),
                )
                return proposal.as_dict()

        return None

    def clip_delta(
        self,
        delta: TraitsDelta,
        current_traits: PersonalityTraits,
    ) -> TraitsDelta:
        """将超限的 delta 截断到安全范围（不拒绝，而是截断）。

        这是一个辅助方法，当 check() 拒绝后可用此方法获得安全的 delta。
        """
        delta_dict = delta.as_dict()
        clipped: dict[str, float] = {}

        for name in TRAIT_NAMES:
            d = float(delta_dict.get(name, 0.0))

            # 单次幅度截断
            if abs(d) > self._max_single:
                d = self._max_single if d > 0 else -self._max_single

            # 不可变核心保护截断
            if name in self._immutable_core:
                baseline, max_dev = self._immutable_core[name]
                current_value = float(getattr(current_traits, name, baseline))
                new_value = current_value + d
                if new_value > baseline + max_dev:
                    d = baseline + max_dev - current_value
                elif new_value < baseline - max_dev:
                    d = baseline - max_dev - current_value

            clipped[name] = round(d, 4)

        return TraitsDelta(**clipped)
