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
    assert report.evaluated_commit_sha == "f379d6dbe34f9ef5946bb812c430467d36c6f86d"
    assert report.migration_head == "0118_conversation_acceptance_effects.sql"
    assert tuple(report.route_statuses) == REQUIRED_ROUTES
    assert report.route_statuses == {
        "chat": "INCOMPLETE",
        "skill": "INCOMPLETE",
        "plan": "INCOMPLETE",
        "delegate": "INCOMPLETE",
        "scheduler": "INCOMPLETE",
        "internal": "INCOMPLETE",
        "operator": "INCOMPLETE",
    }
    assert report.missing_route_controls == {
        "chat": (
            "budget_enforcement",
            "transactional_side_effects",
            "commit_bound_acceptance",
        ),
        "skill": (
            "canonical_execution_authority",
            "budget_enforcement",
            "transactional_side_effects",
            "commit_bound_acceptance",
        ),
        "plan": (
            "canonical_execution_authority",
            "node_bound_grant",
            "budget_enforcement",
            "review_resume",
            "verification",
            "transactional_side_effects",
            "commit_bound_acceptance",
        ),
        "delegate": (
            "budget_enforcement",
            "review_resume",
            "verification",
            "transactional_side_effects",
            "commit_bound_acceptance",
        ),
        "scheduler": (
            "canonical_execution_authority",
            "budget_enforcement",
            "verification",
            "transactional_side_effects",
            "commit_bound_acceptance",
        ),
        "internal": (
            "canonical_execution_authority",
            "budget_enforcement",
            "transactional_side_effects",
            "commit_bound_acceptance",
        ),
        "operator": (
            "host_command",
            "immutable_task_graph",
            "canonical_execution_authority",
            "durable_attempt_lease",
            "node_bound_grant",
            "budget_enforcement",
            "review_resume",
            "verification",
            "acceptance",
            "receipt",
            "transactional_side_effects",
            "commit_bound_acceptance",
        ),
    }
    assert report.missing_architecture_controls == (
        "generic_three_layer_planning_search",
        "native_multi_node_task_graph",
        "visual_agent_direct_execution_authority",
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
    payload["routes"]["skill"]["status"] = "COMPLETE"
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


def test_manifest_requires_all_seven_production_routes(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    del payload["routes"]["scheduler"]
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniversalRuntimeCoverageError, match="contain exactly"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)


def test_manifest_rejects_invalid_evidence_commit_sha(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["evidence_binding"]["evaluated_commit_sha"] = "f379d6dbe"
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniversalRuntimeCoverageError, match="full lowercase Git SHA"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)


def test_manifest_rejects_unknown_evidence_commit(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["evidence_binding"]["evaluated_commit_sha"] = "0" * 40
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniversalRuntimeCoverageError, match="not available"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)


def test_manifest_rejects_stale_migration_head(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["evidence_binding"][
        "migration_head"
    ] = "0115_host_hard_state_journal.sql"
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniversalRuntimeCoverageError, match="repository migration head"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)


def test_route_cannot_pass_commit_bound_acceptance_without_receipt_binding(
    tmp_path: Path,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["routes"]["chat"]["controls"]["budget_enforcement"] = "PASS"
    payload["routes"]["chat"]["controls"]["transactional_side_effects"] = "PASS"
    payload["routes"]["chat"]["controls"]["commit_bound_acceptance"] = "PASS"
    payload["routes"]["chat"]["status"] = "COMPLETE"
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniversalRuntimeCoverageError, match="without acceptance_binding"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)


def test_route_can_pass_only_with_exact_commit_and_migration_receipt(
    tmp_path: Path,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["routes"]["chat"]["controls"]["budget_enforcement"] = "PASS"
    payload["routes"]["chat"]["controls"]["transactional_side_effects"] = "PASS"
    payload["routes"]["chat"]["controls"]["commit_bound_acceptance"] = "PASS"
    payload["routes"]["chat"]["status"] = "COMPLETE"
    payload["routes"]["chat"]["acceptance_binding"] = {
        "receipt_ref": "tests/fixtures/release/universal_runtime_acceptance_v2.json"
    }
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    report = load_universal_runtime_coverage(ROOT, manifest_path=candidate)

    assert report.route_statuses["chat"] == "COMPLETE"
    assert (
        "tests/fixtures/release/universal_runtime_acceptance_v2.json"
        in report.evidence_refs
    )


def test_historical_four_route_receipt_cannot_satisfy_current_commit_binding(
    tmp_path: Path,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["routes"]["chat"]["controls"]["budget_enforcement"] = "PASS"
    payload["routes"]["chat"]["controls"]["transactional_side_effects"] = "PASS"
    payload["routes"]["chat"]["controls"]["commit_bound_acceptance"] = "PASS"
    payload["routes"]["chat"]["status"] = "COMPLETE"
    payload["routes"]["chat"]["acceptance_binding"] = {
        "receipt_ref": (
            "docs/45_testing/test_reports/"
            "gr06_universal_governed_runtime_live_acceptance_20260715.json"
        )
    }
    candidate = tmp_path / "coverage.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniversalRuntimeCoverageError, match="commit SHA does not match"):
        load_universal_runtime_coverage(ROOT, manifest_path=candidate)


def test_manifest_distinguishes_partial_and_not_wired_from_pass(tmp_path: Path) -> None:
    report = load_universal_runtime_coverage(ROOT)
    assert "generic_three_layer_planning_search" in report.missing_architecture_controls
    assert "visual_agent_direct_execution_authority" in report.missing_architecture_controls
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
