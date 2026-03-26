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
  - 持久化到 JSON 文件（进程重启后可恢复）
  - 恢复时自动应用衰减
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

# 中性基线（可通过环境变量覆盖）
DEFAULT_MOOD_BASELINE = float(os.environ.get("HOST_EMOTION_BASELINE_VALENCE", "0.0"))
DEFAULT_BASELINE_AROUSAL = float(os.environ.get("HOST_EMOTION_BASELINE_AROUSAL", "0.3"))
DEFAULT_BASELINE_DOMINANCE = float(os.environ.get("HOST_EMOTION_BASELINE_DOMINANCE", "0.0"))

# 默认持久化文件路径（相对于项目 data 目录）
_DEFAULT_PERSISTENCE_DIR = os.environ.get(
    "NATSUME_EMOTION_PERSISTENCE_DIR",
    os.path.join(os.path.expanduser("~"), ".natsume", "emotion"),
)


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

            state = PersistedEmotionState(
                user_id=uid,
                valence=float(valence),
                arousal=float(arousal),
                dominance=float(dominance),
                last_session_end_ts_ms=ts,
                mood_baseline=float(baseline),
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
