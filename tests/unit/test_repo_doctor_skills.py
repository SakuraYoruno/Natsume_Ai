from __future__ import annotations

from pathlib import Path

from host_brain.skills.impl.repo_executor import execute_repo_skill
from host_brain.skills.repo_registry import RepoSkillProvider, build_repo_skill_registry


def test_repo_health_scan_reports_repo_shape_without_running_tests(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("demo", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")

    provider = RepoSkillProvider()
    payload = provider.repo_health_scan(
        {
            "repo_root": str(tmp_path),
            "include_git": False,
            "include_tests": True,
            "include_python": True,
            "run_tests": False,
        },
        {"request_id": "req-repo-health"},
    )

    result = execute_repo_skill(payload, payload["_context"])
    detail = result["detail"]

    assert result["status"] == "OK"
    assert detail["health_status"] in {"PASS", "CHECK"}
    assert detail["files"]["has_readme"] is True
    assert detail["tests"]["test_file_count"] == 1
    assert detail["tests"]["has_pytest_config"] is True
    assert "test_run" not in detail


def test_test_failure_triage_extracts_failed_tests_and_recommendations() -> None:
    provider = RepoSkillProvider()
    output = "\n".join(
        [
            "FAILED tests/unit/test_demo.py::test_contract - AssertionError",
            "tests/unit/test_demo.py:12: AssertionError",
            "E   AssertionError: expected OK",
            "1 failed, 4 passed in 0.12s",
        ]
    )
    payload = provider.test_failure_triage(
        {
            "output": output,
            "command": "pytest -q tests/unit/test_demo.py",
            "max_failures": 5,
        },
        {"request_id": "req-test-triage"},
    )

    result = execute_repo_skill(payload, payload["_context"])
    detail = result["detail"]

    assert result["status"] == "OK"
    assert detail["has_failures"] is True
    assert detail["failed_tests"] == ["tests/unit/test_demo.py::test_contract"]
    assert "AssertionError" in detail["error_types"]
    assert detail["summary_counts"]["failed"] == 1
    assert detail["summary_counts"]["passed"] == 4
    assert detail["file_hotspots"][0]["path"] == "tests/unit/test_demo.py"
    assert any("AssertionError" in item for item in detail["recommendations"])


def test_repo_registry_exposes_low_risk_doctor_skills() -> None:
    registry = build_repo_skill_registry()

    health_meta = registry.require("repo_health_scan")
    triage_meta = registry.require("test_failure_triage")

    assert health_meta.risk_level == 1
    assert health_meta.require_confirm is False
    assert health_meta.family == "code"
    assert "repo" in health_meta.capability_tags
    assert triage_meta.risk_level == 1
    assert triage_meta.require_confirm is False
    assert triage_meta.family == "code"
    assert "triage" in triage_meta.capability_tags
