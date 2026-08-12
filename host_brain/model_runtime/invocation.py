"""调用已注册、已编译提示词的应用端口。

领域模块迁移期间仍可自行编译提示词，但跨模型边界必须携带注册的提示词身份并经唯一
``ModelAttemptService`` 调用。此处刻意不暴露 provider、模型、凭据、base URL、回退或
缓存参数，避免调用方绕过路由与审计权威。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.model_runtime.contracts import (
    ModelBudgetGuard,
    ModelAttemptRequest,
    ModelAttemptResult,
    PromptRef,
    RetryEvaluator,
)
from host_brain.model_runtime.model_attempt_service import ModelAttemptService


def compiled_prompt_request(
    *,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = True,
    retry_evaluator: RetryEvaluator | None = None,
    metadata: Mapping[str, Any] | None = None,
    model_budget_guard: ModelBudgetGuard | None = None,
) -> ModelAttemptRequest:
    """为已注册的编译消息提示词构造规范请求，并把消息作为受摘要保护的变量。"""

    normalized_messages = tuple(dict(message) for message in messages)
    return ModelAttemptRequest(
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        prompt=PromptRef(prompt_id, variables={"messages": normalized_messages}),
        deadline_ms=deadline_ms,
        foreground=foreground,
        retry_evaluator=retry_evaluator,
        metadata=dict(metadata or {}),
        model_budget_guard=model_budget_guard,
    )


async def complete_compiled_prompt(
    *,
    attempt_service: ModelAttemptService,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = True,
    retry_evaluator: RetryEvaluator | None = None,
    metadata: Mapping[str, Any] | None = None,
    model_budget_guard: ModelBudgetGuard | None = None,
) -> ModelAttemptResult:
    """经唯一异步模型权威调用编译提示词，使路由、预算和审计不可被旁路。"""

    request = compiled_prompt_request(
        prompt_id=prompt_id,
        messages=messages,
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        deadline_ms=deadline_ms,
        foreground=foreground,
        retry_evaluator=retry_evaluator,
        metadata=metadata,
        model_budget_guard=model_budget_guard,
    )
    return await attempt_service.complete(request)


def complete_compiled_prompt_sync(
    *,
    attempt_service: ModelAttemptService,
    prompt_id: str,
    messages: Sequence[Mapping[str, Any]],
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    deadline_ms: int,
    foreground: bool = True,
    retry_evaluator: RetryEvaluator | None = None,
    metadata: Mapping[str, Any] | None = None,
    model_budget_guard: ModelBudgetGuard | None = None,
) -> ModelAttemptResult:
    """经唯一同步模型权威调用编译提示词；语义与异步入口保持一致。"""

    request = compiled_prompt_request(
        prompt_id=prompt_id,
        messages=messages,
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        deadline_ms=deadline_ms,
        foreground=foreground,
        retry_evaluator=retry_evaluator,
        metadata=metadata,
        model_budget_guard=model_budget_guard,
    )
    return attempt_service.complete_sync(request)


__all__ = [
    "compiled_prompt_request",
    "complete_compiled_prompt",
    "complete_compiled_prompt_sync",
]
