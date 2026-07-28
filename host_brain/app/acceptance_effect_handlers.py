"""Production handlers for committed governed Acceptance Effects."""
from __future__ import annotations
from collections.abc import Callable, Mapping
import hashlib
import re
from typing import Any
from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.app.acceptance_effect_handler_common import (
    _canonical_id_ref,
    _is_exact_effect_identity,
    _json_object,
    _opaque_failure,
    _receipt,
)
from host_brain.app.acceptance_surface_effect_handler import (
    _AcceptedSurfaceReplyDeliveryHandler,
    _accepted_surface_request,
    _deliver_accepted_surface_reply,
    _surface_delivery_id,
    _surface_gateway_payload_digest,
)
from host_brain.app.accepted_terminal_reconciliation_handler import (
    AcceptedTerminalReconciliationHandler,
)
from host_brain.app.accepted_operation_reconciliation_handler import (
    AcceptedOperationReconciliationHandler,
)
from host_brain.app.accepted_effect_reconciliation_handler import (
    register_accepted_effect_reconciliation_handler,
)
from host_brain.app.accepted_edge_embodiment_handler import (
    AcceptedEdgeEmbodimentEnqueueHandler,
)
from host_brain.app.accepted_sys_mode_receipt import (
    sys_mode_transition_handler_receipt_ref,
)
from host_brain.app.accepted_sys_mode_transition_handler import (
    AcceptedSysModeTransitionHandler,
)
from host_brain.app.accepted_operator_control_handler import (
    AcceptedOperatorControlHandler,
)
from host_brain.app.accepted_operator_state_control_handler import (
    AcceptedOperatorStateControlHandler,
)
from host_brain.app.life_projection_barrier import (
    LifeProjectionBarrierError,
    dispatch_and_require_life_projection_barrier,
    require_life_projection_barrier,
)
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
    scheduler_command_service_provider: Callable[[], Any] | None = None,
    long_term_goal_authority_provider: Callable[[], Any] | None = None,
    scheduler_operation_handler: Callable[[Mapping[str, Any]], Any] | None = None,
    internal_result_commit_handler: Callable[[Mapping[str, Any]], Any] | None = None,
    agent_workspace_artifact_handler: Callable[[Mapping[str, Any]], Any] | None = None,
    conversation_turn_reader: Any | None = None,
    conversation_event_reader: Any | None = None,
    preference_store: Any | None = None,
    life_command_service_provider: Callable[[], Any] | None = None,
    life_receipt_reader_provider: Callable[[], Any] | None = None,
    life_projection_dispatcher: Any | None = None,
    relationship_lifecycle_service_provider: Callable[[], Any] | None = None,
    gateway_reply_query_port: Callable[..., Any] | None = None,
    terminal_reconciliation_service: Any | None = None,
    terminal_reconciliation_store: Any | None = None,
    terminal_operator_command_store: Any | None = None,
    operation_reconciliation_service: Any | None = None,
    effect_reconciliation_service: Any | None = None,
    operation_reconciliation_evidence_store: Any | None = None,
    effect_reconciliation_evidence_store: Any | None = None,
    operation_reacquisition_continuation_provider: Callable[[], Any] | None = None,
    agent_runtime_ports: AgentRuntimeApplicationPorts | None = None,
    embodiment_runtime_provider: Callable[[], Any] | None = None,
    graph_cancellation_service_provider: Callable[[], Any] | None = None,
    secure_ingress_authority_resolver: Any | None = None,
    task_store: Any | None = None,
    review_command_service: Any | None = None,
    review_store: Any | None = None,
) -> dict[str, Any]:
    handlers: dict[str, Any] = {
        "conversation.user_turn": _AcceptedConversationUserTurnHandler(
            turn_reader=conversation_turn_reader,
            event_reader=conversation_event_reader,
            record_user_turn=(
                agent_runtime_ports.record_user_turn
                if isinstance(agent_runtime_ports, AgentRuntimeApplicationPorts)
                else None
            ),
        ),
        "conversation.preference_signal": _AcceptedPreferenceSignalHandler(
            preference_store=preference_store,
        ),
        "life.conversation_turn_recorded": _AcceptedLifeConversationHandler(
            receipt_reader_provider=life_receipt_reader_provider,
            projection_dispatcher=life_projection_dispatcher,
        ),
        "life.foreground_completed": _AcceptedLifeForegroundHandler(
            command_service_provider=life_command_service_provider,
            receipt_reader_provider=life_receipt_reader_provider,
            projection_dispatcher=life_projection_dispatcher,
            relationship_lifecycle_service_provider=(
                relationship_lifecycle_service_provider
            ),
        ),
        "surface.reply_delivery": _AcceptedSurfaceReplyDeliveryHandler(
            query_port=gateway_reply_query_port,
        ),
    }
    if mode_transition_service_provider is not None:
        handlers["host.sys_mode_transition"] = AcceptedSysModeTransitionHandler(
            service_provider=mode_transition_service_provider,
        )
    if scheduler_command_service_provider is not None:
        if (
            mode_transition_service_provider is None
            or secure_ingress_authority_resolver is None
            or long_term_goal_authority_provider is None
        ):
            raise TypeError(
                "Operator state control requires all app-local dependencies"
            )
        handlers["host.operator_state_control"] = (
            AcceptedOperatorStateControlHandler(
                scheduler_command_service_provider=(
                    scheduler_command_service_provider
                ),
                mode_transition_service_provider=(
                    mode_transition_service_provider
                ),
                long_term_goal_authority_provider=(
                    long_term_goal_authority_provider
                ),
                secure_ingress_authority_resolver=(
                    secure_ingress_authority_resolver
                ),
            )
        )
    operator_control_dependencies = (
        graph_cancellation_service_provider,
        secure_ingress_authority_resolver,
        task_store,
        review_command_service,
        review_store,
    )
    if any(value is not None for value in operator_control_dependencies):
        if any(value is None for value in operator_control_dependencies):
            raise TypeError(
                "Operator control handler requires all app-local dependencies"
            )
        handlers["task.operator_control"] = AcceptedOperatorControlHandler(
            graph_cancellation_service_provider=(
                graph_cancellation_service_provider
            ),
            secure_ingress_authority_resolver=(
                secure_ingress_authority_resolver
            ),
            task_store=task_store,
            review_command_service=review_command_service,
            review_store=review_store,
        )
    if embodiment_runtime_provider is not None:
        if not callable(embodiment_runtime_provider):
            raise TypeError("embodiment_runtime_provider must be callable")
        handlers["edge.embodiment_enqueue"] = (
            AcceptedEdgeEmbodimentEnqueueHandler(
                runtime_provider=embodiment_runtime_provider,
            )
        )
    if scheduler_operation_handler is not None:
        if not callable(scheduler_operation_handler):
            raise TypeError("scheduler_operation_handler must be callable")
        handlers["scheduler.operation.execute"] = scheduler_operation_handler
    if internal_result_commit_handler is not None:
        if not callable(internal_result_commit_handler):
            raise TypeError("internal_result_commit_handler must be callable")
        handlers["internal.agent.result_commit"] = internal_result_commit_handler
    if agent_workspace_artifact_handler is not None:
        if not callable(agent_workspace_artifact_handler):
            raise TypeError(
                "agent_workspace_artifact_handler must be callable"
            )
        handlers["agent.workspace_artifact_promote"] = (
            agent_workspace_artifact_handler
        )
    if terminal_reconciliation_service is not None:
        handlers["task.terminal_reconciliation"] = (
            AcceptedTerminalReconciliationHandler(
                service=terminal_reconciliation_service,
                task_store=terminal_reconciliation_store,
                command_store=terminal_operator_command_store,
            )
        )
    if operation_reconciliation_service is not None:
        if operation_reacquisition_continuation_provider is None:
            raise TypeError(
                "Operation reconciliation handler requires continuation provider"
            )
        if operation_reconciliation_evidence_store is None:
            raise TypeError(
                "Operation reconciliation handler requires evidence store"
            )
        handlers["task.operation_reconciliation"] = (
            AcceptedOperationReconciliationHandler(
                service=operation_reconciliation_service,
                evidence_store=operation_reconciliation_evidence_store,
                continuation_provider=operation_reacquisition_continuation_provider,
            )
        )
    register_accepted_effect_reconciliation_handler(
        handlers,
        service=effect_reconciliation_service,
        evidence_store=effect_reconciliation_evidence_store,
    )
    return handlers


