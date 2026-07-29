from __future__ import annotations

import pytest

from host_brain.app.host_life_scheduler_wiring import (
    RETIRED_LEGACY_SCHEDULER_TASK_IDS,
)
from host_brain.composition.scheduler import (
    SCHEDULER_COMPOSITION_INVALID,
    SchedulerCompositionError,
    _disable_retired_scheduler_tasks,
)
from host_brain.composition.tool_runtime import _PERSONALITY_TOOL_DEFS
from host_brain.skills.executor_registry import build_builtin_tool_executor_registry
from host_brain.skills.scheduler_registry import _normalize_scheduler_add


class _Definitions:
    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = {task_id: dict(row) for task_id, row in rows.items()}
        self.calls: list[dict] = []

    def get(self, task_id: str) -> dict | None:
        row = self.rows.get(task_id)
        return None if row is None else dict(row)

    def set_enabled(
        self,
        *,
        task_id: str,
        enabled: bool,
        expected_head_version: int,
    ) -> dict:
        current = self.rows[task_id]
        self.calls.append(
            {
                "task_id": task_id,
                "enabled": enabled,
                "expected_head_version": expected_head_version,
            }
        )
        if current["head_version"] != expected_head_version:
            raise RuntimeError("stale")
        current["enabled"] = bool(enabled)
        current["head_version"] += 1
        return dict(current)


def test_startup_disables_pre_cutover_proactive_definition_with_cas() -> None:
    definitions = _Definitions(
        {
            "proactive_talk_periodic": {
                "enabled": True,
                "retired": False,
                "head_version": 7,
            },
            "life_tick": {
                "enabled": True,
                "retired": False,
                "head_version": 3,
            },
        }
    )

    disabled = _disable_retired_scheduler_tasks(
        definitions=definitions,
        task_ids=RETIRED_LEGACY_SCHEDULER_TASK_IDS,
    )

    assert disabled == ("proactive_talk_periodic",)
    assert definitions.rows["proactive_talk_periodic"]["enabled"] is False
    assert definitions.rows["life_tick"]["enabled"] is True
    assert definitions.calls == [
        {
            "task_id": "proactive_talk_periodic",
            "enabled": False,
            "expected_head_version": 7,
        }
    ]


def test_legacy_proactive_cannot_reenter_through_catalog_executor_or_new_scheduler_task() -> None:
    assert "proactive_talk" not in {
        str(row[0]) for row in _PERSONALITY_TOOL_DEFS
    }
    assert "proactive_talk" not in build_builtin_tool_executor_registry()._executor_ids_unchecked()
    normalized = _normalize_scheduler_add(
        {"task_name": "legacy", "task_type": "proactive_talk"},
        None,
    )
    assert normalized["task_type"] == "custom"


def test_startup_refuses_to_continue_when_legacy_disable_loses_nonterminal_race() -> None:
    class _RacingDefinitions(_Definitions):
        def set_enabled(self, **_kwargs):  # type: ignore[no-untyped-def]
            self.rows["proactive_talk_periodic"]["head_version"] = 8
            raise RuntimeError("concurrent mutation")

    definitions = _RacingDefinitions(
        {
            "proactive_talk_periodic": {
                "enabled": True,
                "retired": False,
                "head_version": 7,
            },
        }
    )

    with pytest.raises(SchedulerCompositionError) as exc_info:
        _disable_retired_scheduler_tasks(
            definitions=definitions,
            task_ids=RETIRED_LEGACY_SCHEDULER_TASK_IDS,
        )

    assert exc_info.value.reason_code == SCHEDULER_COMPOSITION_INVALID
