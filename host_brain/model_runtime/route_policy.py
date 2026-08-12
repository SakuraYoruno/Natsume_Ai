"""Injectable route policies for the model-attempt service."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Mapping

from host_brain.model_runtime.contracts import (
    ModelAttemptRequest,
    RenderedPrompt,
    RouteDecision,
)


class RoutePolicyError(RuntimeError):
    pass


class RouteNotConfiguredError(RoutePolicyError):
    pass


RouteResolver = Callable[
    [ModelAttemptRequest, RenderedPrompt],
    RouteDecision | Awaitable[RouteDecision],
]


class StaticRoutePolicy:
    """Return a fixed or scene/tier-specific immutable route decision."""

    def __init__(
        self,
        default: RouteDecision | None = None,
        *,
        decisions: Mapping[tuple[str, str], RouteDecision] | None = None,
    ) -> None:
        # 键在组合时规范化为场景/认知层级对，使路由查找不受调用方大小写影响；值仍是
        # 不可变 RouteDecision，策略本身不临时拼接 provider 或凭据。
        self._default = default
        self._decisions = {
            (str(scene).strip().upper(), str(tier).strip().upper()): decision
            for (scene, tier), decision in dict(decisions or {}).items()
        }

    async def resolve(
        self,
        request: ModelAttemptRequest,
        prompt: RenderedPrompt,
    ) -> RouteDecision:
        # 静态策略只以已验证的请求分类选路，忽略提示词正文，避免内容本身影响模型
        # 权威或成为绕过场景/tier 治理的隐式路由信号。
        del prompt
        decision = self._decisions.get((request.scene_type, request.cognitive_tier), self._default)
        if decision is None:
            # 没有明确映射时 fail-closed；不能落入随机/默认 provider，以保证新增场景
            # 必须显式获得成本、能力和审计策略。
            raise RouteNotConfiguredError(
                f"no model route for scene={request.scene_type} tier={request.cognitive_tier}"
            )
        return decision


class CallableRoutePolicy:
    """Compatibility port around an injected resolver.

    The callback must return the frozen ``RouteDecision`` contract.  This
    class intentionally does not understand legacy runtime dictionaries.
    """

    def __init__(self, resolver: RouteResolver) -> None:
        if not callable(resolver):
            raise TypeError("route resolver must be callable")
        self._resolver = resolver

    async def resolve(
        self,
        request: ModelAttemptRequest,
        prompt: RenderedPrompt,
    ) -> RouteDecision:
        # 兼容回调可同步或异步，但必须最终返回冻结契约。此处不接受旧字典，防止调用
        # 点以未验证的字段改变路由预算或重试参数。
        result: Any = self._resolver(request, prompt)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, RouteDecision):
            raise RoutePolicyError(
                f"route resolver returned {type(result).__name__}, expected RouteDecision"
            )
        return result


class RejectingRoutePolicy:
    """Safe composition default until production adapters are installed."""

    def __init__(self, reason: str = "MODEL_ROUTE_NOT_CONFIGURED") -> None:
        self._reason = str(reason or "MODEL_ROUTE_NOT_CONFIGURED")

    async def resolve(
        self,
        request: ModelAttemptRequest,
        prompt: RenderedPrompt,
    ) -> RouteDecision:
        # 未装配生产路由时，默认策略明确拒绝所有模型调用；这比“演示默认路由”更安全，
        # 可防止启动配置遗漏时意外消耗外部模型资源。
        del request, prompt
        raise RouteNotConfiguredError(self._reason)


__all__ = [
    "CallableRoutePolicy",
    "RejectingRoutePolicy",
    "RouteNotConfiguredError",
    "RoutePolicyError",
    "RouteResolver",
    "StaticRoutePolicy",
]
