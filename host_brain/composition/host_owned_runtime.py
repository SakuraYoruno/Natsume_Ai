"""Host-owned runtime factories and exact app-local publication.

The wrapper/runtime identities built here belong to one Host application root;
they are not process-global service-locator state.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from threading import RLock
from typing import Any


Factory = Callable[..., Any]
ServiceProvider = Callable[[], Any]
GraphHandlerBindings = Callable[[], Sequence[tuple[str, Any]]]


class HostMemoryServicePublication:
    """Publish one Memory container inside one Host application root.

    Replacement and withdrawal are exact-identity operations.  This prevents a
    stale/overlapping lifespan from clearing another Host epoch's Memory
    service and removes the former process-global publication slot.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._container: Any | None = None

    def publish(self, container: Any) -> None:
        if container is None or getattr(container, "service", None) is None:
            raise TypeError("memory service container must expose .service")
        with self._lock:
            current = self._container
            if current is not None and current is not container:
                raise RuntimeError("HOST_MEMORY_SERVICE_ALREADY_PUBLISHED")
            self._container = container

    def withdraw(self, expected_container: Any) -> None:
        with self._lock:
            current = self._container
            if current is None:
                return
            if current is not expected_container:
                raise RuntimeError("HOST_MEMORY_SERVICE_PUBLICATION_MISMATCH")
            self._container = None

    def require_service(self) -> Any:
        with self._lock:
            container = self._container
        if container is None:
            raise RuntimeError("LIFE_MEMORY_SERVICE_NOT_COMPOSED")
        service = getattr(container, "service", None)
        if service is None:
            raise RuntimeError("LIFE_MEMORY_SERVICE_NOT_COMPOSED")
        return service


def build_host_graph_runtime(
    *,
    graph_runtime_factory: Factory,
    graph_handler_bindings: GraphHandlerBindings,
    ledger_provider: ServiceProvider,
) -> Any:
    runtime = graph_runtime_factory(
        ledger=ledger_provider(),
        default_timeout_ms=5_000,
    )
    for handler_id, handler in graph_handler_bindings():
        runtime.register_node_handler(handler_id, handler)
    return runtime


__all__ = [
    "HostMemoryServicePublication",
    "build_host_graph_runtime",
]
