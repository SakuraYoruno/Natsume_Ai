from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.host_hard_state import (
    HOST_MODE_TRANSITIONS,
    HOST_SYS_MODES,
)
from host_brain.core.metabolism import ALLOWED_SYS_MODES
from host_brain.core.mode_transition import VALID_TRANSITIONS
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.hard_state_journal import (
    HardStateJournalError,
    SQLiteHardStateJournal,
)


ROOT = Path(__file__).resolve().parents[2]


def test_mode_contract_is_immutable_and_persistence_neutral() -> None:
    assert HOST_SYS_MODES == {
        "normal",
        "focus",
        "gaming",
        "sleep",
        "maintenance",
        "locked",
    }
    assert set(HOST_MODE_TRANSITIONS) == HOST_SYS_MODES
    assert HOST_MODE_TRANSITIONS["normal"] == {
        "focus",
        "gaming",
        "sleep",
        "maintenance",
        "locked",
    }
    assert ALLOWED_SYS_MODES is HOST_SYS_MODES
    assert VALID_TRANSITIONS is HOST_MODE_TRANSITIONS
    with pytest.raises(AttributeError):
        HOST_SYS_MODES.add("invalid")  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        HOST_MODE_TRANSITIONS["normal"] = frozenset()  # type: ignore[index]
    with pytest.raises(AttributeError):
        HOST_MODE_TRANSITIONS["normal"].add("invalid")  # type: ignore[attr-defined]
    persistence_source = (
        ROOT / "host_brain" / "persistence" / "hard_state_journal.py"
    ).read_text(encoding="utf-8")
    assert "host_brain.core.metabolism" not in persistence_source
    assert "host_brain.core.mode_transition" not in persistence_source


class _Clock:
    def __init__(self, value: int) -> None:
        self.value = int(value)

    def __call__(self) -> int:
        return self.value

    def advance(self, milliseconds: int = 1) -> int:
        self.value += int(milliseconds)
        return self.value


def _runtime(tmp_path: Path):
    db_path = tmp_path / "hard-state.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="hard-state-contract"
    )
    assert migrated.success, migrated.detail
    now = int(time.time() * 1000)
    clock = _Clock(now)
    lease_repository = EmbodimentOutboxRepository(db_path)
    authority = lease_repository.acquire_runtime_lease(
        worker_id="host-a",
        lease_ms=3_600_000,
        now_ms=now,
    )
    assert authority is not None

    def provider():
        return {**authority, "runtime_scope_id": lease_repository.runtime_scope_id}

    journal = SQLiteHardStateJournal(
        db_path,
        runtime_scope_id=lease_repository.runtime_scope_id,
        runtime_authority_provider=provider,
        clock=clock,
        replay_page_size=17,
    )
    return db_path, clock, lease_repository, authority, provider, journal


def _initialize(journal: SQLiteHardStateJournal, clock: _Clock):
    return journal.load_or_initialize(
        initial_mode="normal",
        event_id="hard-state-init",
        transition_ts_ms=clock(),
    )


def test_initialization_transition_restart_and_exact_replay(tmp_path: Path) -> None:
    db_path, clock, lease_repository, _authority, provider, journal = _runtime(
        tmp_path
    )
    initialized = _initialize(journal, clock)
    clock.advance()
    changed = journal.transition(
        expected_revision=1,
        expected_mode="normal",
        target_mode="focus",
        event_id="mode-focus-1",
        source="manual",
        reason="focus",
        transition_ts_ms=clock(),
    )
    replay = journal.transition(
        expected_revision=1,
        expected_mode="normal",
        target_mode="focus",
        event_id="mode-focus-1",
        source="manual",
        reason="focus",
        transition_ts_ms=clock(),
    )
    restarted = SQLiteHardStateJournal(
        db_path,
        runtime_scope_id=lease_repository.runtime_scope_id,
        runtime_authority_provider=provider,
        clock=clock,
        replay_page_size=1,
    )

    assert initialized.snapshot.revision == 1
    assert changed.snapshot.revision == 2
    assert replay.exact_replay is True
    assert replay.snapshot == changed.snapshot
    assert restarted.load() == changed.snapshot
    assert [event.event_kind for event in restarted.history()] == [
        "MODE_CHANGED",
        "INITIALIZED",
    ]


