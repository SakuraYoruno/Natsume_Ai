"""Only platform-owned services a Domain Pack may receive."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class DomainModelAttemptPort(Protocol):
    def complete_sync(self, request: Any) -> Any: ...


class DomainCapabilityBrokerPort(Protocol):
    def execute(self, request: Any) -> Any: ...


class DomainTaskPort(Protocol):
    def execute(self, task_id: str, payload: Mapping[str, Any]) -> Any: ...


class DomainEventPort(Protocol):
    def append(self, event_type: str, payload: Mapping[str, Any]) -> str: ...


class DomainArtifactPort(Protocol):
    def put(self, artifact_name: str, content: bytes, *, media_type: str) -> str: ...


@dataclass(frozen=True, slots=True)
class DomainPackPlatformPorts:
    """Narrow injection surface; packs cannot acquire global writers or clocks."""

    model_attempt_service: DomainModelAttemptPort | None = None
    capability_broker: DomainCapabilityBrokerPort | None = None
    task_port: DomainTaskPort | None = None
    event_port: DomainEventPort | None = None
    artifact_port: DomainArtifactPort | None = None

    def require(self, port_name: str) -> Any:
        value = getattr(self, str(port_name), None)
        if value is None:
            raise RuntimeError(f"DOMAIN_PACK_PORT_MISSING:{port_name}")
        return value


__all__ = [
    "DomainArtifactPort", "DomainCapabilityBrokerPort", "DomainEventPort",
    "DomainModelAttemptPort", "DomainPackPlatformPorts", "DomainTaskPort",
]
