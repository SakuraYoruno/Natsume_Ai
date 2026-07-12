"""Typed, declarative Domain Pack contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .ports import DomainPackPlatformPorts


DOMAIN_PACK_SCHEMA_VERSION = "Natsume.domain_pack.manifest.v1"
ALLOWED_PLATFORM_PORTS = frozenset({
    "model_attempt_service",
    "capability_broker",
    "task_port",
    "event_port",
    "artifact_port",
})
_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_LIFECYCLE_PHASES = frozenset({"postprocess", "delivery", "recovery"})


def _required(value: Any, field_name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{field_name} is required")
    return resolved


def _frozen_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    payload = json.loads(json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True, default=str))
    return MappingProxyType(payload)


@dataclass(frozen=True, slots=True)
class DomainLifecycleStageManifest:
    stage_id: str
    phase: str
    task_id: str
    required: bool = True
    user_visible: bool = False

    def __post_init__(self) -> None:
        stage_id = _required(self.stage_id, "stage_id")
        task_id = _required(self.task_id, "task_id")
        phase = _required(self.phase, "phase").lower()
        if not _ID_RE.fullmatch(stage_id) or not _ID_RE.fullmatch(task_id):
            raise ValueError("lifecycle stage_id and task_id must be stable lowercase identifiers")
        if phase not in _LIFECYCLE_PHASES:
            raise ValueError("lifecycle phase must be postprocess, delivery, or recovery")
        if type(self.required) is not bool or type(self.user_visible) is not bool:
            raise ValueError("lifecycle required and user_visible must be bool")
        if self.user_visible and phase != "delivery":
            raise ValueError("only delivery lifecycle stages may be user visible")
        object.__setattr__(self, "stage_id", stage_id)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "task_id", task_id)


@dataclass(frozen=True, slots=True)
class DomainCapabilityManifest:
    capability_id: str
    handler_id: str
    description: str
    risk_level: int
    allowed_task_ids: tuple[str, ...] = ()
    lifecycle_stages: tuple[DomainLifecycleStageManifest, ...] = ()

    def __post_init__(self) -> None:
        capability_id = _required(self.capability_id, "capability_id")
        handler_id = _required(self.handler_id, "handler_id")
        if not _ID_RE.fullmatch(capability_id) or not _ID_RE.fullmatch(handler_id):
            raise ValueError("capability_id and handler_id must be stable lowercase identifiers")
        if not 1 <= int(self.risk_level) <= 5:
            raise ValueError("risk_level must be in [1,5]")
        allowed_task_ids = tuple(
            dict.fromkeys(_required(item, "allowed_task_id") for item in self.allowed_task_ids)
        )
        if any(not _ID_RE.fullmatch(item) for item in allowed_task_ids):
            raise ValueError("allowed_task_ids must be stable lowercase identifiers")
        lifecycle_stages = tuple(self.lifecycle_stages or ())
        if any(not isinstance(item, DomainLifecycleStageManifest) for item in lifecycle_stages):
            raise ValueError("lifecycle_stages must contain DomainLifecycleStageManifest values")
        stage_ids = [item.stage_id for item in lifecycle_stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("lifecycle stage_ids must be unique per capability")
        undeclared_tasks = {item.task_id for item in lifecycle_stages} - set(allowed_task_ids)
        if undeclared_tasks:
            raise ValueError("lifecycle stage task_ids must be included in allowed_task_ids")
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "handler_id", handler_id)
        object.__setattr__(self, "risk_level", int(self.risk_level))
        object.__setattr__(self, "allowed_task_ids", allowed_task_ids)
        object.__setattr__(self, "lifecycle_stages", lifecycle_stages)


@dataclass(frozen=True, slots=True)
class DomainPackManifest:
    pack_id: str
    version: str
    domain: str
    capabilities: tuple[DomainCapabilityManifest, ...]
    required_ports: tuple[str, ...] = ("task_port",)
    enabled_by_default: bool = True
    schema_version: str = DOMAIN_PACK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        pack_id = _required(self.pack_id, "pack_id")
        domain = _required(self.domain, "domain")
        if not _ID_RE.fullmatch(pack_id) or not _ID_RE.fullmatch(domain):
            raise ValueError("pack_id and domain must be stable lowercase identifiers")
        if not _VERSION_RE.fullmatch(str(self.version or "")):
            raise ValueError("version must be semantic x.y.z")
        capabilities = tuple(self.capabilities or ())
        if not capabilities:
            raise ValueError("domain pack must declare capabilities")
        ids = [item.capability_id for item in capabilities]
        handlers = [item.handler_id for item in capabilities]
        if len(ids) != len(set(ids)) or len(handlers) != len(set(handlers)):
            raise ValueError("domain pack capabilities and handlers must be unique")
        required_ports = tuple(dict.fromkeys(str(item or "").strip() for item in self.required_ports))
        if not set(required_ports).issubset(ALLOWED_PLATFORM_PORTS):
            raise ValueError("domain packs may request only explicit platform ports")
        object.__setattr__(self, "pack_id", pack_id)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "required_ports", required_ports)

    @property
    def version_key(self) -> tuple[int, int, int]:
        return tuple(int(part) for part in self.version.split("."))  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class DomainPackRequest:
    request_id: str
    capability_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _required(self.request_id, "request_id"))
        object.__setattr__(self, "capability_id", _required(self.capability_id, "capability_id"))
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))


class DomainPackHandler(Protocol):
    def execute(self, request: DomainPackRequest, ports: "DomainPackPlatformPorts") -> Any: ...


@dataclass(frozen=True, slots=True)
class DomainPack:
    manifest: DomainPackManifest
    handlers: Mapping[str, DomainPackHandler]

    def __post_init__(self) -> None:
        handlers = dict(self.handlers or {})
        declared = {item.handler_id for item in self.manifest.capabilities}
        if set(handlers) != declared:
            raise ValueError("pack handlers must exactly match manifest handler_ids")
        if any(not callable(getattr(handler, "execute", None)) for handler in handlers.values()):
            raise ValueError("domain pack handler must implement execute")
        object.__setattr__(self, "handlers", MappingProxyType(handlers))


__all__ = [
    "ALLOWED_PLATFORM_PORTS", "DOMAIN_PACK_SCHEMA_VERSION", "DomainCapabilityManifest",
    "DomainLifecycleStageManifest", "DomainPack", "DomainPackHandler", "DomainPackManifest",
    "DomainPackRequest",
]
