from __future__ import annotations

import ast
from pathlib import Path

import host_brain.runtime as runtime
from tools.digital_life_harness import DigitalLifeSupervisor


ROOT = Path(__file__).resolve().parents[2]


def test_legacy_supervisor_is_validation_only_not_runtime_authority() -> None:
    assert "DigitalLifeSupervisor" not in runtime.__all__
    assert not hasattr(runtime, "DigitalLifeSupervisor")
    assert not (ROOT / "host_brain" / "runtime" / "digital_life_supervisor.py").exists()
    assert DigitalLifeSupervisor.__module__ == "tools.digital_life_harness"


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
