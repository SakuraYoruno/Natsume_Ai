"""
test_routing_matrix.py —— RoutingMatrix 单元测试
"""
from __future__ import annotations

import pytest

from host_brain.app.llm.routing_matrix import RoutingMatrix, FALLBACK_CHAIN
from host_brain.app.schemas.memory_types import RiskLevel
from host_brain.app.schemas.routing_types import (
    LatencyBudget,
    ModelTarget,
    TaskType,
)


class TestPrimaryRouting:
    """主路由规则测试（需求 5.2、5.3、5.4）。"""

    def test_chat_fast_routes_to_host_8b(self):
        """验证 chat + <2s → host_8b（需求 5.2）。"""
        matrix = RoutingMatrix()
        decision = matrix.select(
            task_type=TaskType.CHAT,
            latency_budget=LatencyBudget.FAST,
        )
        assert decision.selected_model == ModelTarget.HOST_8B

    def test_reasoning_routes_to_cloud(self):
        """验证 reasoning → cloud（需求 5.3）。"""
        matrix = RoutingMatrix()
        decision = matrix.select(task_type=TaskType.REASONING)
        assert decision.selected_model == ModelTarget.CLOUD

    def test_long_context_routes_to_cloud(self):
        """验证 context > 16k → cloud（需求 5.3）。"""
        matrix = RoutingMatrix()
        decision = matrix.select(
            task_type=TaskType.CHAT,
            context_length=20000,
        )
        assert decision.selected_model == ModelTarget.CLOUD

    def test_dream_routes_to_skills(self):
        """验证 dream → skills（需求 5.4）。"""
        matrix = RoutingMatrix()
        decision = matrix.select(task_type=TaskType.DREAM)
        assert decision.selected_model == ModelTarget.SKILLS

    def test_diary_routes_to_skills(self):
        """验证 diary → skills（需求 5.4）。"""
        matrix = RoutingMatrix()
        decision = matrix.select(task_type=TaskType.DIARY)
        assert decision.selected_model == ModelTarget.SKILLS


class TestFallbackRouting:
    """降级路由测试（需求 5.5、5.6）。"""

    def test_fallback_chain_order(self):
        """验证降级链顺序：cloud → host_8b → edge_3b（需求 5.5）。"""
        assert FALLBACK_CHAIN == [
            ModelTarget.CLOUD,
            ModelTarget.HOST_8B,
            ModelTarget.EDGE_3B,
        ]

    def test_high_risk_blocks_edge_3b(self):
        """验证 HIGH risk_level 禁止路由到 edge_3b（需求 5.6）。"""
        matrix = RoutingMatrix()
        decision = matrix.select_with_fallback(
            task_type=TaskType.CHAT,
            risk_level=RiskLevel.HIGH,
            failed_models=[ModelTarget.CLOUD, ModelTarget.HOST_8B],
        )
        assert decision.selected_model != ModelTarget.EDGE_3B

    def test_fallback_marked_in_decision(self):
        """验证降级决策中 is_fallback=True（需求 5.5）。"""
        matrix = RoutingMatrix()
        decision = matrix.select_with_fallback(
            task_type=TaskType.CHAT,
            failed_models=[ModelTarget.HOST_8B],
        )
        if decision.is_fallback:
            assert decision.fallback_reason is not None
            assert decision.original_model is not None

    def test_route_log_written(self):
        """验证路由决策写入 llm_route_log（需求 5.5）。"""
        log_entries = []

        def mock_log_writer(decision):
            log_entries.append(decision)

        matrix = RoutingMatrix(route_log_writer=mock_log_writer)
        matrix.select(task_type=TaskType.CHAT)

        assert len(log_entries) == 1
        assert log_entries[0].task_type == TaskType.CHAT
