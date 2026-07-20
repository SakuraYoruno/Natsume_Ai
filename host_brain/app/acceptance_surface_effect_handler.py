"""Gateway Surface reply Acceptance Effect handler and read-only reconciler."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import inspect
import json
import re
from typing import Any

from host_brain.app.acceptance_effect_handler_common import (
    _canonical_id_ref,
    _is_exact_effect_identity,
    _opaque_failure,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


class _AcceptedSurfaceNoEffectFailure(RuntimeError):
    """A trusted Gateway response explicitly attested zero external effects."""


class _AcceptedSurfaceReplyDeliveryHandler:
    """Submit once, then recover exclusively through exact Gateway reads."""

    def __init__(self, *, query_port: Callable[..., Any] | None) -> None:
        if query_port is not None and not callable(query_port):
            raise TypeError("gateway_reply_query_port must be callable")
        self._query_port = query_port

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        # Reject request/proof drift before entering the transport.  Once the
        # transport has been invoked, any non-terminal exception may have been
        # raised after Gateway intake and therefore cannot be retried safely.
        _accepted_surface_invocation(payload)
        try:
            return await _deliver_accepted_surface_reply(payload)
        except _AcceptedSurfaceNoEffectFailure:
            # The authenticated transport explicitly proved that it did not
            # enqueue or send anything.  Only this narrow result is safe for
            # the dispatcher's ordinary failure/retry policy.
            raise
        except Exception as exc:
            try:
                return await self.reconcile(payload)
            except _AcceptedSurfaceNoEffectFailure:
                raise
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "surface reply invocation crossed the transport boundary "
                    "without exact Gateway attribution after "
                    f"{_opaque_failure(exc)}; {reconciliation.detail}"
                ) from exc

    async def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value = _accepted_surface_request(payload)
        if self._query_port is None:
            raise AcceptanceEffectOutcomeUnknown(
                "surface reply delivery cannot be read-only reconciled: the Host "
                "has no authenticated Gateway outbox query port for delivery "
                f"identity {_surface_delivery_id(value)}"
            )
        value, terminal_proof = _accepted_surface_invocation(payload)
        effect_id = _surface_effect_id(
            value=value,
            terminal_proof=terminal_proof,
            reopen_preview=True,
        )
        delivery_id = _surface_delivery_id(value)
        payload_digest = _surface_gateway_payload_digest(
            value,
            effect_id=effect_id,
        )
        try:
            result = self._query_port(
                effect_id=effect_id,
                delivery_id=delivery_id,
                platform=value["reply_channel"],
                conversation_id=value["reply_target"],
                payload_digest=payload_digest,
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "authenticated Gateway reply query is unavailable after "
                f"{_opaque_failure(exc)}"
            ) from exc
        return _validated_surface_query_result(
            value=value,
            effect_id=effect_id,
            result=result,
        )


async def _deliver_accepted_surface_reply(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    from host_brain.channels.edge_transport import (  # noqa: PLC0415
        submit_gateway_reply,
    )

    value, terminal_proof = _accepted_surface_invocation(payload)
    channel = value["reply_channel"]
    target = value["reply_target"]
    request_id = value["request_id"]
    effect_id = _surface_effect_id(
        value=value,
        terminal_proof=terminal_proof,
        reopen_preview=False,
    )
    delivery_id = _surface_delivery_id(value)
    # The Acceptance Effect binds one exact, visible text result.  Reply-form,
    # voice, avatar, media and arbitrary ``raw_extra`` transforms require
    # their own accepted effect/Preview receipts; allowing them here would
    # turn an immutable Acceptance decision into a second planning surface.
    result = await submit_gateway_reply(
        platform=channel,
        conversation_id=target,
        content=str(value.get("narrative") or ""),
        msg_type="text",
        request_id=request_id,
        effect_id=effect_id,
        wait_result=True,
        action_preview_service=terminal_proof.get("service"),
        action_preview_event_id=str(terminal_proof.get("event_id") or ""),
        action_preview_boundary=dict(terminal_proof.get("boundary") or {}),
    )
    projected = (
        dict(result.as_dict())
        if hasattr(result, "as_dict")
        else dict(result or {})
    )
    if projected.get("success") is not True:
        event = projected.get("event")
        evidence = _opaque_failure(
            RuntimeError(
                repr(
                    {
                        "status": projected.get("status"),
                        "reason_code": projected.get("reason_code"),
                        "detail": projected.get("detail"),
                        "event": event,
                    }
                )
            )
        )
        if (
            isinstance(event, Mapping)
            and event.get("external_side_effects") is False
        ):
            raise _AcceptedSurfaceNoEffectFailure(
                "Gateway explicitly attested external_side_effects=false for "
                f"{_surface_delivery_id(value)} after {evidence}"
            )
        # A 500, missing/malformed body, apparently queued response, string
        # "false", or any other non-success is not proof of no effect.  Host
        # has no authenticated outbox read port, so all remain unknown.
        raise AcceptanceEffectOutcomeUnknown(
            "Gateway reply has no trusted zero-effect attestation after the "
            f"transport boundary for {_surface_delivery_id(value)}; {evidence}"
        )
    event = projected.get("event")
    outbox_id = (
        str(event.get("outbox_id") or "").strip()
        if isinstance(event, Mapping)
        else ""
    )
    expected_delivery_id = delivery_id
    expected_payload_digest = _surface_gateway_payload_digest(
        value,
        effect_id=effect_id,
    )
    if (
        not outbox_id.startswith("gateway-outbox-")
        or not isinstance(event, Mapping)
        or str(event.get("delivery_id") or "") != expected_delivery_id
        or str(event.get("platform") or "") != channel
        or str(event.get("conversation_id") or "") != target
        or str(event.get("payload_digest") or "") != expected_payload_digest
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Gateway accepted the reply without returning an exact canonical "
            "outbox/delivery/destination/payload binding for "
            f"{expected_delivery_id}"
        )
    return {
        **projected,
        "outbox_id": outbox_id,
        "delivery_id": expected_delivery_id,
        "payload_digest": expected_payload_digest,
        "receipt_ref": _canonical_id_ref("gateway-outbox", outbox_id),
    }


def _accepted_surface_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    value.pop("_action_preview_terminal_proof", None)
    required = {
        "request_id",
        "session_id",
        "person_id",
        "reply_channel",
        "reply_target",
        "decision",
        "narrative",
        "authority",
    }
    if set(value) != required:
        raise ValueError("accepted surface delivery payload drifted")
    authority = value.get("authority")
    request_id = value.get("request_id")
    session_id = value.get("session_id")
    person_id = value.get("person_id")
    channel = value.get("reply_channel")
    target = value.get("reply_target")
    decision = value.get("decision")
    narrative = value.get("narrative")
    if (
        not _is_exact_effect_identity(request_id, limit=180)
        or not _is_exact_effect_identity(session_id, limit=180)
        or not _is_exact_effect_identity(person_id, limit=120)
        or not _is_exact_effect_identity(channel, limit=32)
        or channel != channel.lower()
        or not _is_exact_effect_identity(target, limit=240)
        or decision != "APPROVE"
        or not isinstance(narrative, str)
        or not isinstance(authority, Mapping)
        or authority.get("request_id") != request_id
    ):
        raise ValueError("accepted surface delivery identity is incomplete")
    return {
        **value,
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "reply_channel": channel,
        "reply_target": target,
        "decision": "APPROVE",
        "narrative": narrative,
        "authority": dict(authority),
    }


def _accepted_surface_invocation(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate all local input/proof structure before Gateway invocation."""

    value = _accepted_surface_request(payload)
    terminal_proof = dict(payload).get("_action_preview_terminal_proof")
    if not isinstance(terminal_proof, Mapping) or set(terminal_proof) != {
        "service",
        "event_id",
        "boundary",
    }:
        raise ValueError("accepted delivery lacks exact terminal ActionPreview proof")
    service = terminal_proof.get("service")
    event_id = str(terminal_proof.get("event_id") or "").strip()
    boundary = terminal_proof.get("boundary")
    if (
        not callable(getattr(service, "require_execution", None))
        or not event_id
        or not isinstance(boundary, Mapping)
        or not boundary
    ):
        raise ValueError("accepted delivery terminal ActionPreview proof is incomplete")
    return value, {
        "service": service,
        "event_id": event_id,
        "boundary": dict(boundary),
    }


