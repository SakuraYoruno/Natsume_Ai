"""Production handlers for committed governed Acceptance Effects."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from host_brain.digital_life.application_boundary import submit_life_command


def build_production_acceptance_effect_handlers() -> dict[str, Any]:
    return {
        "conversation.user_turn": _record_accepted_user_turn,
        "life.foreground_completed": _submit_accepted_life_foreground,
        "surface.reply_delivery": _deliver_accepted_surface_reply,
    }


def _record_accepted_user_turn(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    from host_brain.app.agent_runtime_shared import AGENT_RUNTIME_PORTS  # noqa: PLC0415

    value = dict(payload or {})
    value.pop("_action_preview_terminal_proof", None)
    result = AGENT_RUNTIME_PORTS.record_user_turn(
        request_id=str(value.get("request_id") or ""),
        session_id=str(value.get("session_id") or ""),
        person_id=str(value.get("person_id") or ""),
        content=str(value.get("content") or ""),
        ts_ms=int(value.get("ts_ms") or 0),
        lang=str(value.get("lang") or "zh"),
        metadata=dict(value.get("metadata") or {}),
    )
    projected = dict(result or {})
    status = str(projected.get("status") or "").strip().upper()
    reason_code = str(projected.get("reason_code") or "").strip().upper()
    if status not in {"OK", "SKIPPED"}:
        raise RuntimeError(
            (reason_code or "CONVERSATION_USER_TURN_FAILED")
            + ": accepted user turn was not durably recorded"
        )
    if status == "SKIPPED" and reason_code not in {
        "DUPLICATE_CONVERSATION_TURN",
        "DUPLICATE_COMMAND",
    }:
        raise RuntimeError(
            (reason_code or "CONVERSATION_USER_TURN_SKIPPED")
            + ": accepted user turn was unexpectedly skipped"
        )
    return {
        "receipt_ref": _receipt("conversation-user-turn", value),
        "status": "APPLIED" if status == "OK" else "REPLAYED",
    }


def _submit_accepted_life_foreground(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    from host_brain.composition.life import PROCESS_LIFE_PROVIDER  # noqa: PLC0415

    value = dict(payload or {})
    value.pop("_action_preview_terminal_proof", None)
    life = PROCESS_LIFE_PROVIDER.require()
    receipt = submit_life_command(
        service=life.command_service,
        reader=life.snapshot_reader,
        command_type=str(value.get("command_type") or ""),
        person_id=str(value.get("person_id") or ""),
        command_id=str(value.get("command_id") or ""),
        correlation_id=str(value.get("correlation_id") or "") or None,
        payload=dict(value.get("payload") or {}),
        created_at_ms=int(value.get("created_at_ms") or 0),
        metadata=dict(value.get("metadata") or {}),
    )
    projected = dict(receipt or {})
    status = str(projected.get("status") or "").strip().upper()
    if status != "APPLIED":
        raise RuntimeError(
            str(projected.get("reason_code") or "LIFE_FOREGROUND_NOT_APPLIED")
            + ": accepted foreground command has no applied Life receipt"
        )
    projected["receipt_ref"] = _receipt("life-foreground", value)
    return projected


async def _deliver_accepted_surface_reply(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    from host_brain.channels.edge_transport import (  # noqa: PLC0415
        submit_gateway_reply,
    )

    value = dict(payload or {})
    terminal_proof = value.pop("_action_preview_terminal_proof", None)
    if not isinstance(terminal_proof, Mapping):
        raise ValueError("accepted delivery lacks terminal ActionPreview proof")
    channel = str(value.get("reply_channel") or "").strip()
    target = str(value.get("reply_target") or "").strip()
    request_id = str(value.get("request_id") or "").strip()
    if not request_id or not channel or not target:
        raise ValueError(
            "accepted delivery requires request_id, reply_channel and reply_target"
        )
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
        raise RuntimeError(
            str(projected.get("reason_code") or "SURFACE_REPLY_DELIVERY_FAILED")
            + ": "
            + str(projected.get("detail") or "delivery failed")
        )
    return {
        **projected,
        "receipt_ref": _receipt("surface-reply-delivery", value),
    }


def _receipt(kind: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{kind}://sha256/{digest}"


__all__ = ["build_production_acceptance_effect_handlers"]
