"""Negative regression tests for the removed Executive planning authority."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from host_brain.app import executive_runtime


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_legacy_executive_planning_modules_are_physically_retired() -> None:
    for relative in (
        "host_brain/executive_runtime/composition.py",
        "host_brain/executive_runtime/conversation_execution.py",
        "host_brain/executive_runtime/route_planning.py",
        "host_brain/executive_runtime/skill_routing.py",
        "host_brain/executive_runtime/ports.py",
        "host_brain/app/runtime_port_wiring.py",
        "host_brain/app/tool_execution_matrix.py",
    ):
        assert not (ROOT / relative).exists(), relative


def test_retired_executive_namespace_has_no_composition_or_override_authority() -> None:
    package_source = _source("host_brain/executive_runtime/__init__.py")
    app_source = _source("host_brain/app/executive_runtime.py")
    skill_source = _source("host_brain/executive_runtime/skill_execution.py")
    forbidden = (
        "build_executive_runtime_application_ports",
        "get_runtime_route_override",
        "maybe_auto_plan_for_gap",
        "_maybe_synthesize_runtime_capability",
        "_build_failure_recovery_plan",
    )
    assert "from . import composition" not in package_source
    assert all(marker not in app_source for marker in forbidden)
    assert all(marker not in skill_source for marker in forbidden[1:])


def test_governed_adapter_uses_planning_search_not_executive_planning() -> None:
    path = ROOT / "host_brain/app/governed_task_adapter.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "host_brain.orchestration.plan_intent_search" in imported_modules
    assert "host_brain.orchestration.planning_graph_compiler" in imported_modules
    assert not any(module.startswith("host_brain.executive_runtime") for module in imported_modules)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ("chat", "skill", "delegate", "plan"))
async def test_stale_app_entry_fails_closed_without_executive_ports(route: str) -> None:
    stream = executive_runtime.executive_runtime_sse_generator(
        request_id=f"request-retired-{route}",
        intent_draft={"route": route},
    )
    with pytest.raises(
        PermissionError,
        match="CANONICAL_ROUTE_REQUIRES_GOVERNED_TASK_RUNTIME",
    ):
        await anext(stream)
