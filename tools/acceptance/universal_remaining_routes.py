"""Local acceptance driver for the three non-Host universal runtime routes.

The driver is deliberately outside the production object graph.  It invokes
the real Scheduler, Internal and Operator application boundaries against one
canonical file-backed SQLite database, while using bounded deterministic
workers so the run proves governance and durability rather than model quality.
It is not eligible as a real-provider, PlanningSearch-benefit or NE-07
full-stack receipt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any, Mapping

from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.app.governed_operator_runtime import (
    build_governed_operator_runtime,
)
from host_brain.app.host_hard_state import HostHardStateOwner
from host_brain.app.host_runtime_context_wiring import (
    compose_host_runtime_context,
    runtime_environment_observation_provider,
)
from host_brain.app.internal_task_runtime import (
    InternalAgentCommandService,
    build_governed_internal_runtime,
)
from host_brain.app.observability_runtime.domain_command_ports import (
    build_default_obs_domain_command_registry,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.embodiment import HostEmbodimentRuntime
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.composition.task_runtime import (
    build_process_task_runtime_container,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.core.mode_transition import ModeTransitionService
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.observability.command_outcome_journal import (
    ObsCommandOutcomeJournal,
    ObsCommandOutcomeKey,
    ObsCommandOutcomeState,
)
from host_brain.observability.command_ports import (
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandEnvelope,
    bind_server_authority,
    governed_review_continuation_ref,
)
from host_brain.observability.operator_command_gateway import (
    bind_operator_command_runtime,
)
from host_brain.observability.secure_command_ingress import (
    SecureObsCommandIngress,
)
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.persistence.agent_execution_repository import (
    AgentExecutionResultRepository,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.hard_state_journal import SQLiteHardStateJournal
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
)
from host_brain.runtime.action_preview_resolver import (
    ProductionActionPreviewAuthorityResolver,
)
from host_brain.runtime.environment_state import build_environment_snapshot
from host_brain.scheduler.accepted_operation import (
    SchedulerAcceptedOperationHandler,
)
from host_brain.scheduler.governed_runtime import (
    SchedulerCommandService,
    SchedulerDefinitionPolicyEvaluator,
    build_governed_scheduler_runtime,
)
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
)
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.skills.agent_runtime_registry import (
    build_agent_runtime_skill_registry,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
    AcceptanceEffectRuntime,
)
from host_brain.agent_runtime.accepted_result import InternalAcceptedResultHandler
from natsume_contracts.task import HostCommandSurface


SCHEDULER_REQUEST_ID = "request-ne01-acceptance-scheduler"
INTERNAL_REQUEST_ID = "request-ne01-acceptance-internal"
OPERATOR_REQUEST_ID = "request-ne01-acceptance-operator"
_OPERATOR_IDEMPOTENCY_KEY = "ne01-acceptance-operator-idempotency"


class UniversalRemainingRouteAcceptanceError(RuntimeError):
    """Raised when a local acceptance route cannot reach its durable terminal."""


class _UnusedEmbodimentCommandPort:
    async def submit(self, _command: Any) -> Any:
        raise AssertionError(
            "universal route acceptance must commit through Acceptance effects"
        )


class _NoopContinuationWorker:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def start(self) -> None:
        return None

    def wake(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def join(self) -> None:
        return None


class _BoundedApprovalEngine:
    """Persist one real Governor decision for deterministic acceptance work."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger,
        token_service: TokenService,
        risk_level: int,
    ) -> None:
        self._ledger = ledger
        self._tokens = token_service
        self._risk_level = int(risk_level)

    def evaluate_intent(self, **kwargs: Any) -> dict[str, Any]:
        intent = dict(kwargs["raw_intent"])
        token_ref = self._tokens.issue_approval_token(
            intent_id=str(intent["id"]),
            skill_name=str(intent["action"]),
            session_id=str(intent["session_id"]),
            seq=int(intent["seq"]),
            now_ts_ms=int(intent["ts_ms"]),
        )
        binding = build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref=token_ref,
            risk_level=self._risk_level,
            issued_at_ms=int(intent["ts_ms"]),
            expires_at_ms=int(intent["ts_ms"]) + 120_000,
        )
        decision = {
            "request_id": str(kwargs["request_id"]),
            "intent_id": str(intent["id"]),
            "decision": "APPROVE",
            "reason_code": None,
            "tokens": {"approval_token_ref": token_ref},
            "approved_action_binding": binding,
            "snapshot_refs": {},
            "stage": "S_INTENT_OK",
            "ts_ms": int(intent["ts_ms"]),
        }
        self._ledger.log_governor(decision)
        return decision


