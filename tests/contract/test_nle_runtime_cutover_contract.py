from __future__ import annotations

import pytest

from host_brain.agent_runtime.runtime_router import (
    RUNTIME_CANARY_OWNER_REQUIRED,
    RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED,
    RUNTIME_DUPLICATE_SIDE_EFFECT,
    RUNTIME_KILL_SWITCH_ACTIVE,
    RUNTIME_LEGACY_DRAIN_INCOMPLETE,
    CanonicalRuntimeRouter,
    RuntimeExecutor,
    RuntimeMode,
    RuntimeRouterError,
    assert_no_duplicate_side_effects,
    build_canary_metrics,
    build_legacy_drain_report,
)
from host_brain.agent_runtime.task_scheduler import (
    CanonicalShadowScheduler,
    SchedulerAction,
    evaluate_configured_shadow,
)
from host_brain.app.settings.core import CoreSettings, get_core_settings


class MutableClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = int(value)

    def __call__(self) -> int:
        return self.value


def test_t_nle_11_runtime_router_defaults_to_legacy_and_preserves_existing_assignment() -> None:
    router = CanonicalRuntimeRouter(clock=MutableClock())
    assignment = router.assign_new_task("wo-cutover-default")

    assert assignment.executor is RuntimeExecutor.LEGACY
    assert assignment.mode is RuntimeMode.LEGACY
    assert assignment.is_new_task is True

    canonical_router = CanonicalRuntimeRouter(
        mode="canonical",
        canary_percent=100,
        canonical_owner="owner",
        confirmed_owner="owner",
        clock=MutableClock(2_000),
    )
    preserved = canonical_router.preserve_existing_assignment(
        assignment.to_dict(),
        work_order_id="wo-cutover-default",
    )

    assert preserved.executor is RuntimeExecutor.LEGACY
    assert preserved.assignment_id == assignment.assignment_id


def test_t_nle_11_canonical_canary_requires_owner_and_kill_switch_rolls_back_new_tasks() -> None:
    with pytest.raises(RuntimeRouterError) as missing_owner:
        CanonicalRuntimeRouter(mode="canonical", canary_percent=1).assign_new_task("wo-canary")
    assert missing_owner.value.reason_code == RUNTIME_CANARY_OWNER_REQUIRED

    canonical = CanonicalRuntimeRouter(
        mode="canonical",
        canary_percent=100,
        canonical_owner="owner",
        confirmed_owner="owner",
        clock=MutableClock(3_000),
    )
    assigned = canonical.assign_new_task("wo-canary-selected")
    assert assigned.executor is RuntimeExecutor.CANONICAL
    assert assigned.canary_selected is True

    rollback = CanonicalRuntimeRouter(
        mode="canonical",
        canary_percent=100,
        kill_switch=True,
        canonical_owner="owner",
        confirmed_owner="owner",
        clock=MutableClock(4_000),
    ).assign_new_task("wo-canary-rollback")
    assert rollback.executor is RuntimeExecutor.LEGACY
    assert rollback.reason_code == RUNTIME_KILL_SWITCH_ACTIVE


def test_t_nle_11_cross_executor_resume_is_rejected() -> None:
    assignment = CanonicalRuntimeRouter(
        mode="canonical",
        canary_percent=100,
        canonical_owner="owner",
        confirmed_owner="owner",
    ).assign_new_task("wo-resume")

    with pytest.raises(RuntimeRouterError) as rejected:
        CanonicalRuntimeRouter().validate_resume_executor(
            assignment,
            requested_executor=RuntimeExecutor.LEGACY,
        )
    assert rejected.value.reason_code == RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED


def test_t_nle_11_legacy_drain_report_and_canary_metrics_are_deterministic() -> None:
    jobs = [
        {"job_id": "job-queued", "status": "QUEUED"},
        {"job_id": "job-running", "status": "RUNNING"},
        {"job_id": "job-completed", "status": "COMPLETED"},
        {"job_id": "job-failed", "status": "FAILED"},
    ]
    drain = build_legacy_drain_report(jobs)
    assert drain["drained"] is False
    assert drain["reason_code"] == RUNTIME_LEGACY_DRAIN_INCOMPLETE
    assert drain["legacy_open_job_ids"] == ("job-queued", "job-running")

    router = CanonicalRuntimeRouter(
        mode="canonical",
        canary_percent=100,
        canonical_owner="owner",
        confirmed_owner="owner",
    )
    metrics = build_canary_metrics(
        [
            router.assign_new_task("wo-canonical-1"),
            CanonicalRuntimeRouter().assign_new_task("wo-legacy-1"),
        ]
    )
    assert metrics["assignment_count"] == 2
    assert metrics["canonical_count"] == 1
    assert metrics["legacy_count"] == 1


def test_t_nle_11_duplicate_side_effect_detection_rejects_replay() -> None:
    clean = assert_no_duplicate_side_effects(
        [
            {"operation_fingerprint": "sha256:a", "executor": "legacy", "attempt_id": "a1"},
            {"operation_fingerprint": "sha256:b", "executor": "canonical", "attempt_id": "b1"},
        ]
    )
    assert clean["duplicate_side_effect_count"] == 0

    with pytest.raises(RuntimeRouterError) as duplicate:
        assert_no_duplicate_side_effects(
            [
                {"operation_fingerprint": "sha256:same", "executor": "legacy", "attempt_id": "a1"},
                {"operation_fingerprint": "sha256:same", "executor": "canonical", "attempt_id": "b1"},
            ]
        )
    assert duplicate.value.reason_code == RUNTIME_DUPLICATE_SIDE_EFFECT


def test_t_nle_11_core_settings_accept_canonical_cutover_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_TASK_RUNTIME_MODE", "canonical")
    monkeypatch.setenv("HOST_TASK_RUNTIME_CANARY_PERCENT", "7")
    monkeypatch.setenv("HOST_TASK_RUNTIME_KILL_SWITCH", "true")
    monkeypatch.setenv("HOST_TASK_RUNTIME_CANONICAL_OWNER", "owner")
    get_core_settings.cache_clear()
    try:
        settings = get_core_settings()
        assert settings.task_runtime_mode == "canonical"
        assert settings.task_runtime_canary_percent == 7
        assert settings.task_runtime_kill_switch is True
        assert settings.task_runtime_canonical_owner == "owner"
        fields = {item.field_name for item in CoreSettings.readiness_fields()}
        assert "task_runtime_canary_percent" in fields
        assert "task_runtime_kill_switch" in fields
        assert "task_runtime_canonical_owner" in fields
    finally:
        get_core_settings.cache_clear()


def test_t_nle_11_configured_canonical_scheduler_uses_owner_confirmed_durable_snapshot() -> None:
    graph = {
        "graph": {"graph_id": "graph-1", "revision": 1, "nodes": []},
        "nodes": [
            {
                "node_id": "node-1",
                "status": "READY",
                "status_version": 0,
                "dependencies": [],
                "input_refs": [],
                "retry_policy": {"max_attempts": 1},
            }
        ],
    }

    class Store:
        def read_scheduler_snapshot(self, graph_id, revision):
            return {
                "graph_record": graph,
                "attempts": [],
                "active_leases": [],
                "budgets": [],
            }

    report = evaluate_configured_shadow(
        CanonicalShadowScheduler(),
        Store(),
        "graph-1",
        1,
        runtime_mode="canonical",
        configured_owner="owner",
        confirmed_owner="owner",
        now_ts_ms=1,
    )

    assert report is not None
    assert report.decisions[0].action is SchedulerAction.READY
