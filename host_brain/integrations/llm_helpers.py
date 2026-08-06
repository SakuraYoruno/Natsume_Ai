"""LLM 辅助函数：API Key 加载、情感生成参数注入、池化调度、SSE 流式输出。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from queue import Empty, Queue
from threading import Lock, Thread, local
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable, Mapping

from host_brain.core.errors import LLM_POOL_EXHAUSTED
from host_brain.integrations.codex_cli_provider import (
    codex_cli_lm_allowed_for_background,
    codex_cli_lm_available,
    invoke_codex_cli_text,
    resolve_codex_cli_model,
    resolve_codex_cli_timeout_ms,
)
from host_brain.integrations.llm_gateway_schemas import ModelAttempt, PoolCompleteResult

PROVIDER_OPENAI = "openai"
PROVIDER_CODEX = "codex"
PROVIDER_GEMINI = "gemini"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_MINIMAX = "minimax"
PROVIDER_MOCK = "mock"
PROVIDER_DISABLED = "disabled"

ProviderFn = Callable[[dict[str, Any]], Any]

_logger = logging.getLogger("Natsume.integrations.llm_helpers")
_POOL_DEFAULT_SPECS: dict[str, str] = {
    "A": "deepseek:deepseek-v4-pro,gemini:gemini-2.5-pro",
    "B": "deepseek:deepseek-v4-flash,deepseek:deepseek-v4-pro,gemini:gemini-2.5-flash-lite",
    "C": "deepseek:deepseek-v4-flash,gemini:gemini-2.5-flash-lite",
    "D": "deepseek:deepseek-v4-flash,gemini:gemini-2.5-flash-lite",
}
DEFAULT_LLM_POOL_CODEX_MODEL = "gpt-5.5"
_POOL_CODEX_MIGRATION_DEFAULT_EXCLUDED_SCENES = frozenset({"CHAT", "CHAT_SURFACE", "MULTIMODAL"})
_MULTIMODAL_PROVIDER_ALLOWLIST = {PROVIDER_GEMINI}
_POOL_COOLDOWN_LOCK = Lock()
_POOL_COOLDOWN_UNTIL_MS: dict[str, int] = {}
_POOL_CACHE_LOCK = Lock()
_POOL_COMPLETION_CACHE: dict[str, tuple[int, PoolCompleteResult]] = {}
_PROVIDER_USAGE_LOCAL = local()
_OPENAI_COMPAT_SESSION_LOCK = Lock()
_OPENAI_COMPAT_SESSION: Any | None = None
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", flags=re.DOTALL | re.IGNORECASE)
_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "too many requests", "resource_exhausted", "quota")
_BILLING_FAIL_MARKERS = ("402", "insufficient balance", "payment required", "billing")
_UNAVAILABLE_MARKERS = (
    "timeout",
    "timed out",
    "service unavailable",
    "temporarily unavailable",
    "connection error",
    "network error",
    "connection reset",
    "eof occurred",
    "ssl",
    "unavailable",
)
_KEY_FAIL_MARKERS = ("401", "403", "api key", "api_key", "authentication", "unauthorized", "permission")
_LOCAL_OPENAI_COMPAT_PLACEHOLDER_KEY = "host-local-placeholder-key"

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator


def _set_last_provider_usage(usage: Any) -> None:
    setattr(_PROVIDER_USAGE_LOCAL, "last_usage", dict(usage) if isinstance(usage, Mapping) else None)


def _pop_last_provider_usage() -> dict[str, Any] | None:
    usage = getattr(_PROVIDER_USAGE_LOCAL, "last_usage", None)
    setattr(_PROVIDER_USAGE_LOCAL, "last_usage", None)
    return dict(usage) if isinstance(usage, Mapping) else None


def _invoke_provider_with_timeout(
    *,
    provider: ProviderFn,
    payload: dict[str, Any],
    timeout_ms: int,
) -> Any:
    """Run provider behind a hard wall-clock timeout.

    SDK-level timeouts are not consistently enforced across all upstream
    providers, so the gateway must enforce a deterministic upper bound itself.
    """
    if int(timeout_ms) <= 0:
        return provider(payload)

    result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)

    def _target() -> None:
        try:
            result_queue.put((True, provider(payload)))
        except Exception as exc:  # pragma: no cover - re-raised on caller thread
            result_queue.put((False, exc))

    worker = Thread(
        target=_target,
        name="llm-gateway-provider",
        daemon=True,
    )
    worker.start()
    worker.join(max(1, int(timeout_ms)) / 1000.0)
    if worker.is_alive():
        raise TimeoutError(f"provider hard timeout>{int(timeout_ms)}ms")
    try:
        ok, value = result_queue.get_nowait()
    except Empty as exc:  # pragma: no cover - defensive
        raise RuntimeError("provider worker finished without returning a result") from exc
    if ok:
        return value
    raise value


def _normalize_raw_response(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _parse_csv_env(name: str, *, default: str = "") -> set[str]:
    raw = str(os.environ.get(name) or default or "").strip()
    if not raw:
        return set()
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _pool_env_token(value: str | None) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip().upper()).strip("_")
    return token or "DEFAULT"


def _resolve_pool_spec(*, normalized_scene: str, normalized_tier: str) -> tuple[str, bool, str]:
    scene_token = _pool_env_token(normalized_scene)
    tier_token = _pool_env_token(normalized_tier)
    scene_tier_env = f"HOST_LLM_POOL_SCENE_{scene_token}_TIER_{tier_token}"
    scene_env = f"HOST_LLM_POOL_SCENE_{scene_token}"
    for env_name in (scene_tier_env, scene_env):
        raw_value = str(os.environ.get(env_name) or "").strip()
        if raw_value:
            return raw_value, True, env_name
    tier_env = f"HOST_LLM_POOL_TIER_{tier_token}"
    raw_spec = str(os.environ.get(tier_env) or _POOL_DEFAULT_SPECS.get(tier_token) or "").strip()
    return raw_spec, False, tier_env


def _scene_pool_include_explicit(*, normalized_scene: str, scene_specific: bool) -> bool:
    if not scene_specific:
        return True
    scene_token = _pool_env_token(normalized_scene)
    return _parse_bool_env(
        f"HOST_LLM_POOL_SCENE_{scene_token}_INCLUDE_EXPLICIT",
        default=False,
    )


def _resolve_pool_max_attempts(*, normalized_scene: str, normalized_tier: str) -> int:
    scene_token = _pool_env_token(normalized_scene)
    tier_token = _pool_env_token(normalized_tier)
    for env_name in (
        f"HOST_LLM_POOL_SCENE_{scene_token}_TIER_{tier_token}_MAX_ATTEMPTS",
        f"HOST_LLM_POOL_SCENE_{scene_token}_MAX_ATTEMPTS",
        f"HOST_LLM_POOL_TIER_{tier_token}_MAX_ATTEMPTS",
        "HOST_LLM_POOL_MAX_ATTEMPTS",
    ):
        raw = str(os.environ.get(env_name) or "").strip()
        if raw:
            return _parse_int_env(env_name, default=0, minimum=0, maximum=20)
    return 0


def _json_text_fallback_enabled(*, normalized_scene: str) -> bool:
    scene_token = str(normalized_scene or "").strip().upper()
    if not scene_token:
        return False
    enabled_scenes = _parse_csv_env(
        "HOST_LLM_POOL_JSON_TEXT_FALLBACK_SCENES",
        default="CHAT_SURFACE",
    )
    return scene_token in enabled_scenes


def _codex_pool_migration_enabled(scene_type: str) -> bool:
    if not _parse_bool_env("HOST_LLM_POOL_CODEX_MIGRATION_ENABLED", default=False):
        return False
    normalized_scene = str(scene_type or "").strip().upper()
    if not normalized_scene:
        return False
    excluded = _parse_csv_env(
        "HOST_LLM_POOL_CODEX_EXCLUDED_SCENES",
        default=",".join(sorted(_POOL_CODEX_MIGRATION_DEFAULT_EXCLUDED_SCENES)),
    )
    if normalized_scene in excluded:
        return False
    included = _parse_csv_env("HOST_LLM_POOL_CODEX_INCLUDED_SCENES")
    if included and normalized_scene not in included:
        return False
    return codex_cli_lm_allowed_for_background()


def _resolve_pool_codex_model_id() -> str:
    return resolve_codex_cli_model(os.environ.get("HOST_LLM_POOL_CODEX_MODEL_ID") or DEFAULT_LLM_POOL_CODEX_MODEL)


def _resolve_pool_scene_attempt_timeout_ms(
    *,
    scene_type: str | None,
    cognitive_tier: str | None,
    provider_name: str | None,
    timeout_ms: int,
) -> int:
    resolved_timeout_ms = int(timeout_ms)
    scene_token = _pool_env_token(scene_type) if str(scene_type or "").strip() else ""
    tier_token = _pool_env_token(cognitive_tier) if str(cognitive_tier or "").strip() else ""
    provider_token = _pool_env_token(provider_name) if str(provider_name or "").strip() else ""
    chat_surface_cap_ms = 0
    if scene_token == "CHAT_SURFACE":
        scene_default_raw = str(os.environ.get("HOST_LLM_POOL_CHAT_SURFACE_DEFAULT_ATTEMPT_TIMEOUT_MS") or "").strip()
        default_cap_ms = 12_000 if provider_token == "GEMINI" else 6_000
        chat_surface_cap_ms = _parse_int_env(
            "HOST_LLM_POOL_CHAT_SURFACE_DEFAULT_ATTEMPT_TIMEOUT_MS",
            default=default_cap_ms,
            minimum=1_000,
            maximum=120_000,
        ) if scene_default_raw else default_cap_ms
        resolved_timeout_ms = min(resolved_timeout_ms, chat_surface_cap_ms)
    env_names: list[str] = []
    if scene_token and tier_token and provider_token:
        env_names.append(f"HOST_LLM_POOL_SCENE_{scene_token}_TIER_{tier_token}_{provider_token}_ATTEMPT_TIMEOUT_MS")
    if scene_token and provider_token:
        env_names.append(f"HOST_LLM_POOL_SCENE_{scene_token}_{provider_token}_ATTEMPT_TIMEOUT_MS")
    if scene_token and tier_token:
        env_names.append(f"HOST_LLM_POOL_SCENE_{scene_token}_TIER_{tier_token}_ATTEMPT_TIMEOUT_MS")
    if scene_token:
        env_names.append(f"HOST_LLM_POOL_SCENE_{scene_token}_ATTEMPT_TIMEOUT_MS")
    if tier_token and provider_token:
        env_names.append(f"HOST_LLM_POOL_TIER_{tier_token}_{provider_token}_ATTEMPT_TIMEOUT_MS")
    if tier_token:
        env_names.append(f"HOST_LLM_POOL_TIER_{tier_token}_ATTEMPT_TIMEOUT_MS")
    env_names.append("HOST_LLM_POOL_ATTEMPT_TIMEOUT_MS")
    for env_name in env_names:
        raw_value = str(os.environ.get(env_name) or "").strip()
        if raw_value:
            resolved_timeout_ms = _parse_int_env(
                env_name,
                default=resolved_timeout_ms,
                minimum=250,
                maximum=10 * 60 * 1000,
            )
            break
    if chat_surface_cap_ms > 0:
        resolved_timeout_ms = min(resolved_timeout_ms, chat_surface_cap_ms)
    return resolved_timeout_ms


def _resolve_pool_attempt_timeout_ms(
    *,
    provider_name: str,
    api_key: str | None,
    timeout_ms: int,
    scene_type: str | None = None,
    cognitive_tier: str | None = None,
) -> int:
    resolved_timeout_ms = _resolve_pool_scene_attempt_timeout_ms(
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        provider_name=provider_name,
        timeout_ms=int(timeout_ms),
    )
    if (
        str(provider_name or "").strip().lower() == PROVIDER_CODEX
        and not str(api_key or "").strip()
        and codex_cli_lm_allowed_for_background()
    ):
        resolved_timeout_ms = resolve_codex_cli_timeout_ms(resolved_timeout_ms)
        floor_ms = _parse_int_env(
            "HOST_LLM_POOL_CODEX_MIN_TIMEOUT_MS",
            default=30_000,
            minimum=1_000,
            maximum=10 * 60 * 1000,
        )
        resolved_timeout_ms = max(resolved_timeout_ms, floor_ms)
    scene_token = _pool_env_token(scene_type) if str(scene_type or "").strip() else ""
    tier_token = _pool_env_token(cognitive_tier) if str(cognitive_tier or "").strip() else ""
    provider_token = _pool_env_token(provider_name) if str(provider_name or "").strip() else ""
    hard_cap_names: list[str] = []
    if scene_token and tier_token and provider_token:
        hard_cap_names.append(f"HOST_LLM_POOL_SCENE_{scene_token}_TIER_{tier_token}_{provider_token}_ATTEMPT_TIMEOUT_HARD_CAP_MS")
    if scene_token and provider_token:
        hard_cap_names.append(f"HOST_LLM_POOL_SCENE_{scene_token}_{provider_token}_ATTEMPT_TIMEOUT_HARD_CAP_MS")
    if scene_token and tier_token:
        hard_cap_names.append(f"HOST_LLM_POOL_SCENE_{scene_token}_TIER_{tier_token}_ATTEMPT_TIMEOUT_HARD_CAP_MS")
    if scene_token:
        hard_cap_names.append(f"HOST_LLM_POOL_SCENE_{scene_token}_ATTEMPT_TIMEOUT_HARD_CAP_MS")
    if tier_token and provider_token:
        hard_cap_names.append(f"HOST_LLM_POOL_TIER_{tier_token}_{provider_token}_ATTEMPT_TIMEOUT_HARD_CAP_MS")
    if tier_token:
        hard_cap_names.append(f"HOST_LLM_POOL_TIER_{tier_token}_ATTEMPT_TIMEOUT_HARD_CAP_MS")
    hard_cap_names.extend([
        "HOST_LLM_POOL_ATTEMPT_TIMEOUT_HARD_CAP_MS",
        "PENTEST_LLM_ATTEMPT_TIMEOUT_HARD_CAP_MS",
    ])
    for env_name in hard_cap_names:
        raw_value = str(os.environ.get(env_name) or "").strip()
        if raw_value:
            hard_cap_ms = _parse_int_env(
                env_name,
                default=resolved_timeout_ms,
                minimum=250,
                maximum=10 * 60 * 1000,
            )
            resolved_timeout_ms = min(resolved_timeout_ms, hard_cap_ms)
            break
    return resolved_timeout_ms


def _normalize_openai_compat_base_url(value: str | None) -> str | None:
    base_url = str(value or "").strip().rstrip("/")
    if not base_url:
        return None
    if base_url.lower().endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _extract_first_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None

    candidates = [text]
    candidates.extend(_JSON_BLOCK_RE.findall(text))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, Mapping):
            return dict(parsed)

    depth = 0
    start = -1
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth <= 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except Exception:
                    start = -1
                    continue
                if isinstance(parsed, Mapping):
                    return dict(parsed)
                start = -1
    return None


def _normalize_response_format(response_format: Any) -> str | None:
    if response_format is None:
        return None
    if isinstance(response_format, str):
        normalized = str(response_format or "").strip().lower()
        if normalized in {"json", "json_object", "object"}:
            return "json_object"
        return None
    if isinstance(response_format, Mapping):
        normalized = str(response_format.get("type") or "").strip().lower()
        if normalized in {"json", "json_object", "object"}:
            return "json_object"
    return None


def _validate_pool_response(*, content: str, response_format: Any, scene_type: str | None = None) -> str:
    text = str(content or "").strip()
    if not text:
        raise RuntimeError("empty llm response")
    normalized_format = _normalize_response_format(response_format)
    if normalized_format != "json_object":
        return text
    parsed = _extract_first_json_object(text)
    if not isinstance(parsed, Mapping):
        normalized_scene = str(scene_type or "").strip().upper()
        if _json_text_fallback_enabled(normalized_scene=normalized_scene):
            return json.dumps(
                {
                    "reply_text": text,
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        raise ValueError("pool response is not a valid json object")
    return json.dumps(dict(parsed), ensure_ascii=False, sort_keys=True)


def _json_text_fallback_gen_params(gen_params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(gen_params, Mapping):
        return gen_params
    cleaned = dict(gen_params)
    cleaned.pop("response_format", None)
    return cleaned


def _should_retry_json_text_fallback(*, normalized_scene: str, response_format: Any, exc: BaseException) -> bool:
    if _normalize_response_format(response_format) != "json_object":
        return False
    if not _json_text_fallback_enabled(normalized_scene=normalized_scene):
        return False
    if "empty llm response" in str(exc or "").strip().lower():
        return False
    reason_code, _cooldown_ms = _classify_pool_error(exc)
    return reason_code == "POOL_FORMAT_INVALID"


def _resolve_format_invalid_cooldown_ms(*, scene_type: str | None = None) -> int:
    normalized_scene = str(scene_type or "").strip().upper()
    scene_token = _pool_env_token(normalized_scene) if normalized_scene else ""
    if scene_token:
        env_name = f"HOST_LLM_POOL_SCENE_{scene_token}_FORMAT_INVALID_COOLDOWN_MS"
        if str(os.environ.get(env_name) or "").strip():
            return _parse_int_env(env_name, default=0, minimum=0, maximum=10 * 60 * 1000)
    return _parse_int_env(
        "HOST_LLM_POOL_FORMAT_INVALID_COOLDOWN_MS",
        default=0,
        minimum=0,
        maximum=10 * 60 * 1000,
    )


def _classify_pool_error(exc: BaseException, *, scene_type: str | None = None) -> tuple[str, int]:
    detail = str(exc or "").strip().lower()
    if isinstance(exc, TimeoutError):
        return "POOL_TIMEOUT", _parse_int_env(
            "HOST_LLM_POOL_TIMEOUT_COOLDOWN_MS",
            default=30_000,
            minimum=0,
            maximum=10 * 60 * 1000,
        )
    if "empty llm response" in detail or "response is not a valid json object" in detail:
        return "POOL_FORMAT_INVALID", _resolve_format_invalid_cooldown_ms(scene_type=scene_type)
    if any(marker in detail for marker in _KEY_FAIL_MARKERS):
        return "POOL_AUTH_FAILED", 0
    if any(marker in detail for marker in _BILLING_FAIL_MARKERS):
        return "POOL_BILLING_UNAVAILABLE", _parse_int_env(
            "HOST_LLM_POOL_BILLING_COOLDOWN_MS",
            default=15 * 60 * 1000,
            minimum=0,
            maximum=60 * 60 * 1000,
        )
    if any(marker in detail for marker in _RATE_LIMIT_MARKERS):
        return "POOL_RATE_LIMITED", _parse_int_env(
            "HOST_LLM_POOL_RATE_LIMIT_COOLDOWN_MS",
            default=90_000,
            minimum=0,
            maximum=15 * 60 * 1000,
        )
    if any(marker in detail for marker in _UNAVAILABLE_MARKERS):
        return "POOL_PROVIDER_UNAVAILABLE", _parse_int_env(
            "HOST_LLM_POOL_UNAVAILABLE_COOLDOWN_MS",
            default=45_000,
            minimum=0,
            maximum=10 * 60 * 1000,
        )
    return "POOL_CALL_FAILED", 0


def _make_pool_cache_key(
    *,
    scene_type: str,
    cognitive_tier: str,
    provider_name: str | None,
    model_id: str | None,
    base_url_override: str | None,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None,
    response_format: Any,
) -> str:
    payload = {
        "scene_type": str(scene_type or "").strip().upper(),
        "cognitive_tier": str(cognitive_tier or "").strip().upper(),
        "provider_name": str(provider_name or "").strip().lower(),
        "model_id": str(model_id or "").strip(),
        "base_url_override": str(base_url_override or "").strip(),
        "messages": [dict(item) for item in list(messages or []) if isinstance(item, Mapping)],
        "gen_params": dict(gen_params or {}),
        "response_format": response_format,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="ignore")).hexdigest()


def _resolve_pool_cache_ttl_ms() -> int:
    if not _parse_bool_env("HOST_LLM_POOL_CACHE_ENABLED", default=True):
        return 0
    return _parse_int_env(
        "HOST_LLM_POOL_CACHE_TTL_MS",
        default=15_000,
        minimum=0,
        maximum=10 * 60 * 1000,
    )


def _get_cached_pool_result(cache_key: str) -> PoolCompleteResult | None:
    ttl_ms = _resolve_pool_cache_ttl_ms()
    if ttl_ms <= 0:
        return None
    now_value = _now_ms()
    with _POOL_CACHE_LOCK:
        cached = _POOL_COMPLETION_CACHE.get(str(cache_key or ""))
        if not cached:
            return None
        expires_at_ms, result = cached
        if expires_at_ms <= now_value:
            _POOL_COMPLETION_CACHE.pop(str(cache_key or ""), None)
            return None
        return PoolCompleteResult(
            content=result.content,
            final_model=result.final_model,
            final_provider=result.final_provider,
            attempts=list(result.attempts),
            pool_exhausted=result.pool_exhausted,
            total_latency_ms=result.total_latency_ms,
            cached=True,
            cognitive_tier=result.cognitive_tier,
        )


def _put_cached_pool_result(cache_key: str, result: PoolCompleteResult) -> None:
    ttl_ms = _resolve_pool_cache_ttl_ms()
    if ttl_ms <= 0 or result.pool_exhausted:
        return
    with _POOL_CACHE_LOCK:
        _POOL_COMPLETION_CACHE[str(cache_key or "")] = (
            _now_ms() + ttl_ms,
            PoolCompleteResult(
                content=result.content,
                final_model=result.final_model,
                final_provider=result.final_provider,
                attempts=list(result.attempts),
                pool_exhausted=result.pool_exhausted,
                total_latency_ms=result.total_latency_ms,
                cached=False,
                cognitive_tier=result.cognitive_tier,
            ),
        )


def _normalize_cooldown_scope(*, scene_type: str | None = None) -> str:
    normalized_scene = str(scene_type or "").strip().upper()
    return normalized_scene or "GLOBAL"


def _cooldown_key(*, provider_name: str, model_id: str, scene_type: str | None = None) -> str:
    scope = _normalize_cooldown_scope(scene_type=scene_type)
    return f"{str(provider_name or '').strip().lower()}::{str(model_id or '').strip()}::{scope}"


def _get_cooldown_remaining_ms(*, provider_name: str, model_id: str, scene_type: str | None = None) -> int:
    key = _cooldown_key(provider_name=provider_name, model_id=model_id, scene_type=scene_type)
    now_value = _now_ms()
    with _POOL_COOLDOWN_LOCK:
        until_ms = int(_POOL_COOLDOWN_UNTIL_MS.get(key) or 0)
        if until_ms <= now_value:
            _POOL_COOLDOWN_UNTIL_MS.pop(key, None)
            return 0
        return until_ms - now_value


def _set_cooldown_ms(*, provider_name: str, model_id: str, cooldown_ms: int, scene_type: str | None = None) -> None:
    if cooldown_ms <= 0:
        return
    if not _parse_bool_env("HOST_LLM_POOL_COOLDOWN_ENABLED", default=True):
        return
    key = _cooldown_key(provider_name=provider_name, model_id=model_id, scene_type=scene_type)
    with _POOL_COOLDOWN_LOCK:
        _POOL_COOLDOWN_UNTIL_MS[key] = _now_ms() + int(cooldown_ms)


def _openai_compat_base_url_env() -> str:
    return str(
        os.environ.get("HOST_OPENAI_BASE_URL")
        or os.environ.get("HOST_CHAT_LOCAL_LLM_BASE_URL")
        or os.environ.get("HOST_CHAT_HOST_14B_BASE_URL")
        or ""
    ).strip()


def _is_local_openai_compat_base_url(value: str | None) -> bool:
    lowered = str(value or "").strip().lower()
    return (
        "127.0.0.1" in lowered
        or "localhost" in lowered
        or "[::1]" in lowered
    )


def _local_openai_compat_empty_key_allowed() -> bool:
    if str(os.environ.get("HOST_CHAT_LOCAL_LLM_ALLOW_EMPTY_KEY") or "").strip():
        return _parse_bool_env("HOST_CHAT_LOCAL_LLM_ALLOW_EMPTY_KEY", default=True)
    return _parse_bool_env("HOST_CHAT_HOST_14B_ALLOW_EMPTY_KEY", default=True)


def _load_api_key_from_env(provider_name: str) -> str | None:
    """M24-A：从环境变量读取 API Key（不硬编码）。

    支持的环境变量：
    - openai: OPENAI_API_KEY
    - gemini: GEMINI_API_KEY
    - deepseek: DEEPSEEK_API_KEY
    - anthropic: ANTHROPIC_API_KEY
    - ollama: 无需 API Key（本地服务）
    """
    provider_to_env: dict[str, str] = {
        PROVIDER_OPENAI: "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
        PROVIDER_MINIMAX: "MINIMAX_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_key = provider_to_env.get(str(provider_name or "").strip().lower())
    if not env_key:
        return None
    value = os.environ.get(env_key, "").strip()
    if not value:
        # 尝试 _REF 变体：DEEPSEEK_API_KEY_REF → sec://... → 解析
        ref_env = f"{env_key}_REF"
        ref_value = os.environ.get(ref_env, "").strip()
        if ref_value and ref_value.startswith("sec://"):
            value = _load_api_key_from_runtime_profiles(provider_name) or ""
            if value:
                os.environ[env_key] = value  # 缓存到 env 避免重复解析
    if value:
        return value
    if (
        provider_name == PROVIDER_OPENAI
        and _local_openai_compat_empty_key_allowed()
        and _is_local_openai_compat_base_url(_openai_compat_base_url_env())
    ):
        return _LOCAL_OPENAI_COMPAT_PLACEHOLDER_KEY
    return _load_api_key_from_runtime_profiles(provider_name)


def _load_api_key_from_runtime_profiles(provider_name: str) -> str | None:
    # Vault/profile resolution belongs to the application composition adapter.
    # Legacy pool callers receive only already-resolved credentials or env keys.
    del provider_name
    return None


def _resolve_provider_base_url(provider_name: str, *, base_url_override: str | None = None) -> str | None:
    provider = str(provider_name or "").strip().lower()
    if base_url_override:
        return _normalize_openai_compat_base_url(base_url_override)
    if provider == PROVIDER_DEEPSEEK:
        return _normalize_openai_compat_base_url(
            str(os.environ.get("HOST_DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()
        )
    if provider == PROVIDER_GEMINI:
        return _normalize_openai_compat_base_url(
            str(os.environ.get("HOST_GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai").strip()
        )
    if provider == PROVIDER_CODEX:
        return _normalize_openai_compat_base_url(
            str(os.environ.get("HOST_CODEX_BASE_URL") or os.environ.get("HOST_OPENAI_BASE_URL") or "").strip()
        )
    if provider == PROVIDER_MINIMAX:
        return _normalize_openai_compat_base_url(
            str(os.environ.get("HOST_MINIMAX_BASE_URL") or "https://api.minimaxi.com/v1").strip()
        )
    if provider == PROVIDER_OPENAI:
        return _normalize_openai_compat_base_url(_openai_compat_base_url_env())
    return None


def _is_minimax_standard_model(model_id: str | None) -> bool:
    return str(model_id or "").strip().lower() in {"m2-her", "minimax-m2-her"}


def _resolve_minimax_base_url(base_url_override: str | None = None) -> str:
    raw = str(base_url_override or os.environ.get("HOST_MINIMAX_BASE_URL") or "https://api.minimaxi.com/v1").strip()
    return raw or "https://api.minimaxi.com/v1"


def _resolve_minimax_standard_endpoint(base_url_override: str | None = None) -> str:
    raw = _resolve_minimax_base_url(base_url_override).rstrip("/")
    lowered = raw.lower()
    if lowered.endswith("/v1/text/chatcompletion_v2"):
        return raw
    if lowered.endswith("/v1"):
        return f"{raw}/text/chatcompletion_v2"
    return "https://api.minimaxi.com/v1/text/chatcompletion_v2"


def _extract_minimax_text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        text = str(value.get("text") or value.get("content") or "").strip()
        return text
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            piece = _extract_minimax_text_content(item)
            if piece:
                parts.append(piece)
        return "".join(parts)
    return str(value or "").strip()


def _internal_hard_max_output_tokens(params: Mapping[str, Any]) -> int | None:
    """Read the transport-only output ceiling without exposing it downstream.

    ``_natsume_hard_max_output_tokens`` is attached by the governed Model
    Attempt transport after route configuration has been frozen.  It is not a
    provider generation parameter: builders consume it only to constrain their
    public token field, and must never copy it into a provider request.
    """

    try:
        parsed = int(params.get("_natsume_hard_max_output_tokens"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _build_minimax_standard_payload(
    *,
    model_id: str,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None,
    stream: bool,
) -> dict[str, Any]:
    params = dict(gen_params or {})
    hard_max_tokens = _internal_hard_max_output_tokens(params)
    temperature = float(params.get("temperature", 0.4))
    top_p = float(params.get("top_p", 0.9))
    max_tokens = 1024
    for token_key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
        raw_value = params.get(token_key)
        try:
            parsed = int(raw_value) if raw_value is not None else 0
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            max_tokens = parsed
            break
    if hard_max_tokens is not None:
        max_tokens = min(max_tokens, hard_max_tokens)
    normalized_messages: list[dict[str, Any]] = []
    for item in list(messages or []):
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "user").strip() or "user"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        name = str(item.get("name") or "").strip()
        row: dict[str, Any] = {
            "role": role,
            "content": content,
        }
        if name:
            row["name"] = name
        normalized_messages.append(row)
    outbound_model_id = str(model_id or "").strip() or "M2-her"
    if outbound_model_id.lower() == "minimax-m2-her":
        outbound_model_id = "M2-her"
    return {
        "model": outbound_model_id,
        "messages": normalized_messages,
        "stream": bool(stream),
        "temperature": temperature,
        "top_p": top_p,
        "max_completion_tokens": max_tokens,
    }


def _iter_minimax_standard_stream(
    *,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> "Generator[str, None, None]":
    endpoint = _resolve_minimax_standard_endpoint(base_url_override)
    payload = _build_minimax_standard_payload(
        model_id=model_id,
        messages=messages,
        gen_params=gen_params,
        stream=True,
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "NatsumeHost/1.0",
        },
        method="POST",
    )
    timeout_s = max(1.0, float(timeout_ms) / 1000.0)
    saw_delta = False
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            while True:
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                data_text = line[5:].strip() if line.startswith("data:") else line
                if not data_text or data_text == "[DONE]":
                    continue
                try:
                    parsed = json.loads(data_text)
                except Exception:
                    _logger.debug("minimax standard stream skipped non-json line: %s", data_text[:160])
                    continue
                choices = parsed.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                first_choice = choices[0] if isinstance(choices[0], Mapping) else {}
                delta = first_choice.get("delta") if isinstance(first_choice, Mapping) else None
                delta_text = _extract_minimax_text_content((delta or {}).get("content") if isinstance(delta, Mapping) else None)
                if delta_text:
                    saw_delta = True
                    yield delta_text
                    continue
                if saw_delta:
                    continue
                message = first_choice.get("message") if isinstance(first_choice, Mapping) else None
                final_text = _extract_minimax_text_content((message or {}).get("content") if isinstance(message, Mapping) else None)
                if final_text:
                    yield final_text
                    return
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise RuntimeError(f"MiniMax standard stream API error {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MiniMax standard stream request failed: {exc}") from exc


def _invoke_minimax_standard_text(
    *,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> str:
    endpoint = _resolve_minimax_standard_endpoint(base_url_override)
    payload = _build_minimax_standard_payload(
        model_id=model_id,
        messages=messages,
        gen_params=gen_params,
        stream=False,
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "NatsumeHost/1.0",
        },
        method="POST",
    )
    timeout_s = max(1.0, float(timeout_ms) / 1000.0)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw_text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise RuntimeError(f"MiniMax standard API error {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MiniMax standard API request failed: {exc}") from exc

    try:
        parsed = json.loads(raw_text)
    except Exception as exc:
        raise RuntimeError(f"MiniMax standard API returned non-JSON payload: {raw_text[:400]}") from exc
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"MiniMax standard API returned no choices: {raw_text[:400]}")
    _set_last_provider_usage(parsed.get("usage"))
    first_choice = choices[0] if isinstance(choices[0], Mapping) else {}
    message = first_choice.get("message") if isinstance(first_choice, Mapping) else None
    content = _extract_minimax_text_content((message or {}).get("content") if isinstance(message, Mapping) else None)
    if not content:
        raise RuntimeError(f"MiniMax standard API returned empty content: {raw_text[:400]}")
    return content


def _default_mock_provider(_payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "narrative": {"text": "这是一个草案回复。"},
            "intent_draft": {
                "route": "chat",
                "target_id": "",
                "action": "narrative_only",
                "args": {},
            },
            "meta": {"draft_only": True, "source": "mock_gateway"},
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _inject_emotion_gen_params(
    prompt_payload: dict[str, Any],
    emotion_context: dict[str, Any],
) -> dict[str, Any]:
    """M29-F（OPT-10）：根据情感上下文动态调整 LLM 生成参数。

    情感强度高（EXCITED/ANGRY）→ temperature 上调（更有创意/活力）
    情感低落（SAD/TIRED）→ temperature 下调（更稳定/温柔）
    默认 temperature=0.7，top_p=0.9
    """
    emotion_tag = str(emotion_context.get("emotion_tag") or "NEUTRAL").upper()
    mood_state = str(emotion_context.get("mood_state") or "steady").lower()

    temperature = 0.55
    top_p = 0.85

    # Phase 2: 扩展到 16 种标签的精细化参数
    if emotion_tag in ("EXCITED", "HAPPY", "SURPRISED", "LOVING", "PROUD"):
        temperature = 0.72
        top_p = 0.92
    elif emotion_tag in ("ANGRY", "FRUSTRATED", "ANXIOUS"):
        temperature = 0.50
        top_p = 0.82
    elif emotion_tag in ("SAD", "TIRED", "BORED") or mood_state in ("tired", "critical"):
        temperature = 0.45
        top_p = 0.80
    elif emotion_tag in ("CALM", "GRATEFUL"):
        temperature = 0.52
        top_p = 0.85
    elif emotion_tag in ("EMBARRASSED", "CONFUSED"):
        temperature = 0.58
        top_p = 0.88

    result = dict(prompt_payload)
    result["gen_params"] = {
        "temperature": temperature,
        "top_p": top_p,
        "emotion_adjusted": True,
        "emotion_tag": emotion_tag,
    }
    return result


# 公开别名，供外部模块在 gateway 调用前预注入 gen_params
adjust_gen_params_for_emotion = _inject_emotion_gen_params


def _normalize_message_content(value: Any) -> str | list[dict[str, Any]]:
    if isinstance(value, list):
        normalized_items: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping):
                normalized_items.append(dict(item))
            elif item is not None:
                normalized_items.append({"type": "text", "text": str(item)})
        return normalized_items
    if isinstance(value, Mapping):
        return [dict(value)]
    return str(value or "")


def _build_langchain_messages(messages: list[Mapping[str, Any]]) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    lc_messages: list[Any] = []
    for item in list(messages or []):
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = _normalize_message_content(item.get("content"))
        if content == "" or content == []:
            continue
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=content))
    return lc_messages


def _extract_text_from_response_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _build_chat_openai_kwargs(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
    streaming: bool,
) -> dict[str, Any]:
    params = dict(gen_params or {})
    temperature = float(params.get("temperature", 0.4))
    top_p = float(params.get("top_p", 0.9))
    max_tokens = None
    for token_key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
        raw_value = params.get(token_key)
        try:
            parsed = int(raw_value) if raw_value is not None else 0
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            max_tokens = parsed
            break

    llm_kwargs: dict[str, Any] = {
        "model": str(model_id or "").strip() or "default",
        "api_key": api_key,
        "base_url": _resolve_provider_base_url(provider_name, base_url_override=base_url_override),
        "timeout": max(1.0, float(timeout_ms) / 1000.0),
        "max_retries": 1,
        "temperature": temperature,
        "top_p": top_p,
        "streaming": streaming,
    }
    if max_tokens is not None:
        llm_kwargs["max_tokens"] = max_tokens
    return llm_kwargs


def _resolve_openai_compat_chat_endpoint(
    provider_name: str,
    *,
    base_url_override: str | None = None,
) -> str:
    base_url = _resolve_provider_base_url(provider_name, base_url_override=base_url_override)
    if not base_url and str(provider_name or "").strip().lower() in {PROVIDER_OPENAI}:
        base_url = "https://api.openai.com/v1"
    if not base_url:
        raise RuntimeError(f"missing OpenAI-compatible base_url for provider={provider_name}")
    return f"{str(base_url).rstrip('/')}/chat/completions"


def _openai_compat_keepalive_enabled() -> bool:
    return _parse_bool_env("HOST_LLM_OPENAI_COMPAT_KEEPALIVE_ENABLED", default=True)


def _get_openai_compat_session() -> Any:
    global _OPENAI_COMPAT_SESSION
    with _OPENAI_COMPAT_SESSION_LOCK:
        if _OPENAI_COMPAT_SESSION is not None:
            return _OPENAI_COMPAT_SESSION
        import requests  # noqa: PLC0415
        from requests.adapters import HTTPAdapter  # noqa: PLC0415

        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _OPENAI_COMPAT_SESSION = session
        return session


def _post_openai_compat_json(
    *,
    endpoint: str,
    payload: dict[str, Any],
    api_key: str,
    timeout_s: float,
) -> Mapping[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if _openai_compat_keepalive_enabled():
        try:
            session = _get_openai_compat_session()
            response = session.post(endpoint, json=payload, headers=headers, timeout=timeout_s)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, Mapping):
                raise RuntimeError("OpenAI-compatible chat returned non-object payload")
            return data
        except ImportError:
            pass
        except Exception as exc:
            if exc.__class__.__name__ == "HTTPError":
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", "") if response is not None else ""
                body = str(getattr(response, "text", "") or "").strip() if response is not None else ""
                if body:
                    raise RuntimeError(
                        f"OpenAI-compatible chat failed HTTP {status_code}: {body[:1000]}"
                    ) from exc
                raise RuntimeError(f"OpenAI-compatible chat failed: {exc}") from exc
            if exc.__class__.__name__ == "JSONDecodeError":
                raise RuntimeError(f"OpenAI-compatible chat failed: {exc}") from exc
            raise

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI-compatible chat failed HTTP {exc.code}: {body[:1000]}") from exc
    data = json.loads(raw)
    if not isinstance(data, Mapping):
        raise RuntimeError("OpenAI-compatible chat returned non-object payload")
    return data


def _normalize_openai_compat_message_content(value: Any) -> str | list[dict[str, Any]]:
    if isinstance(value, list):
        content: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping):
                content.append(dict(item))
            elif item is not None:
                content.append({"type": "text", "text": str(item)})
        return content
    if isinstance(value, Mapping):
        return [dict(value)]
    return str(value or "")


def _build_openai_compat_messages(messages: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(messages or []):
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"system", "assistant", "user", "tool"}:
            role = "user"
        content = _normalize_openai_compat_message_content(item.get("content"))
        if content == "" or content == []:
            continue
        row: dict[str, Any] = {"role": role, "content": content}
        name = str(item.get("name") or "").strip()
        if name:
            row["name"] = name
        tool_call_id = str(item.get("tool_call_id") or "").strip()
        if tool_call_id:
            row["tool_call_id"] = tool_call_id
        rows.append(row)
    return rows


def _build_openai_compat_payload(
    *,
    provider_name: str = "",
    model_id: str,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    stream: bool = False,
    include_stream_usage: bool = False,
) -> dict[str, Any]:
    params = dict(gen_params or {})
    hard_max_tokens = _internal_hard_max_output_tokens(params)
    response_format = params.get("response_format")
    minimum_max_tokens = _openai_compat_minimum_max_tokens(
        model_id=model_id,
        response_format=response_format,
    )
    payload: dict[str, Any] = {
        "model": str(model_id or "").strip() or "default",
        "messages": _build_openai_compat_messages(messages),
        "stream": bool(stream),
    }
    thinking_mode = str(params.pop("thinking_mode", "") or "").strip().lower()
    if thinking_mode and thinking_mode not in {"enabled", "disabled"}:
        raise ValueError("thinking_mode must be enabled or disabled")
    if thinking_mode and str(provider_name or "").strip().lower() == "deepseek":
        payload["thinking"] = {"type": thinking_mode}
    # This is requested only by the result-attested streaming path.  A final
    # provider usage frame is evidence, not an estimate from emitted text.
    # Normal live streaming remains compatible with transports that do not
    # implement this OpenAI-compatible extension.
    if stream and include_stream_usage:
        payload["stream_options"] = {"include_usage": True}
    if "temperature" in params:
        payload["temperature"] = float(params.get("temperature", 0.4))
    else:
        payload["temperature"] = 0.4
    if "top_p" in params:
        payload["top_p"] = float(params.get("top_p", 0.9))
    else:
        payload["top_p"] = 0.9
    for token_key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
        raw_value = params.get(token_key)
        try:
            parsed = int(raw_value) if raw_value is not None else 0
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            if hard_max_tokens is not None:
                parsed = min(parsed, hard_max_tokens)
            elif minimum_max_tokens > 0:
                parsed = max(parsed, minimum_max_tokens)
            payload["max_tokens"] = parsed
            break
    if hard_max_tokens is not None and "max_tokens" not in payload:
        payload["max_tokens"] = hard_max_tokens
    if isinstance(response_format, Mapping):
        payload["response_format"] = dict(response_format)
    elif _normalize_response_format(response_format) == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _openai_compat_minimum_max_tokens(*, model_id: str, response_format: Any) -> int:
    normalized_model = str(model_id or "").strip().lower()
    if normalized_model != "deepseek-v4-flash":
        return 0
    if _normalize_response_format(response_format) == "json_object":
        return _parse_int_env(
            "HOST_DEEPSEEK_V4_FLASH_MIN_JSON_MAX_TOKENS",
            default=384,
            minimum=64,
            maximum=8192,
        )
    return _parse_int_env(
        "HOST_DEEPSEEK_V4_FLASH_MIN_TEXT_MAX_TOKENS",
        default=128,
        minimum=32,
        maximum=8192,
    )


def _extract_openai_compat_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], Mapping) else {}

    def _try_extract(container: Mapping) -> str:
        content_text = _extract_text_from_response_content(container.get("content"))
        if content_text:
            return content_text
        # reasoning_content: deepseek 等模型的推理链输出。
        # content 为空时自动回退到 reasoning_content（默认行为）。
        # 设 HOST_LLM_ACCEPT_REASONING_CONTENT_AS_TEXT=0 可强制禁用。
        reasoning = container.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            if not _parse_bool_env("HOST_LLM_ACCEPT_REASONING_CONTENT_AS_TEXT", default=True):
                return ""
            return reasoning.strip()
        return ""

    message = first.get("message") if isinstance(first, Mapping) else None
    if isinstance(message, Mapping):
        return _try_extract(message)
    delta = first.get("delta") if isinstance(first, Mapping) else None
    if isinstance(delta, Mapping):
        return _try_extract(delta)
    return ""


def _summarize_openai_compat_empty_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    summary: dict[str, Any] = {
        "choice_count": len(choices) if isinstance(choices, list) else 0,
    }
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], Mapping) else {}
        summary["choice_keys"] = sorted(str(key) for key in first.keys())
        summary["finish_reason"] = first.get("finish_reason")
        for field_name in ("message", "delta"):
            container = first.get(field_name) if isinstance(first, Mapping) else None
            if not isinstance(container, Mapping):
                continue
            content_text = _extract_text_from_response_content(container.get("content"))
            summary[f"{field_name}_keys"] = sorted(str(key) for key in container.keys())
            summary[f"{field_name}_content_type"] = type(container.get("content")).__name__
            summary[f"{field_name}_content_len"] = len(content_text)
            for extra_field in ("reasoning_content", "refusal"):
                extra_value = container.get(extra_field)
                if isinstance(extra_value, str):
                    summary[f"{field_name}_{extra_field}_len"] = len(extra_value)
            tool_calls = container.get("tool_calls")
            if isinstance(tool_calls, list):
                summary[f"{field_name}_tool_call_count"] = len(tool_calls)
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        summary["usage_keys"] = sorted(str(key) for key in usage.keys())
    return summary


def _invoke_openai_compat_text(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> str:
    endpoint = _resolve_openai_compat_chat_endpoint(provider_name, base_url_override=base_url_override)
    payload = _build_openai_compat_payload(
        provider_name=provider_name,
        model_id=model_id,
        messages=messages,
        gen_params=gen_params,
        stream=False,
    )
    data = _post_openai_compat_json(
        endpoint=endpoint,
        payload=payload,
        api_key=api_key,
        timeout_s=max(1.0, float(timeout_ms) / 1000.0),
    )
    _set_last_provider_usage(data.get("usage"))
    text = _extract_openai_compat_text(data)
    if not text and _parse_bool_env("HOST_LLM_OPENAI_COMPAT_EMPTY_RESPONSE_LOG_ENABLED", default=True):
        _logger.warning(
            "openai-compatible response empty: provider=%s model=%s summary=%s",
            str(provider_name or "").strip().lower(),
            str(model_id or "").strip(),
            json.dumps(_summarize_openai_compat_empty_response(data), ensure_ascii=False, sort_keys=True),
        )
    return text


def _iter_openai_compat_stream(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> "Generator[str, None, None]":
    endpoint = _resolve_openai_compat_chat_endpoint(provider_name, base_url_override=base_url_override)
    payload = _build_openai_compat_payload(
        provider_name=provider_name,
        model_id=model_id,
        messages=messages,
        gen_params=gen_params,
        stream=True,
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    final_message = ""
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_ms) / 1000.0)) as response:
            while True:
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data_text = line[len("data:") :].strip()
                if data_text == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_text)
                except Exception:
                    continue
                if not isinstance(chunk, Mapping):
                    continue
                text = _extract_openai_compat_text(chunk)
                if text:
                    yield text
                choices = chunk.get("choices")
                if isinstance(choices, list) and choices:
                    first = choices[0] if isinstance(choices[0], Mapping) else {}
                    message = first.get("message") if isinstance(first, Mapping) else None
                    if isinstance(message, Mapping):
                        candidate = _extract_text_from_response_content(message.get("content"))
                        if candidate:
                            final_message = candidate
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI-compatible stream failed HTTP {exc.code}: {body[:1000]}") from exc
    if final_message:
        yield final_message


def _iter_openai_compat_stream_events(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> "Generator[dict[str, Any], None, None]":
    """Yield text deltas and one provider-owned final usage record.

    The terminal record deliberately preserves an empty usage mapping when a
    provider omits it.  The governed ModelAttempt authority will then reject
    the attempt rather than reverse-engineering tokens or cost from text.
    """

    endpoint = _resolve_openai_compat_chat_endpoint(
        provider_name,
        base_url_override=base_url_override,
    )
    payload = _build_openai_compat_payload(
        provider_name=provider_name,
        model_id=model_id,
        messages=messages,
        gen_params=gen_params,
        stream=True,
        include_stream_usage=True,
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    chunks: list[str] = []
    final_content = ""
    final_usage: dict[str, Any] = {}
    final_finish_reason: str | None = None
    try:
        with urllib.request.urlopen(
            request,
            timeout=max(1.0, float(timeout_ms) / 1000.0),
        ) as response:
            while True:
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data_text = line[len("data:") :].strip()
                if data_text == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_text)
                except Exception:
                    continue
                if not isinstance(chunk, Mapping):
                    continue
                usage = chunk.get("usage")
                if isinstance(usage, Mapping):
                    final_usage = dict(usage)
                choices = chunk.get("choices")
                if isinstance(choices, list) and choices:
                    first = choices[0] if isinstance(choices[0], Mapping) else {}
                    finish_reason = first.get("finish_reason") if isinstance(first, Mapping) else None
                    if finish_reason is not None:
                        final_finish_reason = str(finish_reason)
                    message = first.get("message") if isinstance(first, Mapping) else None
                    if isinstance(message, Mapping):
                        candidate = _extract_text_from_response_content(message.get("content"))
                        if candidate:
                            final_content = candidate
                text = _extract_openai_compat_text(chunk)
                if text:
                    chunks.append(text)
                    yield {"kind": "delta", "text": text}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenAI-compatible stream failed HTTP {exc.code}: {body[:1000]}"
        ) from exc
    yield {
        "kind": "final",
        "content": final_content or "".join(chunks),
        "finish_reason": final_finish_reason,
        "usage": final_usage,
    }


def _build_chat_google_genai_kwargs(
    *,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    gen_params: dict[str, Any] | None = None,
    streaming: bool,
) -> dict[str, Any]:
    params = dict(gen_params or {})
    hard_max_tokens = _internal_hard_max_output_tokens(params)
    temperature = float(params.get("temperature", 0.4))
    top_p = float(params.get("top_p", 0.9))
    normalized_model_id = str(model_id or "").strip().lower()
    max_tokens = None
    for token_key in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
        raw_value = params.get(token_key)
        try:
            parsed = int(raw_value) if raw_value is not None else 0
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            # Native Gemini Pro models can spend a tiny output cap before visible
            # text appears (signed content blocks / thinking overhead), which
            # looks like a false empty response to the pool validator.
            minimum_cap = 1024 if "pro" in normalized_model_id else 64
            max_tokens = (
                min(parsed, hard_max_tokens)
                if hard_max_tokens is not None
                else max(parsed, minimum_cap)
            )
            break
    if max_tokens is None and hard_max_tokens is not None:
        max_tokens = hard_max_tokens

    llm_kwargs: dict[str, Any] = {
        "model": str(model_id or "").strip() or "gemini-2.5-flash-lite",
        "api_key": api_key,
        "request_timeout": max(1.0, float(timeout_ms) / 1000.0),
        "retries": 0,
        "temperature": temperature,
        "top_p": top_p,
        "streaming": streaming,
    }
    if max_tokens is not None:
        llm_kwargs["max_tokens"] = max_tokens
    return llm_kwargs


def _invoke_gemini_native_text(
    *,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
) -> str:
    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(
        **_build_chat_google_genai_kwargs(
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            gen_params=gen_params,
            streaming=False,
        )
    )
    response = llm.invoke(_build_langchain_messages(messages))
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, Mapping):
        metadata = getattr(response, "response_metadata", None)
        if isinstance(metadata, Mapping):
            usage = metadata.get("usage_metadata") or metadata.get("token_usage")
    _set_last_provider_usage(usage)
    return _extract_text_from_response_content(getattr(response, "content", ""))


def invoke_llm_text(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    response_format: Any = None,
    base_url_override: str | None = None,
    feature_key: str | None = None,
) -> str:
    provider = str(provider_name or "").strip().lower()
    if provider == PROVIDER_MOCK:
        return _default_mock_provider({})
    if provider == PROVIDER_DISABLED:
        raise RuntimeError("provider disabled")
    if provider == PROVIDER_CODEX and not str(api_key or "").strip() and codex_cli_lm_allowed_for_background():
        return invoke_codex_cli_text(
            model_id=model_id,
            messages=messages,
            timeout_ms=timeout_ms,
            response_format=response_format,
            feature_key=feature_key,
        )
    if not str(api_key or "").strip():
        raise RuntimeError(f"missing api key for provider={provider_name}")
    if response_format is not None:
        runtime_gen_params = dict(gen_params or {})
        runtime_gen_params.setdefault("response_format", response_format)
    else:
        runtime_gen_params = gen_params
    if provider == PROVIDER_MINIMAX and _is_minimax_standard_model(model_id):
        return _invoke_minimax_standard_text(
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=messages,
            gen_params=runtime_gen_params,
            base_url_override=base_url_override,
        )
    if provider == PROVIDER_GEMINI:
        return _invoke_gemini_native_text(
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=messages,
            gen_params=runtime_gen_params,
        )

    return _invoke_openai_compat_text(
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        timeout_ms=timeout_ms,
        messages=messages,
        gen_params=runtime_gen_params,
        base_url_override=base_url_override,
    )


def _resolve_pool_candidates(
    *,
    cognitive_tier: str,
    scene_type: str | None = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
    base_url_override: str | None = None,
) -> list[dict[str, Any]]:
    normalized_tier = str(cognitive_tier or "B").strip().upper() or "B"
    normalized_scene = str(scene_type or "").strip().upper()
    raw_spec, scene_specific_spec, _spec_source = _resolve_pool_spec(
        normalized_scene=normalized_scene,
        normalized_tier=normalized_tier,
    )
    parsed_specs: list[tuple[str, str]] = []
    for item in raw_spec.split(","):
        text = str(item or "").strip()
        if not text or ":" not in text:
            continue
        provider_part, model_part = text.split(":", 1)
        provider_token = str(provider_part or "").strip().lower()
        model_token = str(model_part or "").strip()
        if provider_token and model_token:
            parsed_specs.append((provider_token, model_token))

    explicit_provider = str(provider_name or "").strip().lower()
    explicit_model = str(model_id or "").strip()
    explicit_key = str(api_key or "").strip() or None
    explicit_base_url = str(base_url_override or "").strip() or None

    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []

    def _append_candidate(provider_token: str, model_token: str, key_value: str | None, base_url_value: str | None) -> None:
        if normalized_scene == "MULTIMODAL" and provider_token not in _MULTIMODAL_PROVIDER_ALLOWLIST:
            return
        dedupe_key = f"{provider_token}::{model_token}"
        if dedupe_key in seen:
            return
        resolved_key = key_value or _load_api_key_from_env(provider_token)
        cli_backed_codex = provider_token == PROVIDER_CODEX and not resolved_key and codex_cli_lm_allowed_for_background()
        if provider_token not in {PROVIDER_MOCK, PROVIDER_DISABLED} and not resolved_key:
            if not cli_backed_codex:
                return
        candidates.append(
            {
                "provider_name": provider_token,
                "model_id": model_token,
                "api_key": resolved_key,
                "base_url_override": base_url_value or _resolve_provider_base_url(provider_token),
            }
        )
        seen.add(dedupe_key)

    include_explicit = _scene_pool_include_explicit(
        normalized_scene=normalized_scene,
        scene_specific=scene_specific_spec,
    )

    if (
        _codex_pool_migration_enabled(normalized_scene)
        and explicit_provider not in {PROVIDER_MOCK, PROVIDER_DISABLED}
        and include_explicit
    ):
        _append_candidate(PROVIDER_CODEX, _resolve_pool_codex_model_id(), None, None)

    if include_explicit and explicit_provider and explicit_model:
        _append_candidate(explicit_provider, explicit_model, explicit_key, explicit_base_url)
        if explicit_provider == PROVIDER_GEMINI:
            gemini_fallback_model = str(
                os.environ.get("HOST_GEMINI_MODEL_ID")
                or os.environ.get("HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID")
                or "gemini-2.5-flash-lite"
            ).strip()
            if gemini_fallback_model and gemini_fallback_model != explicit_model:
                _append_candidate(explicit_provider, gemini_fallback_model, explicit_key, explicit_base_url)

    for provider_token, model_token in parsed_specs:
        _append_candidate(provider_token, model_token, None, None)

    if normalized_scene == "CHAT_SURFACE" and scene_specific_spec:
        for item in str(_POOL_DEFAULT_SPECS.get(normalized_tier) or "").split(","):
            text = str(item or "").strip()
            if not text or ":" not in text:
                continue
            provider_part, model_part = text.split(":", 1)
            provider_token = str(provider_part or "").strip().lower()
            model_token = str(model_part or "").strip()
            if provider_token and model_token:
                _append_candidate(provider_token, model_token, None, None)

    return candidates


def _log_llm_pool_call(
    *,
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    attempts: list[ModelAttempt],
    final_model: str,
    final_provider: str,
    pool_exhausted: bool,
    cached: bool,
    total_latency_ms: int,
    messages: list[Mapping[str, Any]] | None = None,
    content: Any = "",
    token_usage: Mapping[str, Any] | None = None,
    ts_ms: int | None = None,
    ledger: Any = None,
) -> None:
    try:
        from host_brain.observability.token_usage import build_llm_token_usage_row  # noqa: PLC0415

        if ledger is None:
            return
        ts_value = int(ts_ms if ts_ms is not None else _now_ms())
        attempts_payload = [
            {
                "model": item.model,
                "provider": item.provider,
                "status": item.status,
                "latency_ms": int(item.latency_ms),
                "reason_code": item.reason_code,
                "detail": item.detail,
            }
            for item in list(attempts or [])
        ]
        attempts_json = json.dumps(
            attempts_payload,
            ensure_ascii=False,
            sort_keys=True,
        )
        if hasattr(ledger, "log_llm_pool_call"):
            ledger.log_llm_pool_call(
                {
                    "request_id": str(request_id or "").strip(),
                    "scene_type": str(scene_type or "").strip().upper(),
                    "cognitive_tier": str(cognitive_tier or "").strip().upper(),
                    "attempts_json": attempts_json,
                    "attempt_count": len(list(attempts or [])),
                    "final_model": str(final_model or "").strip(),
                    "final_provider": str(final_provider or "").strip().lower(),
                    "pool_exhausted": 1 if pool_exhausted else 0,
                    "cached": 1 if cached else 0,
                    "degraded_to_rule": 0,
                    "total_latency_ms": int(total_latency_ms),
                    "ts_ms": ts_value,
                }
            )
        if hasattr(ledger, "log_llm_token_usage"):
            ledger.log_llm_token_usage(
                build_llm_token_usage_row(
                    request_id=str(request_id or "").strip(),
                    scene_type=str(scene_type or "").strip().upper(),
                    cognitive_tier=str(cognitive_tier or "").strip().upper(),
                    provider=str(final_provider or "").strip().lower(),
                    model_id=str(final_model or "").strip(),
                    messages=messages or [],
                    content=content,
                    attempts=list(attempts or []),
                    cached=bool(cached),
                    pool_exhausted=bool(pool_exhausted),
                    ts_ms=ts_value,
                    provider_usage=token_usage,
                )
            )
    except Exception as exc:
        _logger.debug("log_llm_pool_call skipped: %s", exc)


def complete_llm_with_pool_sync(
    *,
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    messages: list[Mapping[str, Any]],
    timeout_ms: int,
    gen_params: dict[str, Any] | None = None,
    response_format: Any = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
    base_url_override: str | None = None,
) -> PoolCompleteResult:
    normalized_scene = str(scene_type or "CHAT").strip().upper() or "CHAT"
    normalized_tier = str(cognitive_tier or "B").strip().upper() or "B"
    attempt_rows: list[ModelAttempt] = []
    started_at = perf_counter()
    max_attempts = _resolve_pool_max_attempts(
        normalized_scene=normalized_scene,
        normalized_tier=normalized_tier,
    )
    attempted_candidates = 0

    cache_key = _make_pool_cache_key(
        scene_type=normalized_scene,
        cognitive_tier=normalized_tier,
        provider_name=provider_name,
        model_id=model_id,
        base_url_override=base_url_override,
        messages=messages,
        gen_params=gen_params,
        response_format=response_format,
    )
    cached_result = _get_cached_pool_result(cache_key)
    if cached_result is not None:
        _log_llm_pool_call(
            request_id=request_id,
            scene_type=normalized_scene,
            cognitive_tier=normalized_tier,
            attempts=list(cached_result.attempts),
            final_model=cached_result.final_model,
            final_provider=cached_result.final_provider,
            pool_exhausted=False,
            cached=True,
            total_latency_ms=cached_result.total_latency_ms,
            messages=messages,
            content=cached_result.content,
        )
        return cached_result

    for candidate in _resolve_pool_candidates(
        cognitive_tier=normalized_tier,
        scene_type=normalized_scene,
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        base_url_override=base_url_override,
    ):
        provider_token = str(candidate.get("provider_name") or "").strip().lower()
        model_token = str(candidate.get("model_id") or "").strip()
        cooldown_remaining_ms = _get_cooldown_remaining_ms(
            provider_name=provider_token,
            model_id=model_token,
            scene_type=normalized_scene,
        )
        if cooldown_remaining_ms > 0:
            attempt_rows.append(
                ModelAttempt(
                    model=model_token,
                    provider=provider_token,
                    status="COOLDOWN",
                    latency_ms=0,
                    reason_code="POOL_COOLDOWN_ACTIVE",
                    detail=f"cooldown_remaining_ms={cooldown_remaining_ms}",
                )
            )
            continue

        if max_attempts > 0 and attempted_candidates >= max_attempts:
            break
        attempted_candidates += 1
        attempt_started = perf_counter()
        try:
            _set_last_provider_usage(None)
            attempt_timeout_ms = _resolve_pool_attempt_timeout_ms(
                provider_name=provider_token,
                api_key=str(candidate.get("api_key") or "").strip(),
                timeout_ms=int(timeout_ms),
                scene_type=normalized_scene,
                cognitive_tier=normalized_tier,
            )
            raw_text = _invoke_provider_with_timeout(
                provider=lambda _payload: invoke_llm_text(
                    provider_name=provider_token,
                    model_id=model_token,
                    api_key=str(candidate.get("api_key") or "").strip(),
                    timeout_ms=attempt_timeout_ms,
                    messages=messages,
                    gen_params=gen_params,
                    response_format=response_format,
                    base_url_override=str(candidate.get("base_url_override") or "").strip() or None,
                    feature_key=f"pool:{normalized_scene}:{normalized_tier}",
                ),
                payload={},
                timeout_ms=attempt_timeout_ms,
            )
            provider_usage = _pop_last_provider_usage()
            try:
                content = _validate_pool_response(
                    content=raw_text,
                    response_format=response_format,
                    scene_type=normalized_scene,
                )
                fallback_used = False
            except Exception as validation_exc:
                if not _should_retry_json_text_fallback(
                    normalized_scene=normalized_scene,
                    response_format=response_format,
                    exc=validation_exc,
                ):
                    raise
                _set_last_provider_usage(None)
                fallback_raw_text = _invoke_provider_with_timeout(
                    provider=lambda _payload: invoke_llm_text(
                        provider_name=provider_token,
                        model_id=model_token,
                        api_key=str(candidate.get("api_key") or "").strip(),
                        timeout_ms=attempt_timeout_ms,
                        messages=messages,
                        gen_params=_json_text_fallback_gen_params(gen_params),
                        response_format=None,
                        base_url_override=str(candidate.get("base_url_override") or "").strip() or None,
                        feature_key=f"pool:{normalized_scene}:{normalized_tier}:json_text_fallback",
                    ),
                    payload={},
                    timeout_ms=attempt_timeout_ms,
                )
                fallback_usage = _pop_last_provider_usage()
                if fallback_usage:
                    provider_usage = fallback_usage
                content = _validate_pool_response(
                    content=fallback_raw_text,
                    response_format=response_format,
                    scene_type=normalized_scene,
                )
                fallback_used = True
            latency_ms = int(round((perf_counter() - attempt_started) * 1000))
            attempt_rows.append(
                ModelAttempt(
                    model=model_token,
                    provider=provider_token,
                    status="OK",
                    latency_ms=latency_ms,
                    detail=(
                        f"attempt_timeout_ms={attempt_timeout_ms};json_text_fallback"
                        if fallback_used
                        else f"attempt_timeout_ms={attempt_timeout_ms}"
                    ),
                )
            )
            result = PoolCompleteResult(
                content=content,
                final_model=model_token,
                final_provider=provider_token,
                attempts=list(attempt_rows),
                pool_exhausted=False,
                total_latency_ms=int(round((perf_counter() - started_at) * 1000)),
                cached=False,
                cognitive_tier=normalized_tier,
            )
            _put_cached_pool_result(cache_key, result)
            _log_llm_pool_call(
                request_id=request_id,
                scene_type=normalized_scene,
                cognitive_tier=normalized_tier,
                attempts=list(result.attempts),
                final_model=result.final_model,
                final_provider=result.final_provider,
                pool_exhausted=False,
                cached=False,
                total_latency_ms=result.total_latency_ms,
                messages=messages,
                content=result.content,
                token_usage=provider_usage,
            )
            return result
        except Exception as exc:
            _pop_last_provider_usage()
            latency_ms = int(round((perf_counter() - attempt_started) * 1000))
            reason_code, cooldown_ms = _classify_pool_error(exc, scene_type=normalized_scene)
            _set_cooldown_ms(
                provider_name=provider_token,
                model_id=model_token,
                cooldown_ms=cooldown_ms,
                scene_type=normalized_scene,
            )
            attempt_rows.append(
                ModelAttempt(
                    model=model_token,
                    provider=provider_token,
                    status="FAILED",
                    latency_ms=latency_ms,
                    reason_code=reason_code,
                    detail=f"attempt_timeout_ms={attempt_timeout_ms};{str(exc)[:220]}",
                )
            )
            continue

    result = PoolCompleteResult(
        content="",
        final_model="",
        final_provider="",
        attempts=list(attempt_rows),
        pool_exhausted=True,
        total_latency_ms=int(round((perf_counter() - started_at) * 1000)),
        cached=False,
        cognitive_tier=normalized_tier,
    )
    _log_llm_pool_call(
        request_id=request_id,
        scene_type=normalized_scene,
        cognitive_tier=normalized_tier,
        attempts=list(result.attempts),
        final_model="",
        final_provider="",
        pool_exhausted=True,
        cached=False,
        total_latency_ms=result.total_latency_ms,
        messages=messages,
        content="",
    )
    return result


async def acomplete_llm_with_pool(
    **kwargs: Any,
) -> PoolCompleteResult:
    return await __import__("asyncio").to_thread(complete_llm_with_pool_sync, **kwargs)


def complete_prompt_with_pool_sync(
    *,
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    prompt: str,
    system_prompt: str = "",
    timeout_ms: int,
    gen_params: dict[str, Any] | None = None,
    response_format: Any = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
    base_url_override: str | None = None,
) -> str:
    messages = []
    system_text = str(system_prompt or "").strip()
    user_text = str(prompt or "").strip()
    if system_text:
        messages.append({"role": "system", "content": system_text})
    if user_text:
        messages.append({"role": "user", "content": user_text})
    if not messages:
        raise RuntimeError("empty llm prompt")
    result = complete_llm_with_pool_sync(
        request_id=request_id,
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        messages=messages,
        timeout_ms=timeout_ms,
        gen_params=gen_params,
        response_format=response_format,
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        base_url_override=base_url_override,
    )
    if result.pool_exhausted:
        raise RuntimeError(LLM_POOL_EXHAUSTED)
    return result.content


def build_pool_prompt_llm_fn(
    *,
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    timeout_ms: int,
    system_prompt: str = "",
    gen_params: dict[str, Any] | None = None,
    response_format: Any = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
    base_url_override: str | None = None,
) -> Callable[[str], str]:
    def _llm_fn(prompt: str) -> str:
        return complete_prompt_with_pool_sync(
            request_id=request_id,
            scene_type=scene_type,
            cognitive_tier=cognitive_tier,
            prompt=str(prompt or "").strip(),
            system_prompt=system_prompt,
            timeout_ms=timeout_ms,
            gen_params=gen_params,
            response_format=response_format,
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            base_url_override=base_url_override,
        )

    return _llm_fn


def stream_llm_chunks(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> "Generator[str, None, None]":
    """P0 SSE 优化：直接调用 LangChain streaming 模式，逐 token yield 文本片段。

    Args:
        provider_name: 提供商名称（openai/gemini/deepseek）
        model_id: 模型 ID
        api_key: API Key
        timeout_ms: 超时毫秒
        messages: 原生 chat messages 数组
        gen_params: 可选生成参数（temperature/top_p）

    Yields:
        str: LLM 返回的文本片段（token chunk）
    """
    provider = str(provider_name or "").strip().lower()
    if provider == PROVIDER_MOCK:
        yield _default_mock_provider({})
        return
    if provider == PROVIDER_DISABLED:
        raise RuntimeError("provider disabled")
    if provider == PROVIDER_CODEX and not str(api_key or "").strip() and codex_cli_lm_allowed_for_background():
        yield invoke_codex_cli_text(
            model_id=model_id,
            messages=messages,
            timeout_ms=timeout_ms,
            feature_key="stream",
        )
        return
    if not str(api_key or "").strip():
        raise RuntimeError(f"missing api key for provider={provider_name}")

    if provider == PROVIDER_MINIMAX and _is_minimax_standard_model(model_id):
        yield from _iter_minimax_standard_stream(
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=messages,
            gen_params=gen_params,
            base_url_override=base_url_override,
        )
        return

    if provider == PROVIDER_GEMINI:
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            **_build_chat_google_genai_kwargs(
                model_id=model_id,
                api_key=api_key,
                timeout_ms=timeout_ms,
                gen_params=gen_params,
                streaming=True,
            )
        )
        for chunk in llm.stream(_build_langchain_messages(messages)):
            text = _extract_text_from_response_content(getattr(chunk, "content", "")) or ""
            if text:
                yield text
        return

    yield from _iter_openai_compat_stream(
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        timeout_ms=timeout_ms,
        messages=messages,
        gen_params=gen_params,
        base_url_override=base_url_override,
    )


def stream_llm_stream_events(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> "Generator[dict[str, Any], None, None]":
    """Stream deltas plus a terminal usage record for governed chat.

    Only the OpenAI-compatible protocol is enabled here because it has a
    concrete final usage frame (`stream_options.include_usage`).  Unsupported
    providers fail explicitly; a caller must not turn missing evidence into a
    token or cost estimate.
    """

    provider = str(provider_name or "").strip().lower()
    if provider not in {PROVIDER_OPENAI, PROVIDER_DEEPSEEK}:
        raise RuntimeError(
            "provider does not support canonical final usage for governed streaming"
        )
    if not str(api_key or "").strip():
        raise RuntimeError(f"missing api key for provider={provider_name}")
    yield from _iter_openai_compat_stream_events(
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        timeout_ms=timeout_ms,
        messages=messages,
        gen_params=gen_params,
        base_url_override=base_url_override,
    )


async def astream_llm_chunks(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> "AsyncGenerator[str, None]":
    """P0 SSE 优化：原生异步流式生成器，通过 asyncio.Queue 桥接同步 LangChain streaming。

    相比 stream_llm_chunks（同步），本函数可在 async 上下文中直接 async for 迭代，
    无需在调用方手动创建线程桥接，适合在 FastAPI SSE handler 中直接使用。

    Args:
        provider_name: 提供商名称（openai/gemini/deepseek）
        model_id: 模型 ID
        api_key: API Key
        timeout_ms: 超时毫秒
        messages: 原生 chat messages 数组
        gen_params: 可选生成参数（temperature/top_p）

    Yields:
        str: LLM 返回的文本片段（token chunk）
    """
    import asyncio
    import threading

    loop = asyncio.get_event_loop()
    # 修复：使用 str | None | BaseException 联合类型队列，将生产者异常传递给消费者。
    # 原逻辑 except Exception: pass 静默吞掉所有异常，导致 SSE 流静默返回空内容，
    # 调用方无法区分"LLM 正常返回空"和"LLM 调用失败"，且错误日志完全缺失。
    chunk_queue: asyncio.Queue[str | None | BaseException] = asyncio.Queue()
    _SENTINEL = None  # 正常结束哨兵

    def _producer() -> None:
        try:
            for chunk in stream_llm_chunks(
                provider_name=provider_name,
                model_id=model_id,
                api_key=api_key,
                timeout_ms=timeout_ms,
                messages=messages,
                gen_params=gen_params,
                base_url_override=base_url_override,
            ):
                asyncio.run_coroutine_threadsafe(chunk_queue.put(chunk), loop)
        except Exception as exc:
            # 将异常对象放入队列，由消费者侧重新抛出
            asyncio.run_coroutine_threadsafe(chunk_queue.put(exc), loop)
            return
        finally:
            asyncio.run_coroutine_threadsafe(chunk_queue.put(_SENTINEL), loop)

    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()

    while True:
        item = await chunk_queue.get()
        if item is _SENTINEL:
            break
        if isinstance(item, BaseException):
            # 重新抛出生产者线程中的异常，确保调用方可以感知流式生成失败
            raise item
        yield item  # type: ignore[misc]


async def astream_llm_stream_events(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> "AsyncGenerator[dict[str, Any], None]":
    """Bridge the usage-attested synchronous SSE reader into async code.

    Exceptions are propagated to the consumer rather than becoming an empty
    stream.  The terminal `final` mapping is deliberately passed through even
    when its usage mapping is empty; the ModelAttempt authority owns the
    resulting fail-closed budget decision.
    """

    import asyncio
    import threading

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[dict[str, Any] | BaseException | None] = asyncio.Queue()
    sentinel = None

    def _producer() -> None:
        try:
            for event in stream_llm_stream_events(
                provider_name=provider_name,
                model_id=model_id,
                api_key=api_key,
                timeout_ms=timeout_ms,
                messages=messages,
                gen_params=gen_params,
                base_url_override=base_url_override,
            ):
                asyncio.run_coroutine_threadsafe(event_queue.put(dict(event)), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(event_queue.put(exc), loop)
        finally:
            asyncio.run_coroutine_threadsafe(event_queue.put(sentinel), loop)

    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()
    while True:
        item = await event_queue.get()
        if item is sentinel:
            break
        if isinstance(item, BaseException):
            raise item
        yield dict(item)


async def astream_llm_chunks_with_pool(
    *,
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    provider_name: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
    base_url_override: str | None = None,
) -> "AsyncGenerator[str, None]":
    normalized_scene = str(scene_type or "CHAT").strip().upper() or "CHAT"
    normalized_tier = str(cognitive_tier or "B").strip().upper() or "B"
    attempt_rows: list[ModelAttempt] = []
    started_at = perf_counter()
    max_attempts = _resolve_pool_max_attempts(
        normalized_scene=normalized_scene,
        normalized_tier=normalized_tier,
    )
    attempted_candidates = 0

    for candidate in _resolve_pool_candidates(
        cognitive_tier=normalized_tier,
        scene_type=normalized_scene,
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        base_url_override=base_url_override,
    ):
        provider_token = str(candidate.get("provider_name") or "").strip().lower()
        model_token = str(candidate.get("model_id") or "").strip()
        cooldown_remaining_ms = _get_cooldown_remaining_ms(
            provider_name=provider_token,
            model_id=model_token,
            scene_type=normalized_scene,
        )
        if cooldown_remaining_ms > 0:
            attempt_rows.append(
                ModelAttempt(
                    model=model_token,
                    provider=provider_token,
                    status="COOLDOWN",
                    latency_ms=0,
                    reason_code="POOL_COOLDOWN_ACTIVE",
                    detail=f"cooldown_remaining_ms={cooldown_remaining_ms}",
                )
            )
            continue

        if max_attempts > 0 and attempted_candidates >= max_attempts:
            break
        attempted_candidates += 1
        attempt_started = perf_counter()
        yielded_any = False
        streamed_parts: list[str] = []
        try:
            attempt_timeout_ms = _resolve_pool_attempt_timeout_ms(
                provider_name=provider_token,
                api_key=str(candidate.get("api_key") or "").strip(),
                timeout_ms=int(timeout_ms),
                scene_type=normalized_scene,
                cognitive_tier=normalized_tier,
            )
            async for chunk in astream_llm_chunks(
                provider_name=provider_token,
                model_id=model_token,
                api_key=str(candidate.get("api_key") or "").strip(),
                timeout_ms=attempt_timeout_ms,
                messages=messages,
                gen_params=gen_params,
                base_url_override=str(candidate.get("base_url_override") or "").strip() or None,
            ):
                yielded_any = True
                streamed_parts.append(str(chunk))
                yield chunk
            latency_ms = int(round((perf_counter() - attempt_started) * 1000))
            if not yielded_any:
                raise RuntimeError("empty llm response")
            attempt_rows.append(
                ModelAttempt(
                    model=model_token,
                    provider=provider_token,
                    status="OK",
                    latency_ms=latency_ms,
                )
            )
            _log_llm_pool_call(
                request_id=request_id,
                scene_type=normalized_scene,
                cognitive_tier=normalized_tier,
                attempts=list(attempt_rows),
                final_model=model_token,
                final_provider=provider_token,
                pool_exhausted=False,
                cached=False,
                total_latency_ms=int(round((perf_counter() - started_at) * 1000)),
                messages=messages,
                content="".join(streamed_parts),
            )
            return
        except Exception as exc:
            latency_ms = int(round((perf_counter() - attempt_started) * 1000))
            reason_code, cooldown_ms = _classify_pool_error(exc, scene_type=normalized_scene)
            _set_cooldown_ms(
                provider_name=provider_token,
                model_id=model_token,
                cooldown_ms=cooldown_ms,
                scene_type=normalized_scene,
            )
            attempt_rows.append(
                ModelAttempt(
                    model=model_token,
                    provider=provider_token,
                    status="FAILED_AFTER_STREAM" if yielded_any else "FAILED",
                    latency_ms=latency_ms,
                    reason_code=reason_code,
                    detail=str(exc)[:220],
                )
            )
            if yielded_any:
                _log_llm_pool_call(
                    request_id=request_id,
                    scene_type=normalized_scene,
                    cognitive_tier=normalized_tier,
                    attempts=list(attempt_rows),
                    final_model=model_token,
                    final_provider=provider_token,
                    pool_exhausted=False,
                    cached=False,
                    total_latency_ms=int(round((perf_counter() - started_at) * 1000)),
                    messages=messages,
                    content="".join(streamed_parts),
                )
                raise
            continue

    _log_llm_pool_call(
        request_id=request_id,
        scene_type=normalized_scene,
        cognitive_tier=normalized_tier,
        attempts=list(attempt_rows),
        final_model="",
        final_provider="",
        pool_exhausted=True,
        cached=False,
        total_latency_ms=int(round((perf_counter() - started_at) * 1000)),
        messages=messages,
        content="",
    )
    raise RuntimeError(LLM_POOL_EXHAUSTED)
