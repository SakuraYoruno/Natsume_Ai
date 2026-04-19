"""LLM 辅助函数：API Key 加载、情感生成参数注入、SSE 流式输出、超时执行。"""

from __future__ import annotations

import json
import os
from queue import Empty, Queue
from threading import Thread
from typing import TYPE_CHECKING, Any, Callable

PROVIDER_OPENAI = "openai"
PROVIDER_CODEX = "codex"
PROVIDER_GEMINI = "gemini"
PROVIDER_DEEPSEEK = "deepseek"

ProviderFn = Callable[[dict[str, Any]], Any]

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
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_key = provider_to_env.get(str(provider_name or "").strip().lower())
    if not env_key:
        return None
    value = os.environ.get(env_key, "").strip()
    return value or None


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


def stream_llm_chunks(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    prompt_text: str,
    system_prompt: str,
    gen_params: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> "Generator[str, None, None]":
    """P0 SSE 优化：直接调用 LangChain streaming 模式，逐 token yield 文本片段。

    Args:
        provider_name: 提供商名称（openai/gemini/deepseek）
        model_id: 模型 ID
        api_key: API Key
        timeout_ms: 超时毫秒
        prompt_text: 用户侧 prompt 文本
        system_prompt: system 角色提示词
        gen_params: 可选生成参数（temperature/top_p）

    Yields:
        str: LLM 返回的文本片段（token chunk）
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    provider = str(provider_name or "").strip().lower()
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
        "base_url": base_url,
        "timeout": max(1.0, float(timeout_ms) / 1000.0),
        "max_retries": 1,
        "temperature": temperature,
        "top_p": top_p,
        "streaming": True,
    }
    if max_tokens is not None:
        llm_kwargs["max_tokens"] = max_tokens

    llm = ChatOpenAI(
        **llm_kwargs,
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt_text),
    ]
    for chunk in llm.stream(messages):
        text = getattr(chunk, "content", "") or ""
        if text:
            yield text


async def astream_llm_chunks(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    prompt_text: str,
    system_prompt: str,
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
        prompt_text: 用户侧 prompt 文本
        system_prompt: system 角色提示词
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
                prompt_text=prompt_text,
                system_prompt=system_prompt,
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
