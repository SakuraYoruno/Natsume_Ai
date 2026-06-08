"""
test_state_arbiter.py —— StateArbiter 单元测试
"""
from __future__ import annotations

import pytest

from host_brain.app.memory.state_arbiter import (
    ArbiterResult,
    StateArbiter,
    StateSource,
    SOURCE_PRIORITY,
)


class TestStateArbiterPriority:
    """状态源优先级测试。"""

    def test_priority_order_is_correct(self):
        """验证优先级顺序：WorldCanon > EpisodicStore > Bond > ChromaDB > EdgeRuntime。"""
        assert SOURCE_PRIORITY[0] == StateSource.WORLD_CANON
        assert SOURCE_PRIORITY[1] == StateSource.EPISODIC_STORE
        assert SOURCE_PRIORITY[2] == StateSource.BOND_GRAPH
        assert SOURCE_PRIORITY[3] == StateSource.CHROMA_DB
        assert SOURCE_PRIORITY[4] == StateSource.EDGE_RUNTIME

    @pytest.mark.asyncio
    async def test_world_canon_wins_over_episodic(self):
        """验证 WorldCanon 优先于 EpisodicStore（需求 2.1）。"""
        arbiter = StateArbiter()
        arbiter.register_source(StateSource.WORLD_CANON, lambda k: "canon_value")
        arbiter.register_source(StateSource.EPISODIC_STORE, lambda k: "episodic_value")

        result = await arbiter.resolve("user.name")
        assert result is not None
        assert result.value == "canon_value"
        assert result.source == StateSource.WORLD_CANON

    @pytest.mark.asyncio
    async def test_fallback_when_world_canon_unreachable(self):
        """验证 WorldCanon 不可达时降级到下一优先级（需求 2.4）。"""
        audit_events = []

        async def mock_audit(event, detail):
            audit_events.append(event)

        arbiter = StateArbiter(audit_writer=mock_audit)

        def failing_getter(k):
            raise ConnectionError("WorldCanon 不可达")

        arbiter.register_source(StateSource.WORLD_CANON, failing_getter)
        arbiter.register_source(StateSource.EPISODIC_STORE, lambda k: "episodic_value")

        result = await arbiter.resolve("user.episodic")
        assert result is not None
        assert result.value == "episodic_value"
        assert result.source == StateSource.EPISODIC_STORE
        assert result.is_fallback is True
        assert "STATE_SOURCE_FALLBACK" in audit_events

    @pytest.mark.asyncio
    async def test_session_override_not_written_to_canon(self):
        """验证 SESSION_OVERRIDE 不直接写入 Canon（需求 2.3）。"""
        arbiter = StateArbiter()
        arbiter.register_source(StateSource.WORLD_CANON, lambda k: "canon_value")

        # 设置 session override
        arbiter.set_session_override("session-001", "user.name", "override_value")

        # session override 应该被返回
        result = await arbiter.resolve("user.name", session_id="session-001")
        assert result is not None
        assert result.value == "override_value"

        # 但 WorldCanon 的值不应被修改（override 只在 session 内有效）
        result_no_session = await arbiter.resolve("user.name")
        assert result_no_session is not None
        assert result_no_session.value == "canon_value"

    @pytest.mark.asyncio
    async def test_conflict_detected_and_audited(self):
        """验证冲突检测并写入审计（需求 2.2）。"""
        audit_events = []

        async def mock_audit(event, detail):
            audit_events.append(event)

        arbiter = StateArbiter(audit_writer=mock_audit)
        arbiter.register_source(StateSource.WORLD_CANON, lambda k: "canon_value")
        arbiter.register_source(StateSource.EPISODIC_STORE, lambda k: "different_value")

        result = await arbiter.resolve("user.name")
        assert result is not None
        assert result.value == "canon_value"  # WorldCanon 胜出
        assert result.conflict_detected is True
        assert "STATE_SOURCE_CONFLICT" in audit_events


class TestStateArbiterSessionOverride:
    """SESSION_OVERRIDE 测试。"""

    def test_set_and_get_override(self):
        """验证 SESSION_OVERRIDE 的设置和获取。"""
        arbiter = StateArbiter()
        arbiter.set_session_override("session-001", "user.age", 25)
        overrides = arbiter.get_session_overrides("session-001")
        assert overrides["user.age"] == 25

    def test_clear_session_override(self):
        """验证清除 SESSION_OVERRIDE。"""
        arbiter = StateArbiter()
        arbiter.set_session_override("session-001", "user.age", 25)
        arbiter.clear_session_override("session-001")
        overrides = arbiter.get_session_overrides("session-001")
        assert len(overrides) == 0
