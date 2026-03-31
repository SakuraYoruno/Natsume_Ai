"""M28-E：evolve_hard_state 情感事件语义扩展测试（T-EVOLVE-EMO-01~03）"""

from __future__ import annotations

import pytest
from host_brain.core.metabolism import HardState, evolve_hard_state


def _make_state(energy: int = 60, mood: str = "steady") -> HardState:
    return HardState(
        energy_level=energy,
        mood_state=mood,
        sys_mode="normal",
        safety_flag=False,
        last_event_id=None,
        ts_ms=0,
    )


class TestEvolveHardStateEmotion:
    """T-EVOLVE-EMO-01~03：evolve_hard_state 情感事件语义扩展测试"""

    def test_emotion_spike_increases_energy(self):
        """T-EVOLVE-EMO-01：event_kind="emotion_spike" 后，energy_level 上升"""
        state = _make_state(energy=60)
        evolved = evolve_hard_state(state, event_kind="emotion_spike")
        assert evolved.energy_level > state.energy_level

    def test_emotion_calm_moves_toward_calm(self):
        """T-EVOLVE-EMO-02：event_kind="emotion_calm" 后，mood_state 向 calm 靠近"""
        state = _make_state(energy=60, mood="engaged")
        evolved = evolve_hard_state(state, event_kind="emotion_calm")
        assert evolved.mood_state in ("calm", "steady")

    def test_unknown_event_kind_no_error(self):
        """T-EVOLVE-EMO-03：未知 event_kind 不报错，使用默认演化逻辑"""
        state = _make_state(energy=60)
        evolved = evolve_hard_state(state, event_kind="unknown_event_xyz")
        assert evolved.mood_state in ("calm", "steady", "engaged", "tired", "critical")

    def test_bond_deepen_increases_energy(self):
        """event_kind="bond_deepen" 后，energy_level 小幅上升"""
        state = _make_state(energy=60)
        evolved = evolve_hard_state(state, event_kind="bond_deepen")
        assert evolved.energy_level >= state.energy_level
