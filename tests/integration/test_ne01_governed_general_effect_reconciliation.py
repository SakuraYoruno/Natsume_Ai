from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

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
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.hard_state_journal import SQLiteHardStateJournal
from host_brain.persistence.reconciliation_proof_verifier import (
    Ed25519ReconciliationProofVerifier,
)
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
)
from host_brain.runtime.action_preview_resolver import (
    ProductionActionPreviewAuthorityResolver,
)
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.skills.agent_runtime_registry import (
    build_agent_runtime_skill_registry,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from natsume_contracts.task import HostCommandSurface
from tests.contract.test_ne01_graph_cancellation_store import _store
from tests.integration.test_ne01_general_acceptance_effect_reconciliation import (
    _unknown_effect,
)
from tests.integration.test_ne01_governed_operator_sys_mode import (
    _UnusedEmbodimentCommandPort,
    _runtime_authority_provider,
)


_COMMAND_ID = "task_effect.reconcile"
_PRINCIPAL_ID = "operator.effect.integration"


def _ingress_envelope(
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
        action="obs:command:task_effect.reconcile",
        required_scopes=scopes,
        idempotency_key="effect-reconciliation-integration-idempotency",
        nonce=f"effect-reconciliation-integration:{request_id}",
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
            correlation_id="effect-reconciliation-integration-correlation",
            surface="obs.command",
            action="obs:command:task_effect.reconcile",
            method="POST",
            normalized_path="/v1/host/obs-commands/task_effect.reconcile",
            principal_id=_PRINCIPAL_ID,
            tenant_id="effect-reconciliation-integration",
            key_id="effect-reconciliation-integration-hmac",
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
        tenant_id="effect-reconciliation-integration",
        request_id=request_id,
    )
    return (
        store,
        ObsCommandEnvelope(
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
            correlation_id=(
                "effect-reconciliation-integration-correlation"
            ),
        ),
        raw_body,
    )


@pytest.mark.asyncio
async def test_general_effect_proof_runs_only_after_governed_acceptance(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "general-effect-governed-v2"
    seed_store = _store(
        tmp_path,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {key_id: public_key}
        ),
    )
    db_path = Path(seed_store.db_path)
    target_effect_id = "acceptance-effect://general/governed-target"
    _unknown_effect(
        seed_store,
        effect_id=target_effect_id,
        status="FAILED",
    )
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = build_process_task_runtime_container(
        ledger=ledger,
        canonical_db_path=db_path,
        reconciliation_public_keys={key_id: public_key},
    )
    embodiment = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id="governed-effect-reconciliation-scope",
        command_port=_UnusedEmbodimentCommandPort(),
        authority_lease_ms=600_000,
    )
    operator_runtime = None
    ingress_store = None
    try:
        issued_ts_ms = int(time.time() * 1_000)
        downstream_receipt = "gateway-receipt://governed/general-target"
        challenge = (
            container.effect_reconciliation_service.build_operator_challenge(
                effect_id=target_effect_id,
                outcome="SIDE_EFFECT_CONFIRMED",
                reconciler_actor_id=_PRINCIPAL_ID,
                issued_ts_ms=issued_ts_ms,
                downstream_receipt_ref=downstream_receipt,
                executor_quiescence_ref=(
                    "executor-quiescence://governed/general-target"
                ),
                attestation_ref=(
                    "attestation://governed/general-effect-target"
                ),
                attestation_key_id=key_id,
            )
        )
        signing_bytes = base64.urlsafe_b64decode(
            str(challenge["signing_material"]["payload_b64"]).encode(
                "ascii"
            )
        )
        payload = {
            key: challenge[key]
            for key in (
                "effect_id",
                "case_id",
                "expected_case_snapshot_sha256",
                "expected_subject_snapshot_sha256",
                "purpose",
                "outcome",
                "issued_ts_ms",
                "downstream_receipt_ref",
                "executor_quiescence_ref",
                "attestation_ref",
                "attestation_key_id",
            )
        }
        payload["attestation_signature"] = (
            "ed25519:"
            + base64.urlsafe_b64encode(
                private_key.sign(signing_bytes)
            ).decode("ascii")
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
            initialization_event_id=(
                "effect-reconciliation-governed-hard-state"
            ),
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
        request_id = "request-governed-general-effect"
        ingress_store, envelope, raw_body = _ingress_envelope(
            db_path,
            request_id=request_id,
            payload=payload,
        )
        dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers=build_production_acceptance_effect_handlers(
                mode_transition_service_provider=lambda: mode_service,
                effect_reconciliation_service=(
                    container.effect_reconciliation_service
                ),
                effect_reconciliation_evidence_store=(
                    container.task_store
                ),
            ),
            owner_id="governed-effect-reconciliation-dispatcher",
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
            owner_id="governed-effect-reconciliation-operator",
        )
        await operator_runtime.activate()
        registry = build_default_obs_domain_command_registry()
        port = registry.resolve_port(_COMMAND_ID)
        assert port is not None
        state = SimpleNamespace(
            task_effect_reconciliation_operator_enabled=True,
            governed_operator_runtime=operator_runtime,
        )
        with bind_operator_command_runtime(state, raw_body=raw_body):
            first_domain = await port.execute(envelope)
        first = dict(first_domain.result["output"])
        assert first["status"] == "WAITING_REVIEW"
        assert first_domain.side_effects_executed is False
        before = container.task_store.read_acceptance_effect_reconciliation(
            target_effect_id
        )
        assert before["proof"] is None
        record = container.command_repository.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        assert record is not None
        durable_pack = dict(record["work_order"].input_pack or {})
        durable_intent = dict(durable_pack["intent_draft"])
        durable_args = dict(durable_intent["args"])
        serialized_pack = json.dumps(
            durable_pack,
            ensure_ascii=False,
            sort_keys=True,
        )
        assert "attestation_signature" not in serialized_pack
        assert "expected_subject_snapshot_sha256" not in serialized_pack
        evidence = (
            container.task_store
            .get_acceptance_effect_reconciliation_evidence(
                durable_args["evidence_ref"]
            )
        )
        assert evidence["attestation_signature"] == (
            payload["attestation_signature"]
        )
        assert evidence["evidence_digest"] == durable_args[
            "evidence_digest"
        ]

        review = first["reviews"][0]
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
                worker_id="governed-effect-review-worker",
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
        receipt = continued["execution_receipt"]
        assert receipt["accepted"] is True
        assert (
            continued["acceptance_effect_receipt"]["committed"] is True
        )
        settled = (
            container.task_store.read_acceptance_effect_reconciliation(
                target_effect_id,
                case_id=str(challenge["case_id"]),
            )
        )
        assert settled["proof"]["outcome"] == "SIDE_EFFECT_CONFIRMED"
        assert settled["current_effect"]["status"] == "COMMITTED"
        assert (
            settled["current_effect"]["receipt_ref"]
            == downstream_receipt
        )
        outer_effects = container.task_store.list_acceptance_effect_intents(
            graph_id=receipt["graph_id"],
            graph_revision=receipt["graph_revision"],
            node_id=receipt["node_id"],
            limit=10,
        )
        assert len(outer_effects) == 1
        assert outer_effects[0]["effect_type"] == (
            "task.acceptance_effect_reconciliation"
        )
        assert outer_effects[0]["status"] == "COMMITTED"

        with bind_operator_command_runtime(state, raw_body=raw_body):
            replay_domain = await port.execute(envelope)
        replay = dict(replay_domain.result["output"])
        assert replay["exact_replay"] is True
        assert replay_domain.side_effects_executed is False
        assert replay["acceptance_effect_receipt"]["proof"]["proof_id"] == (
            settled["proof"]["proof_id"]
        )
    finally:
        if operator_runtime is not None:
            await operator_runtime.aclose()
        if ingress_store is not None:
            ingress_store.close()
        await container.aclose()
        embodiment.close()
        ledger.close()
