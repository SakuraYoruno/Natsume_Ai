"""LLM Provider 工厂模块。

从 fastapi_app.py 拆分，负责构建 LangChain provider 函数及 Gemini 重试逻辑。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Mapping

from host_brain.app.chat_runtime_input import (
    get_chat_runtime_render_target_lang,
    get_chat_runtime_user_message,
)
from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.integrations.llm_gateway import (
    PROVIDER_CODEX,
    PROVIDER_DEEPSEEK,
    PROVIDER_GEMINI,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
)
from host_brain.app.prompt_builder import (
    CHAT_DIRECT_NODE_ID,
    CHAT_RENDER_NODE_ID,
    coerce_gateway_payload_json_text,
    extract_langchain_content_text,
    render_host_generation_prompt,
    render_node_generation_prompt,
    repair_utf8_latin1_mojibake,
    _truncate_for_prompt,
)

_logger = logging.getLogger("Natsume.provider_factory")


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def _build_host_openai_system_prompt(node_id: str | None) -> str:
    templates = _load_template_mapping("provider_factory.host_openai.prompt_templates")
    resolved_node_id = str(node_id or "").strip()
    if resolved_node_id:
        return _mapping_text(
            templates,
            "system_prompt_with_node",
            node_id=resolved_node_id,
        )
    return _mapping_text(
        templates,
        "system_prompt_default",
    )


def _build_host_mock_reply_text(node_id: str | None, runtime_payload: dict[str, Any]) -> str:
    templates = _load_template_mapping("provider_factory.host_mock.reply_templates")
    resolved_node_id = str(node_id or "").strip()
    user_message = (
        _truncate_for_prompt(get_chat_runtime_user_message(runtime_payload), limit=120)
        or _mapping_text(templates, "fallback_user_message")
    )
    if resolved_node_id in {CHAT_RENDER_NODE_ID, CHAT_DIRECT_NODE_ID}:
        render_target_lang = get_chat_runtime_render_target_lang(runtime_payload)
        if render_target_lang == "zh":
            return _mapping_text(
                templates,
                "zh_render_reply",
                user_message=user_message,
            )
        return _mapping_text(
            templates,
            "ja_render_reply",
            user_message=user_message,
        )
    if resolved_node_id in {"safety_verifier", "consistency_verifier"}:
        return _mapping_text(
            templates,
            "verifier_pass_reply",
        )
    return _mapping_text(
        templates,
        "generic_reply",
        user_message=user_message,
    )

_GEMINI_PRIMARY_RETRY_NODE_ID = "core_reasoner_primary"
_GEMINI_PRIMARY_RETRY_MODEL_IDS = frozenset({"gemini-2.5-pro"})
_GEMINI_PRIMARY_FALLBACK_MODEL_ID = "gemini-3.1-pro-preview"
_GEMINI_PRIMARY_RETRYABLE_MARKERS = (
    "503",
    "deadline exceeded",
    "service unavailable",
    "service_unavailable",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "unavailable",
)
_GEMINI_PRIMARY_NON_RETRYABLE_MARKERS = (
    "401",
    "403",
    "api key",
    "api_key",
    "authentication",
    "billing",
    "permission",
    "quota",
    "resource_exhausted",
    "unauthorized",
)
_GEMINI_PRIMARY_FALLBACK_BLOCKED_MARKERS = (
    "401",
    "403",
    "api key",
    "api_key",
    "authentication",
    "permission",
    "unauthorized",
)
_GEMINI_PRIMARY_FALLBACK_TRIGGER_MARKERS = (
    "429",
    "quota",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    * _GEMINI_PRIMARY_RETRYABLE_MARKERS,
)


def resolve_host_llm_model_id(provider_name: str) -> str:
    provider = str(provider_name or "").strip().lower()
    override_model = str(os.environ.get("HOST_LLM_MODEL_ID") or "").strip()
    if override_model:
        return override_model
    if provider == PROVIDER_GEMINI:
        return str(os.environ.get("HOST_GEMINI_MODEL_ID") or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    if provider == PROVIDER_CODEX:
        return str(os.environ.get("HOST_CODEX_MODEL_ID") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    if provider == PROVIDER_OPENAI:
        return str(os.environ.get("HOST_OPENAI_MODEL_ID") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    if provider == PROVIDER_DEEPSEEK:
        return str(os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-chat").strip() or "deepseek-chat"
    return "mock-default"


def _should_retry_node_provider_failure(
    *,
    provider_name: str,
    model_id: str,
    node_id: str,
    exc: BaseException,
) -> bool:
    provider = str(provider_name or "").strip().lower()
    if provider != PROVIDER_GEMINI:
        return False
    if str(node_id or "").strip() != _GEMINI_PRIMARY_RETRY_NODE_ID:
        return False
    if str(model_id or "").strip() not in _GEMINI_PRIMARY_RETRY_MODEL_IDS:
        return False
    if isinstance(exc, TimeoutError):
        return True
    detail = str(exc or "").strip().lower()
    if not detail:
        return False
    if any(marker in detail for marker in _GEMINI_PRIMARY_NON_RETRYABLE_MARKERS):
        return False
    return any(marker in detail for marker in _GEMINI_PRIMARY_RETRYABLE_MARKERS)


def resolve_gemini_primary_fallback_model_id(primary_model_id: str) -> str | None:
    disable_fallback = str(os.environ.get("HOST_GEMINI_PRIMARY_DISABLE_MODEL_FALLBACK") or "").strip().lower()
    if disable_fallback in {"1", "true", "yes", "on"}:
        return None
    fallback_model_id = str(
        os.environ.get("HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID") or _GEMINI_PRIMARY_FALLBACK_MODEL_ID
    ).strip()
    if not fallback_model_id:
        return None
    if fallback_model_id == str(primary_model_id or "").strip():
        return None
    return fallback_model_id


def _should_try_gemini_primary_model_fallback(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    detail = str(exc or "").strip().lower()
    if not detail:
        return False
    if any(marker in detail for marker in _GEMINI_PRIMARY_FALLBACK_BLOCKED_MARKERS):
        return False
    return any(marker in detail for marker in _GEMINI_PRIMARY_FALLBACK_TRIGGER_MARKERS)


def wrap_node_provider_fn_with_retry(
    *,
    provider_fn: Callable[[dict[str, Any]], Any],
    provider_name: str,
    model_id: str,
    node_id: str,
    fallback_provider_fn: Callable[[dict[str, Any]], Any] | None = None,
    fallback_model_id: str | None = None,
) -> Callable[[dict[str, Any]], Any]:
    provider = str(provider_name or "").strip().lower()
    resolved_node_id = str(node_id or "").strip()
    resolved_model_id = str(model_id or "").strip()
    if (
        provider != PROVIDER_GEMINI
        or resolved_node_id != _GEMINI_PRIMARY_RETRY_NODE_ID
        or resolved_model_id not in _GEMINI_PRIMARY_RETRY_MODEL_IDS
    ):
        return provider_fn

    try:
        configured_attempts = int(str(os.environ.get("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS") or "2").strip())
    except ValueError:
        configured_attempts = 2
    max_attempts = max(1, min(configured_attempts, 3))
    if max_attempts <= 1 and not callable(fallback_provider_fn):
        return provider_fn

    try:
        configured_delay_ms = float(str(os.environ.get("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS") or "150").strip())
    except ValueError:
        configured_delay_ms = 150.0
    retry_delay_s = max(0.0, min(configured_delay_ms / 1000.0, 2.0))

    async def _async_provider(prompt_payload: dict[str, Any]) -> Any:
        last_exc: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await asyncio.to_thread(provider_fn, prompt_payload)
            except Exception as exc:
                last_exc = exc
                should_retry = _should_retry_node_provider_failure(
                    provider_name=provider,
                    model_id=resolved_model_id,
                    node_id=resolved_node_id,
                    exc=exc,
                )
                should_try_fallback = (
                    callable(fallback_provider_fn)
                    and _should_try_gemini_primary_model_fallback(exc)
                )
                if not should_retry or attempt >= max_attempts:
                    if should_try_fallback and fallback_provider_fn is not None:
                        resolved_fallback_model = str(fallback_model_id or "").strip() or _GEMINI_PRIMARY_FALLBACK_MODEL_ID
                        _logger.warning(
                            "Downgrading node provider call for %s from %s to %s after failure: %s",
                            resolved_node_id,
                            resolved_model_id,
                            resolved_fallback_model,
                            exc,
                        )
                        try:
                            return await asyncio.to_thread(fallback_provider_fn, prompt_payload)
                        except Exception as fallback_exc:
                            raise RuntimeError(
                                f"gemini primary model failed: {exc}; fallback model {resolved_fallback_model} failed: {fallback_exc}"
                            ) from fallback_exc
                    raise
                _logger.warning(
                    "Retrying node provider call for %s/%s after transient Gemini failure (%d/%d): %s",
                    resolved_node_id,
                    resolved_model_id,
                    attempt,
                    max_attempts,
                    exc,
                )
                if retry_delay_s > 0:
                    await asyncio.sleep(retry_delay_s)
        if last_exc is not None:
            raise last_exc
        return await asyncio.to_thread(provider_fn, prompt_payload)

    return _async_provider  # type: ignore[return-value]


def build_langchain_chat_provider_fn(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    node_id: str | None = None,
    base_url_override: str | None = None,
) -> Callable[[dict[str, Any]], Any]:
    from langchain_openai import ChatOpenAI  # noqa: PLC0415
    from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

    provider = str(provider_name or "").strip().lower()
    model_name = str(model_id or "").strip() or "default"
    base_url: str | None = str(base_url_override or "").strip() or None
    if not base_url:
        if provider == PROVIDER_DEEPSEEK:
            base_url = str(os.environ.get("HOST_DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()
        elif provider == PROVIDER_GEMINI:
            base_url = str(
                os.environ.get("HOST_GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai"
            ).strip()
        elif provider == PROVIDER_CODEX:
            base_url = str(
                os.environ.get("HOST_CODEX_BASE_URL")
                or os.environ.get("HOST_OPENAI_BASE_URL")
                or ""
            ).strip() or None
        else:
            base_url = str(os.environ.get("HOST_OPENAI_BASE_URL") or "").strip() or None

    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        timeout=max(1.0, float(timeout_ms) / 1000.0),
        max_retries=1,
        temperature=0.4,
    )
    resolved_node_id = str(node_id or "").strip()
    system_prompt = _build_host_openai_system_prompt(resolved_node_id)

    def _provider(prompt_payload: dict[str, Any]) -> str:
        runtime_payload = prompt_payload.get("input") if isinstance(prompt_payload, dict) else None
        if not isinstance(runtime_payload, dict):
            runtime_payload = dict(prompt_payload or {}) if isinstance(prompt_payload, dict) else {}
        if resolved_node_id:
            prompt_text = render_node_generation_prompt(node_id=resolved_node_id, runtime_payload=runtime_payload)
        else:
            prompt_text = render_host_generation_prompt(runtime_payload)

        # Ref #70: 读取注入检测结果，写入 runtime_payload 供上层写入 audit_tags
        try:
            from host_brain.app.prompt_builder import get_last_injection_meta  # noqa: PLC0415
            _inj_meta = get_last_injection_meta()
            if _inj_meta.get("detected"):
                _risk = str(_inj_meta.get("risk_level") or "LOW").upper()
                runtime_payload["_injection_detected"] = True
                runtime_payload["_injection_risk"] = _risk
        except Exception:
            pass

        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt_text),
            ]
        )
        response_text = extract_langchain_content_text(getattr(response, "content", ""))
        return coerce_gateway_payload_json_text(response_text)

    return _provider


def build_host_mock_provider_for_node(node_id: str | None) -> Callable[[dict[str, Any]], Any]:
    resolved_node_id = str(node_id or "").strip()

    def _provider(prompt_payload: dict[str, Any]) -> str:
        runtime_payload = prompt_payload.get("input") if isinstance(prompt_payload, dict) else None
        if not isinstance(runtime_payload, dict):
            runtime_payload = dict(prompt_payload or {}) if isinstance(prompt_payload, dict) else {}
        text = _build_host_mock_reply_text(resolved_node_id, runtime_payload)
        return json.dumps(
            {
                "narrative": {"text": text},
                "intent_draft": {},
                "meta": {
                    "draft_only": True,
                    "source": "host_mock_provider",
                    "node_id": (resolved_node_id or "chat_model"),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    return _provider


def build_host_mock_provider() -> Callable[[dict[str, Any]], Any]:
    return build_host_mock_provider_for_node(None)


def build_host_provider_fn(
    *,
    provider_name: str,
    model_id: str,
    api_key: str | None,
    timeout_ms: int,
    base_url_override: str | None = None,
) -> Callable[[dict[str, Any]], Any]:
    provider = str(provider_name or "").strip().lower()
    key_value = str(api_key or "").strip()
    if provider in {PROVIDER_OPENAI, PROVIDER_CODEX, PROVIDER_DEEPSEEK, PROVIDER_GEMINI} and key_value:
        return build_langchain_chat_provider_fn(
            provider_name=provider,
            model_id=model_id,
            api_key=key_value,
            timeout_ms=timeout_ms,
            base_url_override=base_url_override,
        )
    return build_host_mock_provider()


def build_host_provider_fn_for_node(
    *,
    provider_name: str,
    model_id: str,
    api_key: str | None,
    timeout_ms: int,
    node_id: str,
    base_url_override: str | None = None,
) -> Callable[[dict[str, Any]], Any]:
    provider = str(provider_name or "").strip().lower()
    key_value = str(api_key or "").strip()
    if provider in {PROVIDER_OPENAI, PROVIDER_CODEX, PROVIDER_DEEPSEEK, PROVIDER_GEMINI} and key_value:
        provider_fn = build_langchain_chat_provider_fn(
            provider_name=provider,
            model_id=model_id,
            api_key=key_value,
            timeout_ms=timeout_ms,
            node_id=node_id,
            base_url_override=base_url_override,
        )
        fallback_provider_fn: Callable[[dict[str, Any]], Any] | None = None
        fallback_model_id: str | None = None
        if (
            provider == PROVIDER_GEMINI
            and str(node_id or "").strip() == _GEMINI_PRIMARY_RETRY_NODE_ID
            and str(model_id or "").strip() in _GEMINI_PRIMARY_RETRY_MODEL_IDS
        ):
            fallback_model_id = resolve_gemini_primary_fallback_model_id(str(model_id or ""))
            if fallback_model_id:
                fallback_provider_fn = build_langchain_chat_provider_fn(
                    provider_name=provider,
                    model_id=fallback_model_id,
                    api_key=key_value,
                    timeout_ms=timeout_ms,
                    node_id=node_id,
                    base_url_override=base_url_override,
                )
        return wrap_node_provider_fn_with_retry(
            provider_fn=provider_fn,
            provider_name=provider,
            model_id=model_id,
            node_id=node_id,
            fallback_provider_fn=fallback_provider_fn,
            fallback_model_id=fallback_model_id,
        )
    return build_host_mock_provider_for_node(node_id)
