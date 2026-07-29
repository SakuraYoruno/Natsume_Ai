"""Provider adapter registry and injectable compatibility ports.

No concrete provider SDK belongs in this module yet.  ``CallableProviderAdapter``
lets the cutover inject existing transports without copying their routing or
retry behavior.  One adapter method invocation always represents one transport
attempt.
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
    ProviderStreamEventKind,
)


class ProviderAdapterError(RuntimeError):
    pass


class UnknownProviderAdapterError(ProviderAdapterError):
    pass


class DuplicateProviderAdapterError(ProviderAdapterError):
    pass


CompleteCallable = Callable[
    [ProviderAttemptRequest],
    ProviderResponse | str | Mapping[str, Any] | Awaitable[ProviderResponse | str | Mapping[str, Any]],
]
StreamCallable = Callable[
    [ProviderAttemptRequest],
    AsyncIterable[Any] | Iterable[Any] | Awaitable[AsyncIterable[Any] | Iterable[Any]],
]
StreamEventsCallable = Callable[
    [ProviderAttemptRequest],
    AsyncIterable[Any] | Iterable[Any] | Awaitable[AsyncIterable[Any] | Iterable[Any]],
]


def _coerce_response(value: Any) -> ProviderResponse:
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


def _coerce_stream_event(value: Any) -> ProviderStreamEvent:
    """Normalize adapter-owned transport frames without inventing usage."""

    if isinstance(value, ProviderStreamEvent):
        return value
    if isinstance(value, Mapping):
        payload = dict(value)
        kind = str(payload.get("kind") or payload.get("event") or "delta").strip().lower()
        if kind in {"final", "terminal", "complete", "completed"}:
            response_value = payload.get("response")
            if response_value is None:
                response_value = {
                    "content": payload.get("content", payload.get("text", "")),
                    "finish_reason": payload.get("finish_reason"),
                    "usage": payload.get("usage"),
                    "metadata": payload.get("metadata"),
                }
            return ProviderStreamEvent(
                kind=ProviderStreamEventKind.FINAL,
                response=_coerce_response(response_value),
            )
        if kind not in {"delta", "chunk", "text"}:
            raise ProviderAdapterError(f"unsupported provider stream event kind: {kind}")
        return ProviderStreamEvent(
            kind=ProviderStreamEventKind.DELTA,
            text=_coerce_chunk(payload),
        )
    return ProviderStreamEvent(
        kind=ProviderStreamEventKind.DELTA,
        text=_coerce_chunk(value),
    )


class CallableProviderAdapter:
    """Adapter around injected single-call complete/stream functions."""

    def __init__(
        self,
        adapter_id: str,
        *,
        complete_fn: CompleteCallable,
        stream_fn: StreamCallable | None = None,
        stream_events_fn: StreamEventsCallable | None = None,
        run_sync_in_thread: bool = False,
    ) -> None:
        normalized_id = str(adapter_id or "").strip()
        if not normalized_id:
            raise ValueError("adapter_id must not be empty")
        if not callable(complete_fn):
            raise TypeError("complete_fn must be callable")
        if stream_fn is not None and not callable(stream_fn):
            raise TypeError("stream_fn must be callable")
        if stream_events_fn is not None and not callable(stream_events_fn):
            raise TypeError("stream_events_fn must be callable")
        self._adapter_id = normalized_id
        self._complete_fn = complete_fn
        self._stream_fn = stream_fn
        self._stream_events_fn = stream_events_fn
        self._run_sync_in_thread = bool(run_sync_in_thread)

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def supports_final_stream_usage(self) -> bool:
        """Whether this adapter was explicitly given a final-usage protocol."""

        return self._stream_events_fn is not None

    async def complete(self, request: ProviderAttemptRequest) -> ProviderResponse:
        if self._run_sync_in_thread and not inspect.iscoroutinefunction(self._complete_fn):
            result: Any = await asyncio.to_thread(self._complete_fn, request)
        else:
            result = self._complete_fn(request)
            if inspect.isawaitable(result):
                result = await result
        return _coerce_response(result)

    def stream(self, request: ProviderAttemptRequest):
        return self._stream(request)

    def stream_events(self, request: ProviderAttemptRequest):
        return self._stream_events(request)

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

    async def _stream_events(self, request: ProviderAttemptRequest):
        """Expose a final response only when the source explicitly provides it.

        The compatibility fallback deliberately emits a final response with no
        usage.  ``ModelAttemptService`` will therefore reject a governed call
        instead of silently converting text chunks into a fictional budget
        measurement.
        """

        if self._stream_events_fn is None:
            chunks: list[str] = []
            async for chunk in self._stream(request):
                text = str(chunk or "")
                if text:
                    chunks.append(text)
                    yield ProviderStreamEvent(
                        kind=ProviderStreamEventKind.DELTA,
                        text=text,
                    )
            yield ProviderStreamEvent(
                kind=ProviderStreamEventKind.FINAL,
                response=ProviderResponse(
                    content="".join(chunks),
                    metadata={"streamed": True, "usage_attested": False},
                ),
            )
            return

        if self._run_sync_in_thread and not inspect.iscoroutinefunction(
            self._stream_events_fn
        ):
            source: Any = await asyncio.to_thread(self._stream_events_fn, request)
        else:
            source = self._stream_events_fn(request)
            if inspect.isawaitable(source):
                source = await source

        if isinstance(source, AsyncIterable):
            async for raw_event in source:
                yield _coerce_stream_event(raw_event)
            return
        if isinstance(source, Iterable) and not isinstance(source, (str, bytes, bytearray)):
            for raw_event in source:
                yield _coerce_stream_event(raw_event)
            return
        raise ProviderAdapterError(
            "stream events callable returned "
            f"{type(source).__name__}, expected iterable or async iterable"
        )


class ProviderAdapterRegistry:
    """Process composition registry; duplicate ids fail closed."""

    def __init__(self, adapters: Iterable[ProviderAdapter] = ()) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        self._lock = threading.RLock()
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter, *, replace_existing: bool = False) -> None:
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
    "StreamCallable",
    "StreamEventsCallable",
    "UnknownProviderAdapterError",
]
