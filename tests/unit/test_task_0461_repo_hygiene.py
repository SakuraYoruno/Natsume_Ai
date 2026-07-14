from __future__ import annotations

import json
import shlex
import subprocess
import warnings
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIENCE_LINT = REPO_ROOT / "tools" / "doc_lint" / "doc_audience_lint.py"
CANDIDATE_SCORING = (
    REPO_ROOT / "host_brain" / "persona" / "surface" / "candidate_scoring.py"
)

# These existing warnings are registered as separate cleanup debt. TASK-0461 only
# authorizes the candidate_scoring.py fix, so this test must not rewrite them.
DEFERRED_SAME_ESCAPE_WARNINGS = (
    "host_brain/persona/surface/scoring_texture.py",
    "host_brain/persona/surface/scoring/parse.py",
    "host_brain/persona/surface/scoring/texture.py",
)


def _compile_deprecation_warnings(relative_path: str) -> list[str]:
    path = REPO_ROOT / relative_path
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    return [
        str(item.message)
        for item in caught
        if issubclass(item.category, DeprecationWarning)
    ]


def test_doc_audience_pre_commit_hook_uses_real_cli_and_staged_paths() -> None:
    config = yaml.safe_load(
        (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    hooks = [
        hook
        for repository in config["repos"]
        for hook in repository.get("hooks", ())
        if hook.get("id") == "doc-audience-lint"
    ]

    assert AUDIENCE_LINT.is_file()
    assert len(hooks) == 1
    hook = hooks[0]
    assert hook["pass_filenames"] is True
    assert "tools/doc_lint/doc_audience_lint.py" in hook["entry"]
    assert "tools/doc_audience_lint.py" not in hook["entry"]

    # Executing the configured entry with two staged-file arguments proves that
    # filenames become repeatable --path arguments instead of a full-repo scan.
    command = shlex.split(hook["entry"])
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


def test_candidate_scoring_compiles_with_deprecation_warnings_as_errors() -> None:
    # compile() deterministically exercises import-time parsing without relying
    # on whether an existing .pyc happens to suppress the warning.
    source = CANDIDATE_SCORING.read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        compile(source, str(CANDIDATE_SCORING), "exec")


@pytest.mark.parametrize("relative_path", DEFERRED_SAME_ESCAPE_WARNINGS)
def test_other_same_escape_warnings_remain_registered_debt(relative_path: str) -> None:
    messages = _compile_deprecation_warnings(relative_path)
    assert any("invalid escape sequence" in message for message in messages)
