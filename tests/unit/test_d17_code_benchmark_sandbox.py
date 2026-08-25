from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from host_brain.contracts.planning_search.benchmarking import (
    BenchmarkArm,
    DomainBenchmarkWorkload,
)
from host_brain.contracts.planning_search.contracts import SearchBudget
from host_brain.contracts.planning_search.code_benchmark_models import (
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkContractError,
    CodeBenchmarkPathPolicy,
    CodeVerifierResult,
)
from host_brain.planning_search.code_benchmark_sandbox import (
    CodeBenchmarkWorkspace,
    CodeBenchmarkSandboxError,
    CodeHistoricalBenchmarkSandbox,
    PrivateBareBenchmarkSource,
    _GIT_HARDENING_CONFIG,
    _git_environment,
)


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(path), *args),
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _source_repo(tmp_path: Path) -> tuple[Path, str, Path, Path]:
    source = tmp_path / "protected-source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "benchmark@example.invalid")
    _git(source, "config", "user.name", "benchmark")
    _git(source, "config", "core.autocrlf", "false")
    (source / "src").mkdir()
    (source / "src" / "app.py").write_text("value = 'base'\n", encoding="utf-8")
    _git(source, "add", "src/app.py")
    _git(source, "commit", "-m", "seed")
    private_root = tmp_path / "private-bares"
    worktree_root = tmp_path / "benchmark-worktrees"
    private_root.mkdir()
    worktree_root.mkdir()
    return source, _git(source, "rev-parse", "HEAD"), private_root, worktree_root


def _arm(name: str) -> BenchmarkArm:
    return BenchmarkArm(
        arm_id=name,
        selected_candidate_id=f"{name}-inspect",
        selected_path_candidate_ids=(f"{name}-inspect", f"{name}-patch"),
        trace_digest="a" * 64,
        counters={"candidates": 2, "expansions": 1, "transitions": 1, "rollouts": 0},
        virtual_state_schema="natsume.planning-search.virtual-state.v1",
        virtual_state_digest="b" * 64,
    )


def _workload(*, source_ref: str, commit: str, policy: CodeBenchmarkPathPolicy) -> DomainBenchmarkWorkload:
    return DomainBenchmarkWorkload(
        workload_id="code-historical-d17",
        domain="code",
        workload_ref="benchmark://code/d17/historical-task",
        baseline_ref="benchmark://code/d17/baseline",
        verifier_refs=("verifier://code/d17/hidden-oracle",),
        isolation={
            "sandbox_mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "source_ref": source_ref,
            "source_commit": commit,
            "path_policy_digest": policy.digest,
            "executor_trust": CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
            "network_isolation": CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
        },
        budget=SearchBudget(max_candidates=8, max_expansions=4, max_rollouts=2),
        cohort_key="code-historical-d17",
    )


class _Writer:
    def execute(self, *, workspace, arm):  # noqa: ANN001, ANN201
        prior = workspace.read_text("src/app.py")
        workspace.write_text("src/app.py", prior.replace("base", arm.arm_id))
        return {"tool_call_count": 1}


class _Verifier:
    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def verify(self, *, workspace, arm):  # noqa: ANN001, ANN201
        content = workspace.read_text("src/app.py")
        self.seen[arm.arm_id] = content
        success = arm.arm_id in content
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return CodeVerifierResult(
            success=success,
            receipt_ref=f"receipt://code/d17/{arm.arm_id}",
            cost={"tool_call_count": 1},
            evidence_digest=digest,
        )


class _VerifierWithUnboundD19Counters(_Verifier):
    """A legacy verifier must not self-report D19-only subprocess counters."""

    def verify(self, *, workspace, arm):  # noqa: ANN001, ANN201
        result = super().verify(workspace=workspace, arm=arm)
        return CodeVerifierResult(
            success=result.success,
            receipt_ref=result.receipt_ref,
            cost={
                "tool_call_count": 1,
                "test_command_count": 1,
                "verification_wall_time_ms": 1,
            },
            evidence_digest=result.evidence_digest,
        )


class _EscapingWriter:
    def execute(self, *, workspace, arm):  # noqa: ANN001, ANN201
        del arm
        workspace.write_text(".git/config", "malicious")
        return {"tool_call_count": 1}


class _NewFileWriter:
    def execute(self, *, workspace, arm):  # noqa: ANN001, ANN201
        workspace.write_text("src/generated.py", f"arm = {arm.arm_id!r}\n")
        return {"tool_call_count": 1}


