"""EmotionPersistence：跨会话情感连续性——Phase VII。

职责：
  1. 会话结束时持久化当前情感向量
  2. 新会话开始时恢复情感向量（带离线衰减模型）
  3. 衰减模型：指数衰减向基线回归（约 7 小时半衰期）

衰减模型特性：
  - 离线 1 小时：保留约 90% 上次情感
  - 离线 7 小时（半衰期）：保留约 50%
  - 离线 24 小时：保留约 9%，接近基线
  - 离线 48 小时+：完全回归基线

设计原则：
  - 持久化到内存字典（可扩展为 sqlite_ledger）
  - 恢复时自动应用衰减
  - 无持久化记录时从中性基线启动
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.emotion_persistence")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 衰减率：约 7 小时半衰期
# exp(-0.1 * 7) ≈ 0.4966，即 7 小时后保留约 50%
DECAY_RATE = 0.1

# 中性基线
DEFAULT_MOOD_BASELINE = 0.0
DEFAULT_BASELINE_AROUSAL = 0.3
DEFAULT_BASELINE_DOMINANCE = 0.0


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class PersistedEmotionState:
    """持久化的情感状态。"""
    user_id: str
    valence: float = 0.0       # 上次会话结束时的效价
    arousal: float = 0.3       # 上次会话结束时的唤醒度
    dominance: float = 0.0     # 上次会话结束时的支配感
    last_session_end_ts_ms: int = 0  # 上次会话结束时间
    mood_baseline: float = DEFAULT_MOOD_BASELINE  # 长期心情基线

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4),
            "last_session_end_ts_ms": self.last_session_end_ts_ms,
            "mood_baseline": round(self.mood_baseline, 4),
        }


@dataclass(frozen=True)
class RestoredEmotionState:
    """恢复后的情感状态（含衰减信息）。"""
    valence: float
    arousal: float
    dominance: float
    decay_factor: float         # 衰减因子（0~1，1=完全保留）
    hours_elapsed: float        # 离线时长（小时）
    from_baseline: bool         # 是否从基线启动（无持久化记录）

    def as_dict(self) -> dict[str, Any]:
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4),
            "decay_factor": round(self.decay_factor, 4),
            "hours_elapsed": round(self.hours_elapsed, 2),
            "from_baseline": self.from_baseline,
        }


# ---------------------------------------------------------------------------
# EmotionPersistence
# ---------------------------------------------------------------------------

class EmotionPersistence:
    """跨会话情感连续性管理器。

    用法：
        persistence = EmotionPersistence()

        # 会话结束时保存
        persistence.persist(
            user_id="user1",
            valence=0.6, arousal=0.4, dominance=0.1,
        )

        # 新会话开始时恢复
        restored = persistence.restore(user_id="user1")
        # restored.valence 已经应用了离线衰减
    """

    def __init__(
        self,
        *,
        decay_rate: float = DECAY_RATE,
        baseline_valence: float = DEFAULT_MOOD_BASELINE,
        baseline_arousal: float = DEFAULT_BASELINE_AROUSAL,
        baseline_dominance: float = DEFAULT_BASELINE_DOMINANCE,
    ) -> None:
        self._decay_rate = float(decay_rate)
        self._baseline_valence = float(baseline_valence)
        self._baseline_arousal = float(baseline_arousal)
        self._baseline_dominance = float(baseline_dominance)
        # user_id → PersistedEmotionState
        self._states: dict[str, PersistedEmotionState] = {}

    # ── 持久化 ────────────────────────────────────────────────────────────

    def persist(
        self,
        *,
        user_id: str,
        valence: float = 0.0,
        arousal: float = 0.3,
        dominance: float = 0.0,
        ts_ms: int = 0,
        mood_baseline: float | None = None,
    ) -> PersistedEmotionState:
        """会话结束时持久化当前情感向量。

        Args:
            user_id: 用户 ID
            valence: 当前效价
            arousal: 当前唤醒度
            dominance: 当前支配感
            ts_ms: 会话结束时间戳（0=使用当前时间）
            mood_baseline: 长期心情基线（None=使用之前的值或默认值）

        Returns:
            持久化后的状态
        """
        uid = str(user_id).strip()
        ts = int(ts_ms) if ts_ms else _now_ms()

        # 获取或创建状态
        existing = self._states.get(uid)
        baseline = (
            mood_baseline
            if mood_baseline is not None
            else (existing.mood_baseline if existing else self._baseline_valence)
        )

        state = PersistedEmotionState(
            user_id=uid,
            valence=float(valence),
            arousal=float(arousal),
            dominance=float(dominance),
            last_session_end_ts_ms=ts,
            mood_baseline=float(baseline),
        )
        self._states[uid] = state

        _logger.info(
            "情感持久化: user=%s v=%.3f a=%.3f d=%.3f ts=%d",
            uid, valence, arousal, dominance, ts,
        )
        return state

    # ── 恢复 ──────────────────────────────────────────────────────────────

    def restore(
        self,
        *,
        user_id: str,
        current_ts_ms: int = 0,
    ) -> RestoredEmotionState:
        """新会话开始时恢复情感向量（带离线衰减）。

        Args:
            user_id: 用户 ID
            current_ts_ms: 当前时间戳（0=使用当前时间）

        Returns:
            恢复后的情感状态
        """
        uid = str(user_id).strip()
        now_ts = int(current_ts_ms) if current_ts_ms else _now_ms()

        state = self._states.get(uid)
        if state is None or state.last_session_end_ts_ms <= 0:
            # 无持久化记录：从中性基线启动
            _logger.info("情感恢复: user=%s 无持久化记录，从基线启动", uid)
            return RestoredEmotionState(
                valence=self._baseline_valence,
                arousal=self._baseline_arousal,
                dominance=self._baseline_dominance,
                decay_factor=0.0,
                hours_elapsed=0.0,
                from_baseline=True,
            )

        # 计算离线时长
        elapsed_ms = max(0, now_ts - state.last_session_end_ts_ms)
        hours_elapsed = elapsed_ms / (3600.0 * 1000.0)

        # 计算衰减因子
        decay_factor = math.exp(-self._decay_rate * hours_elapsed)
        decay_factor = max(0.0, min(1.0, decay_factor))

        # 恢复：向基线回归
        restored_valence = (
            state.valence * decay_factor
            + state.mood_baseline * (1.0 - decay_factor)
        )
        restored_arousal = (
            state.arousal * decay_factor
            + self._baseline_arousal * (1.0 - decay_factor)
        )
        restored_dominance = (
            state.dominance * decay_factor
            + self._baseline_dominance * (1.0 - decay_factor)
        )

        _logger.info(
            "情感恢复: user=%s hours=%.1f decay=%.3f v=%.3f→%.3f",
            uid, hours_elapsed, decay_factor,
            state.valence, restored_valence,
        )

        return RestoredEmotionState(
            valence=round(restored_valence, 4),
            arousal=round(restored_arousal, 4),
            dominance=round(restored_dominance, 4),
            decay_factor=round(decay_factor, 4),
            hours_elapsed=round(hours_elapsed, 2),
            from_baseline=False,
        )

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_persisted_state(self, user_id: str) -> PersistedEmotionState | None:
        """获取用户持久化的情感状态（不应用衰减）。"""
        return self._states.get(str(user_id).strip())

    def has_persisted_state(self, user_id: str) -> bool:
        """检查用户是否有持久化的情感状态。"""
        uid = str(user_id).strip()
        state = self._states.get(uid)
        return state is not None and state.last_session_end_ts_ms > 0

    # ── 清理 ──────────────────────────────────────────────────────────────

    def clear(self, user_id: str) -> None:
        """清除用户持久化的情感状态。"""
        uid = str(user_id).strip()
        self._states.pop(uid, None)


def _now_ms() -> int:
    return int(time.time() * 1000)
