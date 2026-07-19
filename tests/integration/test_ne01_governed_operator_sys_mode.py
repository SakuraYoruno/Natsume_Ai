from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.app.governed_operator_runtime import (
    GovernedOperatorRuntimeError,
    build_governed_operator_runtime,
)
from host_brain.app.observability_runtime.domain_command_ports import (
    build_default_obs_domain_command_registry,
)
from host_brain.app.observability_runtime.review_wait_recovery import (
    CanonicalReviewWaitRecoveryResolver,
)
from host_brain.app.host_hard_state import HostHardStateOwner
from host_brain.app.host_runtime_context_wiring import (
    compose_host_runtime_context,
    runtime_environment_observation_provider,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.embodiment import HostEmbodimentRuntime
from host_brain.composition.task_runtime import build_process_task_runtime_container
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.core.mode_transition import ModeTransitionService
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.hard_state_journal import SQLiteHardStateJournal
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.observability.command_ports import (
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandEnvelope,
    bind_server_authority,
    governed_review_continuation_ref,
)
from host_brain.observability.command_outcome_journal import (
    ObsCommandOutcomeJournal,
    ObsCommandOutcomeKey,
    ObsCommandOutcomeState,
)
from host_brain.observability.operator_command_gateway import (
    bind_operator_command_runtime,
)
from host_brain.observability.secure_command_ingress import SecureObsCommandIngress
from host_brain.runtime.action_preview_resolver import (
    ProductionActionPreviewAuthorityResolver,
)
from host_brain.runtime.action_preview_proof import ActionPreviewExecutionProof
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
    AcceptanceEffectOutcomeUnknown,
    AcceptanceEffectRuntime,
)
from natsume_contracts.task import HostCommandSurface


_OPERATOR_IDEMPOTENCY_KEY = "operator-sys-mode-integration-idempotency"


class _UnusedEmbodimentCommandPort:
    async def submit(self, _command: Any) -> Any:
        raise AssertionError(
            "sys_mode.switch must commit through the Task Runtime Acceptance effect"
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


def _operator_ingress_authority(
    db_path: Path,
    *,
    request_id: str,
    target_mode: str = "focus",
    source: str = "manual",
    reason: str = "NE-01 governed Operator integration",
    idempotency_key: str = _OPERATOR_IDEMPOTENCY_KEY,
) -> tuple[SQLiteSecureIngressStore, ObsCommandEnvelope, bytes]:
    command_payload = {
        "target_mode": target_mode,
        "source": source,
        "reason": reason,
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
    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    now = int(time.time() * 1000)
    scopes = ("obs:write", "obs:sys-mode:write")
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    claim = store.try_claim(
        principal_id="principal.operator.integration",
        surface="obs.command",
        action="obs:command:sys_mode.switch",
        required_scopes=scopes,
        idempotency_key=idempotency_key,
        nonce=f"operator-sys-mode-integration-nonce:{request_id}",
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
            correlation_id="operator-sys-mode-integration-correlation",
            surface="obs.command",
            action="obs:command:sys_mode.switch",
            method="POST",
            normalized_path="/v1/host/obs-commands/sys_mode.switch",
            principal_id="principal.operator.integration",
            tenant_id="operator-sys-mode-integration",
            key_id="operator-sys-mode-integration-key",
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
            ts_ms=now,
        ),
    )
    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec("sys_mode.switch")
    assert spec is not None
    authority = ObsCommandAuthority.bind(
        principal_id="principal.operator.integration",
        tenant_id="operator-sys-mode-integration",
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
        correlation_id="operator-sys-mode-integration-correlation",
    )
    return store, envelope, raw_body


def _runtime_authority_provider(
    embodiment_runtime: HostEmbodimentRuntime,
) -> dict[str, Any]:
    return {
        **embodiment_runtime.renew_authority(),
        "runtime_scope_id": embodiment_runtime.runtime_scope_id,
    }


def _accepted_mode_effect_payload(
    *,
    runtime_scope_id: str,
    event_id: str,
    request_id: str,
    reason: str,
) -> dict[str, Any]:
    payload = {
        "operator_command_id": "sys_mode.switch",
        "request_id": request_id,
        "target_mode": "focus",
        "source": "manual",
        "reason": reason,
        "event_id": event_id,
        "runtime_scope_id": runtime_scope_id,
    }
    effect_id = "acceptance-effect://sha256/" + "5" * 64
    boundary = {
        "action_kind": "acceptance-effect.host.sys_mode_transition",
        "action_scope": effect_id,
        "risk_level": 4,
        "action_args": dict(payload),
        "context_pack_digest": "sha256:" + "6" * 64,
        "scene_identity": {
            "schema": "natsume.scene-identity.v2",
            "workspace_id": "workspace-operator-reconciliation",
            "subject_id": "operator",
            "scene_id": "release-maintenance",
            "revision": 1,
            "decision_digest": "sha256:" + "7" * 64,
        },
        "task_graph_id": "graph-operator-reconciliation",
        "graph_revision": 1,
        "task_node_id": "node-operator-reconciliation",
        "parent_governor_grant_ref": "governor-grant://operator/parent",
        "node_grant_ref": "governor-grant://operator/node",
        "node_grant": {"grant_ref": "governor-grant://operator/node"},
        "authority_context": {
            "schema_version": (
                "Natsume.action_preview.acceptance_effect_authority_request.v1"
            ),
            "effect_id": effect_id,
            "lease_owner_id": "operator-reconciliation-effect-owner",
            "fencing_token": 1,
        },
    }
    preview = ActionPreviewService(
        runtime_event_store=object(),
        guardian=SimpleNamespace(evaluate_capability=lambda **_kwargs: {}),
        authority_resolver=SimpleNamespace(resolve=lambda **_kwargs: None),
    )
    args_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    preview.require_execution = lambda **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        payload={
            "authority_kind": "ACCEPTANCE_EFFECT",
            "action_kind": boundary["action_kind"],
            "action_scope": effect_id,
            "risk_level": 4,
            "action_args_digest": args_digest,
        }
    )
    payload["_action_preview_terminal_proof"] = ActionPreviewExecutionProof(
        service=preview,
        event_id="runtimeevent://sha256/" + "8" * 64,
        boundary=boundary,
    )
    return payload


