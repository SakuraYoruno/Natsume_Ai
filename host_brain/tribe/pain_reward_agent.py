"""PainRewardAgent — 痛觉/奖励系统（T-13）

人体类比：痛觉感受器 + 多巴胺/内啡肽奖励回路
核心职责：
  1. 记录痛觉事件（负向刺激：被拒绝、被忽视、被攻击、话题受挫）
  2. 记录奖励事件（正向刺激：被认可、被关心、有趣对话、情感共鸣）
  3. 维护 hedonic_state（享乐状态）：pain_level / reward_level / net_valence
  4. 向 EndocrineAgent 提供压力信号（pain 累积 → cortisol 上升）
  5. 向 LimbicAgent 提供情绪调制信号（reward → 积极情绪偏置）
  6. 订阅 INTERACTION_RECORDED / EMOTION_UPDATED 事件，自动更新状态

依赖：
  - EndocrineAgent（写入压力信号）
  - TribeStateRegistry（写入 hedonic_state 快照）
  - TribeEventBus（订阅事件）
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry

# T-27：持久化文件路径（可通过环境变量覆盖）
_DEFAULT_STATE_PATH = Path(
    os.environ.get("NATSUME_PAIN_REWARD_STATE_PATH", "host_brain/data/pain_reward_state.json")
)
# 节流：每 N 次 flush 才写一次磁盘（避免频繁 IO）
_PERSIST_THROTTLE = 5


# ── 痛觉/奖励事件类型 ────────────────────────────────────────────────────────

PAIN_SOURCES = frozenset({
    "rejection",        # 被拒绝
    "ignored",          # 被忽视
    "attacked",         # 被攻击/侮辱
    "topic_blocked",    # 话题受挫（ImmuneAgent BLOCK）
    "bond_regress",     # 关系退步
    "loneliness",       # 孤独感
    "misunderstood",    # 被误解
})

REWARD_SOURCES = frozenset({
    "recognized",       # 被认可/夸奖
    "cared_for",        # 被关心
    "interesting_chat", # 有趣对话
    "emotional_sync",   # 情感共鸣
    "bond_progress",    # 关系进展
    "task_success",     # 任务成功
    "humor_landed",     # 幽默成功
})

# 衰减系数：每次 tick（对话轮次）后，pain/reward 向基线衰减
_DECAY_RATE = 0.12   # 每轮衰减 12%
_PAIN_CEILING = 1.0
_REWARD_CEILING = 1.0
_PAIN_FLOOR = 0.0
_REWARD_FLOOR = 0.0


@dataclass(slots=True)
class PainRewardAgent:
    """痛觉/奖励系统 Agent（T-13）。

    维护 hedonic_state：
      pain_level (float 0~1): 当前痛觉强度
      reward_level (float 0~1): 当前奖励强度
      net_valence (float -1~1): 净享乐效价（reward - pain）
      cumulative_pain (float): 累积痛觉（不衰减，用于长期压力评估）
      cumulative_reward (float): 累积奖励（不衰减，用于长期满足感评估）
      last_pain_source (str | None): 最近一次痛觉来源
      last_reward_source (str | None): 最近一次奖励来源
      ts_ms (int): 最近更新时间戳
    """

    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="pain_reward_agent",
            family="pain_reward",
            capabilities=("pain_sensing", "reward_sensing", "hedonic_state", "stress_signal"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)  # T-29：使用全局单例

    _pain_level: float = field(default=0.0, init=False, repr=False)
    _reward_level: float = field(default=0.0, init=False, repr=False)
    _cumulative_pain: float = field(default=0.0, init=False, repr=False)
    _cumulative_reward: float = field(default=0.0, init=False, repr=False)
    _last_pain_source: str | None = field(default=None, init=False, repr=False)
    _last_reward_source: str | None = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    # T-27：持久化节流计数器
    _flush_counter: int = field(default=0, init=False, repr=False)
    _state_path: Path = field(default_factory=lambda: _DEFAULT_STATE_PATH, init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    # ── 核心接口 ─────────────────────────────────────────────────────────────

    def record_pain(
        self,
        *,
        source: str,
        intensity: float = 0.3,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """记录一次痛觉事件，更新 pain_level 并写入 state_registry。

        Args:
            source: 痛觉来源（建议使用 PAIN_SOURCES 中的值）
            intensity: 痛觉强度 0.0 ~ 1.0（默认 0.3 = 轻度）
            metadata: 附加元数据（request_id / session_id 等）

        Returns:
            更新后的 hedonic_state
        """
        intensity = max(0.0, min(1.0, float(intensity)))
        with self._lock:
            # 痛觉叠加（非线性：已有痛觉时新增痛觉效果减弱）
            _existing = self._pain_level
            _delta = intensity * (1.0 - _existing * 0.5)
            self._pain_level = min(_PAIN_CEILING, _existing + _delta)
            self._cumulative_pain += intensity
            self._last_pain_source = str(source or "unknown").strip()
        state = self._flush_state(event_type="pain", source=source, metadata=metadata)
        return state

    def record_reward(
        self,
        *,
        source: str,
        intensity: float = 0.4,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """记录一次奖励事件，更新 reward_level 并写入 state_registry。

        Args:
            source: 奖励来源（建议使用 REWARD_SOURCES 中的值）
            intensity: 奖励强度 0.0 ~ 1.0（默认 0.4 = 中度）
            metadata: 附加元数据

        Returns:
            更新后的 hedonic_state
        """
        intensity = max(0.0, min(1.0, float(intensity)))
        with self._lock:
            _existing = self._reward_level
            _delta = intensity * (1.0 - _existing * 0.3)
            self._reward_level = min(_REWARD_CEILING, _existing + _delta)
            self._cumulative_reward += intensity
            self._last_reward_source = str(source or "unknown").strip()
        state = self._flush_state(event_type="reward", source=source, metadata=metadata)

        # T-349：奖励事件后联动 DefaultModeAgent.tick_idle_cycle()
        # 奖励强化自我连续感（"我做到了"是自我叙事的核心）
        if intensity >= 0.25:
            try:
                from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
                _dma = get_default_mode_agent()
                if hasattr(_dma, "tick_idle_cycle"):
                    _dma.tick_idle_cycle(
                        source=f"pain_reward_reward:{str(source or 'unknown')[:32]}",
                        person_id="user_1",
                    )
            except Exception:
                pass

        return state

    def record_reward_event(
        self,
        *,
        source: str,
        reward_level: float = 0.4,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """T-324：record_reward 的别名方法，支持 reward_level 参数名。

        供 ProprioceptionAgent（T-316）、InitiativeGovernorAgent（T-317）、
        SocialBrainAgent（T-318）等外部族调用，统一使用 reward_level 语义。

        Args:
            source: 奖励来源标识
            reward_level: 奖励强度 0.0 ~ 1.0（默认 0.4）
            metadata: 附加元数据

        Returns:
            更新后的 hedonic_state
        """
        return self.record_reward(
            source=source,
            intensity=reward_level,
            metadata=metadata,
        )

    def tick_decay(self) -> dict[str, Any]:
        """每轮对话结束后调用，对 pain/reward 进行自然衰减（向基线靠拢）。

        Returns:
            衰减后的 hedonic_state
        """
        with self._lock:
            self._pain_level = max(_PAIN_FLOOR, self._pain_level * (1.0 - _DECAY_RATE))
            self._reward_level = max(_REWARD_FLOOR, self._reward_level * (1.0 - _DECAY_RATE))
        return self._flush_state(event_type="decay", source="tick")

    def get_hedonic_state(self) -> dict[str, Any]:
        """获取当前享乐状态快照（不触发写入）。"""
        with self._lock:
            return self._build_state_dict()

    def get_loneliness_level(self) -> float:
        """T-64：返回当前孤独感强度（0.0 ~ 1.0）。

        孤独感由 _on_loneliness_signal() 写入 _loneliness_level 字段，
        供 ANSAgent.inner_world_skill 和诊断端点查询使用。

        Returns:
            float: 当前孤独感强度，0.0 表示无孤独感，1.0 表示极度孤独。
                   若从未收到 LONELINESS_SIGNAL 事件则返回 0.0。
        """
        return float(getattr(self, "_loneliness_level", 0.0))

    def get_stress_signal(self) -> dict[str, Any]:
        """向 EndocrineAgent 提供压力信号。

        Returns:
            pain_stress_delta (float): 痛觉引起的皮质醇增量建议（0.0 ~ 0.3）
            reward_relief_delta (float): 奖励引起的皮质醇减量建议（0.0 ~ 0.2）
            net_stress_delta (float): 净压力变化量
        """
        with self._lock:
            pain = self._pain_level
            reward = self._reward_level
        pain_stress = round(pain * 0.3, 4)
        reward_relief = round(reward * 0.2, 4)
        return {
            "pain_stress_delta": pain_stress,
            "reward_relief_delta": reward_relief,
            "net_stress_delta": round(pain_stress - reward_relief, 4),
        }

    def get_emotion_modulation(self) -> dict[str, Any]:
        """向 LimbicAgent 提供情绪调制信号。

        Returns:
            valence_bias (float -1~1): 情绪效价偏置（正 = 积极偏置，负 = 消极偏置）
            arousal_boost (float 0~1): 唤醒度提升（pain/reward 均会提升唤醒）
            suggested_emotion_tag (str | None): 建议的情绪标签（仅在极端状态时）
        """
        with self._lock:
            pain = self._pain_level
            reward = self._reward_level
        net_valence = reward - pain
        arousal = min(1.0, (pain + reward) * 0.6)
        suggested_tag: str | None = None
        if pain >= 0.7:
            suggested_tag = "HURT"
        elif pain >= 0.5 and reward < 0.2:
            suggested_tag = "SAD"
        elif reward >= 0.7:
            suggested_tag = "JOY"
        elif reward >= 0.5 and pain < 0.2:
            suggested_tag = "CONTENT"
        return {
            "valence_bias": round(net_valence, 4),
            "arousal_boost": round(arousal, 4),
            "suggested_emotion_tag": suggested_tag,
        }

    # ── TribeEventBus 订阅注册 ───────────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        在 http_hooks.py lifespan 中调用，确保 Bus 的 dispatch_loop 启动后
        PainRewardAgent 的 handler 能被正确触发。

        订阅事件：
          - INTERACTION_RECORDED：每轮对话结束后触发衰减
          - EMOTION_UPDATED：情绪更新时推断痛觉/奖励
          - IMMUNE_BLOCK：ImmuneAgent 拦截时记录 topic_blocked 痛觉
          - BOND_PROGRESS：关系进展时记录 bond_progress 奖励（T-24）
          - BOND_REGRESS：关系退步时记录 bond_regress 痛觉（T-24）
          - TASK_SUCCESS：任务成功时记录 task_success 奖励（T-24）
          - HUMOR_LANDED：幽默成功时记录 humor_landed 奖励（T-24）
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
            bus.subscribe("EMOTION_UPDATED", self._on_emotion_updated)
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block)
            # T-24：新增事件源
            bus.subscribe("BOND_PROGRESS", self._on_bond_progress)
            bus.subscribe("BOND_REGRESS", self._on_bond_regress)
            bus.subscribe("TASK_SUCCESS", self._on_task_success)
            bus.subscribe("HUMOR_LANDED", self._on_humor_landed)
            # T-40：孤独感信号
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.pain_reward_agent").warning(
                "PainRewardAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_interaction_recorded(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：触发 pain/reward 自然衰减。"""
        try:
            self.tick_decay()
        except Exception:
            pass

    async def _on_emotion_updated(self, event: Any) -> None:
        """处理 EMOTION_UPDATED 事件：根据情绪标签推断痛觉/奖励。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            emotion_tag = str(payload.get("emotion_tag") or "").strip().upper()
            intensity = float(payload.get("emotion_intensity") or 0.3)
            if emotion_tag in ("HURT", "SAD", "ANGRY", "FEAR", "DISGUST"):
                self.record_pain(
                    source="emotion_negative",
                    intensity=intensity * 0.5,
                    metadata={"emotion_tag": emotion_tag},
                )
            elif emotion_tag in ("JOY", "CONTENT", "EXCITED", "LOVE", "AMUSED"):
                self.record_reward(
                    source="emotion_positive",
                    intensity=intensity * 0.5,
                    metadata={"emotion_tag": emotion_tag},
                )
        except Exception:
            pass

    async def _on_immune_block(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：记录 topic_blocked 痛觉。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            self.record_pain(
                source="topic_blocked",
                intensity=0.2,
                metadata=payload,
            )
        except Exception:
            pass

    # ── T-24：新增事件源 handler ──────────────────────────────────────────────

    async def _on_bond_progress(self, event: Any) -> None:
        """处理 BOND_PROGRESS 事件：关系进展 → bond_progress 奖励。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            # 根据 bond_stage 变化幅度调整奖励强度（默认 0.5）
            intensity = float(payload.get("intensity") or 0.5)
            self.record_reward(
                source="bond_progress",
                intensity=min(1.0, intensity),
                metadata=payload,
            )
        except Exception:
            pass

    async def _on_bond_regress(self, event: Any) -> None:
        """处理 BOND_REGRESS 事件：关系退步 → bond_regress 痛觉。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            intensity = float(payload.get("intensity") or 0.4)
            self.record_pain(
                source="bond_regress",
                intensity=min(1.0, intensity),
                metadata=payload,
            )
        except Exception:
            pass

    async def _on_task_success(self, event: Any) -> None:
        """处理 TASK_SUCCESS 事件：任务成功 → task_success 奖励。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            intensity = float(payload.get("intensity") or 0.35)
            self.record_reward(
                source="task_success",
                intensity=min(1.0, intensity),
                metadata=payload,
            )
        except Exception:
            pass

    async def _on_humor_landed(self, event: Any) -> None:
        """处理 HUMOR_LANDED 事件：幽默成功 → humor_landed 奖励。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            intensity = float(payload.get("intensity") or 0.3)
            self.record_reward(
                source="humor_landed",
                intensity=min(1.0, intensity),
                metadata=payload,
            )
        except Exception:
            pass

    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：孤独感 → loneliness 痛觉。

        T-40：ANSAgent 的 inner_world_skill 在检测到高孤独感时发布此事件，
        PainRewardAgent 订阅后记录 loneliness 痛觉，影响 hedonic_state。
        孤独感强度与 pain_level 正相关，但上限为 0.6（避免单次事件过度影响）。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.4)))
            # 孤独感 > 0.5 才触发痛觉（低孤独感属于正常状态）
            if loneliness > 0.5:
                intensity = min(0.6, (loneliness - 0.5) * 1.2)  # 0.5~1.0 → 0.0~0.6
                self.record_pain(
                    source="loneliness",
                    intensity=intensity,
                    metadata={"loneliness": loneliness, **payload},
                )
        except Exception:
            pass

    # ── 事件订阅（TribeEventBus）────────────────────────────────────────────

    def on_tribe_event(self, event: Any) -> None:
        """处理 TribeEventBus 事件。

        订阅：
          - INTERACTION_RECORDED：每轮对话结束后触发衰减
          - EMOTION_UPDATED：情绪更新时，根据情绪标签推断痛觉/奖励
          - IMMUNE_BLOCK：ImmuneAgent 拦截时记录 topic_blocked 痛觉
        """
        try:
            event_type = str(getattr(event, "event_type", "") or "").strip().upper()
            payload = dict(getattr(event, "payload", None) or {})

            if event_type == "INTERACTION_RECORDED":
                self.tick_decay()

            elif event_type == "EMOTION_UPDATED":
                emotion_tag = str(payload.get("emotion_tag") or "").strip().upper()
                intensity = float(payload.get("emotion_intensity") or 0.3)
                # 负向情绪 → 痛觉信号
                if emotion_tag in ("HURT", "SAD", "ANGRY", "FEAR", "DISGUST"):
                    self.record_pain(
                        source="emotion_negative",
                        intensity=intensity * 0.5,
                        metadata={"emotion_tag": emotion_tag},
                    )
                # 正向情绪 → 奖励信号
                elif emotion_tag in ("JOY", "CONTENT", "EXCITED", "LOVE", "AMUSED"):
                    self.record_reward(
                        source="emotion_positive",
                        intensity=intensity * 0.5,
                        metadata={"emotion_tag": emotion_tag},
                    )

            elif event_type == "IMMUNE_BLOCK":
                self.record_pain(
                    source="topic_blocked",
                    intensity=0.2,
                    metadata=payload,
                )

            # T-24：新增事件源
            elif event_type == "BOND_PROGRESS":
                intensity = float(payload.get("intensity") or 0.5)
                self.record_reward(
                    source="bond_progress",
                    intensity=min(1.0, intensity),
                    metadata=payload,
                )

            elif event_type == "BOND_REGRESS":
                intensity = float(payload.get("intensity") or 0.4)
                self.record_pain(
                    source="bond_regress",
                    intensity=min(1.0, intensity),
                    metadata=payload,
                )

            elif event_type == "TASK_SUCCESS":
                intensity = float(payload.get("intensity") or 0.35)
                self.record_reward(
                    source="task_success",
                    intensity=min(1.0, intensity),
                    metadata=payload,
                )

            elif event_type == "HUMOR_LANDED":
                intensity = float(payload.get("intensity") or 0.3)
                self.record_reward(
                    source="humor_landed",
                    intensity=min(1.0, intensity),
                    metadata=payload,
                )

        except Exception:
            pass

    # ── 内部方法 ─────────────────────────────────────────────────────────────

    def _build_state_dict(self) -> dict[str, Any]:
        """构建当前状态字典（需在 _lock 内调用）。"""
        pain = self._pain_level
        reward = self._reward_level
        net_valence = round(reward - pain, 4)
        return {
            "pain_level": round(pain, 4),
            "reward_level": round(reward, 4),
            "net_valence": net_valence,
            "cumulative_pain": round(self._cumulative_pain, 4),
            "cumulative_reward": round(self._cumulative_reward, 4),
            "last_pain_source": self._last_pain_source,
            "last_reward_source": self._last_reward_source,
            "hedonic_tone": _classify_hedonic_tone(net_valence),
            "ts_ms": now_ms(),
        }

    def _flush_state(
        self,
        *,
        event_type: str,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建状态字典并写入 state_registry。

        T-27：每 _PERSIST_THROTTLE 次 flush 触发一次磁盘持久化，
        确保重启后 hedonic_state 不归零。
        """
        with self._lock:
            state = self._build_state_dict()
            self._flush_counter += 1
            should_persist = (self._flush_counter % _PERSIST_THROTTLE == 0)
        self.state_registry.set_state(
            "pain_reward_agent",
            {**state, "last_event_type": event_type, "last_event_source": source},
            writer="pain_reward_agent",
        )
        # T-27：节流持久化到磁盘
        if should_persist:
            self.save_state()

        # T-215：高痛觉时主动驱动 LimbicAgent 情绪偏移
        pain_level = float(state.get("pain_level") or 0.0)
        reward_level = float(state.get("reward_level") or 0.0)
        if event_type == "pain" and pain_level >= 0.55:
            try:
                from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                limbic = get_limbic_agent()
                if hasattr(limbic, "drive_emotion_metabolism"):
                    valence_shift = -((pain_level - 0.55) * 1.2)   # 最大 -0.54
                    arousal_shift = (pain_level - 0.55) * 0.6       # 最大 +0.27
                    limbic.drive_emotion_metabolism(
                        valence=round(valence_shift, 4),
                        arousal=round(arousal_shift, 4),
                        dominance=0.0,
                        blend_weight=0.30,
                        event_kind=f"pain_overload:{source}",
                        source="pain_reward_agent",
                    )
            except Exception:
                pass
        elif event_type == "reward" and reward_level >= 0.60:
            try:
                from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                limbic = get_limbic_agent()
                if hasattr(limbic, "drive_emotion_metabolism"):
                    valence_shift = (reward_level - 0.60) * 0.9    # 最大 +0.36
                    limbic.drive_emotion_metabolism(
                        valence=round(valence_shift, 4),
                        arousal=0.05,
                        dominance=0.0,
                        blend_weight=0.25,
                        event_kind=f"reward_surge:{source}",
                        source="pain_reward_agent",
                    )
            except Exception:
                pass

        # T-216：累积痛觉实际写入 EndocrineAgent cortisol
        cumulative_pain = float(state.get("cumulative_pain") or 0.0)
        if event_type == "pain" and cumulative_pain >= 1.0:
            try:
                from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415
                endocrine = get_endocrine_agent()
                if hasattr(endocrine, "record_stress_event"):
                    # 将累积痛觉转换为压力事件强度（对数压缩，避免无限增长）
                    import math  # noqa: PLC0415
                    stress_intensity = min(0.85, math.log1p(cumulative_pain) * 0.25)
                    endocrine.record_stress_event(
                        source=f"pain_reward:{source}",
                        intensity=round(stress_intensity, 4),
                    )
            except Exception:
                pass

        # T-306：高奖励(reward_level>=0.55)时联动 InteroceptionAgent 降低 recovery_urgency（奖励是正向信号）
        reward_level = float(state.get("reward_level") or 0.0)
        if event_type == "reward" and reward_level >= 0.55:
            try:
                from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                _intero = get_interoception_agent()
                if hasattr(_intero, "reduce_recovery_urgency"):
                    _relief = round(min(0.45, (reward_level - 0.55) * 0.60), 4)
                    _intero.reduce_recovery_urgency(
                        source=f"pain_reward_high_reward:{source}",
                        relief=_relief,
                    )
            except Exception:
                pass

        return state

    # ── T-27：持久化方法 ─────────────────────────────────────────────────────

    def save_state(self) -> bool:
        """将当前 hedonic_state 持久化到磁盘 JSON 文件。

        Returns:
            True 表示保存成功，False 表示保存失败（降级，不抛异常）。
        """
        try:
            with self._lock:
                persist_data = {
                    "pain_level": self._pain_level,
                    "reward_level": self._reward_level,
                    "cumulative_pain": self._cumulative_pain,
                    "cumulative_reward": self._cumulative_reward,
                    "last_pain_source": self._last_pain_source,
                    "last_reward_source": self._last_reward_source,
                    "saved_at_ms": now_ms(),
                }
            path = self._state_path
            path.parent.mkdir(parents=True, exist_ok=True)
            # 原子写入：先写临时文件，再 rename
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(persist_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return True
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.pain_reward_agent").debug(
                "PainRewardAgent.save_state 失败（降级）: %s", exc
            )
            return False

    def load_state(self) -> bool:
        """从磁盘 JSON 文件加载 hedonic_state，恢复跨会话状态。

        Returns:
            True 表示加载成功，False 表示文件不存在或加载失败（降级，不抛异常）。
        """
        try:
            path = self._state_path
            if not path.exists():
                return False
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            with self._lock:
                self._pain_level = max(0.0, min(1.0, float(data.get("pain_level") or 0.0)))
                self._reward_level = max(0.0, min(1.0, float(data.get("reward_level") or 0.0)))
                self._cumulative_pain = max(0.0, float(data.get("cumulative_pain") or 0.0))
                self._cumulative_reward = max(0.0, float(data.get("cumulative_reward") or 0.0))
                _lps = data.get("last_pain_source")
                self._last_pain_source = str(_lps).strip() if _lps else None
                _lrs = data.get("last_reward_source")
                self._last_reward_source = str(_lrs).strip() if _lrs else None
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.pain_reward_agent").info(
                "PainRewardAgent 加载持久化状态: pain=%.3f reward=%.3f cumulative_pain=%.3f",
                self._pain_level, self._reward_level, self._cumulative_pain,
            )
            return True
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.pain_reward_agent").debug(
                "PainRewardAgent.load_state 失败（降级）: %s", exc
            )
            return False

    # ── 享乐状态反思（A 级升级）──────────────────────────────────────────────

    def generate_hedonic_reflection(
        self,
        *,
        delta_valence: float = 0.0,
        trigger_event: str = "",
        net_valence: float = 0.0,
        bond_stage: str = "stranger",
    ) -> str | None:
        """享乐状态显著变化时调用 LLM 生成内心感受叙事。

        触发条件：|delta_valence| > 0.3。
        调用：Flash 小模型。
        开关：PAIN_HEDONIC_REFLECTION_ENABLED（默认 false）。
        输出写入：DefaultModeAgent 独白素材池。

        Returns:
            内心感受叙事字符串，或 None（未触发/失败）。
        """
        import os as _os  # noqa: PLC0415
        _enabled = str(_os.environ.get("PAIN_HEDONIC_REFLECTION_ENABLED", "false")).lower() in ("1", "true", "yes")
        if not _enabled or abs(float(delta_valence or 0.0)) <= 0.3:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.pain_reward_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("pain_reward_agent.hedonic_reflection") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("pain_reward_agent.hedonic_reflection 模板未配置")

            _user_prompt = _user_tpl.format(
                delta_valence=float(delta_valence),
                trigger_event=str(trigger_event or "")[:80],
                net_valence=float(net_valence),
                bond_stage=str(bond_stage or "stranger"),
            )
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=2000,
                agent_role="hedonic_reflector",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=64,
                temperature=0.3,
            )
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _reflection = str(_parsed.get("hedonic_reflection") or "").strip()[:60]
            if _reflection:
                _log.debug("PainRewardAgent.generate_hedonic_reflection: %s", _reflection)
                # 写入 state_registry 供 DefaultModeAgent 读取
                try:
                    self.state_registry.set_state(
                        "pain_reward_agent",
                        {"last_hedonic_reflection": _reflection},
                        writer="pain_reward_agent",
                    )
                except Exception:
                    pass
            return _reflection or None
        except Exception as exc:
            _log.debug("PainRewardAgent.generate_hedonic_reflection 失败（降级）: %s", exc)
            return None

    def export_runtime_state(self) -> dict[str, Any]:
        return {
            "state": self.get_hedonic_state(),
            "stress_signal": self.get_stress_signal(),
            "emotion_modulation": self.get_emotion_modulation(),
        }


def _classify_hedonic_tone(net_valence: float) -> str:
    """将净效价分类为享乐基调标签。"""
    if net_valence >= 0.5:
        return "euphoric"
    elif net_valence >= 0.2:
        return "pleasant"
    elif net_valence >= -0.1:
        return "neutral"
    elif net_valence >= -0.4:
        return "dysphoric"
    else:
        return "suffering"


_PAIN_REWARD_AGENT: PainRewardAgent | None = None


def get_pain_reward_agent() -> PainRewardAgent:
    global _PAIN_REWARD_AGENT
    if _PAIN_REWARD_AGENT is None:
        _PAIN_REWARD_AGENT = PainRewardAgent()
        # T-27：启动时自动加载持久化状态，避免重启后 hedonic_state 归零
        _PAIN_REWARD_AGENT.load_state()
    return _PAIN_REWARD_AGENT


def set_pain_reward_agent(agent: PainRewardAgent | None) -> None:
    global _PAIN_REWARD_AGENT
    _PAIN_REWARD_AGENT = agent


__all__ = [
    "PainRewardAgent",
    "get_pain_reward_agent",
    "set_pain_reward_agent",
    "PAIN_SOURCES",
    "REWARD_SOURCES",
]
