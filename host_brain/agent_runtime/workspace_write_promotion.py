"""Deterministic Git materialization for governed Workspace write results."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from host_brain.agent_runtime.workspace_paths import resolve_workspace_path
from host_brain.persistence.workspace_repository import (
    WORKSPACE_AUTHORITY_INVALID,
    WorkspaceAuthority,
    WorkspaceRepositoryError,
)


_MAX_PATCH_BYTES = 32 * 1024 * 1024
_MAX_PATHS = 1_000
_SECRET_PATH_NAMES = frozenset(
    {
        ".env",
        ".ssh",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)

GitOutput = Callable[..., str]
GitBytes = Callable[..., bytes]
GitReturnCode = Callable[..., int]
GitOutputWithEnv = Callable[..., str]
PathAuthorizer = Callable[[WorkspaceAuthority, str], None]


# 候选物封存相对冻结基线的差异字节；验收前它只是证据，不能直接改变共享分支。
@dataclass(frozen=True, slots=True)
class WorkspaceWriteCandidate:
    workspace_id: str
    base_commit: str
    changed_paths: tuple[str, ...]
    diff_digest: str
    patch_bytes: bytes
    receipt_ref: str

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "Natsume.agent.workspace_write_candidate.v1",
            "workspace_id": self.workspace_id,
            "base_commit": self.base_commit,
            "changed_paths": list(self.changed_paths),
            "diff_digest": self.diff_digest,
            "patch_size": len(self.patch_bytes),
            "receipt_ref": self.receipt_ref,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceWriteSnapshot:
    workspace_id: str
    base_commit: str
    accepted_head: str
    tree_digest: str
    diff_digest: str
    receipt_ref: str


def capture_workspace_write_candidate(
    *,
    authority: WorkspaceAuthority,
    workspace: Path,
    base_commit: str,
    git_object_pattern: Any,
    git_output: GitOutput,
    git_bytes: GitBytes,
    git_returncode: GitReturnCode,
    authorize_path: PathAuthorizer,
) -> WorkspaceWriteCandidate | None:
    """Capture a bounded binary patch relative to the frozen source commit."""

    # 此处捕获的是待验收的字节级候选物，不是向共享分支提交的授权；后续必须由
    # 持有同一 WorkspaceAuthority 的提升路径再次验证，避免 worker 自行发布。
    # 当前分支必须仍从冻结 source 提交演化，避免工作区被重定基后将无关历史作为本次产物晋升。
    current_head = git_output(
        workspace,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
    ).lower()
    if (
        git_object_pattern.fullmatch(current_head) is None
        or git_returncode(
            workspace,
            "merge-base",
            "--is-ancestor",
            base_commit,
            current_head,
        )
        != 0
    ):
        _invalid("Workspace write branch no longer descends from its frozen source")
    untracked = _git_nul_paths(
        git_bytes(
            workspace,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
    )
    if untracked:
        _validate_write_paths(
            authority,
            untracked,
            workspace=workspace,
            authorize_path=authorize_path,
        )
        git_output(
            workspace,
            "add",
            "--intent-to-add",
            "--",
            *untracked,
        )
    changed_paths = _git_nul_paths(
        git_bytes(
            workspace,
            "diff",
            "--name-only",
            "-z",
            base_commit,
            "--",
        )
    )
    if not changed_paths:
        # “没有差异”是合法且可区分的结果；不制造空补丁或伪造提交，让上层能
        # 以明确的无变更收据结束，而不是把空产物当作一次写入成功。
        return None
    # 路径检查在读取补丁前执行，阻断秘密文件、Git 元数据、符号链接与越界目标。
    _validate_write_paths(
        authority,
        changed_paths,
        workspace=workspace,
        authorize_path=authorize_path,
    )
    patch_bytes = git_bytes(
        workspace,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        base_commit,
        "--",
    )
    if not patch_bytes or len(patch_bytes) > _MAX_PATCH_BYTES:
        _invalid("Workspace write patch is empty or exceeds 32 MiB")
    diff_digest = "sha256:" + hashlib.sha256(patch_bytes).hexdigest()
    # 收据同时绑定工作区、冻结基线、路径集、完整补丁摘要和大小；它用于证明
    # 被审核的候选究竟是哪一份，而不会把可能含敏感内容的 patch 写进元数据。
    receipt_digest = hashlib.sha256(
        json.dumps(
            {
                "workspace_id": authority.workspace_id,
                "base_commit": base_commit,
                "changed_paths": list(changed_paths),
                "diff_digest": diff_digest,
                "patch_size": len(patch_bytes),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return WorkspaceWriteCandidate(
        workspace_id=authority.workspace_id,
        base_commit=base_commit,
        changed_paths=changed_paths,
        diff_digest=diff_digest,
        patch_bytes=patch_bytes,
        receipt_ref=f"workspace-write-candidate://sha256/{receipt_digest}",
    )


def accept_workspace_write_candidate(
    *,
    authority: WorkspaceAuthority,
    workspace: Path,
    base_commit: str,
    branch: str,
    manifest: Mapping[str, Any],
    patch_bytes: bytes,
    git_object_pattern: Any,
    git_output: GitOutput,
    git_bytes: GitBytes,
    git_returncode: GitReturnCode,
    git_output_with_env: GitOutputWithEnv,
    authorize_path: PathAuthorizer,
) -> WorkspaceWriteSnapshot:
    """Revalidate sealed bytes and create a deterministic accepted commit."""

    # 提升入口绝不信任调用方给出的 manifest/patch；先从当前 worktree 重新捕获，
    # 才能发现候选审核后发生的修改、路径新增或基线漂移。

    # 验收时从 live worktree 重新捕获并逐字比较密封补丁，防止审查后工作区被悄悄修改。
    candidate = capture_workspace_write_candidate(
        authority=authority,
        workspace=workspace,
        base_commit=base_commit,
        git_object_pattern=git_object_pattern,
        git_output=git_output,
        git_bytes=git_bytes,
        git_returncode=git_returncode,
        authorize_path=authorize_path,
    )
    if (
        candidate is None
        or dict(manifest) != candidate.manifest()
        or patch_bytes != candidate.patch_bytes
    ):
        _invalid("sealed Workspace write candidate differs from the live worktree")
    git_output(workspace, "add", "-A", "--")
    tree_object = git_output(workspace, "write-tree").lower()
    if git_object_pattern.fullmatch(tree_object) is None:
        _invalid("Workspace write produced a non-canonical Git tree")
    materialized_patch = git_bytes(
        workspace,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        base_commit,
        tree_object,
        "--",
    )
    if materialized_patch != patch_bytes:
        _invalid("staged Workspace tree differs from its sealed patch")
    message = (
        f"Natsume accepted Workspace {authority.workspace_id}\n\n"
        f"Candidate: {candidate.receipt_ref}"
    )
    # 固定 Git 身份与时间让相同树和基线得到确定性 accepted commit，便于可审计重放。
    accepted_head = git_output_with_env(
        workspace,
        "commit-tree",
        tree_object,
        "-p",
        base_commit,
        "-m",
        message,
        extra_env={
            "GIT_AUTHOR_NAME": "Natsume Governed Runtime",
            "GIT_AUTHOR_EMAIL": "runtime@natsume.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_NAME": "Natsume Governed Runtime",
            "GIT_COMMITTER_EMAIL": "runtime@natsume.invalid",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        },
    ).lower()
    if git_object_pattern.fullmatch(accepted_head) is None:
        _invalid("Workspace accepted HEAD is not a canonical Git object")
    current_head = git_output(
        workspace,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
    ).lower()
    git_output(
        workspace,
        "update-ref",
        f"refs/heads/{branch}",
        accepted_head,
        current_head,
    )
    # update-ref 已携带 current_head 作为 compare-and-swap 栅栏：另一执行者若已
    # 推进目标分支，本次验收会失败而非覆盖其已接受的产物。
    git_output(workspace, "reset", "--hard", accepted_head)
    if (
        git_output(
            workspace,
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
        ).lower()
        != accepted_head
        or git_output(
            workspace,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    ):
        _invalid("Workspace accepted HEAD did not leave a clean exact worktree")
    tree_digest = "sha256:" + hashlib.sha256(
        f"git-tree:{tree_object}".encode("ascii")
    ).hexdigest()
    receipt_digest = hashlib.sha256(
        (
            f"{authority.workspace_id}\n{base_commit}\n{accepted_head}\n"
            f"{tree_digest}\n{candidate.diff_digest}\n{candidate.receipt_ref}"
        ).encode("utf-8")
    ).hexdigest()
    return WorkspaceWriteSnapshot(
        workspace_id=authority.workspace_id,
        base_commit=base_commit,
        accepted_head=accepted_head,
        tree_digest=tree_digest,
        diff_digest=candidate.diff_digest,
        receipt_ref=f"workspace-write://sha256/{receipt_digest}",
    )


def _validate_write_paths(
    authority: WorkspaceAuthority,
    paths: tuple[str, ...],
    *,
    workspace: Path,
    authorize_path: PathAuthorizer,
) -> None:
    # Promotion 采用白名单式路径约束与总量上限，绝不把工作区的任意文件系统对象视为可提交材料。
    if len(paths) > _MAX_PATHS:
        _invalid("Workspace write candidate exceeds 1000 changed paths")
    total_material_bytes = 0
    for candidate in paths:
        path_parts = tuple(part.lower() for part in candidate.split("/"))
        if (
            not candidate
            or len(candidate) > 512
            or candidate.startswith("/")
            or candidate == ".git"
            or candidate.startswith(".git/")
            or "\\" in candidate
            or any(ord(character) < 32 for character in candidate)
            or any(part in _SECRET_PATH_NAMES for part in path_parts)
        ):
            _invalid("Workspace write candidate contains an unsafe path")
        target = resolve_workspace_path(workspace, candidate)
        authorize_path(authority, target.relative_to(workspace).as_posix())
        if target.is_symlink():
            _invalid("Workspace write promotion rejects symbolic links")
        if target.exists() and target.is_dir():
            _invalid("Workspace write promotion rejects Git links and directories")
        if target.exists():
            try:
                total_material_bytes += target.stat().st_size
            except OSError as exc:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "Workspace write candidate file cannot be measured",
                ) from exc
            if total_material_bytes > _MAX_PATCH_BYTES:
                _invalid("Workspace write material exceeds 32 MiB")


def _git_nul_paths(payload: bytes) -> tuple[str, ...]:
    try:
        decoded = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WorkspaceRepositoryError(
            WORKSPACE_AUTHORITY_INVALID,
            "Workspace Git path is not valid UTF-8",
        ) from exc
    values = tuple(item for item in decoded.split("\0") if item)
    if len(values) != len(set(values)):
        _invalid("Workspace Git path inventory contains duplicates")
    return tuple(sorted(values))


def _invalid(detail: str) -> None:
    raise WorkspaceRepositoryError(WORKSPACE_AUTHORITY_INVALID, detail)


__all__ = [
    "WorkspaceWriteCandidate",
    "WorkspaceWriteSnapshot",
    "accept_workspace_write_candidate",
    "capture_workspace_write_candidate",
]
