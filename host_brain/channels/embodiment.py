"""Host-side command authoring port for all physical embodiment effects.

This module may send a versioned command to Edge.  It must never import device
drivers or perform MateEngine/VTS/voice/desktop effects itself.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
import threading
from typing import Any, Mapping

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
_port_override: EmbodimentCommandPort | None = None
_pending_tasks: set[asyncio.Task[EmbodimentReceipt]] = set()
_transport_lease: EdgeSessionLease | None = None
_transport_lease_state_lock = threading.Lock()


class _TransportLeaseUnavailable(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "EDGE_EMBODIMENT_LEASE_UNAVAILABLE")
        self.detail = str(detail or "edge embodiment lease unavailable")[:300]
        super().__init__(self.reason_code)


class EdgeRpcEmbodimentCommandPort:
    async def submit(self, command: EmbodimentCommand) -> EmbodimentReceipt:
        from host_brain.channels.edge_rpc_client import _post_to_edge  # noqa: PLC0415

        try:
            lease = await _get_transport_lease(_post_to_edge)
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
        result = await _post_to_edge(
            path="/v1/edge/embodiment",
            payload=leased_command.as_dict(),
            request_id=leased_command.request_id,
            request_timeout_ms=max(1000, leased_command.deadline_ms - now_ms()),
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
            request_timeout_ms=5_000,
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
                    request_timeout_ms=5_000,
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


def configure_embodiment_command_port(port: EmbodimentCommandPort | None) -> None:
    global _port_override
    _port_override = port


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


async def submit_embodiment_command(command: EmbodimentCommand) -> EmbodimentReceipt:
    port = _port_override or EdgeRpcEmbodimentCommandPort()
    return await port.submit(command)


def queue_embodiment_command(command: EmbodimentCommand) -> EmbodimentReceipt:
    """Submit from synchronous Host call sites without executing a device effect."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(submit_embodiment_command(command))
    task = loop.create_task(submit_embodiment_command(command))
    _pending_tasks.add(task)
    task.add_done_callback(_on_submission_done)
    return EmbodimentReceipt(
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
        accepted=True,
        status="QUEUED",
        reason_code=None,
        detail="embodiment command queued for Edge authority",
        received_at_ms=now_ms(),
        event={"request_id": command.request_id, "kind": command.kind, "action": command.action},
    )


async def submit_voice_command(*, action: str = "speak", payload: Mapping[str, Any], **meta: Any) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_VOICE, action=action, payload=payload, **meta)
    )


async def submit_avatar_command(*, action: str, payload: Mapping[str, Any], **meta: Any) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_AVATAR, action=action, payload=payload, **meta)
    )


def queue_mateengine_command(*, action: str, payload: Mapping[str, Any], **meta: Any) -> EmbodimentReceipt:
    return queue_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_MATEENGINE, action=action, payload=payload, **meta)
    )


def queue_desktop_command(*, action: str, payload: Mapping[str, Any], **meta: Any) -> EmbodimentReceipt:
    return queue_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_DESKTOP, action=action, payload=payload, **meta)
    )


async def submit_desktop_command(*, action: str, payload: Mapping[str, Any], **meta: Any) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_DESKTOP, action=action, payload=payload, **meta)
    )


def _default_idempotency_key(
    *, request_id: str, kind: str, action: str, payload: Mapping[str, Any]
) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"{request_id}:{str(kind).lower()}:{str(action).lower()}:{digest}"


def _on_submission_done(task: asyncio.Task[EmbodimentReceipt]) -> None:
    _pending_tasks.discard(task)
    try:
        receipt = task.result()
    except Exception as exc:  # pragma: no cover - fire-and-forget observer
        _logger.warning("Edge embodiment command submission failed: %s", exc)
        return
    if not receipt.accepted:
        _logger.warning(
            "Edge rejected embodiment command command_id=%s reason=%s",
            receipt.command_id,
            receipt.reason_code,
        )


__all__ = [
    "EdgeRpcEmbodimentCommandPort", "configure_embodiment_command_port",
    "build_host_embodiment_command", "submit_embodiment_command",
    "queue_embodiment_command", "submit_voice_command", "submit_avatar_command",
    "queue_mateengine_command", "queue_desktop_command", "submit_desktop_command",
    "reset_embodiment_transport_session",
]
