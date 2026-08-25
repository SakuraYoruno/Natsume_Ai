"""低层 provider 传输、API Key 解析与 SSE 流式适配。"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from threading import Lock, local
from typing import Any, Mapping

from host_brain.integrations.codex_cli_provider import (
    codex_cli_lm_allowed_for_background,
    invoke_codex_cli_text,
)
from host_brain.core.runtime_helpers import parse_bool_env as _parse_bool_env

PROVIDER_OPENAI = "openai"
PROVIDER_CODEX = "codex"
PROVIDER_GEMINI = "gemini"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_MINIMAX = "minimax"
PROVIDER_MOCK = "mock"
PROVIDER_DISABLED = "disabled"

_logger = logging.getLogger("Natsume.integrations.llm_helpers")
_PROVIDER_USAGE_LOCAL = local()
_OPENAI_COMPAT_SESSION_LOCK = Lock()
_OPENAI_COMPAT_SESSION: Any | None = None

def _set_last_provider_usage(usage: Any) -> None:
    setattr(_PROVIDER_USAGE_LOCAL, "last_usage", dict(usage) if isinstance(usage, Mapping) else None)


def _pop_last_provider_usage() -> dict[str, Any] | None:
    usage = getattr(_PROVIDER_USAGE_LOCAL, "last_usage", None)
    setattr(_PROVIDER_USAGE_LOCAL, "last_usage", None)
    return dict(usage) if isinstance(usage, Mapping) else None


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


def _openai_compat_base_url_env() -> str:
    return str(
        os.environ.get("HOST_OPENAI_BASE_URL")
        or os.environ.get("HOST_CHAT_LOCAL_LLM_BASE_URL")
        or os.environ.get("HOST_CHAT_HOST_14B_BASE_URL")
        or ""
    ).strip()


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
    if _parse_bool_env("HOST_LLM_OPENAI_COMPAT_KEEPALIVE_ENABLED", default=True):
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
    if (
        str(provider_name or "").strip().lower() == PROVIDER_DEEPSEEK
        and str(model_id or "").strip().lower().startswith("deepseek-v4-flash")
    ):
        thinking_mode = "disabled"
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
    is_gemini_36_flash = normalized_model_id.startswith("gemini-3.6-flash")
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
            # looks like a false empty response to the provider response contract.
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
        "streaming": streaming,
    }
    if is_gemini_36_flash:
        # Gemini 3.6 Flash ignores the legacy sampling controls today and the
        # provider contract reserves the right to reject them.  Realtime chat
        # instead carries the typed thinking hint mapped by the production
        # adapter below.
        thinking_level = str(params.get("thinking_level") or "").strip().lower()
        if thinking_level:
            if thinking_level not in {"minimal", "low", "medium", "high"}:
                raise ValueError(
                    "Gemini 3.6 thinking_level must be minimal, low, medium, or high"
                )
            llm_kwargs["thinking_level"] = thinking_level
    else:
        llm_kwargs["temperature"] = temperature
        llm_kwargs["top_p"] = top_p
    if max_tokens is not None:
        llm_kwargs["max_tokens"] = max_tokens
    response_format = params.get("response_format")
    normalized_response_format = _normalize_response_format(response_format)
    if (
        normalized_response_format is None
        and isinstance(response_format, Mapping)
        and str(response_format.get("type") or "").strip().lower() == "json_schema"
    ):
        normalized_response_format = "json_schema"
    if normalized_response_format in {"json_object", "json_schema"}:
        # ChatGoogleGenerativeAI does not consume the OpenAI-compatible
        # ``response_format`` nested inside generation parameters.  Bind the
        # equivalent native Gemini controls so a server-owned JSON contract is
        # enforced by the provider instead of relying on prompt wording.
        llm_kwargs["response_mime_type"] = "application/json"
    if normalized_response_format == "json_schema" and isinstance(
        response_format,
        Mapping,
    ):
        raw_schema = response_format.get("json_schema")
        if isinstance(raw_schema, Mapping):
            nested_schema = raw_schema.get("schema")
            schema = nested_schema if isinstance(nested_schema, Mapping) else raw_schema
            if schema:
                llm_kwargs["response_schema"] = dict(schema)
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
    """按提供方适配真实文本调用；此处只分发协议，凭据和治理策略由上层管理。"""
    provider = str(provider_name or "").strip().lower()
    if provider == PROVIDER_MOCK:
        return _default_mock_provider({})
    if provider == PROVIDER_DISABLED:
        raise RuntimeError("provider disabled")
    if provider == PROVIDER_CODEX and not str(api_key or "").strip() and codex_cli_lm_allowed_for_background():
        # 仅在后台策略明确允许时走 Codex CLI，避免无密钥时静默扩大本地执行面。
        return invoke_codex_cli_text(
            model_id=model_id,
            messages=messages,
            timeout_ms=timeout_ms,
            response_format=response_format,
            feature_key=feature_key,
        )
    if not str(api_key or "").strip():
        # 除受策略认可的 CLI 路径外，缺少凭据必须明确失败，不尝试匿名远程调用。
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
