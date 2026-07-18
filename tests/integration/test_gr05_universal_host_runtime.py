from __future__ import annotations

import hashlib
from types import SimpleNamespace
import sqlite3
import time

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import (
    build_governed_host_runtime as _build_governed_host_runtime,
)
from host_brain.runtime.degradation_authority import LegacyModuleGlobalDegradationAdapter


def build_governed_host_runtime(**kwargs):  # type: ignore[no-untyped-def]
    kwargs.setdefault(
        "degradation_capability_port",
        LegacyModuleGlobalDegradationAdapter(),
    )
    return _build_governed_host_runtime(**kwargs)
from host_brain.composition import process_services as dependencies
from host_brain.composition.life import (
    PROCESS_LIFE_PROVIDER,
    build_process_life_container,
)
from host_brain.app.model_provider_composition import ApplicationModelAttemptServicePort
from host_brain.app.production_authority import verify_durable_production_authority
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.task_runtime import build_process_task_runtime_container
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry
from natsume_contracts.task import HostCommandSurface


def _intent(
    *,
    request_id: str,
    session_id: str,
    seq: int,
    route: str,
    target_id: str,
    task: str,
    planned_steps: list[dict] | None = None,
) -> dict:
    value = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-{request_id}",
        "request_id": request_id,
        "ts_ms": int(time.time() * 1000),
        "session_id": session_id,
        "seq": seq,
        "nonce": f"nonce-{request_id}",
        "type": "query",
        "action": "narrative_only" if route == "chat" else "exec_intent",
        "target": "host",
        "route": route,
        "target_id": target_id,
        "task": task,
        "person_id": "person-gr05-universal",
        "principal_id": "person-gr05-universal",
        "args": {"text": task},
    }
    if planned_steps is not None:
        value["planned_steps"] = planned_steps
        value["args"].update(
            {
                "planned_steps": planned_steps,
                "route": "plan",
                "target_id": target_id,
            }
        )
    return value


async def _execute(
    runtime,
    *,
    intent: dict,
    governor: dict,
    request_body: object | None = None,
    command_authority: dict | None = None,
) -> list[dict]:  # noqa: ANN001
    if request_body is None:
        request_body = SimpleNamespace()
        if intent["route"] == "chat":
            request_body = SimpleNamespace(
                input_source="GATEWAY",
                reply_channel="qq",
                reply_target="qq_dm_gr05_universal",
            )
    return [
        event
        async for event in runtime.execute_stream(
            request_body=request_body,
            request_id=intent["request_id"],
            session_id=intent["session_id"],
            ts_ms=intent["ts_ms"],
            intent_draft=intent,
            governor_result=governor,
            person_id=intent["person_id"],
            sanitized_message=intent["task"],
            emotion_context={"emotion_tag": "NEUTRAL"},
            render_target_lang="zh",
            command_authority=command_authority,
        )
    ]


def _gateway_ingress_authority(
    db_path,
    *,
    request_id: str,
    suffix: str,
) -> tuple[SQLiteSecureIngressStore, dict[str, str]]:
    body_sha256 = hashlib.sha256(
        f"gr05-universal-gateway:{suffix}".encode("utf-8")
    ).hexdigest()
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    claim = store.try_claim(
        principal_id="service.edge.gateway",
        surface="gateway.webhook",
        action="host:input",
        required_scopes=("host:input",),
        idempotency_key=f"gr05-universal:{suffix}:idempotency",
        nonce=f"gr05-universal:{suffix}:nonce",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=1_800_000_000_000,
        expires_at_ms=1_800_000_060_000,
    )
    assert claim.status is ClaimStatus.NEW
    assert claim.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"gr05-universal:{suffix}:correlation",
            surface="gateway.webhook",
            action="host:input",
            method="POST",
            normalized_path="/v1/host/input",
            principal_id="service.edge.gateway",
            tenant_id="gr05-universal",
            key_id="gr05-universal-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + "1" * 64,
            requested_scopes=("host:input",),
            required_scopes=("host:input",),
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
            ts_ms=1_800_000_000_000,
        ),
    )
    return store, {
        "principal_id": "service.edge.gateway",
        "claim_ref": str(claim.claim_ref),
        "audit_ref": str(audit_ref),
        "body_sha256": body_sha256,
    }


