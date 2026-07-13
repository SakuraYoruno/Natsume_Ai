from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.command_ingress import (
    build_process_command_ingress_container,
)
from host_brain.composition.scheduler import (
    SchedulerClockIdentityConfig,
    build_process_scheduler_container,
)
from host_brain.integrations.runtime_events import (
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
)
from host_brain.secure_ingress import (
    IngressSurface,
    SECURE_INGRESS_SURFACE_POLICIES,
)


NOW = 1_800_000_000_000
PRINCIPAL_ID = "service.scheduler.clock.integration"
TENANT_ID = "tenant.scheduler.integration"
KEY_ID = "key.scheduler.clock.integration"
SECRET = "scheduler-clock-integration-secret"


def _register_clock_authority(db_path: Path) -> None:
    policy = SECURE_INGRESS_SURFACE_POLICIES[IngressSurface.SCHEDULER_ADMIN]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms,
                updated_at_ms
            ) VALUES (?, 'service', ?, 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                TENANT_ID,
                json.dumps(list(policy.required_scopes)),
                json.dumps([policy.audience]),
                json.dumps([policy.action]),
                NOW - 1_000,
                NOW + 60_000,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm, secret_ref,
                status, version, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?,
                      'ACTIVE', 1, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                KEY_ID,
                "test-secret-ref://scheduler-clock-integration",
                NOW - 1_000,
                NOW + 60_000,
                NOW,
            ),
        )


class _IntentEngine:
    def __init__(self, ledger: SQLiteLedger) -> None:
        self.ledger = ledger
        self.calls = 0

    def evaluate_intent(self, **kwargs):
        self.calls += 1
        intent = kwargs["raw_intent"]
        decision = {
            "request_id": kwargs["request_id"],
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "reason_code": None,
            "tokens": {},
            "ts_ms": intent["ts_ms"],
        }
        self.ledger.log_governor(decision)
        return decision


@pytest.mark.asyncio
async def test_clock_to_task_runtime_to_durable_result_is_one_real_chain(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "governed-scheduler.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw02-scheduler-production-cutover",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    _register_clock_authority(db_path)

    ledger = SQLiteLedger(str(db_path))
    command_ingress = build_process_command_ingress_container(
        canonical_db_path=db_path,
        mode="ENFORCE",
        hmac_secrets={KEY_ID: SECRET},
    )
    task_store = DurableTaskStore(db_path)
    event_store = RuntimeEventStore(db_path)
    definitions = SchedulerDefinitionRepository(db_path, clock=lambda: NOW)
    results = SchedulerExecutionResultRepository(db_path, clock=lambda: NOW)
    infrastructure = SimpleNamespace(
        task_store=task_store,
        command_repository=HostCommandRepository(db_path),
        outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
        scheduler_definition_repository=definitions,
        scheduler_result_repository=results,
    )
    worker_calls: list[str] = []

    async def worker(**kwargs):
        worker_calls.append(kwargs["occurrence"].occurrence_id)
        return {
            "status": "SUCCEEDED",
            "summary": "governed scheduler production leaf completed",
        }

    intent_engine = _IntentEngine(ledger)
    scheduler = build_process_scheduler_container(
        task_runtime_container=infrastructure,
        ledger=ledger,
        evaluator=command_ingress.evaluator,
        claim_result_store=command_ingress.store,
        intent_engine=intent_engine,
        runtime_router=CanonicalRuntimeRouter(),
        owner_id="scheduler-integration-owner",
        current_mode=lambda: "normal",
        default_handler=worker,
        seed_definitions=(
            {
                "task_id": "scheduler.integration.task",
                "next_scheduled_at_ms": NOW,
                "definition": {
                    "task_id": "scheduler.integration.task",
                    "trigger_mode": "interval",
                    "trigger_config": {"seconds": 60},
                    "intent_template": {"skill_name": "integration", "args": {}},
                    "priority": "HIGH",
                    "enabled": True,
                    "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
                    "resource_profile": {},
                    "worker_key": "scheduler_production",
                },
            },
        ),
        clock_enabled=True,
        clock_identity=SchedulerClockIdentityConfig(
            principal_id=PRINCIPAL_ID,
            tenant_id=TENANT_ID,
            key_id=KEY_ID,
            secret=SECRET,
        ),
        now_ms=lambda: NOW,
    )
    try:
        await scheduler.runtime.activate()
        tick = await scheduler.clock.run_once()

        assert tick == {
            "received_at_ms": NOW,
            "recovered": 0,
            "admitted": 1,
            "executed": 1,
            "deferred": 0,
            "failed": 0,
            "failures": [],
        }
        assert len(worker_calls) == 1
        assert intent_engine.calls == 1
        occurrence = definitions.list_pending_occurrences(limit=10)
        assert occurrence == []
        durable = results.get_by_occurrence(worker_calls[0])
        assert durable is not None
        assert durable["result_status"] == "SUCCEEDED"
        command = infrastructure.command_repository.get_by_request(
            surface="scheduler",
            request_id=(
                definitions.get_occurrence(worker_calls[0])["request_id"]
            ),
        )
        assert command is not None
        assert command["command"].surface.value == "scheduler"
        assert task_store.list_pending_outbox(limit=100) == []
    finally:
        await scheduler.runtime.aclose()
        await command_ingress.aclose()
        ledger.close()
