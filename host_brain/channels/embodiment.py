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
from typing import Any, Callable, Mapping, Protocol

from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_KIND_AVATAR,
    EMBODIMENT_KIND_DESKTOP,
    EMBODIMENT_KIND_MATEENGINE,
    EMBODIMENT_KIND_VOICE,
    EmbodimentCommand,
    EmbodimentCommandPort,
    EmbodimentReceipt,
    build_embodiment_command,
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
_durable_outbox_authority: "EmbodimentOutboxPort | None" = None
_transport_lease: EdgeSessionLease | None = None
_transport_lease_state_lock = threading.Lock()
_receipt_observers: set[
    Callable[[EmbodimentCommand, EmbodimentReceipt], None]
] = set()
_receipt_observers_lock = threading.Lock()


class _TransportLeaseUnavailable(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "EDGE_EMBODIMENT_LEASE_UNAVAILABLE")
        self.detail = str(detail or "edge embodiment lease unavailable")[:300]
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
    def success(self) -> bool:
        return self.status == "PENDING"

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
            "event": self.event,
        }


class EmbodimentOutboxPort(Protocol):
    def enqueue(self, command: EmbodimentCommand) -> EmbodimentOutboxAck: ...


class EdgeRpcEmbodimentCommandPort:
    async def submit(self, command: EmbodimentCommand) -> EmbodimentReceipt:
        from host_brain.channels.edge_transport import submit_edge_request  # noqa: PLC0415

        try:
            lease = await _get_transport_lease(submit_edge_request)
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
        result = await submit_edge_request(
            path="/v1/edge/embodiment",
            payload=leased_command.as_dict(),
            request_id=leased_command.request_id,
            timeout_ms=max(1000, leased_command.deadline_ms - now_ms()),
        )
        event = dict(result.event or {})
        receipt_payload = event.get("receipt")
        if isinstance(receipt_payload, Mapping):
            return parse_embodiment_receipt(receipt_payload)
        return EmbodimentReceipt(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            accepted=bool(result.success),
            status=str(result.status or "REJECTED"),
            reason_code=result.reason_code,
            detail=str(result.detail or ""),
            received_at_ms=command.issued_at_ms,
            completed_at_ms=now_ms(),
            event=event,
        )


async def _get_transport_lease(post_to_edge: Any) -> EdgeSessionLease:
    global _transport_lease
    now = now_ms()
    cached = _transport_lease
    if cached is not None and cached.expires_at_ms - now > 5_000:
        return cached

    async with _hold_transport_lease_lock():
        now = now_ms()
        cached = _transport_lease
        if cached is not None and cached.expires_at_ms - now > 5_000:
            return cached
        session_id = str(
            os.environ.get("HOST_EDGE_EMBODIMENT_SESSION_ID") or "host-brain-primary"
        ).strip()
        owner_id = str(
            os.environ.get("HOST_EDGE_EMBODIMENT_OWNER_ID") or "host-brain"
        ).strip()
        try:
            ttl_ms = max(
                10_000,
                min(300_000, int(os.environ.get("HOST_EDGE_EMBODIMENT_LEASE_TTL_MS") or 30_000)),
            )
        except (TypeError, ValueError):
            ttl_ms = 30_000
        payload: dict[str, Any] = {
            "session_id": session_id,
            "owner_id": owner_id,
            "ttl_ms": ttl_ms,
        }
        if cached is not None and cached.expires_at_ms > now:
            payload.update(
                {
                    "reconnect_lease_id": cached.lease_id,
                    "reconnect_fencing_token": cached.fencing_token,
                }
            )
        result = await post_to_edge(
            path="/v1/edge/embodiment/session",
            payload=payload,
            request_id=new_request_id(),
            timeout_ms=5_000,
        )
        if not bool(getattr(result, "success", False)):
            reason = str(getattr(result, "reason_code", "") or "EDGE_EMBODIMENT_LEASE_UNAVAILABLE")
            # Edge may have restarted with a new durable store.  One fresh
            # handshake is safe because no command has crossed the boundary.
            if cached is not None and reason == EDGE_SESSION_RECONNECT_INVALID:
                result = await post_to_edge(
                    path="/v1/edge/embodiment/session",
                    payload={"session_id": session_id, "owner_id": owner_id, "ttl_ms": ttl_ms},
                    request_id=new_request_id(),
                    timeout_ms=5_000,
                )
            if not bool(getattr(result, "success", False)):
                raise _TransportLeaseUnavailable(
                    str(getattr(result, "reason_code", "") or reason),
                    str(getattr(result, "detail", "") or "Edge session handshake rejected"),
                )
        event = dict(getattr(result, "event", {}) or {})
        try:
            lease = parse_edge_session_lease(event.get("lease"))
        except (TypeError, ValueError) as exc:
            raise _TransportLeaseUnavailable(
                "EDGE_EMBODIMENT_LEASE_INVALID",
                "Edge session handshake returned an invalid lease",
            ) from exc
        if lease.session_id != session_id or lease.owner_id != owner_id:
            raise _TransportLeaseUnavailable(
                "EDGE_EMBODIMENT_LEASE_INVALID",
                "Edge session handshake authority mismatch",
            )
        _transport_lease = lease
        return lease


