from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from natsume_contracts.task import HostCommandSurface

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.observability_runtime.command_composition import (
    build_obs_command_composition,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.integrations.runtime_events import (
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.observability.command_http import create_obs_command_http_router
from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandPortRegistry,
)
from host_brain.observability.secure_command_ingress import (
    ObsCommandIngressRequest,
    build_obs_command_secure_request,
)
from host_brain.persistence.host_command_repository import (
    HOST_COMMAND_INGRESS_CONFLICT,
    HostCommandRepository,
    HostCommandRepositoryError,
)
from host_brain.persistence.review_continuation_store import ReviewContinuationStore
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
)
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.scheduler.governed_runtime import (
    SchedulerCommandService,
    SchedulerDefinitionPolicyEvaluator,
    build_governed_scheduler_runtime,
)
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry
from host_brain.secure_ingress import build_hmac_sha256_signature


COMMAND_ID = "scheduler.task.trigger"
PRINCIPAL_ID = "operator.scheduler.integration"
TENANT_ID = "tenant.operator.integration"
KEY_ID = "operator.scheduler.hmac.v1"
SECRET = "operator-scheduler-integration-secret"


def _definition() -> dict[str, Any]:
    return {
        "trigger_mode": "interval",
        "trigger_config": {"seconds": 60},
        "intent_template": {
            "skill_name": "operator_governed_worker",
            "args": {},
        },
        "worker_key": "operator_governed_worker",
        "priority": "HIGH",
        "mode_allowlist": ["normal"],
        "resource_profile": {},
        "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
        "risk_level": 3,
        "enabled": True,
    }


class _ApprovingIntentEngine:
    def __init__(self, ledger: SQLiteLedger, tokens: TokenService) -> None:
        self._ledger = ledger
        self._tokens = tokens
        self.calls = 0

    def evaluate_intent(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        intent = dict(kwargs["raw_intent"])
        approval = self._tokens.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=intent["ts_ms"],
        )
        binding = build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref=approval,
            risk_level=3,
            issued_at_ms=intent["ts_ms"],
            expires_at_ms=intent["ts_ms"] + 120_000,
        )
        decision = {
            "request_id": kwargs["request_id"],
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "reason_code": None,
            "tokens": {"approval_token_ref": approval},
            "approved_action_binding": binding,
            "snapshot_refs": {},
            "stage": "S_INTENT_OK",
            "ts_ms": intent["ts_ms"],
        }
        self._ledger.log_governor(decision)
        return decision


def _seed_operator_principal(db_path: Path, *, now_ms: int) -> None:
    spec = ObsCommandPortRegistry().resolve_spec(COMMAND_ID)
    assert spec is not None
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms,
                updated_at_ms
            ) VALUES (?, 'service', ?, 'ACTIVE', 1, ?, '["obs"]', ?, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                TENANT_ID,
                json.dumps(list(spec.required_scopes)),
                json.dumps([spec.action]),
                now_ms - 60_000,
                now_ms + 3_600_000,
                now_ms,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm,
                secret_ref, status, version, valid_from_ms,
                valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?,
                      'ACTIVE', 1, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                KEY_ID,
                f"test-secret-ref://{KEY_ID}",
                now_ms - 60_000,
                now_ms + 3_600_000,
                now_ms,
            ),
        )


def _identity(*, now_ms: int, request_id: str) -> IngressIdentityEnvelope:
    spec = ObsCommandPortRegistry().resolve_spec(COMMAND_ID)
    assert spec is not None
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="operator.scheduler.integration.proof.1",
        issued_at_epoch_ms=now_ms,
        expires_at_epoch_ms=now_ms + 600_000,
    )
    return IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=PRINCIPAL_ID,
            kind=PrincipalKind.SERVICE,
            tenant_id=TENANT_ID,
            granted_scopes=spec.required_scopes,
        ),
        credential_proof=metadata,
        audience="obs",
        request_id=request_id,
        requested_scopes=spec.required_scopes,
    )


