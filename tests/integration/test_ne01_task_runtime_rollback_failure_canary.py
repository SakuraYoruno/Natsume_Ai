from __future__ import annotations

from pathlib import Path

from scripts.runtime.run_task_runtime_rollback_failure_canary import run_canary


ROOT = Path(__file__).resolve().parents[2]


def test_real_sqlite_task_runtime_rollback_failure_canary() -> None:
    receipt = run_canary(
        repository_root=ROOT,
        run_id="integration-task-runtime-rollback-failure",
    )

    expected_head = max(
        (ROOT / "host_brain" / "audit" / "migrations").glob("*.sql"),
        key=lambda path: (int(path.name[:4]), path.name),
    )
    assert receipt["status"] == "PASS"
    assert receipt["database_kind"] == "ISOLATED_REAL_SQLITE"
    assert receipt["migration_head"] == expected_head.name
    assert receipt["migration_head_version"] == int(expected_head.name[:4])
    assert receipt["activation_failure"] == {
        "status": "PASS",
        "reason_code": "TASK_RUNTIME_ACTIVATION_FAILED",
        "coordinator_activate_calls": 1,
        "continuation_start_calls": 1,
    }
    fence = receipt["rollback_failure_fence"]
    assert fence["status"] == "PASS"
    assert fence["reactivation_reason_code"] == (
        "TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED"
    )
    assert fence["bound_runtime_retained"] is True
    assert fence["infrastructure_closed_after_failure"] is False
    assert fence["first_close_failures"][-1]["component"] == (
        "runtime_coordinator_quiescence"
    )
    assert receipt["close_retry"]["status"] == "PASS"
    assert receipt["close_retry"]["container_closed"] is True
    assert receipt["terminal_replay"] == {
        "status": "PASS",
        "additional_coordinator_close_calls": 0,
    }
    assert receipt["durable_state"]["integrity_check"] == "ok"
    assert receipt["durable_state"]["task_graph_count"] == 0
    assert receipt["durable_state"]["task_outbox_count"] == 0
    assert receipt["fault_scope"] == "PROCESS_LOCAL_LIFECYCLE_BOUNDARY"
    assert receipt["production_failpoint_added"] is False
    assert receipt["external_side_effects"] == 0
    assert receipt["temporary_database_removed"] is True
