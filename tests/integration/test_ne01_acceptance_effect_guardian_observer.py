from __future__ import annotations

import json
from pathlib import Path

from host_brain.app.acceptance_effect_recovery_observer import (
    AcceptanceEffectRecoveryGuardianObserver,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.runtime.runtime_guardian import RuntimeGuardian


NOW = 1_900_000_000_000
LEASE_MS = 7 * 24 * 60 * 60 * 1000


def _guardian(tmp_path: Path):
    db_path = tmp_path / "acceptance-effect-guardian.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-acceptance-effect-guardian",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    scope = "runtime-scope-acceptance-effects"
    repository = EmbodimentOutboxRepository(db_path, runtime_scope_id=scope)
    lease = repository.acquire_runtime_lease(
        worker_id="owner-acceptance-effects",
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


def test_recovery_failure_and_restart_resolution_are_durable_and_redacted(
    tmp_path: Path,
) -> None:
    build_guardian = _guardian(tmp_path)
    first = build_guardian()
    observer = AcceptanceEffectRecoveryGuardianObserver(guardian=first)
    failed = observer(
        {
            "schema": "natsume.acceptance_effect.recovery_observation.v1",
            "status": "FAILED",
            "reason_code": "ACCEPTANCE_EFFECT_RECOVERY_PASS_FAILED",
            "error_type": "builtins.RuntimeError",
            "error_digest": "sha256:" + "a" * 64,
            "error_detail": "provider-password=must-never-enter-journal",
        }
    )

    assert failed["status"] == "FAILED"
    assert first.snapshot().status == "SLO_BREACH"
    assert first.snapshot().slo_breaches
    serialized = json.dumps(first.event_history(), ensure_ascii=False)
    assert "acceptance-effect-recovery-pass" in serialized
    assert "provider-password" not in serialized
    assert "must-never-enter-journal" not in serialized
    assert "error_type" not in serialized

    restarted = build_guardian()
    assert restarted.snapshot().status == "SLO_BREACH"
    recovered = AcceptanceEffectRecoveryGuardianObserver(guardian=restarted)(
        {
            "schema": "natsume.acceptance_effect.recovery_observation.v1",
            "status": "RECOVERED",
            "reason_code": "ACCEPTANCE_EFFECT_RECOVERY_PASS_RECOVERED",
        }
    )

    assert recovered["status"] == "RECOVERED"
    assert len(recovered["resolved_breaches"]) == 1
    assert restarted.snapshot().slo_breaches == ()
    assert [row["event_type"] for row in restarted.event_history()] == [
        "GUARDIAN_SLO_BREACH",
        "GUARDIAN_SLO_RESOLVED",
    ]

    replayed = build_guardian()
    before = replayed.snapshot().revision
    exact_replay = AcceptanceEffectRecoveryGuardianObserver(guardian=replayed)(
        {
            "schema": "natsume.acceptance_effect.recovery_observation.v1",
            "status": "RECOVERED",
            "reason_code": "ACCEPTANCE_EFFECT_RECOVERY_PASS_RECOVERED",
        }
    )
    assert exact_replay["resolved_breaches"] == []
    assert replayed.snapshot().revision == before
