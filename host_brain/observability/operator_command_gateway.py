"""Request-local runtime bridge for Operator Command domain ports.

Secure Ingress remains the authority.  This bridge exposes only the current
ASGI application state to explicitly registered command ports so they can
reach process-owned services without loopback HTTP or global writer URLs.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from typing import Any, Iterator


_RUNTIME: ContextVar[tuple[Any, str | None] | None] = ContextVar(
    "natsume_operator_command_app_state",
    default=None,
)


@contextmanager
def bind_operator_command_runtime(
    app_state: Any,
    *,
    raw_body: bytes | None = None,
) -> Iterator[None]:
    body_sha256 = (
        hashlib.sha256(bytes(raw_body)).hexdigest()
        if raw_body is not None
        else None
    )
    token = _RUNTIME.set((app_state, body_sha256))
    try:
        yield
    finally:
        _RUNTIME.reset(token)


def operator_runtime_value(name: str) -> Any | None:
    runtime = _RUNTIME.get()
    if runtime is None:
        return None
    state, body_sha256 = runtime
    if str(name) == "command_body_sha256":
        return body_sha256
    return getattr(state, str(name), None) if state is not None else None


__all__ = ["bind_operator_command_runtime", "operator_runtime_value"]
