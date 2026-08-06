"""Immutable route decisions derived from server-owned graph runtime data."""

from __future__ import annotations

from dataclasses import replace
import inspect
import os
from typing import Any, Awaitable, Callable, Mapping

from host_brain.model_runtime.contracts import (
    ModelAttemptRequest,
    ProviderTarget,
    RenderedPrompt,
    RouteDecision,
    sha256_digest,
)
from host_brain.model_runtime.production_provider_adapters import production_adapter_id
from host_brain.model_runtime.live_prompts import GRAPH_NODE_PROMPT_IDS, TRIBE_PROMPT_IDS
from host_brain.model_runtime.route_catalog import ServerRouteCatalog
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
    # The realtime Surface returns paired Chinese text and Japanese TTS JSON.
    # Reasoning-capable Flash models may spend ~200 completion tokens before
    # emitting content; 192 therefore produced an empty answer in both real
    # attempts.  The per-target 15 s timeout still bounds latency.
    "CHAT_SURFACE_REALTIME": {"temperature": 0.72, "top_p": 0.9, "max_tokens": 640},
    "CHAT_SURFACE_MEMORY_REPAIR": {"temperature": 0.65, "max_tokens": 120},
    "RESOURCE_MANAGEMENT": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 900},
    "MEMORY_DISTILLATION": {"temperature": 0.35, "max_tokens": 400},
    "MEMORY_DISTILLATION_BACKGROUND": {"temperature": 0.35, "max_tokens": 400},
    "EMOTION": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 400},
    "MULTIMODAL": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 1_200},
    "PLAN_SYNTHESIZE": {"temperature": 0.4, "top_p": 0.9, "max_tokens": 1_200},
    # Agent JSON includes both one bounded analysis and the publishable plan.
    # 1,200 tokens was repeatedly exhausted by real reasoning providers before
    # the closing JSON object, so the canonical contract could never validate.
    "NATSUME_AGENT": {"temperature": 0.3, "top_p": 0.85, "max_tokens": 2_400},
    "SUBAGENT": {"temperature": 0.3, "top_p": 0.85, "max_tokens": 1_200},
    "VISION_UI": {"temperature": 0.7, "max_tokens": 800},
    "VISION_ACTION_PLANNER": {"temperature": 0.2, "max_tokens": 1_200},
    "INNER_ACTIVITY": {"temperature": 0.68, "top_p": 0.92, "max_tokens": 320},
    "SURFACE_REPLY_JUDGE": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 1_000},
    "STARTUP_MODEL_WARMUP": {"temperature": 0.0, "max_tokens": 8},
    "SELF_REFLECTION": {"temperature": 0.55, "top_p": 0.9, "max_tokens": 800},
    "PREFRONTAL_PLAN": {"temperature": 0.1, "max_tokens": 128},
    "MIRROR_EMPATHY": {"temperature": 0.2, "max_tokens": 64},
    "INITIATIVE_TOPIC": {"temperature": 0.4, "max_tokens": 64},
    "HIPPOCAMPUS_RECALL": {"temperature": 0.35, "max_tokens": 600},
    "EMOTION_REACTIVITY": {"temperature": 0.4, "max_tokens": 256},
    "SILENCE_STATE": {"temperature": 0.35, "max_tokens": 256},
    "REPAIR_PLANNER": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 1_000},
    "SCHEDULER_NARRATIVE": {"temperature": 0.45, "max_tokens": 700},
    "PROACTIVE_TALK": {"temperature": 0.55, "max_tokens": 180},
    "EVOLUTION_DESIRE": {"temperature": 0.65, "top_p": 0.9, "max_tokens": 700},
    "EMERGENT_GROWTH": {"temperature": 0.55, "top_p": 0.9, "max_tokens": 700},
}

