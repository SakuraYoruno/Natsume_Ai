from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIENCE_LINT = REPO_ROOT / "tools" / "doc_lint" / "doc_audience_lint.py"
CANDIDATE_SCORING = (
    REPO_ROOT / "host_brain" / "persona" / "surface" / "candidate_scoring.py"
)

def _doc_audience_hook() -> dict[str, object]:
    config = yaml.safe_load(
        (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    hooks = [
        hook
        for repository in config["repos"]
        for hook in repository.get("hooks", ())
        if hook.get("id") == "doc-audience-lint"
    ]
    assert len(hooks) == 1
    return hooks[0]


def test_doc_audience_pre_commit_hook_uses_real_cli_and_staged_paths() -> None:
    hook = _doc_audience_hook()

    assert AUDIENCE_LINT.is_file()
    assert hook["pass_filenames"] is True
    assert "tools/doc_lint/doc_audience_lint.py" in str(hook["entry"])
    assert "tools/doc_audience_lint.py" not in str(hook["entry"])

    command = shlex.split(str(hook["entry"]))
    command.extend(("docs/30_devspec/DevSpec.md", "docs/40_agents/AGENTS.md"))
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert json.loads(completed.stdout)["status"] == "passed"


def test_doc_audience_hook_skips_negative_fixtures_when_paths_are_explicit() -> None:
    hook = _doc_audience_hook()
    command = shlex.split(str(hook["entry"]))
    command.extend(
        (
            "tests/fixtures/ci/doc_gate/bad/missing_audience.md",
            "tests/fixtures/ci/doc_gate/bad/missing_title.md",
        )
    )

    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert json.loads(completed.stdout)["status"] == "passed"


def test_candidate_scoring_compiles() -> None:
    source = CANDIDATE_SCORING.read_text(encoding="utf-8")
    compile(source, str(CANDIDATE_SCORING), "exec")
