"""Production handlers for committed governed Acceptance Effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
import hashlib
import json
from typing import Any

from host_brain.digital_life.application_boundary import submit_life_command
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


def build_production_acceptance_effect_handlers(
    *,
    mode_transition_service_provider: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    handlers: dict[str, Any] = {
        "conversation.user_turn": _record_accepted_user_turn,
        "life.foreground_completed": _submit_accepted_life_foreground,
        "surface.reply_delivery": _deliver_accepted_surface_reply,
    }
    if mode_transition_service_provider is not None:
        handlers["host.sys_mode_transition"] = partial(
            _apply_accepted_sys_mode_transition,
            service_provider=mode_transition_service_provider,
        )
    return handlers


def _apply_accepted_sys_mode_transition(
    payload: Mapping[str, Any],
    *,
    service_provider: Callable[[], Any],
) -> Mapping[str, Any]:
    from host_brain.core.mode_transition import TransitionSource  # noqa: PLC0415
    from host_brain.runtime.action_preview_proof import (  # noqa: PLC0415
        ActionPreviewExecutionProof,
    )

    value = dict(payload or {})
    terminal_proof = value.pop("_action_preview_terminal_proof", None)
    if not isinstance(terminal_proof, ActionPreviewExecutionProof):
        raise ValueError(
            "accepted sys_mode transition lacks typed terminal ActionPreview proof"
        )
    required = {
        "operator_command_id",
        "request_id",
        "target_mode",
        "source",
        "reason",
        "event_id",
        "runtime_scope_id",
    }
    if set(value) != required or value.get("operator_command_id") != "sys_mode.switch":
        raise ValueError("accepted sys_mode transition payload drifted")
    target_mode = str(value.get("target_mode") or "").strip().lower()
    reason = str(value.get("reason") or "").strip()
    event_id = str(value.get("event_id") or "").strip()
    request_id = str(value.get("request_id") or "").strip()
    runtime_scope_id = str(value.get("runtime_scope_id") or "").strip()
    try:
        source = TransitionSource(str(value.get("source") or "").strip().lower())
    except ValueError as exc:
        raise ValueError("accepted sys_mode transition source is invalid") from exc
    if (
        not target_mode
        or not reason
        or not event_id
        or not request_id
        or not runtime_scope_id
    ):
        raise ValueError("accepted sys_mode transition identity is incomplete")

    boundary = terminal_proof.boundary_dict()
    authority_context = boundary.get("authority_context")
    action_scope = str(boundary.get("action_scope") or "").strip()
    if (
        terminal_proof.action_kind != "acceptance-effect.host.sys_mode_transition"
        or int(boundary.get("risk_level") or 0) != 4
        or terminal_proof.action_args != value
        or not isinstance(authority_context, Mapping)
    ):
        raise ValueError("accepted sys_mode transition Preview boundary drifted")
    exact_authority = dict(authority_context)
    if (
        frozenset(exact_authority)
        != {
            "schema_version",
            "effect_id",
            "lease_owner_id",
            "fencing_token",
        }
        or exact_authority.get("schema_version")
        != "Natsume.action_preview.acceptance_effect_authority_request.v1"
        or not action_scope
        or str(exact_authority.get("effect_id") or "").strip() != action_scope
        or not str(exact_authority.get("lease_owner_id") or "").strip()
        or isinstance(exact_authority.get("fencing_token"), bool)
        or int(exact_authority.get("fencing_token") or 0) <= 0
    ):
        raise ValueError("accepted sys_mode transition Preview authority drifted")

    reopened = terminal_proof.service.require_execution(
        event_id=terminal_proof.event_id,
        **boundary,
    )
    preview_payload = getattr(reopened, "payload", None)
    expected_args_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(preview_payload, Mapping)
        or preview_payload.get("authority_kind") != "ACCEPTANCE_EFFECT"
        or preview_payload.get("action_kind")
        != "acceptance-effect.host.sys_mode_transition"
        or str(preview_payload.get("action_scope") or "").strip() != action_scope
        or int(preview_payload.get("risk_level") or 0) != 4
        or str(preview_payload.get("action_args_digest") or "").lower()
        != expected_args_digest
    ):
        raise ValueError("accepted sys_mode transition Preview receipt drifted")

    service = service_provider()
    switch_mode = getattr(service, "switch_mode", None)
    if not callable(switch_mode):
        raise RuntimeError("APP_LOCAL_MODE_TRANSITION_SERVICE_UNAVAILABLE")
    try:
        result = switch_mode(
            target_mode,
            source=source,
            reason=reason,
            event_id=event_id,
        )
    except Exception as exc:
        # Once the side-effect port has been invoked, an exception cannot
        # prove that the durable HardState transaction did not commit before
        # the response was lost.  Only the exact event identity may settle it.
        raise AcceptanceEffectOutcomeUnknown(
            "sys_mode transition invocation lost its durable attribution "
            f"for event {event_id}: {type(exc).__name__}: {str(exc)[:1200]}"
        ) from exc
    projected = dict(result or {}) if isinstance(result, Mapping) else {}
    reported_failure = projected.get("success") is not True
    try:
        reopen = getattr(service, "reopen_transition_commit", None)
        if not callable(reopen):
            raise RuntimeError("HARD_STATE_EXACT_HISTORY_UNAVAILABLE")
        commit = reopen(
            event_id=event_id,
            target_mode=target_mode,
            source=source.value,
            reason=reason,
        )
        # A negative service projection is not proof that the durable write
        # failed.  The lower authority may have committed before its response
        # was lost or rejected by an upper projection check.  Only an exact,
        # successfully completed history query returning ``None`` proves that
        # this immutable event identity was not committed.
        if reported_failure and commit is None:
            raise _SysModeTransitionDefinitelyNotCommitted(
                str(projected.get("error") or "SYS_MODE_TRANSITION_NOT_COMMITTED")
            )
        if reported_failure:
            status = "REPLAYED"
            event = getattr(commit, "event", None)
            snapshot = getattr(commit, "snapshot", None)
            projected = {
                **projected,
                "success": True,
                "error": None,
                "from_mode": getattr(event, "from_mode", None),
                "to_mode": getattr(event, "to_mode", None),
                "event_id": getattr(event, "event_id", None),
                "ts_ms": getattr(event, "transition_ts_ms", None),
                "revision": getattr(event, "revision", None),
                "event_digest": getattr(event, "event_digest", None),
                "exact_replay": True,
                "no_change": False,
                "transition_committed": True,
                "current_mode": getattr(snapshot, "sys_mode", None),
                "current_revision": getattr(snapshot, "revision", None),
                "current_event_digest": getattr(snapshot, "event_digest", None),
            }
        else:
            if (
                str(projected.get("to_mode") or "").strip().lower() != target_mode
                or (
                    not bool(projected.get("no_change"))
                    and str(projected.get("event_id") or "").strip() != event_id
                )
            ):
                raise RuntimeError("SYS_MODE_TRANSITION_RECEIPT_IDENTITY_INVALID")
            status = (
                "NO_CHANGE"
                if bool(projected.get("no_change"))
                else "REPLAYED"
                if bool(projected.get("exact_replay"))
                else "APPLIED"
            )
        if status == "NO_CHANGE":
            if commit is not None:
                raise RuntimeError("SYS_MODE_NO_CHANGE_RECEIPT_CONFLICT")
        else:
            event = getattr(commit, "event", None)
            event_digest = str(getattr(event, "event_digest", "") or "").strip()
            if (
                getattr(commit, "exact_replay", None) is not True
                or str(getattr(event, "event_id", "") or "").strip() != event_id
                or str(getattr(event, "runtime_scope_id", "") or "").strip()
                != runtime_scope_id
                or str(getattr(event, "to_mode", "") or "").strip().lower()
                != target_mode
                or str(getattr(event, "source", "") or "").strip().lower()
                != source.value
                or str(getattr(event, "reason", "") or "") != reason
                or not event_digest.startswith("sha256:")
                or len(event_digest) != 71
            ):
                raise RuntimeError("SYS_MODE_TRANSITION_DURABLE_RECEIPT_INVALID")
        return {
            **projected,
            "status": status,
            "runtime_scope_id": runtime_scope_id,
            "receipt_ref": sys_mode_transition_handler_receipt_ref(
                value,
                outcome_status=status,
            ),
        }
    except _SysModeTransitionDefinitelyNotCommitted as exc:
        raise RuntimeError(str(exc)) from exc
    except AcceptanceEffectOutcomeUnknown:
        raise
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "sys_mode transition requires exact HardState reconciliation "
            f"for event {event_id}: {type(exc).__name__}: {str(exc)[:1200]}"
        ) from exc


class _SysModeTransitionDefinitelyNotCommitted(RuntimeError):
    """Exact HardState history authoritatively proved the event is absent."""


def sys_mode_transition_handler_receipt_ref(
    payload: Mapping[str, Any],
    *,
    outcome_status: str,
) -> str:
    """Return the canonical handler receipt for one frozen mode effect."""

    value = dict(payload or {})
    value.pop("_action_preview_context", None)
    value.pop("_action_preview_terminal_proof", None)
    required = {
        "operator_command_id",
        "request_id",
        "target_mode",
        "source",
        "reason",
        "event_id",
        "runtime_scope_id",
    }
    if set(value) != required:
        raise ValueError("sys_mode transition receipt payload drifted")
    status = str(outcome_status or "").strip().upper()
    if status not in {"APPLIED", "REPLAYED", "NO_CHANGE"}:
        raise ValueError("sys_mode transition outcome status is invalid")
    return _receipt(
        f"host-sys-mode-{status.lower().replace('_', '-')}",
        {**value, "handler_outcome_status": status},
    )


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


__all__ = [
    "build_production_acceptance_effect_handlers",
    "sys_mode_transition_handler_receipt_ref",
]
