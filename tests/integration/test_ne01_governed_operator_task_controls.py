from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from host_brain.app.governed_operator_runtime import (
    build_governed_operator_composition,
)
from host_brain.app.host_hard_state import HostHardStateOwner
from host_brain.app.host_mode_policy import HostModePolicyOwner
from host_brain.app.host_runtime_context_wiring import (
    compose_action_preview_service,
    compose_host_runtime_context,
    runtime_environment_observation_provider,
)
from host_brain.app.observability_runtime.domain_command_ports import (
    build_default_obs_domain_command_registry,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.embodiment import HostEmbodimentRuntime
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.composition.task_runtime import (
    build_process_task_runtime_container,
)
from host_brain.core.mode_transition import ModeTransitionService
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.observability.command_ports import (
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandEnvelope,
    bind_server_authority,
)
from host_brain.observability.operator_command_gateway import (
    bind_operator_command_runtime,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.hard_state_journal import SQLiteHardStateJournal
from host_brain.persistence.host_mode_policy_journal import (
    SQLiteHostModePolicyJournal,
)
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
)
from host_brain.secure_ingress import (
    ClaimStatus,
    IngressAuditEvent,
    SecureIngressAuthority,
)
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
)
from host_brain.scheduler.governed_runtime import (
    SchedulerCommandService,
    SchedulerDefinitionPolicyEvaluator,
)
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry
from host_brain.skills.agent_runtime_registry import (
    build_agent_runtime_skill_registry,
)
from natsume_contracts.task import HostCommandSurface


_PRINCIPAL = "principal.operator.task-control"
_TENANT = "tenant-operator-task-control"


class _UnusedEmbodimentPort:
    async def submit(self, _command: Any) -> Any:
        raise AssertionError("Operator Task control must not invoke Edge")


class _OperatorOutcomeFinalizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def finalize_review_outcome(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


def _runtime_authority_provider(
    embodiment_runtime: HostEmbodimentRuntime,
) -> dict[str, Any]:
    return {
        **embodiment_runtime.renew_authority(),
        "runtime_scope_id": embodiment_runtime.runtime_scope_id,
    }


def _operator_envelope(
    *,
    store: SQLiteSecureIngressStore,
    registry: Any,
    command_id: str,
    payload: dict[str, Any],
    request_id: str,
) -> tuple[ObsCommandEnvelope, bytes]:
    spec = registry.resolve_spec(command_id)
    assert spec is not None
    normalized = spec.payload_schema.validate(payload)
    raw_body = json.dumps(
        {
            "command_id": command_id,
            "payload": payload,
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    now = int(time.time() * 1_000)
    claim = store.try_claim(
        principal_id=_PRINCIPAL,
        surface="obs.command",
        action=spec.action,
        required_scopes=spec.required_scopes,
        idempotency_key=f"idempotency:{request_id}",
        nonce=f"nonce:{request_id}",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=now,
        expires_at_ms=now + 600_000,
    )
    assert claim.status is ClaimStatus.NEW
    assert claim.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"correlation:{request_id}",
            surface="obs.command",
            action=spec.action,
            method="POST",
            normalized_path=f"/v1/host/obs-commands/{command_id}",
            principal_id=_PRINCIPAL,
            tenant_id=_TENANT,
            key_id="operator-task-control-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + "1" * 64,
            requested_scopes=spec.required_scopes,
            required_scopes=spec.required_scopes,
            body_sha256=body_sha256,
            idempotency_digest="sha256:" + hashlib.sha256(
                f"idempotency:{request_id}".encode("utf-8")
            ).hexdigest(),
            nonce_digest="sha256:" + hashlib.sha256(
                f"nonce:{request_id}".encode("utf-8")
            ).hexdigest(),
            transport_key_digest="sha256:" + "2" * 64,
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=claim.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=now,
        ),
    )
    authority = ObsCommandAuthority.bind(
        principal_id=_PRINCIPAL,
        tenant_id=_TENANT,
        request_id=request_id,
    )
    return (
        ObsCommandEnvelope(
            schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
            command_id=command_id,
            action=spec.action,
            required_scopes=spec.required_scopes,
            payload=bind_server_authority(
                normalized,
                spec=spec,
                authority=authority,
            ),
            authority=authority,
            claim_ref=str(claim.claim_ref),
            audit_ref=str(audit_ref),
            body_sha256=body_sha256,
            correlation_id=f"correlation:{request_id}",
        ),
        raw_body,
    )


def _scheduler_admin_authority(
    *,
    store: SQLiteSecureIngressStore,
    request_id: str,
    method: str,
    path: str,
    payload: dict[str, Any],
) -> SecureIngressAuthority:
    raw_body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    now = int(time.time() * 1_000)
    claim = store.try_claim(
        principal_id=_PRINCIPAL,
        surface="scheduler.admin",
        action="scheduler:admin",
        required_scopes=("scheduler:admin",),
        idempotency_key=f"idempotency:{request_id}",
        nonce=f"nonce:{request_id}",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=now,
        expires_at_ms=now + 600_000,
    )
    assert claim.status is ClaimStatus.NEW
    assert claim.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"correlation:{request_id}",
            surface="scheduler.admin",
            action="scheduler:admin",
            method=method,
            normalized_path=path,
            principal_id=_PRINCIPAL,
            tenant_id=_TENANT,
            key_id="scheduler-admin-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + "3" * 64,
            requested_scopes=("scheduler:admin",),
            required_scopes=("scheduler:admin",),
            body_sha256=body_sha256,
            idempotency_digest="sha256:" + hashlib.sha256(
                f"idempotency:{request_id}".encode("utf-8")
            ).hexdigest(),
            nonce_digest="sha256:" + hashlib.sha256(
                f"nonce:{request_id}".encode("utf-8")
            ).hexdigest(),
            transport_key_digest="sha256:" + "4" * 64,
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=claim.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=now,
        ),
    )
    return SecureIngressAuthority(
        allowed=True,
        duplicate=False,
        reason_code="SECURE_INGRESS_ALLOWED",
        request_id=request_id,
        principal_id=_PRINCIPAL,
        claim_ref=str(claim.claim_ref),
        audit_ref=str(audit_ref),
        body_sha256=body_sha256,
    )


