"""Owned loopback socket reservations for production listener handoff.

``ReadOnlySocketBindProbe`` can answer whether a bind is available at one
instant, but closing its probe socket necessarily leaves a race before the
real server binds.  This provider removes that gap: it binds every selected
TCP/UDP claim as one transaction, keeps those exact sockets open through
attestation consumption, and transfers the same socket objects to the
candidate.

The provider never discovers, adopts, closes, or otherwise mutates an
incumbent listener.  Every close performed here targets a socket created by
this instance.  Handoff is deliberately limited to loopback listeners; a
future remote/socket-activation provider must be an explicit composition
choice rather than an accidental widening of this authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import ipaddress
import secrets
import socket
import threading
from typing import Mapping, Sequence

from .models import EndpointKind, EndpointSpec, Exposure, Transport
from .probes import (
    BindProbeResult,
    ReservationAttestation,
    strict_socket_addresses,
)


MAX_SOCKET_RESERVATION_TTL_MS = 30_000

_STATE_RESERVED = "RESERVED"
_STATE_CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class _SocketClaim:
    endpoint_id: str
    family: int
    address: str
    port: int
    socket_type: int


@dataclass(slots=True)
class _HeldReservation:
    attestation: ReservationAttestation
    binding_key: tuple[str, tuple[str, ...]]
    sockets: dict[str, tuple[socket.socket, ...]]
    state: str = _STATE_RESERVED
    expiry_timer: threading.Timer | None = None


def _normalized_digest(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or normalized != value or any(character.isspace() for character in normalized):
        return ""
    return normalized


def _normalized_endpoint_ids(value: object) -> tuple[str, ...] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    endpoint_ids: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        normalized = item.strip()
        if not normalized or normalized != item:
            return None
        endpoint_ids.append(normalized)
    if not endpoint_ids or len(set(endpoint_ids)) != len(endpoint_ids):
        return None
    return tuple(sorted(endpoint_ids))


def _socket_type(endpoint: EndpointSpec) -> int:
    if endpoint.transport is Transport.TCP:
        return socket.SOCK_STREAM
    if endpoint.transport is Transport.UDP:
        return socket.SOCK_DGRAM
    raise ValueError("unsupported listener transport")


def _claims_for_endpoint(endpoint: object) -> tuple[_SocketClaim, ...]:
    if type(endpoint) is not EndpointSpec:
        raise ValueError("reservation endpoint must be EndpointSpec")
    assert isinstance(endpoint, EndpointSpec)
    if (
        endpoint.enabled is not True
        or endpoint.kind is not EndpointKind.LISTENER
        or endpoint.exposure is not Exposure.LOOPBACK
    ):
        raise ValueError("reservation requires an enabled loopback listener")
    socket_type = _socket_type(endpoint)
    claims: list[_SocketClaim] = []
    for family, address in strict_socket_addresses(endpoint.bind_host):
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:  # defensive; strict_socket_addresses parsed it
            raise ValueError("reservation bind address is invalid") from exc
        if not parsed.is_loopback:
            raise ValueError("reservation bind address must be loopback")
        claims.append(
            _SocketClaim(
                endpoint_id=endpoint.endpoint_id,
                family=family,
                address=address,
                port=endpoint.port,
                socket_type=socket_type,
            )
        )
    if not claims:
        raise ValueError("reservation has no concrete bind address")
    return tuple(claims)


def _bind_tuple(claim: _SocketClaim) -> tuple[object, ...]:
    if claim.family == socket.AF_INET6:
        return (claim.address, claim.port, 0, 0)
    return (claim.address, claim.port)


def _open_exclusive_socket(claim: _SocketClaim) -> socket.socket:
    owned = socket.socket(claim.family, claim.socket_type)
    try:
        if claim.family == socket.AF_INET6:
            ipv6_only = getattr(socket, "IPV6_V6ONLY", None)
            if ipv6_only is not None:
                owned.setsockopt(socket.IPPROTO_IPV6, ipv6_only, 1)
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            owned.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        owned.bind(_bind_tuple(claim))
        return owned
    except BaseException:
        owned.close()
        raise


def _close_sockets(sockets_by_endpoint: Mapping[str, Sequence[socket.socket]]) -> None:
    for endpoint_sockets in tuple(sockets_by_endpoint.values()):
        for owned in tuple(endpoint_sockets):
            try:
                owned.close()
            except OSError:
                pass


def _copy_attestation(attestation: ReservationAttestation) -> ReservationAttestation:
    return ReservationAttestation(
        reservation_ref=attestation.reservation_ref,
        snapshot_digest=attestation.snapshot_digest,
        endpoint_ids=attestation.endpoint_ids,
        issued_at_ms=attestation.issued_at_ms,
        expires_at_ms=attestation.expires_at_ms,
        consumable=attestation.consumable,
    )


class OwnedSocketReservationProbe:
    """Atomic all-or-none loopback socket reservation and one-shot handoff.

    ``take_consumed_sockets`` transfers ownership to its caller.  Sockets
    returned by that method are no longer closed by :meth:`close`; the
    candidate must close them during its own rollback/shutdown.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reservations: dict[str, _HeldReservation] = {}
        self._reservation_by_binding: dict[tuple[str, tuple[str, ...]], str] = {}
        self._closed = False

    def inspect(self, endpoint: EndpointSpec) -> BindProbeResult:
        endpoint_id = str(getattr(endpoint, "endpoint_id", "") or "")
        opened: dict[str, list[socket.socket]] = {endpoint_id: []}
        try:
            claims = _claims_for_endpoint(endpoint)
            for claim in claims:
                opened[endpoint_id].append(_open_exclusive_socket(claim))
        except (OSError, ValueError) as exc:
            _close_sockets(opened)
            return BindProbeResult(
                endpoint_id=endpoint_id,
                available=False,
                detail=f"exclusive loopback bind unavailable: {type(exc).__name__}",
            )
        _close_sockets(opened)
        return BindProbeResult(endpoint_id=endpoint_id, available=True)

    def reserve(
        self,
        endpoints: Sequence[EndpointSpec],
        *,
        snapshot_digest: str,
        now_ms: int,
        ttl_ms: int,
    ) -> ReservationAttestation | None:
        digest = _normalized_digest(snapshot_digest)
        if (
            not digest
            or type(now_ms) is not int
            or now_ms < 0
            or type(ttl_ms) is not int
            or not 1 <= ttl_ms <= MAX_SOCKET_RESERVATION_TTL_MS
            or isinstance(endpoints, (str, bytes))
            or not isinstance(endpoints, Sequence)
        ):
            return None

        endpoint_rows = tuple(endpoints)
        endpoint_ids = _normalized_endpoint_ids(
            tuple(
                str(getattr(endpoint, "endpoint_id", "") or "")
                for endpoint in endpoint_rows
            )
        )
        if endpoint_ids is None or len(endpoint_ids) != len(endpoint_rows):
            return None
        binding_key = (digest, endpoint_ids)

        with self._lock:
            if self._closed:
                return None
            self._cleanup_expired_locked(now_ms)
            if binding_key in self._reservation_by_binding:
                return None

            opened_lists: dict[str, list[socket.socket]] = {
                endpoint_id: [] for endpoint_id in endpoint_ids
            }
            try:
                for endpoint in sorted(
                    endpoint_rows,
                    key=lambda item: str(getattr(item, "endpoint_id", "")),
                ):
                    claims = _claims_for_endpoint(endpoint)
                    for claim in claims:
                        opened_lists[claim.endpoint_id].append(
                            _open_exclusive_socket(claim)
                        )
            except (OSError, ValueError):
                _close_sockets(opened_lists)
                return None

            opened = {
                endpoint_id: tuple(opened_lists[endpoint_id])
                for endpoint_id in endpoint_ids
            }
            internal_attestation = ReservationAttestation(
                reservation_ref="sockres_" + secrets.token_urlsafe(24),
                snapshot_digest=digest,
                endpoint_ids=endpoint_ids,
                issued_at_ms=now_ms,
                expires_at_ms=now_ms + ttl_ms,
                consumable=True,
            )
            held = _HeldReservation(
                attestation=internal_attestation,
                binding_key=binding_key,
                sockets=opened,
            )
            self._reservations[internal_attestation.reservation_ref] = held
            self._reservation_by_binding[binding_key] = internal_attestation.reservation_ref
            timer = threading.Timer(
                ttl_ms / 1000.0,
                self._expire_from_timer,
                args=(internal_attestation.reservation_ref,),
            )
            timer.daemon = True
            held.expiry_timer = timer
            try:
                timer.start()
            except RuntimeError:
                self._remove_locked(internal_attestation.reservation_ref, close_owned=True)
                return None
            # Never return the internally retained object: frozen dataclasses
            # can still be tampered via object.__setattr__.
            return _copy_attestation(internal_attestation)

    def consume_reservation(
        self,
        attestation: ReservationAttestation,
        *,
        snapshot_digest: str,
        now_ms: int,
    ) -> bool:
        if (
            type(attestation) is not ReservationAttestation
            or type(now_ms) is not int
            or now_ms < 0
        ):
            return False
        digest = _normalized_digest(snapshot_digest)
        if not digest:
            return False

        with self._lock:
            if self._closed:
                return False
            self._cleanup_expired_locked(now_ms)
            held = self._reservations.get(attestation.reservation_ref)
            if held is None or held.state != _STATE_RESERVED:
                return False
            expected = held.attestation
            if not self._attestation_matches(attestation, expected):
                return False
            if not hmac.compare_digest(digest, expected.snapshot_digest):
                return False
            if not expected.issued_at_ms <= now_ms < expected.expires_at_ms:
                self._remove_locked(expected.reservation_ref, close_owned=True)
                return False
            held.state = _STATE_CONSUMED
            return True

    def take_consumed_sockets(
        self,
        *,
        snapshot_digest: str,
        endpoint_ids: Sequence[str],
        now_ms: int,
    ) -> dict[str, tuple[socket.socket, ...]] | None:
        """Transfer the exact consumed socket set for one catalog binding.

        The caller becomes the sole owner of every returned socket.  A wrong
        snapshot, wrong endpoint set, expiry, missing consume, or replay
        returns ``None`` and transfers no authority.
        """

        digest = _normalized_digest(snapshot_digest)
        normalized_ids = _normalized_endpoint_ids(endpoint_ids)
        if (
            not digest
            or normalized_ids is None
            or type(now_ms) is not int
            or now_ms < 0
        ):
            return None
        binding_key = (digest, normalized_ids)
        with self._lock:
            if self._closed:
                return None
            self._cleanup_expired_locked(now_ms)
            reservation_ref = self._reservation_by_binding.get(binding_key)
            held = self._reservations.get(str(reservation_ref or ""))
            if held is None or held.state != _STATE_CONSUMED:
                return None
            if now_ms >= held.attestation.expires_at_ms:
                self._remove_locked(held.attestation.reservation_ref, close_owned=True)
                return None
            transferred = dict(held.sockets)
            self._remove_locked(held.attestation.reservation_ref, close_owned=False)
            return transferred

    def cleanup_expired(self, *, now_ms: int) -> int:
        """Close provider-owned reservations whose logical TTL has elapsed."""

        if type(now_ms) is not int or now_ms < 0:
            return 0
        with self._lock:
            return self._cleanup_expired_locked(now_ms)

    def close(self) -> None:
        """Close every socket still owned by this provider, idempotently."""

        with self._lock:
            self._closed = True
            for reservation_ref in tuple(self._reservations):
                self._remove_locked(reservation_ref, close_owned=True)

    def __enter__(self) -> OwnedSocketReservationProbe:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    @staticmethod
    def _attestation_matches(
        candidate: ReservationAttestation,
        expected: ReservationAttestation,
    ) -> bool:
        try:
            return bool(
                hmac.compare_digest(candidate.reservation_ref, expected.reservation_ref)
                and hmac.compare_digest(candidate.snapshot_digest, expected.snapshot_digest)
                and candidate.endpoint_ids == expected.endpoint_ids
                and candidate.issued_at_ms == expected.issued_at_ms
                and candidate.expires_at_ms == expected.expires_at_ms
                and candidate.consumable is True
                and expected.consumable is True
            )
        except (TypeError, AttributeError):
            return False

    def _cleanup_expired_locked(self, now_ms: int) -> int:
        expired = tuple(
            reservation_ref
            for reservation_ref, held in self._reservations.items()
            if now_ms >= held.attestation.expires_at_ms
        )
        for reservation_ref in expired:
            self._remove_locked(reservation_ref, close_owned=True)
        return len(expired)

    def _expire_from_timer(self, reservation_ref: str) -> None:
        with self._lock:
            # The timer is a wall-duration backstop.  Logical-clock cleanup is
            # also applied by consume/take/cleanup_expired for deterministic
            # tests and injected coordinator clocks.
            self._remove_locked(reservation_ref, close_owned=True)

    def _remove_locked(self, reservation_ref: str, *, close_owned: bool) -> None:
        held = self._reservations.pop(reservation_ref, None)
        if held is None:
            return
        if self._reservation_by_binding.get(held.binding_key) == reservation_ref:
            self._reservation_by_binding.pop(held.binding_key, None)
        timer = held.expiry_timer
        if timer is not None:
            timer.cancel()
        if close_owned:
            _close_sockets(held.sockets)


__all__ = [
    "MAX_SOCKET_RESERVATION_TTL_MS",
    "OwnedSocketReservationProbe",
]
