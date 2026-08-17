"""由服务端拥有的图运行时数据导出的不可变路由决定。"""

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
from host_brain.model_runtime.live_prompts import (
    CODE_BENEFIT_EXECUTION_PROMPT_ID,
    CODE_BENEFIT_GENERATION_PARAMETERS,
    GRAPH_NODE_PROMPT_IDS,
    RESEARCH_EXECUTION_PROMPT_ID,
    RESEARCH_QUALITY_VERIFIER_PROMPT_ID,
    TRIBE_PROMPT_IDS,
)
from host_brain.model_runtime.route_catalog import ServerRouteCatalog
from host_brain.model_runtime.route_policy import (
    CallableRoutePolicy,
    RouteNotConfiguredError,
    StaticRoutePolicy,
)


_GENERATION_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "stop",
        "seed",
        "presence_penalty",
        "frequency_penalty",
        "thinking_mode",
    }
)
ProductionRuntimeResolver = Callable[
    [ModelAttemptRequest, RenderedPrompt],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]

_SCENE_GENERATION_PARAMETERS: dict[str, dict[str, Any]] = {
    "THALAMUS_INTENT": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 400},
    # 实时 Surface 返回配对的中文文本与日语 TTS JSON。普通陪伴需要直接表达而非隐藏
    # 推理预算；provider adapter 只会将此提示用于明确支持它的 provider。Plan/Research
    # 路由保留各自默认值，每个 target 的 15 秒超时仍约束延迟。
    "CHAT_SURFACE_REALTIME": {
        "temperature": 0.72,
        "top_p": 0.9,
        "max_tokens": 640,
        "thinking_mode": "disabled",
    },
    "CHAT_SURFACE_MEMORY_REPAIR": {"temperature": 0.65, "max_tokens": 120},
    "RESOURCE_MANAGEMENT": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 900},
    "MEMORY_DISTILLATION": {"temperature": 0.35, "max_tokens": 400},
    "MEMORY_DISTILLATION_BACKGROUND": {"temperature": 0.35, "max_tokens": 400},
    "EMOTION": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 400},
    "MULTIMODAL": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 1_200},
    "PLAN_SYNTHESIZE": {"temperature": 0.4, "top_p": 0.9, "max_tokens": 1_200},
    # Agent JSON 同时包含有界分析和可发布计划。真实推理 provider 常在闭合 JSON 对象
    # 前耗尽 1,200 tokens，导致规范契约无法校验，因此此处保留更高上限。
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
    CODE_BENEFIT_EXECUTION_PROMPT_ID: dict(CODE_BENEFIT_GENERATION_PARAMETERS),
    RESEARCH_EXECUTION_PROMPT_ID: {
        "temperature": 0.1,
        "top_p": 0.8,
        "max_tokens": 900,
        "thinking_mode": "disabled",
    },
    RESEARCH_QUALITY_VERIFIER_PROMPT_ID: {
        "temperature": 0.0,
        "top_p": 0.8,
        "max_tokens": 240,
        "thinking_mode": "disabled",
    },
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
        "A": "deepseek-v4-flash",
        "B": "deepseek-v4-flash",
        "C": "deepseek-v4-flash",
        "D": "deepseek-v4-flash",
    },
}


