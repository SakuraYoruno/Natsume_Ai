"""Governed Operator runtime for accepted Host hard-state transitions.

The frozen worker can only propose one authority-free Acceptance Effect.  The
app-local dispatcher performs the actual mode transition after Verification
and Acceptance and commits its durable receipt through the canonical queue.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
import hashlib
import hmac
from typing import Any

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.accepted_result import InternalAcceptedResultHandler
from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
    sys_mode_transition_handler_receipt_ref,
)
from host_brain.app.acceptance_effect_recovery_observer import (
    AcceptanceEffectRecoveryGuardianObserver,
)
from host_brain.app.governed_frozen_skill_runtime import (
    _WorkerCommitAcceptance,
    _WorkerCommitVerifier,
)
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.app.user_preference_store import UserPreferenceStore
from host_brain.channels.edge_transport import query_gateway_reply_delivery
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.core.ids import now_ms
from host_brain.core.mode_transition import TransitionSource
from host_brain.orchestration import (
    DurableGovernorGrantResolver,
    GovernedCommandApplication,
)
from host_brain.orchestration.node_grants import (
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.context_repository import ContextRepository
from host_brain.runtime.context_application import RuntimeContextApplicationService
from host_brain.runtime.intent_admission_contracts import (
    INTENT_ADMISSION_REVIEW_REQUIRED,
)
from host_brain.runtime.intent_admission import DurableIntentAdmissionVerifier
from host_brain.task_runtime import GovernedRuntimeError, GovernedTaskCoordinator
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
    AcceptanceEffectRuntime,
)
from host_brain.task_runtime.governed_workers import (
    FrozenNodeInput,
    GovernedFrozenNodeWorker,
)
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from host_brain.task_runtime.worker_registry import GovernedWorkerRegistry
from natsume_contracts.task import HostCommand, HostCommandSurface


GOVERNED_OPERATOR_RUNTIME_INVALID = "GOVERNED_OPERATOR_RUNTIME_INVALID"
GOVERNED_OPERATOR_GOVERNOR_REJECTED = "GOVERNED_OPERATOR_GOVERNOR_REJECTED"
_TARGET_ID = "operator_sys_mode_switch"
_EFFECT_TYPE = "host.sys_mode_transition"
_ACTION_KIND = "operator_sys_mode_transition"
_INGRESS_ACTION = "obs:command:sys_mode.switch"
_INGRESS_PATH = "/v1/host/obs-commands/sys_mode.switch"
_INGRESS_REQUIRED_SCOPES = ("obs:sys-mode:write", "obs:write")


class GovernedOperatorRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_OPERATOR_RUNTIME_INVALID)
        self.detail = str(detail or "governed Operator command is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class GovernedOperatorComposition:
    runtime: "GovernedOperatorRuntime"
    acceptance_effect_dispatcher: AcceptanceEffectDispatcher
    acceptance_effect_runtime: AcceptanceEffectRuntime


class FrozenSysModeSwitchExecutor:
    """Compile one frozen Operator command into a non-executable effect intent."""

    def __init__(self, *, runtime_scope_id: str) -> None:
        self._runtime_scope_id = _runtime_scope_id(runtime_scope_id)

    async def execute_stream(
        self,
        frozen: FrozenNodeInput,
    ) -> AsyncIterator[Mapping[str, Any]]:
        if frozen.route != "delegate" or frozen.target_id != _TARGET_ID:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator route is not sys_mode.switch",
            )
        pack = dict(frozen.input_pack or {})
        intent = pack.get("intent_draft")
        if not isinstance(intent, Mapping):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator WorkOrder lacks intent_draft",
            )
        args = intent.get("args")
        if not isinstance(args, Mapping):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator intent lacks typed args",
            )
        target_mode = str(args.get("target_mode") or "").strip().lower()
        reason = str(args.get("reason") or "").strip()
        try:
            source = TransitionSource(
                str(args.get("source") or "").strip().lower()
            ).value
        except ValueError as exc:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator source is invalid",
            ) from exc
        if (
            str(intent.get("action") or "").strip() != _ACTION_KIND
            or str(intent.get("target") or "").strip().lower() != "system"
            or str(intent.get("route") or "").strip().lower() != "delegate"
            or str(intent.get("target_id") or "").strip() != _TARGET_ID
            or str(args.get("operator_command_id") or "").strip()
            != "sys_mode.switch"
            or not target_mode
            or not reason
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "frozen Operator intent binding drifted",
            )
        event_id = (
            "obs-sys-mode:"
            + hashlib.sha256(frozen.request_id.encode("utf-8")).hexdigest()
        )
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "request_id": frozen.request_id,
            "narrative": f"系统模式切换已通过执行验收：{target_mode}",
            "route_type": frozen.route,
            "target_id": frozen.target_id,
            "audit_refs": {
                "operator_command_id": "sys_mode.switch",
                "proposed_effect_type": _EFFECT_TYPE,
            },
            "acceptance_effect_intents": [
                {
                    "effect_type": _EFFECT_TYPE,
                    "commit_scope": "NODE",
                    "payload": {
                        "operator_command_id": "sys_mode.switch",
                        "request_id": frozen.request_id,
                        "target_mode": target_mode,
                        "source": source,
                        "reason": reason,
                        "event_id": event_id,
                        "runtime_scope_id": self._runtime_scope_id,
                    },
                }
            ],
        }


class GovernedOperatorRuntime:
    """Evaluate, admit and execute the sys_mode.switch Operator vertical slice."""

    def __init__(
        self,
        *,
        runtime: GovernedHostRuntime,
        command_repository: Any,
        intent_engine: Any,
        governor_grants: DurableGovernorGrantResolver,
        review_store: Any,
        task_store: Any,
        mode_transition_service_provider: Any,
        secure_ingress_authority_resolver: Any,
        runtime_scope_id: str,
    ) -> None:
        if not callable(
            getattr(
                secure_ingress_authority_resolver,
                "resolve_committed_authority_by_audit",
                None,
            )
        ):
            raise TypeError(
                "Operator runtime requires committed Secure Ingress authority resolver"
            )
        self._runtime = runtime
        self._commands = command_repository
        self._intent_engine = intent_engine
        self._governor_grants = governor_grants
        self._review_store = review_store
        self._task_store = task_store
        self._mode_transition_service_provider = mode_transition_service_provider
        self._runtime_scope_id = _runtime_scope_id(runtime_scope_id)
        self._secure_ingress_authority_resolver = (
            secure_ingress_authority_resolver
        )

    async def activate(self) -> None:
        await self._runtime.activate()

    async def close_admission(self) -> None:
        await self._runtime.close_admission()

    async def drain(self) -> None:
        await self._runtime.drain()

    async def aclose(self) -> None:
        await self._runtime.aclose()

    async def continue_review_admission(
        self,
        record: Mapping[str, Any],
        claim: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = await self._runtime.continue_review_admission(record, claim)
        bound = dict(result or {})
        receipt_raw = bound.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        if (
            receipt.get("accepted") is not True
            or str(receipt.get("status") or "").strip().upper() != "ACCEPTED"
        ):
            bound["historical_side_effects_executed"] = False
            return bound
        durable = dict(record or {})
        command = durable.get("command")
        work_order = durable.get("work_order")
        if not isinstance(command, HostCommand) or not isinstance(
            work_order, WorkOrderV3
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator Review continuation lacks durable command contracts",
            )
        intent_raw = dict(work_order.input_pack or {}).get("intent_draft")
        intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
        args_raw = intent.get("args")
        args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
        target_mode = str(args.get("target_mode") or "").strip().lower()
        reason = str(args.get("reason") or "").strip()
        source = str(args.get("source") or "").strip().lower()
        if (
            str(intent.get("action") or "").strip() != _ACTION_KIND
            or str(intent.get("target_id") or "").strip() != _TARGET_ID
            or str(args.get("operator_command_id") or "").strip()
            != "sys_mode.switch"
            or source != TransitionSource.MANUAL.value
            or not target_mode
            or not reason
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator Review continuation intent binding drifted",
            )
        outcome = _require_durable_sys_mode_outcome(
            task_store=self._task_store,
            mode_transition_service_provider=self._mode_transition_service_provider,
            execution_receipt=receipt,
            request_id=command.request_id,
            target_mode=target_mode,
            source=source,
            reason=reason,
            runtime_scope_id=self._runtime_scope_id,
        )
        bound["acceptance_effect_receipt"] = outcome
        bound["historical_side_effects_executed"] = bool(
            outcome["hard_state_event"] is not None
        )
        return bound

    async def request_graph_cancellation(
        self,
        record: Mapping[str, Any],
        *,
        authority: Mapping[str, Any],
        source: str,
        reason_code: str,
    ) -> dict[str, Any]:
        return await self._runtime.request_graph_cancellation(
            record,
            authority=authority,
            source=source,
            reason_code=reason_code,
        )

    async def execute_sys_mode_switch(
        self,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        value = dict(payload or {})
        supplied_authority = _require_operator_authority(value)
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator request_id is required",
            )
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        authority = _verify_durable_operator_authority(
            supplied_authority,
            resolver=self._secure_ingress_authority_resolver,
            request_id=request_ref,
            allow_settled_replay=isinstance(existing, Mapping),
        )
        target_mode = str(value.get("target_mode") or "").strip().lower()
        reason = str(value.get("reason") or "").strip() or "操作员请求切换系统模式"
        # ``obs.command`` is the authenticated source authority.  Caller
        # input cannot impersonate an automatic policy, Scheduler, or Skill
        # in the canonical HardState journal.
        source = TransitionSource.MANUAL.value
        principal_id = str(authority["principal_id"])
        if (
            not target_mode
            or str(value.get("principal_id") or "").strip() != principal_id
            or authority.get("operator_command_id") != "sys_mode.switch"
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator sys_mode payload differs from Secure Ingress authority",
            )
        message = f"切换系统模式为 {target_mode}；原因：{reason}"
        args = {
            "text": message,
            "route": "delegate",
            "target_id": _TARGET_ID,
            "operator_command_id": "sys_mode.switch",
            "target_mode": target_mode,
            "source": source,
            "reason": reason,
        }
        intent_draft = {
            "route": "delegate",
            "target_id": _TARGET_ID,
            "domain": "organ_ops",
            "task": message,
            "action": _ACTION_KIND,
            "target": "system",
            "args": args,
        }
        received_at_ms = now_ms()
        if isinstance(existing, Mapping):
            command = existing.get("command")
            work_order = existing.get("work_order")
            if (
                not isinstance(command, HostCommand)
                or not isinstance(work_order, WorkOrderV3)
                or str(
                    dict(work_order.metadata or {}).get(
                        "operator_command_id"
                    )
                    or ""
                ).strip()
                != "sys_mode.switch"
            ):
                raise GovernedOperatorRuntimeError(
                    GOVERNED_OPERATOR_RUNTIME_INVALID,
                    "durable Operator replay lacks exact command metadata",
                )
            decision = self._governor_grants.require(
                request_id=request_ref,
                expected_ref=command.governor_grant_ref,
            )
        else:
            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=_build_governor_intent(
                        request_id=request_ref,
                        principal_id=principal_id,
                        ts_ms=received_at_ms,
                        args=args,
                    ),
                    request_id=request_ref,
                    current_ms=received_at_ms,
                    proposer_node_id="obs_secure_operator_ingress",
                    model_snapshot="server-owned-operator-command",
                    prompt_profile_id="operator-sys-mode-v1",
                )
                or {}
            )
        if str(decision.get("decision") or "").strip().upper() != "APPROVE":
            raise GovernedOperatorRuntimeError(
                str(
                    decision.get("reason_code")
                    or GOVERNED_OPERATOR_GOVERNOR_REJECTED
                ),
                str(decision.get("detail") or "Governor rejected Operator command"),
            )
        observed_mode = _current_mode(self._mode_transition_service_provider)
        terminal: dict[str, Any] | None = None
        try:
            async for raw in self._runtime.execute_stream(
                request_body={"input_source": "OPERATOR"},
                request_id=request_ref,
                session_id=f"operator-{request_ref}",
                ts_ms=received_at_ms,
                intent_draft=intent_draft,
                governor_result=decision,
                person_id=principal_id,
                sanitized_message=message,
                command_authority=authority,
            ):
                event = dict(raw or {})
                if bool(event.get("done")):
                    terminal = event
        except GovernedRuntimeError as exc:
            if exc.reason_code != INTENT_ADMISSION_REVIEW_REQUIRED:
                raise
            return self._pending_review_result(
                request_id=request_ref,
                target_mode=target_mode,
                current_mode=observed_mode,
            )
        if terminal is None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "governed Operator runtime emitted no terminal result",
            )
        receipt = terminal.get("execution_receipt")
        receipt_map = dict(receipt) if isinstance(receipt, Mapping) else {}
        effect_summary = dict(
            dict(terminal.get("audit_refs") or {}).get("acceptance_effects") or {}
        )
        accepted = (
            receipt_map.get("accepted") is True
            and str(receipt_map.get("status") or "").strip().upper() == "ACCEPTED"
        )
        durable_outcome = _require_durable_sys_mode_outcome(
            task_store=self._task_store,
            mode_transition_service_provider=(
                self._mode_transition_service_provider
            ),
            execution_receipt=receipt_map,
            request_id=request_ref,
            target_mode=target_mode,
            source=source,
            reason=reason,
            runtime_scope_id=self._runtime_scope_id,
        )
        current_mode = _current_mode(self._mode_transition_service_provider)
        success = accepted and durable_outcome["committed"] is True
        replayed = receipt_map.get("replayed") is True
        return {
            "success": success,
            "side_effects_executed": bool(
                success
                and durable_outcome["hard_state_event"] is not None
                and not replayed
            ),
            "historical_side_effects_executed": bool(
                success and durable_outcome["hard_state_event"] is not None
            ),
            "exact_replay": replayed,
            "target_mode": target_mode,
            "current_mode": current_mode,
            "execution_receipt": receipt_map,
            "acceptance_effects": effect_summary,
            "acceptance_effect_receipt": durable_outcome,
            "graph_id": receipt_map.get("graph_id"),
            "graph_revision": receipt_map.get("graph_revision"),
            "terminal_status": terminal.get("status"),
        }

    def _pending_review_result(
        self,
        *,
        request_id: str,
        target_mode: str,
        current_mode: str,
    ) -> dict[str, Any]:
        durable = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        command = durable.get("command") if isinstance(durable, Mapping) else None
        if not isinstance(command, HostCommand):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "durable Operator Review admission is unavailable",
            )
        rows = self._review_store.list_reviews(
            task_graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        reviews = []
        for raw in tuple(rows or ()):
            record = dict(raw or {}) if isinstance(raw, Mapping) else {}
            review = record.get("review")
            review = dict(review) if isinstance(review, Mapping) else {}
            review_id = str(review.get("review_id") or "").strip()
            status = str(review.get("status") or "").strip().upper()
            if review_id and status in {"PENDING", "APPROVED"}:
                reviews.append(
                    {
                        "review_id": review_id,
                        "status": status,
                        "subject_ref": review.get("subject_ref"),
                        "subject_version": review.get("subject_version"),
                        "resume_phase": review.get("resume_phase"),
                        "checkpoint_id": review.get("checkpoint_id"),
                    }
                )
        if len(reviews) != 1:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator Review admission must expose one exact pending ReviewItem",
            )
        return {
            # The authenticated command and immutable graph were accepted;
            # the requested mode effect has not executed.
            "success": True,
            "accepted": True,
            "side_effects_executed": False,
            "status": "WAITING_REVIEW",
            "reason_code": INTENT_ADMISSION_REVIEW_REQUIRED,
            "target_mode": target_mode,
            "current_mode": current_mode,
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "reviews": reviews,
        }


def build_governed_operator_runtime(
    *,
    task_runtime_container: Any,
    ledger: Any,
    token_service: Any,
    intent_engine: Any,
    runtime_context_application: RuntimeContextApplicationService,
    action_preview_service: Any,
    acceptance_effect_dispatcher: AcceptanceEffectDispatcher,
    mode_transition_service_provider: Any,
    cancellation_authority_verifier: Any | None,
    secure_ingress_authority_resolver: Any,
    owner_id: str,
) -> GovernedOperatorRuntime:
    runtime_scope_id = _runtime_scope_from_context(runtime_context_application)
    projection_store = getattr(task_runtime_container, "worker_projection_store", None)
    review_store = getattr(task_runtime_container, "review_store", None)
    continuation_store = getattr(
        task_runtime_container,
        "review_continuation_store",
        None,
    )
    if projection_store is None or review_store is None or continuation_store is None:
        raise TypeError("Operator runtime requires Task Runtime projection and Review stores")
    node_grants = NodeBoundGrantService(
        ledger=ledger,
        token_service=token_service,
        review_store=continuation_store,
    )
    node_grant_verifier = DurableNodeGrantVerifier(
        ledger=ledger,
        review_store=continuation_store,
    )
    worker = GovernedFrozenNodeWorker(
        route_kind="delegate",
        executor=FrozenSysModeSwitchExecutor(runtime_scope_id=runtime_scope_id),
        node_grants=node_grant_verifier,
        projections=projection_store,
        action_preview_service=action_preview_service,
        allowed_acceptance_effect_types=frozenset({_EFFECT_TYPE}),
    )
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=GovernedWorkerRegistry(delegate=worker),
        verifier=_WorkerCommitVerifier(),
        acceptance=_WorkerCommitAcceptance(),
        projection_reader=projection_store,
        review_runtime=TaskNodeReviewRuntime(review_store),
        node_grant_verifier=node_grant_verifier,
        acceptance_effects_enabled=True,
        execution_registry=getattr(
            task_runtime_container,
            "graph_execution_registry",
            None,
        ),
        owner_id=owner_id,
    )
    application = GovernedCommandApplication(
        command_repository=task_runtime_container.command_repository,
        coordinator=coordinator,
        node_grant_service=node_grants,
        cancellation_authority_verifier=cancellation_authority_verifier,
        intent_admission_verifier=DurableIntentAdmissionVerifier(
            authority_port=runtime_context_application.composition.intent_authority_port,
            intent_arbiter=runtime_context_application.composition.intent_arbiter,
            event_store=runtime_context_application.composition.event_store,
            review_store=review_store,
        ),
    )
    if not isinstance(acceptance_effect_dispatcher, AcceptanceEffectDispatcher):
        raise TypeError(
            "Operator runtime requires the app-local Acceptance Effect dispatcher"
        )
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=task_runtime_container.command_repository,
        task_store=task_runtime_container.task_store,
        supported_routes=frozenset({"delegate"}),
        planning_enabled=False,
        acceptance_effect_dispatcher=acceptance_effect_dispatcher,
        delegate_target_validator=_require_sys_mode_target,
        runtime_context_application=runtime_context_application,
    )
    return GovernedOperatorRuntime(
        runtime=runtime,
        command_repository=task_runtime_container.command_repository,
        intent_engine=intent_engine,
        governor_grants=DurableGovernorGrantResolver(ledger),
        review_store=review_store,
        task_store=task_runtime_container.task_store,
        mode_transition_service_provider=mode_transition_service_provider,
        secure_ingress_authority_resolver=(
            secure_ingress_authority_resolver
        ),
        runtime_scope_id=runtime_scope_id,
    )


def build_governed_operator_composition(
    *,
    task_runtime_container: Any,
    ledger: Any,
    token_service: Any,
    intent_engine: Any,
    runtime_context_application: RuntimeContextApplicationService,
    action_preview_service: Any,
    mode_transition_service_provider: Any,
    cancellation_authority_verifier: Any | None,
    secure_ingress_authority_resolver: Any,
    runtime_owner_id: str,
    scheduler_operation_handler: Any | None = None,
    life_receipt_reader_provider: Any | None = None,
    gateway_reply_query_port: Any | None = None,
    internal_agent_artifact_store: Any | None = None,
    internal_agent_workspace_runtime: Any | None = None,
) -> GovernedOperatorComposition:
    """Build one dispatcher/recovery owner shared by Host and Operator."""

    dispatcher = AcceptanceEffectDispatcher(
        task_store=task_runtime_container.task_store,
        handlers=build_production_acceptance_effect_handlers(
            mode_transition_service_provider=mode_transition_service_provider,
            scheduler_operation_handler=scheduler_operation_handler,
            internal_result_commit_handler=InternalAcceptedResultHandler(
                command_repository=task_runtime_container.command_repository,
                projection_store=task_runtime_container.worker_projection_store,
                result_repository=task_runtime_container.agent_result_repository,
                artifact_store=internal_agent_artifact_store,
                workspace_runtime=internal_agent_workspace_runtime,
                task_store=task_runtime_container.task_store,
            ),
            conversation_turn_reader=ContextRepository.from_ledger(ledger),
            conversation_event_reader=MemoryEventRepository.from_ledger(ledger),
            preference_store=UserPreferenceStore(ledger=ledger),
            life_receipt_reader_provider=life_receipt_reader_provider,
            gateway_reply_query_port=(
                gateway_reply_query_port or query_gateway_reply_delivery
            ),
        ),
        owner_id=f"{runtime_owner_id}::acceptance-effects",
        action_preview_service=action_preview_service,
    )
    effect_runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        recovery_observer=AcceptanceEffectRecoveryGuardianObserver(
            guardian=runtime_context_application.composition.guardian,
        ),
    )
    runtime = build_governed_operator_runtime(
        task_runtime_container=task_runtime_container,
        ledger=ledger,
        token_service=token_service,
        intent_engine=intent_engine,
        runtime_context_application=runtime_context_application,
        action_preview_service=action_preview_service,
        acceptance_effect_dispatcher=dispatcher,
        mode_transition_service_provider=mode_transition_service_provider,
        cancellation_authority_verifier=cancellation_authority_verifier,
        secure_ingress_authority_resolver=secure_ingress_authority_resolver,
        owner_id=f"{runtime_owner_id}::operator",
    )
    return GovernedOperatorComposition(
        runtime=runtime,
        acceptance_effect_dispatcher=dispatcher,
        acceptance_effect_runtime=effect_runtime,
    )


def _require_sys_mode_target(target_id: str) -> str:
    target = str(target_id or "").strip()
    if target != _TARGET_ID:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "unsupported governed Operator target",
        )
    return target


def _require_operator_authority(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("_operator_authority")
    authority = dict(raw) if isinstance(raw, Mapping) else {}
    required = (
        "request_id",
        "principal_id",
        "tenant_id",
        "claim_ref",
        "audit_ref",
        "body_sha256",
        "action",
        "ingress_surface",
        "method",
        "normalized_path",
        "operator_command_id",
    )
    if authority.get("allowed") is not True or any(
        not str(authority.get(key) or "").strip() for key in required
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "admissible Secure Ingress Operator authority is required",
        )
    body_sha = str(authority.get("body_sha256") or "").strip().lower()
    if body_sha.startswith("sha256:"):
        body_sha = body_sha.removeprefix("sha256:")
    if len(body_sha) != 64 or any(ch not in "0123456789abcdef" for ch in body_sha):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator body_sha256 is invalid",
        )
    return {
        **authority,
        "body_sha256": body_sha,
        "ingress_claim_ref": str(authority["claim_ref"]),
        "ingress_audit_ref": str(authority["audit_ref"]),
    }


def _verify_durable_operator_authority(
    authority: Mapping[str, Any],
    *,
    resolver: Any,
    request_id: str,
    allow_settled_replay: bool,
) -> dict[str, Any]:
    supplied = dict(authority or {})
    audit_ref = _authority_text(supplied, "audit_ref")
    durable_raw = resolver.resolve_committed_authority_by_audit(
        audit_ref=audit_ref,
    )
    if not isinstance(durable_raw, Mapping):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator Secure Ingress claim/audit is not durably committed",
        )
    durable = dict(durable_raw)
    expected_scopes = tuple(sorted(_INGRESS_REQUIRED_SCOPES))
    supplied_scopes = _authority_scopes(supplied.get("required_scopes"))
    durable_scopes = _authority_scopes(durable.get("required_scopes"))
    requested_scopes = _authority_scopes(durable.get("requested_scopes"))
    supplied_body = _authority_sha256(supplied.get("body_sha256"))
    durable_body = _authority_sha256(durable.get("body_sha256"))
    claim_result_ref = str(durable.get("claim_result_ref") or "").strip()
    valid = all(
        (
            supplied.get("allowed") is True,
            supplied.get("duplicate") is False,
            _authority_text(supplied, "request_id") == request_id,
            _authority_text(durable, "request_id") == request_id,
            _authority_text(supplied, "claim_ref")
            == _authority_text(durable, "claim_ref"),
            _authority_text(supplied, "audit_ref")
            == _authority_text(durable, "audit_ref"),
            _authority_text(supplied, "principal_id")
            == _authority_text(durable, "principal_id"),
            _authority_text(supplied, "tenant_id")
            == _authority_text(durable, "tenant_id"),
            _authority_text(supplied, "action") == _INGRESS_ACTION,
            _authority_text(durable, "action") == _INGRESS_ACTION,
            _authority_text(supplied, "ingress_surface") == "obs.command",
            _authority_text(durable, "surface") == "obs.command",
            _authority_text(supplied, "method").upper() == "POST",
            _authority_text(durable, "method").upper() == "POST",
            _authority_text(supplied, "normalized_path") == _INGRESS_PATH,
            _authority_text(durable, "normalized_path") == _INGRESS_PATH,
            _authority_text(supplied, "operator_command_id")
            == "sys_mode.switch",
            supplied_scopes == expected_scopes,
            durable_scopes == expected_scopes,
            set(expected_scopes).issubset(requested_scopes),
            _authority_text(durable, "decision").upper() == "ALLOW",
            _authority_text(durable, "reason_code")
            == "SECURE_INGRESS_ALLOWED",
            _authority_text(durable, "stage").upper() == "COMPLETE",
            durable.get("side_effects_executed") is False,
            _authority_text(durable, "claim_status").upper() == "COMMITTED",
            not claim_result_ref or bool(allow_settled_replay),
        )
    )
    if not valid or not hmac.compare_digest(supplied_body, durable_body):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator Secure Ingress authority binding drifted",
        )
    return {
        "allowed": True,
        "duplicate": False,
        "request_id": request_id,
        "principal_id": _authority_text(durable, "principal_id"),
        "tenant_id": _authority_text(durable, "tenant_id"),
        "claim_ref": _authority_text(durable, "claim_ref"),
        "audit_ref": _authority_text(durable, "audit_ref"),
        "body_sha256": durable_body,
        "action": _INGRESS_ACTION,
        "required_scopes": expected_scopes,
        "ingress_surface": "obs.command",
        "method": "POST",
        "normalized_path": _INGRESS_PATH,
        "operator_command_id": "sys_mode.switch",
        "ingress_claim_ref": _authority_text(durable, "claim_ref"),
        "ingress_audit_ref": _authority_text(durable, "audit_ref"),
    }


def _authority_text(value: Mapping[str, Any], field: str) -> str:
    text = str(value.get(field) or "").strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            f"Operator authority field {field} is invalid",
        )
    return text


def _authority_scopes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator authority scopes are invalid",
        )
    return tuple(sorted(set(str(item).strip() for item in value)))


def _authority_sha256(value: Any) -> str:
    digest = str(value or "").strip().lower().removeprefix("sha256:")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator authority body digest is invalid",
        )
    return digest


def _build_governor_intent(
    *,
    request_id: str,
    principal_id: str,
    ts_ms: int,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-operator-sys-mode-{digest[:40]}",
        "ts_ms": int(ts_ms),
        "session_id": f"operator-{request_id}",
        "seq": int(ts_ms),
        "type": "control",
        "action": _ACTION_KIND,
        "target": "system",
        "args": dict(args),
        "nonce": hashlib.sha256(
            f"operator-sys-mode\n{request_id}".encode("utf-8")
        ).hexdigest(),
        "person_id": principal_id,
        "principal_id": principal_id,
        "route": "delegate",
        "target_id": _TARGET_ID,
    }


def _require_durable_sys_mode_outcome(
    *,
    task_store: Any,
    mode_transition_service_provider: Any,
    execution_receipt: Mapping[str, Any],
    request_id: str,
    target_mode: str,
    source: str,
    reason: str,
    runtime_scope_id: str,
) -> dict[str, Any]:
    receipt = dict(execution_receipt or {})
    graph_id = str(receipt.get("graph_id") or "").strip()
    node_id = str(receipt.get("node_id") or "").strip()
    attempt_id = str(receipt.get("attempt_id") or "").strip()
    try:
        graph_revision = int(receipt.get("graph_revision") or 0)
    except (TypeError, ValueError) as exc:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator execution receipt graph revision is invalid",
        ) from exc
    list_effects = getattr(task_store, "list_acceptance_effect_intents", None)
    if (
        not graph_id
        or graph_revision < 1
        or not node_id
        or not attempt_id
        or not callable(list_effects)
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator execution receipt cannot reopen durable Acceptance Effect",
        )
    rows = tuple(
        list_effects(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            limit=10,
        )
        or ()
    )
    effects = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("effect_type") or "").strip() == _EFFECT_TYPE
    ]
    if len(effects) != 1:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator execution must bind one durable mode Acceptance Effect",
        )
    effect = effects[0]
    payload_raw = effect.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    expected_event_id = (
        "obs-sys-mode:"
        + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    )
    expected_payload = {
        "operator_command_id": "sys_mode.switch",
        "request_id": request_id,
        "target_mode": target_mode,
        "source": source,
        "reason": reason,
        "event_id": expected_event_id,
        "runtime_scope_id": _runtime_scope_id(runtime_scope_id),
    }
    business_payload = {
        key: payload.get(key) for key in expected_payload
    }
    expected_handler_receipts = {
        status: sys_mode_transition_handler_receipt_ref(
            payload,
            outcome_status=status,
        )
        for status in ("APPLIED", "REPLAYED", "NO_CHANGE")
    }
    persisted_receipt = str(effect.get("receipt_ref") or "").strip()
    persisted_status = next(
        (
            status
            for status, receipt_ref in expected_handler_receipts.items()
            if hmac.compare_digest(persisted_receipt, receipt_ref)
        ),
        None,
    )
    if (
        str(effect.get("status") or "").strip().upper() != "COMMITTED"
        or str(effect.get("graph_id") or "").strip() != graph_id
        or int(effect.get("graph_revision") or 0) != graph_revision
        or str(effect.get("node_id") or "").strip() != node_id
        or str(effect.get("attempt_id") or "").strip() != attempt_id
        or business_payload != expected_payload
        or persisted_status is None
        or int(effect.get("committed_ts_ms") or 0) <= 0
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator durable Acceptance Effect receipt binding drifted",
        )

    service = mode_transition_service_provider()
    reopen_commit = getattr(service, "reopen_transition_commit", None)
    if not callable(reopen_commit):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator HardState exact history is unavailable",
        )
    commit = reopen_commit(
        event_id=expected_event_id,
        target_mode=target_mode,
        source=source,
        reason=reason,
    )
    hard_state_event: dict[str, Any] | None = None
    if persisted_status == "NO_CHANGE":
        if commit is not None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator NO_CHANGE receipt conflicts with a durable event",
            )
    else:
        if commit is None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator applied receipt lost its durable HardState event",
            )
        event = getattr(commit, "event", None)
        digest = str(getattr(event, "event_digest", "") or "").strip().lower()
        runtime_scope_id = str(
            getattr(event, "runtime_scope_id", "") or ""
        ).strip()
        if (
            getattr(commit, "exact_replay", None) is not True
            or str(getattr(event, "event_id", "") or "").strip()
            != expected_event_id
            or str(getattr(event, "event_kind", "") or "").strip()
            != "MODE_CHANGED"
            or str(getattr(event, "to_mode", "") or "").strip().lower()
            != target_mode
            or str(getattr(event, "source", "") or "").strip().lower() != source
            or str(getattr(event, "reason", "") or "") != reason
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or runtime_scope_id != expected_payload["runtime_scope_id"]
            or int(getattr(event, "revision", 0) or 0) < 2
            or int(getattr(event, "runtime_fencing_token", 0) or 0) < 1
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator HardState event receipt binding drifted",
            )
        hard_state_event = {
            "event_id": expected_event_id,
            "event_digest": digest,
            "revision": int(event.revision),
            "runtime_scope_id": runtime_scope_id,
            "from_mode": str(event.from_mode or "").strip().lower(),
            "to_mode": target_mode,
            "source": source,
        }

    return {
        "committed": True,
        "effect_id": str(effect.get("effect_id") or ""),
        "effect_type": _EFFECT_TYPE,
        "handler_receipt_ref": persisted_receipt,
        "committed_ts_ms": int(effect["committed_ts_ms"]),
        "event_id": expected_event_id,
        "hard_state_event": hard_state_event,
        "historical_status": persisted_status,
    }


def _runtime_scope_from_context(
    application: RuntimeContextApplicationService,
) -> str:
    composition = getattr(application, "composition", None)
    guardian = getattr(composition, "guardian", None)
    snapshot = getattr(guardian, "snapshot", None)
    if not callable(snapshot):
        raise TypeError("Operator runtime requires RuntimeGuardian authority")
    authority = snapshot()
    value = (
        authority.get("runtime_scope_id")
        if isinstance(authority, Mapping)
        else getattr(authority, "runtime_scope_id", None)
    )
    return _runtime_scope_id(value)


def _runtime_scope_id(value: Any) -> str:
    scope = str(value or "").strip()
    if (
        not scope
        or len(scope.encode("utf-8")) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in scope)
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator runtime_scope_id is invalid",
        )
    return scope


def _current_mode(service_provider: Any) -> str:
    service = service_provider()
    get_status = getattr(service, "get_current_status", None)
    if not callable(get_status):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "app-local mode transition service is unavailable",
        )
    status = get_status()
    mode = str(dict(status or {}).get("sys_mode") or "").strip().lower()
    if not mode:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "app-local sys_mode projection is unavailable",
        )
    return mode


__all__ = [
    "GOVERNED_OPERATOR_GOVERNOR_REJECTED",
    "GOVERNED_OPERATOR_RUNTIME_INVALID",
    "FrozenSysModeSwitchExecutor",
    "GovernedOperatorComposition",
    "GovernedOperatorRuntime",
    "GovernedOperatorRuntimeError",
    "build_governed_operator_composition",
    "build_governed_operator_runtime",
]