class _AcceptedConversationUserTurnHandler:
    """Commit one visible turn and reconcile only from canonical SQLite rows."""

    def __init__(
        self,
        *,
        turn_reader: Any | None,
        event_reader: Any | None,
        record_user_turn: Callable[..., Any] | None = None,
    ) -> None:
        self._turn_reader = turn_reader
        self._event_reader = event_reader
        self._record_user_turn = record_user_turn

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        # Validate the immutable identity before crossing the writer boundary.
        _accepted_conversation_request(payload)
        try:
            invoked = _record_accepted_user_turn(
                payload,
                record_user_turn=self._record_user_turn,
            )
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
        projection_dispatcher: Any | None,
    ) -> None:
        self._receipt_reader_provider = receipt_reader_provider
        self._projection_dispatcher = projection_dispatcher

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = AcceptedLifeConversationProjectionV1.from_mapping(
            _without_terminal_preview_proof(payload)
        )
        fields = _life_conversation_command_fields(request)
        try:
            command_receipt = _submit_accepted_life_conversation(request)
        except Exception as exc:
            try:
                command_receipt = self._command_receipt(request)
            except Exception as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "Life conversation invocation has no exact durable attribution "
                    f"after {_opaque_failure(exc)}; "
                    f"{_opaque_failure(reconciliation)}"
                ) from exc
        try:
            return await dispatch_and_require_life_projection_barrier(
                reader=self._reader(),
                dispatcher=self._projection_dispatcher,
                command_receipt=command_receipt,
                command_payload=fields["payload"],
                correlation_id=request.conversation.request_id,
            )
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "Life conversation local projection has no exact durable "
                    f"attribution after {_opaque_failure(exc)}; "
                    f"{reconciliation.detail}"
                ) from exc

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = AcceptedLifeConversationProjectionV1.from_mapping(
            _without_terminal_preview_proof(payload)
        )
        try:
            reader = self._reader()
            command_receipt = self._command_receipt(request)
            return require_life_projection_barrier(
                reader=reader,
                command_receipt=command_receipt,
                command_payload=_life_conversation_command_fields(request)[
                    "payload"
                ],
                correlation_id=request.conversation.request_id,
            )
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "Life conversation requires exact read-only command reconciliation: "
                f"{_opaque_failure(exc)}"
            ) from exc

    def _reader(self) -> Any:
        if not callable(self._receipt_reader_provider):
            raise LifeProjectionBarrierError(
                "Life conversation requires an app-local projection reader"
            )
        return self._receipt_reader_provider()

    def _command_receipt(
        self,
        request: AcceptedLifeConversationProjectionV1,
    ) -> dict[str, Any]:
        reader = self._reader()
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


