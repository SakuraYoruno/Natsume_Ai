from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
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
from host_brain.composition.task_runtime import build_process_task_runtime_container
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
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.runtime.action_preview_resolver import (
    ProductionActionPreviewAuthorityResolver,
)
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from natsume_contracts.task import HostCommandSurface
from tests.contract.test_ne01_atomic_failure_bundle import NOW, _event, _graph
from tests.integration.test_ne01_governed_operator_sys_mode import (
    _UnusedEmbodimentCommandPort,
    _runtime_authority_provider,
)
from tests.task_runtime_authority_fixtures import seed_raw_task_graph_for_test
from tests.unit.test_ne01_terminal_reconciliation_classification import (
    _active_budget,
)


_COMMAND_ID = "task_terminal.reconcile"
_PRINCIPAL_ID = "operator.terminal.integration"


def _terminal_ingress_envelope(
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
        action="obs:command:task_terminal.reconcile",
        required_scopes=scopes,
        idempotency_key="operator-terminal-integration-idempotency",
        nonce=f"operator-terminal-integration-nonce:{request_id}",
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
            correlation_id="operator-terminal-integration-correlation",
            surface="obs.command",
            action="obs:command:task_terminal.reconcile",
            method="POST",
            normalized_path="/v1/host/obs-commands/task_terminal.reconcile",
            principal_id=_PRINCIPAL_ID,
            tenant_id="operator-terminal-integration",
            key_id="operator-terminal-integration-hmac",
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
        tenant_id="operator-terminal-integration",
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
        correlation_id="operator-terminal-integration-correlation",
    )
    return store, envelope, raw_body


