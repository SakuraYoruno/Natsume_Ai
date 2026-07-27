from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.workspace_cleanup_guardian_observer import (
    WorkspaceCleanupGuardianObserver,
)
from host_brain.app.internal_runtime_composition import (
    compose_internal_runtime,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.composition.agent_workspace import (
    WorkspaceCleanupContinuationWorker,
)
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.runtime.runtime_guardian import RuntimeGuardian


NOW = 1_900_000_000_000
LEASE_MS = 7 * 24 * 60 * 60 * 1000


def _guardian_builder(tmp_path: Path):
    db_path = tmp_path / "workspace-cleanup-guardian.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-workspace-cleanup-guardian",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    scope = "runtime-scope-workspace-cleanup"
    repository = EmbodimentOutboxRepository(db_path, runtime_scope_id=scope)
    lease = repository.acquire_runtime_lease(
        worker_id="owner-workspace-cleanup",
        lease_ms=LEASE_MS,
        now_ms=NOW,
    )
    assert lease is not None

    def provider():
        current = repository.validate_runtime_lease(
            worker_id=str(lease["owner_id"]),
            lease_id=str(lease["lease_id"]),
            fencing_token=int(lease["fencing_token"]),
            now_ms=NOW,
        )
        assert current is not None
        return {**current, "runtime_scope_id": scope}

    def build() -> RuntimeGuardian:
        return RuntimeGuardian(
            event_store=RuntimeEventStore(db_path, clock=lambda: NOW),
            runtime_scope_id=scope,
            runtime_authority_provider=provider,
            clock=lambda: NOW,
        )

    return build


class _CleanupRuntime:
    operation_timeout_s = 0.1

    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls = 0

    def continue_pending_cleanups(self, *, limit: int, cancelled):
        assert limit == 100
        assert cancelled() is False
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return ({"status": "CLEANED", "cleanup_receipt_ref": "opaque"},)


def test_cleanup_failure_and_replacement_process_recovery_are_durable(
    tmp_path: Path,
) -> None:
    build_guardian = _guardian_builder(tmp_path)
    first_guardian = build_guardian()
    failing_runtime = _CleanupRuntime(
        failure=RuntimeError(
            "workspace=C:/private/project provider-password=must-not-persist"
        )
    )
    first_worker = WorkspaceCleanupContinuationWorker(
        failing_runtime,  # type: ignore[arg-type]
        observation_sink=WorkspaceCleanupGuardianObserver(
            guardian=first_guardian,
        ),
    )

    with pytest.raises(RuntimeError, match="must-not-persist"):
        first_worker.run_once()

    failed_health = first_worker.health_snapshot()
    assert failed_health["status"] == "STOPPED"
    assert failed_health["consecutive_failures"] == 1
    assert failed_health["guardian_projection_status"] == "READY"
    assert first_guardian.snapshot().status == "SLO_BREACH"
    serialized = json.dumps(first_guardian.event_history(), ensure_ascii=False)
    assert "agent-workspace-cleanup-continuation" in serialized
    assert "private/project" not in serialized
    assert "provider-password" not in serialized
    assert "must-not-persist" not in serialized
    assert "RuntimeError" not in serialized
    with pytest.raises(RuntimeError, match="must-not-persist"):
        first_worker.run_once()
    assert first_worker.health_snapshot()["consecutive_failures"] == 2
    assert [row["event_type"] for row in first_guardian.event_history()] == [
        "GUARDIAN_SLO_BREACH"
    ]

    restarted_guardian = build_guardian()
    assert restarted_guardian.snapshot().status == "SLO_BREACH"
    recovered_runtime = _CleanupRuntime()
    restarted_worker = WorkspaceCleanupContinuationWorker(
        recovered_runtime,  # type: ignore[arg-type]
        observation_sink=WorkspaceCleanupGuardianObserver(
            guardian=restarted_guardian,
        ),
    )

    assert restarted_worker.run_once()[0]["status"] == "CLEANED"
    assert restarted_guardian.snapshot().slo_breaches == ()
    assert [row["event_type"] for row in restarted_guardian.event_history()] == [
        "GUARDIAN_SLO_BREACH",
        "GUARDIAN_SLO_RESOLVED",
    ]

    before = restarted_guardian.snapshot().revision
    restarted_worker.run_once()
    assert restarted_guardian.snapshot().revision == before


def test_cleanup_observer_failure_keeps_worker_degraded() -> None:
    runtime = _CleanupRuntime()

    def broken_observer(_observation):
        raise OSError("private guardian database path")

    worker = WorkspaceCleanupContinuationWorker(
        runtime,  # type: ignore[arg-type]
        observation_sink=broken_observer,
    )

    with pytest.raises(
        RuntimeError,
        match="AGENT_WORKSPACE_CLEANUP_GUARDIAN_PROJECTION_FAILED",
    ):
        worker.run_once()

    health = worker.health_snapshot()
    assert health["consecutive_failures"] == 1
    assert health["guardian_projection_status"] == "FAILED"
    assert health["guardian_projection_error_type"] == "OSError"
    assert "private guardian database path" not in str(health)


def test_internal_runtime_composition_injects_app_local_guardian_observer() -> None:
    captured: dict[str, object] = {}

    class _CompositionStopped(RuntimeError):
        pass

    def workspace_composer(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        raise _CompositionStopped

    guardian = SimpleNamespace(
        record_slo_breach=lambda **_kwargs: None,
        resolve_slo_breach=lambda **_kwargs: None,
        snapshot=lambda: None,
        event_history=lambda: [],
    )
    task_runtime = SimpleNamespace(
        execution_owner_id=lambda owner: f"process::{owner}",
    )
    application = object()
    runtime_settings = SimpleNamespace(
        task_runtime_canonical_owner="canonical-owner",
    )

    with pytest.raises(_CompositionStopped):
        compose_internal_runtime(
            application=application,
            task_runtime=task_runtime,
            runtime_settings=runtime_settings,
            ledger=object(),
            command_ingress=object(),
            tool_composition=object(),
            runtime_context_application=SimpleNamespace(
                composition=SimpleNamespace(guardian=guardian),
            ),
            action_preview_service=object(),
            cancellation_authority_verifier=object(),
            episodic_projection_store=object(),
            semantic_recaller=object(),
            mode_transition_service_provider=lambda: None,
            scheduler_command_service_provider=lambda: None,
            life_receipt_reader_provider=lambda: None,
            life_projection_dispatcher=object(),
            life_authority_provider=lambda: (None, None),
            agent_runtime_ports=object(),
            token_service_provider=lambda: None,
            intent_engine_provider=lambda: None,
            model_attempt_service_provider=lambda: None,
            prompt_registry_provider=lambda: None,
            workspace_composer=workspace_composer,
            artifact_store_factory=lambda: None,
            scheduler_worker_registry_factory=lambda: None,
            scheduler_operation_handler_factory=lambda **_kwargs: None,
            operator_composition_factory=lambda **_kwargs: None,
            runtime_router_factory=lambda: None,
            agent_executor_factory=lambda **_kwargs: None,
            internal_runtime_factory=lambda **_kwargs: None,
        )

    assert isinstance(
        captured["cleanup_observer"],
        WorkspaceCleanupGuardianObserver,
    )
    assert captured["args"] == (
        application,
        task_runtime,
        runtime_settings,
        "process::canonical-owner",
    )
