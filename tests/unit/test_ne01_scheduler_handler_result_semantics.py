from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from host_brain.app.host_life_scheduler_wiring import (
    SchedulerHandlerResultError,
    distillation_scheduler_handler_result,
    proactive_scheduler_handler_result,
    scheduler_handler_result,
    security_immune_scheduler_handler_result,
)
from host_brain.app.memory.distillation_engine import BatchDistillationReport
from host_brain.security_immune.models import AgentMode, SecuritySnapshot
from host_brain.skills.impl.proactive_talk import execute_proactive_talk


ROOT = Path(__file__).resolve().parents[2]


class _SerializableResult:
    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "APPLIED",
            "summary": "durable command applied",
        }


@pytest.mark.parametrize(
    "value",
    [None, {}, {"summary": "missing status"}, {"status": ""}],
)
def test_scheduler_handler_result_never_invents_success(value: Any) -> None:
    with pytest.raises(SchedulerHandlerResultError) as raised:
        scheduler_handler_result(value)
    assert raised.value.reason_code == "SCHEDULER_HANDLER_RESULT_INVALID"


def test_scheduler_handler_result_preserves_explicit_terminal_meaning() -> None:
    assert scheduler_handler_result(_SerializableResult()) == {
        "status": "SUCCEEDED",
        "summary": "durable command applied",
    }
    assert scheduler_handler_result(
        {"status": "CONFLICT", "reason_code": "VERSION_CONFLICT"}
    )["status"] == "BLOCKED"
    assert scheduler_handler_result(
        {"status": "SKIPPED", "reason_code": "no_due_work"}
    ) == {"status": "SKIPPED", "reason_code": "NO_DUE_WORK"}
    with pytest.raises(SchedulerHandlerResultError):
        scheduler_handler_result({"status": "SKIPPED"})


def test_proactive_mapper_requires_explicit_non_contradictory_status() -> None:
    executed = execute_proactive_talk(
        {"idle_threshold_ms": 0},
        {
            "sys_mode": "normal",
            "last_interaction_ts": 0,
            "ts_ms": 1_800_000_000_000,
            "request_id": "request-proactive-real-schema",
        },
    )
    rejected = execute_proactive_talk(
        {},
        {"sys_mode": "locked", "ts_ms": 1_800_000_000_000},
    )
    assert executed["status"] == "EXECUTED"
    assert proactive_scheduler_handler_result(executed)["status"] == "EXECUTED"
    assert rejected["status"] == "REJECTED"
    assert proactive_scheduler_handler_result(rejected)["status"] == "BLOCKED"
    with pytest.raises(SchedulerHandlerResultError):
        proactive_scheduler_handler_result(
            {"status": "EXECUTED", "success": False}
        )


@pytest.mark.parametrize(
    ("report", "status", "reason_code"),
    [
        (
            BatchDistillationReport(),
            "SKIPPED",
            "DISTILLATION_NO_CANDIDATES",
        ),
        (
            BatchDistillationReport(
                total_candidates=3,
                distilled_count=3,
                duration_ms=12,
            ),
            "SUCCEEDED",
            None,
        ),
        (
            BatchDistillationReport(
                total_candidates=3,
                distilled_count=1,
                skipped_count=1,
                failed_count=1,
            ),
            "DEGRADED",
            "DISTILLATION_PARTIAL_FAILURE",
        ),
        (
            BatchDistillationReport(
                total_candidates=2,
                failed_count=2,
            ),
            "FAILED",
            "DISTILLATION_BATCH_FAILED",
        ),
    ],
)
def test_distillation_mapper_uses_real_batch_counters(
    report: BatchDistillationReport,
    status: str,
    reason_code: str | None,
) -> None:
    result = distillation_scheduler_handler_result(report)
    assert result["status"] == status
    assert result["reason_code"] == reason_code
    assert result["total_candidates"] == report.total_candidates
    assert "proposals" not in result
    assert "results" not in result


def test_distillation_mapper_rejects_inconsistent_batch() -> None:
    with pytest.raises(SchedulerHandlerResultError):
        distillation_scheduler_handler_result(
            BatchDistillationReport(total_candidates=2, distilled_count=1)
        )


def _real_security_snapshot(
    *,
    enabled: bool,
    run_count: int,
    last_run_ts_ms: int | None,
    last_error: str | None = None,
    provider_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = SecuritySnapshot(
        enabled=enabled,
        mode=AgentMode.OBSERVE,
        assets=[],
        incidents=[],
        last_run_ts_ms=last_run_ts_ms,
        run_count=run_count,
        last_error=last_error,
        generated_ts_ms=200,
    ).as_dict()
    value["provider_statuses"] = list(provider_statuses or [])
    return value


@pytest.mark.parametrize(
    ("snapshot", "status", "reason_code"),
    [
        (
            _real_security_snapshot(
                enabled=False,
                run_count=0,
                last_run_ts_ms=None,
            ),
            "SKIPPED",
            "SECURITY_IMMUNE_DISABLED",
        ),
        (
            _real_security_snapshot(
                enabled=True,
                run_count=1,
                last_run_ts_ms=100,
                last_error="provider response lost",
            ),
            "FAILED",
            "SECURITY_IMMUNE_SCAN_FAILED",
        ),
        (
            _real_security_snapshot(
                enabled=True,
                run_count=1,
                last_run_ts_ms=100,
                provider_statuses=[
                    {"provider_id": "wazuh", "status": "degraded"}
                ],
            ),
            "DEGRADED",
            "SECURITY_IMMUNE_PROVIDER_DEGRADED",
        ),
        (
            _real_security_snapshot(
                enabled=True,
                run_count=1,
                last_run_ts_ms=100,
                provider_statuses=[
                    {"provider_id": "local", "status": "ok"},
                    {"provider_id": "defender", "status": "skipped"},
                ],
            ),
            "SUCCEEDED",
            None,
        ),
    ],
)
def test_security_mapper_uses_snapshot_failure_and_provider_semantics(
    snapshot: dict[str, Any],
    status: str,
    reason_code: str | None,
) -> None:
    result = security_immune_scheduler_handler_result(snapshot)
    assert result["status"] == status
    assert result["reason_code"] == reason_code
    assert "assets" not in result
    assert "incidents" not in result
    assert "last_error" not in result


def test_asgi_handlers_use_three_explicit_production_mappers() -> None:
    source = (ROOT / "host_brain/app/asgi.py").read_text(encoding="utf-8")
    assert (
        "return _scheduler_result_wiring.proactive_scheduler_handler_result(result)"
        in source
    )
    assert (
        "return _scheduler_result_wiring.distillation_scheduler_handler_result(report)"
        in source
    )
    assert (
        "return _scheduler_result_wiring.security_immune_scheduler_handler_result(result)"
        in source
    )
    assert "scheduler_handler_result as _scheduler_handler_result" not in source
    wiring = (
        ROOT / "host_brain/app/host_life_scheduler_wiring.py"
    ).read_text(encoding="utf-8")
    assert 'result["status"] = "SUCCEEDED"' not in wiring
