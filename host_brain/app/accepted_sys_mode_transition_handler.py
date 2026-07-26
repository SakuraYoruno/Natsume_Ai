"""Accepted sys-mode transition execution and exact reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from typing import Any

from host_brain.app.acceptance_effect_handler_common import _opaque_failure
from host_brain.app.accepted_sys_mode_receipt import (
    sys_mode_transition_handler_receipt_ref,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


class _AcceptedSysModeTransitionHandler:
    """Callable production handler with an explicit read-only recovery path."""

    def __init__(self, *, service_provider: Callable[[], Any]) -> None:
        if not callable(service_provider):
            raise TypeError("mode_transition_service_provider must be callable")
        self._service_provider = service_provider

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return _apply_accepted_sys_mode_transition(
            payload,
            service_provider=self._service_provider,
        )

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return _reconcile_accepted_sys_mode_transition(
            payload,
            service_provider=self._service_provider,
        )


def _accepted_sys_mode_transition_request(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str, str, str, Any]:
    """Reopen the exact immutable payload and current Preview claim proof."""

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
    return value, target_mode, reason, event_id, runtime_scope_id, source


def _apply_accepted_sys_mode_transition(
    payload: Mapping[str, Any],
    *,
    service_provider: Callable[[], Any],
) -> Mapping[str, Any]:
    (
        value,
        target_mode,
        reason,
        event_id,
        runtime_scope_id,
        source,
    ) = _accepted_sys_mode_transition_request(payload)

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
            f"for event {event_id}: {_opaque_failure(exc)}"
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
        raise RuntimeError(
            "SYS_MODE_TRANSITION_NOT_COMMITTED: " + _opaque_failure(exc)
        ) from exc
    except AcceptanceEffectOutcomeUnknown:
        raise
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "sys_mode transition requires exact HardState reconciliation "
            f"for event {event_id}: {_opaque_failure(exc)}"
        ) from exc


def _reconcile_accepted_sys_mode_transition(
    payload: Mapping[str, Any],
    *,
    service_provider: Callable[[], Any],
) -> Mapping[str, Any]:
    """Read back one exact HardState event without invoking ``switch_mode``."""

    (
        value,
        target_mode,
        reason,
        event_id,
        runtime_scope_id,
        source,
    ) = _accepted_sys_mode_transition_request(payload)
    try:
        service = service_provider()
        reopen = getattr(service, "reopen_transition_commit", None)
        if not callable(reopen):
            raise RuntimeError("HARD_STATE_EXACT_HISTORY_UNAVAILABLE")
        commit = reopen(
            event_id=event_id,
            target_mode=target_mode,
            source=source.value,
            reason=reason,
        )
        if commit is None:
            raise LookupError("exact HardState transition is absent")
        event = getattr(commit, "event", None)
        snapshot = getattr(commit, "snapshot", None)
        event_digest = str(getattr(event, "event_digest", "") or "").strip()
        snapshot_digest = str(
            getattr(snapshot, "event_digest", "") or ""
        ).strip()
        event_revision = int(getattr(event, "revision", 0) or 0)
        snapshot_revision = int(getattr(snapshot, "revision", 0) or 0)
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
            or event_revision <= 0
            or not event_digest.startswith("sha256:")
            or len(event_digest) != 71
            or str(getattr(snapshot, "runtime_scope_id", "") or "").strip()
            != runtime_scope_id
            or snapshot_revision < event_revision
            or not snapshot_digest.startswith("sha256:")
            or len(snapshot_digest) != 71
        ):
            raise RuntimeError("SYS_MODE_TRANSITION_DURABLE_RECEIPT_INVALID")
        return {
            "success": True,
            "error": None,
            "from_mode": getattr(event, "from_mode", None),
            "to_mode": getattr(event, "to_mode", None),
            "event_id": getattr(event, "event_id", None),
            "ts_ms": getattr(event, "transition_ts_ms", None),
            "revision": event_revision,
            "event_digest": event_digest,
            "exact_replay": True,
            "no_change": False,
            "transition_committed": True,
            "current_mode": getattr(snapshot, "sys_mode", None),
            "current_revision": snapshot_revision,
            "current_event_digest": snapshot_digest,
            "status": "REPLAYED",
            "runtime_scope_id": runtime_scope_id,
            "receipt_ref": sys_mode_transition_handler_receipt_ref(
                value,
                outcome_status="REPLAYED",
            ),
        }
    except AcceptanceEffectOutcomeUnknown:
        raise
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "sys_mode transition requires read-only exact HardState reconciliation "
            f"for event {event_id}: {_opaque_failure(exc)}"
        ) from exc


class _SysModeTransitionDefinitelyNotCommitted(RuntimeError):
    """Exact HardState history authoritatively proved the event is absent."""

__all__ = ["AcceptedSysModeTransitionHandler"]

AcceptedSysModeTransitionHandler = _AcceptedSysModeTransitionHandler
