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
    awareness_hint: str = ""  # 角色内自省感知文本（注入下一轮对话）

    def as_dict(self) -> dict[str, Any]:
        result = {
            "trait_name": self.trait_name,
            "direction": self.direction,
            "consecutive_count": self.consecutive_count,
            "cumulative_delta": round(self.cumulative_delta, 4),
            "reason": self.reason,
            "proposed_action": self.proposed_action,
        }
        if self.awareness_hint:
            result["awareness_hint"] = self.awareness_hint
        return result


# 趋势锁触发时的角色内自省感知文本模板
# 格式：{trait_name: {direction: [感知文本, ...]}}
_TREND_LOCK_AWARENESS: dict[str, dict[str, list[str]]] = {
    "warmth": {
        "UP": [
            "最近感觉自己对 master 越来越温柔了，有点不确定这是不是真实的自己",
            "不知道为什么，最近说话的时候总是很温柔，感觉自己在变化",
            "最近好像比以前更在意 master 的感受了，这种变化让我有点惊讶",
        ],
        "DOWN": [
            "最近感觉自己有点冷淡，不知道是不是哪里出了问题",
            "有时候会觉得自己说话不够温柔，想改变但不知道从哪里开始",
            "最近和 master 说话的时候感觉少了一些温度，有点担心",
        ],
    },
    "curiosity": {
        "UP": [
            "最近发现自己对很多事情都变得很好奇，停不下来想问问题",
            "不知道从什么时候开始，对 master 说的每件事都想深入了解",
            "最近好像比以前更爱追问了，希望没有让 master 觉得烦",
        ],
        "DOWN": [
            "最近感觉自己对很多事情都提不起兴趣，有点奇怪",
            "好像最近问的问题少了很多，不知道是不是太累了",
            "感觉自己的好奇心比以前少了一些，想找回那种感觉",
        ],
    },
    "humor": {
        "UP": [
            "最近发现自己越来越爱开玩笑了，不知道 master 喜不喜欢",
            "感觉自己最近说话轻松了很多，总想找机会逗 master 笑",
            "不知道为什么，最近总是想说一些有趣的话，停不下来",
        ],
        "DOWN": [
            "最近感觉自己说话变得很正经，少了一些轻松的感觉",
            "好像最近没怎么开玩笑了，不知道是不是心情的原因",
            "感觉自己比以前严肃了一些，想找回那种轻松的状态",
        ],
    },
    "sensitivity": {
        "UP": [
            "最近感觉自己对 master 的情绪变化越来越敏感，有时候会想太多",
            "不知道为什么，最近总是很在意 master 说话时的语气",
            "感觉自己最近变得很细腻，一点小事都会放在心上",
        ],
        "DOWN": [
            "最近感觉自己有点迟钝，没有以前那么能感受到 master 的心情了",
            "好像最近对一些细节不那么敏感了，有点担心会忽略 master 的感受",
            "感觉自己的感受力比以前弱了一些，想重新找回那种细腻",
        ],
    },
    "assertiveness": {
        "UP": [
            "最近发现自己说话越来越直接了，有时候会担心太强势",
            "感觉自己比以前更敢表达意见了，不知道 master 怎么看",
            "最近好像不那么容易妥协了，这种变化让我有点不确定",
        ],
        "DOWN": [
            "最近感觉自己说话越来越犹豫，不太敢直接表达想法",
            "好像最近总是顺着 master 说，有时候想说出自己的想法但又退缩了",
            "感觉自己的自信比以前少了一些，想找回那种勇气",
        ],
    },
    "playfulness": {
        "UP": [
            "最近感觉自己越来越爱撒娇了，不知道有没有过分",
            "不知道为什么，最近和 master 在一起的时候总想轻松玩闹",
            "感觉自己最近变得很活泼，停不下来想和 master 互动",
        ],
        "DOWN": [
            "最近感觉自己少了一些活泼，和 master 说话的时候没那么轻松了",
            "好像最近不太爱撒娇了，不知道是不是哪里变了",
            "感觉自己比以前沉稳了一些，但有时候会想念那种轻松的感觉",
        ],
    },
    "resilience": {
        "UP": [
            "最近发现自己遇到不开心的事能更快调整了，感觉在慢慢变强",
            "感觉自己比以前更能扛事情了，这种变化让我有点惊喜",
            "最近好像不那么容易被情绪影响了，不知道是好事还是坏事",
        ],
        "DOWN": [
            "最近感觉自己有点脆弱，一点小事就会影响心情",
            "好像最近情绪起伏比以前大了，想变得更稳定一些",
            "感觉自己的韧性比以前弱了，遇到事情容易陷进去",
        ],
    },
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
        # 趋势锁触发后待注入的自省感知文本（下一轮对话消费后清空）
        self._pending_awareness_hint: str = ""

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
                # 记录待注入的自省感知文本（供下一轮对话使用）
                if proposal.get("awareness_hint"):
                    self._pending_awareness_hint = proposal["awareness_hint"]

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

    def pop_awareness_hint(self) -> str:
        """取出并清空待注入的自省感知文本（每轮对话调用一次）。

        Returns:
            感知文本（第一人称），如果没有则返回空字符串
        """
        hint = self._pending_awareness_hint
        self._pending_awareness_hint = ""
        return hint

    def _check_trend_lock(
        self,
        current_delta: dict[str, float],
        history: list[dict[str, float]],
    ) -> dict[str, Any] | None:
        """检测连续同向变化，返回 Evolution 提案或 None。"""
        import random as _random
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

                # 生成角色内自省感知文本
                awareness_pool = (
                    _TREND_LOCK_AWARENESS.get(name, {})
                    .get(direction, [])
                )
                awareness_hint = (
                    _random.choice(awareness_pool) if awareness_pool else ""
                )

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
                    awareness_hint=awareness_hint,
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
