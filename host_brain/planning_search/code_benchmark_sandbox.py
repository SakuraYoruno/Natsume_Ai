"""Private-bare, paired-worktree runner for Code benchmark experiments.

This is an explicit experiment adapter, not a Host route, TaskGraph worker or
Agent tool runtime.  It clones a clean protected source into a disposable
private bare repository and exposes each benchmark arm only through a
repository-relative read/write capability.  It intentionally does *not* claim
container, tenant, OS-process, filesystem or network isolation.  Executors
and verifiers are trusted repository-owned in-process adapters: this module is
a mechanical same-commit benchmark guard, never an untrusted-code sandbox.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from typing import Any, Protocol

from host_brain.planning_search.benchmarking import (
    BenchmarkArm,
    DomainBenchmarkReport,
    DomainBenchmarkWorkload,
)
from host_brain.planning_search.code_benchmark_models import (
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkArmEvidence,
    CodeBenchmarkCleanupReceipt,
    CodeBenchmarkContractError,
    CodeBenchmarkPathPolicy,
    CodeHistoricalBenchmarkRun,
    CodeVerifierResult,
    benchmark_outcome,
)


CODE_BENCHMARK_SANDBOX_ERROR = "CODE_BENCHMARK_SANDBOX_ERROR"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_GOVERNED_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{0,63}://[a-z0-9][a-z0-9._/-]{0,1018}$"
)
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_MAX_FILE_BYTES = 1_048_576
_MAX_GIT_OUTPUT_BYTES = 8 * 1_048_576
_GIT_HARDENING_CONFIG = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "credential.helper=",
    "-c",
    "diff.external=",
)


class CodeBenchmarkSandboxError(RuntimeError):
    """Raised when a paired historical Code run loses its mechanical boundary."""

    def __init__(self, detail: str) -> None:
        self.reason_code = CODE_BENCHMARK_SANDBOX_ERROR
        self.detail = str(detail or "Code benchmark sandbox failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class PrivateBareBenchmarkSource:
    """Server-only location and provenance configuration for one source snapshot."""

    source_repository: Path
    private_bare_root: Path
    worktree_root: Path
    protected_roots: Sequence[Path]
    source_ref: str
    source_commit: str

    def __post_init__(self) -> None:
        source = _existing_directory(self.source_repository, "source_repository")
        bare_root = _existing_directory(self.private_bare_root, "private_bare_root")
        worktree_root = _existing_directory(self.worktree_root, "worktree_root")
        protected = _existing_directories(self.protected_roots, "protected_roots")
        if source not in protected:
            raise CodeBenchmarkSandboxError(
                "source_repository must be explicitly registered as a protected root"
            )
        # Reject linked worktrees, symlinked `.git` folders and Windows
        # junctions before validating configured private roots.  Otherwise a
        # source root can look protected while its actual Git control data is
        # outside that root (or contains one of the configured private roots).
        _source_git_directory(source)
        for candidate in (bare_root, worktree_root):
            if any(_overlaps(candidate, root) for root in protected):
                raise CodeBenchmarkSandboxError(
                    "private bare/worktree roots must not overlap protected roots"
                )
        if _overlaps(bare_root, worktree_root):
            raise CodeBenchmarkSandboxError(
                "private_bare_root and worktree_root must not overlap"
            )
        source_ref = _opaque_ref(self.source_ref, "source_ref")
        source_commit = _commit(self.source_commit, "source_commit")
        object.__setattr__(self, "source_repository", source)
        object.__setattr__(self, "private_bare_root", bare_root)
        object.__setattr__(self, "worktree_root", worktree_root)
        object.__setattr__(self, "protected_roots", protected)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "source_commit", source_commit)


class CodeBenchmarkArmExecutor(Protocol):
    """Trusted repository-owned in-process executor for a benchmark arm.

    The workspace API is deliberately narrow, but Python code in this process
    is not confined.  Do not pass untrusted model, skill, plugin or verifier
    implementations to this protocol.
    """

    def execute(
        self,
        *,
        workspace: "CodeBenchmarkWorkspace",
        arm: BenchmarkArm,
    ) -> Mapping[str, int]: ...


class CodeBenchmarkVerifier(Protocol):
    """Trusted repository-owned in-process verifier returning bounded evidence."""

    def verify(
        self,
        *,
        workspace: "CodeBenchmarkWorkspace",
        arm: BenchmarkArm,
    ) -> CodeVerifierResult: ...


class CodeBenchmarkWorkspace:
    """Trusted-adapter path guard for one detached worktree, not a sandbox."""

    def __init__(self, *, root: Path, policy: CodeBenchmarkPathPolicy) -> None:
        self._root = _existing_directory(root, "worktree")
        self._policy = policy
        self._closed = False

    def read_text(self, relative_path: str) -> str:
        target = self._resolve(relative_path, operation="read", require_exists=True)
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CodeBenchmarkSandboxError("cannot read approved benchmark path") from exc

    def write_text(self, relative_path: str, content: str) -> None:
        if not isinstance(content, str) or len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise CodeBenchmarkSandboxError(
                "benchmark write must be UTF-8 text within the file size limit"
            )
        target = self._resolve(relative_path, operation="write", require_exists=False)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _assert_path_under(target.parent.resolve(strict=True), self._root)
            _reject_symlink_components(target, self._root)
            target.write_text(content, encoding="utf-8", newline="")
        except CodeBenchmarkSandboxError:
            raise
        except OSError as exc:
            raise CodeBenchmarkSandboxError("cannot write approved benchmark path") from exc

    def exists(self, relative_path: str) -> bool:
        target = self._resolve(relative_path, operation="exists", require_exists=False)
        if not target.exists():
            return False
        _reject_symlink_components(target, self._root)
        _assert_path_under(target.resolve(strict=True), self._root)
        return True

    def _resolve(
        self,
        relative_path: str,
        *,
        operation: str,
        require_exists: bool,
    ) -> Path:
        if self._closed:
            raise CodeBenchmarkSandboxError("benchmark arm workspace is closed")
        try:
            canonical_path = self._policy.canonicalize(relative_path)
            permitted = self._policy.permits(canonical_path)
        except CodeBenchmarkContractError as exc:
            raise CodeBenchmarkSandboxError(
                f"benchmark {operation} path is invalid"
            ) from exc
        if not permitted:
            raise CodeBenchmarkSandboxError(
                f"benchmark {operation} path is outside the trusted path policy"
            )
        # Never consume the caller spelling after policy validation.  In
        # particular, stripping a leading space for the ACL while joining the
        # raw string would create a second path outside the checked policy.
        target = self._root.joinpath(*canonical_path.split("/"))
        _assert_path_under(target.parent.resolve(strict=False), self._root)
        _reject_symlink_components(target, self._root)
        if require_exists and not target.is_file():
            raise CodeBenchmarkSandboxError("approved benchmark file does not exist")
        if target.exists():
            _assert_path_under(target.resolve(strict=True), self._root)
            _reject_symlink_components(target, self._root)
        return target

    def _close(self) -> None:
        self._closed = True

    def _trusted_verification_root(self) -> Path:
        """Return the worktree only to a repository-owned verifier adapter.

        This private bridge is intentionally unavailable after the arm closes
        and must never be copied into benchmark evidence or a durable receipt.
        It does not broaden the public file capability or create a sandbox.
        """

        if self._closed:
            raise CodeBenchmarkSandboxError("benchmark arm workspace is closed")
        return self._root


class CodeHistoricalBenchmarkSandbox:
    """Run two Code arms from one frozen commit in private detached worktrees."""

    def __init__(self, *, source: PrivateBareBenchmarkSource) -> None:
        if not isinstance(source, PrivateBareBenchmarkSource):
            raise TypeError("source must be PrivateBareBenchmarkSource")
        self._source = source
        self._assert_source_is_clean_worktree()

    def run_paired(
        self,
        *,
        run_id: str,
        workload: DomainBenchmarkWorkload,
        baseline: BenchmarkArm,
        selected: BenchmarkArm,
        path_policy: CodeBenchmarkPathPolicy,
        baseline_executor: CodeBenchmarkArmExecutor,
        selected_executor: CodeBenchmarkArmExecutor,
        verifier: CodeBenchmarkVerifier,
        arm_order: Sequence[str] = ("BASELINE", "SELECTED"),
    ) -> CodeHistoricalBenchmarkRun:
        """Execute two trusted arms from one commit and derive a shadow report.

        Executors receive a narrow `CodeBenchmarkWorkspace`, but that API does
        not confine Python code in this process.  Any failure aborts comparison
        and cleanup removes only paths created beneath configured private roots.
        """

        normalized_run_id = _run_id(run_id)
        self._validate_request(
            workload=workload,
            baseline=baseline,
            selected=selected,
            path_policy=path_policy,
            baseline_executor=baseline_executor,
            selected_executor=selected_executor,
            verifier=verifier,
        )
        normalized_arm_order = tuple(str(value or "").strip().upper() for value in arm_order)
        if (
            len(normalized_arm_order) != 2
            or set(normalized_arm_order) != {"BASELINE", "SELECTED"}
        ):
            raise CodeBenchmarkSandboxError(
                "arm_order must contain BASELINE and SELECTED exactly once"
            )
        source_before = self._source_fingerprint()
        private_bare = self._source.private_bare_root / normalized_run_id
        run_root = self._source.worktree_root / normalized_run_id
        _assert_direct_child(private_bare, self._source.private_bare_root)
        _assert_direct_child(run_root, self._source.worktree_root)
        if private_bare.exists() or run_root.exists():
            raise CodeBenchmarkSandboxError("benchmark run id already has private artifacts")

        opened_targets: list[Path] = []
        primary_failure: BaseException | None = None
        completed_run: CodeHistoricalBenchmarkRun | None = None
        cleanup_receipt: CodeBenchmarkCleanupReceipt | None = None
        verifier_prepared = False
        try:
            self._prepare_verifier(
                verifier=verifier,
                run_id=normalized_run_id,
                workload=workload,
                baseline=baseline,
                selected=selected,
            )
            verifier_prepared = True
            self._create_private_bare(private_bare)
            _assert_private_directory(
                private_bare,
                self._source.private_bare_root,
                detail="private benchmark bare repository",
                require_exists=True,
            )
            private_before = _git_refs_digest(private_bare)
            run_root.mkdir(mode=0o700)
            arm_inputs = {
                "BASELINE": (baseline, baseline_executor),
                "SELECTED": (selected, selected_executor),
            }
            observed: dict[str, tuple[CodeBenchmarkArmEvidence, Any]] = {}
            for role in normalized_arm_order:
                arm, executor = arm_inputs[role]
                observed[role] = self._run_arm(
                    private_bare=private_bare,
                    run_root=run_root,
                    arm=arm,
                    path_policy=path_policy,
                    executor=executor,
                    verifier=verifier,
                    opened_targets=opened_targets,
                )
            baseline_evidence, baseline_outcome = observed["BASELINE"]
            selected_evidence, selected_outcome = observed["SELECTED"]
            _assert_private_directory(
                private_bare,
                self._source.private_bare_root,
                detail="private benchmark bare repository",
                require_exists=True,
            )
            if _git_refs_digest(private_bare) != private_before:
                raise CodeBenchmarkSandboxError(
                    "arm execution modified private bare repository references"
                )
            report = DomainBenchmarkReport(
                workload=workload,
                baseline=baseline,
                selected=selected,
                baseline_outcome=baseline_outcome,
                selected_outcome=selected_outcome,
            )
            completed_run = CodeHistoricalBenchmarkRun(
                run_id=normalized_run_id,
                report=report,
                baseline_evidence=baseline_evidence,
                selected_evidence=selected_evidence,
            )
        except BaseException as exc:  # noqa: BLE001 - preserve verifier failures
            primary_failure = exc
            raise
        finally:
            try:
                cleanup_result = self._cleanup(
                    private_bare=private_bare,
                    run_root=run_root,
                    opened_targets=opened_targets,
                    source_before=source_before,
                    run_id=normalized_run_id,
                    return_receipt=True,
                )
                assert isinstance(cleanup_result, tuple)
                cleanup_receipt, cleanup_error = cleanup_result
                if cleanup_error is not None:
                    if primary_failure is not None:
                        primary_failure.add_note(
                            f"benchmark cleanup also failed: {cleanup_error}"
                        )
                    else:
                        raise cleanup_error
            finally:
                if verifier_prepared:
                    self._finish_verifier(verifier=verifier, run_id=normalized_run_id)
        if completed_run is None or cleanup_receipt is None:
            raise CodeBenchmarkSandboxError(
                "benchmark did not produce a cleanup-verified paired receipt"
            )
        return CodeHistoricalBenchmarkRun(
            run_id=completed_run.run_id,
            report=completed_run.report,
            baseline_evidence=completed_run.baseline_evidence,
            selected_evidence=completed_run.selected_evidence,
            cleanup_receipt=cleanup_receipt,
        )

    def _validate_request(
        self,
        *,
        workload: DomainBenchmarkWorkload,
        baseline: BenchmarkArm,
        selected: BenchmarkArm,
        path_policy: CodeBenchmarkPathPolicy,
        baseline_executor: CodeBenchmarkArmExecutor,
        selected_executor: CodeBenchmarkArmExecutor,
        verifier: CodeBenchmarkVerifier,
    ) -> None:
        if not isinstance(workload, DomainBenchmarkWorkload) or workload.domain != "code":
            raise CodeBenchmarkSandboxError("benchmark workload must be code domain")
        _opaque_ref(workload.workload_ref, "workload_ref")
        _opaque_ref(workload.baseline_ref, "baseline_ref")
        for index, verifier_ref in enumerate(workload.verifier_refs):
            _opaque_ref(verifier_ref, f"verifier_refs[{index}]")
        if not isinstance(baseline, BenchmarkArm) or not isinstance(selected, BenchmarkArm):
            raise CodeBenchmarkSandboxError("benchmark arms must be BenchmarkArm")
        if baseline.arm_id == selected.arm_id:
            raise CodeBenchmarkSandboxError("baseline and selected arm ids must differ")
        if not isinstance(path_policy, CodeBenchmarkPathPolicy):
            raise CodeBenchmarkSandboxError("path_policy must be CodeBenchmarkPathPolicy")
        if not callable(getattr(baseline_executor, "execute", None)):
            raise CodeBenchmarkSandboxError("baseline executor must expose execute")
        if not callable(getattr(selected_executor, "execute", None)):
            raise CodeBenchmarkSandboxError("selected executor must expose execute")
        if not callable(getattr(verifier, "verify", None)):
            raise CodeBenchmarkSandboxError("verifier must expose verify")
        isolation = dict(workload.isolation)
        expected_isolation_keys = {
            "sandbox_mode",
            "source_ref",
            "source_commit",
            "path_policy_digest",
            "executor_trust",
            "network_isolation",
        }
        if (
            set(isolation) != expected_isolation_keys
            or isolation.get("sandbox_mode") != CODE_HISTORICAL_BENCHMARK_MODE
            or isolation.get("source_ref") != self._source.source_ref
            or isolation.get("source_commit") != self._source.source_commit
            or isolation.get("path_policy_digest") != path_policy.digest
            or isolation.get("executor_trust")
            != CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST
            or isolation.get("network_isolation")
            != CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION
        ):
            raise CodeBenchmarkSandboxError(
                "workload isolation must bind private source, policy, trusted process and non-enforcement"
            )

    def _run_arm(
        self,
        *,
        private_bare: Path,
        run_root: Path,
        arm: BenchmarkArm,
        path_policy: CodeBenchmarkPathPolicy,
        executor: CodeBenchmarkArmExecutor,
        verifier: CodeBenchmarkVerifier,
        opened_targets: list[Path],
    ) -> tuple[CodeBenchmarkArmEvidence, Any]:
        _assert_private_directory(
            private_bare,
            self._source.private_bare_root,
            detail="private benchmark bare repository",
            require_exists=True,
        )
        _assert_private_directory(
            run_root,
            self._source.worktree_root,
            detail="private benchmark run root",
            require_exists=True,
        )
        target = run_root / arm.arm_id
        _assert_direct_child(target, run_root)
        if target.exists():
            raise CodeBenchmarkSandboxError("benchmark arm target already exists")
        # Register before spawning Git: a failed worktree add can still leave
        # a direct-child directory that must be cleaned up in finally.
        opened_targets.append(target)
        _git(private_bare, "worktree", "add", "--detach", str(target), self._source.source_commit)
        self._verify_detached_worktree(private_bare=private_bare, target=target)
        workspace = CodeBenchmarkWorkspace(root=target, policy=path_policy)
        started_ns = time.monotonic_ns()
        try:
            cost = executor.execute(workspace=workspace, arm=arm)
            verifier_result = verifier.verify(workspace=workspace, arm=arm)
        finally:
            workspace._close()
        latency_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
        if not isinstance(verifier_result, CodeVerifierResult):
            raise CodeBenchmarkSandboxError("verifier must return CodeVerifierResult")
        _assert_counter_mapping(cost, field_name="executor cost")
        if not _executor_cost_is_bound_by_verifier(
            executor_cost=cost,
            verifier=verifier_result,
        ):
            raise CodeBenchmarkSandboxError(
                "executor cost must be an exact subset of canonical verifier cost"
            )
        changed_paths = self._changed_paths(target=target, path_policy=path_policy)
        diff_digest = self._workspace_diff_digest(
            target=target,
            path_policy=path_policy,
        )
        changed_path_digest = _digest_json(changed_paths)
        evidence = CodeBenchmarkArmEvidence(
            arm_id=arm.arm_id,
            source_ref=self._source.source_ref,
            source_commit=self._source.source_commit,
            path_policy_digest=path_policy.digest,
            changed_file_count=len(changed_paths),
            changed_path_digest=changed_path_digest,
            workspace_diff_digest=diff_digest,
            verifier=verifier_result,
        )
        return (
            evidence,
            benchmark_outcome(
                arm=arm,
                verifier=verifier_result,
                latency_ms=int(latency_ms),
                workspace_diff_digest=diff_digest,
            ),
        )

    @staticmethod
    def _prepare_verifier(
        *,
        verifier: CodeBenchmarkVerifier,
        run_id: str,
        workload: DomainBenchmarkWorkload,
        baseline: BenchmarkArm,
        selected: BenchmarkArm,
    ) -> None:
        prepare = getattr(verifier, "prepare_run", None)
        if prepare is None:
            return
        if not callable(prepare):
            raise CodeBenchmarkSandboxError("verifier prepare_run must be callable")
        try:
            prepare(
                run_id=run_id,
                workload=workload,
                baseline=baseline,
                selected=selected,
            )
        except CodeBenchmarkSandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed on verifier setup
            raise CodeBenchmarkSandboxError("controlled verifier preflight failed") from exc

    @staticmethod
    def _finish_verifier(*, verifier: CodeBenchmarkVerifier, run_id: str) -> None:
        finish = getattr(verifier, "finish_run", None)
        if finish is None:
            return
        if not callable(finish):
            raise CodeBenchmarkSandboxError("verifier finish_run must be callable")
        try:
            finish(run_id=run_id)
        except CodeBenchmarkSandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - state reset must fail closed
            raise CodeBenchmarkSandboxError("controlled verifier finalization failed") from exc

    def _changed_paths(
        self,
        *,
        target: Path,
        path_policy: CodeBenchmarkPathPolicy,
    ) -> tuple[str, ...]:
        tracked = _git_nul_list(target, "diff", "--name-only", "--no-renames", "-z", "HEAD")
        untracked = _git_nul_list(target, "ls-files", "--others", "--exclude-standard", "-z")
        ignored = _git_nul_list(
            target,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        )
        if ignored:
            raise CodeBenchmarkSandboxError(
                "benchmark arm produced ignored untracked artifacts"
            )
        changed = self._canonical_changed_paths(
            raw_paths=(*tracked, *untracked),
            path_policy=path_policy,
        )
        if len(changed) > path_policy.max_changed_files:
            raise CodeBenchmarkSandboxError("benchmark arm exceeded max_changed_files")
        return changed

    def _canonical_changed_paths(
        self,
        *,
        raw_paths: Sequence[str],
        path_policy: CodeBenchmarkPathPolicy,
    ) -> tuple[str, ...]:
        canonical_paths: list[str] = []
        for raw_path in raw_paths:
            try:
                relative_path = path_policy.canonicalize(raw_path)
                permitted = path_policy.permits(relative_path)
            except CodeBenchmarkContractError as exc:
                raise CodeBenchmarkSandboxError(
                    "benchmark diff contains an invalid repository-relative path"
                ) from exc
            if not permitted:
                raise CodeBenchmarkSandboxError(
                    "benchmark diff contains a path outside the trusted path policy"
                )
            if relative_path not in canonical_paths:
                canonical_paths.append(relative_path)
        return tuple(sorted(canonical_paths))

    def _workspace_diff_digest(
        self,
        *,
        target: Path,
        path_policy: CodeBenchmarkPathPolicy,
    ) -> str:
        """Bind tracked diff bytes and untracked file bytes into one receipt hash."""

        tracked_digest = _git_bytes_digest(
            target,
            "diff",
            "--binary",
            "--no-ext-diff",
            "HEAD",
        )
        untracked = self._canonical_changed_paths(
            raw_paths=_git_nul_list(
                target,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ),
            path_policy=path_policy,
        )
        return _digest_json(
            {
                "tracked_diff_sha256": tracked_digest,
                "untracked_files": [
                    {
                        "path": relative_path,
                        "content_sha256": _worktree_file_digest(target, relative_path),
                    }
                    for relative_path in untracked
                ],
            }
        )

    def _create_private_bare(self, private_bare: Path) -> None:
        self._assert_source_is_clean_worktree()
        _git_process(
            "clone",
            "--bare",
            "--no-local",
            str(self._source.source_repository),
            str(private_bare),
        )
        _assert_private_directory(
            private_bare,
            self._source.private_bare_root,
            detail="private benchmark bare repository",
            require_exists=True,
        )
        if _git(private_bare, "rev-parse", "--is-bare-repository") != "true":
            raise CodeBenchmarkSandboxError("private benchmark clone is not a bare repository")
        _git(private_bare, "cat-file", "-e", f"{self._source.source_commit}^{{commit}}")
        hooks_directory = private_bare / "hooks-disabled"
        hooks_directory.mkdir(mode=0o700)
        _git(
            private_bare,
            "config",
            "--local",
            "core.hooksPath",
            str(hooks_directory),
        )
        _git(private_bare, "remote", "remove", "origin")
        if _git(private_bare, "remote"):
            raise CodeBenchmarkSandboxError("private bare repository retains a remote")

    def _verify_detached_worktree(self, *, private_bare: Path, target: Path) -> None:
        _assert_private_directory(
            private_bare,
            self._source.private_bare_root,
            detail="private benchmark bare repository",
            require_exists=True,
        )
        target_resolved = _existing_directory(target, "benchmark arm worktree")
        if _git(target_resolved, "rev-parse", "--is-inside-work-tree") != "true":
            raise CodeBenchmarkSandboxError("benchmark arm is not a worktree")
        if _git(target_resolved, "rev-parse", "HEAD") != self._source.source_commit:
            raise CodeBenchmarkSandboxError("benchmark arm HEAD differs from frozen source commit")
        common = _resolve_git_path(target_resolved, _git(target_resolved, "rev-parse", "--git-common-dir"))
        if common != private_bare.resolve(strict=True):
            raise CodeBenchmarkSandboxError(
                "benchmark arm does not share only its private bare Git directory"
            )
        if _git(target_resolved, "status", "--porcelain=v1", "--untracked-files=no"):
            raise CodeBenchmarkSandboxError("new benchmark arm worktree is not clean")

    def _source_fingerprint(self) -> str:
        source = self._source.source_repository
        git_directory = _source_git_directory(source)
        return _digest_json(
            {
                "head": _git(source, "rev-parse", "HEAD"),
                "refs": _git_refs_digest(source),
                "status": _git(
                    source,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--ignored=matching",
                ),
                "worktrees": _git(source, "worktree", "list", "--porcelain"),
                "metadata": _git_control_digest(git_directory),
            }
        )

    def _assert_source_is_clean_worktree(self) -> None:
        if _git(self._source.source_repository, "rev-parse", "--is-bare-repository") != "false":
            raise CodeBenchmarkSandboxError("protected source_repository must be a normal worktree")
        top = Path(_git(self._source.source_repository, "rev-parse", "--show-toplevel"))
        if top.resolve(strict=True) != self._source.source_repository:
            raise CodeBenchmarkSandboxError("protected source_repository is not its repository root")
        _source_git_directory(self._source.source_repository)
        _git(self._source.source_repository, "cat-file", "-e", f"{self._source.source_commit}^{{commit}}")
        if _git(
            self._source.source_repository,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored=matching",
        ):
            raise CodeBenchmarkSandboxError(
                "protected source repository must have no tracked, untracked or ignored changes"
            )

    def _cleanup(
        self,
        *,
        private_bare: Path,
        run_root: Path,
        opened_targets: Sequence[Path],
        source_before: str,
        run_id: str | None = None,
        return_receipt: bool = False,
    ) -> (
        CodeBenchmarkSandboxError
        | None
        | tuple[CodeBenchmarkCleanupReceipt | None, CodeBenchmarkSandboxError | None]
    ):
        failures: list[str] = []
        direct_children: tuple[Path, ...] = ()
        if _path_entry_exists(run_root):
            try:
                _assert_direct_child(run_root, self._source.worktree_root)
                if _is_link_or_reparse_point(
                    run_root,
                    detail="private benchmark run root",
                ):
                    # Never enumerate a link/junction.  Removing the direct
                    # child itself is safe; recursing into its target is not.
                    _remove_private_tree(run_root)
                elif not run_root.is_dir():
                    failures.append("private benchmark run root is not a directory")
                else:
                    direct_children = tuple(run_root.iterdir())
            except BaseException as exc:  # noqa: BLE001 - continue independent cleanup
                failures.append(f"run root inventory failed: {exc}")
        targets = tuple(dict.fromkeys((*opened_targets, *direct_children)))
        for target in reversed(targets):
            try:
                _assert_direct_child(target, run_root)
                if _path_entry_exists(target) and _is_link_or_reparse_point(
                    target,
                    detail="private benchmark worktree",
                ):
                    _remove_private_tree(target)
                elif (
                    _path_entry_exists(private_bare)
                    and not _is_link_or_reparse_point(
                        private_bare,
                        detail="private benchmark bare repository",
                    )
                    and target.exists()
                ):
                    try:
                        _git(private_bare, "worktree", "remove", "--force", str(target))
                    except CodeBenchmarkSandboxError:
                        # A failed `worktree add` can leave a directory which
                        # Git has not registered.  The bare repo is disposable;
                        # remove only this validated direct child and later
                        # prune stale metadata before deleting the bare clone.
                        _remove_private_tree(target)
                    _git(private_bare, "worktree", "prune", "--expire=now")
                if _path_entry_exists(target):
                    _remove_private_tree(target)
                if _path_entry_exists(target):
                    failures.append("private benchmark worktree was not removed")
            except BaseException as exc:  # noqa: BLE001 - report all cleanup evidence
                failures.append(f"worktree cleanup failed: {exc}")
        try:
            if _path_entry_exists(run_root):
                _assert_direct_child(run_root, self._source.worktree_root)
                if _is_link_or_reparse_point(
                    run_root,
                    detail="private benchmark run root",
                ):
                    _remove_private_tree(run_root)
                elif not run_root.is_dir():
                    failures.append("private benchmark run root is not a directory")
                elif any(run_root.iterdir()):
                    failures.append("benchmark run root is not empty after worktree cleanup")
                else:
                    run_root.rmdir()
        except BaseException as exc:  # noqa: BLE001
            failures.append(f"run root cleanup failed: {exc}")
        try:
            if _path_entry_exists(private_bare):
                _assert_direct_child(private_bare, self._source.private_bare_root)
                if _is_link_or_reparse_point(
                    private_bare,
                    detail="private benchmark bare repository",
                ):
                    _remove_private_tree(private_bare)
                elif not private_bare.is_dir():
                    failures.append("private benchmark bare repository is not a directory")
                else:
                    shutil.rmtree(private_bare, onerror=_retry_readonly_removal)
            if _path_entry_exists(private_bare):
                failures.append("private bare repository was not removed")
        except BaseException as exc:  # noqa: BLE001
            failures.append(f"private bare cleanup failed: {exc}")
        source_after: str | None = None
        try:
            source_after = self._source_fingerprint()
            if source_after != source_before:
                failures.append("protected source repository changed during benchmark run")
        except BaseException as exc:  # noqa: BLE001
            failures.append(f"protected source verification failed: {exc}")
        cleanup_error = (
            None if not failures else CodeBenchmarkSandboxError("; ".join(failures))
        )
        if not return_receipt:
            return cleanup_error
        if cleanup_error is not None:
            return None, cleanup_error
        if not isinstance(run_id, str) or not run_id.strip() or source_after is None:
            return None, CodeBenchmarkSandboxError(
                "cleanup receipt requires a run id and verified source fingerprint"
            )
        try:
            receipt = CodeBenchmarkCleanupReceipt(
                run_id=run_id,
                source_before_digest=source_before,
                source_after_digest=source_after,
                private_bare_removed=not _path_entry_exists(private_bare),
                worktrees_removed=all(
                    not _path_entry_exists(target) for target in opened_targets
                ),
                run_root_removed=not _path_entry_exists(run_root),
            )
        except CodeBenchmarkContractError as exc:
            error = CodeBenchmarkSandboxError("cleanup could not produce a verified receipt")
            error.__cause__ = exc
            return None, error
        return receipt, None


def _git(repository: Path, *args: str) -> str:
    return _git_process("-C", str(repository), *args).decode("utf-8", errors="strict").strip()


def _git_nul_list(repository: Path, *args: str) -> tuple[str, ...]:
    raw = _git_process("-C", str(repository), *args)
    if not raw:
        return ()
    if not raw.endswith(b"\0"):
        raise CodeBenchmarkSandboxError("Git path inventory is not NUL-terminated")
    try:
        return tuple(item.decode("utf-8", errors="strict") for item in raw[:-1].split(b"\0"))
    except UnicodeDecodeError as exc:
        raise CodeBenchmarkSandboxError("Git path inventory is not UTF-8") from exc


def _git_bytes_digest(repository: Path, *args: str) -> str:
    return hashlib.sha256(_git_process("-C", str(repository), *args)).hexdigest()


def _git_refs_digest(repository: Path) -> str:
    return hashlib.sha256(
        _git_process(
            "-C",
            str(repository),
            "for-each-ref",
            "--format=%(refname)%00%(objectname)%00%(objecttype)",
        )
    ).hexdigest()


def _worktree_file_digest(worktree: Path, relative_path: str) -> str:
    target = worktree.joinpath(*relative_path.split("/"))
    _assert_path_under(target.parent.resolve(strict=False), worktree)
    _reject_symlink_components(target, worktree)
    if (
        _is_link_or_reparse_point(target, detail="untracked benchmark evidence")
        or not target.is_file()
    ):
        raise CodeBenchmarkSandboxError(
            "untracked benchmark evidence must be a regular non-symlink file"
        )
    return _file_sha256(target, detail="untracked benchmark evidence")


def _source_git_directory(source: Path) -> Path:
    if _is_link_or_reparse_point(source, detail="protected source repository"):
        raise CodeBenchmarkSandboxError(
            "protected source_repository must not be a link or reparse point"
        )
    raw_git_directory = source / ".git"
    if (
        not raw_git_directory.is_dir()
        or _is_link_or_reparse_point(
            raw_git_directory,
            detail="protected source Git directory",
        )
    ):
        raise CodeBenchmarkSandboxError(
            "protected source_repository must own a non-linked .git directory"
        )
    expected = raw_git_directory.resolve(strict=True)
    common = _resolve_git_path(source, _git(source, "rev-parse", "--git-common-dir"))
    if expected.parent != source or common != expected:
        raise CodeBenchmarkSandboxError(
            "protected source_repository must own a non-shared .git directory"
        )
    return common


def _git_control_digest(git_directory: Path) -> str:
    """Hash mutable Git control state without reading the object database."""

    control_paths = (
        "HEAD",
        "ORIG_HEAD",
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "config",
        "packed-refs",
        "index",
        "refs",
        "logs",
        "worktrees",
        "rebase-apply",
        "rebase-merge",
        "hooks",
        "info",
    )
    entries: list[dict[str, str]] = []
    for raw_relative in control_paths:
        root = git_directory / raw_relative
        if not root.exists() and not root.is_symlink():
            continue
        if _is_link_or_reparse_point(root, detail="protected source Git control state"):
            raise CodeBenchmarkSandboxError("protected source Git control state contains symlink")
        if root.is_file():
            entries.append(
                {
                    "path": raw_relative,
                    "sha256": _file_sha256(root, detail="protected source Git control state"),
                }
            )
            continue
        if not root.is_dir():
            raise CodeBenchmarkSandboxError("protected source Git control state is unsupported")
        for directory, directories, files in os.walk(root, followlinks=False):
            directories.sort()
            files.sort()
            directory_path = Path(directory)
            for name in directories:
                if _is_link_or_reparse_point(
                    directory_path / name,
                    detail="protected source Git control state",
                ):
                    raise CodeBenchmarkSandboxError(
                        "protected source Git control state contains symlink"
                    )
            for name in files:
                candidate = directory_path / name
                if (
                    _is_link_or_reparse_point(
                        candidate,
                        detail="protected source Git control state",
                    )
                    or not candidate.is_file()
                ):
                    raise CodeBenchmarkSandboxError(
                        "protected source Git control state contains unsupported file"
                    )
                entries.append(
                    {
                        "path": candidate.relative_to(git_directory).as_posix(),
                        "sha256": _file_sha256(
                            candidate,
                            detail="protected source Git control state",
                        ),
                    }
                )
    return _digest_json(entries)


def _file_sha256(path: Path, *, detail: str) -> str:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CodeBenchmarkSandboxError(f"cannot inspect {detail}") from exc
    if size < 0 or size > _MAX_GIT_OUTPUT_BYTES:
        raise CodeBenchmarkSandboxError(f"{detail} exceeded size limit")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise CodeBenchmarkSandboxError(f"cannot read {detail}") from exc
    return digest.hexdigest()


def _is_link_or_reparse_point(path: Path, *, detail: str) -> bool:
    """Detect symlinks and Windows junction-like reparse points without following them."""

    if path.is_symlink():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError as exc:
        raise CodeBenchmarkSandboxError(f"cannot inspect {detail}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _remove_private_tree(target: Path) -> None:
    """Remove one already-validated private-root child without following links."""

    if target.is_symlink():
        target.unlink()
    elif _is_link_or_reparse_point(target, detail="private benchmark artifact"):
        if target.is_dir():
            target.rmdir()
        else:
            target.unlink()
    elif target.is_file():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target, onerror=_retry_readonly_removal)


def _git_environment() -> dict[str, str]:
    """Use a minimal Git environment; this is hardening, not OS isolation."""

    environment = {
        name: os.environ[name]
        for name in (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
        )
        if os.environ.get(name)
    }
    environment.update(
        {
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _git_process(*args: str) -> bytes:
    try:
        completed = subprocess.run(
            ("git", *_GIT_HARDENING_CONFIG, *args),
            capture_output=True,
            check=False,
            env=_git_environment(),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodeBenchmarkSandboxError("Git command could not complete inside sandbox") from exc
    if len(completed.stdout) > _MAX_GIT_OUTPUT_BYTES or len(completed.stderr) > _MAX_GIT_OUTPUT_BYTES:
        raise CodeBenchmarkSandboxError("Git command exceeded sandbox output limit")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CodeBenchmarkSandboxError(f"private benchmark Git operation failed: {detail[:400]}")
    return bytes(completed.stdout)


def _path_entry_exists(path: Path) -> bool:
    """Return whether a path entry exists without following a broken link."""

    return os.path.lexists(path)


def _assert_private_directory(
    candidate: Path,
    configured_root: Path,
    *,
    detail: str,
    require_exists: bool,
) -> None:
    _assert_direct_child(candidate, configured_root)
    if not _path_entry_exists(candidate):
        if require_exists:
            raise CodeBenchmarkSandboxError(f"{detail} does not exist")
        return
    if _is_link_or_reparse_point(candidate, detail=detail):
        raise CodeBenchmarkSandboxError(f"{detail} must not be a link or reparse point")
    if not candidate.is_dir():
        raise CodeBenchmarkSandboxError(f"{detail} must be a directory")


def _existing_directory(value: Path | str, field_name: str) -> Path:
    try:
        raw_path = Path(value).expanduser()
        if not raw_path.is_dir() or _is_link_or_reparse_point(
            raw_path,
            detail=field_name,
        ):
            raise CodeBenchmarkSandboxError(
                f"{field_name} must be an existing non-link directory"
            )
        path = raw_path.resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as exc:
        raise CodeBenchmarkSandboxError(f"{field_name} must be an existing directory") from exc
    if not path.is_dir():
        raise CodeBenchmarkSandboxError(f"{field_name} must be an existing directory")
    return path


def _existing_directories(values: Sequence[Path], field_name: str) -> tuple[Path, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise CodeBenchmarkSandboxError(f"{field_name} must be an explicit sequence")
    output: list[Path] = []
    for index, value in enumerate(values):
        path = _existing_directory(value, f"{field_name}[{index}]")
        if path not in output:
            output.append(path)
    if not output:
        raise CodeBenchmarkSandboxError(f"{field_name} must not be empty")
    return tuple(output)


def _opaque_ref(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkSandboxError(f"{field_name} must be text")
    text = value.strip()
    if (
        len(text) > 1_024
        or not _GOVERNED_REF_RE.fullmatch(text)
        or text.lower().startswith("file://")
        or "/../" in text
        or text.endswith("/..")
    ):
        raise CodeBenchmarkSandboxError(f"{field_name} must be an opaque governed ref")
    return text


def _commit(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _COMMIT_RE.fullmatch(value.strip().lower()):
        raise CodeBenchmarkSandboxError(f"{field_name} must be a full Git commit id")
    return value.strip().lower()


def _run_id(value: Any) -> str:
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value.strip().lower()):
        raise CodeBenchmarkSandboxError("run_id must be a lowercase opaque identifier")
    return value.strip().lower()


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _assert_direct_child(candidate: Path, root: Path) -> None:
    if _is_link_or_reparse_point(root, detail="configured private root"):
        raise CodeBenchmarkSandboxError("configured private root must not be a link or reparse point")
    if candidate.parent.resolve(strict=True) != root.resolve(strict=True):
        raise CodeBenchmarkSandboxError("sandbox target is not a direct configured-root child")


def _assert_path_under(candidate: Path, root: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CodeBenchmarkSandboxError("sandbox path escapes its worktree root") from exc


def _reject_symlink_components(
    target: Path,
    root: Path,
) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise CodeBenchmarkSandboxError("sandbox path escapes its worktree root") from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if not cursor.exists() and not cursor.is_symlink():
            # A missing leaf is expected for a permitted write.  Any later
            # component is also still absent, so there is no link to follow.
            continue
        if _is_link_or_reparse_point(cursor, detail="benchmark workspace path"):
            raise CodeBenchmarkSandboxError("benchmark workspace rejects symlink path components")


def _resolve_git_path(repository: Path, raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = repository / candidate
    try:
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CodeBenchmarkSandboxError("Git returned an unavailable common directory") from exc


def _assert_counter_mapping(value: Any, *, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise CodeBenchmarkSandboxError(f"{field_name} must be a mapping")
    for key, amount in value.items():
        if (
            not isinstance(key, str)
            or not key
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
        ):
            raise CodeBenchmarkSandboxError(
                f"{field_name} must contain non-negative integer counters"
            )


def _executor_cost_is_bound_by_verifier(
    *,
    executor_cost: Mapping[str, int],
    verifier: CodeVerifierResult,
) -> bool:
    """Preserve D17 equality unless a D19 receipt owns the two extra counters.

    A legacy trusted verifier still has the D17 exact-match contract.  Only a
    self-consistent ``CodeBenchmarkControlledVerificationReceipt`` may add
    D19's server-observed test-command and wall-time dimensions; an arbitrary
    in-process verifier cannot self-report those dimensions into canonical
    durable evidence.
    """

    verifier_cost = verifier.cost
    _assert_counter_mapping(verifier_cost, field_name="verifier cost")
    expected = dict(executor_cost)
    observed = dict(verifier_cost)
    controlled = verifier.controlled_receipt
    if controlled is None:
        return observed == expected
    if dict(controlled.cost) != observed:
        return False
    if any(observed.get(name) != amount for name, amount in expected.items()):
        return False
    additional = set(observed) - set(expected)
    return additional <= {"test_command_count", "verification_wall_time_ms"}


def _digest_json(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CodeBenchmarkSandboxError("benchmark evidence must be JSON-safe") from exc
    return hashlib.sha256(payload).hexdigest()


def _retry_readonly_removal(
    operation: Any,
    path: str,
    exc_info: tuple[type[BaseException], BaseException, Any],
) -> None:
    """Retry one Windows read-only deletion only within a validated private root."""

    del exc_info
    try:
        os.chmod(path, stat.S_IWRITE)
        operation(path)
    except OSError as exc:
        raise CodeBenchmarkSandboxError("private bare artifact cannot be removed") from exc


__all__ = (
    "CODE_BENCHMARK_SANDBOX_ERROR",
    "CodeBenchmarkArmExecutor",
    "CodeBenchmarkSandboxError",
    "CodeBenchmarkVerifier",
    "CodeBenchmarkWorkspace",
    "CodeHistoricalBenchmarkSandbox",
    "PrivateBareBenchmarkSource",
)