_PROMPT_GENERATION_PARAMETERS: dict[str, dict[str, Any]] = {
    GRAPH_NODE_PROMPT_IDS["chat_direct_ja"]: {"temperature": 0.45, "max_tokens": 420},
    GRAPH_NODE_PROMPT_IDS["core_reasoner_primary"]: {"temperature": 0.35, "max_tokens": 420},
    GRAPH_NODE_PROMPT_IDS["core_reasoner_secondary"]: {"temperature": 0.30, "max_tokens": 360},
    GRAPH_NODE_PROMPT_IDS["planner_constraints"]: {"temperature": 0.20, "max_tokens": 300},
    GRAPH_NODE_PROMPT_IDS["tool_planner"]: {"temperature": 0.20, "max_tokens": 280},
    GRAPH_NODE_PROMPT_IDS["safety_verifier"]: {"temperature": 0.10, "max_tokens": 220},
    GRAPH_NODE_PROMPT_IDS["consistency_verifier"]: {"temperature": 0.10, "max_tokens": 220},
    GRAPH_NODE_PROMPT_IDS["renderer_ja_c_style"]: {"temperature": 0.45, "max_tokens": 360},
    GRAPH_NODE_PROMPT_IDS["workbench_translate"]: {"temperature": 0.20, "max_tokens": 320},
    GRAPH_NODE_PROMPT_IDS["workbench_code_assist"]: {"temperature": 0.20, "max_tokens": 340},
    TRIBE_PROMPT_IDS["cerebellum_correction_narrative"]: {"temperature": 0.20, "max_tokens": 64},
    TRIBE_PROMPT_IDS["cerebellum_failure_pattern"]: {"temperature": 0.10, "max_tokens": 96},
    TRIBE_PROMPT_IDS["circadian_mood_hint"]: {"temperature": 0.20, "max_tokens": 32},
    TRIBE_PROMPT_IDS["endocrine_hormonal_hint"]: {"temperature": 0.20, "max_tokens": 32},
    TRIBE_PROMPT_IDS["glymphatic_consolidation_summary"]: {"temperature": 0.30, "max_tokens": 128},
    TRIBE_PROMPT_IDS["immune_semantic_intent"]: {"temperature": 0.0, "max_tokens": 64},
    TRIBE_PROMPT_IDS["interoception_narrative"]: {"temperature": 0.20, "max_tokens": 32},
    TRIBE_PROMPT_IDS["language_broca_opening_hint"]: {"temperature": 0.30, "max_tokens": 32},
    TRIBE_PROMPT_IDS["language_wernicke_deep_parse"]: {"temperature": 0.10, "max_tokens": 128},
    TRIBE_PROMPT_IDS["motor_speak_style"]: {"temperature": 0.10, "max_tokens": 48},
    TRIBE_PROMPT_IDS["pain_reward_hedonic_reflection"]: {"temperature": 0.30, "max_tokens": 64},
    TRIBE_PROMPT_IDS["peripheral_context_analysis"]: {"temperature": 0.10, "max_tokens": 48},
    TRIBE_PROMPT_IDS["proprioception_load_hint"]: {"temperature": 0.20, "max_tokens": 32},
    TRIBE_PROMPT_IDS["somatic_narrative"]: {"temperature": 0.20, "max_tokens": 32},
    TRIBE_PROMPT_IDS["vestibular_stability_assessment"]: {"temperature": 0.10, "max_tokens": 48},
    "pentest.failure.classify.json": {"temperature": 0.0, "top_p": 0.8, "max_tokens": 800},
    "pentest.knowledge.extract.json": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 1_200},
    "pentest.cognitive.hypotheses.json": {"temperature": 0.4, "top_p": 0.85, "max_tokens": 1_200},
    "pentest.react.choose.json": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 1_000},
    "pentest.react.propose.json": {"temperature": 0.3, "top_p": 0.8, "max_tokens": 1_000},
    "pentest.react.analyze.json": {"temperature": 0.0, "top_p": 0.8, "max_tokens": 800},
    "pentest.react.reflect.json": {"temperature": 0.2, "top_p": 0.8, "max_tokens": 1_200},
    "pentest.pack.plan.json": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 1_200},
}

_TIER_MODEL_DEFAULTS: dict[str, dict[str, str]] = {
    "gemini": {
        "A": "gemini-2.5-pro",
        "B": "gemini-2.5-flash-lite",
        "C": "gemini-2.5-flash-lite",
        "D": "gemini-2.5-flash-lite",
    },
    "deepseek": {
        "A": "deepseek-v4-pro",
        "B": "deepseek-v4-flash",
        "C": "deepseek-v4-flash",
        "D": "deepseek-v4-flash",
    },
}


