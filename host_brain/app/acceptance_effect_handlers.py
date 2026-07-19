"""Production handlers for committed governed Acceptance Effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import inspect
import json
import re
import secrets
from typing import Any
import urllib.parse

from host_brain.contracts.conversation_acceptance_effects import (
    AcceptedLifeConversationProjectionV1,
    AcceptedPreferenceSignalV1,
)
from host_brain.digital_life.application_boundary import submit_life_command
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


def build_production_acceptance_effect_handlers(
    *,
    mode_transition_service_provider: Callable[[], Any] | None = None,
    scheduler_operation_handler: Callable[[Mapping[str, Any]], Any] | None = None,
    internal_result_commit_handler: Callable[[Mapping[str, Any]], Any] | None = None,
    conversation_turn_reader: Any | None = None,
    conversation_event_reader: Any | None = None,
    preference_store: Any | None = None,
    life_receipt_reader_provider: Callable[[], Any] | None = None,
    gateway_reply_query_port: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    handlers: dict[str, Any] = {
        "conversation.user_turn": _AcceptedConversationUserTurnHandler(
            turn_reader=conversation_turn_reader,
            event_reader=conversation_event_reader,
        ),
        "conversation.preference_signal": _AcceptedPreferenceSignalHandler(
            preference_store=preference_store,
        ),
        "life.conversation_turn_recorded": _AcceptedLifeConversationHandler(
            receipt_reader_provider=life_receipt_reader_provider,
        ),
        "life.foreground_completed": _AcceptedLifeForegroundHandler(
            receipt_reader_provider=life_receipt_reader_provider,
        ),
        "surface.reply_delivery": _AcceptedSurfaceReplyDeliveryHandler(
            query_port=gateway_reply_query_port,
        ),
    }
    if mode_transition_service_provider is not None:
        handlers["host.sys_mode_transition"] = _AcceptedSysModeTransitionHandler(
            service_provider=mode_transition_service_provider,
        )
    if scheduler_operation_handler is not None:
        if not callable(scheduler_operation_handler):
            raise TypeError("scheduler_operation_handler must be callable")
        handlers["scheduler.operation.execute"] = scheduler_operation_handler
    if internal_result_commit_handler is not None:
        if not callable(internal_result_commit_handler):
            raise TypeError("internal_result_commit_handler must be callable")
        handlers["internal.agent.result_commit"] = internal_result_commit_handler
    return handlers


class _AcceptedConversationUserTurnHandler:
    """Commit one visible turn and reconcile only from canonical SQLite rows."""

    def __init__(self, *, turn_reader: Any | None, event_reader: Any | None) -> None:
        self._turn_reader = turn_reader
        self._event_reader = event_reader

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        # Validate the immutable identity before crossing the writer boundary.
        _accepted_conversation_request(payload)
        try:
            invoked = _record_accepted_user_turn(payload)
            reopened = self.reconcile(payload)
            return {**reopened, "status": str(invoked["status"])}
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "conversation user-turn invocation has no exact durable "
                    f"attribution after {_opaque_failure(exc)}; "
                    f"{reconciliation.detail}"
                ) from exc

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return _reconcile_accepted_user_turn(
            payload,
            turn_reader=self._turn_reader,
            event_reader=self._event_reader,
        )


class _AcceptedPreferenceSignalHandler:
    """Commit one extraction evaluation and reopen its immutable receipt."""

    def __init__(self, *, preference_store: Any | None) -> None:
        self._preference_store = preference_store

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = AcceptedPreferenceSignalV1.from_mapping(
            _without_terminal_preview_proof(payload)
        )
        store = self._preference_store
        repository = getattr(store, "repository", None)
        if store is None or repository is None:
            raise AcceptanceEffectOutcomeUnknown(
                "preference signal has no app-local canonical store"
            )
        try:
            repository.assert_signal_effect_schema_ready(
                require_catalog=repository.db_path != ":memory:"
            )
            from host_brain.app.recommendation_feedback import (  # noqa: PLC0415
                derive_user_preference_signal_events,
            )
            from host_brain.app.user_preference_store import (  # noqa: PLC0415
                build_profile_from_events,
            )

            enabled = getattr(store, "signal_writes_enabled", None)
            if not isinstance(enabled, bool):
                raise RuntimeError("PREFERENCE_PRIVACY_GATE_UNAVAILABLE")
            metadata = request.conversation.metadata
            events = []
            if enabled:
                events = derive_user_preference_signal_events(
                    session_id=request.conversation.session_id,
                    person_id=request.conversation.person_id,
                    user_message=request.conversation.content,
                    payload={
                        **metadata,
                        "request_id": request.conversation.request_id,
                        "session_id": request.conversation.session_id,
                        "conversation_id": (
                            metadata.get("conversation_id")
                            or metadata.get("reply_target")
                            or request.conversation.session_id
                        ),
                        "platform": (
                            metadata.get("platform")
                            or metadata.get("reply_channel")
                            or ""
                        ),
                    },
                    store=store,
                    ts_ms=request.conversation.ts_ms,
                    operation_id=request.operation_id,
                )
            receipt = repository.commit_signal_effect(
                operation_id=request.operation_id,
                payload_sha256=request.payload_sha256,
                events=[store.event_to_dict(event) for event in events],
                profile_builder=build_profile_from_events,
                completed_at_ms=request.conversation.ts_ms,
                evaluation_disposition=None if enabled else "DISABLED",
            )
            return _validated_preference_effect_receipt(request, receipt)
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "preference signal invocation has no exact durable attribution "
                    f"after {_opaque_failure(exc)}; {reconciliation.detail}"
                ) from exc

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = AcceptedPreferenceSignalV1.from_mapping(
            _without_terminal_preview_proof(payload)
        )
        repository = getattr(self._preference_store, "repository", None)
        get_receipt = getattr(repository, "get_signal_effect_receipt", None)
        if not callable(get_receipt):
            raise AcceptanceEffectOutcomeUnknown(
                "preference signal reconciliation requires an app-local read port"
            )
        try:
            repository.assert_signal_effect_schema_ready(
                require_catalog=repository.db_path != ":memory:"
            )
            receipt = get_receipt(request.operation_id)
            if not isinstance(receipt, Mapping):
                raise LookupError("exact preference effect receipt is absent")
            return _validated_preference_effect_receipt(request, receipt)
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "preference signal requires exact read-only SQLite reconciliation: "
                f"{_opaque_failure(exc)}"
            ) from exc


class _AcceptedLifeConversationHandler:
    """Submit a deterministic conversation Life command after Acceptance."""

    def __init__(
        self,
        *,
        receipt_reader_provider: Callable[[], Any] | None,
    ) -> None:
        self._receipt_reader_provider = receipt_reader_provider

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = AcceptedLifeConversationProjectionV1.from_mapping(
            _without_terminal_preview_proof(payload)
        )
        try:
            return _submit_accepted_life_conversation(request)
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "Life conversation invocation has no exact durable attribution "
                    f"after {_opaque_failure(exc)}; {reconciliation.detail}"
                ) from exc

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = AcceptedLifeConversationProjectionV1.from_mapping(
            _without_terminal_preview_proof(payload)
        )
        if not callable(self._receipt_reader_provider):
            raise AcceptanceEffectOutcomeUnknown(
                "Life conversation reconciliation requires an app-local receipt reader"
            )
        try:
            reader = self._receipt_reader_provider()
            get_receipt = getattr(reader, "get_receipt", None)
            if not callable(get_receipt):
                raise RuntimeError("LIFE_COMMAND_RECEIPT_READER_UNAVAILABLE")
            receipt = get_receipt(request.command_id)
            if receipt is None:
                raise LookupError("exact Life conversation receipt is absent")
            projected = (
                dict(receipt)
                if isinstance(receipt, Mapping)
                else dict(receipt.as_dict())
                if callable(getattr(receipt, "as_dict", None))
                else None
            )
            if not isinstance(projected, Mapping):
                raise TypeError("Life conversation receipt is not mapping-compatible")
            return _validated_life_conversation_receipt(request, projected)
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "Life conversation requires exact read-only command reconciliation: "
                f"{_opaque_failure(exc)}"
            ) from exc


class _AcceptedLifeForegroundHandler:
    """Submit one Life command and reopen only its immutable command receipt."""

    def __init__(
        self,
        *,
        receipt_reader_provider: Callable[[], Any] | None,
    ) -> None:
        self._receipt_reader_provider = receipt_reader_provider

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        _accepted_life_request(payload)
        try:
            return _submit_accepted_life_foreground(payload)
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "Life foreground invocation has no exact durable attribution "
                    f"after {_opaque_failure(exc)}; "
                    f"{reconciliation.detail}"
                ) from exc

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return _reconcile_accepted_life_foreground(
            payload,
            receipt_reader_provider=self._receipt_reader_provider,
        )


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


class _AcceptedSurfaceNoEffectFailure(RuntimeError):
    """A trusted Gateway response explicitly attested zero external effects."""


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


def _accepted_conversation_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    value.pop("_action_preview_terminal_proof", None)
    expected_fields = {
        "request_id",
        "session_id",
        "person_id",
        "content",
        "ts_ms",
        "lang",
        "metadata",
    }
    if set(value) != expected_fields:
        raise ValueError("accepted conversation user-turn payload drifted")
    request_id = _exact_conversation_identity(
        value.get("request_id"), field="request_id", limit=180
    )
    session_id = _exact_conversation_identity(
        value.get("session_id"), field="session_id", limit=180
    )
    person_id = _exact_conversation_identity(
        value.get("person_id"), field="person_id", limit=120
    )
    lang = _exact_conversation_identity(value.get("lang"), field="lang", limit=32)
    content = value.get("content")
    metadata = value.get("metadata")
    if (
        not isinstance(content, str)
        or not content.strip()
        or len(content) > 4_000
        or content != re.sub(r"\s+", " ", content.strip())
        or not isinstance(metadata, Mapping)
    ):
        raise ValueError("accepted conversation user-turn identity is incomplete")
    ts_ms = value.get("ts_ms")
    if isinstance(ts_ms, bool) or not isinstance(ts_ms, int) or ts_ms < 0:
        raise ValueError("accepted conversation user-turn timestamp is invalid")
    return {
        **value,
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "content": content,
        "ts_ms": ts_ms,
        "lang": lang,
        "metadata": dict(metadata),
    }


def _record_accepted_user_turn(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    from host_brain.app.agent_runtime_shared import AGENT_RUNTIME_PORTS  # noqa: PLC0415

    value = _accepted_conversation_request(payload)
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
        "DUPLICATE_VISIBLE_TURN",
        "DUPLICATE_COMMAND",
    }:
        raise RuntimeError(
            (reason_code or "CONVERSATION_USER_TURN_SKIPPED")
            + ": accepted user turn was unexpectedly skipped"
        )
    return {
        "status": "APPLIED" if status == "OK" else "REPLAYED",
    }


def _reconcile_accepted_user_turn(
    payload: Mapping[str, Any],
    *,
    turn_reader: Any | None,
    event_reader: Any | None,
) -> Mapping[str, Any]:
    """Reopen one exact visible turn without invoking either canonical writer."""

    value = _accepted_conversation_request(payload)
    find_turn = getattr(turn_reader, "find_turn", None)
    find_event = getattr(event_reader, "find_visible_event", None)
    get_event = getattr(event_reader, "get_event", None)
    if not callable(find_turn) or not callable(find_event) or not callable(get_event):
        raise AcceptanceEffectOutcomeUnknown(
            "conversation reconciliation requires app-local ContextRepository "
            "and MemoryEventRepository read ports"
        )
    try:
        turn = find_turn(
            session_id=value["session_id"],
            request_id=value["request_id"],
            role="user",
        )
        event_head = find_event(
            session_id=value["session_id"],
            request_id=value["request_id"],
            role="user",
        )
        if not isinstance(turn, Mapping) or not isinstance(event_head, Mapping):
            raise LookupError("exact visible conversation event/turn is absent")
        event_id = str(event_head.get("event_id") or "").strip()
        event = get_event(event_id=event_id)
        if not event_id or not isinstance(event, Mapping):
            raise LookupError("exact MemoryEvent is absent")
        _validate_conversation_authority(value=value, turn=turn, event=event)
        return {
            "receipt_ref": _canonical_id_ref("memory-event", event_id),
            "memory_event_id": event_id,
            "turn_id": str(turn.get("turn_id") or ""),
            "status": "REPLAYED",
        }
    except AcceptanceEffectOutcomeUnknown:
        raise
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "conversation user-turn requires exact read-only SQLite reconciliation: "
            f"{_opaque_failure(exc)}"
        ) from exc


def _validate_conversation_authority(
    *,
    value: Mapping[str, Any],
    turn: Mapping[str, Any],
    event: Mapping[str, Any],
) -> None:
    expected_turn_id = "turn::sha256:" + hashlib.sha256(
        "\x1f".join(
            (value["session_id"], value["request_id"], "user")
        ).encode("utf-8")
    ).hexdigest()
    turn_metadata = _json_object(turn.get("metadata"), "conversation turn metadata")
    event_refs = _json_object(event.get("refs_json"), "MemoryEvent refs")
    event_projection = _json_object(
        event.get("projection_status_json"),
        "MemoryEvent projection status",
    )
    event_id = str(event.get("event_id") or "").strip()
    turn_id = str(turn.get("turn_id") or "").strip()
    stored_metadata = dict(turn_metadata)
    memory_event_id = str(stored_metadata.pop("memory_event_id", "") or "").strip()
    expected_metadata = dict(value["metadata"])
    # Soul compilation may append a stable summary identifier before the
    # canonical write.  It is allowed only as one extra field; caller-owned
    # metadata must otherwise remain byte-semantically equal.
    stored_metadata.pop("soul_id", None)
    event_metadata = event_refs.get("metadata")
    if isinstance(event_metadata, Mapping):
        normalized_event_metadata = dict(event_metadata)
        normalized_event_metadata.pop("soul_id", None)
    else:
        normalized_event_metadata = None
    conversation_projection = event_projection.get("conversation_turn")
    if (
        turn_id != expected_turn_id
        or str(turn.get("session_id") or "") != value["session_id"]
        or str(turn.get("request_id") or "") != value["request_id"]
        or str(turn.get("person_id") or "") != value["person_id"]
        or str(turn.get("role") or "") != "user"
        or str(turn.get("content") or "") != value["content"]
        or int(turn.get("ts_ms") or 0) != value["ts_ms"]
        or str(turn.get("lang") or "") != value["lang"]
        or int(turn.get("corrupted") or 0) != 0
        or stored_metadata != expected_metadata
        or memory_event_id != event_id
        or str(event.get("person_id") or "") != value["person_id"]
        or str(event.get("session_id") or "") != value["session_id"]
        or str(event.get("conversation_id") or "") != value["session_id"]
        or str(event.get("source_kind") or "") != "conversation_turn.user"
        or str(event.get("event_kind") or "") != "user_message"
        or str(event.get("visibility") or "") != "visible"
        or str(event.get("role") or "") != "user"
        or str(event.get("content") or "") != value["content"]
        or int(event.get("created_at_ms") or 0) != value["ts_ms"]
        or str(event_refs.get("request_id") or "") != value["request_id"]
        or str(event_refs.get("lang") or "") != value["lang"]
        or normalized_event_metadata != expected_metadata
        or str(event_refs.get("turn_id") or "") != turn_id
        or not isinstance(conversation_projection, Mapping)
        or str(conversation_projection.get("status") or "") != "OK"
        or str(conversation_projection.get("turn_id") or "") != turn_id
    ):
        raise RuntimeError("CONVERSATION_USER_TURN_DURABLE_RECEIPT_INVALID")


def _exact_conversation_identity(value: Any, *, field: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        raise ValueError(f"accepted conversation {field} is invalid")
    return value


def _without_terminal_preview_proof(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    value.pop("_action_preview_terminal_proof", None)
    return value


def _validated_preference_effect_receipt(
    request: AcceptedPreferenceSignalV1,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    projected = dict(receipt)
    disposition = str(projected.get("disposition") or "")
    event_bindings = projected.get("event_bindings")
    profile_receipts = projected.get("profile_receipts")
    receipt_digest = str(projected.get("receipt_digest") or "")
    if (
        projected.get("operation_id") != request.operation_id
        or projected.get("payload_sha256") != request.payload_sha256
        or disposition not in {"APPLIED", "NO_SIGNAL", "DISABLED"}
        or not isinstance(event_bindings, list)
        or not isinstance(profile_receipts, list)
        or ((disposition == "APPLIED") != bool(event_bindings))
        or (disposition == "DISABLED" and profile_receipts)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt_digest)
    ):
        raise RuntimeError("PREFERENCE_EFFECT_DURABLE_RECEIPT_INVALID")
    return {
        **projected,
        "status": disposition,
        "receipt_ref": _canonical_id_ref(
            "preference-effect",
            request.operation_id,
        ),
    }


def _submit_accepted_life_conversation(
    request: AcceptedLifeConversationProjectionV1,
) -> Mapping[str, Any]:
    from host_brain.composition.life import PROCESS_LIFE_PROVIDER  # noqa: PLC0415

    fields = _life_conversation_command_fields(request)
    life = PROCESS_LIFE_PROVIDER.require()
    receipt = submit_life_command(
        service=life.command_service,
        reader=life.snapshot_reader,
        command_type="conversation_turn_recorded",
        person_id=request.conversation.person_id,
        command_id=request.command_id,
        correlation_id=request.conversation.request_id,
        causation_id=request.conversation.turn_id,
        payload=fields["payload"],
        created_at_ms=request.conversation.ts_ms,
        metadata=fields["metadata"],
    )
    return _validated_life_conversation_receipt(request, receipt)


def _validated_life_conversation_receipt(
    request: AcceptedLifeConversationProjectionV1,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    from natsume_contracts.life import LifeCommand  # noqa: PLC0415

    projected = dict(receipt)
    fields = _life_conversation_command_fields(request)
    try:
        state_version = int(projected.get("state_version") or 0)
        created_at_ms = int(projected.get("created_at_ms") or 0)
        outbox_count = int(projected.get("outbox_count") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("LIFE_CONVERSATION_DURABLE_RECEIPT_INVALID") from exc
    candidate = LifeCommand(
        command_id=request.command_id,
        person_id=request.conversation.person_id,
        command_type="conversation_turn_recorded",
        payload=fields["payload"],
        expected_version=max(0, state_version - 1),
        created_at_ms=request.conversation.ts_ms,
        correlation_id=request.conversation.request_id,
        causation_id=request.conversation.turn_id,
        metadata=fields["metadata"],
    )
    event_id = str(projected.get("event_id") or "")
    if (
        str(projected.get("status") or "").upper() != "APPLIED"
        or projected.get("command_id") != request.command_id
        or projected.get("person_id") != request.conversation.person_id
        or projected.get("command_hash") != candidate.command_hash
        or event_id != fields["event_id"]
        or state_version <= 0
        or not str(projected.get("state_digest") or "")
        or outbox_count < 0
        or created_at_ms < request.conversation.ts_ms
    ):
        raise RuntimeError("LIFE_CONVERSATION_DURABLE_RECEIPT_INVALID")
    return {
        **projected,
        "receipt_ref": _canonical_id_ref("life-event", event_id),
    }


def _life_conversation_command_fields(
    request: AcceptedLifeConversationProjectionV1,
) -> dict[str, Any]:
    from host_brain.conversation.memory_spine import _signal_weights  # noqa: PLC0415
    from natsume_contracts.life import digest_json  # noqa: PLC0415

    conversation = request.conversation
    metadata = conversation.metadata
    source_kind = "conversation_turn.user"
    event_kind = "user_message"
    salience, valence, uncertainty = _signal_weights(
        role="user",
        source_kind=source_kind,
        content=conversation.content,
        metadata=metadata,
    )
    event_id = f"life_evt_{digest_json({'command_id': request.command_id})[:32]}"
    stable_identity = {
        "request_id": conversation.request_id,
        "session_id": conversation.session_id,
        "turn_id": conversation.turn_id,
        "turn_index": None,
        "role": "user",
        "content_sha256": hashlib.sha256(
            conversation.content[:1000].encode("utf-8")
        ).hexdigest(),
        "ts_ms": conversation.ts_ms,
    }
    evidence = {
        "memory_spine": "conversation_turn",
        "request_id": conversation.request_id,
        "turn_id": conversation.turn_id,
        "turn_index": None,
        "session_id": conversation.session_id,
        "role": "user",
        "lang": conversation.lang,
        "ts_ms": conversation.ts_ms,
        "source_kind": str(metadata.get("source_kind") or ""),
        "input_source": str(metadata.get("input_source") or ""),
        "reply_source": str(metadata.get("reply_source") or ""),
        "command_id": request.command_id,
        "idempotency_key": request.command_id,
        "canonical_life_command_id": request.command_id,
        "canonical_life_command_type": "conversation_turn_recorded",
        "canonical_idempotency_strategy": "request_id_role",
        "canonical_turn_identity": stable_identity,
    }
    semantic = {
        "person_id": conversation.person_id,
        "session_id": conversation.session_id,
        "source_kind": source_kind,
        "event_kind": event_kind,
        "content_summary": conversation.content[:1000],
        "evidence": evidence,
        "salience": salience,
        "valence": valence,
        "uncertainty": uncertainty,
        "consume": True,
    }
    experience = {
        **semantic,
        "command_id": request.command_id,
        "command_type": "conversation_turn_recorded",
        "event_id": event_id,
        "memory_event_id": "",
        "occurred_at_ms": conversation.ts_ms,
    }
    payload = {
        **semantic,
        "state_patch": {
            "continuity": {
                "last_experience_event_id": event_id,
                "last_experience_digest": digest_json(semantic),
                "last_visible_event_id": event_id,
            }
        },
        "projection_intents": [
            {"projection_id": "life.experience", "payload": experience}
        ],
        "organ_intents": [
            {"organ_id": "experience", "payload": experience}
        ],
    }
    return {
        "payload": payload,
        "event_id": event_id,
        "metadata": {
            "source": "governed_conversation_acceptance",
            "source_kind": source_kind,
            "event_kind": event_kind,
            "identity_kind": "canonical_life_command_id",
        },
    }


def _submit_accepted_life_foreground(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    from host_brain.composition.life import PROCESS_LIFE_PROVIDER  # noqa: PLC0415

    value = _accepted_life_request(payload)
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
    return _validated_life_receipt(value=value, receipt=projected, replay=False)


def _accepted_life_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    value.pop("_action_preview_terminal_proof", None)
    required = {
        "command_type",
        "person_id",
        "command_id",
        "correlation_id",
        "payload",
        "created_at_ms",
        "metadata",
    }
    if set(value) != required:
        raise ValueError("accepted Life foreground payload drifted")
    command_type = value.get("command_type")
    person_id = value.get("person_id")
    command_id = value.get("command_id")
    correlation_id = value.get("correlation_id")
    command_payload = value.get("payload")
    metadata = value.get("metadata")
    if (
        command_type != "foreground_completed"
        or not _is_exact_effect_identity(person_id, limit=120)
        or not _is_exact_effect_identity(command_id, limit=240)
        or not _is_exact_effect_identity(correlation_id, limit=180)
        or not isinstance(command_payload, Mapping)
        or not isinstance(metadata, Mapping)
    ):
        raise ValueError("accepted Life foreground identity is incomplete")
    if command_id != f"life::foreground_completed::{correlation_id}":
        raise ValueError("accepted Life foreground command identity drifted")
    created_at_ms = value.get("created_at_ms")
    if (
        isinstance(created_at_ms, bool)
        or not isinstance(created_at_ms, int)
        or created_at_ms < 0
    ):
        raise ValueError("accepted Life foreground timestamp is invalid")
    return {
        **value,
        "command_type": command_type,
        "person_id": person_id,
        "command_id": command_id,
        "correlation_id": correlation_id,
        "payload": dict(command_payload),
        "created_at_ms": created_at_ms,
        "metadata": dict(metadata),
    }


def _reconcile_accepted_life_foreground(
    payload: Mapping[str, Any],
    *,
    receipt_reader_provider: Callable[[], Any] | None,
) -> Mapping[str, Any]:
    """Read one immutable Life command receipt without submitting a command."""

    value = _accepted_life_request(payload)
    if not callable(receipt_reader_provider):
        raise AcceptanceEffectOutcomeUnknown(
            "Life foreground reconciliation requires an app-local "
            "LifeSnapshotReader provider"
        )
    try:
        reader = receipt_reader_provider()
        get_receipt = getattr(reader, "get_receipt", None)
        if not callable(get_receipt):
            raise RuntimeError("LIFE_COMMAND_RECEIPT_READER_UNAVAILABLE")
        receipt = get_receipt(value["command_id"])
        if receipt is None:
            raise LookupError("exact Life command receipt is absent")
        projected = (
            dict(receipt)
            if isinstance(receipt, Mapping)
            else dict(receipt.as_dict())
            if callable(getattr(receipt, "as_dict", None))
            else None
        )
        if not isinstance(projected, Mapping):
            raise TypeError("Life command receipt is not mapping-compatible")
        return _validated_life_receipt(
            value=value,
            receipt=projected,
            replay=True,
        )
    except AcceptanceEffectOutcomeUnknown:
        raise
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "Life foreground requires exact read-only command-receipt reconciliation: "
            f"{_opaque_failure(exc)}"
        ) from exc


def _validated_life_receipt(
    *,
    value: Mapping[str, Any],
    receipt: Mapping[str, Any],
    replay: bool,
) -> dict[str, Any]:
    from natsume_contracts.life import LifeCommand  # noqa: PLC0415

    projected = dict(receipt)
    status = str(projected.get("status") or "").strip().upper()
    event_id = str(projected.get("event_id") or "").strip()
    command_hash = str(projected.get("command_hash") or "").strip()
    state_digest = str(projected.get("state_digest") or "").strip()
    try:
        state_version = int(projected.get("state_version") or 0)
        outbox_count = int(projected.get("outbox_count") or 0)
        persisted_created_at_ms = int(projected.get("created_at_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("LIFE_FOREGROUND_DURABLE_RECEIPT_INVALID") from exc
    candidate = LifeCommand(
        command_id=str(value["command_id"]),
        person_id=str(value["person_id"]),
        command_type=str(value["command_type"]),
        payload=dict(value["payload"]),
        expected_version=max(0, state_version - 1),
        created_at_ms=int(value["created_at_ms"]),
        correlation_id=str(value["correlation_id"]),
        metadata=dict(value["metadata"]),
    )
    if (
        status != "APPLIED"
        or str(projected.get("command_id") or "") != value["command_id"]
        or str(projected.get("person_id") or "") != value["person_id"]
        or not event_id.startswith("life_evt_")
        or not command_hash
        or candidate.command_hash != command_hash
        or state_version <= 0
        or not state_digest
        or outbox_count < 0
        or persisted_created_at_ms < int(value["created_at_ms"])
    ):
        raise RuntimeError("LIFE_FOREGROUND_DURABLE_RECEIPT_INVALID")
    return {
        **projected,
        "replayed": bool(replay or projected.get("replayed")),
        # The Life event id is the canonical immutable journal identity bound
        # atomically by life_command_receipt; no synthetic URI is emitted.
        "receipt_ref": _canonical_id_ref("life-event", event_id),
    }


async def _deliver_accepted_surface_reply(payload: Mapping[str, Any]) -> Mapping[str, Any]:
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


def _is_exact_effect_identity(value: Any, *, limit: int) -> bool:
    """Return true only for an already-canonical immutable effect identity."""

    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and len(value) <= limit
        and re.search(r"[\x00-\x1f\x7f]", value) is None
    )


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


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} is not canonical JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError(f"{field_name} is not an object")
    return dict(decoded)


def _opaque_failure(exc: BaseException) -> str:
    """Return a non-secret failure identity safe for a durable effect row."""

    # Do not hash attacker/provider-controlled text: low-entropy errors and
    # secrets could be guessed offline.  Correlation uses a random incident id;
    # the full exception remains only in ephemeral exception chaining/logging.
    return f"{type(exc).__name__}[incident:{secrets.token_hex(12)}]"


def _canonical_id_ref(kind: str, identity: str) -> str:
    """Wrap one stored primary key in a reversible typed durable reference."""

    normalized_kind = str(kind or "").strip().lower()
    normalized_identity = str(identity or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", normalized_kind):
        raise ValueError("canonical reference kind is invalid")
    if not normalized_identity:
        raise ValueError("canonical reference identity is required")
    return (
        f"{normalized_kind}://id/"
        + urllib.parse.quote(normalized_identity, safe="")
    )


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
