from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.app.governed_operator_runtime import (
    GovernedOperatorRuntimeError,
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
from host_brain.observability.command_outcome_journal import ObsCommandOutcomeJournal
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
    EmbodimentRuntimeAuthority,
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
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from natsume_contracts.edge.embodiment_v1 import (
    EmbodimentReceipt,
    parse_embodiment_command,
)
from natsume_contracts.task import HostCommandSurface


class _RecordingEdgePort:
    def __init__(self) -> None:
        self.submit_calls: list[str] = []
        self.lookup_calls: list[str] = []
        self.receipts: dict[tuple[str, str], EmbodimentReceipt] = {}

    def applied_receipt(self, command: Any) -> EmbodimentReceipt:
        return EmbodimentReceipt(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            accepted=True,
            status="APPLIED",
            reason_code=None,
            detail="edge expression applied",
            received_at_ms=command.issued_at_ms,
            completed_at_ms=command.issued_at_ms + 1,
            event={"external_side_effects": True},
            receipt_ref=(
                "edge-embodiment-receipt://ne01/"
                f"{hashlib.sha256(command.command_id.encode('utf-8')).hexdigest()}"
            ),
        )

    async def submit(self, command: Any) -> EmbodimentReceipt:
        self.submit_calls.append(command.command_id)
        receipt = self.applied_receipt(command)
        self.receipts[(command.command_id, command.idempotency_key)] = receipt
        return receipt

    async def query_receipt(self, command: Any) -> EmbodimentReceipt | None:
        self.lookup_calls.append(command.command_id)
        return self.receipts.get((command.command_id, command.idempotency_key))


def _runtime_authority_provider(
    embodiment_runtime: HostEmbodimentRuntime,
) -> dict[str, Any]:
    return {
        **embodiment_runtime.renew_authority(),
        "runtime_scope_id": embodiment_runtime.runtime_scope_id,
    }


def _edge_envelope(
    *,
    store: SQLiteSecureIngressStore,
    request_id: str,
    idempotency_key: str,
    command_payload: Mapping[str, Any],
) -> tuple[ObsCommandEnvelope, bytes]:
    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec("edge.command.push")
    assert spec is not None
    raw_body = json.dumps(
        {
            "command_id": "edge.command.push",
            "payload": dict(command_payload),
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    now = int(time.time() * 1000)
    scopes = tuple(spec.required_scopes)
    claim = store.try_claim(
        principal_id="principal.operator.edge.integration",
        surface="obs.command",
        action=spec.action,
        required_scopes=scopes,
        idempotency_key=idempotency_key,
        nonce=f"operator-edge-integration-nonce:{request_id}",
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
            correlation_id=f"operator-edge-correlation:{request_id}",
            surface="obs.command",
            action=spec.action,
            method="POST",
            normalized_path="/v1/host/obs-commands/edge.command.push",
            principal_id="principal.operator.edge.integration",
            tenant_id="operator-edge-integration",
            key_id="operator-edge-integration-key",
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
    authority = ObsCommandAuthority.bind(
        principal_id="principal.operator.edge.integration",
        tenant_id="operator-edge-integration",
        request_id=request_id,
    )
    return (
        ObsCommandEnvelope(
            schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
            command_id="edge.command.push",
            action=spec.action,
            required_scopes=scopes,
            payload=bind_server_authority(
                spec.payload_schema.validate(dict(command_payload)),
                spec=spec,
                authority=authority,
            ),
            authority=authority,
            claim_ref=str(claim.claim_ref),
            audit_ref=str(audit_ref),
            body_sha256=body_sha256,
            correlation_id=f"operator-edge-correlation:{request_id}",
        ),
        raw_body,
    )


async def _approve_and_continue(
    *,
    operator_runtime: Any,
    container: Any,
    envelope: ObsCommandEnvelope,
    raw_body: bytes,
    worker_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = build_default_obs_domain_command_registry()
    port = registry.resolve_port("edge.command.push")
    assert port is not None
    app_state = SimpleNamespace(governed_operator_runtime=operator_runtime)
    with bind_operator_command_runtime(app_state, raw_body=raw_body):
        first_domain = await port.execute(envelope)
    first = dict(first_domain.result["output"])
    assert first_domain.success is True
    assert first_domain.side_effects_executed is False
    assert first["status"] == "WAITING_REVIEW"
    assert len(first["reviews"]) == 1
    review = first["reviews"][0]
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
    raw_claim = container.review_continuation_store.claim_pending_review_continuations(
        worker_id=worker_id,
        lease_ms=60_000,
        limit=1,
    )[0]
    continuation_claim = (
        container.review_continuation_store.validate_review_continuation_claim(
            raw_claim
        )
    )
    record = container.command_repository.get_by_request(
        surface=HostCommandSurface.OPERATOR.value,
        request_id=envelope.authority.request_id,
    )
    assert record is not None
    continued = await operator_runtime.continue_review_admission(
        record,
        continuation_claim,
    )
    return first, dict(continued)


@pytest.mark.asyncio
async def test_edge_command_enters_taskgraph_then_dispatches_once_and_reconciles(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "governed-operator-edge.sqlite3"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    outcome_journal = ObsCommandOutcomeJournal(
        tmp_path / "governed-operator-edge-outcomes.jsonl"
    )
    container = None
    embodiment_runtime = None
    operator_runtime = None
    ingress_store = None
    hard_state_owner = None
    edge_port = _RecordingEdgePort()
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
            authenticated_outcome_lookup=outcome_journal.lookup,
        )
        repository = EmbodimentOutboxRepository(db_path)
        embodiment_runtime = HostEmbodimentRuntime(
            repository=repository,
            worker_id="ne01-governed-edge-runtime",
            command_port=edge_port,
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
                    lambda: _runtime_authority_provider(embodiment_runtime)
                ),
            ),
            initialization_event_id="ne01-edge-hard-state-initialized",
        )
        mode_transition_service = ModeTransitionService(
            get_hard_state=hard_state_owner.get_hard_state,
            set_sys_mode=hard_state_owner.set_sys_mode,
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
        ingress_store = SQLiteSecureIngressStore(db_path, rate_limit=100)
        handlers = build_production_acceptance_effect_handlers(
            mode_transition_service_provider=lambda: mode_transition_service,
            embodiment_runtime_provider=lambda: embodiment_runtime,
        )
        acceptance_effect_dispatcher = AcceptanceEffectDispatcher(
            task_store=container.task_store,
            handlers=handlers,
            owner_id="ne01-governed-edge-effects",
            action_preview_service=action_preview_service,
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
            owner_id="ne01-governed-edge-owner",
            embodiment_runtime_provider=lambda: embodiment_runtime,
        )
        await operator_runtime.activate()

        request_id = "request-ne01-governed-edge-first"
        envelope, raw_body = _edge_envelope(
            store=ingress_store,
            request_id=request_id,
            idempotency_key="ne01-governed-edge-first",
            command_payload={
                "command_type": "emotion_state_command",
                "target_agent": "edge.somatic",
                "payload": {"emotion_tag": "happy", "intensity": 0.8},
            },
        )
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM host_embodiment_outbox"
            ).fetchone()[0] == 0
        first, continued = await _approve_and_continue(
            operator_runtime=operator_runtime,
            container=container,
            envelope=envelope,
            raw_body=raw_body,
            worker_id="ne01-edge-review-first",
        )
        assert first["status"] == "WAITING_REVIEW"
        assert continued["execution_receipt"]["accepted"] is True
        assert continued["historical_side_effects_executed"] is False
        assert continued["acceptance_effect_receipt"]["delivery_status"] == "PENDING"
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            outbox = connection.execute(
                "SELECT * FROM host_embodiment_outbox"
            ).fetchone()
            assert outbox is not None
            assert outbox["status"] == "PENDING"
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM task_runtime_acceptance_effect_intent
                WHERE effect_type = 'edge.embodiment_enqueue'
                  AND status = 'COMMITTED'
                """
            ).fetchone()[0] == 1
        assert edge_port.submit_calls == []

        dispatched = await embodiment_runtime.dispatch_pending_async(limit=10)
        assert dispatched.applied == 1
        assert len(edge_port.submit_calls) == 1
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT status FROM host_embodiment_outbox"
            ).fetchone()[0] == "APPLIED"

        registry = build_default_obs_domain_command_registry()
        port = registry.resolve_port("edge.command.push")
        assert port is not None
        with bind_operator_command_runtime(
            SimpleNamespace(governed_operator_runtime=operator_runtime),
            raw_body=raw_body,
        ):
            replay_domain = await port.execute(envelope)
        replay = dict(replay_domain.result["output"])
        assert replay["success"] is True
        assert replay["exact_replay"] is True
        assert replay_domain.side_effects_executed is False
        assert replay["historical_side_effects_executed"] is True
        assert replay["delivery_status"] == "APPLIED"
        assert len(edge_port.submit_calls) == 1
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM host_embodiment_outbox"
            ).fetchone()[0] == 1

        unsupported_id = "request-ne01-governed-edge-unsupported"
        unsupported, unsupported_body = _edge_envelope(
            store=ingress_store,
            request_id=unsupported_id,
            idempotency_key="ne01-governed-edge-unsupported",
            command_payload={
                "command_type": "gate_control_command",
                "target_agent": "edge.channel",
                "payload": {"gate_open": True},
            },
        )
        with bind_operator_command_runtime(
            SimpleNamespace(governed_operator_runtime=operator_runtime),
            raw_body=unsupported_body,
        ):
            with pytest.raises(
                GovernedOperatorRuntimeError,
                match="only edge.somatic emotion_state_command",
            ):
                await port.execute(unsupported)
        assert container.command_repository.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=unsupported_id,
        ) is None
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM host_embodiment_outbox"
            ).fetchone()[0] == 1

        # A second accepted command loses its Host response after the durable
        # dispatch claim.  The replacement runtime may only query the Edge
        # receipt and settle IN_DOUBT; it must never submit the command again.
        crash_id = "request-ne01-governed-edge-crash"
        crash_envelope, crash_body = _edge_envelope(
            store=ingress_store,
            request_id=crash_id,
            idempotency_key="ne01-governed-edge-crash",
            command_payload={
                "command_type": "emotion_state_command",
                "target_agent": "edge.somatic",
                "payload": {"emotion_tag": "calm", "intensity": 0.4},
            },
        )
        _, crash_continued = await _approve_and_continue(
            operator_runtime=operator_runtime,
            container=container,
            envelope=crash_envelope,
            raw_body=crash_body,
            worker_id="ne01-edge-review-crash",
        )
        crash_outcome = crash_continued["acceptance_effect_receipt"]
        crash_outbox_id = crash_outcome["outbox_id"]
        runtime_lease = embodiment_runtime.authority_lease
        assert runtime_lease is not None
        claimed = repository.claim_next(
            worker_id="ne01-governed-edge-runtime",
            authority=EmbodimentRuntimeAuthority(
                worker_id="ne01-governed-edge-runtime",
                lease_id=str(runtime_lease["lease_id"]),
                fencing_token=int(runtime_lease["fencing_token"]),
            ),
            lease_ms=1_000,
        )
        assert claimed is not None
        assert claimed["outbox_id"] == crash_outbox_id
        parsed_command = parse_embodiment_command(claimed["command"])
        edge_port.receipts[
            (parsed_command.command_id, parsed_command.idempotency_key)
        ] = edge_port.applied_receipt(parsed_command)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                UPDATE host_embodiment_outbox
                SET claim_expires_at_ms = 0
                WHERE outbox_id = ?
                """,
                (crash_outbox_id,),
            )
            connection.commit()
        embodiment_runtime.close()
        embodiment_runtime = HostEmbodimentRuntime(
            repository=repository,
            worker_id="ne01-governed-edge-runtime",
            command_port=edge_port,
            authority_lease_ms=600_000,
        )
        before_reconcile_submit_count = len(edge_port.submit_calls)
        reconciled = await embodiment_runtime.reconcile_in_doubt_async(limit=10)
        assert reconciled.expired == 1
        assert reconciled.applied == 1
        assert len(edge_port.submit_calls) == before_reconcile_submit_count
        assert edge_port.lookup_calls == [parsed_command.command_id]
        reconciled_row = repository.get(crash_outbox_id)
        assert reconciled_row is not None
        assert reconciled_row["status"] == "APPLIED"
    finally:
        if hard_state_owner is not None:
            hard_state_owner.unbind_durable_journal()
        if operator_runtime is not None:
            await operator_runtime.aclose()
        if ingress_store is not None:
            ingress_store.close()
        if embodiment_runtime is not None and not embodiment_runtime.closed:
            embodiment_runtime.close()
        if container is not None:
            await container.aclose()
        ledger.close()
