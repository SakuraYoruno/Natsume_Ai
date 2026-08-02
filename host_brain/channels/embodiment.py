"""Host-side command authoring port for all physical embodiment effects.

This module may send a versioned command to Edge.  It must never import device
drivers or perform MateEngine/VTS/voice/desktop effects itself.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import threading
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_KIND_AVATAR,
    EMBODIMENT_KIND_DESKTOP,
    EMBODIMENT_KIND_MATEENGINE,
    EMBODIMENT_KIND_VOICE,
    EMBODIMENT_RECEIPT_NOT_FOUND,
    EmbodimentCommand,
    EmbodimentCommandPort,
    EmbodimentReceipt,
    build_embodiment_command,
    build_embodiment_receipt_query,
    parse_embodiment_receipt,
)
from natsume_contracts.edge.transport_v1 import (
    EDGE_SESSION_RECONNECT_INVALID,
    EdgeSessionLease,
    bind_command_to_lease,
    parse_edge_session_lease,
)
from natsume_contracts.edge.v1 import new_request_id, now_ms


_logger = logging.getLogger("Natsume.embodiment")
_AUTHORITY_REF = "authority://host/device-command-author"

if TYPE_CHECKING:
    from host_brain.channels.embodiment_outbox import EmbodimentOutboxDispatcher
    from host_brain.persistence.embodiment_outbox_repository import (
        EmbodimentOutboxRepository,
    )


class _TransportLeaseUnavailable(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "EDGE_EMBODIMENT_LEASE_UNAVAILABLE")
        self.detail = str(detail or "edge embodiment lease unavailable")[:300]
        super().__init__(self.reason_code)


class EmbodimentReceiptLookupUnavailable(RuntimeError):
    """The Host cannot establish the result of a prior physical command.

    Receipt reconciliation deliberately does not turn this into a synthetic
    rejection: callers must leave the durable Host record in ``IN_DOUBT`` and
    must never replay the physical command merely because its receipt lookup
    was unavailable.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(
            reason_code or "EDGE_EMBODIMENT_RECEIPT_LOOKUP_UNAVAILABLE"
        )
        self.detail = str(detail or "edge embodiment receipt lookup unavailable")[:300]
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class EmbodimentOutboxAck:
    outbox_id: str
    command_id: str
    idempotency_key: str
    status: str = "PENDING"
    reason_code: str | None = None
    detail: str = "embodiment command committed to durable Host outbox"

    @property
    def durable_admitted(self) -> bool:
        """Whether this idempotent command still has a durable Host outcome.

        ``enqueue`` returns the existing durable row for an exact duplicate.
        That row may have advanced past ``PENDING`` while the caller was
        retried (for example after a response was lost).  Treating only a
        fresh ``PENDING`` row as successful makes an already-dispatched or
        already-applied command look as though Host failed to admit it.

        This is deliberately *not* a physical-effect predicate.  Consumers
        that need to claim an Edge effect must use ``effect_verified`` (and
        the durable receipt), never this acknowledgement.
        """

        return str(self.status or "").strip().upper() in {
            "PENDING",
            "DISPATCHING",
            "IN_DOUBT",
            "APPLIED",
        }

    @property
    def effect_verified(self) -> bool:
        """Whether the duplicate row already carries an applied receipt."""

        return str(self.status or "").strip().upper() == "APPLIED"

    @property
    def success(self) -> bool:
        """Compatibility alias for durable admission, not physical success."""

        return self.durable_admitted

    @property
    def accepted(self) -> bool:
        return self.success

    @property
    def event(self) -> dict[str, Any]:
        return {"outbox_id": self.outbox_id, "durable": True}

    @property
    def latency_ms(self) -> int:
        return 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "natsume.host.embodiment-outbox-ack.v1",
            "outbox_id": self.outbox_id,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "accepted": self.accepted,
            "success": self.success,
            "durable_admitted": self.durable_admitted,
            "effect_verified": self.effect_verified,
            "event": self.event,
        }


class EmbodimentOutboxPort(Protocol):
    def enqueue(self, command: EmbodimentCommand) -> EmbodimentOutboxAck: ...


ReceiptObserver = Callable[[EmbodimentCommand, EmbodimentReceipt], None]
EdgeLeasePersister = Callable[[EdgeSessionLease], bool]


@dataclass(frozen=True, slots=True)
class EdgeEmbodimentSessionConfig:
    """Immutable Edge session identity owned by one Host embodiment runtime."""

    session_id: str
    owner_id: str
    ttl_ms: int = 30_000
    renew_before_ms: int = 5_000

    def __post_init__(self) -> None:
        session_id = str(self.session_id or "").strip()
        owner_id = str(self.owner_id or "").strip()
        if not session_id or not owner_id:
            raise ValueError("Edge embodiment session_id and owner_id are required")
        ttl_ms = max(10_000, min(300_000, int(self.ttl_ms)))
        renew_before_ms = max(1_000, min(ttl_ms - 1_000, int(self.renew_before_ms)))
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "ttl_ms", ttl_ms)
        object.__setattr__(self, "renew_before_ms", renew_before_ms)

    @classmethod
    def from_environment(cls) -> "EdgeEmbodimentSessionConfig":
        try:
            ttl_ms = int(
                os.environ.get("HOST_EDGE_EMBODIMENT_LEASE_TTL_MS") or 30_000
            )
        except (TypeError, ValueError):
            ttl_ms = 30_000
        return cls(
            session_id=(
                str(os.environ.get("HOST_EDGE_EMBODIMENT_SESSION_ID") or "host-brain-primary")
            ),
            owner_id=str(os.environ.get("HOST_EDGE_EMBODIMENT_OWNER_ID") or "host-brain"),
            ttl_ms=ttl_ms,
        )


