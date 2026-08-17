"""模型供应商适配器的注入端口与进程内注册表。

此层不绑定任何具体 SDK。``CallableProviderAdapter`` 仅把既有传输函数接入统一
契约，不复制路由或重试逻辑；每次适配器方法调用恰好代表一次传输尝试，供上层
统一计量、限额和错误处理。
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, AsyncIterable, Awaitable, Callable, Iterable, Mapping

from host_brain.model_runtime.contracts import (
    ProviderAdapter,
    ProviderAttemptRequest,
    ProviderResponse,
    ProviderStreamEvent,
)


class ProviderAdapterError(RuntimeError):
    """供应商返回值或适配协议不能被安全归一化时的基础错误。"""
    pass


class UnknownProviderAdapterError(ProviderAdapterError):
    """路由请求了未注册的供应商身份，必须失败而不能静默回退。"""
    pass


class DuplicateProviderAdapterError(ProviderAdapterError):
    """同一适配器身份发生非显式覆盖时抛出，保护进程组合结果。"""
    pass


CompleteCallable = Callable[
    [ProviderAttemptRequest],
    ProviderResponse | str | Mapping[str, Any] | Awaitable[ProviderResponse | str | Mapping[str, Any]],
]
StreamCallable = Callable[
    [ProviderAttemptRequest],
    AsyncIterable[Any] | Iterable[Any] | Awaitable[AsyncIterable[Any] | Iterable[Any]],
]
StreamResultCallable = Callable[
    [ProviderAttemptRequest],
    AsyncIterable[Any] | Iterable[Any] | Awaitable[AsyncIterable[Any] | Iterable[Any]],
]


def _coerce_response(value: Any) -> ProviderResponse:
    """把兼容传输结果收敛为 ``ProviderResponse``，保留供应商原始用量证据。"""
    if isinstance(value, ProviderResponse):
        return value
    if isinstance(value, str):
        return ProviderResponse(content=value)
    if isinstance(value, Mapping):
        payload = dict(value)
        content = payload.get("content")
        if content is None:
            content = payload.get("text", payload.get("output", ""))
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
        return ProviderResponse(
            content=str(content or ""),
            finish_reason=(
                str(payload.get("finish_reason")) if payload.get("finish_reason") is not None else None
            ),
            usage=dict(usage),
            metadata=dict(metadata),
        )
    raise ProviderAdapterError(
        f"provider callable returned {type(value).__name__}, expected ProviderResponse, str or mapping"
    )


def _coerce_chunk(value: Any) -> str:
    """从旧式流分片提取展示文本；此函数不生成可用于计费的用量信息。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        content = value.get("content", value.get("text", value.get("delta", "")))
        return str(content or "")
    content = getattr(value, "content", None)
    if content is not None:
        return str(content)
    return str(value or "")


def _coerce_stream_result_event(value: Any) -> ProviderStreamEvent:
    """归一化带明确用量凭据的流事件，绝不把裸分片当作最终结果。

    纯字符串只兼容旧 ``stream`` 接口；受治理流必须提供包含供应商原生 usage 的
    终结响应，不能根据文本长度猜测计费或预算消耗。
    """

    if isinstance(value, ProviderStreamEvent):
        return value
    if not isinstance(value, Mapping):
        raise ProviderAdapterError(
            "stream result callable must emit ProviderStreamEvent or mapping"
        )
    payload = dict(value)
    kind = payload.get("kind", payload.get("event_type"))
    if str(kind or "").strip().upper() == "DELTA":
        return ProviderStreamEvent.delta(
            str(payload.get("text", payload.get("content", "")) or "")
        )
    if str(kind or "").strip().upper() == "FINAL":
        response = payload.get("response")
        if isinstance(response, ProviderResponse):
            return ProviderStreamEvent.final(response)
        if isinstance(response, Mapping):
            return ProviderStreamEvent.final(_coerce_response(response))
        raise ProviderAdapterError(
            "stream result final mapping requires a ProviderResponse response"
        )
    raise ProviderAdapterError("stream result event kind must be DELTA or FINAL")


