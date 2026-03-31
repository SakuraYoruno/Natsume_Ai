"""M32-G：hard_state_summary 情感时间戳专项测试（T-HARD-TS-01~02）"""

from __future__ import annotations

import pytest
from host_brain.core.metabolism import HardState, HardStateSnapshot, build_hard_snapshot


def _make_state(mood_state: str = "steady", energy_level: int = 80) -> HardState:
    """创建测试用 HardState"""
    return HardState(
        mood_state=mood_state,
        energy_level=energy_level,
        sys_mode="normal",
        safety_flag=True,
        last_event_id=None,
        ts_ms=1_000_000_000_000,
    )


class TestHardStateTimestamp:
    """T-HARD-TS-01~02：hard_state_summary 情感时间戳测试"""

    def test_hard_state_snapshot_has_emotion_ts_ms(self):
        """T-HARD-TS-01：HardStateSnapshot 含 emotion_ts_ms 字段"""
        state = _make_state()
        snapshot = build_hard_snapshot(state)
        assert hasattr(snapshot, "emotion_ts_ms")
        assert isinstance(snapshot.emotion_ts_ms, int)

    def test_emotion_ts_ms_is_non_negative(self):
        """T-HARD-TS-02：emotion_ts_ms 为非负整数"""
        state = _make_state(mood_state="calm", energy_level=70)
        snapshot = build_hard_snapshot(state)
        assert snapshot.emotion_ts_ms >= 0

    def test_emotion_ts_ms_custom_value(self):
        """传入自定义 ts_ms 时，emotion_ts_ms 使用该值"""
        state = _make_state(mood_state="engaged", energy_level=90)
        custom_ts = 1_700_000_000_000
        snapshot = build_hard_snapshot(state, ts_ms=custom_ts)
        assert snapshot.emotion_ts_ms == custom_ts

    def test_as_dict_contains_emotion_ts_ms(self):
        """as_dict() 包含 emotion_ts_ms 字段"""
        state = _make_state()
        snapshot = build_hard_snapshot(state, ts_ms=1_000_000)
        d = snapshot.as_dict()
        assert "emotion_ts_ms" in d
        assert d["emotion_ts_ms"] == 1_000_000

    def test_snapshot_immutable(self):
        """HardStateSnapshot 是不可变的（frozen=True）"""
        state = _make_state()
        snapshot = build_hard_snapshot(state)
        with pytest.raises((AttributeError, TypeError)):
            snapshot.emotion_ts_ms = 999  # type: ignore

    def test_default_ts_ms_is_current_time(self):
        """不传 ts_ms 时，emotion_ts_ms 使用当前时间（> 0）"""
        state = _make_state()
        snapshot = build_hard_snapshot(state)
        # 当前时间应该是一个合理的毫秒时间戳（> 2020年）
        assert snapshot.emotion_ts_ms > 1_577_836_800_000  # 2020-01-01