def build_durable_host_embodiment_session_config(
    canonical_db_path: str | os.PathLike[str],
) -> EdgeEmbodimentSessionConfig:
    """Derive a stable Edge lease identity for one canonical Host authority.

    An ASGI app must not use the process-wide default session string directly:
    two applications with different durable stores would otherwise contend for
    the same Edge lease.  The suffix is stable across restart of the *same*
    canonical store, so its old command receipts remain reconcilable, while a
    different store receives a distinct Edge session.  Two applications aimed
    at one same store deliberately still contend rather than gaining a second
    writer identity for one durable authority.
    """

    raw_path = str(canonical_db_path or "").strip()
    if not raw_path:
        raise ValueError("canonical_db_path is required for embodiment session")
    normalized_path = os.path.normcase(
        os.path.abspath(os.path.expanduser(raw_path))
    )
    scope = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()[:20]
    base = EdgeEmbodimentSessionConfig.from_environment()
    return EdgeEmbodimentSessionConfig(
        session_id=f"{base.session_id}::canonical-{scope}",
        owner_id=f"{base.owner_id}::canonical-{scope}",
        ttl_ms=base.ttl_ms,
        renew_before_ms=base.renew_before_ms,
    )


class _EdgeRpcEmbodimentCommandPort:
    """One Host runtime's authenticated Edge command port.

    The Edge lease is intentionally instance state.  A process can therefore
    host two ASGI applications without one application's lease renewal,
    reset, or shutdown mutating the other's command authority.
    """

    def __init__(
        self,
        *,
        post_to_edge: Callable[..., Any] | None = None,
        session_config: EdgeEmbodimentSessionConfig | None = None,
        initial_lease: EdgeSessionLease | None = None,
        lease_persister: EdgeLeasePersister | None = None,
        rotate_initial_lease_fence: bool = False,
    ) -> None:
        if post_to_edge is None:
            from host_brain.channels.edge_transport import _submit_edge_request  # noqa: PLC0415

            post_to_edge = _submit_edge_request
        if not callable(post_to_edge):
            raise TypeError("post_to_edge must be callable")
        self._post_to_edge = post_to_edge
        self._session_config = session_config or EdgeEmbodimentSessionConfig.from_environment()
        if initial_lease is not None and (
            initial_lease.session_id != self._session_config.session_id
            or initial_lease.owner_id != self._session_config.owner_id
        ):
            raise ValueError("initial Edge lease authority does not match session_config")
        if lease_persister is not None and not callable(lease_persister):
            raise TypeError("lease_persister must be callable")
        self._lease_persister = lease_persister
        self._transport_lease: EdgeSessionLease | None = initial_lease
        # A replacement Host runtime may have loaded a still-live Edge lease
        # issued to an older Host fence.  It must rotate the Edge fence before
        # routes open, otherwise an old delayed HTTP request could still cross
        # the physical boundary under that old Edge lease.
        self._rotate_initial_lease_fence = bool(
            initial_lease is not None and rotate_initial_lease_fence
        )
        # ``threading.Lock`` is deliberately instance-scoped: TestClient and
        # embedded applications may drive one runtime from separate event
        # loops, while no lock or lease may cross an application boundary.
        self._transport_lease_state_lock = threading.Lock()

    @property
    def session_config(self) -> EdgeEmbodimentSessionConfig:
        return self._session_config

    def reset_transport_session(self) -> None:
        """Drop only this runtime's local lease cache."""

        with self._transport_lease_state_lock:
            self._transport_lease = None
            self._rotate_initial_lease_fence = False

    @property
    def requires_transport_fence_rotation(self) -> bool:
        with self._transport_lease_state_lock:
            return bool(self._rotate_initial_lease_fence)

    async def establish_transport_authority(self) -> None:
        """Fence a replacement Host before it can admit physical work.

        No network call is made for a fresh runtime without an inherited Edge
        lease.  If a live inherited lease exists, the exact reconnect performs
        an Edge fence rotation before this method returns.
        """

        if not self.requires_transport_fence_rotation:
            return
        await self._get_transport_lease()

    async def submit(self, command: EmbodimentCommand) -> EmbodimentReceipt:
        try:
            lease = await self._get_transport_lease()
        except _TransportLeaseUnavailable as exc:
            return EmbodimentReceipt(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                accepted=False,
                status="REJECTED",
                reason_code=exc.reason_code,
                detail=exc.detail,
                received_at_ms=now_ms(),
                completed_at_ms=now_ms(),
                event={"external_side_effects": False},
            )
        leased_command = bind_command_to_lease(command, lease)
        result = await self._post_to_edge(
            path="/v1/edge/embodiment",
            payload=leased_command.as_dict(),
            request_id=leased_command.request_id,
            timeout_ms=max(1000, leased_command.deadline_ms - now_ms()),
        )
        event = dict(result.event or {})
        receipt_payload = event.get("receipt")
        if not isinstance(receipt_payload, Mapping):
            return self._in_doubt_receipt(
                command,
                reason_code="EDGE_EMBODIMENT_RECEIPT_INVALID",
                detail="Edge embodiment response omitted a durable receipt payload",
                event=event,
            )
        try:
            receipt = parse_embodiment_receipt(receipt_payload)
        except (TypeError, ValueError):
            return self._in_doubt_receipt(
                command,
                reason_code="EDGE_EMBODIMENT_RECEIPT_INVALID",
                detail="Edge embodiment response contained an invalid durable receipt",
                event=event,
            )
        if (
            receipt.command_id != command.command_id
            or receipt.idempotency_key != command.idempotency_key
            or receipt.command_id != leased_command.command_id
            or receipt.idempotency_key != leased_command.idempotency_key
        ):
            return self._in_doubt_receipt(
                command,
                reason_code="EDGE_EMBODIMENT_RECEIPT_IDENTITY_MISMATCH",
                detail="Edge embodiment receipt identity does not match the submitted command",
                event=event,
            )
        return receipt

    @staticmethod
    def _in_doubt_receipt(
        command: EmbodimentCommand,
        *,
        reason_code: str,
        detail: str,
        event: Mapping[str, Any] | None = None,
    ) -> EmbodimentReceipt:
        """Make an untrusted Edge response safe for durable Host settlement.

        The returned receipt always preserves the command authored by this
        Host.  In particular, never surface identifiers supplied by a forged
        or malformed Edge event as an accepted/rejected command result.
        """

        source_event = dict(event or {})
        safe_event: dict[str, Any] = {
            "external_side_effects": "UNKNOWN",
            "receipt_validation": "FAILED",
        }
        transport_status = str(source_event.get("status") or "").strip()
        if transport_status:
            safe_event["transport_status"] = transport_status
        return EmbodimentReceipt(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            accepted=False,
            status="IN_DOUBT",
            reason_code=reason_code,
            detail=str(detail or "Edge embodiment receipt could not be verified")[:300],
            received_at_ms=command.issued_at_ms,
            completed_at_ms=now_ms(),
            event=safe_event,
        )

    async def query_receipt(
        self,
        command: EmbodimentCommand,
    ) -> EmbodimentReceipt | None:
        """Read an exact durable Edge receipt without resubmitting a command.

        A current Host-owned session lease is required for the HMAC-protected
        Edge query.  ``None`` has one precise meaning: Edge accepted the
        lookup and found no matching receipt.  Transport, authority, malformed
        response and identity failures raise instead so the caller preserves
        its Host record as ``IN_DOUBT``.
        """

        try:
            lease = await self._get_transport_lease()
        except _TransportLeaseUnavailable as exc:
            raise EmbodimentReceiptLookupUnavailable(
                exc.reason_code,
                exc.detail,
            ) from exc
        try:
            query = build_embodiment_receipt_query(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                session_id=lease.session_id,
                lease_id=lease.lease_id,
                owner_id=lease.owner_id,
                fencing_token=lease.fencing_token,
            )
        except (TypeError, ValueError) as exc:
            raise EmbodimentReceiptLookupUnavailable(
                "EDGE_EMBODIMENT_RECEIPT_LOOKUP_INVALID",
                "Host command cannot be represented as an exact receipt query",
            ) from exc

        result = await self._post_to_edge(
            path="/v1/edge/embodiment/receipt",
            payload=query.as_dict(),
            request_id=new_request_id(),
            timeout_ms=5_000,
        )
        if not bool(getattr(result, "success", False)):
            raise EmbodimentReceiptLookupUnavailable(
                str(
                    getattr(result, "reason_code", "")
                    or "EDGE_EMBODIMENT_RECEIPT_LOOKUP_UNAVAILABLE"
                ),
                str(
                    getattr(result, "detail", "")
                    or "Edge receipt query was not accepted"
                ),
            )

        event = dict(getattr(result, "event", {}) or {})
        query_event = event.get("receipt_query")
        query_status = (
            str(query_event.get("status") or "").strip().upper()
            if isinstance(query_event, Mapping)
            else ""
        )
        if (
            str(getattr(result, "reason_code", "") or "")
            == EMBODIMENT_RECEIPT_NOT_FOUND
            or query_status == "NOT_FOUND"
        ):
            return None

        receipt_payload = event.get("receipt")
        if not isinstance(receipt_payload, Mapping):
            raise EmbodimentReceiptLookupUnavailable(
                "EDGE_EMBODIMENT_RECEIPT_LOOKUP_INVALID",
                "Edge accepted receipt lookup without a durable receipt payload",
            )
        try:
            receipt = parse_embodiment_receipt(receipt_payload)
        except (TypeError, ValueError) as exc:
            raise EmbodimentReceiptLookupUnavailable(
                "EDGE_EMBODIMENT_RECEIPT_LOOKUP_INVALID",
                "Edge returned an invalid durable receipt payload",
            ) from exc
        if (
            receipt.command_id != command.command_id
            or receipt.idempotency_key != command.idempotency_key
        ):
            raise EmbodimentReceiptLookupUnavailable(
                "EDGE_EMBODIMENT_RECEIPT_IDENTITY_MISMATCH",
                "Edge receipt identity does not match the Host outbox command",
            )
        return receipt

    async def _get_transport_lease(self) -> EdgeSessionLease:
        now = now_ms()
        cached = self._transport_lease
        if (
            cached is not None
            and not self._rotate_initial_lease_fence
            and cached.expires_at_ms - now > self._session_config.renew_before_ms
        ):
            return cached

        async with self._hold_transport_lease_lock():
            now = now_ms()
            cached = self._transport_lease
            if (
                cached is not None
                and not self._rotate_initial_lease_fence
                and cached.expires_at_ms - now > self._session_config.renew_before_ms
            ):
                return cached
            payload: dict[str, Any] = {
                "session_id": self._session_config.session_id,
                "owner_id": self._session_config.owner_id,
                "ttl_ms": self._session_config.ttl_ms,
            }
            if cached is not None and cached.expires_at_ms > now:
                payload.update(
                    {
                        "reconnect_lease_id": cached.lease_id,
                        "reconnect_fencing_token": cached.fencing_token,
                    }
                )
                if self._rotate_initial_lease_fence:
                    payload["rotate_fencing_token"] = True
            result = await self._post_to_edge(
                path="/v1/edge/embodiment/session",
                payload=payload,
                request_id=new_request_id(),
                timeout_ms=5_000,
            )
            if not bool(getattr(result, "success", False)):
                reason = str(
                    getattr(result, "reason_code", "")
                    or "EDGE_EMBODIMENT_LEASE_UNAVAILABLE"
                )
                # A routine reconnect may recover from an Edge store restart.
                # A replacement Host may not: falling back to a fresh session
                # there would discard the one credential that proves which
                # physical fence must be invalidated, allowing a delayed old
                # command to overlap the replacement runtime.
                if (
                    cached is not None
                    and not self._rotate_initial_lease_fence
                    and reason == EDGE_SESSION_RECONNECT_INVALID
                ):
                    result = await self._post_to_edge(
                        path="/v1/edge/embodiment/session",
                        payload={
                            "session_id": self._session_config.session_id,
                            "owner_id": self._session_config.owner_id,
                            "ttl_ms": self._session_config.ttl_ms,
                        },
                        request_id=new_request_id(),
                        timeout_ms=5_000,
                    )
                if not bool(getattr(result, "success", False)):
                    raise _TransportLeaseUnavailable(
                        str(getattr(result, "reason_code", "") or reason),
                        str(
                            getattr(result, "detail", "")
                            or "Edge session handshake rejected"
                        ),
                    )
            event = dict(getattr(result, "event", {}) or {})
            try:
                lease = parse_edge_session_lease(event.get("lease"))
            except (TypeError, ValueError) as exc:
                raise _TransportLeaseUnavailable(
                    "EDGE_EMBODIMENT_LEASE_INVALID",
                    "Edge session handshake returned an invalid lease",
                ) from exc
            if (
                lease.session_id != self._session_config.session_id
                or lease.owner_id != self._session_config.owner_id
            ):
                raise _TransportLeaseUnavailable(
                    "EDGE_EMBODIMENT_LEASE_INVALID",
                    "Edge session handshake authority mismatch",
                )
            if self._rotate_initial_lease_fence and cached is not None and (
                lease.fencing_token != cached.fencing_token + 1
                or lease.lease_id == cached.lease_id
            ):
                raise _TransportLeaseUnavailable(
                    "EDGE_EMBODIMENT_LEASE_INVALID",
                    "Edge replacement handshake did not advance the fencing token",
                )
            if self._lease_persister is not None and not bool(
                self._lease_persister(lease)
            ):
                raise _TransportLeaseUnavailable(
                    "HOST_EMBODIMENT_RUNTIME_AUTHORITY_LOST",
                    "Host runtime lost durable authority before the Edge lease was committed",
                )
            self._transport_lease = lease
            self._rotate_initial_lease_fence = False
            return lease

    @asynccontextmanager
    async def _hold_transport_lease_lock(self):
        """Serialize this runtime's handshakes without blocking its event loop."""

        acquire_task = asyncio.create_task(
            asyncio.to_thread(self._transport_lease_state_lock.acquire)
        )
        try:
            try:
                acquired = bool(await asyncio.shield(acquire_task))
            except asyncio.CancelledError:
                acquired = bool(await acquire_task)
                if acquired:
                    self._transport_lease_state_lock.release()
                raise
            if not acquired:  # pragma: no cover - threading.Lock always returns True
                raise _TransportLeaseUnavailable(
                    "EDGE_EMBODIMENT_LEASE_LOCK_FAILED",
                    "failed to serialize Edge session handshake",
                )
            try:
                yield
            finally:
                self._transport_lease_state_lock.release()
        finally:
            if not acquire_task.done():  # pragma: no cover - cancellation cleanup
                acquire_task.cancel()


