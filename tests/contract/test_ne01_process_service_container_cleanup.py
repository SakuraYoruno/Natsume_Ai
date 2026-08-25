"""NE-01 contract for physical production locator retirement."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST_ROOT = ROOT / "host_brain"
RETIRED_PRODUCTION_MODULES = (
    "host_brain/composition/process_service_container.py",
    "host_brain/composition/process_services.py",
    "host_brain/composition/process_environment.py",
    "host_brain/app/process_service_wiring.py",
)
RETIRED_IMPORT_MODULES = frozenset(
    {
        "host_brain.composition.process_service_container",
        "host_brain.composition.process_services",
        "host_brain.composition.process_environment",
        "host_brain.app.process_service_wiring",
    }
)


def _retired_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            findings.extend(
                alias.name
                for alias in node.names
                if alias.name in RETIRED_IMPORT_MODULES
            )
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            if module in RETIRED_IMPORT_MODULES:
                findings.append(module)
    return findings


def test_ne01_production_process_locator_modules_are_physically_deleted() -> None:
    assert [
        relative
        for relative in RETIRED_PRODUCTION_MODULES
        if (ROOT / relative).exists()
    ] == []


def test_ne01_production_tree_has_no_retired_process_locator_imports() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): _retired_imports(path)
        for path in HOST_ROOT.rglob("*.py")
        if _retired_imports(path)
    }

    assert violations == {}


def test_ne01_test_compatibility_bridge_is_retired() -> None:
    bridge = ROOT / "tests" / "support" / "process_service_compat.py"

    assert not bridge.exists()


def test_ne01_security_immune_module_has_no_global_agent_accessor() -> None:
    from host_brain.security_immune import agent

    assert not hasattr(agent, "get_security_immune_agent")
    assert not hasattr(agent, "_global_security_immune_agent")
