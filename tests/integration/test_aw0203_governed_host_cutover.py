from __future__ import annotations

from types import SimpleNamespace
import time

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.domain_packs import production_runtime as production_executor
from host_brain.composition import process_services as dependencies
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.app.governed_task_adapter import build_governed_host_runtime
from host_brain.app.model_provider_composition import ApplicationModelAttemptServicePort
from host_brain.app.production_authority import verify_durable_production_authority
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.integrations.runtime_events import RuntimeEventStore, RuntimeOutboxPublisher
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry
from host_brain.skills.executor_registry import (
    _verify_durable_governed_authority,
    bind_validated_skill_authority,
)


@pytest.mark.asyncio
async def test_real_sqlite_direct_skill_cutover_is_durable_and_replay_safe(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_ACTION_ARBITER_ENABLED", "false")
    db_path = tmp_path / "governed-host.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw0203-real-cutover",
    )
    assert migrated.success is True, migrated.as_dict()

    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    monkeypatch.setattr(dependencies.get_process_service_container(), "ledger", ledger)
    monkeypatch.setattr(dependencies.get_process_service_container(), "capability_runtime_composition", None)
    runtime = None
    try:
        request_id = "request-aw0203-cutover"
        session_id = "session-aw0203-cutover"
        ts_ms = int(time.time() * 1000)
        registry = build_agent_runtime_skill_registry()
        token_service = TokenService(
            approval_ttl_ms=120_000,
            confirm_ttl_ms=120_000,
            undo_ttl_ms=120_000,
        )
        intent_engine = IntentDecisionEngine(
            ledger=ledger,
            token_service=token_service,
            registry=registry,
        )
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": "intent-aw0203-cutover",
            "ts_ms": ts_ms,
            "session_id": session_id,
            "seq": 1,
            "nonce": "nonce-aw0203-cutover",
            "type": "query",
            "action": "agent_memory_curator",
            "target": "host",
            "route": "skill",
            "target_id": "agent_memory_curator",
            "task": "curate the admitted memory candidates",
            "person_id": "person-aw0203",
            "principal_id": "person-aw0203",
            "args": {
                "task": "curate the admitted memory candidates",
                "artifacts_root": str(tmp_path / "production-runs"),
            },
        }
        governor = intent_engine.evaluate_intent(
            raw_intent=intent,
            request_id=request_id,
            current_ms=ts_ms,
            governance_snapshot={"trust_level": 100, "energy_level": 100},
        )
        assert governor["decision"] == "APPROVE", governor

        calls: list[str] = []

        def governed_worker(_order, *, store, run_dir, **_kwargs):  # noqa: ANN001, ARG001
            calls.append("production-worker-finished")
            return {
                "status": "EXECUTED",
                "summary": "Governed production worker finished.",
                "result_text": "curated durable memory candidates",
                "metadata": {"worker_terminal": True},
            }

        monkeypatch.setattr(
            "host_brain.domain_packs.production_runtime._execute_worker",
            governed_worker,
        )
        task_store = DurableTaskStore(db_path)
        event_store = RuntimeEventStore(db_path)
        infrastructure = SimpleNamespace(
            task_store=task_store,
            command_repository=HostCommandRepository(db_path),
            outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
        )
        runtime = build_governed_host_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            skill_registry=registry,
            token_service=token_service,
            capability_broker=dependencies.get_capability_broker(),
            model_attempt_service=ApplicationModelAttemptServicePort(),
            production_authority_verifier=verify_durable_production_authority,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="integration-owner",
        )
        await runtime.activate()
        request = {
            "request_body": {},
            "request_id": request_id,
            "session_id": session_id,
            "ts_ms": ts_ms,
            "intent_draft": intent,
            "governor_result": governor,
            "person_id": "person-aw0203",
            "sanitized_message": "curate the admitted memory candidates",
            "emotion_context": {},
            "render_target_lang": "zh",
        }

        first = []
        async for event in runtime.execute_stream(**request):
            if event.get("execution_receipt"):
                assert calls == ["production-worker-finished"], [*first, event]
            first.append(event)
        durable_receipt = first[-1]["execution_receipt"]
        for forged_command_id, forged_attempt_id in (
            ("host-command://chat/forged", durable_receipt["attempt_id"]),
            (durable_receipt["command_id"], "attempt::governed::forged"),
        ):
            forged_authority = bind_validated_skill_authority(
                payload={
                    "tool": "codex_production",
                    "work_order": {
                        "work_order_id": durable_receipt["work_order_id"],
                    },
                },
                request_id=request_id,
                skill_id="agent_memory_curator",
                principal_id="person-aw0203",
                target_id="agent_memory_curator",
                governed_execution_context={
                    "execution_mode": "governed_task_runtime",
                    "command_id": forged_command_id,
                    "attempt_id": forged_attempt_id,
                    "request_id": request_id,
                    "principal_id": "person-aw0203",
                    "target_id": "agent_memory_curator",
                    "work_order_id": durable_receipt["work_order_id"],
                    "graph_id": durable_receipt["graph_id"],
                    "graph_revision": durable_receipt["graph_revision"],
                    "node_id": durable_receipt["node_id"],
                    "lease_id": "lease::governed::forged",
                    "fencing_token": 1,
                },
                durable_ledger=ledger,
                validated_by="integration.forged_authority_fixture",
            )
            assert _verify_durable_governed_authority(forged_authority) is not None
        replay_governor = {**governor, "ts_ms": ts_ms + 5_000}
        ledger.log_governor(replay_governor)
        replay_request = {
            **request,
            "ts_ms": ts_ms + 5_000,
            "governor_result": replay_governor,
        }
        replay = [event async for event in runtime.execute_stream(**replay_request)]

        assert first[-1]["decision"] == "APPROVE"
        assert first[-1]["narrative"] == "curated durable memory candidates"
        assert first[-1]["execution_receipt"]["executor"] == "canonical"
        assert first[-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert replay[-1]["replayed"] is True
        assert replay[-1]["narrative"] == first[-1]["narrative"]
        assert calls == ["production-worker-finished"]

        command_id = first[-1]["execution_receipt"]["command_id"]
        admitted = HostCommandRepository(db_path).get(command_id)
        assert admitted is not None
        assert admitted["graph"].status.value == "CREATED"
        live_graph = task_store.get_graph(
            admitted["graph_id"],
            admitted["graph_revision"],
        )
        assert live_graph["status"] == "COMPLETED"
        assert task_store.list_pending_outbox(limit=100) == []
        event_types = {
            row["event_type"]
            for row in event_store.list_events(limit=100)
        }
        assert {"HOST_COMMAND_ADMITTED", "TASK_GRAPH_COMPLETED"} <= event_types
    finally:
        if runtime is not None:
            await runtime.aclose()
        dependencies.reset_capability_runtime_composition()
        ledger.close()