def build_embodiment_receipt_audit_observer(
    writer: Callable[[Mapping[str, Any]], None],
) -> Callable[[EmbodimentCommand, EmbodimentReceipt], None]:
    """Bind the durable audit sink at composition without module-global state."""

    if not callable(writer):
        raise TypeError("embodiment receipt audit writer must be callable")

    def observe(command: EmbodimentCommand, receipt: EmbodimentReceipt) -> None:
        _append_durable_receipt_fact(command, receipt, writer=writer)

    return observe


_PUBLIC_RECEIPT_EVENT_FIELDS = frozenset(
    {
        "action", "available", "behavior_id", "component", "connected",
        "device", "emotion_tag", "enabled", "external_side_effects", "kind",
        "model", "state", "status",
    }
)
_SENSITIVE_RECEIPT_DETAIL_MARKERS = (
    "api_key", "approval::", "authorization", "bearer ", "confirm::",
    "credential", "password", "secret", "token",
)


def _public_receipt_detail(value: Any) -> str:
    detail = str(value or "")[:300]
    lowered = detail.lower()
    if any(marker in lowered for marker in _SENSITIVE_RECEIPT_DETAIL_MARKERS):
        return "[redacted receipt detail]"
    return detail


def _append_durable_receipt_fact(
    command: EmbodimentCommand,
    receipt: EmbodimentReceipt,
    *,
    writer: Callable[[Mapping[str, Any]], None],
) -> None:
    """Append a secret-free cross-process receipt fact to the shared ledger."""
    try:
        public_event = {
            str(key): value
            for key, value in dict(receipt.event or {}).items()
            if str(key) in _PUBLIC_RECEIPT_EVENT_FIELDS
            and isinstance(value, (str, int, float, bool, type(None)))
        }
        writer(
            {
                "request_id": command.request_id,
                "event_type": f"EMBODIMENT_RECEIPT_{command.kind.upper()}",
                "direction": "EDGE_TO_HOST",
                "status": str(receipt.status or "UNKNOWN").upper(),
                "reason_code": receipt.reason_code,
                "stage": "embodiment.receipt",
                "detail": _public_receipt_detail(receipt.detail),
                "payload": {
                    "kind": command.kind,
                    "action": command.action,
                    "command_id": receipt.command_id,
                    "accepted": bool(receipt.accepted),
                    "success": bool(receipt.success),
                    "received_at_ms": int(receipt.received_at_ms),
                    "completed_at_ms": receipt.completed_at_ms,
                    "latency_ms": receipt.latency_ms,
                    "receipt_ref": receipt.receipt_ref,
                    "event": public_event,
                },
                "ts_ms": int(receipt.completed_at_ms or receipt.received_at_ms),
            }
        )
    except Exception as exc:  # pragma: no cover - audit sink isolation
        _logger.debug("Embodiment receipt audit append skipped: %s", exc)


