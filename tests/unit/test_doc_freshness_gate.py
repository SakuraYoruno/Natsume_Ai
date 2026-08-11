from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "doc_lint" / "doc_freshness_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("doc_freshness_gate_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load freshness gate: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


freshness = _load_module()


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=command_env,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Freshness Gate Test")
    _git(repo, "config", "user.email", "freshness@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    return repo


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit_all(repo: Path, message: str, *, date: str | None = None) -> None:
    _git(repo, "add", "-A")
    env = None
    if date:
        env = {
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
        }
    _git(repo, "commit", "-q", "-m", message, env=env)


def _timestamp(path: Path) -> float:
    return float(path.stat().st_mtime)


def _best_timestamp(path: Path, repo: Path, dirty_paths: set[str]) -> float | None:
    return freshness._best_effort_timestamp(
        path,
        repo,
        dirty_paths=dirty_paths,
    )


def _make_stale_repo(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repo = _init_repo(tmp_path)
    doc = repo / "docs" / "target doc.md"
    source = repo / "src" / "source file.py"
    policy_path = repo / "freshness policy.json"
    _write(doc, "old documentation\n")
    _write(source, "VALUE = 1\n")
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "test-policy",
                "default_max_age_days": 1,
                "rules": [
                    {
                        "id": "target-doc",
                        "doc": "docs/target doc.md",
                        "max_age_days": 1,
                        "sources": ["src/source file.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _commit_all(repo, "old baseline", date="2000-01-01T00:00:00+0000")

    _write(source, "VALUE = 2\n")
    _commit_all(repo, "new source", date="2000-01-11T00:00:00+0000")
    return repo, doc, source, policy_path


def test_clean_tracked_path_uses_git_timestamp(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    path = repo / "docs" / "clean tracked.md"
    _write(path, "unchanged\n")
    _commit_all(repo, "baseline")

    git_timestamp = freshness._git_timestamp(path, repo)
    assert git_timestamp is not None
    newer_mtime = git_timestamp + 3600
    os.utime(path, (newer_mtime, newer_mtime))

    dirty_paths = freshness._collect_git_dirty_paths(repo)
    assert freshness._to_rel_lower(path, repo) not in dirty_paths
    assert _best_timestamp(path, repo, dirty_paths) == pytest.approx(git_timestamp)


def test_modified_tracked_path_uses_filesystem_mtime(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    path = repo / "docs" / "modified tracked.md"
    _write(path, "baseline\n")
    _commit_all(repo, "baseline")

    _write(path, "modified working tree content\n")
    dirty_paths = freshness._collect_git_dirty_paths(repo)

    assert freshness._to_rel_lower(path, repo) in dirty_paths
    assert _best_timestamp(path, repo, dirty_paths) == pytest.approx(_timestamp(path))


def test_staged_tracked_path_uses_filesystem_mtime(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    path = repo / "docs" / "staged tracked.md"
    _write(path, "baseline\n")
    _commit_all(repo, "baseline")

    _write(path, "staged content\n")
    _git(repo, "add", "--", "docs/staged tracked.md")
    dirty_paths = freshness._collect_git_dirty_paths(repo)

    assert freshness._to_rel_lower(path, repo) in dirty_paths
    assert _best_timestamp(path, repo, dirty_paths) == pytest.approx(_timestamp(path))


def test_untracked_path_with_spaces_uses_filesystem_mtime(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    tracked = repo / "README.md"
    _write(tracked, "baseline\n")
    _commit_all(repo, "baseline")

    path = repo / "docs" / "untracked report with spaces.md"
    _write(path, "new report\n")
    dirty_paths = freshness._collect_git_dirty_paths(repo)

    assert freshness._to_rel_lower(path, repo) in dirty_paths
    assert _best_timestamp(path, repo, dirty_paths) == pytest.approx(_timestamp(path))


def test_rename_tracks_both_paths_without_splitting_spaces(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    old_path = repo / "docs" / "original report name.md"
    new_path = repo / "docs" / "renamed report name.md"
    _write(old_path, "baseline\n")
    _commit_all(repo, "baseline")

    _git(
        repo,
        "mv",
        "--",
        "docs/original report name.md",
        "docs/renamed report name.md",
    )
    dirty_paths = freshness._collect_git_dirty_paths(repo)

    assert freshness._to_rel_lower(old_path, repo) in dirty_paths
    assert freshness._to_rel_lower(new_path, repo) in dirty_paths
    assert _best_timestamp(new_path, repo, dirty_paths) == pytest.approx(
        _timestamp(new_path)
    )


def test_build_report_still_rejects_clean_stale_document(tmp_path: Path) -> None:
    repo, _doc, source, policy_path = _make_stale_repo(tmp_path)

    report = freshness.build_report(
        repo,
        config=policy_path,
        changed_files=[source.relative_to(repo)],
    )

    assert report["status"] == "failed"
    assert report["reason_code"] == freshness.REASON_DOC_FRESHNESS_STALE
    assert report["summary"]["failure_count"] == 1
    failure = report["failures"][0]
    assert failure["max_age_days"] == 1
    assert failure["rule_id"] == "target-doc"


def test_change_contract_rejects_code_without_documentation_in_same_change(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "src" / "runtime.py"
    doc = repo / "docs" / "runtime.md"
    policy_path = repo / "freshness.json"
    _write(source, "VALUE = 1\n")
    _write(doc, "baseline\n")
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "change_contract": {
                    "required": True,
                    "sources": ["src/*"],
                    "documentation": ["docs/*"],
                },
                "rules": [],
            }
        ),
        encoding="utf-8",
    )
    _commit_all(repo, "baseline")

    report = freshness.build_report(
        repo,
        config=policy_path,
        changed_files=[source.relative_to(repo)],
    )

    assert report["status"] == "failed"
    assert report["reason_code"] == freshness.REASON_DOC_CHANGE_REQUIRED
    assert report["summary"]["change_contract_triggered"] is True
    assert report["summary"]["changed_documentation_count"] == 0


def test_change_contract_accepts_documentation_in_same_change(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "src" / "runtime.py"
    doc = repo / "docs" / "runtime.md"
    policy_path = repo / "freshness.json"
    _write(source, "VALUE = 1\n")
    _write(doc, "baseline\n")
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "change_contract": {
                    "required": True,
                    "sources": ["src/*"],
                    "documentation": ["docs/*"],
                },
                "rules": [],
            }
        ),
        encoding="utf-8",
    )
    _commit_all(repo, "baseline")

    report = freshness.build_report(
        repo,
        config=policy_path,
        changed_files=[source.relative_to(repo), doc.relative_to(repo)],
    )

    assert report["status"] == "passed"
    assert report["summary"]["change_contract_triggered"] is True
    assert report["summary"]["changed_documentation_count"] == 1


def test_required_change_contract_rejects_empty_patterns(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    policy_path = repo / "freshness.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "change_contract": {
                    "required": True,
                    "sources": [],
                    "documentation": [],
                },
                "rules": [],
            }
        ),
        encoding="utf-8",
    )
    _commit_all(repo, "baseline")

    report = freshness.build_report(repo, config=policy_path)

    assert report["status"] == "failed"
    assert report["reason_code"] == freshness.REASON_DOC_FRESHNESS_CONFIG_INVALID
    assert report["summary"]["change_contract_valid"] is False


def test_modified_document_mtime_is_used_by_build_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, doc, source, policy_path = _make_stale_repo(tmp_path)
    source_timestamp = freshness._git_timestamp(source, repo)
    assert source_timestamp is not None

    _write(doc, "fresh uncommitted documentation\n")
    fresh_timestamp = source_timestamp + 3600
    os.utime(doc, (fresh_timestamp, fresh_timestamp))

    calls = 0
    collect_dirty_paths = freshness._collect_git_dirty_paths

    def counted_collect_dirty_paths(root: Path) -> set[str]:
        nonlocal calls
        calls += 1
        return collect_dirty_paths(root)

    monkeypatch.setattr(
        freshness,
        "_collect_git_dirty_paths",
        counted_collect_dirty_paths,
    )
    report = freshness.build_report(
        repo,
        config=policy_path,
        changed_files=[source.relative_to(repo)],
    )

    assert calls == 1
    assert report["status"] == "passed"
    assert report["summary"]["failure_count"] == 0


def test_scoped_report_only_timestamps_triggering_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    doc = repo / "docs" / "target.md"
    changed_source = repo / "src" / "changed.py"
    untouched_source = repo / "src" / "untouched.py"
    policy_path = repo / "freshness.json"
    _write(doc, "documentation\n")
    _write(changed_source, "CHANGED = 1\n")
    _write(untouched_source, "UNTOUCHED = 1\n")
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "scoped-policy",
                "default_max_age_days": 30,
                "rules": [
                    {
                        "id": "target",
                        "doc": "docs/target.md",
                        "sources": ["src/*.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _commit_all(repo, "baseline")

    seen: list[Path] = []
    real_timestamp = freshness._best_effort_timestamp

    def recording_timestamp(path: Path, root: Path, *, dirty_paths: set[str]):
        seen.append(path.resolve())
        return real_timestamp(path, root, dirty_paths=dirty_paths)

    monkeypatch.setattr(freshness, "_best_effort_timestamp", recording_timestamp)
    report = freshness.build_report(
        repo,
        config=policy_path,
        changed_files=[changed_source.relative_to(repo)],
    )

    assert report["status"] == "passed"
    assert doc.resolve() in seen
    assert changed_source.resolve() in seen
    assert untouched_source.resolve() not in seen


def test_check_all_uses_batched_git_pathspec_timestamp(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    first = repo / "src" / "first.py"
    second = repo / "src" / "nested" / "second.py"
    _write(first, "FIRST = 1\n")
    _commit_all(repo, "first", date="2000-01-01T00:00:00+0000")
    _write(second, "SECOND = 1\n")
    _commit_all(repo, "second", date="2000-01-02T00:00:00+0000")

    latest = freshness._git_latest_timestamp_for_patterns(
        repo,
        ["src/**/*.py"],
    )

    assert latest is not None
    assert latest[0] == "src/nested/second.py"
    assert latest[1] == freshness._git_timestamp(second, repo)
