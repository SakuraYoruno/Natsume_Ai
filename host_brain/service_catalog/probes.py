"""Fail-closed probe contracts for the service catalog preflight gate.

The default socket probe is intentionally read-only: it can establish that a
bind would currently succeed, but it cannot issue a handoff reservation.  It
never kills, adopts, or closes an incumbent process and it never asks the OS
to reassign a configured port.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from typing import Protocol, Sequence, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from host_brain.service_catalog.models import EndpointSpec


@dataclass(frozen=True, slots=True)
class BindProbeResult:
    """Observation for one fixed listener claim."""

    endpoint_id: str
    available: bool
    owner_id: str | None = None
    protocol_id: str | None = None
    protocol_version: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HealthProbeResult:
    """Health observation with explicit service identity evidence.

    A status code, including an arbitrary 2xx response, is not service owner
    evidence.  Callers must compare all three identity fields with the catalog.
    """

    endpoint_id: str
    healthy: bool
    status_code: int | None = None
    owner_id: str | None = None
    protocol_id: str | None = None
    protocol_version: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReservationAttestation:
    """Opaque, probe-issued reservation prepared for one startup handoff."""

    reservation_ref: str
    snapshot_digest: str
    endpoint_ids: tuple[str, ...]
    issued_at_ms: int
    expires_at_ms: int
    consumable: bool


@runtime_checkable
class BindProbe(Protocol):
    """Port observation and optional atomic reservation provider."""

    def inspect(self, endpoint: EndpointSpec) -> BindProbeResult:
        """Inspect one listener without changing the incumbent."""

    def reserve(
        self,
        endpoints: Sequence[EndpointSpec],
        *,
        snapshot_digest: str,
        now_ms: int,
        ttl_ms: int,
    ) -> ReservationAttestation | None:
        """Prepare a short-lived reservation for an explicit later consume."""

    def consume_reservation(
        self,
        attestation: ReservationAttestation,
        *,
        snapshot_digest: str,
        now_ms: int,
    ) -> bool:
        """Atomically hand the exact reservation to startup once."""


@runtime_checkable
class HealthProbe(Protocol):
    """Read-only health and service identity observer."""

    def inspect(self, endpoint: EndpointSpec) -> HealthProbeResult:
        """Inspect the endpoint without treating a status code as identity."""


def strict_socket_addresses(host: str) -> tuple[tuple[int, str], ...]:
    """Parse a literal bind host without DNS or ambient resolver behavior."""

    raw = str(host or "").strip()
    if not raw:
        raise ValueError("bind host is required")
    lowered = raw.lower()
    if lowered == "localhost":
        return (
            (socket.AF_INET, "127.0.0.1"),
            (socket.AF_INET6, "::1"),
        )
    if lowered == "*":
        return (
            (socket.AF_INET, "0.0.0.0"),
            (socket.AF_INET6, "::"),
        )
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError("bind host must be an IP literal, localhost, or wildcard") from exc
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    return ((family, address.compressed),)


def _transport_value(endpoint: EndpointSpec) -> str:
    transport = getattr(endpoint, "transport", "")
    value = getattr(transport, "value", transport)
    return str(value or "").strip().lower()


def _bind_host_value(endpoint: EndpointSpec) -> str:
    """Return the catalog bind host without consulting DNS or environment.

    ``EndpointSpec`` calls this field ``bind_host``.  The ``host`` fallback is
    deliberately kept for the narrow legacy adapter while callers move to the
    catalog model; an explicitly present (but empty) ``bind_host`` is not
    replaced by a legacy value.
    """

    if hasattr(endpoint, "bind_host"):
        return str(getattr(endpoint, "bind_host") or "")
    return str(getattr(endpoint, "host", "") or "")


def _socket_type(endpoint: EndpointSpec) -> int:
    transport = _transport_value(endpoint)
    if transport == "tcp":
        return socket.SOCK_STREAM
    if transport == "udp":
        return socket.SOCK_DGRAM
    raise ValueError("transport must be TCP or UDP")


def _bind_tuple(family: int, address: str, port: int) -> tuple[object, ...]:
    if family == socket.AF_INET6:
        return (address, port, 0, 0)
    return (address, port)


class ReadOnlySocketBindProbe:
    """Conservative bind availability probe with exclusive socket semantics.

    Successful sockets are closed immediately because this class is not a
    reservation provider.  Only sockets created by this instance are closed.
    Incumbent sockets are never discoverable through this API.
    """

    def inspect(self, endpoint: EndpointSpec) -> BindProbeResult:
        endpoint_id = str(getattr(endpoint, "endpoint_id", "") or "")
        try:
            port = int(getattr(endpoint, "port"))
            addresses = strict_socket_addresses(_bind_host_value(endpoint))
            socket_type = _socket_type(endpoint)
        except (TypeError, ValueError, AttributeError) as exc:
            return BindProbeResult(
                endpoint_id=endpoint_id,
                available=False,
                detail=f"invalid bind claim: {exc}",
            )

        for family, address in addresses:
            probe_socket: socket.socket | None = None
            try:
                probe_socket = socket.socket(family, socket_type)
                exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if exclusive is not None:
                    probe_socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                probe_socket.bind(_bind_tuple(family, address, port))
            except OSError as exc:
                return BindProbeResult(
                    endpoint_id=endpoint_id,
                    available=False,
                    detail=f"configured bind is unavailable: {type(exc).__name__}",
                )
            finally:
                if probe_socket is not None:
                    probe_socket.close()
        return BindProbeResult(endpoint_id=endpoint_id, available=True)

    def reserve(
        self,
        endpoints: Sequence[EndpointSpec],
        *,
        snapshot_digest: str,
        now_ms: int,
        ttl_ms: int,
    ) -> ReservationAttestation | None:
        # A check-then-close result is not a reservation and must never be
        # upgraded into startup authority.
        return None

    def consume_reservation(
        self,
        attestation: ReservationAttestation,
        *,
        snapshot_digest: str,
        now_ms: int,
    ) -> bool:
        return False


__all__ = [
    "BindProbe",
    "BindProbeResult",
    "HealthProbe",
    "HealthProbeResult",
    "ReadOnlySocketBindProbe",
    "ReservationAttestation",
    "strict_socket_addresses",
]