def test_replay_drift_illegal_path_and_noop_fail_closed(tmp_path: Path) -> None:
    _db, clock, _lease, _authority, _provider, journal = _runtime(tmp_path)
    _initialize(journal, clock)
    clock.advance()
    journal.transition(
        expected_revision=1,
        expected_mode="normal",
        target_mode="focus",
        event_id="mode-focus-1",
        source="manual",
        reason="focus",
        transition_ts_ms=clock(),
    )
    with pytest.raises(HardStateJournalError, match="REPLAY_DRIFT"):
        journal.transition(
            expected_revision=1,
            expected_mode="normal",
            target_mode="focus",
            event_id="mode-focus-1",
            source="manual",
            reason="changed reason",
            transition_ts_ms=clock(),
        )
    with pytest.raises(HardStateJournalError, match="NOOP"):
        journal.transition(
            expected_revision=2,
            expected_mode="focus",
            target_mode="focus",
            event_id="mode-noop",
            source="manual",
            reason="noop",
            transition_ts_ms=clock(),
        )
    with pytest.raises(HardStateJournalError, match="PATH_REJECTED"):
        journal.transition(
            expected_revision=2,
            expected_mode="focus",
            target_mode="gaming",
            event_id="mode-illegal",
            source="manual",
            reason="illegal",
            transition_ts_ms=clock(),
        )


def test_historical_receipt_lookup_survives_newer_head_without_reapplying(
    tmp_path: Path,
) -> None:
    _db, clock, _lease, _authority, _provider, journal = _runtime(tmp_path)
    _initialize(journal, clock)
    first_ts = clock.advance()
    first = journal.transition(
        expected_revision=1,
        expected_mode="normal",
        target_mode="focus",
        event_id="historical-focus",
        source="manual",
        reason="work",
        transition_ts_ms=first_ts,
    )
    clock.advance()
    latest = journal.transition(
        expected_revision=2,
        expected_mode="focus",
        target_mode="normal",
        event_id="later-normal",
        source="manual",
        reason="done",
        transition_ts_ms=clock(),
    )
    replay = journal.replay_transition(
        target_mode="focus",
        event_id="historical-focus",
        source="manual",
        reason="work",
    )
    assert replay is not None
    assert replay.exact_replay is True
    assert replay.event == first.event
    assert replay.snapshot == latest.snapshot
    assert journal.load() == latest.snapshot
    with pytest.raises(HardStateJournalError, match="REPLAY_DRIFT"):
        journal.transition(
            expected_revision=999,
            expected_mode="normal",
            target_mode="focus",
            event_id="historical-focus",
            source="manual",
            reason="work",
            transition_ts_ms=clock(),
        )
    with pytest.raises(HardStateJournalError, match="REPLAY_DRIFT"):
        journal.transition(
            expected_revision=1,
            expected_mode="gaming",
            target_mode="focus",
            event_id="historical-focus",
            source="manual",
            reason="work",
            transition_ts_ms=clock(),
        )