async def _eventually(
    read: Callable[[], Any],
    *,
    accepted: Callable[[Any], bool],
    timeout_seconds: float = 8.0,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: Any = None
    while asyncio.get_running_loop().time() < deadline:
        last = read()
        if accepted(last):
            return last
        await asyncio.sleep(0.02)
    raise AssertionError(f"durable state did not converge: {last!r}")


@pytest.mark.asyncio
async def test_task_controls_cross_governed_acceptance_and_cancel_exact_graph(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "governed-operator-task-controls.sqlite3"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = None
    embodiment_runtime = None
    hard_state_owner = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
        )
        embodiment_runtime = HostEmbodimentRuntime(
            repository=EmbodimentOutboxRepository(db_path),
            worker_id="ne01-operator-task-control-runtime",
            command_port=_UnusedEmbodimentPort(),
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
                runtime_scope_id=embodiment_runtime.runtime_scope_id,
                runtime_authority_provider=(
                    lambda: _runtime_authority_provider(
                        embodiment_runtime
                    )
                ),
            ),
            initialization_event_id="operator-task-control-hard-state",
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
            embodiment_runtime=embodiment_runtime,
            environment_observation_provider=(
                runtime_environment_observation_provider(mode_service)
            ),
        )
        action_preview = compose_action_preview_service(
            task_runtime=container,
            ledger=ledger,
            runtime_context_application=runtime_context,
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
        ingress_store = SQLiteSecureIngressStore(db_path, rate_limit=100)
        composition = build_governed_operator_composition(
            task_runtime_container=container,
            ledger=ledger,
            token_service=token_service,
            intent_engine=intent_engine,
            runtime_context_application=runtime_context,
            action_preview_service=action_preview,
            mode_transition_service_provider=lambda: mode_service,
            cancellation_authority_verifier=(
                SecureIngressGraphCancellationAuthorityVerifier(
                    ingress_store
                )
            ),
            secure_ingress_authority_resolver=ingress_store,
            runtime_owner_id="ne01-operator-task-control",
            embodiment_runtime_provider=lambda: embodiment_runtime,
        )
        finalizer = _OperatorOutcomeFinalizer()
        runtime_group = GovernedRuntimeGroup(
            {"operator": composition.runtime},
            review_store=container.review_continuation_store,
            review_decision_store=container.review_store,
            command_repository=container.command_repository,
            claim_result_store=ingress_store,
            operator_outcome_finalizer=finalizer,
            review_continuation_worker_id=(
                "ne01-operator-task-control-review-worker"
            ),
            graph_cancellation_operator_enabled=True,
            graph_cancellation_state_reader=container.task_store,
            acceptance_effect_runtime=composition.acceptance_effect_runtime,
            operation_reacquisition_owner_id=(
                "ne01-operator-task-control"
            ),
        )
        composition.review_command_service.bind_wake_continuation(
            runtime_group.wake_review_continuation
        )
        container.bind_runtime(runtime_group)
        await container.activate_runtime()
        registry = build_default_obs_domain_command_registry(
            degradation_capability_port_provider=(
                lambda: runtime_context.composition.guardian
            )
        )
        app_state = SimpleNamespace(
            governed_operator_runtime=composition.runtime,
            task_graph_cancellation_operator_enabled=True,
        )

        async def execute(
            command_id: str,
            payload: dict[str, Any],
            request_id: str,
        ) -> tuple[ObsCommandEnvelope, bytes, Any]:
            envelope, raw_body = _operator_envelope(
                store=ingress_store,
                registry=registry,
                command_id=command_id,
                payload=payload,
                request_id=request_id,
            )
            port = registry.resolve_port(command_id)
            assert port is not None
            with bind_operator_command_runtime(
                app_state,
                raw_body=raw_body,
            ):
                result = await port.execute(envelope)
            return envelope, raw_body, result

        target_envelope, _target_body, target_result = await execute(
            "sys_mode.switch",
            {
                "target_mode": "focus",
                "source": "manual",
                "reason": "create one cancellable governed graph",
            },
            "request-ne01-task-control-target",
        )
        target = dict(target_result.result["output"])
        assert target["status"] == "WAITING_REVIEW"
        assert hard_state_owner.get_hard_state().sys_mode == "normal"

        cancel_envelope, cancel_body, cancel_result = await execute(
            "task_graph.cancel",
            {
                "graph_id": target["graph_id"],
                "graph_revision": target["graph_revision"],
                "reason_code": "OPERATOR_REQUESTED",
            },
            "request-ne01-task-control-cancel",
        )
        cancellation = dict(cancel_result.result["output"])
        assert cancellation["status"] == "WAITING_REVIEW"
        assert cancel_result.side_effects_executed is False
        cancel_review = dict(cancellation["reviews"][0])
        assert container.task_store.get_graph_cancellation_for_graph(
            target["graph_id"],
            target["graph_revision"],
        ) is None

        approve_envelope, approve_body, approve_result = await execute(
            "task_review.approve",
            {
                "review_id": cancel_review["review_id"],
                "subject_ref": cancel_review["subject_ref"],
                "subject_version": cancel_review["subject_version"],
                "resume_phase": cancel_review["resume_phase"],
                "checkpoint_id": cancel_review["checkpoint_id"],
            },
            "request-ne01-task-control-approve",
        )
        approved = dict(approve_result.result["output"])
        public_token = str(approved["resume_token_ref"])
        assert public_token.startswith("review-token://")
        assert "review_resume::" not in json.dumps(
            approved,
            sort_keys=True,
        )
        approved_record = container.review_store.get_review(
            cancel_review["review_id"]
        )
        approved_version = approved_record["status_version"]

        approve_port = registry.resolve_port("task_review.approve")
        assert approve_port is not None
        with bind_operator_command_runtime(
            app_state,
            raw_body=approve_body,
        ):
            approve_replay = await approve_port.execute(approve_envelope)
        assert approve_replay.side_effects_executed is False
        assert container.review_store.get_review(
            cancel_review["review_id"]
        )["status_version"] == approved_version

        _resume_envelope, _resume_body, resume_result = await execute(
            "task_review.resume",
            {
                "review_id": cancel_review["review_id"],
                "resume_token_ref": public_token,
                "subject_ref": cancel_review["subject_ref"],
                "subject_version": cancel_review["subject_version"],
                "resume_phase": cancel_review["resume_phase"],
                "checkpoint_id": cancel_review["checkpoint_id"],
            },
            "request-ne01-task-control-resume",
        )
        resumed = dict(resume_result.result["output"])
        assert resumed["review_status"] == "CONSUMED"
        assert resume_result.side_effects_executed is True

        cancellation_intent = await _eventually(
            lambda: container.task_store.get_graph_cancellation_for_graph(
                target["graph_id"],
                target["graph_revision"],
            ),
            accepted=lambda value: (
                isinstance(value, dict)
                and str(value.get("status") or "") == "FINALIZED"
            ),
        )
        assert cancellation_intent["payload"]["request_id"] == (
            cancel_envelope.authority.request_id
        )
        assert cancellation_intent["payload"]["request_identity"] == (
            cancel_envelope.audit_ref
        )
        assert cancellation_intent["payload"]["source"] == "OPERATOR"
        cancelled_graph = container.task_store.get_graph(
            target["graph_id"],
            target["graph_revision"],
        )
        assert cancelled_graph["status"] == "CANCELLED"
        assert hard_state_owner.get_hard_state().sys_mode == "normal"

        cancel_port = registry.resolve_port("task_graph.cancel")
        assert cancel_port is not None
        with bind_operator_command_runtime(app_state, raw_body=cancel_body):
            cancel_replay = await cancel_port.execute(cancel_envelope)
        cancel_replayed = dict(cancel_replay.result["output"])
        assert cancel_replayed["exact_replay"] is True
        assert cancel_replayed["cancellation_status"] == "FINALIZED"
        assert cancel_replay.side_effects_executed is False

        with bind_operator_command_runtime(app_state, raw_body=approve_body):
            approve_after_resume = await approve_port.execute(approve_envelope)
        historical_approval = dict(approve_after_resume.result["output"])
        assert historical_approval["exact_replay"] is True
        assert historical_approval["review_status"] == "APPROVED"
        assert historical_approval["review_status_version"] == 1
        assert historical_approval["resume_token_ref"] == public_token
        assert approve_after_resume.side_effects_executed is False

        _second_envelope, _second_body, second_result = await execute(
            "sys_mode.switch",
            {
                "target_mode": "maintenance",
                "source": "manual",
                "reason": "create one governed rejection",
            },
            "request-ne01-task-control-reject-target",
        )
        second = dict(second_result.result["output"])
        second_review = dict(second["reviews"][0])
        _reject_envelope, _reject_body, reject_result = await execute(
            "task_review.reject",
            {"review_id": second_review["review_id"]},
            "request-ne01-task-control-reject",
        )
        rejected = dict(reject_result.result["output"])
        assert rejected["review_status"] == "REJECTED"
        await _eventually(
            lambda: container.review_store.get_review(
                second_review["review_id"]
            ),
            accepted=lambda value: (
                isinstance(value, dict)
                and value.get("review", {}).get("status") == "REJECTED"
            ),
        )

        for request_id in (
            "request-ne01-task-control-cancel",
            "request-ne01-task-control-approve",
            "request-ne01-task-control-resume",
            "request-ne01-task-control-reject",
        ):
            record = container.command_repository.get_by_request(
                surface=HostCommandSurface.OPERATOR.value,
                request_id=request_id,
            )
            assert record is not None
            work_order_json = json.dumps(
                record["work_order"].to_dict(),
                ensure_ascii=False,
                sort_keys=True,
            )
            assert "review_resume::" not in work_order_json
            assert (
                record["work_order"].metadata["operator_command_id"]
                in {
                    "task_graph.cancel",
                    "task_review.approve",
                    "task_review.resume",
                    "task_review.reject",
                }
            )
        assert b"review_resume::" not in db_path.read_bytes()
        assert finalizer.calls
        assert target_envelope.body_sha256
    finally:
        if container is not None:
            await container.aclose()
        if hard_state_owner is not None:
            hard_state_owner.unbind_durable_journal()
        if embodiment_runtime is not None:
            embodiment_runtime.close()
        ledger.close()


@pytest.mark.asyncio
async def test_scheduler_and_mode_policy_controls_commit_only_after_acceptance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "governed-operator-state-controls.sqlite3"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = None
    embodiment_runtime = None
    hard_state_owner = None
    mode_policy_owner = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
        )
        embodiment_runtime = HostEmbodimentRuntime(
            repository=EmbodimentOutboxRepository(db_path),
            worker_id="ne01-operator-state-control-runtime",
            command_port=_UnusedEmbodimentPort(),
            authority_lease_ms=600_000,
        )
        runtime_authority = lambda: _runtime_authority_provider(
            embodiment_runtime
        )
        hard_state_owner = HostHardStateOwner.bootstrap(
            initial_sys_mode="normal",
            endocrine_resolver=lambda **_kwargs: {},
            require_durable=True,
        )
        hard_state_owner.bind_durable_journal(
            SQLiteHardStateJournal(
                db_path,
                runtime_scope_id=embodiment_runtime.runtime_scope_id,
                runtime_authority_provider=runtime_authority,
            ),
            initialization_event_id="operator-state-control-hard-state",
        )
        now = int(time.time() * 1_000)
        mode_policy_owner = HostModePolicyOwner.bootstrap(
            initial_last_interaction_ts_ms=now,
            require_durable=True,
        )
        mode_policy_owner.bind_durable_journal(
            SQLiteHostModePolicyJournal(
                db_path,
                runtime_scope_id=embodiment_runtime.runtime_scope_id,
                runtime_authority_provider=runtime_authority,
                replay_page_size=17,
            ),
            initialization_operation_id=(
                "operator-state-control-mode-policy"
            ),
        )
        mode_service = ModeTransitionService(
            get_hard_state=hard_state_owner.get_hard_state,
            set_sys_mode=hard_state_owner.set_sys_mode,
            history_provider=hard_state_owner.durable_history,
            exact_history_provider=hard_state_owner.reopen_sys_mode_transition,
            mode_policy=mode_policy_owner,
            ledger=ledger,
        )
        definitions = container.scheduler_definition_repository
        definitions.publish_definition(
            task_id="operator_state_scheduler",
            definition={
                "trigger_mode": "interval",
                "trigger_config": {"seconds": 60},
                "intent_template": {
                    "skill_name": "operator_state_worker",
                    "args": {},
                },
                "worker_key": "operator_state_worker",
                "priority": "HIGH",
                "mode_allowlist": ["normal"],
                "resource_profile": {},
                "retry_policy": {
                    "max_attempts": 1,
                    "retry_backoff_ms": 0,
                },
                "risk_level": 3,
                "enabled": True,
            },
            expected_head_version=0,
            next_scheduled_at_ms=now + 60_000,
        )
        runtime_context = compose_host_runtime_context(
            task_runtime=container,
            ledger=ledger,
            embodiment_runtime=embodiment_runtime,
            environment_observation_provider=(
                runtime_environment_observation_provider(mode_service)
            ),
        )
        action_preview = compose_action_preview_service(
            task_runtime=container,
            ledger=ledger,
            runtime_context_application=runtime_context,
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
        ingress_store = SQLiteSecureIngressStore(db_path, rate_limit=200)
        scheduler_workers = SchedulerWorkerRegistry()
        scheduler_workers.register(
            "operator_state_worker",
            lambda **_kwargs: {"status": "SUCCEEDED"},
        )
        scheduler_service = SchedulerCommandService(
            runtime=SimpleNamespace(),
            intent_engine=intent_engine,
            claim_result_store=ingress_store,
            definition_repository=definitions,
            policy_evaluator=SchedulerDefinitionPolicyEvaluator(
                current_mode=lambda: "normal"
            ),
            worker_registry=scheduler_workers,
        )
        composition = build_governed_operator_composition(
            task_runtime_container=container,
            ledger=ledger,
            token_service=token_service,
            intent_engine=intent_engine,
            runtime_context_application=runtime_context,
            action_preview_service=action_preview,
            mode_transition_service_provider=lambda: mode_service,
            scheduler_command_service_provider=lambda: scheduler_service,
            cancellation_authority_verifier=(
                SecureIngressGraphCancellationAuthorityVerifier(
                    ingress_store
                )
            ),
            secure_ingress_authority_resolver=ingress_store,
            runtime_owner_id="ne01-operator-state-control",
            embodiment_runtime_provider=lambda: embodiment_runtime,
        )
        finalizer = _OperatorOutcomeFinalizer()
        runtime_group = GovernedRuntimeGroup(
            {"operator": composition.runtime},
            review_store=container.review_continuation_store,
            review_decision_store=container.review_store,
            command_repository=container.command_repository,
            claim_result_store=ingress_store,
            operator_outcome_finalizer=finalizer,
            review_continuation_worker_id=(
                "ne01-operator-state-control-review-worker"
            ),
            acceptance_effect_runtime=composition.acceptance_effect_runtime,
            operation_reacquisition_owner_id=(
                "ne01-operator-state-control"
            ),
        )
        composition.review_command_service.bind_wake_continuation(
            runtime_group.wake_review_continuation
        )
        container.bind_runtime(runtime_group)
        await container.activate_runtime()
        registry = build_default_obs_domain_command_registry(
            degradation_capability_port_provider=(
                lambda: runtime_context.composition.guardian
            )
        )
        app_state = SimpleNamespace(
            governed_operator_runtime=composition.runtime,
        )

        async def execute_new(
            command_id: str,
            payload: dict[str, Any],
            request_id: str,
        ) -> tuple[ObsCommandEnvelope, bytes, Any]:
            envelope, raw_body = _operator_envelope(
                store=ingress_store,
                registry=registry,
                command_id=command_id,
                payload=payload,
                request_id=request_id,
            )
            port = registry.resolve_port(command_id)
            assert port is not None
            with bind_operator_command_runtime(
                app_state,
                raw_body=raw_body,
            ):
                result = await port.execute(envelope)
            return envelope, raw_body, result

        async def replay(
            command_id: str,
            envelope: ObsCommandEnvelope,
            raw_body: bytes,
        ) -> Any:
            port = registry.resolve_port(command_id)
            assert port is not None
            with bind_operator_command_runtime(
                app_state,
                raw_body=raw_body,
            ):
                return await port.execute(envelope)

        async def accepted(
            command_id: str,
            payload: dict[str, Any],
            request_id: str,
        ) -> tuple[ObsCommandEnvelope, bytes, Any]:
            envelope, raw_body, initial = await execute_new(
                command_id,
                payload,
                request_id,
            )
            output = dict(initial.result["output"])
            if output.get("status") != "WAITING_REVIEW":
                assert output["success"] is True
                return envelope, raw_body, initial
            review = dict(output["reviews"][0])
            _, _, approved = await execute_new(
                "task_review.approve",
                {
                    "review_id": review["review_id"],
                    "subject_ref": review["subject_ref"],
                    "subject_version": review["subject_version"],
                    "resume_phase": review["resume_phase"],
                    "checkpoint_id": review["checkpoint_id"],
                },
                f"{request_id}-approve",
            )
            public_token = str(
                approved.result["output"]["resume_token_ref"]
            )
            _, _, resumed = await execute_new(
                "task_review.resume",
                {
                    "review_id": review["review_id"],
                    "resume_token_ref": public_token,
                    "subject_ref": review["subject_ref"],
                    "subject_version": review["subject_version"],
                    "resume_phase": review["resume_phase"],
                    "checkpoint_id": review["checkpoint_id"],
                },
                f"{request_id}-resume",
            )
            assert resumed.result["output"]["review_status"] == "CONSUMED"
            deadline = asyncio.get_running_loop().time() + 8
            while asyncio.get_running_loop().time() < deadline:
                final = await replay(command_id, envelope, raw_body)
                if final.result["output"].get("status") != "WAITING_REVIEW":
                    assert final.result["output"]["success"] is True
                    return envelope, raw_body, final
                await asyncio.sleep(0.02)
            raise AssertionError(
                f"state control did not complete: {command_id}"
            )

        async def accepted_scheduler_admin(
            *,
            command_id: str,
            payload: dict[str, Any],
            request_id: str,
            method: str,
            path: str,
        ) -> tuple[SecureIngressAuthority, dict[str, Any]]:
            authority = _scheduler_admin_authority(
                store=ingress_store,
                request_id=request_id,
                method=method,
                path=path,
                payload=payload,
            )

            async def invoke() -> dict[str, Any]:
                return dict(
                    await composition.runtime.execute_scheduler_admin_control(
                        command_id=command_id,
                        payload=payload,
                        authority=authority,
                        request_id=request_id,
                        method=method,
                        normalized_path=path,
                    )
                )

            initial = await invoke()
            if initial.get("status") != "WAITING_REVIEW":
                assert initial["success"] is True
                return authority, initial
            review = dict(initial["reviews"][0])
            _, _, approved = await execute_new(
                "task_review.approve",
                {
                    "review_id": review["review_id"],
                    "subject_ref": review["subject_ref"],
                    "subject_version": review["subject_version"],
                    "resume_phase": review["resume_phase"],
                    "checkpoint_id": review["checkpoint_id"],
                },
                f"{request_id}-approve",
            )
            public_token = str(
                approved.result["output"]["resume_token_ref"]
            )
            _, _, resumed = await execute_new(
                "task_review.resume",
                {
                    "review_id": review["review_id"],
                    "resume_token_ref": public_token,
                    "subject_ref": review["subject_ref"],
                    "subject_version": review["subject_version"],
                    "resume_phase": review["resume_phase"],
                    "checkpoint_id": review["checkpoint_id"],
                },
                f"{request_id}-resume",
            )
            assert resumed.result["output"]["review_status"] == "CONSUMED"
            final = await _eventually(
                lambda: ingress_store.resolve_committed_authority_by_audit(
                    audit_ref=str(authority.audit_ref)
                ),
                accepted=lambda value: bool(
                    isinstance(value, dict)
                    and value.get("claim_result_ref")
                ),
            )
            assert final["claim_result_ref"]
            replayed = await invoke()
            assert replayed["exact_replay"] is True
            return authority, replayed

        publish_definition = {
            "trigger_mode": "interval",
            "trigger_config": {"seconds": 120},
            "intent_template": {
                "skill_name": "operator_state_worker",
                "args": {},
            },
            "worker_key": "operator_state_worker",
            "priority": "HIGH",
            "mode_allowlist": ["normal"],
            "resource_profile": {},
            "retry_policy": {
                "max_attempts": 1,
                "retry_backoff_ms": 0,
            },
            "risk_level": 3,
            "enabled": True,
        }
        publish_payload = {
            "task_id": "scheduler_http_governed",
            "definition": publish_definition,
            "expected_head_version": 0,
            "next_scheduled_at_ms": now + 120_000,
        }
        publish_authority, published = await accepted_scheduler_admin(
            command_id="scheduler.task.publish",
            payload=publish_payload,
            request_id="request-ne01-scheduler-http-publish",
            method="POST",
            path="/scheduler/tasks",
        )
        assert published["task_id"] == "scheduler_http_governed"
        assert published["definition"]["task_id"] == (
            "scheduler_http_governed"
        )
        published_head = definitions.get("scheduler_http_governed")
        assert published_head["head_version"] == published["head_version"]
        publish_record = container.command_repository.get_by_request(
            surface=HostCommandSurface.SCHEDULER.value,
            request_id=str(publish_authority.request_id),
        )
        assert publish_record is not None
        assert publish_record["command"].target_id == (
            "operator_state_control"
        )

        updated_definition = {
            **publish_definition,
            "description": "governed Scheduler PATCH",
        }
        _, updated = await accepted_scheduler_admin(
            command_id="scheduler.task.publish",
            payload={
                "task_id": "scheduler_http_governed",
                "definition": updated_definition,
                "expected_head_version": published["head_version"],
                "next_scheduled_at_ms": now + 180_000,
            },
            request_id="request-ne01-scheduler-http-update",
            method="PATCH",
            path="/host-scheduler/tasks/scheduler_http_governed",
        )
        assert updated["definition"]["description"] == (
            "governed Scheduler PATCH"
        )

        _, disabled_http = await accepted_scheduler_admin(
            command_id="scheduler.task.disable",
            payload={
                "task_id": "scheduler_http_governed",
                "expected_head_version": updated["head_version"],
            },
            request_id="request-ne01-scheduler-http-disable",
            method="POST",
            path=(
                "/scheduler/tasks/scheduler_http_governed/disable"
            ),
        )
        assert disabled_http["enabled"] is False

        delete_authority, deleted = await accepted_scheduler_admin(
            command_id="scheduler.task.delete",
            payload={
                "task_id": "scheduler_http_governed",
                "expected_head_version": disabled_http["head_version"],
            },
            request_id="request-ne01-scheduler-http-delete",
            method="DELETE",
            path="/scheduler/tasks/scheduler_http_governed",
        )
        assert deleted["deleted"] is True
        retired_head = definitions.get("scheduler_http_governed")
        assert retired_head["retired"] is True
        assert retired_head["enabled"] is False
        delete_claim = ingress_store.resolve_committed_authority_by_audit(
            audit_ref=str(delete_authority.audit_ref)
        )
        assert delete_claim["claim_result_ref"]

        disabled_envelope, disabled_body, disabled_result = await accepted(
            "scheduler.task.disable",
            {"task_id": "operator_state_scheduler"},
            "request-ne01-scheduler-disable",
        )
        disabled = dict(disabled_result.result["output"])
        assert disabled["enabled"] is False
        assert definitions.get("operator_state_scheduler")["enabled"] is False
        assert disabled_result.side_effects_executed is True

        _, _, enabled_result = await accepted(
            "scheduler.task.enable",
            {
                "task_id": "operator_state_scheduler",
                "expected_head_version": disabled["head_version"],
            },
            "request-ne01-scheduler-enable",
        )
        enabled = dict(enabled_result.result["output"])
        assert enabled["enabled"] is True
        assert definitions.get("operator_state_scheduler")["enabled"] is True

        rules_before = mode_policy_owner.snapshot()
        rules_envelope, rules_body, rules_result = await accepted(
            "sys_mode.auto_rules.update",
            {"rules": {"idle_to_sleep": False}},
            "request-ne01-mode-rules",
        )
        rules = dict(rules_result.result["output"])
        assert rules["auto_rules"]["idle_to_sleep"] is False
        assert rules["policy_revision"] == rules_before.revision + 1
        assert mode_policy_owner.snapshot().auto_rules["idle_to_sleep"] is False

        hard_state_owner.set_sys_mode(
            "sleep",
            expected_from_mode="normal",
            event_id="state-control-event-precondition",
            source="skill",
            reason="prepare governed wakeup event",
        )
        event_envelope, event_body, event_result = await accepted(
            "sys_mode.event.submit",
            {"event_type": "user_message_received"},
            "request-ne01-mode-event",
        )
        event = dict(event_result.result["output"])
        assert event["triggered"] is True
        assert event["result"]["to_mode"] == "normal"
        assert hard_state_owner.get_hard_state().sys_mode == "normal"

        revisions = (
            definitions.get("operator_state_scheduler")["head_version"],
            mode_policy_owner.snapshot().revision,
        )
        for command_id, envelope, raw_body in (
            (
                "scheduler.task.disable",
                disabled_envelope,
                disabled_body,
            ),
            (
                "sys_mode.auto_rules.update",
                rules_envelope,
                rules_body,
            ),
            (
                "sys_mode.event.submit",
                event_envelope,
                event_body,
            ),
        ):
            replayed = await replay(command_id, envelope, raw_body)
            output = dict(replayed.result["output"])
            assert output["exact_replay"] is True
            assert replayed.side_effects_executed is False
        assert (
            definitions.get("operator_state_scheduler")["head_version"],
            mode_policy_owner.snapshot().revision,
        ) == revisions

        for request_id in (
            "request-ne01-scheduler-disable",
            "request-ne01-scheduler-enable",
            "request-ne01-mode-rules",
            "request-ne01-mode-event",
        ):
            record = container.command_repository.get_by_request(
                surface=HostCommandSurface.OPERATOR.value,
                request_id=request_id,
            )
            assert record is not None
            assert record["command"].target_id == "operator_state_control"
            assert record["work_order"].metadata["operator_command_id"] in {
                "scheduler.task.disable",
                "scheduler.task.enable",
                "sys_mode.auto_rules.update",
                "sys_mode.event.submit",
            }
        assert finalizer.calls
    finally:
        if container is not None:
            await container.aclose()
        if mode_policy_owner is not None:
            mode_policy_owner.unbind_durable_journal()
        if hard_state_owner is not None:
            hard_state_owner.unbind_durable_journal()
        if embodiment_runtime is not None:
            embodiment_runtime.close()
        ledger.close()