def _signed_headers(
    *,
    registry: ObsCommandPortRegistry,
    identity: IngressIdentityEnvelope,
    raw_body: bytes,
    now_ms: int,
) -> dict[str, str]:
    spec = registry.resolve_spec(COMMAND_ID)
    assert spec is not None
    path = f"/v1/host/obs-commands/{COMMAND_ID}"
    unsigned = ObsCommandIngressRequest(
        command_id=COMMAND_ID,
        raw_body=raw_body,
        identity=identity,
        credential=CredentialProof(
            metadata=identity.credential_proof,
            raw_proof="0" * 64,
        ),
        idempotency_key="operator.scheduler.integration.idem.1",
        nonce="operator.scheduler.integration.nonce.1",
        received_at_epoch_ms=now_ms,
        transport_key="http-peer:127.0.0.1",
        normalized_path=path,
        correlation_id="operator.scheduler.integration.correlation.1",
    )
    secure = build_obs_command_secure_request(unsigned, spec)
    encoded_identity = base64.urlsafe_b64encode(
        identity.to_canonical_json().encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    return {
        "Content-Type": "application/json",
        "X-Natsume-Identity": encoded_identity,
        "X-Natsume-Proof": build_hmac_sha256_signature(secure, SECRET),
        "X-Natsume-Idempotency-Key": unsigned.idempotency_key,
        "X-Natsume-Nonce": unsigned.nonce,
        "X-Natsume-Correlation-Id": str(unsigned.correlation_id),
    }


@pytest.mark.asyncio
async def test_authenticated_operator_trigger_runs_one_reviewed_durable_taskgraph(
    tmp_path: Path,
) -> None:
    import time

    now_ms = int(time.time() * 1_000)
    db_path = tmp_path / "operator-scheduler.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-governed-operator-scheduler-trigger",
        ts_ms=now_ms,
    )
    assert migrated.success, migrated.as_dict()
    _seed_operator_principal(db_path, now_ms=now_ms)

    definitions = SchedulerDefinitionRepository(db_path)
    definitions.publish_definition(
        task_id="operator_governed_task",
        definition=_definition(),
        expected_head_version=0,
        next_scheduled_at_ms=now_ms + 60_000,
    )
    results = SchedulerExecutionResultRepository(db_path)
    task_store = DurableTaskStore(db_path)
    review_store = ReviewStore(db_path)
    continuation_store = ReviewContinuationStore(db_path)
    composition = None

    def authenticated_outcome_lookup(receipt_ref: str) -> Any:
        assert composition is not None
        assert composition.outcome_journal is not None
        return composition.outcome_journal.lookup(receipt_ref)

    commands = HostCommandRepository(
        db_path,
        authenticated_outcome_lookup=authenticated_outcome_lookup,
    )
    event_store = RuntimeEventStore(db_path)
    infrastructure = SimpleNamespace(
        task_store=task_store,
        review_store=review_store,
        review_continuation_store=continuation_store,
        command_repository=commands,
        outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
        scheduler_definition_repository=definitions,
        graph_execution_registry=None,
    )
    handler_calls: list[dict[str, Any]] = []

    async def handler(**kwargs: Any) -> dict[str, Any]:
        handler_calls.append(dict(kwargs["execution_context"]))
        return {"status": "OK", "summary": "governed Operator worker ran"}

    workers = SchedulerWorkerRegistry()
    workers.register("operator_governed_worker", handler)
    ledger = SQLiteLedger(str(db_path))
    tokens = TokenService(approval_ttl_ms=180_000)
    scheduler_runtime = build_governed_scheduler_runtime(
        task_runtime_container=infrastructure,
        ledger=ledger,
        token_service=tokens,
        result_repository=results,
        runtime_router=CanonicalRuntimeRouter(),
        worker_registry=workers,
        owner_id="operator-scheduler-runtime-owner",
    )
    claim_store = SQLiteSecureIngressStore(db_path)
    runtime_group = GovernedRuntimeGroup(
        {"scheduler": scheduler_runtime},
        review_store=continuation_store,
        review_decision_store=review_store,
        command_repository=commands,
        claim_result_store=claim_store,
        review_continuation_worker_id="operator-scheduler-review-worker",
    )
    engine = _ApprovingIntentEngine(ledger, tokens)
    service = SchedulerCommandService(
        runtime=scheduler_runtime,
        intent_engine=engine,
        claim_result_store=claim_store,
        definition_repository=definitions,
        policy_evaluator=SchedulerDefinitionPolicyEvaluator(
            current_mode=lambda: "normal"
        ),
        worker_registry=workers,
    )
    await runtime_group.activate()
    try:
        composition = build_obs_command_composition(
            env={
                "HOST_SECURE_INGRESS_MODE": "ENFORCE",
                "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps(
                    {KEY_ID: SECRET}
                ),
            },
            db_path=db_path,
        )
        assert composition.execution_enabled is True
        app = FastAPI()
        app.state.scheduler_container = SimpleNamespace(command_service=service)
        app.include_router(
            create_obs_command_http_router(
                ingress_provider=lambda: composition.ingress,
                clock_ms=lambda: now_ms,
            )
        )
        request_id = "operator.scheduler.integration.request.1"
        identity = _identity(now_ms=now_ms, request_id=request_id)
        raw_body = json.dumps(
            {
                "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
                "command_id": COMMAND_ID,
                "payload": {
                    "task_id": "operator_governed_task",
                    "payload": {"reason": "authenticated integration"},
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = _signed_headers(
            registry=composition.registry,
            identity=identity,
            raw_body=raw_body,
            now_ms=now_ms,
        )
        transport = httpx.ASGITransport(
            app=app,
            client=("127.0.0.1", 12345),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/v1/host/obs-commands/{COMMAND_ID}",
                content=raw_body,
                headers=headers,
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        assert response.json()["executed"] is False
        assert handler_calls == []

        admitted = commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        assert admitted is not None
        command = admitted["command"]
        graph = admitted["graph"]
        order = admitted["work_order"]
        assert command.surface is HostCommandSurface.OPERATOR
        assert command.ingress_claim_ref == response.json()["claim_ref"]
        assert command.ingress_audit_ref == response.json()["audit_ref"]
        assert graph.revision == 1 and len(graph.nodes) == 1
        assert order.review_policy["durable_review_required"] is True
        assert order.budget_policy is not None

        waiting = task_store.read_scheduler_snapshot(
            command.graph_id,
            command.graph_revision,
        )
        assert waiting["graph_record"]["status"] == "BLOCKED"
        assert waiting["graph_record"]["nodes"][0]["status"] == (
            "WAITING_APPROVAL"
        )
        assert waiting["attempts"] == []
        reviews = review_store.list_reviews(
            task_graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        assert len(reviews) == 1
        review = dict(reviews[0]["review"])
        assert review["status"] == "PENDING"
        approved = review_store.approve_review(
            review["review_id"],
            actor_id=PRINCIPAL_ID,
            expected_subject_ref=review["subject_ref"],
            expected_subject_version=review["subject_version"],
            expected_resume_phase=review["resume_phase"],
            expected_checkpoint_id=review["checkpoint_id"],
        )
        review_store.resume_review(
            review["review_id"],
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=review["subject_ref"],
            subject_version=review["subject_version"],
            resume_phase=review["resume_phase"],
            checkpoint_id=review["checkpoint_id"],
        )
        worker = runtime_group.review_continuation_worker
        assert worker is not None
        await worker.run_once()

        final = task_store.read_scheduler_snapshot(
            command.graph_id,
            command.graph_revision,
        )
        assert final["graph_record"]["status"] == "COMPLETED"
        assert final["graph_record"]["nodes"][0]["status"] == "ACCEPTED"
        assert len(final["attempts"]) == 1
        assert final["budgets"][0]["status"] == "CONSUMED"
        assert len(handler_calls) == 1
        verified_grant = handler_calls[0]["verified_node_grant"]
        assert verified_grant["grant_ref"] != command.governor_grant_ref
        assert verified_grant["parent_grant_ref"] == command.governor_grant_ref
        assert review_store.get_review(review["review_id"])["review"]["status"] == (
            "CONSUMED"
        )

        occurrence = definitions.get_occurrence(command.input_ref)
        assert occurrence is not None
        replay = await service.execute_occurrence(
            occurrence=occurrence,
            authority={
                "allowed": True,
                "duplicate": False,
                "request_id": request_id,
                "principal_id": PRINCIPAL_ID,
                "claim_ref": command.ingress_claim_ref,
                "audit_ref": command.ingress_audit_ref,
                "body_sha256": command.input_sha256,
                "ingress_surface": "obs.command",
                "operator_command_id": COMMAND_ID,
                "action": f"obs:command:{COMMAND_ID}",
                "required_scopes": ("obs:write", "scheduler:write"),
            },
            received_at_ms=now_ms,
            command_surface=HostCommandSurface.OPERATOR,
        )
        receipt = replay["execution_receipt"]
        assert receipt["status"] == "ACCEPTED"
        assert receipt["verification_status"] == "PASS"
        assert receipt["accepted"] is True
        assert replay["replayed"] is True
        assert len(handler_calls) == 1

        with sqlite3.connect(str(db_path)) as conn:
            outer_result_ref = conn.execute(
                """
                SELECT result_ref
                FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (command.ingress_claim_ref,),
            ).fetchone()[0]
        assert outer_result_ref == response.json()["result_ref"]
        assert outer_result_ref != receipt["receipt_ref"]

        reopened = HostCommandRepository(
            db_path,
            authenticated_outcome_lookup=authenticated_outcome_lookup,
        )
        validated = reopened.validate_ingress_claim_result(command.command_id)
        assert validated["command"] == command

        missing_lookup = HostCommandRepository(db_path)
        with pytest.raises(HostCommandRepositoryError) as missing:
            missing_lookup.validate_ingress_claim_result(command.command_id)
        assert missing.value.reason_code == HOST_COMMAND_INGRESS_CONFLICT

        def raising_lookup(_receipt_ref: str) -> Any:
            raise OSError("outcome journal unavailable")

        unavailable_lookup = HostCommandRepository(
            db_path,
            authenticated_outcome_lookup=raising_lookup,
        )
        with pytest.raises(HostCommandRepositoryError) as unavailable:
            unavailable_lookup.validate_ingress_claim_result(command.command_id)
        assert unavailable.value.reason_code == HOST_COMMAND_INGRESS_CONFLICT

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                UPDATE secure_ingress_idempotency_claim
                SET result_ref = 'obs-command://result/tampered/arbitrary'
                WHERE claim_ref = ?
                """,
                (command.ingress_claim_ref,),
            )
        with pytest.raises(HostCommandRepositoryError) as raised:
            reopened.validate_ingress_claim_result(command.command_id)
        assert raised.value.reason_code == HOST_COMMAND_INGRESS_CONFLICT
    finally:
        if composition is not None:
            composition.close()
        await runtime_group.aclose()
        claim_store.close()
        ledger.close()


def test_operator_command_inventory_keeps_every_other_writer_fail_closed() -> None:
    from host_brain.app.observability_runtime.domain_command_ports import (
        BLOCKED_OPERATOR_COMMAND_IDS,
        GOVERNED_OPERATOR_COMMAND_IDS,
    )

    manifest = {spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS}
    assert GOVERNED_OPERATOR_COMMAND_IDS == {
        COMMAND_ID,
        "sys_mode.switch",
    }
    assert len(BLOCKED_OPERATOR_COMMAND_IDS) == 35
    assert BLOCKED_OPERATOR_COMMAND_IDS | GOVERNED_OPERATOR_COMMAND_IDS == manifest