def build_production_server_route_catalog() -> ServerRouteCatalog:
    """Build the server-owned catalog installed by production composition."""

    # 目录把按 prompt 的精细参数与按 scene 的默认参数集中到服务端；业务调用方只能提供
    # 已验证的分类与 prompt 身份，不能在请求中夹带温度、模型或 provider 选择。
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
    # 只接收历史运行时中的有限字段别名，并在路由边界规范化；未知生成参数随后会被
    # route_decision_from_graph_runtime 拒绝，避免配置漂移悄然扩大模型控制面。
    for name in ("agent_gen_params", "surface_gen_params", "generation_parameters"):
        raw = runtime.get(name)
        if isinstance(raw, Mapping):
            resolved = {str(key): value for key, value in raw.items()}
            # 图配置历史上使用 SDK 特定名称；在服务端路由边界统一归一化，业务请求仍不能
            # 自行提供生成参数。
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
    """应用唯一由服务端拥有的场景/认知层级模型策略。

    该策略取代已退役的业务侧池选择器，绝不读取请求元数据、凭据或调用方模型提示。
    """

    # 路由模型只能来自部署环境的 scene/tier 策略或固定默认，不读取 request metadata；
    # 这样文本、用户输入或工作流参数均不能越权挑选成本/能力不同的模型。
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
    """为多模态模型场景投影服务端选定的云端通道。"""

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
    """将一个已选旧运行时冻结为一个规范 target。

    旧 fallback 池和 provider 函数被有意忽略。重试与回退只能在后续由服务拥有的显式
    attempt 表示，绝不能隐藏在此决定内。
    """

    # 在这里冻结一次性决策，旧 runtime 中的 pool/fallback 函数不会穿透；重试由尝试
    # 服务以相同 target 显式记录，从而保证一次请求的路由身份可审计、可复放。
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
        # 新参数必须先进入版本化白名单，不能由配置直接传往 adapter，避免供应商私有
        # 开关绕过输出限制、推理预算或安全约束。
        raise RouteNotConfiguredError(
            "unsupported graph runtime generation parameters: "
            + ", ".join(sorted(str(key) for key in unknown_generation_keys))
        )
    if "thinking_mode" in generation_parameters:
        thinking_mode = str(generation_parameters["thinking_mode"] or "").strip().lower()
        if thinking_mode not in {"enabled", "disabled"}:
            raise RouteNotConfiguredError(
                "graph runtime thinking_mode must be enabled or disabled"
            )
        generation_parameters["thinking_mode"] = thinking_mode
    if provider == "deepseek" and model_id.lower().startswith("deepseek-v4-flash"):
        # 特定 provider/model 的能力约束在服务端强制覆盖运行时配置，防止快速路线
        # 因遗留 thinking_mode 设置而消耗不适配的隐藏推理预算。
        generation_parameters["thinking_mode"] = "disabled"
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
    # decision_id 覆盖所有影响 transport 的字段；任何模型、超时或生成参数变动都会
    # 产生不同身份，防止审计把不同实际调用合并为同一决策。
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
    """从已解析的服务端运行时构造路由策略。"""

    # 该构造器只消费已经解析好的服务端 runtime。空目录没有安全默认路由，组合失败
    # 比启动后意外调用外部模型更可诊断。
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
    """从当前服务端配置解析一条不含秘密的路由。

    每个 ``ModelAttemptRequest`` 只解析一次运行时，结果路由只含一个 target；语义/schema
    重试复用该冻结 target，且始终归 ``ModelAttemptService`` 所有。
    """

    # resolver 与 catalog 二选一，避免两套权威来源对同一请求给出无法比较的路由；
    # catalog 是默认的可复现生产路径。
    if runtime_resolver is not None and route_catalog is not None:
        raise ValueError("provide runtime_resolver or route_catalog, not both")
    catalog = route_catalog or build_production_server_route_catalog()

    async def _catalog_resolver(
        request: ModelAttemptRequest,
        prompt: RenderedPrompt,
    ) -> Mapping[str, Any]:
        # 目录只按照请求分类和已渲染 prompt_id 查询，提示词正文与元数据不参与选路。
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
        # 运行时解析每次请求只执行一次，结果马上冻结为 RouteDecision；即便后续发生
        # schema 修复重试，也不会重新解析配置而切换 provider/model。
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
        # 两次 attempt 足以完成一次显式 schema/语义修复；provider adapter 在每个 attempt
        # 内仍只执行一次传输调用。
        # 仅预留一次显式语义/结构修复；adapter 在每个 attempt 内仍只做一次传输调用，
        # 因此重试上限可被预算和审计准确计数。
        return replace(decision, max_attempts=2)

    return CallableRoutePolicy(_resolve)


__all__ = [
    "ProductionRuntimeResolver",
    "build_production_server_route_catalog",
    "build_production_route_policy",
    "build_server_owned_production_route_policy",
    "route_decision_from_graph_runtime",
]
