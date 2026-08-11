from __future__ import annotations

import ast
from pathlib import Path

from host_brain.scheduler.command_service import (
    SchedulerCommandService as FocusedSchedulerCommandService,
)
from host_brain.scheduler.governed_runtime import SchedulerCommandService


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_MODULE = ROOT / "host_brain" / "scheduler" / "governed_runtime.py"
COMMAND_MODULE = ROOT / "host_brain" / "scheduler" / "command_service.py"


def _classes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def test_scheduler_command_service_is_focused_and_compatibly_reexported() -> None:
    assert SchedulerCommandService is FocusedSchedulerCommandService
    assert "SchedulerCommandService" not in _classes(RUNTIME_MODULE)
    assert "SchedulerCommandService" in _classes(COMMAND_MODULE)


def test_scheduler_governed_runtime_stays_below_mixed_file_ceiling() -> None:
    source = RUNTIME_MODULE.read_text(encoding="utf-8")
    assert len(source.splitlines()) < 1_500


def test_scheduler_command_module_does_not_import_governed_runtime() -> None:
    source = COMMAND_MODULE.read_text(encoding="utf-8")
    assert "host_brain.scheduler.governed_runtime" not in source
    assert "from .governed_runtime" not in source