def _edge_session_lease_from_durable_row(
    row: Mapping[str, Any] | None,
) -> EdgeSessionLease | None:
    if row is None:
        return None
    return EdgeSessionLease(
        session_id=str(row["session_id"]),
        lease_id=str(row["lease_id"]),
        owner_id=str(row["owner_id"]),
        fencing_token=int(row["fencing_token"]),
        issued_at_ms=int(row["issued_at_ms"]),
        expires_at_ms=int(row["expires_at_ms"]),
    )


class HostEmbodimentRuntime:
    """Application-owned embodiment authority and durable dispatch runtime.

    An instance is created by one Host application's composition root.  It is
    the only object that owns its durable outbox, Edge lease cache and receipt
    observers.  The module deliberately does not retain a default runtime:
    resolving a command path without an explicit runtime fails closed instead
    of silently borrowing another ASGI application's authority.
    """

    def __init__(
        self,
        *,
        repository: "EmbodimentOutboxRepository",
        worker_id: str,
        command_port: EmbodimentCommandPort | None = None,
        post_to_edge: Callable[..., Any] | None = None,
        session_config: EdgeEmbodimentSessionConfig | None = None,
        receipt_observers: tuple[ReceiptObserver, ...] = (),
        authority_lease_ms: int = 30_000,
    ) -> None:
        if repository is None:
            raise TypeError("repository is required")
        if not str(worker_id or "").strip():
            raise ValueError("worker_id is required")
        if command_port is not None and (
            post_to_edge is not None or session_config is not None
        ):
            raise TypeError(
                "command_port cannot be combined with post_to_edge or session_config"
            )
        observers = tuple(receipt_observers)
        if not all(callable(observer) for observer in observers):
            raise TypeError("receipt_observers must be callable")

        self._repository = repository
        self._worker_id = str(worker_id).strip()
        self._authority_lease_ms = max(1_000, int(authority_lease_ms))
        self._authority_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = False
        self._authority_lost = False
        self._receipt_observers: set[ReceiptObserver] = set(observers)
        self._authority_lease = repository.acquire_runtime_lease(
            worker_id=self._worker_id,
            lease_ms=self._authority_lease_ms,
        )
        if self._authority_lease is None:
            self._closed = True
            raise RuntimeError("EMBODIMENT_RUNTIME_AUTHORITY_UNAVAILABLE")

        try:
            effective_port = command_port
            if effective_port is None:
                effective_session = session_config or (
                    build_durable_host_embodiment_session_config(
                        repository.canonical_db_path
                    )
                )
                initial_lease = _edge_session_lease_from_durable_row(
                    repository.load_edge_session_lease(
                        effective_session.session_id,
                        effective_session.owner_id,
                    )
                )
                if initial_lease is not None and not self._persist_edge_session_lease(
                    initial_lease
                ):
                    raise RuntimeError("EMBODIMENT_EDGE_LEASE_REBIND_FAILED")
                effective_port = _EdgeRpcEmbodimentCommandPort(
                    post_to_edge=post_to_edge,
                    session_config=effective_session,
                    initial_lease=initial_lease,
                    lease_persister=self._persist_edge_session_lease,
                    rotate_initial_lease_fence=initial_lease is not None,
                )
            if not callable(getattr(effective_port, "submit", None)):
                raise TypeError("command_port must expose async submit(command)")

            # Import after this module has defined its contracts: the outbox
            # module imports ``EmbodimentOutboxAck`` but never constructs a
            # global runtime.
            from host_brain.channels.embodiment_outbox import (  # noqa: PLC0415
                DurableEmbodimentOutbox,
                EmbodimentOutboxDispatcher,
            )

            self._command_port = effective_port
            self._outbox: EmbodimentOutboxPort = DurableEmbodimentOutbox(
                repository,
                authority_provider=self._authority_for_mutation,
            )
            self._dispatcher: EmbodimentOutboxDispatcher = EmbodimentOutboxDispatcher(
                repository,
                worker_id=self._worker_id,
                # Only a claimed durable outbox row may use the private
                # physical submitter.  Public submit/enqueue entry points must
                # first reopen an exact ActionPreview receipt.
                submitter=self._submit_claimed_outbox,
                receipt_lookup=self.query_receipt,
                receipt_observer=self._publish_receipt,
                authority_provider=self._authority_for_mutation,
            )
        except BaseException:
            self._release_runtime_authority()
            with self._state_lock:
                self._closed = True
                self._receipt_observers.clear()
            raise

    @property
    def dispatcher(self) -> "EmbodimentOutboxDispatcher":
        return self._dispatcher

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def runtime_scope_id(self) -> str:
        return str(self._repository.runtime_scope_id)

    @property
    def authority_lease(self) -> dict[str, Any] | None:
        with self._authority_lock:
            return (
                dict(self._authority_lease)
                if self._authority_lease is not None
                else None
            )

    def enqueue(
        self,
        command: EmbodimentCommand,
        *,
        action_preview_service: Any | None = None,
        action_preview_event_id: str | None = None,
        action_preview_boundary: Mapping[str, Any] | None = None,
    ) -> EmbodimentOutboxAck:
        _require_terminal_embodiment_preview(
            command=command,
            action_preview_service=action_preview_service,
            action_preview_event_id=action_preview_event_id,
            action_preview_boundary=action_preview_boundary,
        )
        self._require_authority()
        return self._outbox.enqueue(command)

    def reopen_outbox_command(
        self,
        command: EmbodimentCommand,
    ) -> dict[str, Any] | None:
        """Read one exact durable Host command without enqueueing or dispatching it."""

        if not isinstance(command, EmbodimentCommand):
            raise TypeError("EmbodimentCommand is required")
        self._require_authority()
        row = self._repository.get_by_command_identity(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
        )
        if row is None:
            return None
        stored = row.get("command")
        if not isinstance(stored, Mapping) or dict(stored) != command.as_dict():
            raise RuntimeError("EMBODIMENT_OUTBOX_COMMAND_IDENTITY_DRIFT")
        return dict(row)

    async def submit(
        self,
        command: EmbodimentCommand,
        *,
        action_preview_service: Any | None = None,
        action_preview_event_id: str | None = None,
        action_preview_boundary: Mapping[str, Any] | None = None,
    ) -> EmbodimentReceipt:
        _require_terminal_embodiment_preview(
            command=command,
            action_preview_service=action_preview_service,
            action_preview_event_id=action_preview_event_id,
            action_preview_boundary=action_preview_boundary,
        )
        return await self._submit_claimed_outbox(command)

    async def _submit_claimed_outbox(
        self,
        command: EmbodimentCommand,
    ) -> EmbodimentReceipt:
        """Execute only a command already claimed from this runtime's outbox."""

        self._require_authority()
        receipt = await self._command_port.submit(command)
        # A physical effect may complete after a stalled Host lost its durable
        # fence.  Do not let that stale process settle or publish the receipt;
        # its dispatch claim will expire to IN_DOUBT and the replacement Host
        # can perform the read-only receipt reconciliation path.
        self._require_authority()
        self._publish_receipt(command, receipt)
        return receipt

    async def establish_transport_authority(self) -> None:
        """Complete an inherited Edge fence rotation before ASGI serves routes."""

        self._require_authority()
        establish = getattr(self._command_port, "establish_transport_authority", None)
        if callable(establish):
            await establish()
        self._require_authority()

    async def dispatch_pending_async(self, *, limit: int = 100) -> Any:
        self._require_authority()
        return await self._dispatcher.dispatch_pending_async(limit=limit)

    async def query_receipt(
        self,
        command: EmbodimentCommand,
    ) -> EmbodimentReceipt | None:
        """Query this application's Edge authority for one old receipt only."""

        self._require_authority()
        lookup = getattr(self._command_port, "query_receipt", None)
        if not callable(lookup):
            raise EmbodimentReceiptLookupUnavailable(
                "EDGE_EMBODIMENT_RECEIPT_LOOKUP_UNAVAILABLE",
                "this embodiment command port has no read-only receipt lookup",
            )
        receipt = await lookup(command)
        self._require_authority()
        if receipt is not None and (
            receipt.command_id != command.command_id
            or receipt.idempotency_key != command.idempotency_key
        ):
            raise EmbodimentReceiptLookupUnavailable(
                "EDGE_EMBODIMENT_RECEIPT_IDENTITY_MISMATCH",
                "receipt lookup returned a different embodiment command",
            )
        return receipt

    async def reconcile_in_doubt_async(self, *, limit: int = 100) -> Any:
        """Reconcile durable uncertainty by read-only lookup, never replay."""

        self._require_authority()
        return await self._dispatcher.reconcile_in_doubt_async(limit=limit)

    def register_receipt_observer(
        self,
        observer: ReceiptObserver,
    ) -> Callable[[], None]:
        """Register a best-effort observer scoped to this runtime only."""

        if not callable(observer):
            raise TypeError("embodiment receipt observer must be callable")
        self._require_authority()
        with self._state_lock:
            if self._closed:
                raise RuntimeError("EMBODIMENT_RUNTIME_CLOSED")
            self._receipt_observers.add(observer)

        def unsubscribe() -> None:
            with self._state_lock:
                self._receipt_observers.discard(observer)

        return unsubscribe

    def reset_transport_session(self) -> None:
        """Reset this application's Edge session cache, never another app's."""

        self._require_authority()
        reset = getattr(self._command_port, "reset_transport_session", None)
        if callable(reset):
            reset()

    def close(self) -> None:
        """Release this application's exact Host fence, retaining Edge reconnect state."""

        with self._authority_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                self._receipt_observers.clear()
            self._release_runtime_authority_locked()
            reset = getattr(self._command_port, "reset_transport_session", None)
            if callable(reset):
                reset()

    def _require_open(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("EMBODIMENT_RUNTIME_CLOSED")

    def renew_authority(self) -> dict[str, Any]:
        """Renew and return this runtime's exact durable authority claim."""

        return self._require_authority()

    async def run_authority_heartbeat(self) -> None:
        """Keep the Host fence live independently of the governed Scheduler.

        The Scheduler clock is intentionally default-off and therefore cannot
        own infrastructure authority renewal.  The ASGI lifespan starts this
        coroutine and cancels/joins it before releasing the runtime on
        shutdown.  Any lost fence terminates the heartbeat; every command path
        still performs its own exact renewal and fails closed.
        """

        interval_seconds = min(
            10.0,
            max(0.25, float(self._authority_lease_ms) / 3_000.0),
        )
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    await asyncio.to_thread(self.renew_authority)
                except RuntimeError as exc:
                    _logger.error(
                        "Host embodiment runtime authority heartbeat stopped: %s",
                        exc,
                    )
                    return
                except Exception as exc:  # pragma: no cover - transient storage fault
                    _logger.warning(
                        "Host embodiment runtime authority heartbeat failed: %s",
                        exc,
                    )
        except asyncio.CancelledError:
            raise

    def _require_authority(self) -> dict[str, Any]:
        with self._authority_lock:
            self._require_open()
            claim = self._authority_lease
            if claim is None or self._authority_lost:
                raise RuntimeError("EMBODIMENT_RUNTIME_AUTHORITY_LOST")
            renewed = self._repository.renew_runtime_lease(
                worker_id=self._worker_id,
                lease_id=str(claim["lease_id"]),
                fencing_token=int(claim["fencing_token"]),
                lease_ms=self._authority_lease_ms,
            )
            if renewed is None:
                self._authority_lost = True
                raise RuntimeError("EMBODIMENT_RUNTIME_AUTHORITY_LOST")
            self._authority_lease = renewed
            return dict(renewed)

    def _authority_for_mutation(self) -> Any:
        """Return a lease token that the repository rechecks in its write tx."""

        from host_brain.persistence.embodiment_outbox_repository import (  # noqa: PLC0415
            EmbodimentRuntimeAuthority,
        )

        claim = self._require_authority()
        return EmbodimentRuntimeAuthority(
            worker_id=self._worker_id,
            lease_id=str(claim["lease_id"]),
            fencing_token=int(claim["fencing_token"]),
        )

    def _persist_edge_session_lease(self, lease: EdgeSessionLease) -> bool:
        with self._authority_lock:
            try:
                self._require_open()
            except RuntimeError:
                return False
            claim = self._authority_lease
            if claim is None or self._authority_lost:
                return False
            renewed = self._repository.renew_runtime_lease(
                worker_id=self._worker_id,
                lease_id=str(claim["lease_id"]),
                fencing_token=int(claim["fencing_token"]),
                lease_ms=self._authority_lease_ms,
            )
            if renewed is None:
                self._authority_lost = True
                return False
            self._authority_lease = renewed
            persisted = self._repository.persist_edge_session_lease(
                session_id=lease.session_id,
                owner_id=lease.owner_id,
                lease_id=lease.lease_id,
                fencing_token=lease.fencing_token,
                issued_at_ms=lease.issued_at_ms,
                expires_at_ms=lease.expires_at_ms,
                expected_runtime_lease_id=str(renewed["lease_id"]),
                expected_runtime_fencing_token=int(renewed["fencing_token"]),
            )
            return persisted is not None

    def _release_runtime_authority(self) -> bool:
        with self._authority_lock:
            return self._release_runtime_authority_locked()

    def _release_runtime_authority_locked(self) -> bool:
        claim = self._authority_lease
        self._authority_lease = None
        if claim is None:
            return False
        try:
            return bool(
                self._repository.release_runtime_lease(
                    worker_id=self._worker_id,
                    lease_id=str(claim["lease_id"]),
                    fencing_token=int(claim["fencing_token"]),
                )
            )
        except Exception as exc:  # pragma: no cover - shutdown isolation
            _logger.error("Host embodiment runtime lease release failed: %s", exc)
            return False

    def _publish_receipt(
        self,
        command: EmbodimentCommand,
        receipt: EmbodimentReceipt,
    ) -> None:
        with self._state_lock:
            observers = tuple(self._receipt_observers)
        for observer in observers:
            try:
                observer(command, receipt)
            except Exception as exc:  # pragma: no cover - observer isolation
                _logger.warning("Embodiment receipt observer failed: %s", exc)


async def build_host_embodiment_runtime_with_takeover_wait(
    *,
    repository: "EmbodimentOutboxRepository",
    worker_id: str,
    command_port: EmbodimentCommandPort | None = None,
    post_to_edge: Callable[..., Any] | None = None,
    session_config: EdgeEmbodimentSessionConfig | None = None,
    receipt_observers: tuple[ReceiptObserver, ...] = (),
    authority_lease_ms: int = 30_000,
    takeover_wait_ms: int = 35_000,
    retry_interval_ms: int = 250,
) -> HostEmbodimentRuntime:
    """Wait only for an expired predecessor authority during process takeover.

    A Windows crash/forced stop cannot run the ASGI shutdown hook that releases
    the durable embodiment lease.  The replacement must therefore wait for
    the existing 30-second lease to expire and acquire a strictly higher
    fencing token.  Only the exact authority-unavailable result is retryable;
    every configuration, storage, Edge rebind, or construction failure remains
    fail-closed.
    """

    wait_ms = max(0, int(takeover_wait_ms))
    interval_ms = max(1, int(retry_interval_ms))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (wait_ms / 1_000.0)
    while True:
        try:
            return HostEmbodimentRuntime(
                repository=repository,
                worker_id=worker_id,
                command_port=command_port,
                post_to_edge=post_to_edge,
                session_config=session_config,
                receipt_observers=receipt_observers,
                authority_lease_ms=authority_lease_ms,
            )
        except RuntimeError as exc:
            if str(exc) != "EMBODIMENT_RUNTIME_AUTHORITY_UNAVAILABLE":
                raise
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise
            await asyncio.sleep(min(interval_ms / 1_000.0, remaining))


def build_host_embodiment_command(
    *,
    kind: str,
    action: str,
    payload: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    authority_ref: str = _AUTHORITY_REF,
    grant_ref: str | None = None,
    deadline_ms: int | None = None,
    idempotency_key: str | None = None,
) -> EmbodimentCommand:
    req_id = str(request_id or new_request_id()).strip()
    body = dict(payload or {})
    key = str(idempotency_key or "").strip() or _default_idempotency_key(
        request_id=req_id,
        kind=kind,
        action=action,
        payload=body,
    )
    issued = now_ms()
    return build_embodiment_command(
        kind=kind,
        action=action,
        payload=body,
        authority_ref=authority_ref,
        grant_ref=str(grant_ref or f"grant://host/device/{req_id}").strip(),
        deadline_ms=int(deadline_ms if deadline_ms is not None else issued + 30_000),
        idempotency_key=key,
        request_id=req_id,
        issued_at_ms=issued,
    )


async def submit_embodiment_command(
    command: EmbodimentCommand,
    *,
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
) -> EmbodimentReceipt:
    """Submit through an explicitly supplied app runtime or command port.

    ``command_port=`` supports narrow test/adaptor use.  Production paths must
    pass their app-owned ``runtime=`` so its receipt audit and local lease are
    retained.  There is intentionally no module default.
    """

    if runtime is not None:
        if command_port is not None:
            raise TypeError("runtime and command_port are mutually exclusive")
        return await runtime.submit(
            command,
            action_preview_service=action_preview_service,
            action_preview_event_id=action_preview_event_id,
            action_preview_boundary=action_preview_boundary,
        )
    if command_port is None:
        raise RuntimeError("EMBODIMENT_RUNTIME_REQUIRED")
    _require_terminal_embodiment_preview(
        command=command,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )
    return await command_port.submit(command)


def _require_terminal_embodiment_preview(
    *,
    command: EmbodimentCommand,
    action_preview_service: Any,
    action_preview_event_id: str | None,
    action_preview_boundary: Mapping[str, Any] | None,
) -> None:
    """Reopen exact Preview authority before public enqueue/direct submit."""

    from host_brain.governor.action_preview import ActionPreviewService  # noqa: PLC0415

    if not isinstance(action_preview_service, ActionPreviewService):
        raise RuntimeError("ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED")
    if not str(action_preview_event_id or "").strip() or not isinstance(
        action_preview_boundary,
        Mapping,
    ):
        raise RuntimeError("ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED")
    boundary = dict(action_preview_boundary)
    args = boundary.get("action_args")
    expected_kind = {
        (EMBODIMENT_KIND_AVATAR, "set_expression"): "embodiment.vts.set_expression",
        (EMBODIMENT_KIND_MATEENGINE, "command"): "embodiment.mateengine.command",
    }.get((str(command.kind), str(command.action)))
    direct_preview = (
        expected_kind is not None
        and str(boundary.get("action_kind") or "") == expected_kind
        and str(boundary.get("action_scope") or "")
        == str(command.idempotency_key)
        and isinstance(args, Mapping)
        and dict(args)
        == {
            "delivery_key": str(command.idempotency_key),
            "command": command.as_dict(),
        }
    )
    authority_context = boundary.get("authority_context")
    acceptance_effect_preview = (
        str(boundary.get("action_kind") or "")
        == "acceptance-effect.edge.embodiment_enqueue"
        and isinstance(args, Mapping)
        and dict(args).get("embodiment_command") == command.as_dict()
        and isinstance(authority_context, Mapping)
        and str(authority_context.get("effect_id") or "").strip()
        == str(boundary.get("action_scope") or "").strip()
        and str(authority_context.get("schema_version") or "")
        == "Natsume.action_preview.acceptance_effect_authority_request.v1"
    )
    companion_expression_preview = False
    if (
        str(boundary.get("action_kind") or "")
        == "acceptance-effect.host.companion_expression_enqueue"
        and isinstance(args, Mapping)
        and isinstance(authority_context, Mapping)
        and str(authority_context.get("effect_id") or "").strip()
        == str(boundary.get("action_scope") or "").strip()
        and str(authority_context.get("schema_version") or "")
        == "Natsume.action_preview.acceptance_effect_authority_request.v1"
        and str(command.grant_ref or "")
        == str(boundary.get("node_grant_ref") or "").strip()
    ):
        # Keep the public enqueue boundary independent from the handler while
        # still proving that a companion-plan Preview owns this exact command.
        # The helper is pure; it cannot open an Edge transport or mutate Life.
        from host_brain.digital_life.companion_expression_acceptance import (  # noqa: PLC0415
            verify_companion_expression_command,
        )

        companion_expression_preview = verify_companion_expression_command(
            payload=dict(args),
            command=command,
        )
    if (
        not direct_preview
        and not acceptance_effect_preview
        and not companion_expression_preview
    ):
        raise RuntimeError("ACTION_PREVIEW_DRIFT")
    action_preview_service.require_execution(
        event_id=str(action_preview_event_id),
        **boundary,
    )


def queue_embodiment_command(
    command: EmbodimentCommand,
    *,
    runtime: HostEmbodimentRuntime | None = None,
    outbox: EmbodimentOutboxPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
) -> EmbodimentOutboxAck:
    """Durably enqueue through an explicitly owned app runtime or outbox."""

    _require_terminal_embodiment_preview(
        command=command,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )

    if runtime is not None:
        if outbox is not None:
            raise TypeError("runtime and outbox are mutually exclusive")
        return runtime.enqueue(
            command,
            action_preview_service=action_preview_service,
            action_preview_event_id=action_preview_event_id,
            action_preview_boundary=action_preview_boundary,
        )
    if outbox is None:
        raise RuntimeError("EMBODIMENT_RUNTIME_REQUIRED")
    return outbox.enqueue(command)


def register_embodiment_receipt_observer(
    observer: ReceiptObserver,
    *,
    runtime: HostEmbodimentRuntime,
) -> Callable[[], None]:
    """Compatibility adapter that remains explicitly runtime-scoped."""

    return runtime.register_receipt_observer(observer)


async def submit_voice_command(
    *,
    action: str = "speak",
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_VOICE, action=action, payload=payload, **meta),
        runtime=runtime,
        command_port=command_port,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


async def submit_avatar_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_AVATAR, action=action, payload=payload, **meta),
        runtime=runtime,
        command_port=command_port,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


