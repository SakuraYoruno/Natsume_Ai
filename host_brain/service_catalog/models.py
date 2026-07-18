"""Versioned endpoint ownership contracts for the local service catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping


SERVICE_CATALOG_SCHEMA_VERSION = "natsume.service-catalog.v1"
SERVICE_CATALOG_MODE_ENV = "HOST_SERVICE_CATALOG_MODE"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_PROTOCOL_RE = re.compile(r"^[a-z][a-z0-9.+_-]{0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class CatalogMode(str, Enum):
    AUDIT = "AUDIT"
    SHADOW = "SHADOW"
    PREFLIGHT = "PREFLIGHT"
    CANARY = "CANARY"
    ENFORCE = "ENFORCE"


class EndpointKind(str, Enum):
    LISTENER = "LISTENER"
    DEPENDENCY = "DEPENDENCY"
    DYNAMIC_TEMPLATE = "DYNAMIC_TEMPLATE"


class Transport(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class Exposure(str, Enum):
    LOOPBACK = "LOOPBACK"
    PRIVATE = "PRIVATE"
    PUBLIC = "PUBLIC"


class AuthKind(str, Enum):
    PUBLIC_LIVENESS = "PUBLIC_LIVENESS"
    HMAC = "HMAC"
    TOKEN = "TOKEN"
    MTLS = "MTLS"
    PLATFORM_SIGNATURE = "PLATFORM_SIGNATURE"
    ROUTE_POLICY = "ROUTE_POLICY"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class CatalogModeState:
    mode: CatalogMode
    configured_value: str
    valid: bool
    reason_code: str | None = None


def _required_identifier(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if _ID_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a stable lowercase identifier")
    return normalized


def _required_text(value: Any, field_name: str, maximum: int = 512) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > maximum
        or _CONTROL_RE.search(normalized) is not None
    ):
        raise ValueError(f"{field_name} must be non-empty safe text")
    return normalized


@dataclass(frozen=True, slots=True)
class ProtocolSpec:
    name: str
    version: str

    def __post_init__(self) -> None:
        name = str(self.name or "").strip().lower()
        version = str(self.version or "").strip()
        if _PROTOCOL_RE.fullmatch(name) is None:
            raise ValueError("protocol name is invalid")
        if _VERSION_RE.fullmatch(version) is None:
            raise ValueError("protocol version is invalid")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)

    def canonical_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True, slots=True)
class HealthSpec:
    kind: str
    path: str
    expected_owner_id: str
    expected_protocol: str
    expected_protocol_version: str

    def __post_init__(self) -> None:
        kind = _required_identifier(self.kind, "health.kind")
        path = _required_text(self.path, "health.path", 1024)
        owner = _required_identifier(
            self.expected_owner_id, "health.expected_owner_id"
        )
        protocol = str(self.expected_protocol or "").strip().lower()
        version = str(self.expected_protocol_version or "").strip()
        if _PROTOCOL_RE.fullmatch(protocol) is None:
            raise ValueError("health expected protocol is invalid")
        if _VERSION_RE.fullmatch(version) is None:
            raise ValueError("health expected protocol version is invalid")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "expected_owner_id", owner)
        object.__setattr__(self, "expected_protocol", protocol)
        object.__setattr__(self, "expected_protocol_version", version)

    def canonical_dict(self) -> dict[str, str]:
        return {
            "expected_owner_id": self.expected_owner_id,
            "expected_protocol": self.expected_protocol,
            "expected_protocol_version": self.expected_protocol_version,
            "kind": self.kind,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    endpoint_id: str
    listener_id: str
    owner_id: str
    kind: EndpointKind
    transport: Transport
    bind_host: str
    port: int
    protocols: tuple[ProtocolSpec, ...]
    health: HealthSpec
    exposure: Exposure
    auth_kind: AuthKind
    auth_policy_ref: str
    enabled: bool = True
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        endpoint_id = _required_identifier(self.endpoint_id, "endpoint_id")
        listener_id = _required_identifier(self.listener_id, "listener_id")
        owner_id = _required_identifier(self.owner_id, "owner_id")
        if not isinstance(self.kind, EndpointKind):
            raise ValueError("kind must be EndpointKind")
        if not isinstance(self.transport, Transport):
            raise ValueError("transport must be Transport")
        host = _required_text(self.bind_host, "bind_host", 255).lower()
        if type(self.port) is not int:
            raise ValueError("port must be int")
        if self.kind is EndpointKind.DYNAMIC_TEMPLATE:
            if not (0 <= self.port <= 65535):
                raise ValueError("dynamic template port is invalid")
        elif not (1 <= self.port <= 65535):
            raise ValueError("port must be in 1..65535")
        if (
            not isinstance(self.protocols, tuple)
            or not self.protocols
            or any(type(item) is not ProtocolSpec for item in self.protocols)
        ):
            raise ValueError("protocols must be a non-empty tuple[ProtocolSpec]")
        protocol_keys = {(item.name, item.version) for item in self.protocols}
        if len(protocol_keys) != len(self.protocols):
            raise ValueError("protocols contain duplicates")
        if type(self.health) is not HealthSpec:
            raise ValueError("health must be HealthSpec")
        if self.health.expected_owner_id != owner_id:
            raise ValueError("health owner must equal endpoint owner")
        if not isinstance(self.exposure, Exposure):
            raise ValueError("exposure must be Exposure")
        if not isinstance(self.auth_kind, AuthKind):
            raise ValueError("auth_kind must be AuthKind")
        policy_ref = str(self.auth_policy_ref or "").strip()
        if self.auth_kind is not AuthKind.NONE:
            policy_ref = _required_text(policy_ref, "auth_policy_ref", 512)
        elif _CONTROL_RE.search(policy_ref) is not None:
            raise ValueError("auth_policy_ref is invalid")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be bool")
        if not isinstance(self.source_refs, tuple) or any(
            not isinstance(ref, str) or not ref.strip() or _CONTROL_RE.search(ref)
            for ref in self.source_refs
        ):
            raise ValueError("source_refs must be safe tuple[str]")
        object.__setattr__(self, "endpoint_id", endpoint_id)
        object.__setattr__(self, "listener_id", listener_id)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "bind_host", host)
        object.__setattr__(self, "auth_policy_ref", policy_ref)
        object.__setattr__(self, "source_refs", tuple(self.source_refs))

    @property
    def host(self) -> str:
        """Compatibility alias used by injected bind probes."""

        return self.bind_host

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "auth_kind": self.auth_kind.value,
            "auth_policy_ref": self.auth_policy_ref,
            "bind_host": self.bind_host,
            "enabled": self.enabled,
            "endpoint_id": self.endpoint_id,
            "exposure": self.exposure.value,
            "health": self.health.canonical_dict(),
            "kind": self.kind.value,
            "listener_id": self.listener_id,
            "owner_id": self.owner_id,
            "port": self.port,
            "protocols": [item.canonical_dict() for item in self.protocols],
            "source_refs": list(self.source_refs),
            "transport": self.transport.value,
        }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    schema_version: str
    endpoints: tuple[EndpointSpec, ...]
    digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SERVICE_CATALOG_SCHEMA_VERSION:
            raise ValueError("unsupported service catalog schema")
        if not isinstance(self.endpoints, tuple) or any(
            type(item) is not EndpointSpec for item in self.endpoints
        ):
            raise ValueError("endpoints must be tuple[EndpointSpec]")
        ordered = tuple(sorted(self.endpoints, key=lambda item: item.endpoint_id))
        ids = [item.endpoint_id for item in ordered]
        if len(ids) != len(set(ids)):
            raise ValueError("endpoint_id must be unique")
        payload = {
            "endpoints": [item.canonical_dict() for item in ordered],
            "schema_version": self.schema_version,
        }
        expected = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        if self.digest and self.digest != expected:
            raise ValueError("catalog digest mismatch")
        object.__setattr__(self, "endpoints", ordered)
        object.__setattr__(self, "digest", expected)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "endpoints": [item.canonical_dict() for item in self.endpoints],
            "schema_version": self.schema_version,
        }

    def by_id(self) -> Mapping[str, EndpointSpec]:
        return MappingProxyType({item.endpoint_id: item for item in self.endpoints})


@dataclass(frozen=True, slots=True)
class CatalogIssue:
    reason_code: str
    endpoint_ids: tuple[str, ...]
    detail: str
    socket_ref: str | None = None
    blocking: bool = True

    def __post_init__(self) -> None:
        reason = _required_text(self.reason_code, "reason_code", 128).upper()
        if not isinstance(self.endpoint_ids, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.endpoint_ids
        ):
            raise ValueError("endpoint_ids must be tuple[str]")
        detail = _required_text(self.detail, "detail", 1024)
        if self.socket_ref is not None and (
            not isinstance(self.socket_ref, str)
            or _CONTROL_RE.search(self.socket_ref) is not None
        ):
            raise ValueError("socket_ref is invalid")
        if type(self.blocking) is not bool:
            raise ValueError("blocking must be bool")
        object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "endpoint_ids", tuple(sorted(set(self.endpoint_ids))))
        object.__setattr__(self, "detail", detail)


@dataclass(frozen=True, slots=True)
class PreflightDecision:
    mode: CatalogMode
    snapshot_digest: str
    start_allowed: bool
    issues: tuple[CatalogIssue, ...]
    reservation_ref: str | None = None
    issued_at_ms: int | None = None
    expires_at_ms: int | None = None
    reservation: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.mode, CatalogMode):
            raise ValueError("mode must be CatalogMode")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.snapshot_digest) is None:
            raise ValueError("snapshot_digest is invalid")
        if type(self.start_allowed) is not bool:
            raise ValueError("start_allowed must be bool")
        if not isinstance(self.issues, tuple) or any(
            type(item) is not CatalogIssue for item in self.issues
        ):
            raise ValueError("issues must be tuple[CatalogIssue]")
        time_values = (self.issued_at_ms, self.expires_at_ms)
        if any(value is not None for value in time_values):
            if (
                type(self.issued_at_ms) is not int
                or type(self.expires_at_ms) is not int
                or self.issued_at_ms < 0
                or self.expires_at_ms <= self.issued_at_ms
                or not self.reservation_ref
            ):
                raise ValueError("reservation metadata is invalid")

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.reason_code for issue in self.issues)


def load_catalog_mode(
    environ: Mapping[str, str] | None = None,
) -> CatalogModeState:
    source = environ or {}
    raw = str(source.get(SERVICE_CATALOG_MODE_ENV, "") or "").strip().upper()
    if not raw:
        return CatalogModeState(CatalogMode.AUDIT, "", True)
    try:
        return CatalogModeState(CatalogMode(raw), raw, True)
    except ValueError:
        return CatalogModeState(
            CatalogMode.AUDIT,
            raw[:64],
            False,
            "SERVICE_CATALOG_MODE_INVALID",
        )


def build_catalog_snapshot(
    endpoints: tuple[EndpointSpec, ...] | list[EndpointSpec],
) -> CatalogSnapshot:
    return CatalogSnapshot(
        schema_version=SERVICE_CATALOG_SCHEMA_VERSION,
        endpoints=tuple(endpoints),
    )


__all__ = [
    "AuthKind",
    "CatalogIssue",
    "CatalogMode",
    "CatalogModeState",
    "CatalogSnapshot",
    "EndpointKind",
    "EndpointSpec",
    "Exposure",
    "HealthSpec",
    "PreflightDecision",
    "ProtocolSpec",
    "SERVICE_CATALOG_MODE_ENV",
    "SERVICE_CATALOG_SCHEMA_VERSION",
    "Transport",
    "build_catalog_snapshot",
    "load_catalog_mode",
]
