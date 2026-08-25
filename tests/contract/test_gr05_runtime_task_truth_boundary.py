from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST_BRAIN = ROOT / "host_brain"

_REGISTRY_MARKERS = (
    "RuntimeTaskRegistry",
    "runtime_task_registry",
    "get_runtime_task_registry",
    "get_legacy_runtime_task_registry",
)
_CANONICAL_RUNTIME_FILES = (
    "host_brain/agent_runtime/application_ports.py",
    "host_brain/agent_runtime/cortex_runtime.py",
    "host_brain/app/governed_task_adapter.py",
    "host_brain/app/governed_route_executors.py",
    "host_brain/observability/runtime_audit.py",
)


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_process_local_runtime_task_registry_is_deleted() -> None:
    assert not (ROOT / "host_brain/app/runtime_task_registry.py").exists()
    offenders: list[str] = []
    for path in HOST_BRAIN.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if any(marker in source for marker in _REGISTRY_MARKERS):
            offenders.append(_relative(path))

    assert offenders == []

def test_canonical_runtime_object_graph_has_no_legacy_registry_identifier() -> None:
    offenders: list[str] = []
    forbidden = set(_REGISTRY_MARKERS)
    for relative in _CANONICAL_RUNTIME_FILES:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        leaked = sorted(identifiers & forbidden)
        if leaked:
            offenders.append(f"{relative}: {','.join(leaked)}")

    assert offenders == []
