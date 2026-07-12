"""Authorized transport router for committed Digital Life outbox messages."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from host_brain.digital_life.contracts import LifeOutboxMessage
from host_brain.orchestration.governor_grant import DurableGovernorGrantResolver


_DESTINATION_AUTHORITY_KINDS = {
    "edge.proactive": frozenset(
        {"scheduler_host_command", "governed_life_http"}
    ),
    "proactive.gateway": frozenset(
        {"scheduler_host_command", "governed_life_http"}
    ),
    "edge.gateway_reply": frozenset(
        {"scheduler_host_command", "governed_life_http"}
    ),
    "vts.emotion": frozenset({"surface_governor"}),
    "mateengine.state": frozenset({"surface_governor"}),
}


class LifeDeliveryTransport:
    """Dispatch one already-committed delivery with its stable idempotency key."""

    def __init__(self, *, ledger: Any) -> None:
        self._ledger = ledger
        self._grant_resolver = DurableGovernorGrantResolver(ledger)

    async def __call__(self, message: LifeOutboxMessage) -> Mapping[str, Any]:
        destination = str(message.destination or "").strip().lower()
        allowed_authorities = _DESTINATION_AUTHORITY_KINDS.get(destination)
        if allowed_authorities is None:
            raise ValueError(
                f"unsupported Life delivery destination: {destination}"
            )
        if message.authorization.authority_kind not in allowed_authorities:
            raise ValueError(
                "Life delivery authority kind is not allowed for destination: "
                f"{destination}"
            )
        payload = dict(message.payload)
        self._grant_resolver.require_delivery_bound(
            request_id=message.authorization.request_id,
            expected_ref=message.authorization.governor_grant_ref,
            authority_kind=message.authorization.authority_kind,
            authority_principal_id=message.authorization.authority_principal_id,
            person_id=message.person_id,
            destination=destination,
            payload=payload,
        )
        if destination == "edge.proactive":
            from host_brain.channels.edge_rpc_client import post_proactive_push  # noqa: PLC0415

            result = await post_proactive_push(
                message=payload,
                request_id=message.delivery_key,
            )
            return _validated_result(destination, result)
        if destination in {"proactive.gateway", "edge.gateway_reply"}:
            from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

            result = await post_gateway_reply(
                platform=str(payload.get("platform") or ""),
                conversation_id=str(payload.get("conversation_id") or ""),
                content=str(payload.get("content") or ""),
                msg_type=str(payload.get("msg_type") or "text"),
                raw_extra=dict(payload.get("raw_extra") or {}),
                request_id=message.delivery_key,
                delivery_id=message.delivery_key,
                wait_result=True,
            )
            return _validated_result(destination, result)
        if destination == "vts.emotion":
            from host_brain.channels.vts import TRIGGER_HOST, resolve_vts_expression  # noqa: PLC0415

            emotion_context = dict(payload.get("emotion_context") or {})
            target = dict(payload.get("target") or {})
            result = await asyncio.to_thread(
                resolve_vts_expression,
                emotion_tag=str(
                    emotion_context.get("emotion_tag")
                    or target.get("emotion_tag")
                    or "NEUTRAL"
                ),
                ledger=self._ledger,
                request_id=message.delivery_key,
                trigger=TRIGGER_HOST,
                ts_ms=int(payload.get("ts_ms") or 0) or None,
                edge_available=True,
            )
            return _validated_result(destination, result)
        if destination == "mateengine.state":
            from host_brain.channels.mateengine import send_mateengine_action  # noqa: PLC0415

            target = dict(payload.get("target") or {})
            result = await asyncio.to_thread(
                send_mateengine_action,
                action=str(payload.get("action") or "state"),
                state=str(
                    payload.get("state")
                    or target.get("mateengine_state")
                    or "idle"
                ),
                emotion_tag=str(
                    payload.get("emotion_tag")
                    or target.get("emotion_tag")
                    or "NEUTRAL"
                ),
                expression=str(
                    payload.get("expression")
                    or target.get("expression")
                    or ""
                ),
                request_id=message.delivery_key,
            )
            return _validated_result(destination, result)
        raise AssertionError(f"unroutable Life delivery destination: {destination}")


def _result_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    converter = getattr(value, "as_dict", None)
    if callable(converter):
        converted = converter()
        if isinstance(converted, Mapping):
            return dict(converted)
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        return dict(attributes)
    return {"value": str(value)}


def _validated_result(destination: str, value: Any) -> dict[str, Any]:
    result = _result_mapping(value)
    status = str(result.get("status") or "").strip().upper()
    if result.get("success") is False or result.get("ok") is False:
        raise RuntimeError(
            f"Life delivery failed for {destination}: "
            f"{result.get('reason_code') or result.get('error') or status}"
        )
    if status in {"FAILED", "ERROR", "REJECTED", "DEGRADED"}:
        raise RuntimeError(
            f"Life delivery failed for {destination}: "
            f"{result.get('reason_code') or result.get('error') or status}"
        )
    return result


__all__ = ["LifeDeliveryTransport"]
