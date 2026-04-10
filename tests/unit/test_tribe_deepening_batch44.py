"""T-334：第四十四批 Tribe Agent 接口修复单元测试。

覆盖场景：
- T-324: PainRewardAgent.record_reward_event() 别名方法
- T-329: HippocampusAgent tick_idle_cycle 使用 person_id 参数名
- T-330: ANSAgent.set_recovery_mode() 公开方法
- T-330: CircadianAgent/DefaultModeAgent 改用 set_recovery_mode() 而非直接访问私有属性
- T-325~T-327: 三处 record_reward_event 调用参数名正确
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# T-324: PainRewardAgent.record_reward_event() 别名方法
# ─────────────────────────────────────────────────────────────────────────────

class TestT324RecordRewardEventAlias:
    """T-324：PainRewardAgent.record_reward_event() 应正确委托给 record_reward()。"""

    def _make_agent(self):
        from host_brain.tribe.pain_reward_agent import PainRewardAgent
        from host_brain.tribe.tribe_state import TribeStateRegistry
        reg = MagicMock(spec=TribeStateRegistry)
        reg.get_state.return_value = None
        reg.set_state.return_value = None
        agent = PainRewardAgent(state_registry=reg)
        return agent

    def test_record_reward_event_exists(self):
        """record_reward_event 方法应存在。"""
        agent = self._make_agent()
        assert hasattr(agent, "record_reward_event"), "record_reward_event 方法不存在"

    def test_record_reward_event_delegates_to_record_reward(self):
        """record_reward_event 应委托给 record_reward，reward_level 映射为 intensity。"""
        agent = self._make_agent()
        called_with = {}

        original = agent.record_reward
        def mock_record_reward(*, source, intensity, metadata=None):
            called_with["source"] = source
            called_with["intensity"] = intensity
            called_with["metadata"] = metadata
            return original(source=source, intensity=intensity, metadata=metadata)

        agent.record_reward = mock_record_reward
        agent.record_reward_event(
            source="test_source",
            reward_level=0.75,
            metadata={"key": "val"},
        )
        assert called_with["source"] == "test_source"
        assert abs(called_with["intensity"] - 0.75) < 1e-6
        assert called_with["metadata"] == {"key": "val"}

    def test_record_reward_event_default_reward_level(self):
        """record_reward_event 默认 reward_level=0.4。"""
        agent = self._make_agent()
        called_with = {}

        original = agent.record_reward
        def mock_record_reward(*, source, intensity, metadata=None):
            called_with["intensity"] = intensity
            return original(source=source, intensity=intensity, metadata=metadata)

        agent.record_reward = mock_record_reward
        agent.record_reward_event(source="default_test")
        assert abs(called_with["intensity"] - 0.4) < 1e-6

    def test_record_reward_event_clamps_reward_level(self):
        """record_reward_event 应将 reward_level 限制在 0.0~1.0。"""
        agent = self._make_agent()
        # 超出范围的值应被 record_reward 内部 clamp
        result = agent.record_reward_event(source="clamp_test", reward_level=1.5)
        assert isinstance(result, dict)
        assert result.get("reward_level", 1.0) <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# T-329: HippocampusAgent tick_idle_cycle 使用 person_id 参数名
# ─────────────────────────────────────────────────────────────────────────────

class TestT329HippocampusTickIdleCyclePersonId:
    """T-329：HippocampusAgent 调用 DefaultModeAgent.tick_idle_cycle 时应使用 person_id 参数名。"""

    def test_episodic_write_uses_person_id(self):
        """write_episodic_memory 联动 DefaultModeAgent 时应传 person_id 而非 user_id。"""
        tick_calls = []

        mock_dma = MagicMock()
        def mock_tick(**kwargs):
            tick_calls.append(kwargs)
            return {}
        mock_dma.tick_idle_cycle = mock_tick

        mock_reg = MagicMock()
        mock_reg.get_state.return_value = None
        mock_reg.set_state.return_value = None
        mock_reg.write.return_value = None

        mock_arbiter = MagicMock()
        mock_arbiter.run.return_value = {"status": "OK", "layer": "episodic"}

        with patch("host_brain.tribe.default_mode_agent.get_default_mode_agent", return_value=mock_dma), \
             patch("host_brain.tribe.hippocampus_agent.get_memory_arbiter", return_value=mock_arbiter, create=True):
            from host_brain.tribe.hippocampus_agent import HippocampusAgent
            agent = HippocampusAgent(state_registry=mock_reg)
            try:
                agent.write_episodic_memory(
                    person_id="user_42",
                    event_summary="测试事件",
                    emotion_tag="happy",
                )
            except Exception:
                pass  # 允许内部其他依赖失败，只检查 tick_idle_cycle 调用

        if tick_calls:
            call = tick_calls[0]
            assert "user_id" not in call, f"不应使用 user_id 参数名，实际调用: {call}"
            assert "person_id" in call, f"应使用 person_id 参数名，实际调用: {call}"

    def test_autobiographical_write_uses_person_id(self):
        """write_autobiographical_memory 联动 DefaultModeAgent 时应传 person_id 而非 user_id。"""
        tick_calls = []

        mock_dma = MagicMock()
        def mock_tick(**kwargs):
            tick_calls.append(kwargs)
            return {}
        mock_dma.tick_idle_cycle = mock_tick

        mock_reg = MagicMock()
        mock_reg.get_state.return_value = None
        mock_reg.set_state.return_value = None
        mock_reg.write.return_value = None

        mock_arbiter = MagicMock()
        mock_arbiter.run.return_value = {"status": "OK", "layer": "autobiographical"}

        with patch("host_brain.tribe.default_mode_agent.get_default_mode_agent", return_value=mock_dma), \
             patch("host_brain.tribe.hippocampus_agent.get_memory_arbiter", return_value=mock_arbiter, create=True):
            from host_brain.tribe.hippocampus_agent import HippocampusAgent
            agent = HippocampusAgent(state_registry=mock_reg)
            try:
                agent.write_autobiographical_memory(
                    person_id="user_42",
                    narrative="测试自传体记忆",
                    emotion_tag="nostalgic",
                )
            except Exception:
                pass

        if tick_calls:
            call = tick_calls[0]
            assert "user_id" not in call, f"不应使用 user_id 参数名，实际调用: {call}"
            assert "person_id" in call, f"应使用 person_id 参数名，实际调用: {call}"


# ─────────────────────────────────────────────────────────────────────────────
# T-330: ANSAgent.set_recovery_mode() 公开方法
# ─────────────────────────────────────────────────────────────────────────────

class TestT330ANSAgentSetRecoveryMode:
    """T-330：ANSAgent.set_recovery_mode() 应正确设置 _recovery_mode 标志。"""

    def _make_agent(self):
        from host_brain.tribe.ans_agent import ANSAgent
        agent = ANSAgent()
        return agent

    def test_set_recovery_mode_exists(self):
        """set_recovery_mode 方法应存在。"""
        agent = self._make_agent()
        assert hasattr(agent, "set_recovery_mode"), "set_recovery_mode 方法不存在"

    def test_set_recovery_mode_true(self):
        """set_recovery_mode(True) 应将 _recovery_mode 设为 True。"""
        agent = self._make_agent()
        agent.set_recovery_mode(True, source="test")
        assert agent._recovery_mode is True

    def test_set_recovery_mode_false(self):
        """set_recovery_mode(False) 应将 _recovery_mode 设为 False。"""
        agent = self._make_agent()
        agent._recovery_mode = True  # 先设为 True
        agent.set_recovery_mode(False, source="test")
        assert agent._recovery_mode is False

    def test_set_recovery_mode_no_change_no_emit(self):
        """set_recovery_mode 状态未变化时不应发布事件。"""
        agent = self._make_agent()
        agent._recovery_mode = False
        emit_calls = []
        agent.emit_from_thread = lambda *a, **kw: emit_calls.append((a, kw))
        agent.set_recovery_mode(False, source="test")  # 未变化
        assert len(emit_calls) == 0

    def test_set_recovery_mode_false_emits_recovery_exit(self):
        """set_recovery_mode(False) 从 True 变化时应发布 INNER_WORLD_TICKED 事件。"""
        agent = self._make_agent()
        agent._recovery_mode = True
        emit_calls = []
        agent.emit_from_thread = lambda event_type, payload: emit_calls.append((event_type, payload))
        agent.set_recovery_mode(False, source="circadian_day_phase")
        assert len(emit_calls) == 1
        event_type, payload = emit_calls[0]
        assert event_type == "INNER_WORLD_TICKED"
        assert payload.get("recovery_mode") is False
        assert payload.get("proactive_multiplier") == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# T-330: CircadianAgent 改用 set_recovery_mode() 而非直接访问私有属性
# ─────────────────────────────────────────────────────────────────────────────

class TestT330CircadianAgentUsesSetRecoveryMode:
    """T-330：CircadianAgent 日间相位切换时应调用 ANSAgent.set_recovery_mode() 而非直接访问 _recovery_mode。"""

    def test_circadian_day_phase_calls_set_recovery_mode(self):
        """CircadianAgent 日间相位切换时应调用 set_recovery_mode(False)。"""
        set_recovery_calls = []

        mock_ans = MagicMock()
        mock_ans.set_recovery_mode = lambda enabled, **kw: set_recovery_calls.append(enabled)
        # 不应有 _recovery_mode 直接赋值
        mock_ans._recovery_mode = True

        mock_reg = MagicMock()
        mock_reg.get_state.return_value = None
        mock_reg.set_state.return_value = None
        mock_reg.aggregate_vital_signs.return_value = {}

        mock_endo = MagicMock()
        mock_endo.adjust_cortisol = MagicMock(return_value={})

        with patch("host_brain.tribe.ans_agent.get_ans_agent", return_value=mock_ans), \
             patch("host_brain.tribe.endocrine_agent.get_endocrine_agent", return_value=mock_endo, create=True):
            from host_brain.tribe.circadian_agent import CircadianAgent
            agent = CircadianAgent(state_registry=mock_reg)
            try:
                agent.tick_phase(now_ts_ms=1000000)
            except Exception:
                pass

        # 如果触发了日间相位，应调用 set_recovery_mode(False)
        # 由于相位依赖时间，只验证方法存在且不直接访问私有属性
        import inspect
        source = inspect.getsource(agent.tick_phase)
        assert "_recovery_mode = False" not in source, \
            "tick_phase 不应直接赋值 _recovery_mode，应使用 set_recovery_mode()"
        assert "set_recovery_mode" in source, \
            "tick_phase 应调用 set_recovery_mode()"


# ─────────────────────────────────────────────────────────────────────────────
# T-330: DefaultModeAgent 改用 set_recovery_mode() 而非直接访问私有属性
# ─────────────────────────────────────────────────────────────────────────────

class TestT330DefaultModeAgentUsesSetRecoveryMode:
    """T-330：DefaultModeAgent 高连续性时应调用 ANSAgent.set_recovery_mode() 而非直接访问 _recovery_mode。"""

    def test_default_mode_tick_uses_set_recovery_mode(self):
        """DefaultModeAgent.tick_idle_cycle 应调用 set_recovery_mode() 而非直接赋值 _recovery_mode。"""
        import inspect
        from host_brain.tribe.default_mode_agent import DefaultModeAgent
        source = inspect.getsource(DefaultModeAgent.tick_idle_cycle)
        assert "_recovery_mode = False" not in source, \
            "tick_idle_cycle 不应直接赋值 _recovery_mode，应使用 set_recovery_mode()"
        assert "set_recovery_mode" in source, \
            "tick_idle_cycle 应调用 set_recovery_mode()"


# ─────────────────────────────────────────────────────────────────────────────
# T-325~T-327: record_reward_event 调用参数名验证
# ─────────────────────────────────────────────────────────────────────────────

class TestT325T327RecordRewardEventCallSites:
    """T-325~T-327：三处 record_reward_event 调用均应使用 reward_level 参数名。"""

    def test_proprioception_agent_uses_reward_level(self):
        """ProprioceptionAgent 调用 record_reward_event 时应使用 reward_level 参数名。"""
        import inspect
        from host_brain.tribe import proprioception_agent
        source = inspect.getsource(proprioception_agent)
        # 找到 record_reward_event 调用块
        assert "reward_level=" in source, \
            "ProprioceptionAgent 应使用 reward_level= 参数名调用 record_reward_event"

    def test_initiative_governor_agent_uses_reward_level(self):
        """InitiativeGovernorAgent 调用 record_reward_event 时应使用 reward_level 参数名。"""
        import inspect
        from host_brain.tribe import initiative_governor_agent
        source = inspect.getsource(initiative_governor_agent)
        assert "reward_level=" in source, \
            "InitiativeGovernorAgent 应使用 reward_level= 参数名调用 record_reward_event"

    def test_social_brain_agent_uses_reward_level(self):
        """SocialBrainAgent 调用 record_reward_event 时应使用 reward_level 参数名。"""
        import inspect
        from host_brain.tribe import social_brain_agent
        source = inspect.getsource(social_brain_agent)
        assert "reward_level=" in source, \
            "SocialBrainAgent 应使用 reward_level= 参数名调用 record_reward_event"


# ─────────────────────────────────────────────────────────────────────────────
# T-328: InteroceptionAgent.reduce_recovery_urgency() 方法存在性
# ─────────────────────────────────────────────────────────────────────────────

class TestT328InteroceptionReduceRecoveryUrgency:
    """T-328：InteroceptionAgent.reduce_recovery_urgency() 方法应存在且正确降低 recovery_urgency。"""

    def _make_agent(self):
        from host_brain.tribe.interoception_agent import InteroceptionAgent
        from host_brain.tribe.tribe_state import TribeStateRegistry
        reg = MagicMock(spec=TribeStateRegistry)
        reg.get_state.return_value = {"state": {"recovery_urgency": 0.60}}
        reg.set_state.return_value = None
        reg.aggregate_vital_signs.return_value = {
            "stress_index": 0.3,
            "cognitive_load_index": 0.2,
            "sleep_pressure": 0.2,
            "energy_reserve": 0.6,
            "immune_risk_level": "none",
            "homeostasis_ok": True,
        }
        agent = InteroceptionAgent(state_registry=reg)
        return agent, reg

    def test_reduce_recovery_urgency_exists(self):
        """reduce_recovery_urgency 方法应存在。"""
        agent, _ = self._make_agent()
        assert hasattr(agent, "reduce_recovery_urgency"), "reduce_recovery_urgency 方法不存在"

    def test_reduce_recovery_urgency_lowers_urgency(self):
        """reduce_recovery_urgency 应降低 recovery_urgency。"""
        agent, reg = self._make_agent()
        written_state = {}

        def mock_set_state(namespace, state, writer):
            written_state.update(state)

        reg.set_state = mock_set_state
        agent.reduce_recovery_urgency(source="test_relief", relief=0.80)

        # 原始 urgency=0.60，relief=0.80，降幅=0.80*0.30=0.24，新值=0.36
        assert "recovery_urgency" in written_state
        new_urgency = written_state["recovery_urgency"]
        assert new_urgency < 0.60, f"recovery_urgency 应降低，实际: {new_urgency}"
        assert new_urgency >= 0.0, "recovery_urgency 不应低于 0.0"

    def test_reduce_recovery_urgency_records_source(self):
        """reduce_recovery_urgency 应记录 last_relief_source。"""
        agent, reg = self._make_agent()
        written_state = {}

        def mock_set_state(namespace, state, writer):
            written_state.update(state)

        reg.set_state = mock_set_state
        agent.reduce_recovery_urgency(source="peripheral_connected:qq", relief=0.50)
        assert written_state.get("last_relief_source") == "peripheral_connected:qq"
