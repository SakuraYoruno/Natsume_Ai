from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
from host_brain.app.observability_runtime.domain_command_ports import (
    build_default_obs_domain_command_registry,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.embodiment import HostEmbodimentRuntime
from host_brain.composition.task_runtime import (
    build_process_task_runtime_container,
)
from host_brain.contracts.agent_runtime.operation_reconciliation import (
    build_reconciliation_attestation_binding,
)
from host_brain.core.mode_transition import ModeTransitionService
from host_brain.governor.action_preview import ActionPreviewService
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
from host_brain.orchestration import GovernedCommandApplication
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.hard_state_journal import SQLiteHardStateJournal
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
)
from host_brain.persistence.task_store_types import TaskStoreError
from host_brain.runtime.action_preview_resolver import (
    ProductionActionPreviewAuthorityResolver,
)
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.skills.agent_runtime_registry import (
    build_agent_runtime_skill_registry,
)
from host_brain.task_runtime import AdmittedExecution
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from natsume_contracts.task import HostCommandSurface
from tests.integration.test_ne01_governed_operator_sys_mode import (
    _UnusedEmbodimentCommandPort,
    _runtime_authority_provider,
)
from tests.integration.test_ne01_operation_online_takeover import (
    _ProjectedWorker,
    _contracts,
    _coordinator,
    _events,
)
from tests.integration.test_ne01_signed_operation_reacquisition import (
    _FailOnceWorker,
)


_COMMAND_ID = "task_operation.reconcile"
_PRINCIPAL_ID = "operator.operation.integration"