class CallableProviderAdapter:
    """把注入的单次 complete/stream 函数封装为统一供应商端口。

    ``run_sync_in_thread`` 只用于避免遗留同步传输阻塞事件循环；它不改变一次调用
    对应一次传输尝试的计量语义。
    """

    def __init__(
        self,
        adapter_id: str,
        *,
        complete_fn: CompleteCallable,
        stream_fn: StreamCallable | None = None,
        stream_result_fn: StreamResultCallable | None = None,
        run_sync_in_thread: bool = False,
    ) -> None:
        normalized_id = str(adapter_id or "").strip()
        if not normalized_id:
            raise ValueError("adapter_id must not be empty")
        if not callable(complete_fn):
            raise TypeError("complete_fn must be callable")
        if stream_fn is not None and not callable(stream_fn):
            raise TypeError("stream_fn must be callable")
        if stream_result_fn is not None and not callable(stream_result_fn):
            raise TypeError("stream_result_fn must be callable")
        self._adapter_id = normalized_id
        self._complete_fn = complete_fn
        self._stream_fn = stream_fn
        self._stream_result_fn = stream_result_fn
        self._run_sync_in_thread = bool(run_sync_in_thread)

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    async def complete(self, request: ProviderAttemptRequest) -> ProviderResponse:
        """执行一次完整请求，必要时隔离同步 I/O，并在边界验证返回形状。"""
        if self._run_sync_in_thread and not inspect.iscoroutinefunction(self._complete_fn):
            result: Any = await asyncio.to_thread(self._complete_fn, request)
        else:
            result = self._complete_fn(request)
            if inspect.isawaitable(result):
                result = await result
        return _coerce_response(result)

    def stream(self, request: ProviderAttemptRequest):
        """暴露兼容文本流；没有流实现时退化为一次 complete 的正文。"""
        return self._stream(request)

    @property
    def supports_canonical_stream_result(self) -> bool:
        """Whether this injected transport ends with canonical usage evidence."""

        return self._stream_result_fn is not None

    def stream_with_result(self, request: ProviderAttemptRequest):
        """暴露受治理流，调用方据此取得终结的供应商用量凭据。"""
        return self._stream_with_result(request)

    async def _stream(self, request: ProviderAttemptRequest):
        if self._stream_fn is None:
            response = await self.complete(request)
            if response.content:
                yield response.content
            return

        if self._run_sync_in_thread and not inspect.iscoroutinefunction(self._stream_fn):
            source: Any = await asyncio.to_thread(self._stream_fn, request)
        else:
            source = self._stream_fn(request)
            if inspect.isawaitable(source):
                source = await source

        if isinstance(source, AsyncIterable):
            async for raw_chunk in source:
                chunk = _coerce_chunk(raw_chunk)
                if chunk:
                    yield chunk
            return
        if isinstance(source, Iterable) and not isinstance(source, (str, bytes, bytearray)):
            for raw_chunk in source:
                chunk = _coerce_chunk(raw_chunk)
                if chunk:
                    yield chunk
            return
        raise ProviderAdapterError(
            f"stream callable returned {type(source).__name__}, expected iterable or async iterable"
        )

    async def _stream_with_result(self, request: ProviderAttemptRequest):
        if self._stream_result_fn is None:
            raise ProviderAdapterError(
                "provider stream has no final canonical usage response"
            )
        if self._run_sync_in_thread and not inspect.iscoroutinefunction(self._stream_result_fn):
            source: Any = await asyncio.to_thread(self._stream_result_fn, request)
        else:
            source = self._stream_result_fn(request)
            if inspect.isawaitable(source):
                source = await source
        if isinstance(source, AsyncIterable):
            async for raw_event in source:
                yield _coerce_stream_result_event(raw_event)
            return
        if isinstance(source, Iterable) and not isinstance(source, (str, bytes, bytearray)):
            for raw_event in source:
                yield _coerce_stream_result_event(raw_event)
            return
        raise ProviderAdapterError(
            "stream result callable returned "
            f"{type(source).__name__}, expected iterable or async iterable"
        )


class ProviderAdapterRegistry:
    """进程组合期的适配器注册表；重复身份默认拒绝，读写用锁保护。

    注册表只保存能力对象，不决定路由或重试；显式 ``replace_existing`` 才允许测试
    或受控重装替换已有身份，避免并发启动时悄悄劫持供应商选择。
    """

    def __init__(self, adapters: Iterable[ProviderAdapter] = ()) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        self._lock = threading.RLock()
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter, *, replace_existing: bool = False) -> None:
        """校验最小端口能力后原子注册适配器。"""
        adapter_id = str(getattr(adapter, "adapter_id", "") or "").strip()
        if not adapter_id:
            raise ProviderAdapterError("provider adapter must expose a non-empty adapter_id")
        if not callable(getattr(adapter, "complete", None)):
            raise ProviderAdapterError(f"provider adapter {adapter_id} is missing complete()")
        if not callable(getattr(adapter, "stream", None)):
            raise ProviderAdapterError(f"provider adapter {adapter_id} is missing stream()")
        with self._lock:
            if adapter_id in self._adapters and not replace_existing:
                raise DuplicateProviderAdapterError(f"provider adapter already registered: {adapter_id}")
            self._adapters[adapter_id] = adapter

    def get(self, adapter_id: str) -> ProviderAdapter:
        """按身份读取适配器；未知身份显式失败，防止错误路由到默认供应商。"""
        normalized_id = str(adapter_id or "").strip()
        with self._lock:
            adapter = self._adapters.get(normalized_id)
        if adapter is None:
            raise UnknownProviderAdapterError(f"unknown provider adapter: {normalized_id}")
        return adapter

    def ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._adapters))

    def __contains__(self, adapter_id: object) -> bool:
        if not isinstance(adapter_id, str):
            return False
        with self._lock:
            return adapter_id.strip() in self._adapters


__all__ = [
    "CallableProviderAdapter",
    "CompleteCallable",
    "DuplicateProviderAdapterError",
    "ProviderAdapterError",
    "ProviderAdapterRegistry",
    "StreamResultCallable",
    "StreamCallable",
    "UnknownProviderAdapterError",
]
