from __future__ import annotations

from types import SimpleNamespace
import time

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.domain_packs import production_runtime as production_executor
from host_brain.composition import process_services as dependencies
from host_brain.contracts.agent_runtime.budget_policy import BudgetExceededAction
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.app.governed_task_adapter import (
    GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE,
    GovernedHostAdapterError,
    build_governed_host_runtime,
)
from host_brain.composition.life import PROCESS_LIFE_PROVIDER
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
@pytest.mark.parametrize("action_arbiter_enabled", ["false", "true"])
async def test_real_sqlite_direct_skill_cutover_is_durable_and_replay_safe(
    tmp_path,
    monkeypatch,
    action_arbiter_enabled: str,
) -> None:
    monkeypatch.setenv("HOST_ACTION_ARBITER_ENABLED", action_arbiter_enabled)
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
        PROCESS_LIFE_PROVIDER.clear()
        with pytest.raises(GovernedHostAdapterError) as unavailable:
            build_governed_host_runtime(
                task_runtime_container=infrastructure,
                ledger=ledger,
                skill_registry=registry,
                token_service=token_service,
                capability_broker=dependencies.get_capability_broker(),
                model_attempt_service=ApplicationModelAttemptServicePort(),
                production_authority_verifier=verify_durable_production_authority,
                runtime_router=CanonicalRuntimeRouter(),
                owner_id="integration-owner-fail-closed",
            )
        assert unavailable.value.reason_code == GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE
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
            # This focused cutover fixture predates the Life composition.  The
            # production ASGI composition is tested separately and must not use
            # this explicit test-only escape hatch.
            allow_uncomposed_acceptance_effects=True,
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
                assert calls == ["production-worker-finished"], event[
                    "execution_receipt"
                ].get("projection", {}).get("reason_code")
            first.append(event)
        durable_receipt = first[-1]["execution_receipt"]
        command_id = durable_receipt["command_id"]
        first_admitted = HostCommandRepository(db_path).get(command_id)
        assert first_admitted is not None
        first_order = first_admitted["work_order"]
        assert isinstance(first_order, WorkOrderV3)
        assert first_order.budget_policy is not None
        assert first_order.budget_policy.on_exceeded is BudgetExceededAction.BLOCK
        assert all(value > 0 for value in first_order.budget_policy.limits.to_dict().values())
        first_budget = first_order.budget_policy.to_dict()
        first_lineage = {
            "root_work_order_id": first_order.root_work_order_id,
            "task_graph_id": first_order.task_graph_id,
            "task_node_id": first_order.task_node_id,
            "graph_revision": first_order.graph_revision,
            "attempt": first_order.attempt,
            "capability_requirements": first_order.capability_requirements,
            "approval_ref": first_order.approval_ref,
            "review_ref": first_order.review_ref,
            "business_idempotency_scope": first_order.business_idempotency_scope,
            "operation_fingerprint": first_order.operation_fingerprint,
        }
        first_input_pack = first_order.input_pack
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
                session_id=session_id,
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
                    "command_target_id": "host",
                    "work_order_id": durable_receipt["work_order_id"],
                    "graph_id": durable_receipt["graph_id"],
                    "graph_revision": durable_receipt["graph_revision"],
                    "node_id": durable_receipt["node_id"],
                    "lease_id": "lease::governed::forged",
                    "fencing_token": 1,
                    "reservation_id": durable_receipt["reservation_id"],
                    "node_grant_ref": "grant://node/forged",
                    "parent_grant_ref": "grant://parent/forged",
                    "lease_owner_id": "integration-owner",
                    "business_scope": first_order.business_idempotency_scope,
                    "operation_fingerprint": first_order.operation_fingerprint,
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

        admitted = HostCommandRepository(db_path).get(command_id)
        assert admitted is not None
        replayed_order = admitted["work_order"]
        assert isinstance(replayed_order, WorkOrderV3)
        assert replayed_order.budget_policy is not None
        assert replayed_order.budget_policy.to_dict() == first_budget
        assert replayed_order.input_pack == first_input_pack
        assert {
            "root_work_order_id": replayed_order.root_work_order_id,
            "task_graph_id": replayed_order.task_graph_id,
            "task_node_id": replayed_order.task_node_id,
            "graph_revision": replayed_order.graph_revision,
            "attempt": replayed_order.attempt,
            "capability_requirements": replayed_order.capability_requirements,
            "approval_ref": replayed_order.approval_ref,
            "review_ref": replayed_order.review_ref,
            "business_idempotency_scope": replayed_order.business_idempotency_scope,
            "operation_fingerprint": replayed_order.operation_fingerprint,
        } == first_lineage
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
