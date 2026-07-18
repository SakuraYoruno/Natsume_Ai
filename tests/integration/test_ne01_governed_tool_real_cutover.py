from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import build_governed_host_runtime
from host_brain.app.governed_tool_runtime import (
    GovernedToolExecutionError,
    GovernedToolHttpApplication,
)
from host_brain.app.model_provider_composition import ApplicationModelAttemptServicePort
from host_brain.app.production_authority import verify_durable_production_authority
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition import process_services as dependencies
from host_brain.composition.command_ingress import build_process_command_ingress_container
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.composition.task_runtime import build_process_task_runtime_container
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.runtime.degradation_authority import (
    LegacyModuleGlobalDegradationAdapter,
)
from host_brain.secure_ingress import (
    IngressSurface,
    SecureIngressHttpAuthorityAdapter,
    SecureIngressMode,
    build_hmac_sha256_signature,
    build_surface_request,
)
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry
from host_brain.skills.capability_scorecard_provider import (
    FederatedCapabilityScorecardProvider,
)
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog, ToolMeta


NOW = 1_800_200_000_000
PRINCIPAL = "service.host.tool.integration"
KEY_ID = "host.tool.integration.hmac.v1"
SECRET = "host-tool-integration-secret-at-least-32-bytes"


def _seed_tool_principal(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, 'service', 'integration', 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                PRINCIPAL,
                json.dumps(["host:write"]),
                json.dumps(["host"]),
                json.dumps(["host:tool"]),
                NOW - 1_000,
                NOW + 600_000,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm, secret_ref,
                status, version, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?, 'ACTIVE', 1, ?, ?, ?)
            """,
            (
                PRINCIPAL,
                KEY_ID,
                "test-secret-ref://host/tool/integration",
                NOW - 1_000,
                NOW + 600_000,
                NOW,
            ),
        )


def _signed_http_request(raw_body: bytes) -> object:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="proof.host.tool.integration",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 300_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=PRINCIPAL,
            kind=PrincipalKind.SERVICE,
            tenant_id="integration",
            granted_scopes=("host:write",),
        ),
        credential_proof=metadata,
        audience="host",
        request_id="request.host.tool.integration",
        requested_scopes=("host:write",),
    )
    unsigned = build_surface_request(
        surface=IngressSurface.TOOL,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        method="POST",
        normalized_path="/tools/execute",
        idempotency_key="idem.host.tool.integration",
        nonce="nonce.host.tool.integration",
        raw_body=raw_body,
        received_at_epoch_ms=NOW,
        transport_key=f"principal:{PRINCIPAL}",
        correlation_id="correlation.host.tool.integration",
    )
    identity_header = base64.urlsafe_b64encode(
        identity.to_canonical_json().encode("utf-8")
    ).rstrip(b"=")
    headers = [
        (b"x-natsume-identity", identity_header),
        (
            b"x-natsume-proof",
            build_hmac_sha256_signature(unsigned, SECRET).encode("ascii"),
        ),
        (b"x-natsume-idempotency-key", b"idem.host.tool.integration"),
        (b"x-natsume-nonce", b"nonce.host.tool.integration"),
        (b"x-natsume-correlation-id", b"correlation.host.tool.integration"),
    ]
    return SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/tools/execute"),
        scope={"headers": headers},
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _runtime(container, *, ledger, registry, tokens, broker, catalog, owner):  # noqa: ANN001
    return build_governed_host_runtime(
        task_runtime_container=container,
        ledger=ledger,
        skill_registry=registry,
        token_service=tokens,
        capability_broker=dependencies.get_capability_broker(),
        model_attempt_service=ApplicationModelAttemptServicePort(),
        production_authority_verifier=verify_durable_production_authority,
        resource_manager=None,
        episodic_store=None,
        semantic_recaller=None,
        tool_broker=broker,
        tool_catalog=catalog,
        runtime_router=CanonicalRuntimeRouter(),
        owner_id=owner,
        allow_uncomposed_acceptance_effects=True,
        degradation_capability_port=LegacyModuleGlobalDegradationAdapter(),
    )


class _NoopContinuationWorker:
    def __init__(self, **_kwargs) -> None:  # noqa: ANN003
        pass

    async def start(self) -> None:
        return None

    def wake(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def join(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("review_decision", ("APPROVE", "REJECT"))
async def test_real_hmac_airlock_review_resume_and_restart_duplicate(
    tmp_path: Path,
    review_decision: str,
) -> None:
    db_path = tmp_path / "real-governed-tool.sqlite3"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = build_process_task_runtime_container(
        ledger=ledger,
        canonical_db_path=db_path,
    )
    _seed_tool_principal(db_path)
    catalog = ToolCatalog(
        [
            ToolMeta(
                id="read_status",
                name="read status",
                desc="read local status",
                schema_hash="sha256:" + "a" * 64,
                side_effect_level="READ",
                auth_level="NONE",
                domain_tags=("integration",),
                version="1",
            )
        ]
    )
    tokens = TokenService(
        approval_ttl_ms=600_000,
        confirm_ttl_ms=600_000,
        undo_ttl_ms=600_000,
    )
    broker = ToolBroker(catalog=catalog, token_service=tokens, ledger=ledger)
    handler_calls: list[dict] = []

    def handler(args, context):  # noqa: ANN001, ANN201
        handler_calls.append({"args": dict(args), "context": dict(context)})
        return {"status": "real-ok"}

    broker.register_handler("read_status", handler, replay_safe_local=True)
    registry = build_agent_runtime_skill_registry()
    scorecards = FederatedCapabilityScorecardProvider(
        skill_registry=registry,
        tool_catalog=catalog,
        clock=lambda: NOW,
    )
    engine = IntentDecisionEngine(
        ledger=ledger,
        token_service=tokens,
        registry=registry,
        capability_scorecard_provider=scorecards,
        capability_maturity_enabled=True,
    )
    ingress = build_process_command_ingress_container(
        canonical_db_path=db_path,
        mode=SecureIngressMode.ENFORCE,
        hmac_secrets={KEY_ID: SECRET},
    )
    runtime = _runtime(
        container,
        ledger=ledger,
        registry=registry,
        tokens=tokens,
        broker=broker,
        catalog=catalog,
        owner="real-tool-owner-1",
    )
    await runtime.activate()
    raw_body = json.dumps(
        {
            "tool_id": "read_status",
            "intent_id": "client-intent-real-tool",
            "session_id": "session-real-tool",
            "seq": 1,
            "args": {"query": "status"},
            "context": {"workspace": "acceptance"},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request = _signed_http_request(raw_body)
    body = SimpleNamespace(**json.loads(raw_body))
    application = GovernedToolHttpApplication(
        catalog=catalog,
        governed_runtime=runtime,
        intent_engine=engine,
        ingress_adapter=SecureIngressHttpAuthorityAdapter(
            evaluator=ingress.evaluator,
            surface=IngressSurface.TOOL,
        ),
        ingress_store=ingress.store,
        tool_binding_resolver=broker.resolve_execution_binding,
        governance_snapshot_builder=lambda **_kwargs: {
            "trust_level": 100,
            "energy_level": 100,
        },
        clock_ms=lambda: NOW,
    )

    with pytest.raises(GovernedToolExecutionError) as airlock:
        await application.execute(body=body, request=request, raw_body=raw_body)
    assert airlock.value.status_code == 409
    assert handler_calls == []
    command_record = container.command_repository.get_by_request(
        surface="tool",
        request_id="request.host.tool.integration",
    )
    assert command_record is not None
    open_reviews = container.review_store.list_open_reviews()
    assert len(open_reviews) == 1
    review = open_reviews[0]["review"]
    assert review["status"] == "PENDING"
    if review_decision == "APPROVE":
        approved = container.review_store.approve_review(
            review["review_id"],
            actor_id="operator.integration",
        )
        approved_review = approved["review"]["review"]
        resumed = container.review_store.resume_review(
            review["review_id"],
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=approved_review["subject_ref"],
            subject_version=approved_review["subject_version"],
            resume_phase=approved_review["resume_phase"],
            checkpoint_id=approved_review["checkpoint_id"],
        )
        assert resumed["review"]["review"]["status"] == "CONSUMED"
    else:
        rejected = container.review_store.reject_review(
            review["review_id"],
            actor_id="operator.integration",
        )
        assert rejected["review"]["status"] == "REJECTED"
    raw_claim = container.review_continuation_store.claim_pending_review_continuations(
        worker_id="real-tool-review-worker",
        lease_ms=60_000,
        limit=1,
    )[0]
    claim = container.review_continuation_store.validate_review_continuation_claim(
        raw_claim
    )
    group = GovernedRuntimeGroup(
        {"host": runtime},
        review_store=container.review_continuation_store,
        review_decision_store=container.review_store,
        command_repository=container.command_repository,
        claim_result_store=ingress.store,
        review_continuation_worker_factory=_NoopContinuationWorker,
        review_continuation_worker_id="real-tool-runtime-group",
    )
    await group.activate()
    result = await group.continue_review_continuation(claim)
    receipt = result["execution_receipt"]
    if review_decision == "APPROVE":
        assert receipt["status"] == "ACCEPTED", result
        assert receipt["verification_status"] == "PASS"
        assert len(handler_calls) == 1
        graph_id = command_record["command"].graph_id
        graph_revision = command_record["command"].graph_revision
        node_id = receipt["node_id"]
        attempt_id = receipt["attempt_id"]
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            node = conn.execute(
                """
                SELECT status FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                """,
                (graph_id, graph_revision, node_id),
            ).fetchone()
            attempt = conn.execute(
                "SELECT status FROM task_runtime_attempt WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            lease = conn.execute(
                "SELECT status FROM task_runtime_lease WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            budget = conn.execute(
                """
                SELECT status FROM task_runtime_budget_reservation
                WHERE graph_id = ? AND graph_revision = ?
                  AND node_id IS NULL AND attempt_id IS NULL
                """,
                (graph_id, graph_revision),
            ).fetchone()
            reservation = conn.execute(
                """
                SELECT status, receipt_ref
                FROM task_runtime_idempotency_reservation
                WHERE reservation_id = ? AND attempt_id = ?
                """,
                (receipt["reservation_id"], attempt_id),
            ).fetchone()
            artifact = conn.execute(
                """
                SELECT receipt_ref, attempt_id FROM task_runtime_node_artifact
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                """,
                (graph_id, graph_revision, node_id),
            ).fetchone()
            projection = conn.execute(
                """
                SELECT route_type, decision, command_id, request_id
                FROM task_runtime_worker_projection
                WHERE graph_id = ? AND graph_revision = ?
                  AND node_id = ? AND attempt_id = ?
                """,
                (graph_id, graph_revision, node_id, attempt_id),
            ).fetchone()
            event_types = {
                str(row["event_type"])
                for row in conn.execute(
                    """
                    SELECT event_type FROM task_runtime_outbox
                    WHERE aggregate_type = 'TASK_NODE'
                      AND aggregate_id = ?
                    """,
                    (f"{graph_id}@{graph_revision}/{node_id}",),
                ).fetchall()
            }
        assert node is not None and node["status"] == "ACCEPTED"
        assert attempt is not None and attempt["status"] == "SUCCEEDED"
        assert lease is not None and lease["status"] == "RELEASED"
        assert budget is not None and budget["status"] == "CONSUMED"
        assert reservation is not None
        assert reservation["status"] == "COMMITTED"
        assert reservation["receipt_ref"] == receipt["receipt_ref"]
        assert artifact is not None
        assert artifact["attempt_id"] == attempt_id
        assert artifact["receipt_ref"] == receipt["receipt_ref"]
        assert projection is not None
        assert dict(projection) == {
            "route_type": "tool",
            "decision": "APPROVE",
            "command_id": command_record["command"].command_id,
            "request_id": "request.host.tool.integration",
        }
        assert {"TASK_NODE_VERIFIED", "TASK_NODE_ACCEPTED"} <= event_types
        trace = ledger.trace_request("request.host.tool.integration")
        assert any(
            isinstance(entry, dict)
            and isinstance(entry.get("snapshot_refs"), dict)
            and isinstance(entry["snapshot_refs"].get("node_grant"), dict)
            and entry["snapshot_refs"]["node_grant"].get("node_id") == node_id
            and entry["snapshot_refs"]["node_grant"].get("route") == "tool"
            for entry in trace.get("governor_log", [])
        )
    else:
        assert receipt["status"] == "REVIEW_DENIED", result
        assert handler_calls == []
    await group.aclose()
    ingress.close()
    restarted_ingress = build_process_command_ingress_container(
        canonical_db_path=db_path,
        mode=SecureIngressMode.ENFORCE,
        hmac_secrets={KEY_ID: SECRET},
    )
    restarted_runtime = _runtime(
        container,
        ledger=ledger,
        registry=registry,
        tokens=tokens,
        broker=broker,
        catalog=catalog,
        owner="real-tool-owner-2",
    )
    await restarted_runtime.activate()
    restarted_application = GovernedToolHttpApplication(
        catalog=catalog,
        governed_runtime=restarted_runtime,
        intent_engine=engine,
        ingress_adapter=SecureIngressHttpAuthorityAdapter(
            evaluator=restarted_ingress.evaluator,
            surface=IngressSurface.TOOL,
        ),
        ingress_store=restarted_ingress.store,
        tool_binding_resolver=broker.resolve_execution_binding,
        governance_snapshot_builder=lambda **_kwargs: {},
        clock_ms=lambda: NOW + 2,
    )
    try:
        if review_decision == "APPROVE":
            replay = await restarted_application.execute(
                body=body,
                request=request,
                raw_body=raw_body,
            )
            assert replay["output"] == {"status": "real-ok"}
            assert len(handler_calls) == 1
        else:
            for _ in range(2):
                with pytest.raises(GovernedToolExecutionError) as denied:
                    await restarted_application.execute(
                        body=body,
                        request=request,
                        raw_body=raw_body,
                    )
                assert denied.value.status_code == 403
                assert denied.value.reason_code == "GOVERNED_TOOL_REVIEW_REJECTED"
            assert handler_calls == []
        container.command_repository.validate_ingress_claim_result(
            command_record["command"].command_id
        )
    finally:
        await restarted_runtime.aclose()
        restarted_ingress.close()
        await container.aclose()
        ledger.close()