def build_production_server_route_catalog() -> ServerRouteCatalog:
    """Build the server-owned catalog installed by production composition."""

    return ServerRouteCatalog(
        generation_by_prompt=_PROMPT_GENERATION_PARAMETERS,
        generation_by_scene=_SCENE_GENERATION_PARAMETERS,
    )


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
            resolved = {str(key): value for key, value in raw.items()}
            # Graph configuration historically used the SDK-specific name.
            # Normalize it at the server-owned route boundary; business
            # requests still cannot supply generation parameters.
            if "max_output_tokens" in resolved:
                if "max_tokens" in resolved and resolved["max_tokens"] != resolved["max_output_tokens"]:
                    raise RouteNotConfiguredError(
                        "graph runtime defines conflicting max_tokens/max_output_tokens"
                    )
                resolved["max_tokens"] = resolved.pop("max_output_tokens")
            return resolved
    return {}


def _env_token(value: str) -> str:
    return "_".join(
        part for part in "".join(ch if ch.isalnum() else " " for ch in value.upper()).split() if part
    ) or "DEFAULT"


def _apply_cognitive_tier_route(
    runtime: Mapping[str, Any],
    *,
    scene_type: str,
    cognitive_tier: str,
) -> dict[str, Any]:
    """Apply the single server-owned scene/tier model policy.

    This replaces the retired business-side pool selector.  It never reads
    request metadata, credentials, or caller-provided model hints.
    """

    resolved = dict(runtime)
    provider = str(resolved.get("provider_name") or resolved.get("chat_provider_name") or "").strip().lower()
    scene = _env_token(scene_type)
    tier = _env_token(cognitive_tier)
    explicit_model = str(
        os.environ.get(f"HOST_MODEL_ROUTE_SCENE_{scene}_TIER_{tier}_MODEL_ID")
        or os.environ.get(f"HOST_MODEL_ROUTE_TIER_{tier}_MODEL_ID")
        or ""
    ).strip()
    default_model = _TIER_MODEL_DEFAULTS.get(provider, {}).get(tier, "")
    model_id = explicit_model or default_model
    if not model_id:
        return resolved
    resolved["model_id"] = model_id
    resolved["chat_model_id"] = model_id
    resolved["route_reason_code"] = (
        f"SERVER_SCENE_TIER_ROUTE:{scene}:{tier}:"
        f"{'EXPLICIT' if explicit_model else 'DEFAULT'}"
    )
    return resolved


def _cloud_runtime(runtime: Mapping[str, Any]) -> dict[str, Any]:
    """Project the server-selected cloud lane for multimodal model scenes."""

    resolved = dict(runtime)
    resolved["provider_name"] = str(
        runtime.get("cloud_provider_name") or runtime.get("provider_name") or ""
    ).strip()
    resolved["model_id"] = str(
        runtime.get("cloud_model_id") or runtime.get("model_id") or ""
    ).strip()
    resolved["timeout_ms"] = int(
        runtime.get("cloud_timeout_ms") or runtime.get("timeout_ms") or 0
    )
    if "cloud_available" in runtime:
        resolved["available"] = bool(runtime.get("cloud_available"))
    resolved["route_reason_code"] = str(
        runtime.get("cloud_route_reason_code")
        or runtime.get("route_reason_code")
        or "SERVER_CLOUD_LANE"
    ).strip()
    return resolved


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
            "source": "server_route_catalog",
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


def build_server_owned_production_route_policy(
    runtime_resolver: ProductionRuntimeResolver | None = None,
    *,
    route_catalog: ServerRouteCatalog | None = None,
) -> CallableRoutePolicy:
    """Resolve one secret-free route from current server configuration.

    Runtime resolution runs exactly once per ``ModelAttemptRequest``.  The
    resulting route contains one target; semantic/schema retries reuse that
    frozen target and remain owned by ``ModelAttemptService``.
    """

    if runtime_resolver is not None and route_catalog is not None:
        raise ValueError("provide runtime_resolver or route_catalog, not both")
    catalog = route_catalog or build_production_server_route_catalog()

    async def _catalog_resolver(
        request: ModelAttemptRequest,
        prompt: RenderedPrompt,
    ) -> Mapping[str, Any]:
        return catalog.resolve(
            scene_type=request.scene_type,
            cognitive_tier=request.cognitive_tier,
            prompt_id=prompt.prompt_id,
        )

    resolver = runtime_resolver or _catalog_resolver

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
    "build_production_server_route_catalog",
    "build_production_route_policy",
    "build_server_owned_production_route_policy",
    "route_decision_from_graph_runtime",
]
