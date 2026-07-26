from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from typing import Any

import pytest

from host_brain.app.host_mode_policy import HostModePolicyOwner
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.embodiment import HostEmbodimentRuntime
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.host_mode_policy_journal import (
    HostModePolicyJournalError,
    SQLiteHostModePolicyJournal,
)


class _UnusedEmbodimentPort:
    async def submit(self, _command: Any) -> Any:
        raise AssertionError("mode-policy tests must not dispatch Edge effects")


def _runtime(db_path: Path, worker_id: str) -> HostEmbodimentRuntime:
    return HostEmbodimentRuntime(
        repository=EmbodimentOutboxRepository(db_path),
        worker_id=worker_id,
        command_port=_UnusedEmbodimentPort(),
        authority_lease_ms=600_000,
    )


def _authority(runtime: HostEmbodimentRuntime) -> dict[str, Any]:
    return {
        **runtime.renew_authority(),
        "runtime_scope_id": runtime.runtime_scope_id,
    }


def _journal(
    db_path: Path,
    runtime: HostEmbodimentRuntime,
    *,
    replay_page_size: int = 37,
    fault_injector: Any | None = None,
) -> SQLiteHostModePolicyJournal:
    return SQLiteHostModePolicyJournal(
        db_path,
        runtime_scope_id=runtime.runtime_scope_id,
        runtime_authority_provider=lambda: _authority(runtime),
        replay_page_size=replay_page_size,
        fault_injector=fault_injector,
    )


def _migrate(db_path: Path) -> int:
    now = int(time.time() * 1_000)
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"ne01-host-mode-policy-{db_path.stem}",
        ts_ms=now,
    )
    assert result.success, result.as_dict()
    return now


def test_mode_policy_restart_exact_replay_and_1001_event_chain(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "host-mode-policy-restart.sqlite3"
    started = _migrate(db_path)
    runtime = _runtime(db_path, "host-mode-policy-owner-a")
    owner = HostModePolicyOwner.bootstrap(
        initial_last_interaction_ts_ms=started,
        require_durable=True,
    )
    try:
        initial = owner.bind_durable_journal(_journal(db_path, runtime))
        assert initial.revision == 1
        rules = owner.update_auto_rules(
            {"idle_to_sleep": False},
            operation_id="operator-mode-rules-1",
            expected_revision=initial.revision,
            observed_at_ms=started,
        )
        assert rules.snapshot.revision == 2
        for index in range(1_001):
            current = owner.snapshot()
            owner.record_event(
                operation_id=f"mode-observation-{index:04d}",
                expected_revision=current.revision,
                event_kind="INTERACTION_RECORDED",
                input_payload={"index": index},
                event_payload={"interaction_recorded": True, "index": index},
                last_interaction_ts_ms=started + index + 1,
                observed_at_ms=started,
            )
        head = owner.snapshot()
        assert head.revision == 1_003
        assert head.auto_rules["idle_to_sleep"] is False
        exact = owner.update_auto_rules(
            {"idle_to_sleep": False},
            operation_id="operator-mode-rules-1",
            expected_revision=1,
            observed_at_ms=started,
        )
        assert exact.exact_replay is True
        assert exact.event.revision == 2
        assert exact.snapshot.revision == 1_003
        owner.unbind_durable_journal()
    finally:
        runtime.close()

    restarted_runtime = _runtime(db_path, "host-mode-policy-owner-b")
    restarted = HostModePolicyOwner.bootstrap(
        initial_last_interaction_ts_ms=0,
        require_durable=True,
    )
    try:
        recovered = restarted.bind_durable_journal(
            _journal(
                db_path,
                restarted_runtime,
                replay_page_size=31,
            )
        )
        assert recovered.revision == 1_003
        assert recovered.auto_rules["idle_to_sleep"] is False
        assert recovered.last_interaction_ts_ms == started + 1_001
    finally:
        restarted.unbind_durable_journal()
        restarted_runtime.close()


def test_mode_policy_append_failure_rolls_back_event_and_head(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "host-mode-policy-rollback.sqlite3"
    started = _migrate(db_path)
    runtime = _runtime(db_path, "host-mode-policy-fault-owner")
    owner = HostModePolicyOwner.bootstrap(
        initial_last_interaction_ts_ms=started,
        require_durable=True,
    )
    try:
        owner.bind_durable_journal(_journal(db_path, runtime))

        def fail(stage: str) -> None:
            if stage == "after_event_insert":
                raise RuntimeError("injected append failure")

        failing = _journal(
            db_path,
            runtime,
            fault_injector=fail,
        )
        with pytest.raises(RuntimeError, match="injected append failure"):
            failing.append(
                expected_revision=1,
                operation_id="mode-policy-fault",
                event_kind="AUTO_RULES_UPDATED",
                input_payload={"rules": {"gaming_detect": False}},
                event_payload={"rules": {"gaming_detect": False}},
                auto_rules={
                    **dict(owner.snapshot().auto_rules),
                    "gaming_detect": False,
                },
                last_interaction_ts_ms=started,
                observed_at_ms=started,
            )
        assert owner.snapshot().revision == 1
        assert _journal(db_path, runtime).load().revision == 1
        assert (
            _journal(db_path, runtime).get_operation("mode-policy-fault")
            is None
        )
    finally:
        owner.unbind_durable_journal()
        runtime.close()


def test_mode_policy_tamper_and_released_fence_fail_closed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "host-mode-policy-tamper.sqlite3"
    started = _migrate(db_path)
    runtime = _runtime(db_path, "host-mode-policy-tamper-owner")
    owner = HostModePolicyOwner.bootstrap(
        initial_last_interaction_ts_ms=started,
        require_durable=True,
    )
    journal = _journal(db_path, runtime)
    owner.bind_durable_journal(journal)
    owner.update_auto_rules(
        {"morning_wakeup": False},
        operation_id="mode-policy-before-tamper",
        expected_revision=1,
        observed_at_ms=started,
    )
    owner.unbind_durable_journal()
    runtime.close()

    with pytest.raises(HostModePolicyJournalError):
        journal.append(
            expected_revision=2,
            operation_id="mode-policy-stale-fence",
            event_kind="AUTO_RULES_UPDATED",
            input_payload={"rules": {"morning_wakeup": True}},
            event_payload={"rules": {"morning_wakeup": True}},
            auto_rules={
                "gaming_detect": True,
                "gaming_exit": True,
                "idle_to_sleep": True,
                "morning_wakeup": True,
                "wakeup_on_msg": True,
            },
            last_interaction_ts_ms=started,
            observed_at_ms=started,
        )

    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TRIGGER host_mode_policy_event_no_update")
        connection.execute(
            """
            UPDATE host_mode_policy_event
            SET event_payload_json = '{"rules":{"morning_wakeup":true}}'
            WHERE revision = 2
            """
        )
    replacement_runtime = _runtime(
        db_path,
        "host-mode-policy-tamper-reader",
    )
    try:
        with pytest.raises(
            HostModePolicyJournalError,
            match="HOST_MODE_POLICY_(EVENT|SCHEMA|DIGEST|CHAIN)",
        ):
            _journal(db_path, replacement_runtime).load()
    finally:
        replacement_runtime.close()