@asynccontextmanager
async def _hold_transport_lease_lock():
    """Serialize handshakes across event loops without blocking an event loop."""

    acquire_task = asyncio.create_task(
        asyncio.to_thread(_transport_lease_state_lock.acquire)
    )
    try:
        try:
            acquired = bool(await asyncio.shield(acquire_task))
        except asyncio.CancelledError:
            acquired = bool(await acquire_task)
            if acquired:
                _transport_lease_state_lock.release()
            raise
        if not acquired:  # pragma: no cover - threading.Lock always returns True
            raise _TransportLeaseUnavailable(
                "EDGE_EMBODIMENT_LEASE_LOCK_FAILED",
                "failed to serialize Edge session handshake",
            )
        try:
            yield
        finally:
            _transport_lease_state_lock.release()
    finally:
        if not acquire_task.done():  # pragma: no cover - cancellation cleanup
            acquire_task.cancel()


def reset_embodiment_transport_session() -> None:
    """Drop only the local cache; the Edge durable lease remains authoritative."""

    global _transport_lease
    _transport_lease = None


def bind_embodiment_outbox_authority(
    port: EmbodimentOutboxPort,
) -> Callable[[], None]:
    """Bind the single durable writer for one application lifecycle."""

    global _durable_outbox_authority
    if port is None or not callable(getattr(port, "enqueue", None)):
        raise TypeError("embodiment outbox authority must expose enqueue(command)")
    if _durable_outbox_authority is not None:
        raise RuntimeError("EMBODIMENT_OUTBOX_AUTHORITY_ALREADY_BOUND")
    _durable_outbox_authority = port

    def unbind() -> None:
        global _durable_outbox_authority
        if _durable_outbox_authority is port:
            _durable_outbox_authority = None

    return unbind


def register_embodiment_receipt_observer(
    observer: Callable[[EmbodimentCommand, EmbodimentReceipt], None],
) -> Callable[[], None]:
    """Observe completed transport receipts without gaining command authority.

    Observers are deliberately synchronous, best-effort projections.  They
    cannot alter the receipt returned to the caller and are never consulted
    before a command is submitted.
    """

    if not callable(observer):
        raise TypeError("embodiment receipt observer must be callable")
    with _receipt_observers_lock:
        _receipt_observers.add(observer)

    def unsubscribe() -> None:
        with _receipt_observers_lock:
            _receipt_observers.discard(observer)

    return unsubscribe


def _publish_embodiment_receipt(
    command: EmbodimentCommand,
    receipt: EmbodimentReceipt,
) -> None:
    with _receipt_observers_lock:
        observers = tuple(_receipt_observers)
    for observer in observers:
        try:
            observer(command, receipt)
        except Exception as exc:  # pragma: no cover - observer isolation
            _logger.warning("Embodiment receipt observer failed: %s", exc)


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
    command_port: EmbodimentCommandPort | None = None,
) -> EmbodimentReceipt:
    port = command_port or EdgeRpcEmbodimentCommandPort()
    receipt = await port.submit(command)
    _publish_embodiment_receipt(command, receipt)
    return receipt


def queue_embodiment_command(command: EmbodimentCommand) -> EmbodimentOutboxAck:
    """Commit from synchronous Host call sites to the canonical durable outbox."""

    if _durable_outbox_authority is None:
        raise RuntimeError("EMBODIMENT_OUTBOX_NOT_CONFIGURED")
    return _durable_outbox_authority.enqueue(command)


async def submit_voice_command(
    *,
    action: str = "speak",
    payload: Mapping[str, Any],
    command_port: EmbodimentCommandPort | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_VOICE, action=action, payload=payload, **meta),
        command_port=command_port,
    )


async def submit_avatar_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    command_port: EmbodimentCommandPort | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_AVATAR, action=action, payload=payload, **meta),
        command_port=command_port,
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
    return await submit_avatar_command(action="behavior", payload=payload, **meta)


def queue_mateengine_command(*, action: str, payload: Mapping[str, Any], **meta: Any) -> EmbodimentReceipt:
    return queue_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_MATEENGINE, action=action, payload=payload, **meta)
    )


def queue_desktop_command(*, action: str, payload: Mapping[str, Any], **meta: Any) -> EmbodimentReceipt:
    return queue_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_DESKTOP, action=action, payload=payload, **meta)
    )


async def submit_desktop_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    command_port: EmbodimentCommandPort | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_DESKTOP, action=action, payload=payload, **meta),
        command_port=command_port,
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
    "EdgeRpcEmbodimentCommandPort", "EmbodimentOutboxAck", "EmbodimentOutboxPort",
    "bind_embodiment_outbox_authority", "build_embodiment_receipt_audit_observer",
    "build_host_embodiment_command", "submit_embodiment_command",
    "queue_embodiment_command", "submit_voice_command", "submit_avatar_command",
    "submit_avatar_behavior",
    "queue_mateengine_command", "queue_desktop_command", "submit_desktop_command",
    "reset_embodiment_transport_session", "register_embodiment_receipt_observer",
]
