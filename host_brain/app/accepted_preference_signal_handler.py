"""Accepted preference-signal effect handler, isolated from the registry root."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.app.acceptance_effect_handler_common import (
    _canonical_id_ref,
    _opaque_failure,
)
from host_brain.contracts.conversation_acceptance_effects import (
    AcceptedPreferenceSignalV1,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
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
        or not _is_sha256_ref(receipt_digest)
    ):
        raise RuntimeError("PREFERENCE_EFFECT_DURABLE_RECEIPT_INVALID")
    return {
        **projected,
        "status": disposition,
        "receipt_ref": _canonical_id_ref("preference-effect", request.operation_id),
    }


def _is_sha256_ref(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


__all__ = ["_AcceptedPreferenceSignalHandler"]
