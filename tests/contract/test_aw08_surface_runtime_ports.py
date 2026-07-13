from __future__ import annotations

import ast
from pathlib import Path

from host_brain.surface_runtime.ports import SurfaceRuntimePorts


ROOT = Path(__file__).resolve().parents[2]
SURFACE_RUNTIME = ROOT / "host_brain/surface_runtime"


def test_surface_runtime_ports_are_frozen_and_composed_only_by_app_adapter() -> None:
    construction_sites: list[str] = []

    for path in sorted((ROOT / "host_brain").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "SurfaceRuntimePorts"
            ):
                construction_sites.append(path.relative_to(ROOT).as_posix())

    assert SurfaceRuntimePorts.__dataclass_params__.frozen is True
    assert construction_sites == ["host_brain/app/surface_main_chain_adapter.py"]


def test_surface_runtime_has_no_app_or_composition_reverse_imports() -> None:
    violations: list[str] = []

    for path in sorted(SURFACE_RUNTIME.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module.startswith(("host_brain.app", "host_brain.composition")):
                    violations.append(f"{path.name}:{module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("host_brain.app", "host_brain.composition")):
                        violations.append(f"{path.name}:{alias.name}")

    assert violations == []


def test_surface_port_consumers_require_an_explicit_ports_parameter() -> None:
    violations: list[str] = []

    for path in sorted(SURFACE_RUNTIME.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            uses_ports = any(
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id == "ports"
                for child in ast.walk(node)
            )
            if not uses_ports:
                continue
            parameters = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            if "ports" not in {parameter.arg for parameter in parameters}:
                violations.append(f"{path.name}:{node.name}")
                continue
            for parameter, default in zip(
                node.args.kwonlyargs,
                node.args.kw_defaults,
                strict=True,
            ):
                if parameter.arg == "ports" and default is not None:
                    violations.append(f"{path.name}:{node.name}:default")

    assert violations == []
