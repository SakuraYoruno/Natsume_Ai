"""
main_chain.py —— 主聊天链路时序编排器

定义主链路节点顺序：
  Airlock(Quarantine) → Governor → GraphExecutor → RecallFusion
  → LlmGateway → QAContract → EpisodicStore → Audit → Airlock(Sanitizer)

每个节点有标准 TypedDict 输入/输出、最大超时常量和降级策略。
SceneRouter 在 Governor 放行后根据 task_type 分流到专用子链。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypedDict

from ..schemas import TaskType
from ..schemas.memory_types import ProposerClass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 节点超时常量（秒）
# ---------------------------------------------------------------------------

NODE_TIMEOUT: Dict[str, float] = {
    "airlock_quarantine": 1.0,
    "governor": 2.0,
    "graph_executor": 5.0,
    "recall_fusion": 5.0,
    "llm_gateway": 30.0,
    "qa_contract": 3.0,
    "episodic_store": 2.0,
    "audit": 1.0,
    "airlock_sanitizer": 1.0,
}


# ---------------------------------------------------------------------------
# 链路上下文（贯穿所有节点的共享状态）
# ---------------------------------------------------------------------------

@dataclass
class ChainContext:
    """贯穿主链路所有节点的共享上下文对象。"""
    request_id: str
    """请求唯一 ID"""
    user_message: str
    """用户原始消息"""
    session_id: str
    """会话 ID"""
    proposer_class: ProposerClass = ProposerClass.USER
    """发起方类别"""
    task_type: TaskType = TaskType.CHAT
    """任务类型（Governor 放行后由 SceneRouter 确定）"""

    # 各节点填充的中间结果
    quarantine_passed: bool = False
    governor_passed: bool = False
    governor_block_reason: Optional[str] = None
    recall_context_pack: Optional[Any] = None
    llm_raw_output: Optional[str] = None
    qa_rewritten: bool = False
    qa_original_output: Optional[str] = None
    final_response: Optional[str] = None

    # 失败语义
    failure_event: Optional[str] = None
    failure_detail: Optional[str] = None

    # 审计追踪
    node_timings: Dict[str, float] = field(default_factory=dict)
    audit_tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 节点输入/输出 TypedDict
# ---------------------------------------------------------------------------

class AirlockQuarantineInput(TypedDict):
    user_message: str
    session_id: str
    request_id: str


class AirlockQuarantineOutput(TypedDict):
    passed: bool
    sanitized_message: str
    block_reason: Optional[str]


class GovernorInput(TypedDict):
    sanitized_message: str
    session_id: str
    proposer_class: str


class GovernorOutput(TypedDict):
    passed: bool
    task_type: str
    block_reason: Optional[str]


class RecallFusionInput(TypedDict):
    message: str
    session_id: str
    task_type: str


class RecallFusionOutput(TypedDict):
    context_pack: Any
    evidence_empty: bool


class LlmGatewayInput(TypedDict):
    message: str
    context_pack: Any
    task_type: str
    session_id: str


class LlmGatewayOutput(TypedDict):
    raw_output: str
    model_used: str
    is_fallback: bool


class QAContractInput(TypedDict):
    raw_output: str
    task_type: str


class QAContractOutput(TypedDict):
    final_output: str
    rewritten: bool
    original_output: Optional[str]


class EpisodicStoreInput(TypedDict):
    session_id: str
    user_message: str
    assistant_response: str
    task_type: str


class EpisodicStoreOutput(TypedDict):
    stored: bool
    record_id: Optional[str]


class AuditInput(TypedDict):
    context: Dict[str, Any]


class AuditOutput(TypedDict):
    audit_id: str


class AirlockSanitizerInput(TypedDict):
    raw_response: str


class AirlockSanitizerOutput(TypedDict):
    safe_response: str


# ---------------------------------------------------------------------------
# 节点基类
# ---------------------------------------------------------------------------

class ChainNode:
    """链路节点基类，所有节点继承此类。"""

    name: str = "base_node"
    timeout: float = 5.0

    async def execute(self, ctx: ChainContext) -> bool:
        """
        执行节点逻辑。

        Returns:
            True 表示继续执行下一节点，False 表示中止链路。
        """
        raise NotImplementedError

    async def execute_with_timeout(self, ctx: ChainContext) -> bool:
        """带超时保护的节点执行。"""
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(self.execute(ctx), timeout=self.timeout)
            ctx.node_timings[self.name] = time.monotonic() - start
            return result
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            ctx.node_timings[self.name] = elapsed
            ctx.failure_event = "MODULE_TIMEOUT"
            ctx.failure_detail = f"节点 {self.name} 超时（{self.timeout}s）"
            logger.warning("节点 %s 超时，已触发 MODULE_TIMEOUT", self.name)
            return False


# ---------------------------------------------------------------------------
# 具体节点实现（骨架，业务逻辑由各模块填充）
# ---------------------------------------------------------------------------

class AirlockQuarantineNode(ChainNode):
    """入口隔离节点：对用户输入进行初步安全检查。"""
    name = "airlock_quarantine"
    timeout = NODE_TIMEOUT["airlock_quarantine"]

    async def execute(self, ctx: ChainContext) -> bool:
        # 骨架实现：直接放行，实际安全检查由业务模块注入
        ctx.quarantine_passed = True
        logger.debug("[%s] 入口隔离通过", ctx.request_id)
        return True


class GovernorNode(ChainNode):
    """治理节点：执行 R1-R7 规则检查，决定是否放行请求。"""
    name = "governor"
    timeout = NODE_TIMEOUT["governor"]

    async def execute(self, ctx: ChainContext) -> bool:
        # 骨架实现：直接放行，实际规则由 Governor 模块注入
        ctx.governor_passed = True
        logger.debug("[%s] Governor 放行", ctx.request_id)
        return True


class GraphExecutorNode(ChainNode):
    """图执行节点：执行 LangGraph 子图（工具调用等）。"""
    name = "graph_executor"
    timeout = NODE_TIMEOUT["graph_executor"]

    async def execute(self, ctx: ChainContext) -> bool:
        logger.debug("[%s] GraphExecutor 执行", ctx.request_id)
        return True


class RecallFusionNode(ChainNode):
    """召回融合节点：执行七路召回并组装 ContextPack。"""
    name = "recall_fusion"
    timeout = NODE_TIMEOUT["recall_fusion"]

    async def execute(self, ctx: ChainContext) -> bool:
        logger.debug("[%s] RecallFusion 执行", ctx.request_id)
        return True


class LlmGatewayNode(ChainNode):
    """LLM 网关节点：按路由矩阵选择模型并生成响应。"""
    name = "llm_gateway"
    timeout = NODE_TIMEOUT["llm_gateway"]

    async def execute(self, ctx: ChainContext) -> bool:
        logger.debug("[%s] LlmGateway 执行", ctx.request_id)
        return True


class QAContractNode(ChainNode):
    """QA 合约节点：验证输出合规性，必要时重写。"""
    name = "qa_contract"
    timeout = NODE_TIMEOUT["qa_contract"]

    async def execute(self, ctx: ChainContext) -> bool:
        logger.debug("[%s] QAContract 执行", ctx.request_id)
        return True


class EpisodicStoreNode(ChainNode):
    """情节记忆存储节点：按宪法规则决定是否写入 EpisodicStore。"""
    name = "episodic_store"
    timeout = NODE_TIMEOUT["episodic_store"]

    async def execute(self, ctx: ChainContext) -> bool:
        logger.debug("[%s] EpisodicStore 执行", ctx.request_id)
        return True


class AuditNode(ChainNode):
    """审计节点：向 triptych 写入审计事件。"""
    name = "audit"
    timeout = NODE_TIMEOUT["audit"]

    async def execute(self, ctx: ChainContext) -> bool:
        logger.debug("[%s] Audit 写入，标签: %s", ctx.request_id, ctx.audit_tags)
        return True


class AirlockSanitizerNode(ChainNode):
    """出口净化节点：对最终响应进行安全过滤。"""
    name = "airlock_sanitizer"
    timeout = NODE_TIMEOUT["airlock_sanitizer"]

    async def execute(self, ctx: ChainContext) -> bool:
        logger.debug("[%s] 出口净化完成", ctx.request_id)
        return True


# ---------------------------------------------------------------------------
# 场景路由器
# ---------------------------------------------------------------------------

class SceneRouter:
    """
    场景路由器：在 Governor 放行后根据 task_type 分流到专用子链。

    主链路节点数量不超过 12 个（需求 1.3）。
    """

    # 专用子链注册表：task_type → 子链节点列表
    _sub_chains: Dict[str, List[ChainNode]] = {}

    @classmethod
    def register_sub_chain(cls, task_type: TaskType, nodes: List[ChainNode]) -> None:
        """注册专用子链。"""
        cls._sub_chains[task_type.value] = nodes

    @classmethod
    def get_sub_chain(cls, task_type: TaskType) -> Optional[List[ChainNode]]:
        """获取专用子链，不存在则返回 None（走主链路）。"""
        return cls._sub_chains.get(task_type.value)

    @classmethod
    def route(cls, ctx: ChainContext) -> List[ChainNode]:
        """
        根据 task_type 返回应执行的节点列表。

        普通 chat 走主链路；其他类型若有注册子链则走子链。
        """
        if ctx.task_type == TaskType.CHAT:
            return []  # 空列表表示走默认主链路
        sub_chain = cls.get_sub_chain(ctx.task_type)
        if sub_chain:
            logger.info("[%s] SceneRouter 分流到 %s 子链", ctx.request_id, ctx.task_type)
            return sub_chain
        return []  # 未注册子链，走主链路


# ---------------------------------------------------------------------------
# 主链路编排器
# ---------------------------------------------------------------------------

class MainChain:
    """
    主聊天链路时序编排器。

    固定节点顺序（需求 1.1）：
      AirlockQuarantine → Governor → GraphExecutor → RecallFusion
      → LlmGateway → QAContract → EpisodicStore → Audit → AirlockSanitizer

    任意节点失败时统一走 FailureHandler（需求 1.2）。
    """

    # 主链路固定节点列表
    MAIN_NODES: List[ChainNode] = [
        AirlockQuarantineNode(),
        GovernorNode(),
        GraphExecutorNode(),
        RecallFusionNode(),
        LlmGatewayNode(),
        QAContractNode(),
        EpisodicStoreNode(),
        AuditNode(),
        AirlockSanitizerNode(),
    ]

    def __init__(self, failure_handler: Optional[Callable] = None) -> None:
        """
        Args:
            failure_handler: 统一失败处理回调，签名为 (event: str, ctx: ChainContext) -> None。
                             对应 FailureHandler.handle()（任务 7 实现后注入）。
        """
        self._failure_handler = failure_handler

    async def run(self, ctx: ChainContext) -> ChainContext:
        """
        执行主链路。

        Governor 放行后由 SceneRouter 决定是否分流；
        任意节点返回 False 或抛出异常时，调用 failure_handler 并终止链路。
        """
        # 确定执行节点列表
        nodes = list(self.MAIN_NODES)

        for node in nodes:
            try:
                should_continue = await node.execute_with_timeout(ctx)
            except Exception as exc:
                ctx.failure_event = "MODULE_TIMEOUT"
                ctx.failure_detail = f"节点 {node.name} 异常: {exc}"
                logger.exception("节点 %s 执行异常", node.name)
                should_continue = False

            if not should_continue:
                # 统一失败处理
                if self._failure_handler and ctx.failure_event:
                    try:
                        await self._failure_handler(ctx.failure_event, ctx)
                    except Exception as fh_exc:
                        logger.error("FailureHandler 执行异常: %s", fh_exc)
                logger.warning(
                    "[%s] 链路在节点 %s 中止，failure_event=%s",
                    ctx.request_id, node.name, ctx.failure_event,
                )
                break

            # Governor 放行后执行场景路由
            if node.name == "governor" and ctx.governor_passed:
                sub_chain = SceneRouter.route(ctx)
                if sub_chain:
                    # 插入子链节点（替换后续主链路节点）
                    remaining_idx = nodes.index(node) + 1
                    nodes = nodes[:remaining_idx] + sub_chain
                    logger.info(
                        "[%s] 已切换到 %s 子链，共 %d 个节点",
                        ctx.request_id, ctx.task_type, len(nodes),
                    )

        return ctx

    def set_failure_handler(self, handler: Callable) -> None:
        """注入统一失败处理器（任务 7 完成后调用）。"""
        self._failure_handler = handler
