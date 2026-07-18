from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RETIRED_MODULES = (
    "host_brain/app/obs_routes_task_runtime.py",
    "host_brain/app/observability_runtime/health_device_projection.py",
    "host_brain/app/observability_runtime/process_state_projection.py",
    "host_brain/app/observability_runtime/projection_runtime.py",
    "host_brain/app/observability_runtime/session_query.py",
)
RETIRED_ZERO_ARGUMENT_REFRESHERS = (
    "get_query_projection_worker",
    "refresh_task_runtime_query_projections",
    "refresh_health_query_projections",
    "refresh_device_status_query_projections",
    "refresh_organ_tribe_query_projection",
    "refresh_session_query_projection",
    "refresh_insights_summary_projection",
    "refresh_digital_life_overview_projection",
    "refresh_resource_manager_projection",
)


def _process_locator_inventory() -> tuple[int, int]:
    modules: set[str] = set()
    imports = 0
    for path in (ROOT / "host_brain").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "host_brain.composition.process_services"
            ):
                modules.add(path.relative_to(ROOT).as_posix())
                imports += len(node.names)
            elif isinstance(node, ast.Import) and any(
                alias.name == "host_brain.composition.process_services"
                for alias in node.names
            ):
                modules.add(path.relative_to(ROOT).as_posix())
                imports += 1
    return len(modules), imports


def test_obs_projection_modules_have_no_process_service_locator() -> None:
    combined = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8-sig")
        for relative in RETIRED_MODULES
    )
    assert "host_brain.composition.process_services" not in combined
    assert "get_process_service_container" not in combined


def test_dead_zero_argument_projection_refreshers_are_removed() -> None:
    combined = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8-sig")
        for relative in RETIRED_MODULES
    )
    for symbol in RETIRED_ZERO_ARGUMENT_REFRESHERS:
        assert f"def {symbol}(" not in combined


def test_process_locator_inventory_only_moves_down() -> None:
    modules, imports = _process_locator_inventory()
    assert modules <= 45
    assert imports <= 147