def test_sys_mode_negative_response_recovers_one_real_sqlite_commit_after_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "sys-mode-negative-response-recovery.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-sys-mode-negative-response-recovery",
    )
    assert migrated.success, migrated.as_dict()
    embodiment_runtime = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id="ne01-sys-mode-negative-response-runtime",
        command_port=_UnusedEmbodimentCommandPort(),
        authority_lease_ms=600_000,
    )
    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal",
        endocrine_resolver=lambda **_kwargs: {},
        require_durable=True,
    )
    recovered_owner = None
    event_id = "obs-sys-mode:" + "9" * 64
    reason = "commit persisted before response was lost"
    request_id = "request-ne01-negative-mode-response"
    try:
        owner.bind_durable_journal(
            SQLiteHardStateJournal(
                db_path,
                runtime_scope_id=embodiment_runtime.runtime_scope_id,
                runtime_authority_provider=(
                    lambda: _runtime_authority_provider(embodiment_runtime)
                ),
            ),
            initialization_event_id="negative-response-hard-state-initialized",
        )

        def commit_then_lose_response(
            target_mode: str, **kwargs: Any
        ) -> dict[str, Any]:
            owner.set_sys_mode(target_mode, **kwargs)
            raise OSError("transition response lost after SQLite commit")

        def temporarily_unreadable_history(**_kwargs: Any):  # noqa: ANN202
            raise OSError("SQLite history temporarily unavailable")

        service = ModeTransitionService(
            get_hard_state=owner.get_hard_state,
            set_sys_mode=commit_then_lose_response,
            exact_history_provider=temporarily_unreadable_history,
        )
        service_results: list[dict[str, Any]] = []
        switch_mode = service.switch_mode

        def capture_negative_projection(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = switch_mode(*args, **kwargs)
            service_results.append(dict(result))
            return result

        service.switch_mode = capture_negative_projection  # type: ignore[method-assign]
        handler = build_production_acceptance_effect_handlers(
            mode_transition_service_provider=lambda: service,
        )["host.sys_mode_transition"]
        payload = _accepted_mode_effect_payload(
            runtime_scope_id=embodiment_runtime.runtime_scope_id,
            event_id=event_id,
            request_id=request_id,
            reason=reason,
        )

        with pytest.raises(
            AcceptanceEffectOutcomeUnknown,
            match="SQLite history temporarily unavailable",
        ):
            handler(payload)
        assert service_results[0]["success"] is False
        assert "response lost after SQLite commit" in service_results[0]["error"]
        assert owner.get_hard_state().sys_mode == "focus"

        owner.unbind_durable_journal()
        recovered_owner = HostHardStateOwner.bootstrap(
            initial_sys_mode="gaming",
            endocrine_resolver=lambda **_kwargs: {},
            require_durable=True,
        )
        recovered_owner.bind_durable_journal(
            SQLiteHardStateJournal(
                db_path,
                runtime_scope_id=embodiment_runtime.runtime_scope_id,
                runtime_authority_provider=(
                    lambda: _runtime_authority_provider(embodiment_runtime)
                ),
            ),
            initialization_event_id="negative-response-restart-initialized",
        )
        recovered_service = ModeTransitionService(
            get_hard_state=recovered_owner.get_hard_state,
            set_sys_mode=recovered_owner.set_sys_mode,
            exact_history_provider=recovered_owner.reopen_sys_mode_transition,
        )
        recovered_handler = build_production_acceptance_effect_handlers(
            mode_transition_service_provider=lambda: recovered_service,
        )["host.sys_mode_transition"]

        receipt = recovered_handler(payload)

        assert receipt["success"] is True
        assert receipt["status"] == "REPLAYED"
        assert receipt["exact_replay"] is True
        assert receipt["event_id"] == event_id
        assert receipt["runtime_scope_id"] == embodiment_runtime.runtime_scope_id
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                """
                SELECT COUNT(*) FROM host_hard_state_event
                WHERE runtime_scope_id = ? AND event_kind = 'MODE_CHANGED'
                  AND event_id = ?
                """,
                (embodiment_runtime.runtime_scope_id, event_id),
            ).fetchone()[0] == 1
    finally:
        if recovered_owner is not None:
            recovered_owner.unbind_durable_journal()
        owner.unbind_durable_journal()
        embodiment_runtime.close()


