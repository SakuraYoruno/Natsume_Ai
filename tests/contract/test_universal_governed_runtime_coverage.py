from __future__ import annotations

import json
from pathlib import Path

import pytest

from host_brain.release.universal_runtime_coverage import (
    PROGRAM_INCOMPLETE,
    REQUIRED_ARCHITECTURE_CONTROLS,
    REQUIRED_ROUTE_CONTROLS,
    REQUIRED_ROUTES,
    UniversalRuntimeCoverageError,
    load_universal_runtime_coverage,
    require_full_architecture_ready,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "configs/release/universal_governed_runtime_coverage.json"


def test_coverage_reports_actual_incomplete_routes_and_architecture_controls() -> None:
    report = load_universal_runtime_coverage(ROOT)

    assert report.status == PROGRAM_INCOMPLETE
    assert tuple(report.route_statuses) == REQUIRED_ROUTES
    assert report.route_statuses == {
        "chat": "COMPLETE",
        "skill": "COMPLETE",
        "delegate": "INCOMPLETE",
        "plan": "INCOMPLETE",
    }
    assert report.missing_route_controls == {
        "delegate": (
            "budget_enforcement",
            "review_resume",
            "verification",
            "transactional_side_effects",
        ),
        "plan": (
            "node_bound_grant",
            "budget_enforcement",
            "review_resume",
            "verification",
            "transactional_side_effects",
        ),
    }
    assert report.missing_architecture_controls == (
        "generic_three_layer_planning_search",
        "native_multi_node_task_graph",
        "revision_plus_one_replan",
        "composition_without_global_service_locator",
    )


def test_full_architecture_gate_fails_closed_while_any_route_is_missing() -> None:
    with pytest.raises(
        UniversalRuntimeCoverageError, match="FULL_ARCHITECTURE_INCOMPLETE"
    ):
        require_full_architecture_ready(ROOT)


def test_manifest_cannot_call_a_partial_route_complete(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["routes"]["skill"]["controls"]["budget_enforcement"] = "MISSING"
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniversalRuntimeCoverageError, match="controls derive INCOMPLETE"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)


def test_manifest_requires_every_control_for_every_route(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    del payload["routes"]["plan"]["controls"][REQUIRED_ROUTE_CONTROLS[-1]]
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniversalRuntimeCoverageError, match="complete control set"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)


def test_manifest_distinguishes_partial_and_not_wired_from_pass(tmp_path: Path) -> None:
    report = load_universal_runtime_coverage(ROOT)
    assert "generic_three_layer_planning_search" in report.missing_architecture_controls
    assert "revision_plus_one_replan" in report.missing_architecture_controls
    assert "composition_without_global_service_locator" in report.missing_architecture_controls

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["architecture_controls"]["composition_without_global_service_locator"][
        "status"
    ] = "PARTIAL"
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")
    partial_report = load_universal_runtime_coverage(ROOT, manifest_path=candidate)
    assert "composition_without_global_service_locator" in (
        partial_report.missing_architecture_controls
    )

    payload["architecture_controls"]["composition_without_global_service_locator"][
        "status"
    ] = "IN_PROGRESS"
    candidate.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(UniversalRuntimeCoverageError, match="PASS, PARTIAL"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)