def _surface_delivery_id(value: Mapping[str, Any]) -> str:
    material = json.dumps(
        {
            "platform": str(value.get("reply_channel") or "").strip().lower(),
            "conversation_id": str(value.get("reply_target") or "").strip(),
            "content": str(value.get("narrative") or ""),
            "msg_type": "text",
            "media_path": "",
            "raw_extra": {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "reply:" + hashlib.sha256(
        str(value.get("request_id") or "").encode("utf-8") + b"\n" + material
    ).hexdigest()


def _surface_gateway_payload_digest(
    value: Mapping[str, Any],
    *,
    effect_id: str | None = None,
) -> str:
    """Digest the exact durable payload accepted by GatewayDeliveryRuntime."""

    delivery_id = _surface_delivery_id(value)
    durable_payload = {
        "request_id": str(value.get("request_id") or ""),
        "delivery_id": delivery_id,
        "platform": str(value.get("reply_channel") or ""),
        "conversation_id": str(value.get("reply_target") or ""),
        "msg_type": "text",
        "content": str(value.get("narrative") or ""),
        "media_path": None,
        "reply_to_msg_id": None,
        "raw_extra": {},
    }
    if effect_id is not None:
        durable_payload["effect_id"] = str(effect_id)
    encoded = json.dumps(
        durable_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _surface_effect_id(
    *,
    value: Mapping[str, Any],
    terminal_proof: Mapping[str, Any],
    reopen_preview: bool,
) -> str:
    """Reopen the stable Effect id; claim owner/fence remain live authority.

    The effect id is persisted inside the Gateway payload digest.  The current
    Acceptance Effect owner/fence is checked through ActionPreview but is not
    persisted downstream, so a later recovery owner can reconcile the same
    immutable outbox after a claim takeover.
    """

    boundary = terminal_proof.get("boundary")
    if not isinstance(boundary, Mapping):
        raise AcceptanceEffectOutcomeUnknown(
            "surface reply delivery lacks an exact ActionPreview boundary"
        )
    exact = dict(boundary)
    effect_id = str(exact.get("action_scope") or "")
    authority = exact.get("authority_context")
    args = exact.get("action_args")
    valid = (
        _is_exact_effect_identity(effect_id, limit=255)
        and exact.get("action_kind")
        == "acceptance-effect.surface.reply_delivery"
        and isinstance(authority, Mapping)
        and set(authority)
        == {
            "schema_version",
            "effect_id",
            "lease_owner_id",
            "fencing_token",
        }
        and authority.get("schema_version")
        == "Natsume.action_preview.acceptance_effect_authority_request.v1"
        and authority.get("effect_id") == effect_id
        and _is_exact_effect_identity(authority.get("lease_owner_id"), limit=255)
        and isinstance(authority.get("fencing_token"), int)
        and not isinstance(authority.get("fencing_token"), bool)
        and int(authority.get("fencing_token")) >= 1
        and isinstance(args, Mapping)
        and dict(args) == dict(value)
    )
    if not valid:
        raise AcceptanceEffectOutcomeUnknown(
            "surface reply authority/effect identity drifted"
        )
    if not reopen_preview:
        # The terminal POST boundary below performs the authoritative Preview
        # reopen.  Avoid consuming or double-counting a one-shot implementation
        # while still persisting the stable effect id in the outbox digest.
        return effect_id
    try:
        terminal_proof["service"].require_execution(
            event_id=str(terminal_proof.get("event_id") or ""),
            **exact,
        )
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "surface reply query could not reopen current Preview authority "
            f"after {_opaque_failure(exc)}"
        ) from exc
    return effect_id


def _validated_surface_query_result(
    *,
    value: Mapping[str, Any],
    effect_id: str,
    result: Any,
) -> dict[str, Any]:
    """Accept only an exact, authenticated Gateway outbox projection."""

    try:
        projected = (
            dict(result.as_dict())
            if callable(getattr(result, "as_dict", None))
            else dict(result or {})
        )
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "authenticated Gateway reply query returned a malformed transport "
            f"projection after {_opaque_failure(exc)}"
        ) from exc
    event = projected.get("event")
    if projected.get("success") is not True or not isinstance(event, Mapping):
        raise AcceptanceEffectOutcomeUnknown(
            "authenticated Gateway reply query returned no trusted snapshot"
        )
    snapshot = dict(event)
    required = {
        "status",
        "schema_version",
        "effect_id",
        "delivery_id",
        "platform",
        "conversation_id",
        "payload_digest",
        "effect_status",
        "reason_code",
        "outbox_id",
        "delivery_state",
        "status_version",
        "fencing_token",
        "delivery_receipt_digest",
    }
    if set(snapshot) != required:
        raise AcceptanceEffectOutcomeUnknown(
            "authenticated Gateway reply query shape drifted"
        )
    delivery_id = _surface_delivery_id(value)
    payload_digest = _surface_gateway_payload_digest(
        value,
        effect_id=effect_id,
    )
    if (
        snapshot.get("schema_version") != "Natsume.gateway.reply-query.v1"
        or snapshot.get("effect_id") != effect_id
        or snapshot.get("delivery_id") != delivery_id
        or snapshot.get("platform") != value["reply_channel"]
        or snapshot.get("conversation_id") != value["reply_target"]
        or snapshot.get("payload_digest") != payload_digest
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "authenticated Gateway reply query identity drifted"
        )
    effect_status = str(snapshot.get("effect_status") or "")
    if effect_status != "COMMITTED":
        # NOT_FOUND, mismatches, cancellations and any future state are not a
        # negative receipt.  Never turn them into a second POST.
        raise AcceptanceEffectOutcomeUnknown(
            "Gateway reply query did not prove an exact committed outbox"
        )
    outbox_id = snapshot.get("outbox_id")
    delivery_state = snapshot.get("delivery_state")
    status_version = snapshot.get("status_version")
    fencing_token = snapshot.get("fencing_token")
    receipt_digest = snapshot.get("delivery_receipt_digest")
    if (
        snapshot.get("status") != "committed"
        or not isinstance(outbox_id, str)
        or not outbox_id.startswith("gateway-outbox-")
        or delivery_state
        not in {"READY", "CLAIMED", "RETRY_WAIT", "ACKED", "DLQ"}
        or not isinstance(status_version, int)
        or isinstance(status_version, bool)
        or status_version < 0
        or not isinstance(fencing_token, int)
        or isinstance(fencing_token, bool)
        or fencing_token < 0
        or (
            receipt_digest is not None
            and (
                not isinstance(receipt_digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", receipt_digest) is None
            )
        )
        or (delivery_state == "ACKED" and receipt_digest is None)
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Gateway reply query committed snapshot is invalid"
        )
    return {
        "success": True,
        "status": "committed",
        "effect_status": "COMMITTED",
        "outbox_id": outbox_id,
        "delivery_id": delivery_id,
        "platform": value["reply_channel"],
        "conversation_id": value["reply_target"],
        "payload_digest": payload_digest,
        "delivery_state": delivery_state,
        "status_version": status_version,
        "fencing_token": fencing_token,
        "delivery_receipt_digest": receipt_digest,
        "replayed": True,
        "receipt_ref": _canonical_id_ref("gateway-outbox", outbox_id),
    }


__all__ = [
    "_AcceptedSurfaceReplyDeliveryHandler",
    "_accepted_surface_request",
    "_deliver_accepted_surface_reply",
    "_surface_delivery_id",
    "_surface_gateway_payload_digest",
]
