from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.runtime.runtime_guardian import RuntimeGuardian, RuntimeGuardianError


NOW = 1_900_000_000_000
LEASE_MS = 7 * 24 * 60 * 60 * 1000


def _authority(tmp_path: Path, *, name: str = "guardian"):
    db_path = tmp_path / f"{name}.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"ne03-{name}",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    scope = f"runtime-scope-{name}"
    repository = EmbodimentOutboxRepository(db_path, runtime_scope_id=scope)
    lease = repository.acquire_runtime_lease(
        worker_id=f"owner-{name}",
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

    return db_path, scope, provider


def test_runtime_guardian_replays_more_than_one_cursor_page_from_sqlite(
    tmp_path: Path,
) -> None:
    db_path, scope, provider = _authority(tmp_path, name="guardian-long-journal")
    store = RuntimeEventStore(db_path, clock=lambda: NOW)
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id=scope,
        runtime_authority_provider=provider,
        clock=lambda: NOW,
    )

    for index in range(501):
        observed = NOW + index * 2 + 1
        breached = guardian.record_slo_breach(
            slo_id="host-latency-p95",
            observed_value=100.0 + index,
            threshold=99.0,
            evidence_ref=f"slo-evidence://run/{index}",
            observed_at_ms=observed,
        )
        guardian.resolve_slo_breach(
            breach_ref=breached.slo_breaches[0],
            evidence_ref=f"slo-recovery://run/{index}",
            observed_at_ms=observed + 1,
        )

    assert guardian.snapshot().revision == 1002
    restarted = RuntimeGuardian(
        event_store=RuntimeEventStore(db_path, clock=lambda: NOW),
        runtime_scope_id=scope,
        runtime_authority_provider=provider,
        clock=lambda: NOW,
    )
    snapshot = restarted.snapshot()
    assert snapshot.revision == 1002
    assert snapshot.slo_breaches == ()
    assert len(restarted.event_history()) == 1002


def test_two_guardians_compete_for_one_sqlite_revision_and_only_one_wins(
    tmp_path: Path,
) -> None:
    db_path, scope, provider = _authority(tmp_path, name="guardian-competition")
    first = RuntimeGuardian(
        event_store=RuntimeEventStore(db_path, clock=lambda: NOW),
        runtime_scope_id=scope,
        runtime_authority_provider=provider,
        clock=lambda: NOW,
    )
    second = RuntimeGuardian(
        event_store=RuntimeEventStore(db_path, clock=lambda: NOW),
        runtime_scope_id=scope,
        runtime_authority_provider=provider,
        clock=lambda: NOW,
    )

    def enter(mode: str) -> str:
        target = first if mode == "MAINTENANCE" else second
        try:
            if mode == "MAINTENANCE":
                target.enter_maintenance(
                    reason_ref="maintenance://operator/change-one",
                    observed_at_ms=NOW + 1,
                )
            else:
                target.enter_sleep(
                    reason_ref="sleep://policy/night-one",
                    observed_at_ms=NOW + 1,
                )
            return f"OK:{mode}"
        except RuntimeGuardianError:
            return f"FENCED:{mode}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = set(pool.map(enter, ("MAINTENANCE", "SLEEP")))

    assert sum(item.startswith("OK:") for item in outcomes) == 1
    assert sum(item.startswith("FENCED:") for item in outcomes) == 1
    restarted = RuntimeGuardian(
        event_store=RuntimeEventStore(db_path, clock=lambda: NOW),
        runtime_scope_id=scope,
        runtime_authority_provider=provider,
        clock=lambda: NOW,
    )
    assert restarted.snapshot().revision == 1
    assert restarted.snapshot().mode in {"MAINTENANCE", "SLEEP"}


class _CommitThenLoseResponseStore:
    def __init__(self, durable: RuntimeEventStore) -> None:
        self._durable = durable

    def append_event(self, event):
        self._durable.append_event(event)
        raise OSError("injected response loss after SQLite commit")

    def list_events_for_aggregates(self, **kwargs):
        return self._durable.list_events_for_aggregates(**kwargs)


def test_guardian_reconciles_sqlite_commit_when_append_response_is_lost(
    tmp_path: Path,
) -> None:
    db_path, scope, provider = _authority(tmp_path, name="guardian-append-loss")
    durable = RuntimeEventStore(db_path, clock=lambda: NOW)
    guardian = RuntimeGuardian(
        event_store=_CommitThenLoseResponseStore(durable),
        runtime_scope_id=scope,
        runtime_authority_provider=provider,
        clock=lambda: NOW,
    )

    snapshot = guardian.enter_maintenance(
        reason_ref="maintenance://operator/append-loss",
        observed_at_ms=NOW + 1,
    )

    assert snapshot.revision == 1
    assert snapshot.mode == "MAINTENANCE"
    restarted = RuntimeGuardian(
        event_store=RuntimeEventStore(db_path, clock=lambda: NOW),
        runtime_scope_id=scope,
        runtime_authority_provider=provider,
        clock=lambda: NOW,
    )
    assert restarted.snapshot() == snapshot
