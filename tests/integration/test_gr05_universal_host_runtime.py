from __future__ import annotations

from types import SimpleNamespace
import sqlite3
import time

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import build_governed_host_runtime
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
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry


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


async def _execute(runtime, *, intent: dict, governor: dict) -> list[dict]:  # noqa: ANN001
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
        )
    ]


@pytest.mark.asyncio
async def test_chat_delegate_and_plan_share_real_governed_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    chat_calls: list[dict] = []
    delegate_calls: list[dict] = []
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

    async def frozen_delegate(**kwargs):  # noqa: ANN003
        delegate_calls.append(dict(kwargs))
        return SimpleNamespace(
            route_type="delegate",
            target_id=kwargs["target_id"],
            success=True,
            result_text=f"delegate:{kwargs['task_text']}",
            error_text="",
            payload={"audit_refs": {"fake_delegate": True}},
        )

    monkeypatch.setattr(
        "host_brain.app.route_execution_ports.stream_governed_chat_with_context",
        frozen_chat,
    )
    monkeypatch.setattr(
        "host_brain.app.route_execution_ports.dispatch_governed_delegate",
        frozen_delegate,
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
            target_id="intent_agent",
            task="delegate through governed runtime",
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
                    "target_id": "intent_agent",
                    "task_description": "research the curated result",
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

        results: dict[str, list[dict]] = {}
        for value in (chat, delegate, plan):
            governor = engine.evaluate_intent(
                raw_intent=value,
                request_id=value["request_id"],
                current_ms=value["ts_ms"],
                governance_snapshot={"trust_level": 100, "energy_level": 100},
            )
            assert governor["decision"] == "APPROVE", governor
            assert runtime.supports(value) is True
            results[value["route"]] = await _execute(
                runtime,
                intent=value,
                governor=governor,
            )

        assert results["chat"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert results["delegate"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert results["plan"][-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert len(chat_calls) == 2
        assert len(delegate_calls) == 2
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
                SELECT graph_id, graph_revision
                FROM host_command_journal
                WHERE request_id = ?
                """,
                (plan["request_id"],),
            ).fetchone()
        assert sorted(row[0] for row in routes) == ["chat", "delegate", "plan"]
        assert attempts == 5
        # Chat/delegate already froze worker projections; the plan's skill
        # node now does so as well, giving every accepted route the same B5
        # result-authority journal.
        assert projections == 5
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
        if runtime is not None:
            await runtime.aclose()
        if container is not None:
            await container.aclose()
        if life is not None:
            PROCESS_LIFE_PROVIDER.clear(life)
        ledger.close()
