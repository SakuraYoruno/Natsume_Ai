"""Task-bound model runtime for governed non-Agent Skills.

The durable Task budget remains the sole authority.  This adapter projects its
frozen allowance into a typed model guard, reduces that allowance after every
canonical provider measurement, and emits only a secret-free worker
attestation for the outer Task budget settlement.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.model_runtime.contracts import (
    ModelAttemptResult,
    ModelBudgetGuard,
    RetryEvaluator,
)
from host_brain.model_runtime.governed_budget import GovernedAgentModelBudget
from host_brain.model_runtime.invocation import complete_compiled_prompt_sync
from host_brain.model_runtime.model_attempt_service import ModelAttemptService
from host_brain.model_runtime.trace import model_attempt_result_trace


GOVERNED_SKILL_MODEL_BUDGET_ATTESTATION_SCHEMA_VERSION = (
    "Natsume.task_runtime.governed_skill_model_budget_attestation.v1"
)


class GovernedSkillModelBudgetRuntime:
    """Non-expandable model port owned by one governed Skill Attempt."""

    def __init__(
        self,
        *,
        attempt_service: ModelAttemptService,
        model_budget_guard: ModelBudgetGuard,
        task_request_id: str,
    ) -> None:
        # 此适配器只接受能够产出规范测量的 attempt_service 与已经冻结的任务预算；
        # 它不创建或扩张额度，因此 Skill 无法借模型调用绕过 Task 的结算权威。
        if not callable(getattr(attempt_service, "complete_sync", None)):
            raise TypeError("attempt_service must implement complete_sync()")
        if not isinstance(model_budget_guard, ModelBudgetGuard):
            raise TypeError("model_budget_guard must be a ModelBudgetGuard")
        normalized_request_id = str(task_request_id or "").strip()
        if not normalized_request_id:
            raise ValueError("task_request_id is required")
        self._attempt_service = attempt_service
        self._budget = GovernedAgentModelBudget(model_budget_guard)
        self._task_request_id = normalized_request_id
        self._traces: list[dict[str, Any]] = []

    @property
    def model_budget_guard(self) -> ModelBudgetGuard:
        return self._budget.base_guard

    @property
    def task_request_id(self) -> str:
        return self._task_request_id

    def complete_sync(
        self,
        *,
        prompt_id: str,
        messages: Sequence[Mapping[str, Any]],
        scene_type: str,
        cognitive_tier: str,
        deadline_ms: int,
        foreground: bool = True,
        retry_evaluator: RetryEvaluator | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ModelAttemptResult:
        """Invoke one model request under the exact remaining Task allowance."""

        # 同一 Task 的首个调用复用 task_request_id，后续调用派生稳定序号，既保持
        # 单次任务归属又能在账本中区分重试之外的多次模型消耗。
        call_index = len(self._traces) + 1
        request_id = (
            self._task_request_id
            if call_index == 1
            else f"{self._task_request_id}::skill-model::{call_index}"
        )
        # 每次从剩余额度投影新的 guard；deadline 取两者更早值，避免 Skill 提供较晚
        # 截止时间而突破 Task 在准入时已经批准的资源时窗。
        guard = self._budget.next_guard()
        result = complete_compiled_prompt_sync(
            attempt_service=self._attempt_service,
            prompt_id=prompt_id,
            messages=messages,
            request_id=request_id,
            scene_type=scene_type,
            cognitive_tier=cognitive_tier,
            deadline_ms=min(int(deadline_ms), guard.deadline_ms),
            foreground=foreground,
            retry_evaluator=retry_evaluator,
            metadata=metadata,
            model_budget_guard=guard,
        )
        # 仅在规范结果已生成后消费 trace，预算递减依据供应商测量而非调用方估计；
        # 即使模型失败，该尝试的真实资源使用也会进入后续额度计算。
        trace = model_attempt_result_trace(result)
        self._budget.consume_trace(trace)
        self._traces.append(trace)
        return result

    def attestation(self) -> dict[str, Any] | None:
        """Return the immutable, authority-free usage proof for Task settlement."""

        # 无模型调用时不伪造零用量证明；有记录时只导出任务结算所需的身份与 trace，
        # 不携带 provider 凭据、提示词正文或改变预算的授权字段。
        if not self._traces:
            return None
        guard = self.model_budget_guard
        return {
            "schema_version": (
                GOVERNED_SKILL_MODEL_BUDGET_ATTESTATION_SCHEMA_VERSION
            ),
            "task_request_id": self._task_request_id,
            "budget_id": guard.budget_id,
            "graph_id": guard.graph_id,
            "graph_revision": guard.graph_revision,
            "node_id": guard.node_id,
            "attempt_id": guard.attempt_id,
            "model_attempts": [dict(item) for item in self._traces],
        }


__all__ = [
    "GOVERNED_SKILL_MODEL_BUDGET_ATTESTATION_SCHEMA_VERSION",
    "GovernedSkillModelBudgetRuntime",
]
