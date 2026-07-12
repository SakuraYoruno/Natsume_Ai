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
        "A": "deepseek-v4-pro",
        "B": "deepseek-v4-flash",
        "C": "deepseek-v4-flash",
        "D": "deepseek-v4-flash",
    },
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
    raw = _first_env(*names)
    try:
        parsed = int(raw) if raw else int(default)
    except ValueError:
        parsed = int(default)
    return max(800, min(parsed, 120_000))


def _assert_secret_free(value: Any, *, path: str = "route") -> None:
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
        scene = str(scene_type or "").strip().upper() or "DEFAULT"
        tier = str(cognitive_tier or "").strip().upper() or "DEFAULT"
        scene_token = _env_token(scene)
        local_chat = (
            scene == "CHAT"
            and _bool_env("HOST_CHAT_LOCAL_LLM_ENABLED")
            and not _bool_env("HOST_CHAT_FORCE_CLOUD")
        )
        provider = (
            _first_env("HOST_CHAT_LOCAL_LLM_PROVIDER", "HOST_CHAT_HOST_14B_PROVIDER")
            if local_chat
            else _first_env(f"HOST_MODEL_ROUTE_SCENE_{scene_token}_PROVIDER", "HOST_LLM_PROVIDER")
        ).lower() or ("openai" if local_chat else "gemini")
        if provider not in _SUPPORTED_PROVIDERS:
            raise ValueError(f"unsupported route catalog provider: {provider}")

        provider_model_override = _first_env(_PROVIDER_MODEL_ENV[provider])
        provider_model = provider_model_override or _PROVIDER_MODEL_DEFAULT[provider]
        if local_chat:
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
            default_timeout = 15_000
            lane = "default"
            route_target = "cloud"
            if scene.startswith("CHAT_SURFACE"):
                role_model_names = ("HOST_SURFACE_REPLY_MODEL_ID", "HOST_CHAT_AGENT_MODEL_ID")
                role_timeout_names = ("HOST_SURFACE_REPLY_TIMEOUT_MS", "HOST_CHAT_AGENT_TIMEOUT_MS")
                default_timeout = 30_000
                lane = "surface_reply"
            elif scene == "SUBAGENT":
                role_model_names = ("HOST_SUBAGENT_MODEL_ID",)
                role_timeout_names = ("HOST_SUBAGENT_TIMEOUT_MS",)
                default_timeout = 30_000
                lane = "subagent"
            elif scene in {"EXEC_INTENT", "THALAMUS_INTENT"}:
                role_model_names = ("HOST_EXECUTIVE_ROUTER_MODEL_ID", "HOST_DISPATCH_AGENT_MODEL_ID")
                role_timeout_names = ("HOST_EXECUTIVE_ROUTER_TIMEOUT_MS", "HOST_DISPATCH_AGENT_TIMEOUT_MS")
                default_timeout = 5_000
                lane = "executive_router"
            elif scene in {"VISION_UI", "VISION_ACTION_PLANNER", "MULTIMODAL"}:
                role_model_names = ("HOST_VISION_LLM_MODEL_ID",)
                role_timeout_names = ("HOST_VISION_LLM_TIMEOUT_MS",)
                default_timeout = 20_000
                lane = "vision"

            explicit_tier_model = _first_env(
                f"HOST_MODEL_ROUTE_SCENE_{scene_token}_TIER_{_env_token(tier)}_MODEL_ID",
                f"HOST_MODEL_ROUTE_TIER_{_env_token(tier)}_MODEL_ID",
            )
            tier_default = _TIER_MODEL_DEFAULTS.get(provider, {}).get(tier, "")
            tier_driven_scene = (
                scene in {"NATSUME_AGENT", "CHAT", "TRIBE_INTERNAL"}
                or scene.startswith("GRAPH_")
            )
            provider_fallback = (
                tier_default or provider_model_override or provider_model
                if tier_driven_scene
                else provider_model_override or tier_default or provider_model
            )
            model_id = (
                explicit_tier_model
                or _first_env(
                    f"HOST_MODEL_ROUTE_SCENE_{scene_token}_MODEL_ID",
                    *role_model_names,
                )
                or provider_fallback
            )
            timeout_ms = _timeout_env(
                f"HOST_MODEL_ROUTE_SCENE_{scene_token}_TIMEOUT_MS",
                *role_timeout_names,
                "HOST_LLM_TIMEOUT_MS",
                default=default_timeout,
            )

        generation = (
            self._generation_by_prompt.get(str(prompt_id))
            or self._generation_by_scene.get(scene)
            or {}
        )
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
            "generation_parameters": dict(generation),
        }
        _assert_secret_free(route)
        return route


__all__ = ["ServerRouteCatalog"]