class _IgnoredArtifactWriter:
    def execute(self, *, workspace, arm):  # noqa: ANN001, ANN201
        del arm
        # This deliberately bypasses the narrow API to verify the trusted
        # adapter's evidence guard.  It is not evidence of an untrusted-code
        # sandbox; D17 explicitly does not provide one.
        ignored = workspace._root / "ignored" / "artifact.txt"
        ignored.parent.mkdir(parents=True, exist_ok=True)
        ignored.write_text("must be detected\n", encoding="utf-8")
        return {"tool_call_count": 1}


class _SourceConfigMutatingVerifier(_Verifier):
    def __init__(self, source: Path) -> None:
        super().__init__()
        self._source = source

    def verify(self, *, workspace, arm):  # noqa: ANN001, ANN201
        _git(self._source, "config", "benchmark.tampered", "yes")
        return super().verify(workspace=workspace, arm=arm)


def _sandbox(
    tmp_path: Path,
) -> tuple[CodeHistoricalBenchmarkSandbox, str, str, CodeBenchmarkPathPolicy, Path, Path, Path]:
    source, commit, private_root, worktree_root = _source_repo(tmp_path)
    source_ref = "snapshot://code/d17/seed"
    sandbox = CodeHistoricalBenchmarkSandbox(
        source=PrivateBareBenchmarkSource(
            source_repository=source,
            private_bare_root=private_root,
            worktree_root=worktree_root,
            protected_roots=(source,),
            source_ref=source_ref,
            source_commit=commit,
        )
    )
    return (
        sandbox,
        source_ref,
        commit,
        CodeBenchmarkPathPolicy(allowed_paths=("src",), max_changed_files=1),
        source,
        private_root,
        worktree_root,
    )