@pytest.mark.asyncio
async def test_signed_terminal_repair_runs_only_after_governed_acceptance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "governed-terminal-operator.sqlite3"
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "terminal-operator-integration-v1"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = build_process_task_runtime_container(
        ledger=ledger,
        canonical_db_path=db_path,
        reconciliation_public_keys={key_id: public_key},
    )
    embodiment = HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id="governed-terminal-operator-scope",
        command_port=_UnusedEmbodimentCommandPort(),
        authority_lease_ms=600_000,
    )
    operator_runtime = None
    ingress_store = None
    try:
        target = _graph()
        seed_raw_task_graph_for_test(
            container.task_store,
            target,
            outbox=_event(
                "TASK_GRAPH",
                target.graph_id,
                0,
                "TASK_GRAPH_CREATED",
                identity="terminal-governed-created",
            ),
        )
        container.task_store.transition_graph(
            target.graph_id,
            target.revision,
            expected_version=0,
            target_status=TaskGraphStatus.ACTIVE,
            outbox=_event(
                "TASK_GRAPH",
                target.graph_id,
                1,
                "TASK_GRAPH_ACTIVE",
                identity="terminal-governed-active",
            ),
        )
        budget_id = _active_budget(
            container.task_store,
            budget_id="budget-terminal-governed-operator",
        )
        container.task_store.transition_graph(
            target.graph_id,
            target.revision,
            expected_version=1,
            target_status=TaskGraphStatus.CANCELLED,
            outbox=_event(
                "TASK_GRAPH",
                target.graph_id,
                2,
                "TASK_GRAPH_CANCELLED",
                identity="terminal-governed-cancelled",
            ),
        )
        inventory = container.task_store.inventory_terminal_reconciliation_cases()
        case_id = str(inventory["cases"][0]["case_id"])
        issued_ts_ms = NOW + 900
        challenge = container.terminal_reconciliation_service.build_operator_challenge(
            case_id=case_id,
            reconciler_actor_id=_PRINCIPAL_ID,
            issued_ts_ms=issued_ts_ms,
            attestation_ref="attestation://terminal/governed-operator",
            attestation_key_id=key_id,
        )
        binding = base64.urlsafe_b64decode(
            challenge["signing_material"]["payload_b64"].encode("ascii")
        )
        payload = {
            "case_id": case_id,
            "expected_case_snapshot_sha256": challenge[
                "case_snapshot_sha256"
            ],
            "expected_subject_snapshot_sha256": challenge[
                "subject_snapshot_sha256"
            ],
            "action": challenge["action"],
            "target_graph_status": challenge["target_graph_status"],
            "target_budget_status": challenge["target_budget_status"],
            "issued_ts_ms": issued_ts_ms,
            "attestation_ref": challenge["authority"]["attestation_ref"],
            "attestation_key_id": key_id,
            "attestation_signature": "ed25519:"
            + base64.urlsafe_b64encode(private_key.sign(binding)).decode("ascii"),
        }

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
            initialization_event_id="terminal-governed-hard-state",
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
        request_id = "request-governed-terminal-operator"
        ingress_store, envelope, raw_body = _terminal_ingress_envelope(
            db_path,
            request_id=request_id,
            payload=payload,
        )
        dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers=build_production_acceptance_effect_handlers(
                mode_transition_service_provider=lambda: mode_service,
                terminal_reconciliation_service=(
                    container.terminal_reconciliation_service
                ),
                terminal_reconciliation_store=container.task_store,
                terminal_operator_command_store=(
                    container.terminal_operator_command_store
                ),
            ),
            owner_id="governed-terminal-acceptance-effects",
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
            owner_id="governed-terminal-operator",
        )
        await operator_runtime.activate()
        registry = build_default_obs_domain_command_registry()
        port = registry.resolve_port(_COMMAND_ID)
        assert port is not None
        state = SimpleNamespace(
            task_terminal_reconciliation_operator_enabled=True,
            governed_operator_runtime=operator_runtime,
        )
        with bind_operator_command_runtime(state, raw_body=raw_body):
            first_domain = await port.execute(envelope)
        first = dict(first_domain.result["output"])
        assert first["status"] == "WAITING_REVIEW"
        assert first_domain.side_effects_executed is False
        assert container.task_store.read_terminal_reconciliation_case(case_id)[
            "proof"
        ] is None
        assert container.task_store.reconcile_budget(budget_id)["status"] == (
            "ACTIVE"
        )

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
        claim = container.review_continuation_store.claim_pending_review_continuations(
            worker_id="governed-terminal-review-worker",
            lease_ms=60_000,
            limit=1,
        )[0]
        continuation = (
            container.review_continuation_store.validate_review_continuation_claim(
                claim
            )
        )
        record = container.command_repository.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        assert record is not None
        frozen_input = json.dumps(
            record["work_order"].input_pack,
            ensure_ascii=False,
            sort_keys=True,
        )
        assert "attestation_signature" not in frozen_input
        assert "attestation_key_id" not in frozen_input
        frozen_args = record["work_order"].input_pack["intent_draft"]["args"]
        signed_command = container.terminal_operator_command_store.read(
            frozen_args["signed_command_ref"]
        )
        assert signed_command["request_id"] == request_id
        assert signed_command["command"]["attestation_signature"] == (
            payload["attestation_signature"]
        )
        continued = await operator_runtime.continue_review_admission(
            record,
            continuation,
        )
        assert continued["execution_receipt"]["accepted"] is True
        assert continued["acceptance_effect_receipt"]["committed"] is True
        proof = container.task_store.read_terminal_reconciliation_case(case_id)[
            "proof"
        ]
        assert proof["authority_kind"] == "SIGNED_OPERATOR"
        assert proof["reconciler_actor_id"] == _PRINCIPAL_ID
        assert container.task_store.reconcile_budget(budget_id)["status"] == (
            "RELEASED"
        )
        effects = container.task_store.list_acceptance_effect_intents(
            graph_id=continued["execution_receipt"]["graph_id"],
            graph_revision=continued["execution_receipt"]["graph_revision"],
            limit=10,
        )
        assert len(effects) == 1
        assert effects[0]["effect_type"] == "task.terminal_reconciliation"
        assert effects[0]["status"] == "COMMITTED"
        with bind_operator_command_runtime(state, raw_body=raw_body):
            replay_domain = await port.execute(envelope)
        replay = dict(replay_domain.result["output"])
        assert replay["exact_replay"] is True
        assert replay_domain.side_effects_executed is False
        assert replay["acceptance_effect_receipt"]["handler_receipt_ref"] == (
            proof["proof_id"]
        )
        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM task_terminal_operator_signed_command"
            ).fetchone() == (1,)
    finally:
        if operator_runtime is not None:
            await operator_runtime.aclose()
        if ingress_store is not None:
            ingress_store.close()
        await container.aclose()
        embodiment.close()
        ledger.close()
