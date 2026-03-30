"""
test_main_chain.py —— 主链路集成测试

覆盖三条核心路径：
1. 正常流：完整链路执行，所有节点通过
2. Governor 拦截流：Governor 节点拦截请求，链路中止
3. 模型降级流：LlmGateway 主模型失败，降级到备用模型
"""
from __future__ import annotations

import asyncio
import pytest

from host_brain.app.graph.main_chain import (
    ChainContext,
    ChainNode,
    GovernorNode,
    MainChain,
    SceneRouter,
)
from host_brain.app.schemas import TaskType
from host_brain.app.schemas.memory_types import ProposerClass
from host_brain.app.failure_matrix import FailureEvent, FailureHandler


# ---------------------------------------------------------------------------
# 辅助：可配置的测试节点
# ---------------------------------------------------------------------------

class AlwaysPassNode(ChainNode):
    """总是通过的测试节点。"""
    def __init__(self, name: str):
        self.name = name
        self.timeout = 5.0
        self.executed = False

    async def execute(self, ctx: ChainContext) -> bool:
        self.executed = True
        return True


class AlwaysFailNode(ChainNode):
    """总是失败的测试节点。"""
    def __init__(self, name: str, failure_event: str = "MODULE_TIMEOUT"):
        self.name = name
        self.timeout = 5.0
        self.failure_event = failure_event

    async def execute(self, ctx: ChainContext) -> bool:
        ctx.failure_event = self.failure_event
        ctx.failure_detail = f"节点 {self.name} 模拟失败"
        return False


class GovernorBlockNode(ChainNode):
    """模拟 Governor 拦截的测试节点。"""
    name = "governor"
    timeout = 2.0

    async def execute(self, ctx: ChainContext) -> bool:
        ctx.governor_passed = False
        ctx.governor_block_reason = "R3: 内容违规"
        ctx.failure_event = FailureEvent.GOVERNOR_BLOCK.value
        ctx.audit_tags.append("GOVERNOR_BLOCK")
        return False


class GovernorPassNode(ChainNode):
    """模拟 Governor 放行的测试节点。"""
    name = "governor"
    timeout = 2.0

    async def execute(self, ctx: ChainContext) -> bool:
        ctx.governor_passed = True
        return True


# ---------------------------------------------------------------------------
# 测试：正常流
# ---------------------------------------------------------------------------

class TestMainChainNormalFlow:
    """主链路正常流测试。"""

    @pytest.mark.asyncio
    async def test_all_nodes_execute_in_order(self):
        """验证所有节点按顺序执行。"""
        execution_order = []

        class TrackingNode(ChainNode):
            def __init__(self, name: str):
                self.name = name
                self.timeout = 5.0

            async def execute(self, ctx: ChainContext) -> bool:
                execution_order.append(self.name)
                if self.name == "governor":
                    ctx.governor_passed = True
                return True

        # 构建追踪节点链路
        nodes = [
            TrackingNode("airlock_quarantine"),
            TrackingNode("governor"),
            TrackingNode("graph_executor"),
            TrackingNode("recall_fusion"),
            TrackingNode("llm_gateway"),
            TrackingNode("qa_contract"),
            TrackingNode("episodic_store"),
            TrackingNode("audit"),
            TrackingNode("airlock_sanitizer"),
        ]

        chain = MainChain()
        chain.MAIN_NODES = nodes

        ctx = ChainContext(
            request_id="test-001",
            user_message="你好",
            session_id="session-001",
        )
        result = await chain.run(ctx)

        expected_order = [
            "airlock_quarantine", "governor", "graph_executor",
            "recall_fusion", "llm_gateway", "qa_contract",
            "episodic_store", "audit", "airlock_sanitizer",
        ]
        assert execution_order == expected_order, f"节点执行顺序错误: {execution_order}"
        assert result.failure_event is None

    @pytest.mark.asyncio
    async def test_node_count_does_not_exceed_12(self):
        """验证主链路节点数量不超过 12 个（需求 1.3）。"""
        assert len(MainChain.MAIN_NODES) <= 12, (
            f"主链路节点数量 {len(MainChain.MAIN_NODES)} 超过上限 12"
        )

    @pytest.mark.asyncio
    async def test_node_timings_recorded(self):
        """验证每个节点的执行时间被记录。"""
        chain = MainChain()
        ctx = ChainContext(
            request_id="test-002",
            user_message="测试",
            session_id="session-002",
        )
        result = await chain.run(ctx)

        # 至少有部分节点的计时被记录
        assert len(result.node_timings) > 0


# ---------------------------------------------------------------------------
# 测试：Governor 拦截流
# ---------------------------------------------------------------------------

