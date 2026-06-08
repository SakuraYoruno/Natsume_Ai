"""
chain_types.py —— 主链路类型定义

包含：ChainContext、所有节点 TypedDict、ChainNode 基类、NODE_TIMEOUT 常量。
从 main_chain.py 拆分而来。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypedDict

from host_brain.app.intent_seq import allocate_intent_seq, sync_intent_seq

from ..schemas import TaskType
from ..schemas.memory_types import ProposerClass
from ..failure_matrix import FailureHandler, FailureEvent

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
    "canon_proposal": 2.0,
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
    """任务类型（Governor 放行后由 Thalamus 意图决策确定）"""

    # 各节点填充的中间结果
    quarantine_passed: bool = False
    governor_passed: bool = False
    governor_block_reason: Optional[str] = None
    recall_payload: Optional[Any] = None
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
    prompt_context: Any
    evidence_empty: bool


class LlmGatewayInput(TypedDict):
    message: str
    prompt_context: Any
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
