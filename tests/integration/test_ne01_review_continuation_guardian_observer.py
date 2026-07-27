from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.internal_runtime_composition import (
    activate_internal_runtime,
)
from host_brain.app.review_continuation_guardian_observer import (
    ReviewContinuationGuardianObserver,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.runtime.runtime_guardian import RuntimeGuardian


NOW = 1_900_000_000_000
LEASE_MS = 7 * 24 * 60 * 60 * 1000


def _guardian_builder(tmp_path: Path):
    db_path = tmp_path / "review-continuation-guardian.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-review-continuation-guardian",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    scope = "runtime-scope-review-continuation"
    repository = EmbodimentOutboxRepository(
        db_path,
        runtime_scope_id=scope,
    )
    lease = repository.acquire_runtime_lease(
        worker_id="owner-review-continuation",
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


def _failure(*, digest_character: str = "a") -> dict[str, object]:
    return {
        "schema": "natsume.review_continuation.failure_snapshot.v1",
        "status": "FAILED",
        "poisoned_count": 500,
        "sampled_count": 128,
        "truncated": True,
        "last_failure_ts_ms": NOW,
        "incident_digest": "sha256:" + (digest_character * 64),
    }


def test_poison_failure_and_replacement_process_recovery_are_durable(
    tmp_path: Path,
) -> None:
    build_guardian = _guardian_builder(tmp_path)
    first_guardian = build_guardian()
    observer = ReviewContinuationGuardianObserver(
        guardian=first_guardian,
    )

    observer(
        {
            **_failure(),
            "private_review_id": "review-secret-user-message",
        }
    )
    assert first_guardian.snapshot().status == "SLO_BREACH"
    serialized = json.dumps(first_guardian.event_history(), ensure_ascii=False)
    assert "task-review-continuation-poison" in serialized
    assert "review-secret-user-message" not in serialized

    before = first_guardian.snapshot().revision
    observer(_failure(digest_character="b"))
    assert first_guardian.snapshot().revision == before

    restarted_guardian = build_guardian()
    assert restarted_guardian.snapshot().status == "SLO_BREACH"
    recovered = ReviewContinuationGuardianObserver(
        guardian=restarted_guardian,
    )(
        {
            **_failure(),
            "status": "RECOVERED",
            "poisoned_count": 0,
            "sampled_count": 0,
            "truncated": False,
            "last_failure_ts_ms": None,
        }
    )

    assert recovered["status"] == "RECOVERED"
    assert restarted_guardian.snapshot().slo_breaches == ()
    assert [row["event_type"] for row in restarted_guardian.event_history()] == [
        "GUARDIAN_SLO_BREACH",
        "GUARDIAN_SLO_RESOLVED",
    ]

    before = restarted_guardian.snapshot().revision
    ReviewContinuationGuardianObserver(
        guardian=restarted_guardian,
    )(
        {
            **_failure(),
            "status": "RECOVERED",
            "poisoned_count": 0,
        }
    )
    assert restarted_guardian.snapshot().revision == before


@pytest.mark.parametrize(
    "observation",
    (
        {},
        {
            **_failure(),
            "poisoned_count": 0,
        },
        {
            **_failure(),
            "incident_digest": "sha256:not-a-digest",
        },
    ),
)
def test_observer_rejects_unbound_failure_identity(
    tmp_path: Path,
    observation: dict[str, object],
) -> None:
    guardian = _guardian_builder(tmp_path)()

    with pytest.raises(ValueError):
        ReviewContinuationGuardianObserver(guardian=guardian)(observation)


@pytest.mark.asyncio
async def test_internal_runtime_activation_injects_app_local_observer() -> None:
    captured: dict[str, object] = {}
    observer = object()

    class _CompositionStopped(RuntimeError):
        pass

    def runtime_group_factory(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        raise _CompositionStopped

    with pytest.raises(_CompositionStopped):
        await activate_internal_runtime(
            application=object(),
            task_runtime=SimpleNamespace(
                review_continuation_store=object(),
                review_store=object(),
                command_repository=object(),
                task_store=object(),
                recovery_id="runtime-recovery",
            ),
            command_ingress=SimpleNamespace(store=object()),
            security=SimpleNamespace(obs_command_composition=None),
            scheduler=SimpleNamespace(runtime=object()),
            composition=SimpleNamespace(
                host_runtime=object(),
                internal_runtime=object(),
                operator_runtime=object(),
                acceptance_effect_runtime=object(),
                canonical_owner="runtime-owner",
                review_commands=object(),
                review_continuation_observer=observer,
            ),
            cancellation_operator_enabled=False,
            operation_reconciliation_operator_enabled=False,
            effect_reconciliation_operator_enabled=False,
            terminal_reconciliation_operator_enabled=False,
            terminal_reconciliation_read_authority=object(),
            memory=object(),
            intent_engine_provider=lambda: None,
            runtime_group_factory=runtime_group_factory,
            internal_command_service_factory=lambda **_kwargs: None,
        )

    assert captured["review_continuation_observer"] is observer
    assert captured["review_continuation_worker_id"] == (
        "runtime-recovery::review-continuation"
    )
