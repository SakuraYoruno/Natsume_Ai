"""
routing_matrix.py —— 模型路由矩阵

按任务类型、延迟预算、风险等级进行能力路由（需求 5），
而不仅仅是故障切换：
- chat + <2s → host_14b（需求 5.2）
- chat + <2s + gaming → cloud_cn（国内低延迟，DeepSeek）
- reasoning / >16k → cloud（需求 5.3）
- dream/diary → skills（需求 5.4）
- 标准降级链：cloud → cloud_cn → host_14b（需求 5.5）
- Gaming 降级链：cloud_cn → cloud（禁用本地模型，避免炸显存）
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

# 标准降级链：cloud → cloud_cn
FALLBACK_CHAIN: List[ModelTarget] = [
    ModelTarget.CLOUD,
    ModelTarget.CLOUD_CN,
]

# Gaming 降级链：cloud_cn → cloud（禁用本地模型，Gaming 时 GPU 全负载跑游戏，本地推理会炸显存）
GAMING_FALLBACK_CHAIN: List[ModelTarget] = [
    ModelTarget.CLOUD_CN,
    ModelTarget.CLOUD,
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
        3. chat + <2s + gaming → cloud_cn（国内低延迟 DeepSeek）
        4. chat + <2s → host_14b（需求 5.2）
        5. 结构化输出 → cloud（保证质量）
        6. 默认 → host_14b
        """
        # 规则 1：dream/diary 专用路由（需求 5.4）
        if task_type in (TaskType.DREAM, TaskType.DIARY):
            return ModelTarget.SKILLS

        # 规则 2：reasoning 或超长上下文 → cloud（需求 5.3）
        if task_type == TaskType.REASONING or context_length > 16000:
            return ModelTarget.CLOUD

        # 规则 3：chat + 快速响应 + gaming 模式 → cloud_cn（国内低延迟 DeepSeek）
        # 全局云端策略：非 gaming 的 chat 也默认走 cloud，避免本地 14B 慢且答非所问
        if task_type == TaskType.CHAT and latency_budget == LatencyBudget.FAST:
            return ModelTarget.CLOUD

        # 规则 4：需要结构化输出 → cloud（保证 JSON 格式稳定性）
        if requires_structured_output:
            return ModelTarget.CLOUD

        # 规则 5：code/translate 默认 → cloud
        if task_type in (TaskType.CODE, TaskType.TRANSLATE):
            return ModelTarget.CLOUD

        # 默认：cloud
        return ModelTarget.CLOUD

    @staticmethod
    def _is_gaming_mode() -> bool:
        """检测当前是否处于 gaming 模式（读取 HOST_SYS_MODE 环境变量）。"""
        import os
        return os.environ.get("HOST_SYS_MODE", "normal").strip().lower() == "gaming"

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

        当主路由失败时，按 cloud → cloud_cn → host_14b 顺序降级（需求 5.5）。

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
        # Gaming 模式使用专用降级链（跳过海外云端，保证低延迟）
        original_model = primary_decision.selected_model
        chain = GAMING_FALLBACK_CHAIN if self._is_gaming_mode() else FALLBACK_CHAIN

        for fallback_model in chain:
            if fallback_model in failed_models:
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
            selected_model=ModelTarget.CLOUD,
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

    # ------------------------------------------------------------------
    # Ref #98: 按 token 成本预算路由
    # ------------------------------------------------------------------

    # 各模型每 1000 token 的相对成本（归一化，cloud=1.0 为基准）
    _MODEL_COST_PER_1K: dict = {
        "cloud": 1.0,
        "cloud_cn": 0.08,
        "host_14b": 0.05,
        "skills": 0.02,
    }

    def select_with_cost_budget(
        self,
        task_type: "TaskType",
        context_length: int = 0,
        latency_budget: "LatencyBudget | None" = None,
        risk_level: "RiskLevel" = None,
        requires_structured_output: bool = False,
        max_cost_per_1k: float = 0.5,
        failed_models: Optional[List["ModelTarget"]] = None,
    ) -> "RouteDecision":
        """
        按 token 成本预算路由（Ref #98）。

        在满足任务需求的前提下，优先选择成本不超过 max_cost_per_1k 的模型。
        若主路由模型成本超出预算，自动降级到成本更低的模型。

        Args:
            task_type: 任务类型
            context_length: 上下文 token 数
            latency_budget: 延迟预算（None 时使用 NORMAL）
            risk_level: 风险等级（None 时使用 LOW）
            requires_structured_output: 是否需要结构化输出
            max_cost_per_1k: 每 1000 token 的最大成本预算（相对值，cloud=1.0）
            failed_models: 已失败的模型列表

        Returns:
            RouteDecision（is_fallback=True 表示因成本预算降级）
        """
        from ..schemas.routing_types import LatencyBudget as _LB  # noqa: PLC0415

        _latency = latency_budget or _LB.NORMAL
        _risk = risk_level or RiskLevel.LOW
        _failed = failed_models or []

        # 先获取主路由决策
        primary = self.select(
            task_type=task_type,
            latency_budget=_latency,
            risk_level=_risk,
            context_length=context_length,
            requires_structured_output=requires_structured_output,
        )

        # 检查主路由模型的成本
        primary_model_name = primary.selected_model.value
        primary_cost = self._MODEL_COST_PER_1K.get(primary_model_name, 1.0)

        if primary_cost <= max_cost_per_1k and primary.selected_model not in _failed:
            # 主路由在预算内，直接返回
            return primary

        # 主路由超出预算，按成本从低到高寻找替代模型
        logger.info(
            "路由成本预算降级: 主路由 %s 成本 %.2f 超出预算 %.2f，寻找替代模型",
            primary_model_name, primary_cost, max_cost_per_1k,
        )

        # 按成本从低到高排序所有模型
        sorted_models = sorted(
            self._MODEL_COST_PER_1K.items(),
            key=lambda x: x[1],
        )

        for model_name, cost in sorted_models:
            if cost > max_cost_per_1k:
                continue

            # 转换为 ModelTarget
            try:
                from ..schemas.routing_types import ModelTarget as _MT  # noqa: PLC0415
                model_target = _MT(model_name)
            except ValueError:
                continue

            if model_target in _failed:
                continue

            # 检查模型可用性
            if self._availability_checker:
                try:
                    if not self._availability_checker(model_target):
                        continue
                except Exception:
                    continue

            fallback_decision = RouteDecision(
                task_type=task_type,
                selected_model=model_target,
                latency_budget=_latency,
                risk_level=_risk,
                context_length=context_length,
                requires_structured_output=requires_structured_output,
                is_fallback=True,
                fallback_reason=(
                    f"成本预算降级: {primary_model_name}(cost={primary_cost:.2f}) "
                    f"→ {model_name}(cost={cost:.2f})，预算上限={max_cost_per_1k:.2f}"
                ),
                original_model=primary.selected_model,
            )
            self._write_route_log(fallback_decision)
            logger.info(
                "成本预算路由: %s → %s（cost %.2f → %.2f，预算 %.2f）",
                primary_model_name, model_name, primary_cost, cost, max_cost_per_1k,
            )
            return fallback_decision

        # 所有模型均超出预算，返回主路由（调用方自行处理）
        logger.warning(
            "成本预算路由: 所有模型均超出预算 %.2f，返回主路由 %s",
            max_cost_per_1k, primary_model_name,
        )
        return primary
