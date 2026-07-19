from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "host_brain" / "app"
CONTAINER_PATH = APP_ROOT / "obs_application.py"
LIFESPAN_PATH = APP_ROOT / "obs_fastapi_app.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _owner_function(tree: ast.Module, target: ast.AST) -> str | None:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    current = target
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def test_projection_jobs_have_one_explicit_app_lifespan_start_owner() -> None:
    calls: list[tuple[Path, int, str | None]] = []
    for path in APP_ROOT.rglob("*.py"):
        tree = _tree(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "start_projection_jobs"
            ):
                calls.append(
                    (
                        path.relative_to(ROOT),
                        node.lineno,
                        _owner_function(tree, node),
                    )
                )

    assert len(calls) == 1
    path, _line, owner = calls[0]
    assert path == Path("host_brain/app/obs_fastapi_app.py")
    assert owner == "lifespan"


def test_obs_container_exposes_no_ambiguous_generic_start_authority() -> None:
    tree = _tree(CONTAINER_PATH)
    container = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ObsApplicationContainer"
    )
    methods = {
        node.name
        for node in container.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "start_projection_jobs" in methods
    assert "start" not in methods
    assert "close" in methods

    ports = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ObsApplicationPorts"
    )
    port_methods = {
        node.name
        for node in ports.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "claim_lifecycle_owner" in port_methods

    lifespan_source = LIFESPAN_PATH.read_text(encoding="utf-8-sig")
    assert "ports_consumption_lock" not in lifespan_source
    assert "consumed_ports" not in lifespan_source


def test_obs_lifespan_starts_before_yield_and_closes_in_finally() -> None:
    tree = _tree(LIFESPAN_PATH)
    lifespan = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    start_calls = [
        node
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "start_projection_jobs"
    ]
    close_calls = [
        node
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "container"
    ]
    yields = [node for node in ast.walk(lifespan) if isinstance(node, ast.Yield)]

    assert len(start_calls) == len(close_calls) == len(yields) == 1
    assert start_calls[0].lineno < yields[0].lineno < close_calls[0].lineno
    assert any(
        isinstance(node, ast.Try)
        and any(
            close_calls[0] in ast.walk(statement)
            for statement in node.finalbody
        )
        for node in ast.walk(lifespan)
    )