class _TargetContinuation:
    def __init__(
        self,
        application: GovernedCommandApplication,
        contracts: tuple[Any, Any, Any, Any],
    ) -> None:
        self._application = application
        self._contracts = contracts
        self.calls = 0

    async def continue_operation_reacquisition(
        self,
        _reconciliation: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls += 1
        events = await _events(self._application, self._contracts)
        return dict(events[-1])


def _operation_ingress_envelope(
    db_path: Path,
    *,
    request_id: str,
    payload: dict[str, Any],
) -> tuple[SQLiteSecureIngressStore, ObsCommandEnvelope, bytes]:
    raw_body = json.dumps(
        {
            "command_id": _COMMAND_ID,
            "payload": payload,
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    current = int(time.time() * 1_000)
    scopes = ("obs:write", "task-runtime:reconcile")
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    claim = store.try_claim(
        principal_id=_PRINCIPAL_ID,
        surface="obs.command",
        action="obs:command:task_operation.reconcile",
        required_scopes=scopes,
        idempotency_key="operation-reconciliation-integration-idempotency",
        nonce=f"operation-reconciliation-integration-nonce:{request_id}",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=current,
        expires_at_ms=current + 600_000,
    )
    assert claim.status is ClaimStatus.NEW and claim.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id="operation-reconciliation-integration-correlation",
            surface="obs.command",
            action="obs:command:task_operation.reconcile",
            method="POST",
            normalized_path="/v1/host/obs-commands/task_operation.reconcile",
            principal_id=_PRINCIPAL_ID,
            tenant_id="operation-reconciliation-integration",
            key_id="operation-reconciliation-integration-hmac",
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
            ts_ms=current,
        ),
    )
    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec(_COMMAND_ID)
    assert spec is not None
    authority = ObsCommandAuthority.bind(
        principal_id=_PRINCIPAL_ID,
        tenant_id="operation-reconciliation-integration",
        request_id=request_id,
    )
    envelope = ObsCommandEnvelope(
        schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
        command_id=_COMMAND_ID,
        action=spec.action,
        required_scopes=spec.required_scopes,
        payload=bind_server_authority(
            spec.payload_schema.validate(payload),
            spec=spec,
            authority=authority,
        ),
        authority=authority,
        claim_ref=str(claim.claim_ref),
        audit_ref=str(audit_ref),
        body_sha256=body_sha256,
        correlation_id="operation-reconciliation-integration-correlation",
    )
    return store, envelope, raw_body


@pytest.mark.asyncio
async def test_governed_operator_reacquires_and_resumes_original_graph_once(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "governed-operation-reconciliation.sqlite3"
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "operation-reconciliation-integration-v1"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = build_process_task_runtime_container(
        ledger=ledger,
        canonical_db_path=db_path,
        reconciliation_public_keys={key_id: public_key},
    )
    embodiment = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id="governed-operation-reconciliation-scope",
        command_port=_UnusedEmbodimentCommandPort(),
        authority_lease_ms=600_000,
    )
    operator_runtime = None
    ingress_store = None
    try:
        contracts = _contracts((("root", ()),))
        admitted = AdmittedExecution.from_contracts(
            command=contracts[0],
            assignment=contracts[2],
            graph=contracts[3],
        )
        calls: list[str] = []
        successful_worker = _ProjectedWorker(
            projections=container.worker_projection_store,
            calls=calls,
        )
        worker = _FailOnceWorker(successful_worker, calls)
        interrupted_application = GovernedCommandApplication(
            command_repository=container.command_repository,
            coordinator=_coordinator(
                store=container.task_store,
                projections=container.worker_projection_store,
                worker=worker,
                owner_id="operation-target-owner-a",
                lease_ttl_ms=500,
            ),
        )
        interrupted = await _events(interrupted_application, contracts)
        assert interrupted[-1]["execution_receipt"]["status"] == (
            "RECOVERY_REQUIRED"
        )
        operation = dict(
            container.task_store.get_operation_reservation(
                admitted.reservation_id
            )
        )
        assert operation["status"] == "RECOVERY_REQUIRED"
        scheduler_snapshot = container.task_store.read_scheduler_snapshot(
            admitted.graph_id,
            admitted.graph_revision,
        )
        graph_budgets = [
            item
            for item in scheduler_snapshot["budgets"]
            if item.get("node_id") is None and item.get("attempt_id") is None
        ]
        assert len(graph_budgets) == 1
        recovery_case = container.task_store.record_operation_recovery_hold(
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            node_id=admitted.node_id,
            attempt_id=operation["attempt_id"],
            reservation_id=operation["reservation_id"],
            budget_id=graph_budgets[0]["budget_id"],
            reason_code="GOVERNED_WORKER_FAILED",
        )
        assert recovery_case["case_kind"] == "UNKNOWN_SIDE_EFFECT"
        assert recovery_case["exact_replay"] is True
        assert recovery_case["current"] is True
        resumed_application = GovernedCommandApplication(
            command_repository=container.command_repository,
            coordinator=_coordinator(
                store=container.task_store,
                projections=container.worker_projection_store,
                worker=worker,
                owner_id="operation-target-owner-b",
                lease_ttl_ms=60_000,
            ),
        )
        target_continuation = _TargetContinuation(
            resumed_application,
            contracts,
        )
        container.operation_reconciliation_service.bind_execution_owner(
            "operation-target-owner-b"
        )
        await asyncio.sleep(0.55)

        attestation_ref = "proof://operation/governed-reacquisition"
        binding = build_reconciliation_attestation_binding(
            reservation_id=operation["reservation_id"],
            business_scope=operation["business_scope"],
            operation_fingerprint=operation["operation_fingerprint"],
            recovery_version=operation["status_version"],
            reconciler_actor_id=_PRINCIPAL_ID,
            attestation_ref=attestation_ref,
            recovery_action="REACQUIRE",
            reconciliation_outcome="NO_SIDE_EFFECT",
        )
        payload = {
            "reservation_id": operation["reservation_id"],
            "expected_version": operation["status_version"],
            "action": "REACQUIRE",
            "attestation_ref": attestation_ref,
            "attestation_key_id": key_id,
            "attestation_signature": "ed25519:"
            + base64.urlsafe_b64encode(private_key.sign(binding)).decode("ascii"),
            "recovery_case_ref": recovery_case["case_ref"],
            "recovery_case_subject_sha256": recovery_case["subject_sha256"],
        }
        with pytest.raises(
            TaskStoreError,
            match="one exact current recovery case",
        ):
            container.task_store.stage_operation_reconciliation_evidence(
                request_id="request-operation-reconciliation-without-case",
                ingress_audit_ref="secure-ingress-audit://operation/missing-case",
                body_sha256="a" * 64,
                reservation_id=payload["reservation_id"],
                expected_version=payload["expected_version"],
                action=payload["action"],
                intent_ref=None,
                reconciler_actor_id=_PRINCIPAL_ID,
                downstream_receipt_ref=None,
                attestation_ref=payload["attestation_ref"],
                attestation_key_id=payload["attestation_key_id"],
                attestation_signature=payload["attestation_signature"],
            )

        hard_state = HostHardStateOwner.bootstrap(
            initial_sys_mode="normal",
            endocrine_resolver=lambda **_kwargs: {},
            require_durable=True,
        )
        hard_state.bind_durable_journal(
            SQLiteHardStateJournal(
                db_path,
                runtime_scope_id=embodiment.runtime_scope_id,
                runtime_authority_provider=(
                    lambda: _runtime_authority_provider(embodiment)
                ),
            ),
            initialization_event_id="operation-reconciliation-hard-state",
        )
        mode_service = ModeTransitionService(
            get_hard_state=hard_state.get_hard_state,
            set_sys_mode=hard_state.set_sys_mode,
            history_provider=hard_state.durable_history,
            exact_history_provider=hard_state.reopen_sys_mode_transition,
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
        tokens = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        engine = IntentDecisionEngine(
            ledger=ledger,
            token_service=tokens,
            registry=build_agent_runtime_skill_registry(),
        )
        request_id = "request-governed-operation-reconciliation"
        ingress_store, envelope, raw_body = _operation_ingress_envelope(
            db_path,
            request_id=request_id,
            payload=payload,
        )
        dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers=build_production_acceptance_effect_handlers(
                mode_transition_service_provider=lambda: mode_service,
                operation_reconciliation_service=(
                    container.operation_reconciliation_service
                ),
                operation_reconciliation_evidence_store=container.task_store,
                operation_reacquisition_continuation_provider=(
                    lambda: target_continuation
                ),
            ),
            owner_id="governed-operation-reconciliation-effects",
            action_preview_service=preview,
        )
        operator_runtime = build_governed_operator_runtime(
            task_runtime_container=container,
            ledger=ledger,
            token_service=tokens,
            intent_engine=engine,
            runtime_context_application=runtime_context,
            action_preview_service=preview,
            acceptance_effect_dispatcher=dispatcher,
            mode_transition_service_provider=lambda: mode_service,
            cancellation_authority_verifier=None,
            secure_ingress_authority_resolver=ingress_store,
            owner_id="governed-operation-reconciliation-operator",
        )
        await operator_runtime.activate()
        registry = build_default_obs_domain_command_registry()
        port = registry.resolve_port(_COMMAND_ID)
        assert port is not None
        state = SimpleNamespace(
            task_operation_reconciliation_operator_enabled=True,
            governed_operator_runtime=operator_runtime,
        )
        with bind_operator_command_runtime(state, raw_body=raw_body):
            pending_domain = await port.execute(envelope)
        pending = dict(pending_domain.result["output"])
        assert pending["status"] == "WAITING_REVIEW"
        assert pending_domain.side_effects_executed is False
        assert worker.invocations == 1

        record = container.command_repository.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        assert record is not None
        frozen = json.dumps(
            record["work_order"].input_pack,
            ensure_ascii=False,
            sort_keys=True,
        )
        assert "attestation_signature" not in frozen
        assert "operation-reconcile-evidence://sha256/" in frozen
        evidence_ref = record["work_order"].input_pack["intent_draft"]["args"][
            "evidence_ref"
        ]
        evidence = container.task_store.get_operation_reconciliation_evidence(
            evidence_ref
        )
        assert evidence["attestation_signature"] == payload[
            "attestation_signature"
        ]
        assert evidence["recovery_case_ref"] == recovery_case["case_ref"]
        assert evidence["recovery_case_subject_sha256"] == (
            recovery_case["subject_sha256"]
        )

        review = pending["reviews"][0]
        approved = container.review_store.approve_review(
            review["review_id"],
            actor_id=_PRINCIPAL_ID,
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
        claim = (
            container.review_continuation_store
            .claim_pending_review_continuations(
                worker_id="operation-reconciliation-review-worker",
                lease_ms=60_000,
                limit=1,
            )[0]
        )
        continuation = (
            container.review_continuation_store
            .validate_review_continuation_claim(claim)
        )
        continued = await operator_runtime.continue_review_admission(
            record,
            continuation,
        )

        assert continued["execution_receipt"]["accepted"] is True
        assert continued["acceptance_effect_receipt"]["committed"] is True
        assert target_continuation.calls == 1
        assert worker.invocations == 2
        assert calls == ["root", "root"]
        final_operation = container.task_store.get_operation_reservation(
            operation["reservation_id"]
        )
        assert final_operation["status"] == "COMMITTED"
        assert final_operation["reconciliation_action"] == "REACQUIRE"
        assert final_operation["owner_id"] == "operation-target-owner-b"
        assert continued["acceptance_effect_receipt"]["recovery_case"] == {
            "case_ref": recovery_case["case_ref"],
            "case_kind": "UNKNOWN_SIDE_EFFECT",
            "classification": "SIGNED_OPERATION_RECONCILIATION_REQUIRED",
            "subject_sha256": recovery_case["subject_sha256"],
            "current": False,
        }
        assert container.task_store.get_operation_recovery_hold(
            recovery_case["case_ref"]
        )["current"] is False
        assert (
            container.task_store.list_current_operation_recovery_holds(
                graph_id=admitted.graph_id
            )
            == ()
        )

        effects = container.task_store.list_acceptance_effect_intents(
            graph_id=continued["execution_receipt"]["graph_id"],
            graph_revision=continued["execution_receipt"]["graph_revision"],
            limit=10,
        )
        assert len(effects) == 1
        assert effects[0]["effect_type"] == "task.operation_reconciliation"
        assert effects[0]["status"] == "COMMITTED"
        with bind_operator_command_runtime(state, raw_body=raw_body):
            replay_domain = await port.execute(envelope)
        replay = dict(replay_domain.result["output"])
        assert replay["exact_replay"] is True
        assert replay_domain.side_effects_executed is False
        assert target_continuation.calls == 1
        assert worker.invocations == 2
    finally:
        if operator_runtime is not None:
            await operator_runtime.aclose()
        if ingress_store is not None:
            ingress_store.close()
        await container.aclose()
        embodiment.close()
        ledger.close()
