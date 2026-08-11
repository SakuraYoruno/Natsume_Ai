from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.scheduler.accepted_effect_runtime import (
    SchedulerAcceptedEffectRuntime,
)


def _accepted_event() -> dict[str, Any]:
    return {
        "done": True,
        "execution_receipt": {
            "status": "ACCEPTED",
            "accepted": True,
            "projection": {},
        },
    }


class _Dispatcher:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def dispatch_graph(self, **_kwargs: Any) -> tuple[dict[str, Any], ...]:
        return tuple(self.rows)


class _Results:
    def __init__(self, value: Any = None, *, error: Exception | None = None) -> None:
        self.value = value
        self.error = error

    def get_by_command(self, _command_id: str) -> Any:
        if self.error is not None:
            raise self.error
        return self.value


class _Occurrences:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def get_occurrence(self, _occurrence_id: str) -> Any:
        return self.value


def _runtime(*, rows: list[dict[str, Any]], result: Any, occurrence: Any):
    return SchedulerAcceptedEffectRuntime(
        dispatcher=_Dispatcher(rows),
        result_repository=result if isinstance(result, _Results) else _Results(result),
        occurrence_repository=_Occurrences(occurrence),
    )


@pytest.mark.asyncio
async def test_committed_effect_reopens_exact_result_and_occurrence() -> None:
    receipt_ref = "schedulerresult://sha256/" + "a" * 64
    runtime = _runtime(
        rows=[{"effect_id": "acceptance-effect://scheduler/1", "status": "COMMITTED"}],
        result={
            "occurrence_id": "scheduleroccurrence://sha256/" + "b" * 64,
            "receipt_ref": receipt_ref,
            "result": {"status": "SUCCEEDED", "summary": "done"},
        },
        occurrence={
            "occurrence_id": "scheduleroccurrence://sha256/" + "b" * 64,
            "task_id": "life_tick",
        },
    )

    projected = await runtime.dispatch(
        _accepted_event(),
        command=SimpleNamespace(
            command_id="host-command://scheduler/1",
            graph_id="graph-scheduler-1",
            graph_revision=1,
        ),
    )

    assert projected["scheduler_effect_status"] == "COMMITTED"
    assert projected["scheduler_result_receipt_ref"] == receipt_ref
    assert projected["task_id"] == "life_tick"


@pytest.mark.asyncio
async def test_committed_effect_with_unavailable_result_stays_recoverable() -> None:
    runtime = _runtime(
        rows=[{"effect_id": "acceptance-effect://scheduler/2", "status": "COMMITTED"}],
        result=_Results(error=OSError("temporary read failure")),
        occurrence=None,
    )

    projected = await runtime.dispatch(
        _accepted_event(),
        command=SimpleNamespace(
            command_id="host-command://scheduler/2",
            graph_id="graph-scheduler-2",
            graph_revision=1,
        ),
    )

    assert projected["scheduler_effect_status"] == "RECOVERY_REQUIRED"
    assert projected["reason_code"] == (
        "SCHEDULER_EFFECT_RESULT_ATTRIBUTION_UNAVAILABLE"
    )


@pytest.mark.asyncio
async def test_unknown_effect_never_projects_handler_success() -> None:
    runtime = _runtime(
        rows=[
            {
                "effect_id": "acceptance-effect://scheduler/3",
                "status": "PENDING",
                "dispatch_state": "RECOVERY_PENDING",
                "recovery_pending": True,
            }
        ],
        result=None,
        occurrence=None,
    )

    projected = await runtime.dispatch(
        _accepted_event(),
        command=SimpleNamespace(
            command_id="host-command://scheduler/3",
            graph_id="graph-scheduler-3",
            graph_revision=1,
        ),
    )

    assert projected["scheduler_effect_status"] == "RECOVERY_REQUIRED"
    assert "scheduler_result_receipt_ref" not in projected
    assert projected["audit_refs"]["acceptance_effects"]["recovery_pending"] == 1