def test_two_repository_writers_use_head_cas(tmp_path: Path) -> None:
    db, clock, lease, _authority, provider, journal = _runtime(tmp_path)
    _initialize(journal, clock)
    clock.advance()
    first = SQLiteHardStateJournal(
        db,
        runtime_scope_id=lease.runtime_scope_id,
        runtime_authority_provider=provider,
        clock=clock,
    )
    second = SQLiteHardStateJournal(
        db,
        runtime_scope_id=lease.runtime_scope_id,
        runtime_authority_provider=provider,
        clock=clock,
    )

    def write(candidate: tuple[SQLiteHardStateJournal, str]):
        repository, target = candidate
        return repository.transition(
            expected_revision=1,
            expected_mode="normal",
            target_mode=target,
            event_id=f"mode-{target}",
            source="manual",
            reason=target,
            transition_ts_ms=clock(),
        )

    outcomes: list[object] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, item) for item in ((first, "focus"), (second, "gaming"))]
        for future in futures:
            try:
                outcomes.append(future.result())
            except HardStateJournalError as exc:
                outcomes.append(exc)
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(
        isinstance(item, HardStateJournalError)
        and "EXPECTED_HEAD_CONFLICT" in str(item)
        for item in outcomes
    ) == 1
    assert journal.load().revision == 2  # type: ignore[union-attr]


@pytest.mark.parametrize("fault_point", ["after_event_append", "after_head_write"])
def test_fault_injection_rolls_back_event_and_head(
    tmp_path: Path, fault_point: str
) -> None:
    db, clock, lease, _authority, provider, journal = _runtime(tmp_path)
    initial = _initialize(journal, clock).snapshot
    clock.advance()

    def inject(point: str) -> None:
        if point == fault_point:
            raise RuntimeError(fault_point)

    failing = SQLiteHardStateJournal(
        db,
        runtime_scope_id=lease.runtime_scope_id,
        runtime_authority_provider=provider,
        clock=clock,
        fault_injector=inject,
    )
    with pytest.raises(RuntimeError, match=fault_point):
        failing.transition(
            expected_revision=1,
            expected_mode="normal",
            target_mode="focus",
            event_id=f"mode-{fault_point}",
            source="manual",
            reason="fault",
            transition_ts_ms=clock(),
        )
    assert journal.load() == initial
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM host_hard_state_event"
        ).fetchone()[0] == 1


def test_released_stale_fence_cannot_write_and_new_fence_recovers(
    tmp_path: Path,
) -> None:
    db, clock, lease, authority, provider, journal = _runtime(tmp_path)
    _initialize(journal, clock)
    clock.advance()
    assert lease.release_runtime_lease(
        worker_id="host-a",
        lease_id=authority["lease_id"],
        fencing_token=authority["fencing_token"],
        now_ms=clock(),
    )
    with pytest.raises(HardStateJournalError, match="AUTHORITY_FENCED"):
        journal.transition(
            expected_revision=1,
            expected_mode="normal",
            target_mode="focus",
            event_id="stale-write",
            source="manual",
            reason="stale",
            transition_ts_ms=clock(),
        )
    clock.advance()
    next_authority = lease.acquire_runtime_lease(
        worker_id="host-b", lease_ms=3_600_000, now_ms=clock()
    )
    assert next_authority is not None
    replacement = SQLiteHardStateJournal(
        db,
        runtime_scope_id=lease.runtime_scope_id,
        runtime_authority_provider=lambda: {
            **next_authority,
            "runtime_scope_id": lease.runtime_scope_id,
        },
        clock=clock,
    )
    recovered = replacement.load_or_initialize(
        initial_mode="gaming",
        event_id="ignored-restart-init",
        transition_ts_ms=clock(),
    )
    assert recovered.snapshot.sys_mode == "normal"
    assert recovered.snapshot.revision == 1


def test_tamper_is_detected_by_full_replay(tmp_path: Path) -> None:
    db, clock, _lease, _authority, _provider, journal = _runtime(tmp_path)
    _initialize(journal, clock)
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TRIGGER host_hard_state_event_no_update")
        connection.execute(
            "UPDATE host_hard_state_event SET reason = 'tampered' WHERE revision = 1"
        )
        connection.commit()
    with pytest.raises(HardStateJournalError, match="EVENT_DIGEST_INVALID"):
        journal.load()