class _BoundedInternalExecutor:
    """Deterministic worker behind the real Internal governed application."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(
        self,
        order: AgentWorkOrder,
        *,
        workspace_execution_authority: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if workspace_execution_authority is None:
            raise UniversalRemainingRouteAcceptanceError(
                "Internal acceptance worker has no durable execution authority"
            )
        self.calls.append(order.work_order_id)
        return AgentResult(
            status="EXECUTED",
            work_order_id=order.work_order_id,
            summary="bounded internal acceptance worker completed",
            result_text="durable internal acceptance result",
            verification={"status": "PASSED"},
            acceptance={"decision": "ACCEPT"},
            risk_flags=[],
            review_required=False,
            metadata={
                "executor_kind": "bounded_local_acceptance",
                "model_provider_used": False,
            },
        ).to_dict()


def _sha256(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _commit_ingress_authority(
    store: SQLiteSecureIngressStore,
    *,
    request_id: str,
    principal_id: str,
    surface: str,
    action: str,
    normalized_path: str,
    required_scopes: tuple[str, ...],
    body: Mapping[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    body_bytes = json.dumps(
        dict(body),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    body_sha256 = _sha256(body_bytes)
    suffix = _sha256(f"{surface}\n{request_id}")
    claim = store.try_claim(
        principal_id=principal_id,
        surface=surface,
        action=action,
        required_scopes=required_scopes,
        idempotency_key=f"acceptance:{suffix}:idempotency",
        nonce=f"acceptance:{suffix}:nonce",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=now_ms,
        expires_at_ms=now_ms + 600_000,
    )
    if claim.status is not ClaimStatus.NEW or not claim.claim_ref:
        raise UniversalRemainingRouteAcceptanceError(
            f"secure ingress claim is not new for {request_id}"
        )
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"acceptance:{suffix}:correlation",
            surface=surface,
            action=action,
            method="POST",
            normalized_path=normalized_path,
            principal_id=principal_id,
            tenant_id="ne01-universal-acceptance",
            key_id="ne01-universal-acceptance-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + "1" * 64,
            requested_scopes=required_scopes,
            required_scopes=required_scopes,
            body_sha256=body_sha256,
            idempotency_digest="sha256:" + "2" * 64,
            nonce_digest="sha256:" + "3" * 64,
            transport_key_digest="sha256:" + "4" * 64,
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=claim.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=now_ms,
        ),
    )
    return {
        "allowed": True,
        "duplicate": False,
        "request_id": request_id,
        "principal_id": principal_id,
        "tenant_id": "ne01-universal-acceptance",
        "claim_ref": str(claim.claim_ref),
        "audit_ref": str(audit_ref),
        "body_sha256": body_sha256,
    }


def _context_bundle(
    *,
    container: Any,
    ledger: SQLiteLedger,
    database: Path,
    owner_id: str,
    environment_observation_provider: Any | None = None,
) -> tuple[HostEmbodimentRuntime, Any, ActionPreviewService]:
    embodiment = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(database),
        worker_id=owner_id,
        command_port=_UnusedEmbodimentCommandPort(),
        authority_lease_ms=600_000,
    )
    environment_provider = environment_observation_provider or (
        lambda *, now_ms: {
            **build_environment_snapshot(now_ms=now_ms),
            "activity_state": "NORMAL",
            "mode_identity": "normal",
        }
    )
    context = compose_host_runtime_context(
        task_runtime=container,
        ledger=ledger,
        embodiment_runtime=embodiment,
        environment_observation_provider=environment_provider,
    )
    preview = ActionPreviewService(
        runtime_event_store=context.composition.event_store,
        guardian=context.composition.guardian,
        authority_resolver=ProductionActionPreviewAuthorityResolver(
            command_repository=container.command_repository,
            task_store=container.task_store,
            node_grant_verifier=DurableNodeGrantVerifier(
                ledger=ledger,
                review_store=container.review_continuation_store,
            ),
            runtime_context_application=context,
        ),
    )
    return embodiment, context, preview


async def append_scheduler_and_internal_routes(
    *,
    database: str | Path,
) -> dict[str, str]:
    """Append Scheduler and Internal route terminals to one canonical DB."""

    db_path = Path(database).expanduser().resolve()
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = None
    embodiment = None
    claim_store = None
    scheduler_runtime = None
    internal_runtime = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
        )
        embodiment, runtime_context, preview = _context_bundle(
            container=container,
            ledger=ledger,
            database=db_path,
            owner_id="ne01-universal-remaining-context",
        )
        claim_store = SQLiteSecureIngressStore(db_path, rate_limit=100)
        token_service = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        now_ms = int(time.time() * 1_000)

        definitions = SchedulerDefinitionRepository(db_path)
        scheduler_results = SchedulerExecutionResultRepository(db_path)
        worker_key = "ne01_universal_acceptance_worker"
        definitions.publish_definition(
            task_id="ne01_universal_acceptance_task",
            definition={
                "trigger_mode": "interval",
                "trigger_config": {"seconds": 60},
                "intent_template": {
                    "skill_name": worker_key,
                    "args": {},
                },
                "worker_key": worker_key,
                "priority": "HIGH",
                "mode_allowlist": ["normal"],
                "resource_profile": {},
                "retry_policy": {
                    "max_attempts": 1,
                    "retry_backoff_ms": 0,
                },
                "risk_level": 2,
                "enabled": True,
            },
            expected_head_version=0,
            next_scheduled_at_ms=now_ms + 60_000,
        )
        scheduler_worker_calls: list[str] = []

        async def scheduler_worker(**kwargs: Any) -> dict[str, Any]:
            context = dict(kwargs.get("execution_context") or {})
            scheduler_worker_calls.append(str(context.get("request_id") or ""))
            return {
                "status": "OK",
                "summary": "bounded scheduler acceptance worker completed",
            }

        scheduler_workers = SchedulerWorkerRegistry()
        scheduler_workers.register(worker_key, scheduler_worker)
        scheduler_handler = SchedulerAcceptedOperationHandler(
            worker_registry=scheduler_workers,
            definition_repository=definitions,
            occurrence_repository=definitions,
            command_repository=container.command_repository,
            result_repository=scheduler_results,
        )
        scheduler_dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers=build_production_acceptance_effect_handlers(
                scheduler_operation_handler=scheduler_handler,
            ),
            owner_id="ne01-universal-scheduler-effects",
            action_preview_service=preview,
        )
        scheduler_runtime = build_governed_scheduler_runtime(
            task_runtime_container=container,
            ledger=ledger,
            token_service=token_service,
            result_repository=scheduler_results,
            runtime_router=CanonicalRuntimeRouter(),
            worker_registry=scheduler_workers,
            owner_id="ne01-universal-scheduler-runtime",
            runtime_context_application=runtime_context,
            action_preview_service=preview,
            acceptance_effect_dispatcher=scheduler_dispatcher,
        )
        scheduler_service = SchedulerCommandService(
            runtime=scheduler_runtime,
            intent_engine=_BoundedApprovalEngine(
                ledger=ledger,
                token_service=token_service,
                risk_level=2,
            ),
            claim_result_store=claim_store,
            definition_repository=definitions,
            policy_evaluator=SchedulerDefinitionPolicyEvaluator(
                current_mode=lambda: "normal"
            ),
            worker_registry=scheduler_workers,
        )
        await scheduler_runtime.activate()
        scheduler_authority = _commit_ingress_authority(
            claim_store,
            request_id=SCHEDULER_REQUEST_ID,
            principal_id="principal.ne01.acceptance.scheduler",
            surface="scheduler.admin",
            action="scheduler:execute",
            normalized_path="/scheduler/ne01_universal_acceptance_task/run",
            required_scopes=("scheduler:execute",),
            body={
                "task_id": "ne01_universal_acceptance_task",
                "payload": {"purpose": "universal runtime acceptance"},
            },
            now_ms=now_ms,
        )
        scheduler_terminal = await scheduler_service.trigger_manual(
            task_id="ne01_universal_acceptance_task",
            authority=scheduler_authority,
            payload={"purpose": "universal runtime acceptance"},
            received_at_ms=now_ms,
            command_surface=HostCommandSurface.SCHEDULER,
        )
        scheduler_receipt = dict(
            scheduler_terminal.get("execution_receipt") or {}
        )
        if (
            scheduler_receipt.get("accepted") is not True
            or str(scheduler_receipt.get("status") or "") != "ACCEPTED"
            or scheduler_worker_calls != [SCHEDULER_REQUEST_ID]
        ):
            raise UniversalRemainingRouteAcceptanceError(
                "Scheduler route did not reach one accepted durable terminal"
            )
        await scheduler_runtime.aclose()
        scheduler_runtime = None

        internal_results = AgentExecutionResultRepository(db_path)
        internal_executor = _BoundedInternalExecutor()
        internal_handler = InternalAcceptedResultHandler(
            command_repository=container.command_repository,
            projection_store=container.worker_projection_store,
            result_repository=internal_results,
        )
        internal_dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers={"internal.agent.result_commit": internal_handler},
            owner_id="ne01-universal-internal-effects",
            action_preview_service=preview,
        )
        internal_runtime = build_governed_internal_runtime(
            task_runtime_container=container,
            ledger=ledger,
            token_service=token_service,
            result_repository=internal_results,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="ne01-universal-internal-runtime",
            canonical_executor=internal_executor,  # type: ignore[arg-type]
            runtime_context_application=runtime_context,
            action_preview_service=preview,
            acceptance_effect_dispatcher=internal_dispatcher,
        )
        internal_service = InternalAgentCommandService(
            runtime=internal_runtime,
            intent_engine=_BoundedApprovalEngine(
                ledger=ledger,
                token_service=token_service,
                risk_level=1,
            ),
            claim_result_store=claim_store,
        )
        await internal_runtime.activate()
        internal_body = {
            "work_order_id": "work-order-ne01-universal-acceptance",
            "objective": "verify one governed internal acceptance route",
        }
        internal_authority = _commit_ingress_authority(
            claim_store,
            request_id=INTERNAL_REQUEST_ID,
            principal_id="principal.ne01.acceptance.internal",
            surface="internal.agent",
            action="internal:agent:execute",
            normalized_path="/internal/codex/work-orders/execute",
            required_scopes=("internal:agent:execute",),
            body=internal_body,
            now_ms=now_ms + 1,
        )
        internal_terminal = await internal_service.execute(
            work_order=AgentWorkOrder(
                work_order_id=str(internal_body["work_order_id"]),
                domain="code",
                run_mode="inspect",
                objective=str(internal_body["objective"]),
                input_pack={"input_ref": "input://ne01/universal-acceptance"},
                allowed_actions=[],
                forbidden_actions=["write_files"],
                acceptance_criteria=["durable result exists"],
            ),
            request_id=INTERNAL_REQUEST_ID,
            session_id="session-ne01-universal-acceptance",
            ts_ms=now_ms + 1,
            command_authority=internal_authority,
        )
        internal_receipt = dict(
            internal_terminal.get("execution_receipt") or {}
        )
        if (
            internal_receipt.get("accepted") is not True
            or str(internal_receipt.get("status") or "") != "ACCEPTED"
            or str(internal_terminal.get("internal_agent_effect_status") or "")
            != "COMMITTED"
            or internal_executor.calls
            != ["work-order-ne01-universal-acceptance"]
        ):
            raise UniversalRemainingRouteAcceptanceError(
                "Internal route did not reach one accepted durable terminal"
            )
        return {
            "scheduler": SCHEDULER_REQUEST_ID,
            "internal": INTERNAL_REQUEST_ID,
        }
    finally:
        if internal_runtime is not None:
            await internal_runtime.aclose()
        if scheduler_runtime is not None:
            await scheduler_runtime.aclose()
        if claim_store is not None:
            claim_store.close()
        if embodiment is not None:
            embodiment.close()
        if container is not None:
            await container.aclose()
        ledger.close()


def _operator_ingress_authority(
    database: Path,
    *,
    request_id: str,
) -> tuple[SQLiteSecureIngressStore, ObsCommandEnvelope, bytes]:
    command_payload = {
        "target_mode": "focus",
        "source": "manual",
        "reason": "NE-01 universal route acceptance",
    }
    raw_body = json.dumps(
        {
            "command_id": "sys_mode.switch",
            "payload": command_payload,
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    body_sha256 = _sha256(raw_body)
    now_ms = int(time.time() * 1_000)
    scopes = ("obs:write", "obs:sys-mode:write")
    store = SQLiteSecureIngressStore(database, rate_limit=100)
    claim = store.try_claim(
        principal_id="principal.ne01.acceptance.operator",
        surface="obs.command",
        action="obs:command:sys_mode.switch",
        required_scopes=scopes,
        idempotency_key=_OPERATOR_IDEMPOTENCY_KEY,
        nonce=f"ne01-acceptance-operator-nonce:{request_id}",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=now_ms,
        expires_at_ms=now_ms + 600_000,
    )
    if claim.status is not ClaimStatus.NEW or not claim.claim_ref:
        raise UniversalRemainingRouteAcceptanceError(
            "Operator secure ingress claim is not new"
        )
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id="ne01-acceptance-operator-correlation",
            surface="obs.command",
            action="obs:command:sys_mode.switch",
            method="POST",
            normalized_path="/v1/host/obs-commands/sys_mode.switch",
            principal_id="principal.ne01.acceptance.operator",
            tenant_id="ne01-universal-acceptance",
            key_id="ne01-universal-acceptance-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + "1" * 64,
            requested_scopes=scopes,
            required_scopes=scopes,
            body_sha256=body_sha256,
            idempotency_digest="sha256:" + "2" * 64,
            nonce_digest="sha256:" + "3" * 64,
            transport_key_digest="sha256:" + "4" * 64,
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=claim.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=now_ms,
        ),
    )
    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec("sys_mode.switch")
    if spec is None:
        raise UniversalRemainingRouteAcceptanceError(
            "Operator sys_mode.switch spec is unavailable"
        )
    authority = ObsCommandAuthority.bind(
        principal_id="principal.ne01.acceptance.operator",
        tenant_id="ne01-universal-acceptance",
        request_id=request_id,
    )
    envelope = ObsCommandEnvelope(
        schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
        command_id="sys_mode.switch",
        action=spec.action,
        required_scopes=spec.required_scopes,
        payload=bind_server_authority(
            spec.payload_schema.validate(command_payload),
            spec=spec,
            authority=authority,
        ),
        authority=authority,
        claim_ref=str(claim.claim_ref),
        audit_ref=str(audit_ref),
        body_sha256=body_sha256,
        correlation_id="ne01-acceptance-operator-correlation",
    )
    return store, envelope, raw_body


def _runtime_authority_provider(
    embodiment: HostEmbodimentRuntime,
) -> dict[str, Any]:
    return {
        **embodiment.renew_authority(),
        "runtime_scope_id": embodiment.runtime_scope_id,
    }


async def append_operator_route(
    *,
    database: str | Path,
    evidence_root: str | Path,
) -> dict[str, str]:
    """Append one reviewed Operator terminal and settle its outer outcome."""

    db_path = Path(database).expanduser().resolve()
    output_root = Path(evidence_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    outcome_journal = ObsCommandOutcomeJournal(
        output_root / "operator-outcomes.jsonl"
    )
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = None
    embodiment = None
    hard_state_owner = None
    ingress_store = None
    runtime_group = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
            authenticated_outcome_lookup=outcome_journal.lookup,
        )
        embodiment = HostEmbodimentRuntime(
            repository=EmbodimentOutboxRepository(db_path),
            worker_id="ne01-universal-operator-context",
            command_port=_UnusedEmbodimentCommandPort(),
            authority_lease_ms=600_000,
        )
        hard_state_owner = HostHardStateOwner.bootstrap(
            initial_sys_mode="normal",
            endocrine_resolver=lambda **_kwargs: {},
            require_durable=True,
        )
        hard_state_owner.bind_durable_journal(
            SQLiteHardStateJournal(
                db_path,
                runtime_scope_id=embodiment.runtime_scope_id,
                runtime_authority_provider=(
                    lambda: _runtime_authority_provider(embodiment)
                ),
            ),
            initialization_event_id="ne01-universal-operator-initialized",
        )
        mode_service = ModeTransitionService(
            get_hard_state=hard_state_owner.get_hard_state,
            set_sys_mode=hard_state_owner.set_sys_mode,
            history_provider=hard_state_owner.durable_history,
            exact_history_provider=hard_state_owner.reopen_sys_mode_transition,
            ledger=ledger,
        )
        runtime_context = compose_host_runtime_context(
            task_runtime=container,
            ledger=ledger,
            embodiment_runtime=embodiment,
            environment_observation_provider=(
                runtime_environment_observation_provider(mode_service)
            ),
        )
        preview = ActionPreviewService(
            runtime_event_store=runtime_context.composition.event_store,
            guardian=runtime_context.composition.guardian,
            authority_resolver=ProductionActionPreviewAuthorityResolver(
                command_repository=container.command_repository,
                task_store=container.task_store,
                node_grant_verifier=DurableNodeGrantVerifier(
                    ledger=ledger,
                    review_store=container.review_continuation_store,
                ),
                runtime_context_application=runtime_context,
            ),
        )
        ingress_store, envelope, raw_body = _operator_ingress_authority(
            db_path,
            request_id=OPERATOR_REQUEST_ID,
        )
        token_service = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        intent_engine = IntentDecisionEngine(
            ledger=ledger,
            token_service=token_service,
            registry=build_agent_runtime_skill_registry(),
        )
        dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers=build_production_acceptance_effect_handlers(
                mode_transition_service_provider=lambda: mode_service,
            ),
            owner_id="ne01-universal-operator-effects",
            action_preview_service=preview,
        )
        effect_runtime = AcceptanceEffectRuntime(
            dispatcher=dispatcher,
            poll_seconds=0.01,
        )
        operator_runtime = build_governed_operator_runtime(
            task_runtime_container=container,
            ledger=ledger,
            token_service=token_service,
            intent_engine=intent_engine,
            runtime_context_application=runtime_context,
            action_preview_service=preview,
            acceptance_effect_dispatcher=dispatcher,
            mode_transition_service_provider=lambda: mode_service,
            cancellation_authority_verifier=None,
            secure_ingress_authority_resolver=ingress_store,
            owner_id="ne01-universal-operator-runtime",
        )
        registry = build_default_obs_domain_command_registry()
        port = registry.resolve_port("sys_mode.switch")
        if port is None:
            raise UniversalRemainingRouteAcceptanceError(
                "Operator sys_mode.switch port is unavailable"
            )
        outer_ingress = SecureObsCommandIngress(
            ingress_adapter=None,
            ingress_service=None,
            claim_result_store=ingress_store,
            outcome_journal=outcome_journal,
            registry=registry,
            router=None,
        )
        runtime_group = GovernedRuntimeGroup(
            {"operator": operator_runtime},
            review_store=container.review_continuation_store,
            review_decision_store=container.review_store,
            command_repository=container.command_repository,
            claim_result_store=ingress_store,
            operator_outcome_finalizer=outer_ingress,
            review_continuation_worker_factory=_NoopContinuationWorker,
            review_continuation_worker_id=(
                "ne01-universal-operator-review-worker"
            ),
            acceptance_effect_runtime=effect_runtime,
        )
        await runtime_group.activate()
        app_state = SimpleNamespace(
            governed_operator_runtime=operator_runtime,
        )
        outcome_key = ObsCommandOutcomeKey.from_values(
            principal_id=envelope.authority.principal_id,
            tenant_id=envelope.authority.tenant_id,
            idempotency_key=_OPERATOR_IDEMPOTENCY_KEY,
            command_id=envelope.command_id,
            action=envelope.action,
            required_scopes=envelope.required_scopes,
            body_sha256=envelope.body_sha256,
        )
        begun = outcome_journal.begin(
            outcome_key,
            claim_ref=envelope.claim_ref,
            audit_ref=envelope.audit_ref,
        )
        with bind_operator_command_runtime(app_state, raw_body=raw_body):
            first_domain = await port.execute(envelope)
        first = dict(first_domain.result["output"])
        continuation_ref = governed_review_continuation_ref(first_domain)
        if (
            not first_domain.success
            or str(first.get("status") or "") != "WAITING_REVIEW"
            or not continuation_ref
        ):
            raise UniversalRemainingRouteAcceptanceError(
                "Operator route did not enter one durable Review"
            )
        waiting = outer_ingress._mark_waiting_review(
            begun.record,
            domain_result_ref=first_domain.result_ref,
            continuation_ref=continuation_ref,
            principal_id=envelope.authority.principal_id,
        )
        if (
            str(waiting.terminal_state)
            != ObsCommandOutcomeState.WAITING_REVIEW.value
        ):
            raise UniversalRemainingRouteAcceptanceError(
                "Operator outer outcome did not enter WAITING_REVIEW"
            )
        review = dict(first["reviews"][0])
        approved = container.review_store.approve_review(
            review["review_id"],
            actor_id=envelope.authority.principal_id,
            expected_subject_ref=review["subject_ref"],
            expected_subject_version=review["subject_version"],
            expected_resume_phase=review["resume_phase"],
            expected_checkpoint_id=review["checkpoint_id"],
        )
        container.review_store.resume_review(
            review["review_id"],
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=review["subject_ref"],
            subject_version=review["subject_version"],
            resume_phase=review["resume_phase"],
            checkpoint_id=review["checkpoint_id"],
        )
        raw_claims = (
            container.review_continuation_store.claim_pending_review_continuations(
                worker_id="ne01-universal-operator-review-claim",
                lease_ms=60_000,
                limit=1,
            )
        )
        if len(raw_claims) != 1:
            raise UniversalRemainingRouteAcceptanceError(
                "Operator Review continuation claim is missing"
            )
        claim = (
            container.review_continuation_store.validate_review_continuation_claim(
                raw_claims[0]
            )
        )
        continued = await runtime_group.continue_review_continuation(claim)
        receipt = dict(continued.get("execution_receipt") or {})
        outcome = outcome_journal.lookup_by_claim_ref(envelope.claim_ref)
        if (
            receipt.get("accepted") is not True
            or str(receipt.get("status") or "") != "ACCEPTED"
            or hard_state_owner.get_hard_state().sys_mode != "focus"
            or outcome is None
            or outcome.state is not ObsCommandOutcomeState.SUCCEEDED
        ):
            raise UniversalRemainingRouteAcceptanceError(
                "Operator route did not reach one accepted durable terminal"
            )
        return {"operator": OPERATOR_REQUEST_ID}
    finally:
        if runtime_group is not None:
            await runtime_group.aclose()
        if ingress_store is not None:
            ingress_store.close()
        if hard_state_owner is not None:
            hard_state_owner.unbind_durable_journal()
        if embodiment is not None:
            embodiment.close()
        if container is not None:
            await container.aclose()
        ledger.close()


async def append_remaining_routes(
    *,
    database: str | Path,
    evidence_root: str | Path,
) -> dict[str, str]:
    """Append Scheduler, Internal and Operator and return their request map."""

    db_path = Path(database).expanduser().resolve()
    if not db_path.is_file():
        raise UniversalRemainingRouteAcceptanceError(
            f"canonical acceptance database is unavailable: {db_path}"
        )
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-universal-remaining-route-migrations",
        ts_ms=int(time.time() * 1_000),
    )
    if not migrated.success:
        raise UniversalRemainingRouteAcceptanceError(
            "canonical acceptance database migrations are not ready"
        )
    routes = await append_scheduler_and_internal_routes(database=db_path)
    routes.update(
        await append_operator_route(
            database=db_path,
            evidence_root=evidence_root,
        )
    )
    return routes


__all__ = [
    "INTERNAL_REQUEST_ID",
    "OPERATOR_REQUEST_ID",
    "SCHEDULER_REQUEST_ID",
    "UniversalRemainingRouteAcceptanceError",
    "append_remaining_routes",
]
