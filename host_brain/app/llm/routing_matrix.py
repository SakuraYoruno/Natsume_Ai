"""
routing_matrix.py —— 模型路由矩阵

按任务类型、延迟预算、风险等级进行能力路由（需求 5），
而不仅仅是故障切换：
- chat + <2s → host_8b（需求 5.2）
- reasoning / >16k → cloud（需求 5.3）
- dream/diary → skills（需求 5.4）
- HIGH risk_level 禁止路由到 edge_3b（需求 5.6）
- 降级链：cloud → host_8b → edge_3b（需求 5.5）
- 路由决策写入 llm_route_log（需求 5.5）
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from ..schemas.memory_types import RiskLevel
from ..schemas.routing_types import (
    LatencyBudget,
    ModelTarget,
    RouteDecision,
    TaskType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 降级链定义
# ---------------------------------------------------------------------------

# 标准降级链：cloud → host_8b → edge_3b
FALLBACK_CHAIN: List[ModelTarget] = [
    ModelTarget.CLOUD,
    ModelTarget.HOST_8B,
    ModelTarget.EDGE_3B,
]


# ---------------------------------------------------------------------------
# 路由矩阵
# ---------------------------------------------------------------------------

class RoutingMatrix:
    """
    LlmGateway 模型路由矩阵。

    根据多维度参数选择最合适的模型目标，并在失败时按降级链切换。
    """

    def __init__(
        self,
        route_log_writer: Optional[Callable] = None,
        availability_checker: Optional[Callable] = None,
    ) -> None:
        """
        Args:
            route_log_writer: 路由日志写入回调，签名为 (decision: RouteDecision) -> None。
                              对应 llm_route_log 写入。
            availability_checker: 模型可用性检查回调，签名为 (model: ModelTarget) -> bool。
                                  用于降级链中跳过不可用模型。
        """
        self._route_log_writer = route_log_writer
        self._availability_checker = availability_checker

    def select(
        self,
        task_type: TaskType,
        latency_budget: LatencyBudget = LatencyBudget.NORMAL,
        risk_level: RiskLevel = RiskLevel.LOW,
        context_length: int = 0,
        requires_structured_output: bool = False,
    ) -> RouteDecision:
        """
        根据多维度参数选择目标模型。

        Args:
            task_type: 任务类型
            latency_budget: 延迟预算
            risk_level: 风险等级
            context_length: 上下文 token 数
            requires_structured_output: 是否需要结构化输出

        Returns:
            RouteDecision 路由决策记录。
        """
        target = self._primary_route(
            task_type, latency_budget, risk_level, context_length, requires_structured_output
        )

        decision = RouteDecision(
            task_type=task_type,
            selected_model=target,
            latency_budget=latency_budget,
            risk_level=risk_level,
            context_length=context_length,
            requires_structured_output=requires_structured_output,
        )

        self._write_route_log(decision)
        logger.info(
            "路由决策: task=%s, latency=%s, risk=%s, ctx=%d → %s",
            task_type, latency_budget, risk_level, context_length, target,
        )
        return decision

    def _primary_route(
        self,
        task_type: TaskType,
        latency_budget: LatencyBudget,
        risk_level: RiskLevel,
        context_length: int,
        requires_structured_output: bool,
    ) -> ModelTarget:
        """
        主路由逻辑，按优先级依次匹配规则。

        规则优先级（从高到低）：
        1. dream/diary → skills（需求 5.4）
        2. reasoning OR context > 16k → cloud（需求 5.3）
        3. chat + <2s → host_8b（需求 5.2）
        4. 结构化输出 → cloud（保证质量）
        5. 默认 → host_8b
        """
        # 规则 1：dream/diary 专用路由（需求 5.4）
        if task_type in (TaskType.DREAM, TaskType.DIARY):
            return ModelTarget.SKILLS

        # 规则 2：reasoning 或超长上下文 → cloud（需求 5.3）
        if task_type == TaskType.REASONING or context_length > 16000:
            return ModelTarget.CLOUD

        # 规则 3：chat + 快速响应 → host_8b（需求 5.2）
        if task_type == TaskType.CHAT and latency_budget == LatencyBudget.FAST:
            return ModelTarget.HOST_8B

        # 规则 4：需要结构化输出 → cloud（保证 JSON 格式稳定性）
        if requires_structured_output:
            return ModelTarget.CLOUD

        # 规则 5：code/translate 默认 → host_8b
        if task_type in (TaskType.CODE, TaskType.TRANSLATE):
            return ModelTarget.HOST_8B

        # 默认：host_8b
        return ModelTarget.HOST_8B

    def select_with_fallback(
        self,
        task_type: TaskType,
        latency_budget: LatencyBudget = LatencyBudget.NORMAL,
        risk_level: RiskLevel = RiskLevel.LOW,
        context_length: int = 0,
        requires_structured_output: bool = False,
        failed_models: Optional[List[ModelTarget]] = None,
    ) -> RouteDecision:
        """
        带降级链的路由选择。

        当主路由失败时，按 cloud → host_8b → edge_3b 顺序降级（需求 5.5）。
        HIGH risk_level 禁止路由到 edge_3b（需求 5.6）。

        Args:
            failed_models: 已失败的模型列表，降级时跳过这些模型。

        Returns:
            RouteDecision 路由决策记录（is_fallback=True 表示已降级）。
        """
        failed_models = failed_models or []

        # 先尝试主路由
        primary_decision = self.select(
            task_type, latency_budget, risk_level, context_length, requires_structured_output
        )

        if primary_decision.selected_model not in failed_models:
            return primary_decision

        # 主路由失败，走降级链（需求 5.5）
        original_model = primary_decision.selected_model

        for fallback_model in FALLBACK_CHAIN:
            if fallback_model in failed_models:
                continue

            # HIGH risk_level 禁止路由到 edge_3b（需求 5.6）
            if risk_level == RiskLevel.HIGH and fallback_model == ModelTarget.EDGE_3B:
                logger.warning(
                    "HIGH risk_level 禁止路由到 edge_3b，跳过该降级目标"
                )
                continue

            # 检查模型可用性
            if self._availability_checker:
                try:
                    available = self._availability_checker(fallback_model)
                    if not available:
                        logger.warning("模型 %s 不可用，继续降级", fallback_model)
                        continue
                except Exception as exc:
                    logger.warning("可用性检查失败（%s）: %s，继续降级", fallback_model, exc)
                    continue

            fallback_decision = RouteDecision(
                task_type=task_type,
                selected_model=fallback_model,
                latency_budget=latency_budget,
                risk_level=risk_level,
                context_length=context_length,
                requires_structured_output=requires_structured_output,
                is_fallback=True,
                fallback_reason=f"主路由 {original_model} 失败，降级到 {fallback_model}",
                original_model=original_model,
            )

            self._write_route_log(fallback_decision)
            logger.warning(
                "路由降级: %s → %s（原因: %s）",
                original_model, fallback_model, fallback_decision.fallback_reason,
            )
            return fallback_decision

        # 所有降级目标均不可用
        logger.error(
            "所有模型均不可用（task=%s, risk=%s），无法完成路由",
            task_type, risk_level,
        )
        # 返回最后一个决策（调用方需处理此情况）
        # 修复：兜底决策同样需要写入路由日志，确保运维可感知"所有模型均不可用"的异常情况
        exhausted_decision = RouteDecision(
            task_type=task_type,
            selected_model=ModelTarget.HOST_8B,
            latency_budget=latency_budget,
            risk_level=risk_level,
            context_length=context_length,
            is_fallback=True,
            fallback_reason="所有模型均不可用",
            original_model=original_model,
        )
        self._write_route_log(exhausted_decision)
        return exhausted_decision

    def _write_route_log(self, decision: RouteDecision) -> None:
        """写入路由决策日志（llm_route_log）。"""
        if self._route_log_writer is None:
            return
        try:
            self._route_log_writer(decision)
        except Exception as exc:
            logger.error("路由日志写入失败: %s", exc)
