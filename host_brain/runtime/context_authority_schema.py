"""Persistence-port readiness gate for NE-03 Runtime Context authority."""

from __future__ import annotations

from typing import Any


class RuntimeContextSchemaError(RuntimeError):
    """RuntimeEventStore lacks the exact CAS and lease fencing schema."""


def assert_runtime_context_authority_schema(event_store: Any) -> None:
    inspector = getattr(
        event_store,
        "assert_runtime_context_authority_schema",
        None,
    )
    if not callable(inspector):
        raise RuntimeContextSchemaError(
            "runtime context schema readiness port is unavailable"
        )
    try:
        inspector()
    except Exception as exc:
        detail = str(getattr(exc, "detail", "") or str(exc)).strip()
        raise RuntimeContextSchemaError(
            detail or "runtime context authority schema cannot be inspected"
        ) from exc


__all__ = [
    "RuntimeContextSchemaError",
    "assert_runtime_context_authority_schema",
]
