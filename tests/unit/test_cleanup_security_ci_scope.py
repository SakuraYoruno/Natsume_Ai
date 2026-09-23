from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "delivery-gates.yml"
README_PATH = REPO_ROOT / ".github" / "workflows" / "README.md"


def _job_block(workflow: str, name: str, next_name: str = "") -> str:
    start = f"  {name}:\n"
    block = workflow.split(start, 1)[1]
    if not next_name:
        return block
    end = f"\n  {next_name}:\n"
    return block.split(end, 1)[0]


def test_security_tests_are_in_the_required_stable_job() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    stable_job = _job_block(
        workflow,
        "stable-contract-integration-tests",
        "delivery-gate",
    )

    assert "name: stable pytest contract / integration / security" in stable_job
    assert "python -m pytest tests/contract tests/integration tests/security" in stable_job
    assert (
        '-m "not e2e and not hw and not prod_env and not runtime_probe '
        'and not docker and not ci_unstable"'
    ) in stable_job
    assert "continue-on-error:" not in stable_job
    assert "pytest-stable-contract-integration-security.xml" in stable_job

    full_suite = _job_block(workflow, "full-suite")
    assert "continue-on-error: true" in full_suite


def test_ci_documentation_describes_required_security_coverage() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    assert "tests/security` 在同一强制 job 中运行" in readme
