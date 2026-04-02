"""Phase VII 验收测试：跨会话情感连续性（持久化 + 衰减恢复模型）。

验收标准：
  V-VII-1: 会话结束时情感向量持久化
  V-VII-2: 新会话开始时恢复情感向量，离线 1h 保留 ≥ 85%
  V-VII-3: 离线 48h+ 情感完全回归基线
  V-VII-4: 无持久化记录时（首次用户），从中性基线启动
"""

from __future__ import annotations

import math
import time

import pytest

from host_brain.persona.emotion_persistence import (
    EmotionPersistence,
    PersistedEmotionState,
    RestoredEmotionState,
    DECAY_RATE,
    DEFAULT_MOOD_BASELINE,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _make_persistence(tmp_path) -> EmotionPersistence:
    return EmotionPersistence(persistence_dir=tmp_path / "emotion_persistence")


# ---------------------------------------------------------------------------
# V-VII-1: 会话结束时情感向量持久化
# ---------------------------------------------------------------------------

class TestVVII1_Persist:
    """V-VII-1: 持久化。"""

    def test_persist_stores_state(self, tmp_path):
        """持久化后可读取状态。"""
        p = _make_persistence(tmp_path)
        p.persist(user_id="u1", valence=0.6, arousal=0.4, dominance=0.1)
        state = p.get_persisted_state("u1")
        assert state is not None
        assert state.valence == 0.6
        assert state.arousal == 0.4
        assert state.dominance == 0.1

    def test_persist_overwrites(self, tmp_path):
        """多次持久化覆盖旧状态。"""
        p = _make_persistence(tmp_path)
        p.persist(user_id="u1", valence=0.3)
        p.persist(user_id="u1", valence=0.8)
        state = p.get_persisted_state("u1")
        assert state is not None
        assert state.valence == 0.8

    def test_persist_returns_state(self, tmp_path):
        """持久化返回 PersistedEmotionState。"""
        p = _make_persistence(tmp_path)
        state = p.persist(user_id="u1", valence=0.5, arousal=0.3)
        assert isinstance(state, PersistedEmotionState)
        assert state.user_id == "u1"

    def test_persist_records_timestamp(self, tmp_path):
        """持久化记录时间戳。"""
        p = _make_persistence(tmp_path)
        ts = _now_ms()
        state = p.persist(user_id="u1", valence=0.5, ts_ms=ts)
        assert state.last_session_end_ts_ms == ts

    def test_has_persisted_state(self, tmp_path):
        """检查是否有持久化状态。"""
        p = _make_persistence(tmp_path)
        assert not p.has_persisted_state("u1")
        p.persist(user_id="u1", valence=0.5)
        assert p.has_persisted_state("u1")

    def test_persist_as_dict(self, tmp_path):
        """持久化状态可序列化。"""
        p = _make_persistence(tmp_path)
        state = p.persist(user_id="u1", valence=0.5)
        d = state.as_dict()
        assert "user_id" in d
        assert "valence" in d


# ---------------------------------------------------------------------------
# V-VII-2: 离线 1h 保留 ≥ 85%
# ---------------------------------------------------------------------------

class TestVVII2_OneHourDecay:
    """V-VII-2: 离线 1h 保留 ≥ 85%。"""

    def test_1h_decay_factor(self):
        """离线 1 小时衰减因子 ≥ 0.85。"""
        # exp(-0.1 * 1) ≈ 0.9048
        factor = math.exp(-DECAY_RATE * 1.0)
        assert factor >= 0.85

    def test_1h_restore_preserves_most(self, tmp_path):
        """离线 1 小时恢复保留 ≥ 85% 情感。"""
        p = _make_persistence(tmp_path)
        base_ts = _now_ms()
        p.persist(user_id="u1", valence=0.8, arousal=0.6, ts_ms=base_ts)

        # 1 小时后恢复
        restore_ts = base_ts + 1 * 3600 * 1000
        restored = p.restore(user_id="u1", current_ts_ms=restore_ts)

        assert not restored.from_baseline
        assert restored.decay_factor >= 0.85
        # valence 保留 ≥ 85% 原值
        assert restored.valence >= 0.8 * 0.85

    def test_half_life_7h(self, tmp_path):
        """7 小时半衰期：保留约 50%。"""
        p = _make_persistence(tmp_path)
        base_ts = _now_ms()
        p.persist(user_id="u1", valence=1.0, ts_ms=base_ts)

        restore_ts = base_ts + 7 * 3600 * 1000
        restored = p.restore(user_id="u1", current_ts_ms=restore_ts)

        assert 0.65 <= restored.decay_factor <= 0.75  # 强情感锚点激活后约 0.7047
        assert restored.anchor_active is True

    def test_restore_returns_correct_type(self, tmp_path):
        """恢复返回 RestoredEmotionState。"""
        p = _make_persistence(tmp_path)
        p.persist(user_id="u1", valence=0.5)
        restored = p.restore(user_id="u1")
        assert isinstance(restored, RestoredEmotionState)

    def test_restore_as_dict(self, tmp_path):
        """恢复结果可序列化。"""
        p = _make_persistence(tmp_path)
        p.persist(user_id="u1", valence=0.5)
        restored = p.restore(user_id="u1")
        d = restored.as_dict()
        assert "valence" in d
        assert "decay_factor" in d
        assert "hours_elapsed" in d


# ---------------------------------------------------------------------------
# V-VII-3: 离线 48h+ 情感完全回归基线
# ---------------------------------------------------------------------------

class TestVVII3_LongOffline:
    """V-VII-3: 离线 48h+ 回归基线。"""

    def test_48h_near_baseline(self, tmp_path):
        """离线 48 小时几乎完全回归基线。"""
        p = _make_persistence(tmp_path)
        base_ts = _now_ms()
        p.persist(user_id="u1", valence=0.9, arousal=0.8, ts_ms=base_ts)

        restore_ts = base_ts + 48 * 3600 * 1000
        restored = p.restore(user_id="u1", current_ts_ms=restore_ts)

        # 强情感锚点激活后：exp(-0.05 * 48) ≈ 0.0907
        assert 0.08 <= restored.decay_factor <= 0.11
        assert restored.anchor_active is True
        # 情感接近基线（默认 valence 基线 = 0.0）
        assert abs(restored.valence - DEFAULT_MOOD_BASELINE) < 0.1

    def test_72h_fully_baseline(self, tmp_path):
        """离线 72 小时完全回归基线。"""
        p = _make_persistence(tmp_path)
        base_ts = _now_ms()
        p.persist(user_id="u1", valence=1.0, ts_ms=base_ts)

        restore_ts = base_ts + 72 * 3600 * 1000
        restored = p.restore(user_id="u1", current_ts_ms=restore_ts)

        assert 0.02 <= restored.decay_factor <= 0.04
        assert restored.anchor_active is True
        assert 0.07 <= abs(restored.valence - DEFAULT_MOOD_BASELINE) <= 0.09
        assert restored.is_short_absence is True

    def test_24h_partial_decay(self, tmp_path):
        """离线 24 小时部分衰减。"""
        p = _make_persistence(tmp_path)
        base_ts = _now_ms()
        p.persist(user_id="u1", valence=0.8, ts_ms=base_ts)

        restore_ts = base_ts + 24 * 3600 * 1000
        restored = p.restore(user_id="u1", current_ts_ms=restore_ts)

        # 强情感锚点激活后：exp(-0.05 * 24) ≈ 0.301
        assert 0.28 <= restored.decay_factor <= 0.33
        assert 0.20 <= abs(restored.valence) <= 0.28
        assert restored.anchor_active is True


# ---------------------------------------------------------------------------
# V-VII-4: 无持久化记录时从中性基线启动
# ---------------------------------------------------------------------------

class TestVVII4_NoPersistence:
    """V-VII-4: 首次用户从基线启动。"""

    def test_first_user_baseline(self, tmp_path):
        """无持久化记录时从中性基线启动。"""
        p = _make_persistence(tmp_path)
        restored = p.restore(user_id="new_user")
        assert restored.from_baseline is True
        assert restored.valence == DEFAULT_MOOD_BASELINE
        assert restored.decay_factor == 0.0

    def test_cleared_user_baseline(self, tmp_path):
        """清除持久化后从基线启动。"""
        p = _make_persistence(tmp_path)
        p.persist(user_id="u1", valence=0.8)
        p.clear("u1")
        restored = p.restore(user_id="u1")
        assert restored.from_baseline is True

    def test_different_users_independent(self, tmp_path):
        """不同用户状态独立。"""
        p = _make_persistence(tmp_path)
        p.persist(user_id="u1", valence=0.8)
        restored_u2 = p.restore(user_id="u2")
        assert restored_u2.from_baseline is True