def test_schema_checksum_drift_fails_before_reads(tmp_path: Path) -> None:
    db, _clock, lease, _authority, provider, _journal = _runtime(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, "0115#host_hard_state_journal"),
        )
        connection.commit()
    with pytest.raises(HardStateJournalError, match="MIGRATION_DRIFT"):
        SQLiteHardStateJournal(
            db,
            runtime_scope_id=lease.runtime_scope_id,
            runtime_authority_provider=provider,
        )


def test_lease_expiry_while_waiting_for_write_lock_fails_closed(
    tmp_path: Path,
) -> None:
    db, clock, lease, authority, provider, journal = _runtime(tmp_path)
    _initialize(journal, clock)
    transition_ts = clock.advance()
    samples = iter((transition_ts, int(authority["expires_at_ms"])))
    expiring = SQLiteHardStateJournal(
        db,
        runtime_scope_id=lease.runtime_scope_id,
        runtime_authority_provider=provider,
        clock=lambda: next(samples),
    )
    with pytest.raises(HardStateJournalError, match="AUTHORITY_FENCED"):
        expiring.transition(
            expected_revision=1,
            expected_mode="normal",
            target_mode="focus",
            event_id="expired-after-lock",
            source="manual",
            reason="lock wait",
            transition_ts_ms=transition_ts,
        )
    assert journal.load().revision == 1  # type: ignore[union-attr]


def test_sql_rejects_non_hex_digest_and_backdated_expired_lease(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "sql-guards.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="hard-state-sql-guards"
    )
    assert result.success
    expired_now = int(time.time() * 1000) - 20_000
    lease = EmbodimentOutboxRepository(db_path, runtime_scope_id="expired-scope")
    authority = lease.acquire_runtime_lease(
        worker_id="expired-owner", lease_ms=1_000, now_ms=expired_now
    )
    assert authority is not None
    columns = (
        "runtime_scope_id,revision,event_id,idempotency_key,event_kind,from_mode,to_mode,"
        "source,reason,previous_event_digest,event_digest,runtime_owner_id,runtime_lease_id,"
        "runtime_fencing_token,runtime_issued_at_ms,runtime_expires_at_ms,transition_ts_ms,created_at_ms"
    )
    base = (
        "expired-scope",
        1,
        "expired-init",
        "sha256:" + "a" * 64,
        "INITIALIZED",
        "normal",
        "normal",
        "host_bootstrap",
        "",
        "",
        "sha256:" + "b" * 64,
        authority["owner_id"],
        authority["lease_id"],
        authority["fencing_token"],
        authority["issued_at_ms"],
        authority["expires_at_ms"],
        expired_now,
        expired_now,
    )
    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="authority is not active"):
            connection.execute(
                f"INSERT INTO host_hard_state_event({columns}) VALUES ({','.join('?' for _ in base)})",
                base,
            )
        malformed = list(base)
        malformed[3] = "sha256:a" + "z" * 63
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO host_hard_state_event({columns}) VALUES ({','.join('?' for _ in malformed)})",
                malformed,
            )


def test_1001_transitions_recover_across_cursor_pages(tmp_path: Path) -> None:
    db, clock, lease, _authority, provider, journal = _runtime(tmp_path)
    _initialize(journal, clock)
    mode = "normal"
    for index in range(1001):
        target = "focus" if mode == "normal" else "normal"
        # This deliberately long replay test must keep the injected application
        # clock aligned with SQLite's wall clock; the production clock is live.
        clock.value = max(clock.value, int(time.time() * 1000))
        journal.transition(
            expected_revision=index + 1,
            expected_mode=mode,
            target_mode=target,
            event_id=f"bulk-mode-{index:04d}",
            source="manual",
            reason="pagination recovery",
            transition_ts_ms=clock(),
        )
        mode = target
    restarted = SQLiteHardStateJournal(
        db,
        runtime_scope_id=lease.runtime_scope_id,
        runtime_authority_provider=provider,
        clock=clock,
        replay_page_size=13,
    )
    snapshot = restarted.load()
    assert snapshot is not None
    assert snapshot.revision == 1002
    assert snapshot.sys_mode == mode
