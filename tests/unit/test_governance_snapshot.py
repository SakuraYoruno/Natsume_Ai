"""M27-C：build_governance_snapshot 传递 mood_state 给 GateSnapshot 测试（T-GOV-SNAP-01~02）"""

from __future__ import annotations

import pytest
from host_brain.core.metabolism import build_governance_snapshot, normalize_hard_state


class TestGovernanceSnapshotEmotionFields:
    """T-GOV-SNAP-01~02：build_governance_snapshot 情感字段传递测试"""

    def test_mood_state_critical_propagated(self):
        """T-GOV-SNAP-01：hard_state_summary.mood_state="critical" 时，GateSnapshot.mood_state 为 "critical" """
        state = normalize_hard_state({"energy_level": 10, "mood_state": "critical"})
        snapshot = build_governance_snapshot(state, overrides={"mood_state": "critical"})
        assert snapshot["mood_state"] == "critical"
        assert snapshot["hard_state_summary"]["mood_state"] == "critical"

    def test_missing_emotion_fields_use_defaults(self):
        """T-GOV-SNAP-02：hard_state_summary 无情感字段时，GateSnapshot 使用默认值，不报错"""
        state = normalize_hard_state({"energy_level": 60})
        snapshot = build_governance_snapshot(state)
        # 默认值
        assert "mood_state" in snapshot
        assert "emotion_tag" in snapshot
        assert "emotion_intensity" in snapshot
        assert snapshot["emotion_tag"] == "NEUTRAL"
        assert snapshot["emotion_intensity"] == 0.5

    def test_emotion_fields_from_overrides(self):
        """overrides 中的情感字段被正确传递"""
        state = normalize_hard_state({"energy_level": 60})
        snapshot = build_governance_snapshot(
            state,
            overrides={"emotion_tag": "HAPPY", "emotion_intensity": 0.9},
        )
        assert snapshot["emotion_tag"] == "HAPPY"
        assert snapshot["emotion_intensity"] == 0.9
