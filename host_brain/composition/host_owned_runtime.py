"""Host-owned runtime factories and exact app-local publication.

The remaining process composition facade may still provide backing authorities,
but the wrapper/runtime identities built here belong to one Host application
root and are never stored in ``ProcessServiceContainer``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import dataclasses
import enum
import logging
from threading import RLock
from typing import Any


Factory = Callable[..., Any]
ServiceProvider = Callable[[], Any]
GraphHandlerBindings = Callable[[], Sequence[tuple[str, Any]]]

_logger = logging.getLogger("Natsume.host_owned_runtime")


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


def build_host_routing_matrix(
    *,
    routing_matrix_factory: Factory,
    ledger_provider: ServiceProvider,
) -> Any:
    ledger = ledger_provider()

    def route_log_writer(decision: object) -> None:
        try:
            if dataclasses.is_dataclass(decision):
                raw = dataclasses.asdict(decision)
                payload = {
                    key: value.value if isinstance(value, enum.Enum) else value
                    for key, value in raw.items()
                }
            else:
                payload = {"raw": str(decision)}
            ledger.log_llm_route(row=payload)
        except Exception as exc:
            _logger.warning("路由日志写入失败: %s", exc)

    return routing_matrix_factory(route_log_writer=route_log_writer)


__all__ = [
    "HostMemoryServicePublication",
    "build_host_graph_runtime",
    "build_host_routing_matrix",
]
