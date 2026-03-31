"""M27-A：GateSnapshot 情感字段测试（T-GATE-SNAP-01~03）"""

from __future__ import annotations

import pytest
from host_brain.governor.rules import GateSnapshot, _normalize_snapshot


class TestGateSnapshotEmotionFields:
    """T-GATE-SNAP-01~03：GateSnapshot 情感字段测试"""

    def test_gate_snapshot_with_mood_state(self):
        """T-GATE-SNAP-01：GateSnapshot 可携带 mood_state="engaged" 构造成功"""
        snapshot = GateSnapshot(
            trust_level=50,
            energy_level=60,
            sys_mode="normal",
            rate_limit_hit=False,
            scope="host",
            scope_allowlist=("host", "memory", "system"),
            safety_flag=False,
            airlock_blocked=False,
            mood_state="engaged",
            emotion_tag="HAPPY",
            emotion_intensity=0.8,
        )
        assert snapshot.mood_state == "engaged"
        assert snapshot.emotion_tag == "HAPPY"
        assert snapshot.emotion_intensity == 0.8

    def test_gate_snapshot_default_emotion_fields(self):
        """T-GATE-SNAP-02：GateSnapshot 不传情感字段时使用默认值，不报错"""
        snapshot = GateSnapshot(
            trust_level=50,
            energy_level=60,
            sys_mode="normal",
            rate_limit_hit=False,
            scope="host",
            scope_allowlist=("host", "memory", "system"),
            safety_flag=False,
            airlock_blocked=False,
        )
        assert snapshot.mood_state == "steady"
        assert snapshot.emotion_tag == "NEUTRAL"
        assert snapshot.emotion_intensity == 0.5

    def test_gate_snapshot_serialization_includes_emotion_fields(self):
        """T-GATE-SNAP-03：GateSnapshot 序列化为 dict 时包含三个情感字段"""
        raw = {
            "trust_level": 50,
            "energy_level": 60,
            "sys_mode": "normal",
            "rate_limit_hit": False,
            "scope": "host",
            "safety_flag": False,
            "airlock_blocked": False,
            "mood_state": "tired",
            "emotion_tag": "SAD",
            "emotion_intensity": 0.3,
        }
        snapshot = _normalize_snapshot(raw)
        # 验证情感字段被正确解析
        assert snapshot.mood_state == "tired"
        assert snapshot.emotion_tag == "SAD"
        assert snapshot.emotion_intensity == 0.3

    def test_normalize_snapshot_emotion_defaults(self):
        """_normalize_snapshot 在缺少情感字段时使用默认值"""
        snapshot = _normalize_snapshot({})
        assert snapshot.mood_state == "steady"
        assert snapshot.emotion_tag == "NEUTRAL"
        assert snapshot.emotion_intensity == 0.5
