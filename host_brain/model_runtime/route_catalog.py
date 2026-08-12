"""Secret-free, server-owned model route catalog.

The catalog reads only non-secret routing configuration. Credentials, base
URLs, clients and transport availability belong exclusively to production
provider adapters.
"""

from __future__ import annotations

import os
from typing import Any, Mapping


_SUPPORTED_PROVIDERS = frozenset(
    {"openai", "openai_compatible", "gemini", "deepseek", "codex", "minimax"}
)
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
_NATSUME_AGENT_MODEL_DEFAULTS = {
    # The production text plane is intentionally unified on non-thinking
    # DeepSeek V4 Flash. Explicit scene/tier/provider overrides still win.
    "deepseek": "deepseek-v4-flash",
    "gemini": "gemini-2.5-pro",
}
_PROVIDER_MODEL_ENV = {
    "openai": "HOST_OPENAI_MODEL_ID",
    "openai_compatible": "HOST_OPENAI_MODEL_ID",
    "gemini": "HOST_GEMINI_MODEL_ID",
    "deepseek": "HOST_DEEPSEEK_MODEL_ID",
    "codex": "HOST_CODEX_MODEL_ID",
    "minimax": "HOST_MINIMAX_MODEL_ID",
}
_PROVIDER_MODEL_DEFAULT = {
    "openai": "qwen3:14b",
    "openai_compatible": "qwen3:14b",
    "gemini": "gemini-2.5-flash-lite",
    "deepseek": "deepseek-v4-flash",
    "codex": "gpt-5.4-mini",
    "minimax": "M2-her",
}
_FORBIDDEN_ROUTE_FIELDS = frozenset(
    {
        "api_key",
        "base_url",
        "base_url_override",
        "client",
        "credential",
        "credentials",
        "provider_fn",
        "secret",
        "token",
        "transport",
    }
)


def _known_model_provider(model_id: str) -> str | None:
    """Return a provider only for model families whose names are unambiguous."""

    # 仅对名称可明确归属的公开模型族作 provider 一致性判断；私有部署常用不透明别名，
    # 不应因无法识别而误拒绝，但已识别的跨 provider 配置必须被挡住。
    model = str(model_id or "").strip().lower()
    if model.startswith("gemini-"):
        return "gemini"
    if model.startswith("deepseek-"):
        return "deepseek"
    if model.startswith(("minimax-", "abab", "m1-", "m2-")):
        return "minimax"
    if model.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4")):
        return "openai"
    return None


def _model_matches_provider(*, provider: str, model_id: str) -> bool:
    model_provider = _known_model_provider(model_id)
    if model_provider is None:
        # Private deployments commonly expose opaque aliases.  Unknown names
        # remain valid; only a positively identified cross-provider family is
        # rejected.
        return True
    if model_provider == provider:
        return True
    return model_provider == "openai" and provider in {
        "openai",
        "openai_compatible",
        "codex",
    }


