from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.app.host_lifecycle_composition import (
    HostLifecycleApplicationPorts,
    HostLifecycleContainer,
)
from host_brain.app.host_runtime_context_wiring import bind_host_hard_state_runtime
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.mode_transition import ModeTransitionService, TransitionSource
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.hard_state_journal import HardStateJournalError


def _lifecycle(initial_mode: str) -> HostLifecycleContainer:
    provider = lambda: object()
    return HostLifecycleContainer(
        ports=HostLifecycleApplicationPorts(
            memory_guard=provider,
            state_arbiter=provider,
            distillation_engine=provider,
            semantic_recaller=provider,
            episodic_store=provider,
            skill_registry=provider,
            tool_catalog=provider,
            capability_broker=provider,
            model_attempt_service=provider,
            mode_transition_service_factory=lambda **_kwargs: object(),
            activity_monitor_factory=lambda **_kwargs: object(),
            resource_manager_factory=lambda **_kwargs: object(),
        ),
        initial_sys_mode=initial_mode,
        require_durable_hard_state=True,
    )


class _Runtime:
    def __init__(self, scope: str, authority: dict[str, object]) -> None:
        self.runtime_scope_id = scope
        self.authority = authority

    def renew_authority(self) -> dict[str, object]:
        return dict(self.authority)


def test_production_binding_recovers_mode_under_rotated_host_fence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "host-runtime.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="hard-state-runtime-binding"
    )
    assert migrated.success, migrated.detail
    repository = EmbodimentOutboxRepository(db_path)
    first_authority = repository.acquire_runtime_lease(
        worker_id="first-host", lease_ms=300_000
    )
    assert first_authority is not None
    first = _lifecycle("normal")
    first.begin()
    bound = bind_host_hard_state_runtime(
        task_runtime=SimpleNamespace(canonical_db_path=db_path),
        embodiment_runtime=_Runtime(repository.runtime_scope_id, first_authority),
        host_lifecycle=first,
    )
    assert bound["revision"] == 1
    changed = first.set_sys_mode(
        "focus",
        expected_from_mode="normal",
        event_id="integration-focus",
        source="manual",
        reason="integration",
    )
    assert changed["revision"] == 2
    returned = first.set_sys_mode(
        "normal",
        expected_from_mode="focus",
        event_id="integration-normal",
        source="manual",
        reason="integration done",
    )
    assert returned["revision"] == 3
    historical = first.set_sys_mode(
        "focus",
        expected_from_mode="normal",
        event_id="integration-focus",
        source="manual",
        reason="integration",
    )
    assert historical["exact_replay"] is True
    assert historical["revision"] == 2
    assert historical["current_revision"] == 3
    assert historical["current_mode"] == "normal"
    assert first.get_hard_state().sys_mode == "normal"
    with pytest.raises(HardStateJournalError, match="REPLAY_DRIFT"):
        first.set_sys_mode(
            "focus",
            expected_from_mode="normal",
            event_id="integration-focus",
            source="manual",
            reason="drifted reason",
        )
    first.stop_activity_monitor(
        logger=SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None)
    )
    assert repository.release_runtime_lease(
        worker_id="first-host",
        lease_id=str(first_authority["lease_id"]),
        fencing_token=int(first_authority["fencing_token"]),
    )
    second_authority = repository.acquire_runtime_lease(
        worker_id="second-host", lease_ms=300_000
    )
    assert second_authority is not None
    second = _lifecycle("gaming")
    second.begin()
    recovered = bind_host_hard_state_runtime(
        task_runtime=SimpleNamespace(canonical_db_path=db_path),
        embodiment_runtime=_Runtime(repository.runtime_scope_id, second_authority),
        host_lifecycle=second,
    )
    assert recovered["revision"] == 3
    assert recovered["sys_mode"] == "normal"
    assert second.get_hard_state().sys_mode == "normal"
    assert second.get_hard_state().last_event_id == "integration-normal"


def test_exact_replay_does_not_duplicate_journal_or_legacy_success_audit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "host-runtime-replay.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="hard-state-runtime-replay"
    )
    assert migrated.success, migrated.detail
    repository = EmbodimentOutboxRepository(db_path)
    authority = repository.acquire_runtime_lease(
        worker_id="replay-host", lease_ms=300_000
    )
    assert authority is not None
    lifecycle = _lifecycle("normal")
    lifecycle.begin()
    bind_host_hard_state_runtime(
        task_runtime=SimpleNamespace(canonical_db_path=db_path),
        embodiment_runtime=_Runtime(repository.runtime_scope_id, authority),
        host_lifecycle=lifecycle,
    )
    ledger = SQLiteLedger(db_path, initialize_schema=False)
    service = ModeTransitionService(
        get_hard_state=lifecycle.get_hard_state,
        set_sys_mode=lifecycle.set_sys_mode,
        ledger=ledger,
    )
    try:
        first = service.switch_mode(
            "focus",
            source=TransitionSource.MANUAL,
            reason="exact replay integration",
            event_id="integration-exact-replay",
        )
        replay = service.switch_mode(
            "focus",
            source=TransitionSource.MANUAL,
            reason="exact replay integration",
            event_id="integration-exact-replay",
        )

        assert first["exact_replay"] is False
        assert replay["exact_replay"] is True
        assert replay["revision"] == first["revision"]
        with sqlite3.connect(db_path) as connection:
            journal_count = connection.execute(
                "SELECT COUNT(*) FROM host_hard_state_event WHERE event_id = ?",
                ("integration-exact-replay",),
            ).fetchone()[0]
            legacy_success_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM error_log
                WHERE request_id = ? AND stage = 'MODE_TRANSITION'
                """,
                ("integration-exact-replay",),
            ).fetchone()[0]
        assert journal_count == 1
        assert legacy_success_count == 0
        assert len(service.get_history(limit=10)) == 1
    finally:
        ledger.close()
