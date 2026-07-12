"""Immutable route decisions derived from server-owned graph runtime data."""

from __future__ import annotations

from dataclasses import replace
import inspect
from typing import Any, Awaitable, Callable, Mapping

from host_brain.model_runtime.contracts import (
    ModelAttemptRequest,
    ProviderTarget,
    RenderedPrompt,
    RouteDecision,
    sha256_digest,
)
from host_brain.model_runtime.production_provider_adapters import production_adapter_id
from host_brain.model_runtime.route_policy import (
    CallableRoutePolicy,
    RouteNotConfiguredError,
    StaticRoutePolicy,
)


_GENERATION_KEYS = frozenset(
    {"temperature", "top_p", "top_k", "max_tokens", "stop", "seed", "presence_penalty", "frequency_penalty"}
)
ProductionRuntimeResolver = Callable[
    [ModelAttemptRequest, RenderedPrompt],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]

_SCENE_GENERATION_PARAMETERS: dict[str, dict[str, Any]] = {
    "THALAMUS_INTENT": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 400},
    "CHAT_SURFACE_MEMORY_REPAIR": {"temperature": 0.65, "max_tokens": 120},
}


def _required_text(runtime: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = str(runtime.get(name) or "").strip()
        if value:
            return value
    raise RouteNotConfiguredError(f"graph runtime missing required field: {names[0]}")


def _generation_parameters(runtime: Mapping[str, Any]) -> dict[str, Any]:
    for name in ("agent_gen_params", "surface_gen_params", "generation_parameters"):
        raw = runtime.get(name)
        if isinstance(raw, Mapping):
            return {str(key): value for key, value in raw.items()}
    return {}


def route_decision_from_graph_runtime(
    runtime: Mapping[str, Any],
    *,
    scene_type: str,
    cognitive_tier: str,
) -> RouteDecision:
    """Freeze one selected legacy runtime into one canonical target.

    Legacy fallback pools and provider functions are intentionally ignored.
    Retry and fallback can only be represented later as explicit service-owned
    attempts, never hidden inside this decision.
    """

    if not isinstance(runtime, Mapping) or not bool(runtime.get("available")):
        raise RouteNotConfiguredError("graph runtime is unavailable")
    provider = _required_text(runtime, "provider_name", "chat_provider_name").lower()
    model_id = _required_text(runtime, "model_id", "chat_model_id")
    adapter_id = str(runtime.get("model_attempt_adapter_id") or "").strip() or production_adapter_id(provider)
    try:
        timeout_ms = int(runtime.get("timeout_ms") or runtime.get("chat_timeout_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise RouteNotConfiguredError("graph runtime timeout_ms is invalid") from exc
    if timeout_ms <= 0:
        raise RouteNotConfiguredError("graph runtime timeout_ms must be positive")

    generation_parameters = _generation_parameters(runtime)
    unknown_generation_keys = set(generation_parameters) - _GENERATION_KEYS
    if unknown_generation_keys:
        raise RouteNotConfiguredError(
            "unsupported graph runtime generation parameters: "
            + ", ".join(sorted(str(key) for key in unknown_generation_keys))
        )
    target = ProviderTarget(
        adapter_id=adapter_id,
        provider=provider,
        model_id=model_id,
        timeout_ms=timeout_ms,
        metadata={"generation_parameters": generation_parameters},
    )
    scene = str(scene_type or "").strip().upper()
    tier = str(cognitive_tier or "").strip().upper()
    reason = str(runtime.get("route_reason_code") or "GRAPH_CONFIG_SELECTED_PROVIDER").strip()
    identity = {
        "scene_type": scene,
        "cognitive_tier": tier,
        "adapter_id": target.adapter_id,
        "provider": target.provider,
        "model_id": target.model_id,
        "timeout_ms": target.timeout_ms,
        "generation_parameters": generation_parameters,
        "reason": reason,
    }
    return RouteDecision(
        decision_id=f"graph-{sha256_digest(identity)[:24]}",
        targets=(target,),
        reason=reason,
        max_attempts=1,
        metadata={
            "source": "graph_config_llm_runtime",
            "scene_type": scene,
            "cognitive_tier": tier,
            "route_target": str(runtime.get("route_target") or "").strip(),
        },
    )


def build_production_route_policy(
    *,
    default_runtime: Mapping[str, Any] | None = None,
    runtime_by_route: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> StaticRoutePolicy:
    """Build a route policy from already-resolved server-owned runtimes."""

    decisions = {
        (str(scene).strip().upper(), str(tier).strip().upper()): route_decision_from_graph_runtime(
            runtime,
            scene_type=scene,
            cognitive_tier=tier,
        )
        for (scene, tier), runtime in dict(runtime_by_route or {}).items()
    }
    default = None
    if default_runtime is not None:
        default = route_decision_from_graph_runtime(
            default_runtime,
            scene_type="DEFAULT",
            cognitive_tier="DEFAULT",
        )
    if default is None and not decisions:
        raise RouteNotConfiguredError("no production graph runtimes configured")
    return StaticRoutePolicy(default, decisions=decisions)


async def _default_runtime_resolver(
    request: ModelAttemptRequest,
    prompt: RenderedPrompt,
) -> Mapping[str, Any]:
    del prompt
    from host_brain.app.graph_config_builder import (  # noqa: PLC0415
        resolve_host_llm_runtime_async,
        resolve_surface_reply_llm_runtime,
    )

    source_scene = "CHAT_SURFACE" if request.scene_type.startswith("CHAT_SURFACE") else request.scene_type
    base = await resolve_host_llm_runtime_async(
        rollout_key=request.request_id,
        scene_type=source_scene,
    )
    runtime = (
        resolve_surface_reply_llm_runtime(base_runtime=dict(base or {}))
        if request.scene_type.startswith("CHAT_SURFACE")
        else dict(base or {})
    )
    # Generation policy is server-owned.  Request metadata cannot alter it.
    override = _SCENE_GENERATION_PARAMETERS.get(request.scene_type)
    if override is not None:
        runtime = {**runtime, "agent_gen_params": dict(override)}
    return runtime


def build_server_owned_production_route_policy(
    runtime_resolver: ProductionRuntimeResolver | None = None,
) -> CallableRoutePolicy:
    """Resolve one secret-free route from current server configuration.

    Runtime resolution runs exactly once per ``ModelAttemptRequest``.  The
    resulting route contains one target; semantic/schema retries reuse that
    frozen target and remain owned by ``ModelAttemptService``.
    """

    resolver = runtime_resolver or _default_runtime_resolver

    async def _resolve(
        request: ModelAttemptRequest,
        prompt: RenderedPrompt,
    ) -> RouteDecision:
        value: Any = resolver(request, prompt)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, Mapping):
            raise RouteNotConfiguredError("production runtime resolver returned a non-mapping")
        runtime = dict(value)
        decision = route_decision_from_graph_runtime(
            runtime,
            scene_type=request.scene_type,
            cognitive_tier=request.cognitive_tier,
        )
        # Two attempts are enough for one explicit schema/semantic repair.  A
        # provider adapter still executes only one transport call per attempt.
        return replace(decision, max_attempts=2)

    return CallableRoutePolicy(_resolve)


__all__ = [
    "ProductionRuntimeResolver",
    "build_production_route_policy",
    "build_server_owned_production_route_policy",
    "route_decision_from_graph_runtime",
]