def _configured_model_candidates(*names: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            candidates.append((name, value))
    return candidates


def _env_token(value: str) -> str:
    return "_".join(
        part
        for part in "".join(ch if ch.isalnum() else " " for ch in value.upper()).split()
        if part
    ) or "DEFAULT"


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _bool_env(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _timeout_env(*names: str, default: int) -> int:
    # 部署超时经过固定下限/上限夹紧，避免环境变量的零、负值或异常大数让服务端路由
    # 产生无意义的即时失败或长期挂起。
    raw = _first_env(*names)
    try:
        parsed = int(raw) if raw else int(default)
    except ValueError:
        parsed = int(default)
    return max(800, min(parsed, 120_000))


def _assert_secret_free(value: Any, *, path: str = "route") -> None:
    # 路由目录是可投影的服务端配置，不承载 credential、base URL、client 或 transport；
    # 递归检查阻止秘密藏在嵌套 generation/诊断字段后进入决策或审计。
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_ROUTE_FIELDS:
                raise ValueError(f"secret/transport field forbidden in route catalog: {path}.{key}")
            _assert_secret_free(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_secret_free(nested, path=f"{path}[{index}]")


class ServerRouteCatalog:
    """Resolve one configured provider/model lane without inspecting secrets."""

    def __init__(
        self,
        *,
        generation_by_prompt: Mapping[str, Mapping[str, Any]] | None = None,
        generation_by_scene: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        # 静态参数表在创建时复制并做秘密扫描，后续 resolve 只读取此受控快照，
        # 不能因调用方修改原 Mapping 而改变运行中请求的生成策略。
        self._generation_by_prompt = {
            str(key): dict(value) for key, value in dict(generation_by_prompt or {}).items()
        }
        self._generation_by_scene = {
            str(key).strip().upper(): dict(value)
            for key, value in dict(generation_by_scene or {}).items()
        }
        _assert_secret_free(self._generation_by_prompt, path="generation_by_prompt")
        _assert_secret_free(self._generation_by_scene, path="generation_by_scene")

    def resolve(
        self,
        *,
        scene_type: str,
        cognitive_tier: str,
        prompt_id: str,
    ) -> dict[str, Any]:
        # 这是从请求分类到单一 provider/model lane 的服务器权威。它读取非秘密部署变量，
        # 但不读取提示词内容、用户 metadata、凭据或网络可用性。
        scene = str(scene_type or "").strip().upper() or "DEFAULT"
        tier = str(cognitive_tier or "").strip().upper() or "DEFAULT"
        scene_token = _env_token(scene)
        local_chat = (
            scene == "CHAT"
            and _bool_env("HOST_CHAT_LOCAL_LLM_ENABLED")
            and not _bool_env("HOST_CHAT_FORCE_CLOUD")
        )
        if local_chat:
            # 本地聊天是显式开关的独立 lane；强制云端开关优先，防止离线模型配置在
            # 运维要求使用云端时悄然截获对话。
            provider = _first_env(
                "HOST_CHAT_LOCAL_LLM_PROVIDER",
                "HOST_CHAT_HOST_14B_PROVIDER",
            )
        elif scene == "MULTIMODAL":
            # 多模态不得继承全局纯文本 provider，避免图像/附件在未声明视觉能力的
            # 模型上发送；默认选择专用云 provider。
            # Attachment observation is an independent model plane.  A global
            # text-provider setting must not silently receive image content.
            provider = _first_env(
                f"HOST_MODEL_ROUTE_SCENE_{scene_token}_PROVIDER",
                "HOST_MULTIMODAL_PROVIDER",
            ) or "gemini"
        elif scene == "CHAT_SURFACE_REALTIME":
            provider = _first_env(
                f"HOST_MODEL_ROUTE_SCENE_{scene_token}_PROVIDER",
                "HOST_REALTIME_COMPANION_PROVIDER",
                "HOST_LLM_PROVIDER",
            )
        else:
            provider = _first_env(
                f"HOST_MODEL_ROUTE_SCENE_{scene_token}_PROVIDER",
                "HOST_LLM_PROVIDER",
            )
        provider = provider.lower() or ("openai" if local_chat else "deepseek")
        if provider not in _SUPPORTED_PROVIDERS:
            # 未登记 provider 没有安全回退，路由组合期/调用期都必须明确配置其传输与
            # 费用能力后才可使用。
            raise ValueError(f"unsupported route catalog provider: {provider}")

        provider_model_override = _first_env(_PROVIDER_MODEL_ENV[provider])
        provider_model = provider_model_override or _PROVIDER_MODEL_DEFAULT[provider]
        if local_chat:
            # 本地 lane 使用独立模型与较长上限，避免共用云端的短路由 timeout；其
            # route_target 同时为诊断投影提供明确的执行位置。
            model_id = _first_env(
                "HOST_CHAT_LOCAL_LLM_MODEL_ID",
                "HOST_CHAT_HOST_14B_MODEL_ID",
            ) or provider_model
            timeout_ms = _timeout_env(
                "HOST_CHAT_LOCAL_LLM_TIMEOUT_MS",
                "HOST_CHAT_HOST_14B_TIMEOUT_MS",
                default=90_000,
            )
            lane = "local_chat"
            route_target = "host_14b"
        else:
            role_model_names: tuple[str, ...] = ()
            role_timeout_names: tuple[str, ...] = ()
            provider_role_model_name = ""
            default_timeout = 15_000
            lane = "default"
            route_target = "cloud"
            if scene == "CHAT_SURFACE_REALTIME":
                # 实时陪伴拥有专门的模型/超时命名空间，不能被普通工具或规划路由的
                # 全局参数意外压缩到无法完成一轮自然回复。
                role_model_names = (
                    "HOST_REALTIME_COMPANION_MODEL_ID",
                    "HOST_CHAT_AGENT_MODEL_ID",
                )
                provider_role_model_name = (
                    f"HOST_{_env_token(provider)}_REALTIME_COMPANION_MODEL_ID"
                )
                role_timeout_names = (
                    "HOST_REALTIME_COMPANION_TIMEOUT_MS",
                    "HOST_CHAT_AGENT_TIMEOUT_MS",
                )
                default_timeout = 15_000
                lane = "realtime_companion"
            elif scene.startswith("CHAT_SURFACE"):
                role_model_names = ("HOST_SURFACE_REPLY_MODEL_ID", "HOST_CHAT_AGENT_MODEL_ID")
                provider_role_model_name = (
                    f"HOST_{_env_token(provider)}_SURFACE_REPLY_MODEL_ID"
                )
                role_timeout_names = ("HOST_SURFACE_REPLY_TIMEOUT_MS", "HOST_CHAT_AGENT_TIMEOUT_MS")
                default_timeout = 30_000
                lane = "surface_reply"
            elif scene == "SUBAGENT":
                role_model_names = ("HOST_SUBAGENT_MODEL_ID",)
                provider_role_model_name = f"HOST_{_env_token(provider)}_SUBAGENT_MODEL_ID"
                role_timeout_names = ("HOST_SUBAGENT_TIMEOUT_MS",)
                default_timeout = 30_000
                lane = "subagent"
            elif scene == "NATSUME_AGENT":
                # Agent lane 保留更长的显式 deadline 与模型优先级；它仍只生成单一 target，
                # 后续重试预算在 ModelAttemptService 中受控。
                role_model_names = ("HOST_NATSUME_AGENT_MODEL_ID",)
                provider_role_model_name = (
                    f"HOST_{_env_token(provider)}_NATSUME_AGENT_MODEL_ID"
                )
                role_timeout_names = ("HOST_NATSUME_AGENT_MODEL_TIMEOUT_MS",)
                default_timeout = 45_000
                lane = "agent"
            elif scene in {"EXEC_INTENT", "THALAMUS_INTENT"}:
                role_model_names = ("HOST_EXECUTIVE_ROUTER_MODEL_ID", "HOST_DISPATCH_AGENT_MODEL_ID")
                provider_role_model_name = (
                    f"HOST_{_env_token(provider)}_EXECUTIVE_ROUTER_MODEL_ID"
                )
                role_timeout_names = ("HOST_EXECUTIVE_ROUTER_TIMEOUT_MS", "HOST_DISPATCH_AGENT_TIMEOUT_MS")
                default_timeout = 5_000
                lane = "executive_router"
            elif scene == "MULTIMODAL":
                role_model_names = (
                    "HOST_MULTIMODAL_MODEL_ID_SYNC",
                    "HOST_VISION_LLM_MODEL_ID",
                )
                provider_role_model_name = f"HOST_{_env_token(provider)}_VISION_MODEL_ID"
                role_timeout_names = (
                    "HOST_MULTIMODAL_TIMEOUT_MS",
                    "HOST_VISION_LLM_TIMEOUT_MS",
                )
                default_timeout = 20_000
                lane = "multimodal"
            elif scene in {"VISION_UI", "VISION_ACTION_PLANNER"}:
                role_model_names = ("HOST_VISION_LLM_MODEL_ID",)
                provider_role_model_name = f"HOST_{_env_token(provider)}_VISION_MODEL_ID"
                role_timeout_names = ("HOST_VISION_LLM_TIMEOUT_MS",)
                default_timeout = 20_000
                lane = "vision"

            tier_default = _TIER_MODEL_DEFAULTS.get(provider, {}).get(tier, "")
            tier_driven_scene = (
                scene in {"NATSUME_AGENT", "CHAT", "TRIBE_INTERNAL"}
                or scene == "CHAT_SURFACE_REALTIME"
                or scene.startswith("GRAPH_")
            )
            if scene == "NATSUME_AGENT":
                provider_fallback = (
                    _NATSUME_AGENT_MODEL_DEFAULTS.get(provider, "")
                    or tier_default
                    or provider_model_override
                    or provider_model
                )
            else:
                provider_fallback = (
                    tier_default or provider_model_override or provider_model
                    if tier_driven_scene
                    else provider_model_override or tier_default or provider_model
                )
            candidate_names = (
                f"HOST_MODEL_ROUTE_SCENE_{scene_token}_TIER_{_env_token(tier)}_MODEL_ID",
                f"HOST_MODEL_ROUTE_TIER_{_env_token(tier)}_MODEL_ID",
                f"HOST_MODEL_ROUTE_SCENE_{scene_token}_MODEL_ID",
                *((provider_role_model_name,) if provider_role_model_name else ()),
                *role_model_names,
            )
            model_candidates = _configured_model_candidates(*candidate_names)
            model_candidates.append(("provider_fallback", provider_fallback))
            ignored_model_sources: list[str] = []
            model_id = ""
            model_selection_source = ""
            for source, candidate in model_candidates:
                # 依优先级选择第一个 provider 兼容候选，同时记录被忽略的跨 provider
                # 覆盖来源，便于排障却不把错误模型传给 adapter。
                if _model_matches_provider(provider=provider, model_id=candidate):
                    model_id = candidate
                    model_selection_source = source
                    break
                ignored_model_sources.append(source)
            if not model_id:
                # 所有候选跨 provider 时拒绝，而不是降格为任意默认模型，防止配置错误
                # 将请求发送到能力/合规性不匹配的外部服务。
                raise ValueError(
                    f"no provider-compatible model configured for provider={provider} scene={scene}"
                )
            timeout_env_names = (
                f"HOST_MODEL_ROUTE_SCENE_{scene_token}_TIMEOUT_MS",
                *role_timeout_names,
            )
            # Ordinary companion chat is an explicit latency lane.  A global
            # provider timeout is commonly tuned for router/tool calls and may
            # be shorter than one real streamed answer; inheriting it here
            # silently turns a healthy provider response into ``NO_REPLY``.
            # The lane therefore owns its deadline, while every other route
            # retains the legacy global fallback for compatibility.
            if scene not in {"CHAT_SURFACE_REALTIME", "NATSUME_AGENT"}:
                timeout_env_names = (*timeout_env_names, "HOST_LLM_TIMEOUT_MS")
            timeout_ms = _timeout_env(
                *timeout_env_names,
                default=default_timeout,
            )

        generation = dict(
            self._generation_by_prompt.get(str(prompt_id))
            or self._generation_by_scene.get(scene)
            or {}
        )
        # prompt 参数优先于 scene 默认，二者都来自目录初始快照；路由再施加特定模型的
        # 能力限制，调用者没有机会传入未审计的 generation 值。
        if provider == "deepseek" and model_id.lower().startswith("deepseek-v4-flash"):
            generation["thinking_mode"] = "disabled"
        route = {
            "available": True,
            "provider_name": provider,
            "model_id": model_id,
            "timeout_ms": timeout_ms,
            "route_target": route_target,
            "route_lane": lane,
            "route_reason_code": f"SERVER_ROUTE_CATALOG:{lane.upper()}",
            "scene_type": scene,
            "cognitive_tier": tier,
            "generation_parameters": generation,
        }
        if not local_chat:
            route["model_selection_source"] = model_selection_source
            route["route_config_warnings"] = [
                f"INCOMPATIBLE_MODEL_OVERRIDE_IGNORED:{source}"
                for source in ignored_model_sources
            ]
        # 返回前再次扫描最终 route，防止未来新增字段绕过构造期检查并意外进入
        # RouteDecision/审计投影。
        _assert_secret_free(route)
        return route


__all__ = ["ServerRouteCatalog"]
