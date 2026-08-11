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
        del prompt
        decision = self._decisions.get((request.scene_type, request.cognitive_tier), self._default)
        if decision is None:
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