@pytest.mark.asyncio
async def test_chat_delegate_and_plan_share_real_governed_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    chat_calls: list[dict] = []
    skill_calls: list[str] = []
    delivered_replies: list[dict] = []

    async def frozen_chat(**kwargs):  # noqa: ANN003
        chat_calls.append(dict(kwargs))
        yield {"phase": "DELTA", "done": False, "delta": "buffered"}
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": f"chat:{kwargs['sanitized_message']}",
            "route_type": "chat",
            "target_id": "surface_reply",
        }

    monkeypatch.setattr(
        "host_brain.app.route_execution_ports.stream_governed_chat_with_context",
        frozen_chat,
    )

    def frozen_skill_worker(_order, *, store, run_dir, **_kwargs):  # noqa: ANN001, ARG001
        skill_calls.append("agent_memory_curator")
        return {
            "status": "EXECUTED",
            "summary": "frozen skill completed",
            "result_text": "skill:curated plan input",
            "metadata": {"worker_terminal": True},
        }

    monkeypatch.setattr(
        "host_brain.domain_packs.production_runtime._execute_worker",
        frozen_skill_worker,
    )

    async def frozen_gateway_delivery(**kwargs):  # noqa: ANN003
        delivered_replies.append(dict(kwargs))
        return {
            "success": True,
            "status": "SENT",
            "latency_ms": 1,
            "reply_text_len": len(str(kwargs.get("narrative") or "")),
        }

    monkeypatch.setattr(
        "host_brain.input_runtime.gateway_delivery._deliver_gateway_reply",
        frozen_gateway_delivery,
    )

    db_path = tmp_path / "universal-host-runtime.sqlite3"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = None
    runtime = None
    life = None
    ingress_store = None
    mismatch_ingress_store = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
        )
        PROCESS_LIFE_PROVIDER.clear()
        life = build_process_life_container(
            ledger=ledger,
            adopted_at_ms=int(time.time() * 1000),
        )
        PROCESS_LIFE_PROVIDER.install(life)
        monkeypatch.setattr(dependencies.get_process_service_container(), "ledger", ledger)
        monkeypatch.setattr(
            dependencies.get_process_service_container(),
            "capability_runtime_composition",
            None,
        )
        registry = build_agent_runtime_skill_registry()
        token_service = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        engine = IntentDecisionEngine(
            ledger=ledger,
            token_service=token_service,
            registry=registry,
        )
        runtime = build_governed_host_runtime(
            task_runtime_container=container,
            ledger=ledger,
            skill_registry=registry,
            token_service=token_service,
            capability_broker=dependencies.get_capability_broker(),
            model_attempt_service=ApplicationModelAttemptServicePort(),
            production_authority_verifier=verify_durable_production_authority,
            resource_manager=None,
            episodic_store=None,
            semantic_recaller=None,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="gr05-universal-owner",
        )
        await runtime.activate()

        chat = _intent(
            request_id="request-gr05-chat",
            session_id="session-gr05-universal",
            seq=1,
            route="chat",
            target_id="surface_reply",
            task="reply through governed chat",
        )
        delegate = _intent(
            request_id="request-gr05-delegate",
            session_id="session-gr05-universal",
            seq=2,
            route="delegate",
            target_id="pentest_agent",
            task="prepare a read-only threat model for example.test",
        )
        plan = _intent(
            request_id="request-gr05-plan",
            session_id="session-gr05-universal",
            seq=3,
            route="plan",
            target_id="planning_search",
            task="delegate then synthesize through governed plan",
            planned_steps=[
                {
                    "candidate_id": "curate",
                    "step_index": 1,
                    "route": "skill",
                    "target_id": "agent_memory_curator",
                    "task_description": "curate the approved plan input",
                    "dependencies": [],
                    "allow_parallel": True,
                    "args": {
                        "task": "curate the approved plan input",
                        "artifacts_root": str(tmp_path / "plan-skill-runs"),
                    },
                },
                {
                    "candidate_id": "research",
                    "step_index": 2,
                    "route": "delegate",
                    "target_id": "pentest_agent",
                    "task_description": "prepare a read-only security plan for example.test",
                    "dependencies": ["curate"],
                },
                {
                    "candidate_id": "synthesize",
                    "step_index": 3,
                    "route": "chat",
                    "target_id": "surface_reply",
                    "task_description": "synthesize the approved result",
                    "dependencies": ["research"],
                },
            ],
        )
        ingress_store, plan_authority = _gateway_ingress_authority(
            db_path,
            request_id=plan["request_id"],
            suffix="plan",
        )

        results: dict[str, list[dict]] = {}
        governors: dict[str, dict] = {}
        for value in (chat, delegate, plan):
            # The real Host Input pipeline freezes the authenticated Secure
            # Ingress principal into the intent *before* Governor admission.
            # Keep this direct-runtime test on that same side of the boundary:
            # person_id remains the conversational projection, while the
            # durable Governor grant is bound to the command principal.
            if value["route"] == "plan":
                value["principal_id"] = plan_authority["principal_id"]
            governor = engine.evaluate_intent(
                raw_intent=value,
                request_id=value["request_id"],
                current_ms=value["ts_ms"],
                governance_snapshot={"trust_level": 100, "energy_level": 100},
            )
            assert governor["decision"] == "APPROVE", governor
            assert runtime.supports(value) is True
            governors[value["route"]] = governor
            execution_kwargs: dict[str, object] = {}
            if value["route"] == "plan":
                execution_kwargs = {
                    "request_body": SimpleNamespace(input_source="GATEWAY"),
                    "command_authority": plan_authority,
                }
            results[value["route"]] = await _execute(
                runtime,
                intent=value,
                governor=governor,
                **execution_kwargs,
            )

        plan_replay = await _execute(
            runtime,
            intent=plan,
            governor=governors["plan"],
            request_body=SimpleNamespace(input_source="GATEWAY"),
            command_authority=plan_authority,
        )

        # A direct caller that skips Cortex keeps the conversational principal
        # in its Governor grant while presenting an authenticated service
        # principal to planning admission. The planning preflight must reject
        # that drift before HostCommand/TaskGraph persistence.
        mismatched_plan = _intent(
            request_id="request-gr05-plan-principal-drift",
            session_id="session-gr05-universal",
            seq=4,
            route="plan",
            target_id="planning_search",
            task="reject mismatched planning principal before admission",
            planned_steps=[
                {
                    "candidate_id": "curate",
                    "step_index": 1,
                    "route": "skill",
                    "target_id": "agent_memory_curator",
                    "task_description": "must not be admitted",
                    "dependencies": [],
                    "allow_parallel": True,
                    "args": {
                        "task": "must not be admitted",
                        "artifacts_root": str(tmp_path / "mismatch-plan-skill-runs"),
                    },
                }
            ],
        )
        mismatch_ingress_store, mismatch_authority = _gateway_ingress_authority(
            db_path,
            request_id=mismatched_plan["request_id"],
            suffix="plan-principal-drift",
        )
        mismatch_governor = engine.evaluate_intent(
            raw_intent=mismatched_plan,
            request_id=mismatched_plan["request_id"],
            current_ms=mismatched_plan["ts_ms"],
            governance_snapshot={"trust_level": 100, "energy_level": 100},
        )
        assert mismatch_governor["decision"] == "APPROVE", mismatch_governor
        with sqlite3.connect(db_path) as conn:
            command_count_before_drift = conn.execute(
                "SELECT COUNT(*) FROM host_command_journal"
            ).fetchone()[0]
        with pytest.raises(Exception, match="NODE_GRANT_PARENT_INVALID"):
            await _execute(
                runtime,
                intent=mismatched_plan,
                governor=mismatch_governor,
                request_body=SimpleNamespace(input_source="GATEWAY"),
                command_authority=mismatch_authority,
            )
        with sqlite3.connect(db_path) as conn:
            command_count_after_drift = conn.execute(
                "SELECT COUNT(*) FROM host_command_journal"
            ).fetchone()[0]
        assert command_count_after_drift == command_count_before_drift

        assert results["chat"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert results["delegate"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert results["plan"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert plan_replay[-1]["execution_receipt"]["command_id"] == (
            results["plan"][-1]["execution_receipt"]["command_id"]
        )
        assert len(chat_calls) == 2
        assert skill_calls == ["agent_memory_curator"]
        assert len(delivered_replies) == 1
        assert delivered_replies[0]["request_id"] == chat["request_id"]
        assert delivered_replies[0]["payload"]["platform"] == "qq"
        assert (
            delivered_replies[0]["payload"]["conversation_id"]
            == "qq_dm_gr05_universal"
        )
        assert all(call["governor_result"] == {} for call in chat_calls)
        assert all(call["request_body"].suppress_edge_tts is True for call in chat_calls)
        assert all(call["request_body"].suppress_edge_vts is True for call in chat_calls)

        with sqlite3.connect(db_path) as conn:
            routes = conn.execute(
                "SELECT route_type FROM host_command_journal ORDER BY created_at_ms, command_id"
            ).fetchall()
            attempts = conn.execute(
                "SELECT COUNT(*) FROM task_runtime_attempt"
            ).fetchone()[0]
            projections = conn.execute(
                "SELECT COUNT(*) FROM task_runtime_worker_projection"
            ).fetchone()[0]
            legacy_delegate_logs = conn.execute(
                """
                SELECT COUNT(*) FROM skill_exec_log
                WHERE request_id IN (?, ?)
                  AND skill_name LIKE 'sub_agent:%'
                """,
                (delegate["request_id"], plan["request_id"]),
            ).fetchone()[0]
            acceptance_effects = conn.execute(
                """
                SELECT effect_type, commit_scope, status, COUNT(*)
                FROM task_runtime_acceptance_effect_intent
                GROUP BY effect_type, commit_scope, status
                ORDER BY effect_type
                """
            ).fetchall()
            plan_graph = conn.execute(
                """
                SELECT graph_id, graph_revision, surface, principal_id,
                       ingress_claim_ref, ingress_audit_ref, input_sha256
                FROM host_command_journal
                WHERE request_id = ?
                """,
                (plan["request_id"],),
            ).fetchone()
        assert sorted(row[0] for row in routes) == ["chat", "delegate", "plan"]
        assert plan_graph[2] == HostCommandSurface.GATEWAY.value
        assert plan_graph[3] == plan_authority["principal_id"]
        assert plan_graph[4] == plan_authority["claim_ref"]
        assert plan_graph[5] == plan_authority["audit_ref"]
        assert plan_graph[6] == "sha256:" + plan_authority["body_sha256"]
        assert attempts == 5
        # Chat/delegate already froze worker projections; the plan's skill
        # node now does so as well, giving every accepted route the same B5
        # result-authority journal.
        assert projections == 5
        assert legacy_delegate_logs == 0
        assert acceptance_effects == [
            ("conversation.user_turn", "GRAPH", "COMMITTED", 3),
            ("life.foreground_completed", "GRAPH", "COMMITTED", 3),
            ("surface.reply_delivery", "GRAPH", "COMMITTED", 1),
        ]
        assert results["chat"][-1]["audit_refs"]["acceptance_effects"] == {
            "count": 3,
            "committed": 3,
            "failed": 0,
            "pending": 0,
        }
        assert all(
            results[route][-1]["audit_refs"]["acceptance_effects"]
            == {"count": 2, "committed": 2, "failed": 0, "pending": 0}
            for route in ("delegate", "plan")
        )
        graph = container.task_store.get_graph(plan_graph[0], plan_graph[1])
        assert graph["status"] == TaskGraphStatus.COMPLETED.value
        assert [node["status"] for node in graph["nodes"]] == [
            "ACCEPTED",
            "ACCEPTED",
            "ACCEPTED",
        ]
    finally:
        if mismatch_ingress_store is not None:
            mismatch_ingress_store.close()
        if ingress_store is not None:
            ingress_store.close()
        if runtime is not None:
            await runtime.aclose()
        if container is not None:
            await container.aclose()
        if life is not None:
            PROCESS_LIFE_PROVIDER.clear(life)
        ledger.close()