class _AcceptedLifeForegroundHandler:
    """Submit one Life command and require its exact local projection receipts."""

    def __init__(
        self,
        *,
        command_service_provider: Callable[[], Any] | None,
        receipt_reader_provider: Callable[[], Any] | None,
        projection_dispatcher: Any | None,
        relationship_lifecycle_service_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._command_service_provider = command_service_provider
        self._receipt_reader_provider = receipt_reader_provider
        self._projection_dispatcher = projection_dispatcher
        self._relationship_lifecycle_service_provider = (
            relationship_lifecycle_service_provider
        )

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value = _accepted_life_request(payload)
        try:
            command_receipt = _submit_accepted_life_foreground(
                payload,
                command_service_provider=self._command_service_provider,
                snapshot_reader=self._reader(),
            )
        except Exception as exc:
            try:
                command_receipt = _reconcile_accepted_life_foreground(
                    payload,
                    receipt_reader_provider=self._receipt_reader_provider,
                )
            except Exception as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "Life foreground invocation has no exact durable attribution "
                    f"after {_opaque_failure(exc)}; "
                    f"{_opaque_failure(reconciliation)}"
                ) from exc
        try:
            relationship_episode = self._provision_relationship_episode(
                value=value,
                foreground_receipt=command_receipt,
            )
            projected = await dispatch_and_require_life_projection_barrier(
                reader=self._reader(),
                dispatcher=self._projection_dispatcher,
                command_receipt=command_receipt,
                command_payload=value["payload"],
                correlation_id=value["correlation_id"],
            )
            return self._with_relationship_episode(projected, relationship_episode)
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "Life foreground local projection has no exact durable "
                    f"attribution after {_opaque_failure(exc)}; "
                    f"{reconciliation.detail}"
                ) from exc

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value = _accepted_life_request(payload)
        command_receipt = _reconcile_accepted_life_foreground(
            payload,
            receipt_reader_provider=self._receipt_reader_provider,
        )
        try:
            projected = require_life_projection_barrier(
                reader=self._reader(),
                command_receipt=command_receipt,
                command_payload=value["payload"],
                correlation_id=value["correlation_id"],
            )
            relationship_episode = self._reconcile_relationship_episode(
                value=value,
                foreground_receipt=command_receipt,
            )
            return self._with_relationship_episode(projected, relationship_episode)
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "Life foreground requires exact read-only projection "
                f"reconciliation: {_opaque_failure(exc)}"
            ) from exc

    def _reader(self) -> Any:
        if not callable(self._receipt_reader_provider):
            raise LifeProjectionBarrierError(
                "Life foreground requires an app-local projection reader"
            )
        return self._receipt_reader_provider()

    def _relationship_service(self) -> Any | None:
        if self._relationship_lifecycle_service_provider is None:
            return None
        if not callable(self._relationship_lifecycle_service_provider):
            raise RuntimeError("RELATIONSHIP_LIFECYCLE_SERVICE_PROVIDER_INVALID")
        service = self._relationship_lifecycle_service_provider()
        if service is None:
            raise RuntimeError("RELATIONSHIP_LIFECYCLE_SERVICE_NOT_INJECTED")
        return service

    def _relationship_inputs(
        self,
        *,
        value: Mapping[str, Any],
        foreground_receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = _json_object(
            value.get("payload"),
            "life.foreground_completed.payload",
        )
        return {
            "workspace_id": str(payload.get("workspace_id") or "workspace::default").strip(),
            "relationship_subject_id": str(value.get("person_id") or "").strip(),
            "session_id": str(payload.get("session_id") or "session::unknown").strip(),
            "request_id": str(value.get("correlation_id") or "").strip(),
            "response_receipt": dict(foreground_receipt),
            # The legacy foreground payload already has this visible reply for
            # its conversation projection.  The relationship kernel consumes
            # it only transiently to derive an opaque digest reference.
            "response_text": str(payload.get("assistant_reply") or ""),
        }

    def _provision_relationship_episode(
        self,
        *,
        value: Mapping[str, Any],
        foreground_receipt: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        service = self._relationship_service()
        if service is None:
            return None
        provision = getattr(service, "provision_accepted_response", None)
        if not callable(provision):
            raise RuntimeError("RELATIONSHIP_LIFECYCLE_SERVICE_NOT_INJECTED")
        receipt = provision(
            **self._relationship_inputs(
                value=value,
                foreground_receipt=foreground_receipt,
            )
        )
        return _relationship_episode_receipt(receipt)

    def _reconcile_relationship_episode(
        self,
        *,
        value: Mapping[str, Any],
        foreground_receipt: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        service = self._relationship_service()
        if service is None:
            return None
        reconcile = getattr(service, "reconcile_accepted_response", None)
        if not callable(reconcile):
            raise RuntimeError("RELATIONSHIP_LIFECYCLE_RECONCILIATION_NOT_INJECTED")
        receipt = reconcile(
            **self._relationship_inputs(
                value=value,
                foreground_receipt=foreground_receipt,
            )
        )
        return _relationship_episode_receipt(receipt)

    @staticmethod
    def _with_relationship_episode(
        projected: Mapping[str, Any],
        relationship_episode: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        result = dict(projected)
        if relationship_episode is not None:
            result["relationship_episode"] = dict(relationship_episode)
        return result


def _relationship_episode_receipt(receipt: Any) -> dict[str, Any]:
    projected = (
        dict(receipt)
        if isinstance(receipt, Mapping)
        else dict(receipt.as_dict())
        if callable(getattr(receipt, "as_dict", None))
        else None
    )
    if not isinstance(projected, Mapping):
        raise TypeError("relationship episode receipt is not mapping-compatible")
    event_id = str(projected.get("event_id") or "").strip()
    command_id = str(projected.get("command_id") or "").strip()
    if (
        str(projected.get("status") or "").strip().upper() != "APPLIED"
        or not command_id
        or not event_id
    ):
        raise RuntimeError("RELATIONSHIP_EPISODE_RECEIPT_INVALID")
    return {
        "command_id": command_id,
        "event_id": event_id,
        "state_version": int(projected.get("state_version") or 0),
        "state_digest": str(projected.get("state_digest") or ""),
        "receipt_ref": _canonical_id_ref("life-event", event_id),
        "replayed": bool(projected.get("replayed")),
    }




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


def _record_accepted_user_turn(
    payload: Mapping[str, Any],
    *,
    record_user_turn: Callable[..., Any] | None,
) -> Mapping[str, Any]:
    if not callable(record_user_turn):
        raise RuntimeError("HOST_AGENT_RUNTIME_PORTS_NOT_INJECTED")
    value = _accepted_conversation_request(payload)
    result = record_user_turn(
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


def _submit_accepted_life_foreground(
    payload: Mapping[str, Any],
    *,
    command_service_provider: Callable[[], Any] | None = None,
    snapshot_reader: Any | None = None,
) -> Mapping[str, Any]:
    """Submit through the app-local Life authority when one is composed.

    The process provider remains a compatibility fallback for older isolated
    adapters.  The production governed composition injects the app-local
    command service, so acceptance effects do not rediscover mutable process
    state during a Host lifespan.
    """
    value = _accepted_life_request(payload)
    if command_service_provider is not None:
        if not callable(command_service_provider):
            raise RuntimeError(
                "LIFE_FOREGROUND_COMMAND_SERVICE_PROVIDER_INVALID"
            )
        service = command_service_provider()
        if service is None:
            raise RuntimeError("LIFE_FOREGROUND_COMMAND_SERVICE_NOT_INJECTED")
        if snapshot_reader is None:
            raise RuntimeError("LIFE_FOREGROUND_SNAPSHOT_READER_NOT_INJECTED")
        reader = snapshot_reader
    else:
        from host_brain.composition.life import PROCESS_LIFE_PROVIDER  # noqa: PLC0415

        life = PROCESS_LIFE_PROVIDER.require()
        service = life.command_service
        reader = snapshot_reader or life.snapshot_reader
    receipt = submit_life_command(
        service=service,
        reader=reader,
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


__all__ = [
    "build_production_acceptance_effect_handlers",
    "sys_mode_transition_handler_receipt_ref",
]