@pytest.mark.asyncio
async def test_sys_mode_switch_commits_once_after_governed_acceptance_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "governed-operator-sys-mode.sqlite3"
    request_id = "request-ne01-governed-operator-sys-mode"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    outcome_journal = ObsCommandOutcomeJournal(
        tmp_path / "governed-operator-outcomes.jsonl"
    )
    container = None
    embodiment_runtime = None
    operator_runtime = None
    runtime_group = None
    ingress_store = None
    hard_state_owner = None
    recovered_owner = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
            authenticated_outcome_lookup=outcome_journal.lookup,
        )
        embodiment_runtime = HostEmbodimentRuntime(
            repository=EmbodimentOutboxRepository(db_path),
            worker_id="ne01-governed-operator-runtime-scope",
            command_port=_UnusedEmbodimentCommandPort(),
            authority_lease_ms=600_000,
        )

        hard_state_owner = HostHardStateOwner.bootstrap(
            initial_sys_mode="normal",
            endocrine_resolver=lambda **_kwargs: {},
            require_durable=True,
        )
        hard_state_journal = SQLiteHardStateJournal(
            db_path,
            runtime_scope_id=embodiment_runtime.runtime_scope_id,
            runtime_authority_provider=(
                lambda: _runtime_authority_provider(embodiment_runtime)
            ),
        )
        initialized = hard_state_owner.bind_durable_journal(
            hard_state_journal,
            initialization_event_id="ne01-operator-hard-state-initialized",
        )
        assert initialized.revision == 1
        assert initialized.sys_mode == "normal"

        setter_observations: list[dict[str, Any]] = []

        def accepted_set_sys_mode(target_mode: str, **kwargs: Any) -> dict[str, Any]:
            with sqlite3.connect(db_path) as connection:
                node_statuses = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT status FROM task_runtime_node ORDER BY node_id"
                    ).fetchall()
                )
                effect_statuses = tuple(
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT status
                        FROM task_runtime_acceptance_effect_intent
                        WHERE effect_type = 'host.sys_mode_transition'
                        ORDER BY effect_id
                        """
                    ).fetchall()
                )
            setter_observations.append(
                {
                    "mode_before_effect": hard_state_owner.get_hard_state().sys_mode,
                    "node_statuses": node_statuses,
                    "effect_statuses": effect_statuses,
                }
            )
            return hard_state_owner.set_sys_mode(target_mode, **kwargs)

        mode_transition_service = ModeTransitionService(
            get_hard_state=hard_state_owner.get_hard_state,
            set_sys_mode=accepted_set_sys_mode,
            history_provider=hard_state_owner.durable_history,
            exact_history_provider=hard_state_owner.reopen_sys_mode_transition,
            ledger=ledger,
        )
        runtime_context_application = compose_host_runtime_context(
            task_runtime=container,
            ledger=ledger,
            embodiment_runtime=embodiment_runtime,
            environment_observation_provider=(
                runtime_environment_observation_provider(mode_transition_service)
            ),
        )
        action_preview_service = ActionPreviewService(
            runtime_event_store=runtime_context_application.composition.event_store,
            guardian=runtime_context_application.composition.guardian,
            authority_resolver=ProductionActionPreviewAuthorityResolver(
                command_repository=container.command_repository,
                task_store=container.task_store,
                node_grant_verifier=DurableNodeGrantVerifier(
                    ledger=ledger,
                    review_store=container.review_continuation_store,
                ),
                runtime_context_application=runtime_context_application,
            ),
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
        ingress_store, envelope, raw_body = _operator_ingress_authority(
            db_path,
            request_id=request_id,
        )
        acceptance_effect_dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers=build_production_acceptance_effect_handlers(
                mode_transition_service_provider=lambda: mode_transition_service,
            ),
            owner_id="ne01-governed-operator-acceptance-effects",
            action_preview_service=action_preview_service,
        )
        acceptance_effect_runtime = AcceptanceEffectRuntime(
            dispatcher=acceptance_effect_dispatcher,
            poll_seconds=0.01,
        )
        operator_runtime = build_governed_operator_runtime(
            task_runtime_container=container,
            ledger=ledger,
            token_service=token_service,
            intent_engine=intent_engine,
            runtime_context_application=runtime_context_application,
            action_preview_service=action_preview_service,
            acceptance_effect_dispatcher=acceptance_effect_dispatcher,
            mode_transition_service_provider=lambda: mode_transition_service,
            cancellation_authority_verifier=None,
            secure_ingress_authority_resolver=ingress_store,
            owner_id="ne01-governed-operator-owner",
        )
        await operator_runtime.activate()
        registry = build_default_obs_domain_command_registry()
        port = registry.resolve_port("sys_mode.switch")
        assert port is not None
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

        assert hard_state_owner.get_hard_state().sys_mode == "normal"
        forged_envelope = replace(
            envelope,
            authority=ObsCommandAuthority.bind(
                principal_id=envelope.authority.principal_id,
                tenant_id=envelope.authority.tenant_id,
                request_id="request-forged-not-canonical",
            ),
        )
        with bind_operator_command_runtime(app_state, raw_body=raw_body):
            with pytest.raises(
                GovernedOperatorRuntimeError,
                match="authority binding drifted",
            ):
                await port.execute(forged_envelope)
        assert container.command_repository.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id="request-forged-not-canonical",
        ) is None
        with bind_operator_command_runtime(app_state, raw_body=raw_body):
            first_domain = await port.execute(envelope)
        first = dict(first_domain.result["output"])

        assert first_domain.success is True
        assert first["success"] is True
        assert first["accepted"] is True
        assert first_domain.side_effects_executed is False
        assert first["status"] == "WAITING_REVIEW"
        assert first["current_mode"] == "normal"
        assert len(first["reviews"]) == 1
        assert setter_observations == []
        assert hard_state_owner.get_hard_state().sys_mode == "normal"
        continuation_ref = governed_review_continuation_ref(first_domain)
        assert continuation_ref is not None
        outer_ingress = SecureObsCommandIngress(
            ingress_adapter=None,
            ingress_service=None,
            claim_result_store=ingress_store,
            outcome_journal=outcome_journal,
            registry=registry,
            router=None,
        )
        original_mark_waiting = outcome_journal.mark_waiting_review
        monkeypatch.setattr(
            outcome_journal,
            "mark_waiting_review",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("fault after durable Review admission")
            ),
        )
        failed_outer = outer_ingress._mark_waiting_review(
            begun.record,
            domain_result_ref=first_domain.result_ref,
            continuation_ref=continuation_ref,
            principal_id=envelope.authority.principal_id,
        )
        assert failed_outer.terminal_state == ObsCommandOutcomeState.EXECUTING.value
        assert outcome_journal.lookup_by_claim_ref(envelope.claim_ref).state is (
            ObsCommandOutcomeState.EXECUTING
        )
        monkeypatch.setattr(
            outcome_journal,
            "mark_waiting_review",
            original_mark_waiting,
        )
        outcome_journal = ObsCommandOutcomeJournal(
            tmp_path / "governed-operator-outcomes.jsonl"
        )
        recovered_waits = outcome_journal.recover_incomplete(
            review_wait_resolver=CanonicalReviewWaitRecoveryResolver(
                command_repository=container.command_repository,
                task_store=container.task_store,
                review_store=container.review_store,
                review_continuation_store=container.review_continuation_store,
                secure_ingress_authority_resolver=ingress_store,
            )
        )
        assert len(recovered_waits) == 1
        settled_outcome = recovered_waits[0]
        assert settled_outcome.state is ObsCommandOutcomeState.WAITING_REVIEW
        assert settled_outcome.reason_code == "OBS_COMMAND_WAITING_REVIEW_RECOVERED"
        pending_authority = ingress_store.resolve_committed_authority_by_audit(
            audit_ref=envelope.audit_ref,
        )
        assert pending_authority is not None
        assert pending_authority["claim_result_ref"] is None

        review = first["reviews"][0]
        approved = container.review_store.approve_review(
            review["review_id"],
            actor_id=envelope.authority.principal_id,
            expected_subject_ref=review["subject_ref"],
            expected_subject_version=review["subject_version"],
            expected_resume_phase=review["resume_phase"],
            expected_checkpoint_id=review["checkpoint_id"],
        )
        consumed = container.review_store.resume_review(
            review["review_id"],
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=review["subject_ref"],
            subject_version=review["subject_version"],
            resume_phase=review["resume_phase"],
            checkpoint_id=review["checkpoint_id"],
        )
        assert consumed["review"]["review"]["status"] == "CONSUMED"
        raw_claim = (
            container.review_continuation_store.claim_pending_review_continuations(
                worker_id="ne01-governed-operator-review-worker",
                lease_ms=60_000,
                limit=1,
            )[0]
        )
        continuation_claim = (
            container.review_continuation_store.validate_review_continuation_claim(
                raw_claim
            )
        )
        runtime_group = GovernedRuntimeGroup(
            {"operator": operator_runtime},
            review_store=container.review_continuation_store,
            review_decision_store=container.review_store,
            command_repository=container.command_repository,
            claim_result_store=ingress_store,
            operator_outcome_finalizer=SecureObsCommandIngress(
                ingress_adapter=None,
                ingress_service=None,
                claim_result_store=ingress_store,
                outcome_journal=outcome_journal,
                registry=registry,
                router=None,
            ),
            review_continuation_worker_factory=_NoopContinuationWorker,
            review_continuation_worker_id=(
                "ne01-governed-operator-review-continuation"
            ),
            acceptance_effect_runtime=acceptance_effect_runtime,
        )
        await runtime_group.activate()
        continued = await runtime_group.continue_review_continuation(
            continuation_claim
        )
        settled_outcome = outcome_journal.lookup_by_claim_ref(envelope.claim_ref)
        assert settled_outcome is not None
        assert settled_outcome.state is ObsCommandOutcomeState.SUCCEEDED
        settled_authority = ingress_store.resolve_committed_authority_by_audit(
            audit_ref=envelope.audit_ref,
        )
        assert settled_authority is not None
        assert settled_authority["claim_result_ref"] == settled_outcome.receipt_ref
        receipt = continued["execution_receipt"]
        assert receipt["accepted"] is True
        assert receipt["status"] == "ACCEPTED"
        assert receipt["verification_status"] == "PASS"
        assert continued["historical_side_effects_executed"] is True
        assert continued["acceptance_effect_receipt"]["historical_status"] == (
            "APPLIED"
        )
        assert continued["audit_refs"]["acceptance_effects"] == {
            "count": 1,
            "committed": 1,
            "failed": 0,
            "pending": 0,
            "recovery_pending": 0,
            "dispatch_status": "COMMITTED",
        }
        assert setter_observations == [
            {
                "mode_before_effect": "normal",
                "node_statuses": ("ACCEPTED",),
                "effect_statuses": ("CLAIMED",),
            }
        ]
        assert hard_state_owner.get_hard_state().sys_mode == "focus"

        command_record = container.command_repository.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        assert command_record is not None
        command = command_record["command"]
        work_order = command_record["work_order"]
        assert command.surface is HostCommandSurface.OPERATOR
        assert command.request_id == request_id
        assert command.principal_id == envelope.authority.principal_id
        assert command.ingress_claim_ref == envelope.claim_ref
        assert command.ingress_audit_ref == envelope.audit_ref
        assert command.input_sha256 == "sha256:" + envelope.body_sha256
        assert work_order.metadata["operator_command_id"] == "sys_mode.switch"

        graph_id = str(receipt["graph_id"])
        graph_revision = int(receipt["graph_revision"])
        node_id = str(receipt["node_id"])
        attempt_id = str(receipt["attempt_id"])
        graph = container.task_store.get_graph(graph_id, graph_revision)
        assert graph["status"] == "COMPLETED"
        assert [node["status"] for node in graph["nodes"]] == ["ACCEPTED"]
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            attempt = connection.execute(
                "SELECT status FROM task_runtime_attempt WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            lease = connection.execute(
                "SELECT status FROM task_runtime_lease WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            budget = connection.execute(
                """
                SELECT status
                FROM task_runtime_budget_reservation
                WHERE graph_id = ? AND graph_revision = ?
                """,
                (graph_id, graph_revision),
            ).fetchone()
            event_types = {
                str(row["event_type"])
                for row in connection.execute(
                    """
                    SELECT event_type
                    FROM task_runtime_outbox
                    WHERE aggregate_type = 'TASK_NODE' AND aggregate_id = ?
                    """,
                    (f"{graph_id}@{graph_revision}/{node_id}",),
                ).fetchall()
            }
            effect = connection.execute(
                """
                SELECT status, effect_type, attempt_id, receipt_ref
                FROM task_runtime_acceptance_effect_intent
                WHERE graph_id = ? AND graph_revision = ?
                  AND effect_type = 'host.sys_mode_transition'
                """,
                (graph_id, graph_revision),
            ).fetchone()
            mode_changed_before_replay = connection.execute(
                """
                SELECT COUNT(*)
                FROM host_hard_state_event
                WHERE runtime_scope_id = ? AND event_kind = 'MODE_CHANGED'
                """,
                (embodiment_runtime.runtime_scope_id,),
            ).fetchone()[0]
        assert attempt is not None and attempt["status"] == "SUCCEEDED"
        assert lease is not None and lease["status"] == "RELEASED"
        assert budget is not None and budget["status"] == "CONSUMED"
        assert {"TASK_NODE_VERIFIED", "TASK_NODE_ACCEPTED"} <= event_types
        assert effect is not None
        assert dict(effect) == {
            "status": "COMMITTED",
            "effect_type": "host.sys_mode_transition",
            "attempt_id": attempt_id,
            "receipt_ref": effect["receipt_ref"],
        }
        assert str(effect["receipt_ref"] or "").strip()
        assert mode_changed_before_replay == 1

        with bind_operator_command_runtime(app_state, raw_body=raw_body):
            replay_domain = await port.execute(envelope)
        replay = dict(replay_domain.result["output"])

        assert replay_domain.result_ref == first_domain.result_ref
        assert replay["success"] is True
        assert replay_domain.side_effects_executed is False
        assert replay["execution_receipt"]["command_id"] == receipt["command_id"]
        assert replay["execution_receipt"]["attempt_id"] == attempt_id
        assert len(setter_observations) == 1
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM host_hard_state_event
                WHERE runtime_scope_id = ? AND event_kind = 'MODE_CHANGED'
                """,
                (embodiment_runtime.runtime_scope_id,),
            ).fetchone()[0] == mode_changed_before_replay
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ?
                """,
                (graph_id, graph_revision),
            ).fetchone()[0] == 1
            assert connection.execute(
                """
                SELECT result_ref
                FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ? AND claim_status = 'COMMITTED'
                """,
                (envelope.claim_ref,),
            ).fetchone()[0] == settled_outcome.receipt_ref
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM task_runtime_acceptance_effect_intent
                WHERE graph_id = ? AND graph_revision = ?
                  AND effect_type = 'host.sys_mode_transition'
                  AND status = 'COMMITTED'
                """,
                (graph_id, graph_revision),
            ).fetchone()[0] == 1

        # Push the original Operator event beyond the bounded history view.
        # Exact replay must reopen its canonical commit by identity, without
        # executing the accepted effect again or depending on the current mode.
        for index in range(1_001):
            current_mode = hard_state_owner.get_hard_state().sys_mode
            next_mode = "normal" if current_mode == "focus" else "focus"
            hard_state_owner.set_sys_mode(
                next_mode,
                event_id=f"history-padding-{index:04d}",
                source="manual",
                reason=f"history padding {index}",
            )
        assert all(
            str(item.get("event_id") or "")
            != "obs-sys-mode:"
            + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
            for item in mode_transition_service.get_history(limit=1_000)
        )
        with sqlite3.connect(db_path) as connection:
            mode_changed_after_padding = connection.execute(
                """
                SELECT COUNT(*)
                FROM host_hard_state_event
                WHERE runtime_scope_id = ? AND event_kind = 'MODE_CHANGED'
                """,
                (embodiment_runtime.runtime_scope_id,),
            ).fetchone()[0]
        with bind_operator_command_runtime(app_state, raw_body=raw_body):
            old_event_replay_domain = await port.execute(envelope)
        old_event_replay = dict(old_event_replay_domain.result["output"])
        assert old_event_replay["success"] is True
        assert old_event_replay["exact_replay"] is True
        assert old_event_replay_domain.side_effects_executed is False
        assert old_event_replay["historical_side_effects_executed"] is True
        assert old_event_replay["current_mode"] == "normal"
        assert old_event_replay["acceptance_effect_receipt"][
            "hard_state_event"
        ]["event_id"] == (
            "obs-sys-mode:"
            + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        )
        assert len(setter_observations) == 1
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM host_hard_state_event
                WHERE runtime_scope_id = ? AND event_kind = 'MODE_CHANGED'
                """,
                (embodiment_runtime.runtime_scope_id,),
            ).fetchone()[0] == mode_changed_after_padding

        hard_state_owner.unbind_durable_journal()
        recovered_owner = HostHardStateOwner.bootstrap(
            initial_sys_mode="gaming",
            endocrine_resolver=lambda **_kwargs: {},
            require_durable=True,
        )
        recovered_snapshot = recovered_owner.bind_durable_journal(
            SQLiteHardStateJournal(
                db_path,
                runtime_scope_id=embodiment_runtime.runtime_scope_id,
                runtime_authority_provider=(
                    lambda: _runtime_authority_provider(embodiment_runtime)
                ),
            ),
            initialization_event_id="ne01-operator-restart-initialization",
        )
        assert recovered_snapshot.revision == 1_003
        assert recovered_snapshot.sys_mode == "normal"
        assert recovered_owner.get_hard_state().sys_mode == "normal"
    finally:
        if recovered_owner is not None:
            recovered_owner.unbind_durable_journal()
        if hard_state_owner is not None:
            hard_state_owner.unbind_durable_journal()
        if runtime_group is not None:
            await runtime_group.aclose()
        elif operator_runtime is not None:
            await operator_runtime.aclose()
        if ingress_store is not None:
            ingress_store.close()
        if embodiment_runtime is not None:
            embodiment_runtime.close()
        if container is not None:
            await container.aclose()
        ledger.close()
