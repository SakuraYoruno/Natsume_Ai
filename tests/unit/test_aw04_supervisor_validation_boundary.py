from __future__ import annotations

import ast
from pathlib import Path

import host_brain.runtime as runtime
from host_brain.validation import DigitalLifeSupervisor
from host_brain.persona.inner_world import OfflineInnerWorld


ROOT = Path(__file__).resolve().parents[2]


def test_legacy_supervisor_is_validation_only_not_runtime_authority() -> None:
    assert "DigitalLifeSupervisor" not in runtime.__all__
    assert not hasattr(runtime, "DigitalLifeSupervisor")
    assert not (ROOT / "host_brain" / "runtime" / "digital_life_supervisor.py").exists()
    assert DigitalLifeSupervisor.__module__ == "host_brain.validation.digital_life_supervisor"


def test_production_composition_does_not_import_validation_harness() -> None:
    production_roots = (
        ROOT / "host_brain" / "app",
        ROOT / "host_brain" / "composition",
        ROOT / "host_brain" / "scheduler",
    )
    violations: list[str] = []
    for root in production_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = str(node.module or "")
                elif isinstance(node, ast.Import):
                    module = " ".join(alias.name for alias in node.names)
                if "host_brain.validation" in module or "digital_life_supervisor" in module:
                    violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_offline_inner_world_is_transient_by_default(tmp_path) -> None:
    state_dir = tmp_path / "retired-inner-world-writer"
    world = OfflineInnerWorld(state_dir=str(state_dir))

    world.tick("person-1", emit_inner_stream=False)

    assert state_dir.exists() is False
