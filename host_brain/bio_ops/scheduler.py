"""In-memory scheduler metadata for Bio-Ops cycles."""

from __future__ import annotations

from host_brain.bio_ops.contracts import BioOpsSchedule
from host_brain.core.ids import now_ms


class BioOpsScheduleRegistry:
    def __init__(self) -> None:
        self._schedules: dict[str, BioOpsSchedule] = {}

    def upsert(self, schedule: BioOpsSchedule) -> BioOpsSchedule:
        self._schedules[schedule.schedule_id] = schedule
        return schedule

    def list_schedules(self) -> list[dict[str, object]]:
        return [schedule.to_dict() for schedule in sorted(self._schedules.values(), key=lambda item: item.schedule_id)]

    def mark_run(self, schedule_id: str, *, next_run_ms: int | None = None) -> BioOpsSchedule | None:
        schedule = self._schedules.get(str(schedule_id or "").strip())
        if schedule is None:
            return None
        updated = BioOpsSchedule(
            schedule_id=schedule.schedule_id,
            capability=schedule.capability,
            cadence=schedule.cadence,
            enabled=schedule.enabled,
            last_run_ms=now_ms(),
            next_run_ms=next_run_ms,
        )
        self._schedules[schedule.schedule_id] = updated
        return updated


__all__ = ["BioOpsScheduleRegistry"]
