from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.composition.host_owned_runtime import (
    HostMemoryServicePublication,
    build_host_graph_runtime,
)


def test_host_memory_publication_is_exact_and_fail_closed() -> None:
    publication = HostMemoryServicePublication()
    first = SimpleNamespace(service=object())
    second = SimpleNamespace(service=object())

    with pytest.raises(RuntimeError, match="LIFE_MEMORY_SERVICE_NOT_COMPOSED"):
        publication.require_service()
    with pytest.raises(TypeError, match="must expose .service"):
        publication.publish(SimpleNamespace())

    publication.publish(first)
    publication.publish(first)
    assert publication.require_service() is first.service
    with pytest.raises(RuntimeError, match="HOST_MEMORY_SERVICE_ALREADY_PUBLISHED"):
        publication.publish(second)
    with pytest.raises(
        RuntimeError,
        match="HOST_MEMORY_SERVICE_PUBLICATION_MISMATCH",
    ):
        publication.withdraw(second)
    assert publication.require_service() is first.service

    publication.withdraw(first)
    publication.withdraw(first)
    with pytest.raises(RuntimeError, match="LIFE_MEMORY_SERVICE_NOT_COMPOSED"):
        publication.require_service()


def test_host_graph_runtime_binds_ledger_timeout_and_handlers() -> None:
    ledger = object()
    handler = object()
    captured: dict[str, object] = {}

    class Runtime:
        def __init__(self) -> None:
            self.handlers: list[tuple[str, object]] = []

        def register_node_handler(self, handler_id: str, value: object) -> None:
            self.handlers.append((handler_id, value))

    def factory(**kwargs):
        captured.update(kwargs)
        return Runtime()

    runtime = build_host_graph_runtime(
        graph_runtime_factory=factory,
        graph_handler_bindings=lambda: (("handler.one", handler),),
        ledger_provider=lambda: ledger,
    )

    assert captured == {"ledger": ledger, "default_timeout_ms": 5_000}
    assert runtime.handlers == [("handler.one", handler)]