async def submit_avatar_behavior(
    *,
    behavior_id: str,
    emotion_tag: str | None = None,
    body_action_id: str | None = None,
    emotion_intensity: float | None = None,
    duration_s: float | None = None,
    talk_state: str | None = None,
    source: str | None = None,
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    """Submit one high-level avatar behavior to the Edge-owned actuator."""

    payload: dict[str, Any] = {"behavior_id": str(behavior_id or "").strip().lower()}
    for key, value, normalizer in (
        ("emotion_tag", emotion_tag, lambda item: str(item).strip().upper()),
        ("body_action_id", body_action_id, lambda item: str(item).strip().lower()),
        ("talk_state", talk_state, lambda item: str(item).strip().lower()),
        ("source", source, lambda item: str(item).strip()),
    ):
        if value is not None and str(value).strip():
            payload[key] = normalizer(value)
    if emotion_intensity is not None:
        payload["emotion_intensity"] = float(emotion_intensity)
    if duration_s is not None:
        payload["duration_s"] = float(duration_s)
    return await submit_avatar_command(
        action="behavior",
        payload=payload,
        runtime=runtime,
        command_port=command_port,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
        **meta,
    )


def queue_mateengine_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    outbox: EmbodimentOutboxPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentOutboxAck:
    return queue_embodiment_command(
        build_host_embodiment_command(
            kind=EMBODIMENT_KIND_MATEENGINE,
            action=action,
            payload=payload,
            **meta,
        ),
        runtime=runtime,
        outbox=outbox,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


def queue_desktop_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    outbox: EmbodimentOutboxPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentOutboxAck:
    return queue_embodiment_command(
        build_host_embodiment_command(
            kind=EMBODIMENT_KIND_DESKTOP,
            action=action,
            payload=payload,
            **meta,
        ),
        runtime=runtime,
        outbox=outbox,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


async def submit_desktop_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_DESKTOP, action=action, payload=payload, **meta),
        runtime=runtime,
        command_port=command_port,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


def _default_idempotency_key(
    *, request_id: str, kind: str, action: str, payload: Mapping[str, Any]
) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"{request_id}:{str(kind).lower()}:{str(action).lower()}:{digest}"


__all__ = [
    "EdgeEmbodimentSessionConfig",
    "EmbodimentOutboxAck", "EmbodimentOutboxPort",
    "EmbodimentReceiptLookupUnavailable", "HostEmbodimentRuntime",
    "ReceiptObserver", "build_embodiment_receipt_audit_observer",
    "build_durable_host_embodiment_session_config",
    "build_host_embodiment_runtime_with_takeover_wait",
    "build_host_embodiment_command", "submit_embodiment_command",
    "queue_embodiment_command", "submit_voice_command", "submit_avatar_command",
    "submit_avatar_behavior",
    "queue_mateengine_command", "queue_desktop_command", "submit_desktop_command",
    "register_embodiment_receipt_observer",
]