def _directory_symlink_or_skip(*, link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as exc:
        symlink_error = exc
    if os.name == "nt":
        junction = subprocess.run(
            ("cmd", "/d", "/c", "mklink", "/J", str(link), str(target)),
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
        )
        if junction.returncode == 0:
            return
    pytest.skip(
        "directory symlink/junction unavailable on this test host: "
        f"{symlink_error}; junction stderr={junction.stderr if os.name == 'nt' else ''}"
    )


def test_private_bare_paired_worktrees_do_not_modify_protected_source(tmp_path: Path) -> None:
    sandbox, source_ref, commit, policy, source, private_root, worktree_root = _sandbox(tmp_path)
    source_refs_before = _git(source, "show-ref", "--head")
    source_worktrees_before = _git(source, "worktree", "list", "--porcelain")
    verifier = _Verifier()
    report = sandbox.run_paired(
        run_id="run-d17-isolated",
        workload=_workload(source_ref=source_ref, commit=commit, policy=policy),
        baseline=_arm("baseline"),
        selected=_arm("selected"),
        path_policy=policy,
        baseline_executor=_Writer(),
        selected_executor=_Writer(),
        verifier=verifier,
    )

    assert report.mode == CODE_HISTORICAL_BENCHMARK_MODE
    assert report.production_influence_enabled is False
    assert report.report.production_influence_enabled is False
    assert report.report.baseline_outcome is not None
    assert report.report.selected_outcome is not None
    assert report.report.baseline_outcome.executed is True
    assert report.cleanup_receipt is not None
    assert report.cleanup_receipt.status == "VERIFIED_CLEAN"
    assert report.cleanup_receipt.run_id == report.run_id
    assert report.cleanup_receipt.source_before_digest == report.cleanup_receipt.source_after_digest
    assert report.baseline_evidence.source_commit == commit
    assert report.baseline_evidence.workspace_diff_digest != report.selected_evidence.workspace_diff_digest
    assert verifier.seen == {
        "baseline": "value = 'baseline'\n",
        "selected": "value = 'selected'\n",
    }
    assert (source / "src" / "app.py").read_text(encoding="utf-8") == "value = 'base'\n"
    assert _git(source, "status", "--porcelain=v1", "--untracked-files=no") == ""
    assert _git(source, "rev-parse", "HEAD") == commit
    assert _git(source, "show-ref", "--head") == source_refs_before
    assert _git(source, "worktree", "list", "--porcelain") == source_worktrees_before
    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []
    serialized = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
    assert str(source) not in serialized
    assert str(private_root) not in serialized
    assert str(worktree_root) not in serialized


def test_paired_worktree_executor_honors_owner_randomized_arm_order(tmp_path: Path) -> None:
    sandbox, source_ref, commit, policy, _source, private_root, worktree_root = _sandbox(
        tmp_path
    )
    verifier = _Verifier()

    report = sandbox.run_paired(
        run_id="selected-first",
        workload=_workload(source_ref=source_ref, commit=commit, policy=policy),
        baseline=_arm("baseline"),
        selected=_arm("selected"),
        path_policy=policy,
        baseline_executor=_Writer(),
        selected_executor=_Writer(),
        verifier=verifier,
        arm_order=("SELECTED", "BASELINE"),
    )

    assert list(verifier.seen) == ["selected", "baseline"]
    assert report.baseline_evidence.arm_id == "baseline"
    assert report.selected_evidence.arm_id == "selected"
    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []


def test_paired_worktree_rejects_invalid_arm_order_before_execution(tmp_path: Path) -> None:
    sandbox, source_ref, commit, policy, _source, _private_root, _worktree_root = _sandbox(
        tmp_path
    )

    with pytest.raises(CodeBenchmarkSandboxError, match="arm_order"):
        sandbox.run_paired(
            run_id="invalid-order",
            workload=_workload(source_ref=source_ref, commit=commit, policy=policy),
            baseline=_arm("baseline"),
            selected=_arm("selected"),
            path_policy=policy,
            baseline_executor=_Writer(),
            selected_executor=_Writer(),
            verifier=_Verifier(),
            arm_order=("BASELINE", "BASELINE"),
        )


def test_path_escape_fails_closed_and_still_cleans_private_artifacts(tmp_path: Path) -> None:
    sandbox, source_ref, commit, policy, source, private_root, worktree_root = _sandbox(tmp_path)

    with pytest.raises(CodeBenchmarkSandboxError, match="invalid|outside"):
        sandbox.run_paired(
            run_id="run-d17-escape",
            workload=_workload(source_ref=source_ref, commit=commit, policy=policy),
            baseline=_arm("baseline"),
            selected=_arm("selected"),
            path_policy=policy,
            baseline_executor=_EscapingWriter(),
            selected_executor=_Writer(),
            verifier=_Verifier(),
        )

    assert (source / "src" / "app.py").read_text(encoding="utf-8") == "value = 'base'\n"
    assert _git(source, "status", "--porcelain=v1", "--untracked-files=no") == ""
    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []


def test_legacy_verifier_cannot_claim_d19_only_cost_dimensions(tmp_path: Path) -> None:
    sandbox, source_ref, commit, policy, source, private_root, worktree_root = _sandbox(tmp_path)

    with pytest.raises(CodeBenchmarkSandboxError, match="exact subset"):
        sandbox.run_paired(
            run_id="run-d17-unbound-d19-counters",
            workload=_workload(source_ref=source_ref, commit=commit, policy=policy),
            baseline=_arm("baseline"),
            selected=_arm("selected"),
            path_policy=policy,
            baseline_executor=_Writer(),
            selected_executor=_Writer(),
            verifier=_VerifierWithUnboundD19Counters(),
        )

    assert (source / "src" / "app.py").read_text(encoding="utf-8") == "value = 'base'\n"
    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []


def test_code_benchmark_rejects_host_paths_and_unregistered_or_overlapping_roots(
    tmp_path: Path,
) -> None:
    source, commit, private_root, worktree_root = _source_repo(tmp_path)
    with pytest.raises(CodeBenchmarkContractError, match="repository-relative"):
        CodeBenchmarkPathPolicy(allowed_paths=("C:/Users/unsafe",))
    with pytest.raises(CodeBenchmarkContractError, match="not a path"):
        CodeVerifierResult(
            success=False,
            receipt_ref="file:///tmp/receipt",
            cost={},
            evidence_digest="a" * 64,
        )
    with pytest.raises(CodeBenchmarkContractError, match="not a path"):
        CodeVerifierResult(
            success=False,
            receipt_ref="receipt://C:/Users/unsafe",
            cost={},
            evidence_digest="a" * 64,
        )
    with pytest.raises(CodeBenchmarkContractError, match="not a path"):
        CodeVerifierResult(
            success=False,
            receipt_ref="file://localhost/tmp/receipt",
            cost={},
            evidence_digest="a" * 64,
        )
    with pytest.raises(CodeBenchmarkSandboxError, match="protected root"):
        PrivateBareBenchmarkSource(
            source_repository=source,
            private_bare_root=private_root,
            worktree_root=worktree_root,
            protected_roots=(tmp_path,),
            source_ref="snapshot://code/d17/seed",
            source_commit=commit,
        )
    with pytest.raises(CodeBenchmarkSandboxError, match="opaque governed ref"):
        PrivateBareBenchmarkSource(
            source_repository=source,
            private_bare_root=private_root,
            worktree_root=worktree_root,
            protected_roots=(source,),
            source_ref="snapshot://C:/Users/unsafe",
            source_commit=commit,
        )
    with pytest.raises(CodeBenchmarkSandboxError, match="must not overlap"):
        PrivateBareBenchmarkSource(
            source_repository=source,
            private_bare_root=source,
            worktree_root=worktree_root,
            protected_roots=(source,),
            source_ref="snapshot://code/d17/seed",
            source_commit=commit,
        )


def test_private_bare_source_rejects_linked_git_worktree(tmp_path: Path) -> None:
    source, commit, private_root, worktree_root = _source_repo(tmp_path)
    linked_source = tmp_path / "linked-source"
    _git(source, "worktree", "add", "--detach", str(linked_source), commit)

    with pytest.raises(CodeBenchmarkSandboxError, match="non-linked .git directory"):
        PrivateBareBenchmarkSource(
            source_repository=linked_source,
            private_bare_root=private_root,
            worktree_root=worktree_root,
            protected_roots=(linked_source,),
            source_ref="snapshot://code/d17/linked",
            source_commit=commit,
        )

    _git(source, "worktree", "remove", "--force", str(linked_source))


def test_private_bare_source_rejects_git_directory_symlink_or_junction(tmp_path: Path) -> None:
    source, commit, private_root, worktree_root = _source_repo(tmp_path)
    external_git_directory = tmp_path / "external-git-control"
    (source / ".git").rename(external_git_directory)
    _directory_symlink_or_skip(link=source / ".git", target=external_git_directory)

    with pytest.raises(CodeBenchmarkSandboxError, match="non-linked .git directory"):
        PrivateBareBenchmarkSource(
            source_repository=source,
            private_bare_root=private_root,
            worktree_root=worktree_root,
            protected_roots=(source,),
            source_ref="snapshot://code/d17/git-junction",
            source_commit=commit,
        )


def test_workspace_path_policy_has_one_portable_spelling(tmp_path: Path) -> None:
    policy = CodeBenchmarkPathPolicy(
        allowed_paths=("src",),
        forbidden_paths=("src/private",),
    )
    workspace = CodeBenchmarkWorkspace(root=tmp_path, policy=policy)

    for unsafe_path in (
        " src/escaped.py",
        "src/escaped.py ",
        "SRC/escaped.py",
        ".GIT/config",
        "src/.git /config",
        "src/private/secret.py",
    ):
        with pytest.raises(CodeBenchmarkSandboxError, match="invalid|outside"):
            workspace.write_text(unsafe_path, "blocked\n")

    assert not (tmp_path / " src").exists()
    assert not (tmp_path / ".GIT").exists()


def test_untracked_content_is_receipted_and_ignored_artifacts_fail_closed(
    tmp_path: Path,
) -> None:
    sandbox, source_ref, commit, policy, source, private_root, worktree_root = _sandbox(tmp_path)
    (source / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _git(source, "add", ".gitignore")
    _git(source, "commit", "-m", "ignore-private-artifacts")
    commit = _git(source, "rev-parse", "HEAD")
    source_ref = "snapshot://code/d17/ignored-artifacts"
    sandbox = CodeHistoricalBenchmarkSandbox(
        source=PrivateBareBenchmarkSource(
            source_repository=source,
            private_bare_root=private_root,
            worktree_root=worktree_root,
            protected_roots=(source,),
            source_ref=source_ref,
            source_commit=commit,
        )
    )
    verifier = _Verifier()
    report = sandbox.run_paired(
        run_id="run-d17-untracked-receipt",
        workload=_workload(source_ref=source_ref, commit=commit, policy=policy),
        baseline=_arm("baseline"),
        selected=_arm("selected"),
        path_policy=policy,
        baseline_executor=_NewFileWriter(),
        selected_executor=_NewFileWriter(),
        verifier=verifier,
    )

    assert report.baseline_evidence.changed_file_count == 1
    assert report.selected_evidence.changed_file_count == 1
    assert report.baseline_evidence.workspace_diff_digest != report.selected_evidence.workspace_diff_digest

    with pytest.raises(CodeBenchmarkSandboxError, match="ignored untracked"):
        sandbox.run_paired(
            run_id="run-d17-ignored-artifact",
            workload=_workload(source_ref=source_ref, commit=commit, policy=policy),
            baseline=_arm("baseline"),
            selected=_arm("selected"),
            path_policy=policy,
            baseline_executor=_IgnoredArtifactWriter(),
            selected_executor=_Writer(),
            verifier=_Verifier(),
        )

    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []


def test_source_git_control_mutation_is_detected_after_cleanup(tmp_path: Path) -> None:
    sandbox, source_ref, commit, policy, source, private_root, worktree_root = _sandbox(tmp_path)

    with pytest.raises(CodeBenchmarkSandboxError, match="protected source repository changed"):
        sandbox.run_paired(
            run_id="run-d17-source-control-change",
            workload=_workload(source_ref=source_ref, commit=commit, policy=policy),
            baseline=_arm("baseline"),
            selected=_arm("selected"),
            path_policy=policy,
            baseline_executor=_Writer(),
            selected_executor=_Writer(),
            verifier=_SourceConfigMutatingVerifier(source),
        )

    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []


def test_cleanup_unlinks_private_symlinks_without_traversing_external_targets(
    tmp_path: Path,
) -> None:
    sandbox, _source_ref, _commit, _policy, _source, private_root, worktree_root = _sandbox(tmp_path)
    external = tmp_path / "external-target"
    external.mkdir()
    sentinel = external / "must-survive.txt"
    sentinel.write_text("outside private roots\n", encoding="utf-8")
    run_root = worktree_root / "run-d17-link"
    private_bare = private_root / "run-d17-link"
    _directory_symlink_or_skip(link=run_root, target=external)
    _directory_symlink_or_skip(link=private_bare, target=external)

    cleanup_error = sandbox._cleanup(
        private_bare=private_bare,
        run_root=run_root,
        opened_targets=(),
        source_before=sandbox._source_fingerprint(),
    )

    assert cleanup_error is None
    assert not run_root.exists()
    assert not private_bare.exists()
    assert sentinel.read_text(encoding="utf-8") == "outside private roots\n"


def test_workload_cannot_claim_network_enforcement(tmp_path: Path) -> None:
    sandbox, source_ref, commit, policy, _source, _private_root, _worktree_root = _sandbox(tmp_path)
    accepted = _workload(source_ref=source_ref, commit=commit, policy=policy)
    legacy_network_claim = DomainBenchmarkWorkload(
        workload_id=accepted.workload_id,
        domain=accepted.domain,
        workload_ref=accepted.workload_ref,
        baseline_ref=accepted.baseline_ref,
        verifier_refs=accepted.verifier_refs,
        isolation={**dict(accepted.isolation), "network_enabled": False},
        budget=accepted.budget,
        cohort_key=accepted.cohort_key,
    )

    with pytest.raises(CodeBenchmarkSandboxError, match="non-enforcement"):
        sandbox.run_paired(
            run_id="run-d17-network-claim",
            workload=legacy_network_claim,
            baseline=_arm("baseline"),
            selected=_arm("selected"),
            path_policy=policy,
            baseline_executor=_Writer(),
            selected_executor=_Writer(),
            verifier=_Verifier(),
        )

    path_leak = DomainBenchmarkWorkload(
        workload_id=accepted.workload_id,
        domain=accepted.domain,
        workload_ref=accepted.workload_ref,
        baseline_ref=accepted.baseline_ref,
        verifier_refs=accepted.verifier_refs,
        isolation={**dict(accepted.isolation), "internal_path": r"C:\private\source"},
        budget=accepted.budget,
        cohort_key=accepted.cohort_key,
    )
    with pytest.raises(CodeBenchmarkSandboxError, match="non-enforcement"):
        sandbox.run_paired(
            run_id="run-d17-isolation-extra",
            workload=path_leak,
            baseline=_arm("baseline"),
            selected=_arm("selected"),
            path_policy=policy,
            baseline_executor=_Writer(),
            selected_executor=_Writer(),
            verifier=_Verifier(),
        )


def test_git_subprocess_uses_a_sanitized_environment() -> None:
    environment = _git_environment()

    assert environment["GIT_ALLOW_PROTOCOL"] == "file"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert "GIT_DIR" not in environment
    assert _GIT_HARDENING_CONFIG == (
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        "-c",
        "diff.external=",
    )
