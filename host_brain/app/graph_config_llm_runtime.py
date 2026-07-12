"""graph_config_llm_runtime.py — LLM 运行时解析。

拆分自 graph_config_builder.py（P2-11）：
  - resolve_host_llm_runtime / resolve_host_llm_runtime_async
  - resolve_surface_reply_llm_runtime
  - resolve_executive_router_llm_runtime
  - resolve_subagent_llm_runtime
  - 健康探测、灰度发布、RoutingMatrix 相关逻辑
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import urllib.error
import urllib.request
from typing import Any

from host_brain.app.config import load_host_config
from host_brain.integrations.llm_gateway import (
    PROVIDER_CODEX,
    PROVIDER_DEEPSEEK,
    PROVIDER_DISABLED,
    PROVIDER_GEMINI,
    PROVIDER_MINIMAX,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
)
from host_brain.app.provider_factory import (
    _GEMINI_PRIMARY_RETRY_NODE_ID,
    resolve_gemini_primary_fallback_model_id,
    resolve_host_llm_model_id,
)
from host_brain.app.model_router import resolve_gemini_model_route
from host_brain.integrations.codex_cli_provider import codex_cli_lm_allowed_for_background, codex_cli_lm_available, resolve_codex_cli_model
from host_brain.app.prompt_builder import (
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)

HOST_CHAT_ROUTE_HOST_14B = "host_14b"
HOST_CHAT_ROUTE_CLOUD = "cloud"


def _secret_free_route_metadata(value: Any) -> Any:
    """Project route evidence without credentials or transport configuration."""

    if isinstance(value, dict):
        return {
            str(key): _secret_free_route_metadata(item)
            for key, item in value.items()
            if str(key).strip().lower()
            not in {
                "api_key",
                "llm_api_key",
                "chat_llm_api_key",
                "cloud_llm_api_key",
                "credential",
                "credentials",
                "token",
                "secret",
                "base_url_override",
            }
        }
    if isinstance(value, (list, tuple)):
        return [_secret_free_route_metadata(item) for item in value]
    return value
HOST_CHAT_ROUTE_SKILLS = "skills"

_HOST_CHAT_14B_DEFAULT_PROVIDER = PROVIDER_OPENAI
_HOST_CHAT_14B_DEFAULT_MODEL_ID = "qwen3:14b"
_HOST_CHAT_14B_FALLBACK_API_KEY = "host-local-placeholder-key"
_HOST_CHAT_14B_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_HOST_CHAT_CLOUD_DEFAULT_PROVIDER = PROVIDER_GEMINI
_PROVIDERS_REQUIRING_API_KEY = frozenset({PROVIDER_OPENAI, PROVIDER_GEMINI, PROVIDER_DEEPSEEK, PROVIDER_MINIMAX})
_AGENT_CODEX_MIGRATION_DEFAULT_EXCLUDED_ROLES = frozenset({"surface_reply"})
_VISION_SCENE_NAMES = frozenset({SCENE_VISION_UI.upper(), "SCENE_VISION_UI", "VISION_UI"})


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(min_value), min(int(max_value), parsed))


def _is_vision_scene(scene_type: str | None) -> bool:
    normalized = str(scene_type or "").strip().upper()
    return normalized in _VISION_SCENE_NAMES


def _parse_csv_tokens(value: Any) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    tokens: set[str] = set()
    for item in raw.split(","):
        token = str(item or "").strip()
        if token:
            tokens.add(token)
    return tokens


def _env_value(name: str, legacy_name: str | None = None, default: str = "") -> str:
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    if legacy_name:
        legacy_value = str(os.environ.get(legacy_name) or "").strip()
        if legacy_value:
            return legacy_value
    return default


def _agent_codex_migration_enabled(role: str) -> bool:
    if not _parse_bool(os.environ.get("HOST_AGENT_CODEX_MIGRATION_ENABLED"), default=False):
        return False
    normalized_role = str(role or "").strip().lower()
    excluded = {
        str(item or "").strip().lower()
        for item in _parse_csv_tokens(
            os.environ.get("HOST_AGENT_CODEX_MIGRATION_EXCLUDED_ROLES")
            or ",".join(sorted(_AGENT_CODEX_MIGRATION_DEFAULT_EXCLUDED_ROLES))
        )
        if str(item or "").strip()
    }
    if normalized_role in excluded:
        return False
    included = {
        str(item or "").strip().lower()
        for item in _parse_csv_tokens(os.environ.get("HOST_AGENT_CODEX_MIGRATION_INCLUDED_ROLES"))
        if str(item or "").strip()
    }
    if included and normalized_role not in included:
        return False
    return codex_cli_lm_allowed_for_background()


def _resolve_agent_codex_model_id() -> str:
    return resolve_codex_cli_model(os.environ.get("HOST_AGENT_CODEX_MODEL_ID") or os.environ.get("HOST_CODEX_MODEL_ID"))


def _surface_reply_codex_experiment_enabled(role: str) -> bool:
    if str(role or "").strip().lower() != "surface_reply":
        return False
    if not _parse_bool(os.environ.get("HOST_SURFACE_REPLY_CODEX_EXPERIMENT_ENABLED") or os.environ.get("HOST_CHAT_AGENT_CODEX_EXPERIMENT_ENABLED"), default=False):
        return False
    return codex_cli_lm_allowed_for_background()


def _resolve_surface_reply_codex_model_id() -> str:
    return resolve_codex_cli_model(
        os.environ.get("HOST_SURFACE_REPLY_CODEX_MODEL_ID")
        or os.environ.get("HOST_CHAT_AGENT_CODEX_MODEL_ID")
        or os.environ.get("HOST_CHAT_AGENT_SPARK_MODEL_ID")
        or os.environ.get("HOST_CODEX_CHAT_MODEL_ID")
        or os.environ.get("HOST_CODEX_MODEL_ID")
    )


def _stable_bucket_0_99(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:8], 16) % 100


def _provider_requires_api_key(provider_name: str) -> bool:
    return str(provider_name or "").strip().lower() in _PROVIDERS_REQUIRING_API_KEY


def _provider_api_key_env_name(provider_name: str) -> str | None:
    provider = str(provider_name or "").strip().lower()
    mapping = {
        PROVIDER_OPENAI: "OPENAI_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
        PROVIDER_MINIMAX: "MINIMAX_API_KEY",
    }
    return mapping.get(provider)


def _resolve_provider_api_key(*, provider_name: str, preferred_api_key: str | None = None) -> str | None:
    preferred = str(preferred_api_key or "").strip()
    if preferred:
        return preferred
    env_name = _provider_api_key_env_name(provider_name)
    if not env_name:
        return None
    value = str(os.environ.get(env_name) or "").strip()
    return value or None


def _is_provider_available(*, provider_name: str, api_key: str | None, configured_available: bool) -> bool:
    provider = str(provider_name or "").strip().lower()
    if not configured_available:
        return False
    if provider == PROVIDER_DISABLED:
        return False
    if provider == PROVIDER_CODEX:
        return bool(str(api_key or "").strip()) or (
            codex_cli_lm_allowed_for_background() and codex_cli_lm_available()
        )
    if _provider_requires_api_key(provider) and not str(api_key or "").strip():
        return False
    return True


def _normalize_openai_compat_base_url(value: str | None) -> str | None:
    base_url = str(value or "").strip().rstrip("/")
    if not base_url:
        return None
    if base_url.lower().endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _resolve_cloud_provider_model_id(*, chain_name: str) -> str:
    normalized_chain = str(chain_name or "").strip().lower()
    if normalized_chain == "gemini":
        return str(os.environ.get("HOST_GEMINI_MODEL_ID") or "gemini-2.5-flash-lite").strip() or "gemini-2.5-flash-lite"
    if normalized_chain == "codex":
        return resolve_codex_cli_model(os.environ.get("HOST_CODEX_MODEL_ID"))
    if normalized_chain == "deepseek":
        return str(os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    if normalized_chain == "minimax":
        return str(os.environ.get("HOST_MINIMAX_MODEL_ID") or "M2-her").strip() or "M2-her"
    return "mock-default"


def _resolve_codex_base_url() -> str | None:
    raw = str(os.environ.get("HOST_CODEX_BASE_URL") or os.environ.get("HOST_OPENAI_BASE_URL") or "").strip()
    if not raw:
        return None
    return _normalize_openai_compat_base_url(raw)


def _split_api_and_root_base(base_url: str | None) -> tuple[str | None, str | None]:
    normalized = _normalize_openai_compat_base_url(base_url)
    if not normalized:
        return None, None
    root = normalized[:-3].rstrip("/") if normalized.lower().endswith("/v1") else normalized.rstrip("/")
    return normalized, (root or normalized)


def _resolve_host_14b_base_url(*, provider_name: str) -> str | None:
    provider = str(provider_name or "").strip().lower()
    if provider != PROVIDER_OPENAI:
        return None
    raw = _env_value(
        "HOST_CHAT_LOCAL_LLM_BASE_URL",
        "HOST_CHAT_HOST_14B_BASE_URL",
        _env_value("HOST_OPENAI_BASE_URL", default=_HOST_CHAT_14B_DEFAULT_BASE_URL),
    )
    return _normalize_openai_compat_base_url(raw)


def _default_host_14b_timeout_ms(*, provider_name: str, cloud_timeout_ms: int) -> int:
    provider = str(provider_name or "").strip().lower()
    default_timeout_ms = int(cloud_timeout_ms)
    if provider != PROVIDER_OPENAI:
        return default_timeout_ms
    base_url = str(_resolve_host_14b_base_url(provider_name=provider_name) or "").strip().lower()
    if "127.0.0.1:11434" in base_url or "localhost:11434" in base_url:
        return max(default_timeout_ms, 90_000)
    return default_timeout_ms


def _probe_http_endpoint(*, url: str, timeout_ms: int) -> tuple[bool, str]:
    endpoint = str(url or "").strip()
    if not endpoint:
        return False, "empty_probe_url"
    timeout_sec = max(0.2, float(max(100, int(timeout_ms))) / 1000.0)
    req = urllib.request.Request(endpoint, headers={"Accept": "application/json", "User-Agent": "NatsumeHost14BHealthProbe/1.0"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            code = int(getattr(resp, "status", 200) or 200)
        if 200 <= code < 500:
            return True, f"http_{code}"
        return False, f"http_{code}"
    except urllib.error.URLError as exc:
        return False, f"url_error:{exc}"
    except Exception as exc:
        return False, f"probe_error:{exc}"


async def _probe_http_endpoint_async(*, url: str, timeout_ms: int) -> tuple[bool, str]:
    return await asyncio.to_thread(_probe_http_endpoint, url=url, timeout_ms=timeout_ms)


def _resolve_host_14b_health_status(*, provider_name: str, timeout_ms: int) -> tuple[bool, str]:
    if not _parse_bool(_env_value("HOST_CHAT_LOCAL_LLM_HEALTHCHECK_ENABLED", "HOST_CHAT_HOST_14B_HEALTHCHECK_ENABLED"), default=False):
        return True, "HOST_LOCAL_LLM_HEALTHCHECK_DISABLED"
    ready_value = _env_value("HOST_CHAT_LOCAL_LLM_READY", "HOST_CHAT_HOST_14B_READY")
    if ready_value is not None and not _parse_bool(ready_value, default=True):
        return False, "HOST_LOCAL_LLM_READY_FLAG_DOWN"
    health_url = _env_value("HOST_CHAT_LOCAL_LLM_HEALTHCHECK_URL", "HOST_CHAT_HOST_14B_HEALTHCHECK_URL")
    probe_urls: list[str] = []
    if health_url:
        probe_urls.append(health_url)
    else:
        api_base, root_base = _split_api_and_root_base(_resolve_host_14b_base_url(provider_name=provider_name))
        if api_base:
            probe_urls.append(f"{api_base}/models")
        if root_base:
            probe_urls.append(f"{root_base}/health")
            probe_urls.append(f"{root_base}/api/tags")
    if not probe_urls:
        if ready_value is not None:
            return _parse_bool(ready_value, default=True), "HOST_LOCAL_LLM_READY_FLAG_ONLY"
        return True, "HOST_LOCAL_LLM_NO_PROBE_URL"
    first_failure = ""
    for url in probe_urls:
        ok, detail = _probe_http_endpoint(url=url, timeout_ms=timeout_ms)
        if ok:
            return True, f"HOST_LOCAL_LLM_PROBE_OK:{detail}"
        if not first_failure:
            first_failure = detail
    return False, f"HOST_LOCAL_LLM_PROBE_FAILED:{first_failure or 'unknown'}"


async def _resolve_host_14b_health_status_async(*, provider_name: str, timeout_ms: int) -> tuple[bool, str]:
    return await asyncio.to_thread(_resolve_host_14b_health_status, provider_name=provider_name, timeout_ms=timeout_ms)


def _evaluate_host_chat_gray_rollout(*, rollout_key: str | None, scene_type: str | None) -> dict[str, Any]:
    enabled = _parse_bool(os.environ.get("HOST_CHAT_GRAY_ENABLED"), default=False)
    percent = _parse_int(os.environ.get("HOST_CHAT_GRAY_PERCENT"), default=100, min_value=0, max_value=100)
    scope_scenes = {str(item).strip().upper() for item in _parse_csv_tokens(os.environ.get("HOST_CHAT_GRAY_SCENES") or "CHAT") if str(item).strip()}
    allowlist = _parse_csv_tokens(os.environ.get("HOST_CHAT_GRAY_USER_ALLOWLIST"))
    normalized_scene = str(scene_type or SCENE_CHAT).strip().upper() or SCENE_CHAT
    key_value = str(rollout_key or "").strip()
    result: dict[str, Any] = {"enabled": enabled, "percent": percent, "scene_type": normalized_scene, "scope_scenes": sorted(scope_scenes), "rollout_key": (key_value or None), "bucket": None, "allowlist_hit": False, "in_rollout": True, "reason_code": "HOST_CHAT_GRAY_DISABLED", "detail": "gray rollout disabled"}
    if not enabled:
        return result
    if scope_scenes and normalized_scene not in scope_scenes:
        result["in_rollout"] = False
        result["reason_code"] = "HOST_CHAT_GRAY_SCENE_EXCLUDED"
        result["detail"] = f"scene_type={normalized_scene} not in gray scope"
        return result
    if key_value and key_value in allowlist:
        result["allowlist_hit"] = True
        result["reason_code"] = "HOST_CHAT_GRAY_ALLOWLIST_MATCH"
        result["detail"] = "rollout key hit allowlist"
        return result
    if not key_value:
        include_without_key = percent >= 100
        result["in_rollout"] = include_without_key
        result["reason_code"] = "HOST_CHAT_GRAY_NO_KEY_INCLUDED" if include_without_key else "HOST_CHAT_GRAY_NO_KEY_EXCLUDED"
        result["detail"] = "rollout key missing"
        return result
    bucket = _stable_bucket_0_99(key_value)
    in_rollout = bucket < percent
    result["bucket"] = bucket
    result["in_rollout"] = in_rollout
    result["reason_code"] = "HOST_CHAT_GRAY_BUCKET_INCLUDED" if in_rollout else "HOST_CHAT_GRAY_BUCKET_EXCLUDED"
    result["detail"] = f"bucket={bucket} {'<' if in_rollout else '>='} percent={percent}"
    return result


async def _resolve_host_llm_runtime_core(*, rollout_key: str | None = None, scene_type: str | None = None, health_probe_async: bool = True) -> dict[str, Any]:
    """LLM 运行时解析公共核心逻辑。"""
    config_result = load_host_config()
    cloud_timeout_ms = 15_000
    gemini_api_key: str | None = None
    codex_api_key: str | None = None
    deepseek_api_key: str | None = None
    minimax_api_key: str | None = None
    configured_cloud_provider = str(os.environ.get("HOST_LLM_PROVIDER") or "").strip().lower() or None
    if config_result.success and config_result.config is not None:
        cloud_timeout_ms = int(getattr(config_result.config, "llm_timeout_ms", cloud_timeout_ms))
        configured_cloud_provider = str(getattr(config_result.config, "llm_provider", "") or configured_cloud_provider or "").strip().lower() or configured_cloud_provider
        gemini_api_key = str(getattr(config_result.config, "gemini_api_key", "") or "").strip() or None
        codex_api_key = str(getattr(config_result.config, "codex_api_key", "") or "").strip() or None
        deepseek_api_key = str(getattr(config_result.config, "deepseek_api_key", "") or "").strip() or None
        minimax_api_key = str(getattr(config_result.config, "minimax_api_key", "") or "").strip() or None
    else:
        gemini_api_key = _resolve_provider_api_key(provider_name=PROVIDER_GEMINI)
        codex_api_key = _resolve_provider_api_key(provider_name=PROVIDER_CODEX)
        deepseek_api_key = _resolve_provider_api_key(provider_name=PROVIDER_DEEPSEEK)
        minimax_api_key = _resolve_provider_api_key(provider_name=PROVIDER_MINIMAX)

    if _is_vision_scene(scene_type):
        vision_timeout_ms = _parse_int(
            os.environ.get("HOST_VISION_LLM_TIMEOUT_MS"),
            default=20_000,
            min_value=8_000,
            max_value=120_000,
        )
        cloud_timeout_ms = max(int(cloud_timeout_ms), int(vision_timeout_ms))

    gemini_cloud_route = resolve_gemini_model_route(role="cloud_primary", preferred_api_key=gemini_api_key, route_key=(str(rollout_key or "").strip() or str(scene_type or "").strip() or None))
    gemini_cloud_model_id = str(gemini_cloud_route.get("model_id") or _resolve_cloud_provider_model_id(chain_name="gemini")).strip() or _resolve_cloud_provider_model_id(chain_name="gemini")
    gemini_cloud_api_key = str(gemini_cloud_route.get("api_key") or "").strip() or None

    minimax_cloud_model_id = _resolve_cloud_provider_model_id(chain_name="minimax")
    raw_minimax_base = str(os.environ.get("HOST_MINIMAX_BASE_URL") or "https://api.minimaxi.com/v1").strip() or "https://api.minimaxi.com/v1"
    candidate_map: dict[str, dict[str, Any]] = {
        "gemini": {"chain_name": "gemini", "provider_name": PROVIDER_GEMINI, "model_id": gemini_cloud_model_id, "api_key": gemini_cloud_api_key, "base_url_override": _normalize_openai_compat_base_url(str(os.environ.get("HOST_GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai").strip()), "route_reason_code": "HOST_CLOUD_CHAIN_PRIMARY_GEMINI", "route_detail": "selected Gemini as primary remote provider", "route_meta": dict(gemini_cloud_route)},
        "codex": {"chain_name": "codex", "provider_name": "codex", "model_id": _resolve_cloud_provider_model_id(chain_name="codex"), "api_key": codex_api_key, "base_url_override": _resolve_codex_base_url(), "route_reason_code": "HOST_CLOUD_CHAIN_FALLBACK_CODEX", "route_detail": "Gemini unavailable; using Codex relay as remote fallback"},
        "deepseek": {"chain_name": "deepseek", "provider_name": PROVIDER_DEEPSEEK, "model_id": _resolve_cloud_provider_model_id(chain_name="deepseek"), "api_key": deepseek_api_key, "base_url_override": _normalize_openai_compat_base_url(str(os.environ.get("HOST_DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()), "route_reason_code": "HOST_CLOUD_CHAIN_FALLBACK_DEEPSEEK", "route_detail": "Gemini/Codex unavailable; using DeepSeek as remote fallback"},
        "minimax": {"chain_name": "minimax", "provider_name": PROVIDER_MINIMAX, "model_id": minimax_cloud_model_id, "api_key": minimax_api_key, "base_url_override": raw_minimax_base, "route_reason_code": "HOST_CLOUD_CHAIN_PRIMARY_MINIMAX", "route_detail": "selected MiniMax as primary remote provider"},
    }
    preferred_chain_order: list[str] = []
    normalized_preferred_provider = str(configured_cloud_provider or "").strip().lower()
    if normalized_preferred_provider in candidate_map:
        preferred_chain_order.append(normalized_preferred_provider)
    for chain_name in ("gemini", "codex", "deepseek", "minimax"):
        if chain_name not in preferred_chain_order:
            preferred_chain_order.append(chain_name)
    remote_cloud_candidates: list[dict[str, Any]] = [dict(candidate_map[name]) for name in preferred_chain_order if name in candidate_map]
    remote_chain_status: list[dict[str, Any]] = []
    selected_remote_candidate: dict[str, Any] | None = None
    for candidate in remote_cloud_candidates:
        candidate_provider = str(candidate.get("provider_name") or "").strip().lower()
        candidate_api_key = str(candidate.get("api_key") or "").strip() or None
        candidate_available = _is_provider_available(provider_name=candidate_provider, api_key=candidate_api_key, configured_available=True)
        remote_chain_status.append({"chain_name": candidate.get("chain_name"), "provider_name": candidate_provider, "model_id": candidate.get("model_id"), "available": bool(candidate_available), "model_route": _secret_free_route_metadata(dict(candidate.get("route_meta") or {}) if isinstance(candidate.get("route_meta"), dict) else {})})
        if candidate_available and selected_remote_candidate is None:
            selected_remote_candidate = dict(candidate)

    sys_mode = str(os.environ.get("HOST_SYS_MODE") or "normal").strip().lower() or "normal"
    route_target = HOST_CHAT_ROUTE_CLOUD
    route_reason_code = "HOST_CLOUD_CHAIN_REMOTE_UNAVAILABLE"
    route_detail = "Gemini, Codex relay, and DeepSeek are all unavailable"

    if selected_remote_candidate is not None:
        cloud_provider_name = str(selected_remote_candidate.get("provider_name") or PROVIDER_GEMINI).strip().lower() or PROVIDER_GEMINI
        cloud_model_id = str(selected_remote_candidate.get("model_id") or "").strip() or resolve_host_llm_model_id(cloud_provider_name)
        cloud_llm_api_key = str(selected_remote_candidate.get("api_key") or "").strip() or None
        cloud_model_route = dict(selected_remote_candidate.get("route_meta") or {}) if isinstance(selected_remote_candidate.get("route_meta"), dict) else {}
        cloud_available = True
        route_reason_code = str(selected_remote_candidate.get("route_reason_code") or route_reason_code)
        route_detail = str(selected_remote_candidate.get("route_detail") or route_detail)
    else:
        cloud_provider_name = PROVIDER_GEMINI if normalized_preferred_provider != PROVIDER_MINIMAX else PROVIDER_MINIMAX
        cloud_model_id = gemini_cloud_model_id
        cloud_llm_api_key = gemini_cloud_api_key if cloud_provider_name == PROVIDER_GEMINI else minimax_api_key
        cloud_model_route = dict(gemini_cloud_route) if cloud_provider_name == PROVIDER_GEMINI else {}
        if cloud_provider_name == PROVIDER_MINIMAX:
            cloud_model_id = minimax_cloud_model_id
        cloud_available = False

    chat_provider_name = cloud_provider_name
    chat_model_id = cloud_model_id
    chat_timeout_ms = cloud_timeout_ms
    chat_available = cloud_available

    host_14b_candidate_runtime: dict[str, Any] = {}
    host_14b_ready = False
    host_14b_unavailable_reason = "HOST_LOCAL_LLM_NOT_EVALUATED"
    host_14b_enabled = _parse_bool(_env_value("HOST_CHAT_LOCAL_LLM_ENABLED", "HOST_CHAT_HOST_14B_ENABLED"), default=False)
    host_14b_force_cloud = _parse_bool(os.environ.get("HOST_CHAT_FORCE_CLOUD"), default=False)
    normalized_scene_type = str(scene_type or SCENE_CHAT).strip().upper() or SCENE_CHAT
    host_14b_chat_preferred = normalized_scene_type == SCENE_CHAT.upper() and not _is_vision_scene(scene_type)
    gray_rollout = _evaluate_host_chat_gray_rollout(rollout_key=rollout_key, scene_type=scene_type)

    if not host_14b_enabled:
        host_14b_unavailable_reason = "HOST_CHAT_LOCAL_LLM_DISABLED"
        if selected_remote_candidate is None:
            route_reason_code = "HOST_CHAT_LOCAL_LLM_DISABLED"
            route_detail = "HOST_CHAT_LOCAL_LLM_ENABLED=false"
    elif host_14b_force_cloud:
        host_14b_unavailable_reason = "HOST_CHAT_FORCE_CLOUD"
        if selected_remote_candidate is None:
            route_reason_code = "HOST_CHAT_FORCE_CLOUD"
            route_detail = "HOST_CHAT_FORCE_CLOUD=true"
    elif selected_remote_candidate is not None and not host_14b_chat_preferred:
        host_14b_unavailable_reason = "HOST_CLOUD_CHAIN_REMOTE_PROVIDER_SELECTED"
    else:
        host_14b_provider = _env_value("HOST_CHAT_LOCAL_LLM_PROVIDER", "HOST_CHAT_HOST_14B_PROVIDER", _HOST_CHAT_14B_DEFAULT_PROVIDER).lower() or _HOST_CHAT_14B_DEFAULT_PROVIDER
        host_14b_model_id = _env_value("HOST_CHAT_LOCAL_LLM_MODEL_ID", "HOST_CHAT_HOST_14B_MODEL_ID", _HOST_CHAT_14B_DEFAULT_MODEL_ID) or _HOST_CHAT_14B_DEFAULT_MODEL_ID
        host_14b_timeout_ms = _parse_int(_env_value("HOST_CHAT_LOCAL_LLM_TIMEOUT_MS", "HOST_CHAT_HOST_14B_TIMEOUT_MS"), default=_default_host_14b_timeout_ms(provider_name=host_14b_provider, cloud_timeout_ms=cloud_timeout_ms), min_value=800, max_value=120_000)
        host_14b_api_key = _resolve_provider_api_key(provider_name=host_14b_provider, preferred_api_key=_env_value("HOST_CHAT_LOCAL_LLM_API_KEY", "HOST_CHAT_HOST_14B_API_KEY") or None)
        if _provider_requires_api_key(host_14b_provider) and not host_14b_api_key:
            if _parse_bool(_env_value("HOST_CHAT_LOCAL_LLM_ALLOW_EMPTY_KEY", "HOST_CHAT_HOST_14B_ALLOW_EMPTY_KEY"), default=True):
                host_14b_api_key = _HOST_CHAT_14B_FALLBACK_API_KEY
            else:
                route_reason_code = "HOST_CHAT_LOCAL_LLM_KEY_MISSING"
                route_detail = f"{_provider_api_key_env_name(host_14b_provider) or 'api_key'} missing"
                host_14b_unavailable_reason = "HOST_CHAT_LOCAL_LLM_KEY_MISSING"
        host_14b_available = _is_provider_available(provider_name=host_14b_provider, api_key=host_14b_api_key, configured_available=True)
        host_14b_candidate_runtime = {
            "provider_name": host_14b_provider,
            "model_id": host_14b_model_id,
            "timeout_ms": host_14b_timeout_ms,
            "available": bool(host_14b_available),
        }
        if host_14b_available:
            if health_probe_async:
                healthy, health_detail = await _resolve_host_14b_health_status_async(provider_name=host_14b_provider, timeout_ms=min(host_14b_timeout_ms, 3_000))
            else:
                healthy, health_detail = _resolve_host_14b_health_status(provider_name=host_14b_provider, timeout_ms=min(host_14b_timeout_ms, 3_000))
            if healthy:
                host_14b_ready = True
                host_14b_unavailable_reason = ""
                chat_provider_name = host_14b_provider
                chat_model_id = host_14b_model_id
                chat_timeout_ms = host_14b_timeout_ms
                chat_available = True
                route_target = HOST_CHAT_ROUTE_HOST_14B
                if selected_remote_candidate is not None and host_14b_chat_preferred:
                    route_reason_code = "HOST_CHAT_LOCAL_LLM_PRIMARY"
                    route_detail = "HOST_CHAT_LOCAL_LLM_ENABLED=true; using healthy local LLM for chat"
                else:
                    route_reason_code = "HOST_CLOUD_CHAIN_FALLBACK_LOCAL"
                    route_detail = "all remote providers unavailable; using local host 14b as final fallback"
            else:
                host_14b_unavailable_reason = str(health_detail or "HOST_CHAT_LOCAL_LLM_UNHEALTHY")
                if selected_remote_candidate is None:
                    route_reason_code = "HOST_CHAT_LOCAL_LLM_UNHEALTHY"
                    route_detail = health_detail
        elif route_reason_code == "HOST_CHAT_ROUTE_CLOUD_DEFAULT":
            route_reason_code = "HOST_CHAT_LOCAL_LLM_UNAVAILABLE"
            route_detail = "local LLM provider unavailable"
            host_14b_unavailable_reason = "HOST_CHAT_LOCAL_LLM_UNAVAILABLE"
        elif not host_14b_available and not host_14b_unavailable_reason:
            host_14b_unavailable_reason = "HOST_CHAT_LOCAL_LLM_UNAVAILABLE"

    # RoutingMatrix 接入
    routing_matrix_decision: dict[str, Any] = {}
    try:
        from host_brain.app.dependencies import get_routing_matrix
        from host_brain.app.schemas.routing_types import TaskType as _TaskType, LatencyBudget as _LatencyBudget
        _scene_to_task = {
            SCENE_CHAT: _TaskType.CHAT,
            SCENE_EXEC_INTENT: _TaskType.REASONING,
            SCENE_WORK_CODE: _TaskType.CODE,
            SCENE_WORK_TRANSLATE: _TaskType.TRANSLATE,
            SCENE_VISION_UI: _TaskType.CHAT,
        }
        _task_type = _scene_to_task.get(str(scene_type or SCENE_CHAT).strip().upper(), _TaskType.CHAT)
        _latency = _LatencyBudget.FAST if sys_mode == "gaming" else _LatencyBudget.NORMAL
        _matrix = get_routing_matrix()
        _decision = _matrix.select_with_fallback(task_type=_task_type, latency_budget=_latency)
        _selected_model = str(_decision.selected_model.value or "").strip().lower()
        _runtime_changed = False
        _not_applied_reason: str | None = None
        if _selected_model == HOST_CHAT_ROUTE_CLOUD:
            if route_target == HOST_CHAT_ROUTE_HOST_14B and route_reason_code == "HOST_CHAT_LOCAL_LLM_PRIMARY":
                _not_applied_reason = "HOST_CHAT_LOCAL_LLM_PRIMARY"
            elif route_target != HOST_CHAT_ROUTE_CLOUD and cloud_available:
                chat_provider_name = cloud_provider_name
                chat_model_id = cloud_model_id
                chat_timeout_ms = cloud_timeout_ms
                chat_available = cloud_available
                route_target = HOST_CHAT_ROUTE_CLOUD
                route_reason_code = "ROUTING_MATRIX_SELECTED_CLOUD"
                route_detail = f"routing_matrix selected cloud: task={_decision.task_type.value}, latency={_decision.latency_budget.value}"
                _runtime_changed = True
            elif route_target != HOST_CHAT_ROUTE_CLOUD:
                _not_applied_reason = "REMOTE_CHAIN_UNAVAILABLE"
        elif _selected_model == HOST_CHAT_ROUTE_HOST_14B:
            if route_target != HOST_CHAT_ROUTE_HOST_14B and host_14b_ready and host_14b_candidate_runtime:
                chat_provider_name = str(host_14b_candidate_runtime.get("provider_name") or chat_provider_name)
                chat_model_id = str(host_14b_candidate_runtime.get("model_id") or chat_model_id)
                chat_timeout_ms = int(host_14b_candidate_runtime.get("timeout_ms") or chat_timeout_ms)
                chat_available = bool(host_14b_candidate_runtime.get("available", True))
                route_target = HOST_CHAT_ROUTE_HOST_14B
                route_reason_code = "ROUTING_MATRIX_SELECTED_HOST_14B"
                route_detail = f"routing_matrix selected host_14b: task={_decision.task_type.value}, latency={_decision.latency_budget.value}"
                _runtime_changed = True
            elif route_target != HOST_CHAT_ROUTE_HOST_14B:
                _not_applied_reason = f"HOST_14B_UNAVAILABLE:{host_14b_unavailable_reason or 'UNKNOWN'}"
        else:
            _not_applied_reason = f"UNSUPPORTED_MODEL_TARGET:{_selected_model or 'unknown'}"
        routing_matrix_decision = {"task_type": _decision.task_type.value, "selected_model": _decision.selected_model.value, "latency_budget": _decision.latency_budget.value, "is_fallback": _decision.is_fallback, "fallback_reason": _decision.fallback_reason, "runtime_applied": _runtime_changed, "runtime_selected_target": route_target, "not_applied_reason": _not_applied_reason}
    except Exception as _rm_exc:
        routing_matrix_decision = {"status": "DEGRADED", "reason": str(_rm_exc)}

    return {
        "provider_name": chat_provider_name, "model_id": chat_model_id, "timeout_ms": chat_timeout_ms,
        "available": chat_available,
        "route_target": route_target, "route_reason_code": route_reason_code, "route_detail": route_detail,
        "sys_mode": sys_mode,
        "chat_provider_name": chat_provider_name, "chat_model_id": chat_model_id, "chat_timeout_ms": chat_timeout_ms,
        "chat_available": chat_available,
        "cloud_provider_name": cloud_provider_name, "cloud_model_id": cloud_model_id, "cloud_timeout_ms": cloud_timeout_ms,
        "cloud_available": cloud_available,
        "cloud_model_route": _secret_free_route_metadata(cloud_model_route),
        "remote_chain_status": remote_chain_status, "gray_rollout": dict(gray_rollout),
        "host_14b_ready": host_14b_ready,
        "host_14b_unavailable_reason": host_14b_unavailable_reason,
        "local_llm_ready": host_14b_ready,
        "local_llm_unavailable_reason": host_14b_unavailable_reason,
        "host_14b_candidate_runtime": dict(host_14b_candidate_runtime),
        "routing_matrix_decision": routing_matrix_decision,
    }


async def resolve_host_llm_runtime_async(*, rollout_key: str | None = None, scene_type: str | None = None) -> dict[str, Any]:
    """Compatibility delegate to the canonical secret-free RouteCatalog."""
    from host_brain.app.route_catalog_runtime import resolve_host_llm_runtime_async as _resolve

    return await _resolve(rollout_key=rollout_key, scene_type=scene_type)


def resolve_host_llm_runtime(*, rollout_key: str | None = None, scene_type: str | None = None) -> dict[str, Any]:
    """Compatibility delegate to the canonical secret-free RouteCatalog."""
    from host_brain.app.route_catalog_runtime import resolve_host_llm_runtime as _resolve

    return _resolve(rollout_key=rollout_key, scene_type=scene_type)


# ---------------------------------------------------------------------------
# Agent 专用 LLM 运行时解析
# ---------------------------------------------------------------------------
def _resolve_non_gemini_agent_model_id_v2(*, provider_name: str, base_runtime: dict[str, Any], model_env_names: tuple[str, ...]) -> str:
    for model_env_name in model_env_names:
        explicit_model = str(os.environ.get(model_env_name) or "").strip()
        if explicit_model and not explicit_model.lower().startswith("gemini-"):
            return explicit_model
    base_model_id = str(base_runtime.get("model_id") or base_runtime.get("chat_model_id") or "").strip()
    if base_model_id:
        return base_model_id
    return resolve_host_llm_model_id(provider_name)


def _first_non_empty_env(*env_names: str) -> str | None:
    for env_name in env_names:
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value
    return None


def _resolve_agent_llm_runtime_v2(*, base_runtime: dict[str, Any], role: str, model_env_name: str, timeout_env_name: str, timeout_default_ms: int, timeout_max_ms: int, gen_params: dict[str, float], fallback_model_env_names: tuple[str, ...] = (), fallback_timeout_env_names: tuple[str, ...] = ()) -> dict[str, Any]:
    provider_name = str(base_runtime.get("provider_name") or PROVIDER_GEMINI).strip().lower() or PROVIDER_GEMINI
    timeout_value = _first_non_empty_env(timeout_env_name, *fallback_timeout_env_names)
    timeout_ms = max(800, _parse_int(timeout_value, default=timeout_default_ms, min_value=800, max_value=timeout_max_ms))
    explicit_model_override = _first_non_empty_env(model_env_name, *fallback_model_env_names)
    model_route: dict[str, Any] = {}
    if _surface_reply_codex_experiment_enabled(role):
        model_id = _resolve_surface_reply_codex_model_id()
        codex_timeout_ms = max(
            timeout_ms,
            _parse_int(
                os.environ.get("HOST_SURFACE_REPLY_CODEX_MIN_TIMEOUT_MS") or os.environ.get("HOST_CHAT_AGENT_CODEX_MIN_TIMEOUT_MS"),
                default=45_000,
                min_value=800,
                max_value=10 * 60 * 1000,
            ),
        )
        return {
            **base_runtime,
            "provider_name": PROVIDER_CODEX,
            "model_id": model_id,
            "chat_model_id": model_id,
            "timeout_ms": codex_timeout_ms,
            "chat_timeout_ms": codex_timeout_ms,
            "available": True,
            "chat_available": True,
            "agent_gen_params": dict(gen_params),
            "agent_role": role,
            "agent_model_route": {"route": "codex_surface_reply_experiment", "source": "HOST_SURFACE_REPLY_CODEX_EXPERIMENT_ENABLED"},
            "route_reason_code": "HOST_SURFACE_REPLY_CODEX_EXPERIMENT",
            "route_detail": f"agent role {role} routed to local Codex CLI chat experiment",
        }
    if _agent_codex_migration_enabled(role):
        model_id = _resolve_agent_codex_model_id()
        return {
            **base_runtime,
            "provider_name": PROVIDER_CODEX,
            "model_id": model_id,
            "chat_model_id": model_id,
            "timeout_ms": max(timeout_ms, _parse_int(os.environ.get("HOST_AGENT_CODEX_MIN_TIMEOUT_MS"), default=30_000, min_value=800, max_value=10 * 60 * 1000)),
            "chat_timeout_ms": max(timeout_ms, _parse_int(os.environ.get("HOST_AGENT_CODEX_MIN_TIMEOUT_MS"), default=30_000, min_value=800, max_value=10 * 60 * 1000)),
            "available": True,
            "chat_available": True,
            "agent_gen_params": dict(gen_params),
            "agent_role": role,
            "agent_model_route": {"route": "codex_cli_migration", "source": "HOST_AGENT_CODEX_MIGRATION_ENABLED"},
            "route_reason_code": "HOST_AGENT_CODEX_CLI_MIGRATION",
            "route_detail": f"agent role {role} routed to local Codex CLI",
        }
    if provider_name == PROVIDER_GEMINI:
        model_route = resolve_gemini_model_route(role=role, preferred_api_key=None, route_key=(str(base_runtime.get("route_reason_code") or "").strip() or str(base_runtime.get("route_target") or "").strip() or None))
        if explicit_model_override and explicit_model_override.lower().startswith("gemini-"):
            model_id = explicit_model_override
        else:
            model_id = str(model_route.get("model_id") or base_runtime.get("model_id") or base_runtime.get("chat_model_id") or "gemini-2.5-flash-lite").strip() or "gemini-2.5-flash-lite"
    else:
        model_id = _resolve_non_gemini_agent_model_id_v2(provider_name=provider_name, base_runtime=base_runtime, model_env_names=(model_env_name, *fallback_model_env_names))
    safe_model_route = {
        key: value
        for key, value in model_route.items()
        if key not in {"api_key", "credential", "token", "base_url_override"}
    }
    return {
        **base_runtime,
        "model_id": model_id,
        "chat_model_id": model_id,
        "timeout_ms": timeout_ms,
        "chat_timeout_ms": timeout_ms,
        "agent_gen_params": dict(gen_params),
        "agent_role": role,
        "agent_model_route": (safe_model_route or None),
    }


def resolve_surface_reply_llm_runtime(*, base_runtime: dict) -> dict:
    from host_brain.app.route_catalog_runtime import resolve_surface_reply_llm_runtime as _resolve

    return _resolve(base_runtime=base_runtime)


def resolve_executive_router_llm_runtime(*, base_runtime: dict) -> dict:
    from host_brain.app.route_catalog_runtime import resolve_executive_router_llm_runtime as _resolve

    return _resolve(base_runtime=base_runtime)


def resolve_subagent_llm_runtime(*, base_runtime: dict) -> dict:
    from host_brain.app.route_catalog_runtime import resolve_subagent_llm_runtime as _resolve

    return _resolve(base_runtime=base_runtime)