class TestGovernorBlockFlow:
    """Governor 拦截流测试。"""

    @pytest.mark.asyncio
    async def test_chain_aborts_on_governor_block(self):
        """验证 Governor 拦截后链路中止，后续节点不执行。"""
        post_governor_executed = []

        class PostGovernorNode(ChainNode):
            def __init__(self, name: str):
                self.name = name
                self.timeout = 5.0

            async def execute(self, ctx: ChainContext) -> bool:
                post_governor_executed.append(self.name)
                return True

        nodes = [
            AlwaysPassNode("airlock_quarantine"),
            GovernorBlockNode(),
            PostGovernorNode("graph_executor"),
            PostGovernorNode("recall_fusion"),
        ]

        chain = MainChain()
        chain.MAIN_NODES = nodes

        ctx = ChainContext(
            request_id="test-003",
            user_message="违规内容",
            session_id="session-003",
        )
        result = await chain.run(ctx)

        assert result.failure_event == FailureEvent.GOVERNOR_BLOCK.value
        assert result.governor_passed is False
        assert len(post_governor_executed) == 0, "Governor 拦截后不应执行后续节点"

    @pytest.mark.asyncio
    async def test_failure_handler_called_on_governor_block(self):
        """验证 Governor 拦截时 FailureHandler 被调用。"""
        failure_events_received = []

        async def mock_failure_handler(event: str, ctx: ChainContext):
            failure_events_received.append(event)

        nodes = [
            AlwaysPassNode("airlock_quarantine"),
            GovernorBlockNode(),
        ]

        chain = MainChain(failure_handler=mock_failure_handler)
        chain.MAIN_NODES = nodes

        ctx = ChainContext(
            request_id="test-004",
            user_message="违规内容",
            session_id="session-004",
        )
        await chain.run(ctx)

        assert FailureEvent.GOVERNOR_BLOCK.value in failure_events_received

    @pytest.mark.asyncio
    async def test_audit_tag_written_on_governor_block(self):
        """验证 Governor 拦截时审计标签被写入。"""
        nodes = [
            AlwaysPassNode("airlock_quarantine"),
            GovernorBlockNode(),
        ]

        chain = MainChain()
        chain.MAIN_NODES = nodes

        ctx = ChainContext(
            request_id="test-005",
            user_message="违规内容",
            session_id="session-005",
        )
        result = await chain.run(ctx)

        assert "GOVERNOR_BLOCK" in result.audit_tags


# ---------------------------------------------------------------------------
# 测试：模型降级流
# ---------------------------------------------------------------------------

class TestModelFallbackFlow:
    """模型降级流测试。"""

    @pytest.mark.asyncio
    async def test_fallback_meta_marked(self):
        """验证模型降级时响应 meta 中标注 model_fallback=True（需求 6.3）。"""
        from host_brain.app.failure_matrix import FailureHandler, FailureEvent

        handler = FailureHandler()
        result = await handler.handle(
            FailureEvent.MODEL_FALLBACK.value,
            {},
            {"fallback_reason": "主模型超时"},
        )

        assert result.meta.get("model_fallback") is True
        assert result.should_abort is False  # 降级后可以继续

    @pytest.mark.asyncio
    async def test_fallback_chain_order(self):
        """验证降级链顺序：cloud → cloud_cn → host_14b → edge_3b（需求 5.5）。"""
        from host_brain.app.llm.routing_matrix import RoutingMatrix, FALLBACK_CHAIN
        from host_brain.app.schemas.routing_types import ModelTarget

        assert FALLBACK_CHAIN[0] == ModelTarget.CLOUD
        assert FALLBACK_CHAIN[1] == ModelTarget.CLOUD_CN
        assert FALLBACK_CHAIN[2] == ModelTarget.HOST_14B
        assert FALLBACK_CHAIN[3] == ModelTarget.EDGE_3B

    @pytest.mark.asyncio
    async def test_high_risk_blocks_edge_3b(self):
        """验证 HIGH risk_level 禁止路由到 edge_3b（需求 5.6）。"""
        from host_brain.app.llm.routing_matrix import RoutingMatrix
        from host_brain.app.schemas.routing_types import ModelTarget, TaskType, LatencyBudget
        from host_brain.app.schemas.memory_types import RiskLevel

        matrix = RoutingMatrix()
        decision = matrix.select_with_fallback(
            task_type=TaskType.CHAT,
            latency_budget=LatencyBudget.NORMAL,
            risk_level=RiskLevel.HIGH,
            failed_models=[ModelTarget.CLOUD, ModelTarget.CLOUD_CN, ModelTarget.HOST_14B],
        )

        # HIGH risk 时不应降级到 edge_3b
        assert decision.selected_model != ModelTarget.EDGE_3B
