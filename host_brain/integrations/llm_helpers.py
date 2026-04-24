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
from threading import Lock, Thread
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable, Mapping

from host_brain.core.errors import LLM_POOL_EXHAUSTED
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
    "A": "gemini:gemini-2.5-pro,gemini:gemini-2.5-flash-lite,minimax:MiniMax-M2.7-Pro,minimax:MiniMax-M2.7,minimax:MiniMax-M2.6",
    "B": "minimax:M2-her,gemini:gemini-2.5-pro,gemini:gemini-2.5-flash-lite,minimax:MiniMax-M2.7-Pro,minimax:MiniMax-M2.7",
    "C": "gemini:gemini-2.5-flash-lite,minimax:MiniMax-abab6.5s,minimax:MiniMax-M2.7,gemini:gemini-2.5-pro",
    "D": "minimax:MiniMax-M2.7,minimax:MiniMax-abab6.5s,gemini:gemini-2.5-flash-lite,gemini:gemini-2.5-pro",
}
_POOL_COOLDOWN_LOCK = Lock()
_POOL_COOLDOWN_UNTIL_MS: dict[str, int] = {}
_POOL_CACHE_LOCK = Lock()
_POOL_COMPLETION_CACHE: dict[str, tuple[int, PoolCompleteResult]] = {}
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", flags=re.DOTALL | re.IGNORECASE)
_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "too many requests", "resource_exhausted", "quota")
_UNAVAILABLE_MARKERS = (
    "timeout",
    "timed out",
    "service unavailable",
    "temporarily unavailable",
    "connection error",
    "network error",
    "unavailable",
)
_KEY_FAIL_MARKERS = ("401", "403", "api key", "api_key", "authentication", "unauthorized", "permission")

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator


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


