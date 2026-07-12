from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.task_store import DurableTaskStore
from host_brain.app.governed_task_adapter import build_governed_host_runtime
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.runtime_events import RuntimeEventStore, RuntimeOutboxPublisher
from host_brain.persistence import HostCommandRepository
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry


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

    ledger = SQLiteLedger(str(db_path))
    runtime = None
    try:
        request_id = "request-aw0203-cutover"
        session_id = "session-aw0203-cutover"
        ts_ms = 1_800_000_000_000
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": "intent-aw0203-cutover",
            "session_id": session_id,
            "seq": 1,
            "nonce": "nonce-aw0203-cutover",
            "route": "skill",
            "target_id": "agent_memory_curator",
            "task": "curate the admitted memory candidates",
            "args": {
                "task": "curate the admitted memory candidates",
                "artifacts_root": str(tmp_path / "production-runs"),
                "start_background": True,
            },
        }
        ledger.log_intent(
            {
                "request_id": request_id,
                "intent_id": intent["id"],
                "session_id": session_id,
                "seq": 1,
                "ts_ms": ts_ms,
                "nonce": intent["nonce"],
                "parse_stage": "STRICT",
                "normalized_json": intent,
                "proposer_node_id": "integration",
                "model_snapshot": "integration",
                "prompt_profile_id": "integration",
                "tool_visibility_hash": "",
                "proposed_intent_draft_json": intent,
                "lint_result": "",
            }
        )
        governor = {
            "request_id": request_id,
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "reason_code": None,
            "rule_trace": [],
            "tokens": {},
            "snapshot_refs": {},
            "stage": "S_INTENT_OK",
            "ts_ms": ts_ms,
        }
        ledger.log_governor(governor)

        calls: list[str] = []
        registry = build_agent_runtime_skill_registry()

        def governed_worker(_order, *, store, run_dir):  # noqa: ANN001, ARG001
            calls.append("production-worker-finished")
            return {
                "status": "EXECUTED",
                "summary": "Governed production worker finished.",
                "result_text": "curated durable memory candidates",
                "metadata": {"worker_terminal": True},
            }

        monkeypatch.setattr(
            "host_brain.agent_runtime.production_executor._execute_worker",
            governed_worker,
        )
        monkeypatch.setattr(
            "host_brain.agent_runtime.production_executor._start_background_job",
            lambda **_kwargs: pytest.fail("governed production must not start a daemon"),
        )
        monkeypatch.setattr(
            "host_brain.agent_runtime.production_executor._persist_production_job_memory",
            lambda **_kwargs: None,
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
            token_service=object(),
            runtime_router=CanonicalRuntimeRouter(
                mode="canonical",
                canary_percent=100,
                canonical_owner="integration-owner",
                confirmed_owner="integration-owner",
            ),
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
                assert calls == ["production-worker-finished"]
            first.append(event)
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
        ledger.close()
