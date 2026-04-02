"""EmotionPersistence：跨会话情感连续性——Phase VII + 情感惯性模型。

职责：
  1. 会话结束时持久化当前情感向量
  2. 新会话开始时恢复情感向量（带离线衰减模型）
  3. 衰减模型：指数衰减向基线回归（约 7 小时半衰期）
  4. 情感惯性：连续多天同向情感时衰减更慢（情感有"重量"）

衰减模型特性：
  - 离线 1 小时：保留约 90% 上次情感
  - 离线 7 小时（半衰期）：保留约 50%
  - 离线 24 小时：保留约 9%，接近基线
  - 离线 48 小时+：完全回归基线

情感惯性模型：
  - 连续 1 天同向情感：无惯性加成（正常衰减）
  - 连续 2 天：衰减率 × 0.85（衰减略慢）
  - 连续 3 天：衰减率 × 0.70（衰减明显变慢）
  - 连续 5 天+：衰减率 × 0.50（半衰期翻倍，情感根深蒂固）
  - 惯性方向：正向（valence > 0.1）/ 负向（valence < -0.1）/ 中性
  - 方向翻转时惯性重置为 0

设计原则：
  - 持久化到 JSON 文件（进程重启后可恢复）
  - 恢复时自动应用衰减 + 惯性调整
  - 无持久化记录时从中性基线启动
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger("Natsume.emotion_persistence")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 衰减率：约 7 小时半衰期
# exp(-0.1 * 7) ≈ 0.4966，即 7 小时后保留约 50%
# 可通过环境变量 HOST_EMOTION_PERSISTENCE_DECAY_RATE 覆盖
DECAY_RATE = float(os.environ.get("HOST_EMOTION_PERSISTENCE_DECAY_RATE", "0.1"))

# 情感记忆锚点：极强情感的衰减减缓系数（值越小衰减越慢）
# 当上次情感效价绝对值 >= 此阈值时，视为「强情感记忆」，衰减率乘以此系数
_ANCHOR_VALENCE_THRESHOLD = 0.6   # 效价绝对值 >= 0.6 触发锚点
_ANCHOR_DECAY_MULTIPLIER = 0.5    # 衰减率 × 0.5，即半衰期翻倍（约 14 小时）

# 久别重逢阈值（小时）
_REUNION_THRESHOLD_HOURS = 7 * 24   # >= 7 天视为「久别重逢」
_SHORT_ABSENCE_THRESHOLD_HOURS = 3 * 24  # >= 3 天视为「短暂分离」

# 中性基线（可通过环境变量覆盖）
DEFAULT_MOOD_BASELINE = float(os.environ.get("HOST_EMOTION_BASELINE_VALENCE", "0.0"))
DEFAULT_BASELINE_AROUSAL = float(os.environ.get("HOST_EMOTION_BASELINE_AROUSAL", "0.3"))
DEFAULT_BASELINE_DOMINANCE = float(os.environ.get("HOST_EMOTION_BASELINE_DOMINANCE", "0.0"))

# 默认持久化文件路径（相对于项目 data 目录）
_DEFAULT_PERSISTENCE_DIR = os.environ.get(
    "NATSUME_EMOTION_PERSISTENCE_DIR",
    os.path.join(os.path.expanduser("~"), ".natsume", "emotion"),
)

# ── 情感惯性常量 ─────────────────────────────────────────────────────────
# 效价方向判定阈值：|valence| > 此值才算有方向性
_MOMENTUM_DIRECTION_THRESHOLD = 0.1
# 惯性天数 → 衰减率乘数（天数越多衰减越慢）
_MOMENTUM_DECAY_MULTIPLIERS: list[tuple[int, float]] = [
    (5, 0.50),   # >= 5 天：衰减率 × 0.50（半衰期翻倍）
    (3, 0.70),   # >= 3 天：衰减率 × 0.70
    (2, 0.85),   # >= 2 天：衰减率 × 0.85
]
# 惯性最大天数上限（防止无限累积）
_MOMENTUM_MAX_DAYS = 14


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
    # ── 情感惯性字段 ──
    momentum_days: int = 0           # 连续同向情感的天数
    momentum_direction: str = ""     # 惯性方向："positive" / "negative" / ""

    def as_dict(self) -> dict[str, Any]:
        d = {
            "user_id": self.user_id,
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4),
            "last_session_end_ts_ms": self.last_session_end_ts_ms,
            "mood_baseline": round(self.mood_baseline, 4),
        }
        # 仅在有惯性时写入（向后兼容旧版本）
        if self.momentum_days > 0:
            d["momentum_days"] = self.momentum_days
            d["momentum_direction"] = self.momentum_direction
        return d


@dataclass(frozen=True)
class RestoredEmotionState:
    """恢复后的情感状态（含衰减信息）。"""
    valence: float
    arousal: float
    dominance: float
    decay_factor: float         # 衰减因子（0~1，1=完全保留）
    hours_elapsed: float        # 离线时长（小时）
    from_baseline: bool         # 是否从基线启动（无持久化记录）
    reunion_type: str = ""      # 重逢类型："reunion"=久别重逢 / "short_absence"=短暂分离 / ""=正常
    anchor_active: bool = False # 是否触发了情感记忆锚点（强情感衰减减缓）
    momentum_active: bool = False  # 是否触发了情感惯性（连续同向情感减缓衰减）
    momentum_days: int = 0         # 当前惯性天数

    def as_dict(self) -> dict[str, Any]:
        d = {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4),
            "decay_factor": round(self.decay_factor, 4),
            "hours_elapsed": round(self.hours_elapsed, 2),
            "from_baseline": self.from_baseline,
        }
        if self.reunion_type:
            d["reunion_type"] = self.reunion_type
        if self.anchor_active:
            d["anchor_active"] = True
        if self.momentum_active:
            d["momentum_active"] = True
            d["momentum_days"] = self.momentum_days
        return d

    @property
    def is_reunion(self) -> bool:
        """是否为久别重逢（>= 7 天）。"""
        return self.reunion_type == "reunion"

    @property
    def is_short_absence(self) -> bool:
        """是否为短暂分离（3~7 天）。"""
        return self.reunion_type == "short_absence"


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

    持久化策略：
        - 内存字典 + JSON 文件双写（进程重启后可恢复）
        - JSON 文件路径：{persistence_dir}/{user_id}.json
        - 线程安全：所有读写操作通过 _lock 保护
    """

    def __init__(
        self,
        *,
        decay_rate: float = DECAY_RATE,
        baseline_valence: float = DEFAULT_MOOD_BASELINE,
        baseline_arousal: float = DEFAULT_BASELINE_AROUSAL,
        baseline_dominance: float = DEFAULT_BASELINE_DOMINANCE,
        persistence_dir: str | None = None,
    ) -> None:
        self._decay_rate = float(decay_rate)
        self._baseline_valence = float(baseline_valence)
        self._baseline_arousal = float(baseline_arousal)
        self._baseline_dominance = float(baseline_dominance)
        # user_id → PersistedEmotionState（内存缓存）
        self._states: dict[str, PersistedEmotionState] = {}
        # 线程锁
        self._lock = threading.Lock()
        # 持久化目录
        self._persistence_dir = Path(persistence_dir or _DEFAULT_PERSISTENCE_DIR)
        self._ensure_persistence_dir()

    def _ensure_persistence_dir(self) -> None:
        """确保持久化目录存在。"""
        try:
            self._persistence_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _logger.warning("无法创建情感持久化目录 %s: %s", self._persistence_dir, exc)

    def _state_file(self, user_id: str) -> Path:
        """获取用户状态文件路径。"""
        # 安全的文件名（避免路径注入）
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_id)
        return self._persistence_dir / f"{safe_name}.json"

    def _save_to_file(self, state: PersistedEmotionState) -> None:
        """将状态保存到 JSON 文件。"""
        try:
            filepath = self._state_file(state.user_id)
            data = state.as_dict()
            tmp_path = filepath.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(filepath)  # 原子替换
            _logger.debug("情感状态已写入文件: %s", filepath)
        except OSError as exc:
            _logger.warning("情感状态文件写入失败 user=%s: %s", state.user_id, exc)

    def _load_from_file(self, user_id: str) -> PersistedEmotionState | None:
        """从 JSON 文件加载状态。"""
        try:
            filepath = self._state_file(user_id)
            if not filepath.exists():
                return None
            data = json.loads(filepath.read_text(encoding="utf-8"))
            return PersistedEmotionState(
                user_id=str(data.get("user_id", user_id)),
                valence=float(data.get("valence", 0.0)),
                arousal=float(data.get("arousal", 0.3)),
                dominance=float(data.get("dominance", 0.0)),
                last_session_end_ts_ms=int(data.get("last_session_end_ts_ms", 0)),
                mood_baseline=float(data.get("mood_baseline", self._baseline_valence)),
                # 情感惯性字段（向后兼容：旧文件无此字段时默认 0/""）
                momentum_days=int(data.get("momentum_days", 0)),
                momentum_direction=str(data.get("momentum_direction", "")),
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _logger.warning("情感状态文件读取失败 user=%s: %s", user_id, exc)
            return None

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

        with self._lock:
            # 获取或创建状态
            existing = self._states.get(uid)
            baseline = (
                mood_baseline
                if mood_baseline is not None
                else (existing.mood_baseline if existing else self._baseline_valence)
            )

            # ── 情感惯性更新 ──────────────────────────────────────────
            new_direction = _classify_valence_direction(float(valence))
            if existing and existing.momentum_direction and new_direction:
                if new_direction == existing.momentum_direction:
                    # 同向：惯性天数 +1（上限 _MOMENTUM_MAX_DAYS）
                    new_momentum_days = min(
                        existing.momentum_days + 1, _MOMENTUM_MAX_DAYS
                    )
                else:
                    # 方向翻转：惯性重置
                    new_momentum_days = 1
            else:
                # 首次记录或中性状态：初始化
                new_momentum_days = 1 if new_direction else 0

            state = PersistedEmotionState(
                user_id=uid,
                valence=float(valence),
                arousal=float(arousal),
                dominance=float(dominance),
                last_session_end_ts_ms=ts,
                mood_baseline=float(baseline),
                momentum_days=new_momentum_days,
                momentum_direction=new_direction,
            )
            self._states[uid] = state

        # 文件写入放在锁外（IO 操作不阻塞内存访问）
        self._save_to_file(state)

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

        查找顺序：内存缓存 → JSON 文件 → 中性基线。

        Args:
            user_id: 用户 ID
            current_ts_ms: 当前时间戳（0=使用当前时间）

        Returns:
            恢复后的情感状态
        """
        uid = str(user_id).strip()
        now_ts = int(current_ts_ms) if current_ts_ms else _now_ms()

        with self._lock:
            state = self._states.get(uid)

        # 内存中无记录，尝试从文件恢复
        if state is None or state.last_session_end_ts_ms <= 0:
            file_state = self._load_from_file(uid)
            if file_state is not None and file_state.last_session_end_ts_ms > 0:
                state = file_state
                with self._lock:
                    self._states[uid] = state  # 回填内存缓存
                _logger.info("情感恢复: user=%s 从文件恢复成功", uid)

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

        # 判断重逢类型
        reunion_type = ""
        if hours_elapsed >= _REUNION_THRESHOLD_HOURS:
            reunion_type = "reunion"
        elif hours_elapsed >= _SHORT_ABSENCE_THRESHOLD_HOURS:
            reunion_type = "short_absence"

        # 情感记忆锚点：极强情感衰减减缓
        anchor_active = abs(state.valence) >= _ANCHOR_VALENCE_THRESHOLD
        effective_decay_rate = (
            self._decay_rate * _ANCHOR_DECAY_MULTIPLIER
            if anchor_active
            else self._decay_rate
        )

        # ── 情感惯性：连续同向情感减缓衰减 ────────────────────────────
        momentum_active = False
        momentum_multiplier = 1.0
        if state.momentum_days >= 2 and state.momentum_direction:
            for threshold_days, multiplier in _MOMENTUM_DECAY_MULTIPLIERS:
                if state.momentum_days >= threshold_days:
                    momentum_multiplier = multiplier
                    momentum_active = True
                    break
            if momentum_active:
                effective_decay_rate *= momentum_multiplier
                _logger.debug(
                    "情感惯性生效: user=%s days=%d dir=%s multiplier=%.2f",
                    uid, state.momentum_days, state.momentum_direction,
                    momentum_multiplier,
                )

        # 计算衰减因子（使用有效衰减率）
        decay_factor = math.exp(-effective_decay_rate * hours_elapsed)
        decay_factor = max(0.0, min(1.0, decay_factor))

        # 久别重逢：在基线回归基础上叠加「重逢唤醒」效果
        # 久别重逢时 arousal 略微提升（期待感），valence 向正向偏移一点
        reunion_valence_boost = 0.0
        reunion_arousal_boost = 0.0
        if reunion_type == "reunion":
            # 久别重逢：无论上次情感如何，都有轻微的期待感唤醒
            reunion_arousal_boost = 0.15
            # 如果上次是正向情感，重逢时正向偏移；负向情感则不叠加（避免强制正向）
            if state.valence >= 0:
                reunion_valence_boost = 0.1
        elif reunion_type == "short_absence":
            reunion_arousal_boost = 0.08
            if state.valence >= 0:
                reunion_valence_boost = 0.05

        # 恢复：向基线回归 + 重逢加成
        restored_valence = (
            state.valence * decay_factor
            + state.mood_baseline * (1.0 - decay_factor)
            + reunion_valence_boost
        )
        restored_arousal = (
            state.arousal * decay_factor
            + self._baseline_arousal * (1.0 - decay_factor)
            + reunion_arousal_boost
        )
        restored_dominance = (
            state.dominance * decay_factor
            + self._baseline_dominance * (1.0 - decay_factor)
        )

        # 钳位到合理范围
        restored_valence = max(-1.0, min(1.0, restored_valence))
        restored_arousal = max(0.0, min(1.0, restored_arousal))
        restored_dominance = max(-1.0, min(1.0, restored_dominance))

        _logger.info(
            "情感恢复: user=%s hours=%.1f decay=%.3f v=%.3f→%.3f anchor=%s reunion=%s",
            uid, hours_elapsed, decay_factor,
            state.valence, restored_valence,
            anchor_active, reunion_type or "none",
        )

        _logger.info(
            "情感惯性状态: user=%s momentum_days=%d momentum_dir=%s momentum_active=%s",
            uid, state.momentum_days, state.momentum_direction or "none",
            momentum_active,
        )

        return RestoredEmotionState(
            valence=round(restored_valence, 4),
            arousal=round(restored_arousal, 4),
            dominance=round(restored_dominance, 4),
            decay_factor=round(decay_factor, 4),
            hours_elapsed=round(hours_elapsed, 2),
            from_baseline=False,
            reunion_type=reunion_type,
            anchor_active=anchor_active,
            momentum_active=momentum_active,
            momentum_days=state.momentum_days,
        )

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_persisted_state(self, user_id: str) -> PersistedEmotionState | None:
        """获取用户持久化的情感状态（不应用衰减）。"""
        uid = str(user_id).strip()
        with self._lock:
            state = self._states.get(uid)
        if state is None:
            # 尝试从文件加载
            state = self._load_from_file(uid)
            if state is not None:
                with self._lock:
                    self._states[uid] = state
        return state

    def has_persisted_state(self, user_id: str) -> bool:
        """检查用户是否有持久化的情感状态。"""
        uid = str(user_id).strip()
        with self._lock:
            state = self._states.get(uid)
        if state is not None and state.last_session_end_ts_ms > 0:
            return True
        # 检查文件
        return self._state_file(uid).exists()

    # ── 清理 ──────────────────────────────────────────────────────────────

    def clear(self, user_id: str) -> None:
        """清除用户持久化的情感状态（内存 + 文件）。"""
        uid = str(user_id).strip()
        with self._lock:
            self._states.pop(uid, None)
        # 删除文件
        try:
            filepath = self._state_file(uid)
            if filepath.exists():
                filepath.unlink()
        except OSError as exc:
            _logger.warning("情感状态文件删除失败 user=%s: %s", uid, exc)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _classify_valence_direction(valence: float) -> str:
    """将效价值分类为方向标签。

    Args:
        valence: 效价值 [-1.0, 1.0]

    Returns:
        "positive" / "negative" / ""（中性）
    """
    if valence > _MOMENTUM_DIRECTION_THRESHOLD:
        return "positive"
    elif valence < -_MOMENTUM_DIRECTION_THRESHOLD:
        return "negative"
    return ""


def compute_momentum_decay_multiplier(momentum_days: int) -> float:
    """根据惯性天数计算衰减率乘数（供外部模块查询）。

    Args:
        momentum_days: 连续同向情感天数

    Returns:
        衰减率乘数（1.0=无惯性，越小衰减越慢）
    """
    if momentum_days < 2:
        return 1.0
    for threshold_days, multiplier in _MOMENTUM_DECAY_MULTIPLIERS:
        if momentum_days >= threshold_days:
            return multiplier
    return 1.0