def _validate_pool_response(*, content: str, response_format: Any) -> str:
    text = str(content or "").strip()
    if not text:
        raise RuntimeError("empty llm response")
    normalized_format = _normalize_response_format(response_format)
    if normalized_format != "json_object":
        return text
    parsed = _extract_first_json_object(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("pool response is not a valid json object")
    return json.dumps(dict(parsed), ensure_ascii=False, sort_keys=True)


def _classify_pool_error(exc: BaseException) -> tuple[str, int]:
    detail = str(exc or "").strip().lower()
    if isinstance(exc, TimeoutError):
        return "POOL_TIMEOUT", _parse_int_env(
            "HOST_LLM_POOL_TIMEOUT_COOLDOWN_MS",
            default=30_000,
            minimum=0,
            maximum=10 * 60 * 1000,
        )
    if "empty llm response" in detail or "response is not a valid json object" in detail:
        return "POOL_FORMAT_INVALID", 0
    if any(marker in detail for marker in _KEY_FAIL_MARKERS):
        return "POOL_AUTH_FAILED", 0
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
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None,
    response_format: Any,
) -> str:
    payload = {
        "scene_type": str(scene_type or "").strip().upper(),
        "cognitive_tier": str(cognitive_tier or "").strip().upper(),
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
        PROVIDER_CODEX: "CODEX_API_KEY",
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
    return value or None


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
        return _normalize_openai_compat_base_url(str(os.environ.get("HOST_OPENAI_BASE_URL") or "").strip())
    return None


def _is_minimax_standard_model(model_id: str | None) -> bool:
    return str(model_id or "").strip().lower() == "m2-her"


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


def _build_minimax_standard_payload(
    *,
    model_id: str,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None,
    stream: bool,
) -> dict[str, Any]:
    params = dict(gen_params or {})
    temperature = float(params.get("temperature", 0.4))
    top_p = float(params.get("top_p", 0.9))
    max_tokens = 1024
    for token_key in ("max_completion_tokens", "max_output_tokens", "max_tokens"):
        raw_value = params.get(token_key)
        try:
            parsed = int(raw_value) if raw_value is not None else 0
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            max_tokens = parsed
            break
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
    return {
        "model": str(model_id or "").strip() or "M2-her",
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
    try:
        from host_brain.app.prompt_builder import extract_langchain_content_text  # noqa: PLC0415

        return str(extract_langchain_content_text(content) or "").strip()
    except Exception:
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


def invoke_llm_text(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    messages: list[Mapping[str, Any]],
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> str:
    if str(provider_name or "").strip().lower() == PROVIDER_MOCK:
        return _default_mock_provider({})
    if str(provider_name or "").strip().lower() == PROVIDER_DISABLED:
        raise RuntimeError("provider disabled")
    if not str(api_key or "").strip():
        raise RuntimeError(f"missing api key for provider={provider_name}")
    if str(provider_name or "").strip().lower() == PROVIDER_MINIMAX and _is_minimax_standard_model(model_id):
        return _invoke_minimax_standard_text(
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=messages,
            gen_params=gen_params,
            base_url_override=base_url_override,
        )

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        **_build_chat_openai_kwargs(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            gen_params=gen_params,
            base_url_override=base_url_override,
            streaming=False,
        )
    )
    response = llm.invoke(_build_langchain_messages(messages))
    return _extract_text_from_response_content(getattr(response, "content", ""))


def _resolve_pool_candidates(
    *,
    cognitive_tier: str,
    provider_name: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
    base_url_override: str | None = None,
) -> list[dict[str, Any]]:
    normalized_tier = str(cognitive_tier or "B").strip().upper() or "B"
    spec_env_name = f"HOST_LLM_POOL_TIER_{normalized_tier}"
    raw_spec = str(os.environ.get(spec_env_name) or _POOL_DEFAULT_SPECS.get(normalized_tier) or "").strip()
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
        dedupe_key = f"{provider_token}::{model_token}"
        if dedupe_key in seen:
            return
        resolved_key = key_value or _load_api_key_from_env(provider_token)
        if provider_token not in {PROVIDER_MOCK, PROVIDER_DISABLED} and not resolved_key:
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

    if explicit_provider and explicit_model:
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
    ts_ms: int | None = None,
) -> None:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

        ledger = get_ledger()
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
                    "ts_ms": int(ts_ms if ts_ms is not None else _now_ms()),
                }
            )
        if hasattr(ledger, "log_llm_node_call"):
            normalized_scene = str(scene_type or "").strip().upper() or "CHAT"
            node_suffix = normalized_scene.lower()
            final_status = "DEGRADED" if pool_exhausted else "OK"
            final_reason = "LLM_POOL_EXHAUSTED" if pool_exhausted else None
            ledger.log_llm_node_call(
                {
                    "request_id": str(request_id or "").strip(),
                    "node_id": f"llm_pool:{node_suffix}",
                    "provider": str(final_provider or "").strip().lower(),
                    "model_id": str(final_model or "").strip(),
                    "status": final_status,
                    "reason_code": final_reason,
                    "latency_ms": int(total_latency_ms),
                    "input_digest": f"scene={normalized_scene};tier={str(cognitive_tier or '').strip().upper() or 'B'};attempts={len(attempts_payload)}",
                    "output_digest": attempts_json[:4000],
                    "ts_ms": int(ts_ms if ts_ms is not None else _now_ms()),
                }
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

    cache_key = _make_pool_cache_key(
        scene_type=normalized_scene,
        cognitive_tier=normalized_tier,
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
        )
        return cached_result

    for candidate in _resolve_pool_candidates(
        cognitive_tier=normalized_tier,
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

        attempt_started = perf_counter()
        try:
            raw_text = invoke_llm_text(
                provider_name=provider_token,
                model_id=model_token,
                api_key=str(candidate.get("api_key") or "").strip(),
                timeout_ms=int(timeout_ms),
                messages=messages,
                gen_params=gen_params,
                base_url_override=str(candidate.get("base_url_override") or "").strip() or None,
            )
            content = _validate_pool_response(content=raw_text, response_format=response_format)
            latency_ms = int(round((perf_counter() - attempt_started) * 1000))
            attempt_rows.append(
                ModelAttempt(
                    model=model_token,
                    provider=provider_token,
                    status="OK",
                    latency_ms=latency_ms,
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
            )
            return result
        except Exception as exc:
            latency_ms = int(round((perf_counter() - attempt_started) * 1000))
            reason_code, cooldown_ms = _classify_pool_error(exc)
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
                    detail=str(exc)[:220],
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
    if str(system_prompt or "").strip():
        messages.append({"role": "system", "content": str(system_prompt or "").strip()})
    messages.append({"role": "user", "content": str(prompt or "").strip()})
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

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        **_build_chat_openai_kwargs(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            gen_params=gen_params,
            base_url_override=base_url_override,
            streaming=True,
        )
    )

    for chunk in llm.stream(_build_langchain_messages(messages)):
        text = _extract_text_from_response_content(getattr(chunk, "content", "")) or ""
        if text:
            yield text


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

    for candidate in _resolve_pool_candidates(
        cognitive_tier=normalized_tier,
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

        attempt_started = perf_counter()
        yielded_any = False
        try:
            async for chunk in astream_llm_chunks(
                provider_name=provider_token,
                model_id=model_token,
                api_key=str(candidate.get("api_key") or "").strip(),
                timeout_ms=int(timeout_ms),
                messages=messages,
                gen_params=gen_params,
                base_url_override=str(candidate.get("base_url_override") or "").strip() or None,
            ):
                yielded_any = True
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
            )
            return
        except Exception as exc:
            latency_ms = int(round((perf_counter() - attempt_started) * 1000))
            reason_code, cooldown_ms = _classify_pool_error(exc)
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
    )
    raise RuntimeError(LLM_POOL_EXHAUSTED)
