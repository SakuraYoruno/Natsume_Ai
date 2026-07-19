"""Artifact persistence for Agent runtime v2.

The generic/legacy helpers remain for non-governed callers.  Governed Internal
Agent execution uses :class:`GovernedAgentArtifactStage`: raw WorkOrders,
prompts and full result material stay below an exact Workspace/Attempt staging
owner until the post-Acceptance effect promotes the sealed directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, BinaryIO, Callable

from . import artifact_store_read_model as _read_model


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEX_RUNS_ROOT = REPO_ROOT / "artifacts" / "codex_runs"

GOVERNED_AGENT_STAGE_SCHEMA = "Natsume.agent.governed_artifact_stage.v1"
GOVERNED_AGENT_STAGE_INVALID = "GOVERNED_AGENT_STAGE_INVALID"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_STAGE_REF = re.compile(
    r"^agent-stage://([A-Za-z0-9._-]{1,120})/"
    r"([A-Za-z0-9._-]{1,240})/sha256/([0-9a-f]{64})$"
)
_TOMBSTONE_REF = re.compile(
    r"^agent-artifact-abandon://([A-Za-z0-9._-]{1,120})/"
    r"([A-Za-z0-9._-]{1,240})/sha256/([0-9a-f]{64})$"
)
_AUTHORITY_FIELDS = (
    "root_work_order_id",
    "graph_id",
    "graph_revision",
    "node_id",
    "attempt_id",
    "lease_id",
    "command_id",
    "principal_id",
    "lease_owner_id",
    "fencing_token",
)
_SEALED_FILES = (
    "work_order.json",
    "prompt.txt",
    "metadata.json",
    "result.json",
    "changed_files.json",
    "verification.json",
)
_PROJECTION_RECOVERY_FILE = "projection_recovery.json"


class GovernedAgentArtifactError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.reason_code = GOVERNED_AGENT_STAGE_INVALID
        self.detail = str(detail or "governed Agent artifact stage is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class GovernedAgentArtifactStage:
    stage_ref: str
    stage_dir: Path
    workspace_id: str
    attempt_id: str
    binding_digest: str
    execution_authority: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GovernedAgentArtifactSeal:
    stage_ref: str
    seal_digest: str
    workspace_id: str
    attempt_id: str
    execution_authority: dict[str, Any]
    stage_dir: Path
    accepted_dir: Path | None = None

    def protected_payload(self) -> dict[str, str]:
        return {
            "schema_version": GOVERNED_AGENT_STAGE_SCHEMA,
            "ref": self.stage_ref,
            "digest": self.seal_digest,
        }


def sanitize_segment(value: str, fallback: str = "agent-run") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip()).strip(".-")
    return cleaned or fallback


def governed_attempt_segment(attempt_id: str) -> str:
    """Return the canonical opaque directory segment for a raw Attempt id."""

    return _attempt_segment(attempt_id)


class AgentArtifactStore:
    def __init__(
        self,
        runs_root: str | Path | None = None,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        configured_root = (
            Path(runs_root).expanduser() if runs_root else DEFAULT_CODEX_RUNS_ROOT
        )
        # Keep the configured path lexical.  Resolving here would follow a
        # pre-positioned symlink/junction and erase the evidence that the
        # governed namespace crossed a filesystem authority boundary.
        self.runs_root = _absolute_lexical(configured_root)
        self._fault_injector = fault_injector

    def prepare_run_dir(self, work_order_id: str) -> Path:
        run_dir = self.runs_root / sanitize_segment(work_order_id, "work-order")
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def prepare_governed_stage(
        self,
        *,
        workspace_id: str,
        execution_authority: dict[str, Any],
    ) -> GovernedAgentArtifactStage:
        """Create or exactly reopen one Workspace/Attempt-owned stage."""

        workspace = _portable_segment(workspace_id, field="workspace_id", maximum=120)
        authority = _canonical_authority(execution_authority)
        attempt_id = _attempt_segment(authority["attempt_id"])
        expected_workspace = _workspace_id_for(
            graph_id=authority["graph_id"],
            graph_revision=authority["graph_revision"],
        )
        if workspace != expected_workspace:
            raise GovernedAgentArtifactError(
                "stage workspace differs from the canonical graph epoch"
            )
        binding = {
            "schema_version": GOVERNED_AGENT_STAGE_SCHEMA,
            "workspace_id": workspace,
            "execution_authority": authority,
        }
        binding_digest = _canonical_digest(binding)
        stage_ref = (
            f"agent-stage://{workspace}/{attempt_id}/sha256/"
            f"{binding_digest.split(':', 1)[1]}"
        )
        stage_dir = self._stage_dir(workspace, attempt_id)
        self._guard_authority_paths(workspace, attempt_id)
        if _path_lexists(self._tombstone_path(workspace, attempt_id)):
            raise GovernedAgentArtifactError(
                "abandoned artifact already owns this Workspace/Attempt"
            )
        if _path_lexists(self._accepted_dir(workspace, attempt_id)):
            raise GovernedAgentArtifactError(
                "accepted artifact already owns this Workspace/Attempt"
            )
        owner_path = stage_dir / "owner.json"
        if _path_lexists(stage_dir):
            _require_regular_directory(stage_dir, root=self.runs_root)
            self._guard_governed_target(owner_path, final_kind="file")
            if not _path_lexists(owner_path):
                # A process may have died after fsyncing the same-directory
                # temp but before owner replacement.  No raw material is
                # trusted until owner.json exists, so only that exact orphan
                # shape may be removed and recreated.
                entries = list(stage_dir.iterdir())
                if entries and not all(
                    entry.name == ".tmp"
                    and _is_regular_file_without_reparse(entry)
                    for entry in entries
                ):
                    raise GovernedAgentArtifactError(
                        "ownerless governed stage contains unknown material"
                    )
                for entry in entries:
                    self._guard_governed_target(entry, final_kind="file")
                    _require_regular_file(entry, root=self.runs_root)
                    entry.unlink()
                _require_regular_directory(stage_dir, root=self.runs_root)
                stage_dir.rmdir()
                self._ensure_governed_directory(stage_dir)
                self._write_governed_metadata(
                    owner_path,
                    {
                        **binding,
                        "binding_digest": binding_digest,
                        "stage_ref": stage_ref,
                    },
                    fault_prefix="owner",
                )
            existing = _read_json_object(owner_path, field="stage owner")
            if existing != {**binding, "binding_digest": binding_digest, "stage_ref": stage_ref}:
                raise GovernedAgentArtifactError(
                    "existing stage owner differs from the exact execution authority"
                )
            if _path_lexists(stage_dir / "seal.json") or _path_lexists(
                stage_dir / "promotion.json"
            ):
                raise GovernedAgentArtifactError(
                    "sealed governed stage cannot be reopened by a worker"
                )
        else:
            self._ensure_governed_directory(stage_dir)
            try:
                self._write_governed_metadata(
                    owner_path,
                    {
                        **binding,
                        "binding_digest": binding_digest,
                        "stage_ref": stage_ref,
                    },
                    fault_prefix="owner",
                )
            except Exception:
                # A crash/fault before owner replacement must not strand an
                # ownerless attempt directory that can never be recovered.
                try:
                    if _path_lexists(stage_dir):
                        _require_regular_directory(stage_dir, root=self.runs_root)
                        stage_dir.rmdir()
                except OSError:
                    pass
                raise
        _require_regular_directory(stage_dir, root=self.runs_root)
        return GovernedAgentArtifactStage(
            stage_ref=stage_ref,
            stage_dir=stage_dir,
            workspace_id=workspace,
            attempt_id=attempt_id,
            binding_digest=binding_digest,
            execution_authority=authority,
        )

    def seal_governed_stage(
        self,
        stage: GovernedAgentArtifactStage,
    ) -> GovernedAgentArtifactSeal:
        """Seal the exact bounded file inventory before it enters a projection."""

        reopened = self._load_stage(stage.stage_ref)
        if (
            reopened.stage_dir != stage.stage_dir
            or reopened.execution_authority != stage.execution_authority
        ):
            raise GovernedAgentArtifactError("stage identity drifted before sealing")
        self._discard_stale_metadata_temp(stage.stage_dir)
        seal_exists = _path_lexists(stage.stage_dir / "seal.json")
        recovery_exists = _path_lexists(
            stage.stage_dir / _PROJECTION_RECOVERY_FILE
        )
        sealed_files = (
            *_SEALED_FILES,
            *((_PROJECTION_RECOVERY_FILE,) if recovery_exists else ()),
        )
        _require_exact_inventory(
            stage.stage_dir,
            allowed={
                "owner.json",
                *sealed_files,
                *({"seal.json"} if seal_exists else set()),
            },
        )
        files: dict[str, str] = {}
        for relative in sealed_files:
            path = stage.stage_dir / relative
            _require_regular_file(path, root=stage.stage_dir)
            files[relative] = _file_digest(path)
        seal_payload = {
            "schema_version": GOVERNED_AGENT_STAGE_SCHEMA,
            "stage_ref": stage.stage_ref,
            "binding_digest": stage.binding_digest,
            "files": files,
        }
        seal_digest = _canonical_digest(seal_payload)
        seal_record = {**seal_payload, "seal_digest": seal_digest}
        seal_path = stage.stage_dir / "seal.json"
        self._guard_governed_target(seal_path, final_kind="file")
        if _path_lexists(seal_path):
            if _read_json_object(seal_path, field="stage seal") != seal_record:
                raise GovernedAgentArtifactError("existing stage seal drifted")
        else:
            self._write_governed_metadata(
                seal_path,
                seal_record,
                fault_prefix="seal",
            )
        return GovernedAgentArtifactSeal(
            stage_ref=stage.stage_ref,
            seal_digest=seal_digest,
            workspace_id=stage.workspace_id,
            attempt_id=stage.attempt_id,
            execution_authority=dict(stage.execution_authority),
            stage_dir=stage.stage_dir,
        )

    def discard_unsealed_governed_stage(
        self,
        stage: GovernedAgentArtifactStage,
    ) -> None:
        """Remove only an exact, owner-bound stage that was never sealed."""

        self._guard_authority_paths(stage.workspace_id, stage.attempt_id)
        if _path_lexists(self._accepted_dir(stage.workspace_id, stage.attempt_id)):
            raise GovernedAgentArtifactError("accepted artifact cannot be discarded")
        if _path_lexists(self._tombstone_path(stage.workspace_id, stage.attempt_id)):
            raise GovernedAgentArtifactError("abandoned artifact cannot be discarded")
        reopened = self._load_stage(stage.stage_ref)
        if (
            reopened.stage_dir != stage.stage_dir
            or reopened.execution_authority != stage.execution_authority
        ):
            raise GovernedAgentArtifactError("unsealed stage identity drifted")
        if _path_lexists(stage.stage_dir / "seal.json") or _path_lexists(
            stage.stage_dir / "promotion.json"
        ):
            raise GovernedAgentArtifactError("sealed stage cannot be discarded")
        entries = list(stage.stage_dir.iterdir())
        allowed = {"owner.json", *_SEALED_FILES, _PROJECTION_RECOVERY_FILE}
        for entry in entries:
            if entry.name not in allowed or not _is_regular_file_without_reparse(entry):
                raise GovernedAgentArtifactError(
                    "unsealed stage inventory is not safe to discard"
                )
        self._guard_authority_paths(stage.workspace_id, stage.attempt_id)
        _require_regular_directory(stage.stage_dir, root=self.runs_root)
        self._remove_governed_attempt_directory(stage.stage_dir)

    def inspect_governed_stage(
        self,
        *,
        stage_ref: str,
        seal_digest: str,
        discard_stale_temp: bool = True,
    ) -> GovernedAgentArtifactSeal:
        """Reopen either STAGING or ACCEPTED material and verify every byte."""

        expected_digest = str(seal_digest or "").strip().lower()
        if not _DIGEST.fullmatch(expected_digest):
            raise GovernedAgentArtifactError("stage seal digest is invalid")
        stage = self._load_stage(stage_ref, allow_accepted=True)
        if discard_stale_temp:
            self._discard_stale_metadata_temp(stage.stage_dir)
        seal = _read_json_object(stage.stage_dir / "seal.json", field="stage seal")
        files = seal.get("files")
        if not isinstance(files, dict):
            raise GovernedAgentArtifactError("stage sealed file inventory drifted")
        actual_files = tuple(sorted(str(value) for value in files))
        base_files = tuple(sorted(_SEALED_FILES))
        recoverable_files = tuple(
            sorted((*_SEALED_FILES, _PROJECTION_RECOVERY_FILE))
        )
        if actual_files not in {base_files, recoverable_files}:
            raise GovernedAgentArtifactError("stage sealed file inventory drifted")
        _require_exact_inventory(
            stage.stage_dir,
            allowed={
                "owner.json",
                *actual_files,
                "seal.json",
                *(
                    {"promotion.json"}
                    if _path_lexists(stage.stage_dir / "promotion.json")
                    else set()
                ),
            },
        )
        seal_without_digest = dict(seal)
        actual_digest = str(seal_without_digest.pop("seal_digest", ""))
        if (
            actual_digest != expected_digest
            or _canonical_digest(seal_without_digest) != expected_digest
            or seal_without_digest.get("stage_ref") != stage.stage_ref
            or seal_without_digest.get("binding_digest") != stage.binding_digest
        ):
            raise GovernedAgentArtifactError("stage seal attribution drifted")
        files = seal_without_digest.get("files")
        if not isinstance(files, dict) or tuple(sorted(files)) != actual_files:
            raise GovernedAgentArtifactError("stage sealed file inventory drifted")
        for relative in actual_files:
            path = stage.stage_dir / relative
            _require_regular_file(path, root=stage.stage_dir)
            if files.get(relative) != _file_digest(path):
                raise GovernedAgentArtifactError(
                    f"sealed Agent artifact changed after execution: {relative}"
                )
        accepted_root = self._governed_root() / "accepted"
        accepted_dir = stage.stage_dir if _is_within(stage.stage_dir, accepted_root) else None
        return GovernedAgentArtifactSeal(
            stage_ref=stage.stage_ref,
            seal_digest=expected_digest,
            workspace_id=stage.workspace_id,
            attempt_id=stage.attempt_id,
            execution_authority=dict(stage.execution_authority),
            stage_dir=stage.stage_dir,
            accepted_dir=accepted_dir,
        )

    def inventory_governed_seals(
        self,
        *,
        namespace: str,
        limit: int = 100,
        after_stage_ref: str | None = None,
    ) -> tuple[GovernedAgentArtifactSeal, ...]:
        """Return a bounded, fully verified, read-only seal inventory."""

        return _read_model.inventory_governed_seals(
            self,
            namespace=namespace,
            limit=limit,
            after_stage_ref=after_stage_ref,
        )

    def inspect_governed_promotion(
        self,
        *,
        stage_ref: str,
        seal_digest: str,
    ) -> dict[str, str]:
        """Reopen one exact accepted promotion without mutating storage."""

        return _read_model.inspect_governed_promotion(
            self,
            stage_ref=stage_ref,
            seal_digest=seal_digest,
        )

    def inspect_governed_stage_for_authority(
        self,
        *,
        workspace_id: str,
        execution_authority: dict[str, Any],
    ) -> GovernedAgentArtifactSeal:
        """Reopen the exact sealed stage derived from immutable Task authority."""

        return _read_model.inspect_governed_stage_for_authority(
            self,
            workspace_id=workspace_id,
            execution_authority=execution_authority,
        )

    def read_projection_recovery(
        self,
        seal: GovernedAgentArtifactSeal,
    ) -> dict[str, Any]:
        """Read the seal-bound recovery envelope; legacy seals fail closed."""

        return _read_model.read_projection_recovery(self, seal)

    def read_sealed_json(
        self,
        seal: GovernedAgentArtifactSeal,
        relative: str,
    ) -> dict[str, Any]:
        """Read one fixed protected JSON member after revalidating the seal."""

        return _read_model.read_sealed_json(self, seal, relative)

    def _sealed_digest_for_ref(self, stage_ref: str) -> str:
        return _read_model.sealed_digest_for_ref(self, stage_ref)

    def promote_governed_stage(
        self,
        *,
        stage_ref: str,
        seal_digest: str,
        acceptance_ref: str,
    ) -> str:
        """Atomically move a sealed stage into the accepted namespace.

        A response-loss replay first verifies the already accepted directory
        byte-for-byte and returns the same content-addressed reference.
        """

        seal = self.inspect_governed_stage(
            stage_ref=stage_ref,
            seal_digest=seal_digest,
        )
        self._guard_authority_paths(seal.workspace_id, seal.attempt_id)
        if _path_lexists(self._tombstone_path(seal.workspace_id, seal.attempt_id)):
            raise GovernedAgentArtifactError(
                "abandoned artifact cannot be promoted"
            )
        accepted_ref = (
            f"agent-artifact://{seal.workspace_id}/{seal.attempt_id}/sha256/"
            f"{seal.seal_digest.split(':', 1)[1]}"
        )
        promotion = {
            "schema_version": GOVERNED_AGENT_STAGE_SCHEMA,
            "stage_ref": seal.stage_ref,
            "seal_digest": seal.seal_digest,
            "accepted_ref": accepted_ref,
            "acceptance_ref": str(acceptance_ref or "").strip(),
        }
        if "://" not in promotion["acceptance_ref"]:
            raise GovernedAgentArtifactError("acceptance_ref is not durable")
        promotion_path = seal.stage_dir / "promotion.json"
        self._guard_governed_target(promotion_path, final_kind="file")
        if _path_lexists(promotion_path):
            if _read_json_object(promotion_path, field="stage promotion") != promotion:
                raise GovernedAgentArtifactError("stage promotion attribution drifted")
        else:
            self._write_governed_metadata(
                promotion_path,
                promotion,
                fault_prefix="promotion",
            )
        self._inject_fault("after_promotion_record")

        accepted_dir = self._accepted_dir(seal.workspace_id, seal.attempt_id)
        if seal.accepted_dir is None:
            if _path_lexists(self._tombstone_path(seal.workspace_id, seal.attempt_id)):
                raise GovernedAgentArtifactError(
                    "abandoned artifact cannot be promoted"
                )
            self._ensure_governed_directory(accepted_dir.parent)
            self._guard_authority_paths(seal.workspace_id, seal.attempt_id)
            if _path_lexists(accepted_dir):
                existing = self.inspect_governed_stage(
                    stage_ref=stage_ref,
                    seal_digest=seal_digest,
                )
                if existing.accepted_dir != accepted_dir:
                    raise GovernedAgentArtifactError(
                        "accepted artifact namespace already owns different material"
                    )
            else:
                _require_regular_directory(seal.stage_dir, root=self.runs_root)
                self._guard_governed_target(accepted_dir, final_kind="directory")
                os.replace(seal.stage_dir, accepted_dir)
                self._inject_fault("after_promotion_rename")
        accepted = self.inspect_governed_stage(
            stage_ref=stage_ref,
            seal_digest=seal_digest,
        )
        if accepted.accepted_dir != accepted_dir or (
            _read_json_object(accepted_dir / "promotion.json", field="stage promotion")
            != promotion
        ):
            raise GovernedAgentArtifactError("accepted artifact promotion is incomplete")
        return accepted_ref

    def abandon_governed_stage(
        self,
        *,
        stage_ref: str,
        seal_digest: str,
        reason_code: str,
    ) -> str:
        """Replace rejected raw material with an owner-bound safe tombstone."""

        workspace, attempt_id, tombstone = _governed_tombstone_record(
            stage_ref=stage_ref,
            seal_digest=seal_digest,
            reason_code=reason_code,
        )
        reason = str(tombstone["reason_code"])
        tombstone_path = self._tombstone_path(workspace, attempt_id)
        tombstone_dir = tombstone_path.parent
        tombstone_ref = str(tombstone["tombstone_ref"])
        self._guard_authority_paths(workspace, attempt_id)
        if _path_lexists(tombstone_path):
            reopened_tombstone = _read_json_object(
                tombstone_path,
                field="stage tombstone",
            )
            _validate_tombstone(reopened_tombstone)
            if reopened_tombstone != tombstone:
                raise GovernedAgentArtifactError("stage tombstone attribution drifted")
            accepted_dir = self._accepted_dir(workspace, attempt_id)
            if _path_lexists(accepted_dir):
                raise GovernedAgentArtifactError(
                    "accepted artifact cannot be deleted by abandonment replay"
                )
            stage_dir = self._stage_dir(workspace, attempt_id)
            if _path_lexists(stage_dir):
                seal = self.inspect_governed_stage(
                    stage_ref=stage_ref,
                    seal_digest=seal_digest,
                )
                if seal.accepted_dir is not None:
                    raise GovernedAgentArtifactError(
                        "accepted artifact cannot be abandoned"
                    )
                self._guard_authority_paths(workspace, attempt_id)
                _require_regular_directory(seal.stage_dir, root=self.runs_root)
                self._remove_governed_attempt_directory(seal.stage_dir)
            return tombstone_ref
        seal = self.inspect_governed_stage(
            stage_ref=stage_ref,
            seal_digest=seal_digest,
        )
        if seal.accepted_dir is not None:
            raise GovernedAgentArtifactError("accepted artifact cannot be abandoned")
        self._ensure_governed_directory(tombstone_dir)
        self._write_governed_metadata(
            tombstone_path,
            tombstone,
            fault_prefix="tombstone",
        )
        self._inject_fault("after_tombstone_record")
        # Reopen after the durable tombstone so a concurrent promotion or a
        # swapped stage can never be removed as rejected material.
        self._guard_authority_paths(workspace, attempt_id)
        if _path_lexists(self._accepted_dir(workspace, attempt_id)):
            raise GovernedAgentArtifactError(
                "accepted artifact cannot be deleted after abandonment"
            )
        reopened = self.inspect_governed_stage(
            stage_ref=stage_ref,
            seal_digest=seal_digest,
        )
        if reopened.accepted_dir is not None:
            raise GovernedAgentArtifactError("accepted artifact cannot be abandoned")
        self._guard_authority_paths(workspace, attempt_id)
        _require_regular_directory(reopened.stage_dir, root=self.runs_root)
        self._remove_governed_attempt_directory(reopened.stage_dir)
        if _path_lexists(seal.stage_dir) or _read_json_object(
            tombstone_path, field="stage tombstone"
        ) != tombstone:
            raise GovernedAgentArtifactError("stage abandonment is incomplete")
        _validate_tombstone(
            _read_json_object(tombstone_path, field="stage tombstone")
        )
        return tombstone_ref

    def plan_governed_abandonment(
        self,
        *,
        stage_ref: str,
        seal_digest: str,
        reason_code: str,
    ) -> str:
        """Return an exact ref without deleting material or writing authority."""

        return _read_model.plan_governed_abandonment(
            self,
            stage_ref=stage_ref,
            seal_digest=seal_digest,
            reason_code=reason_code,
        )

    def inspect_governed_tombstone(
        self,
        *,
        stage_ref: str,
        seal_digest: str,
        reason_code: str,
    ) -> str:
        """Reopen an exact abandonment tombstone without mutating storage."""

        return _read_model.inspect_governed_tombstone(
            self,
            stage_ref=stage_ref,
            seal_digest=seal_digest,
            reason_code=reason_code,
        )

    def inspect_governed_tombstone_ref(
        self,
        tombstone_ref: str,
    ) -> dict[str, str]:
        """Resolve a tombstone reference into its canonical read-only proof."""

        return _read_model.inspect_governed_tombstone_ref(self, tombstone_ref)

    def _load_stage(
        self,
        stage_ref: str,
        *,
        allow_accepted: bool = False,
    ) -> GovernedAgentArtifactStage:
        match = _STAGE_REF.fullmatch(str(stage_ref or "").strip())
        if match is None:
            raise GovernedAgentArtifactError("stage_ref is invalid")
        workspace, attempt_id, digest_hex = match.groups()
        candidates = [self._stage_dir(workspace, attempt_id)]
        if allow_accepted:
            candidates.append(self._accepted_dir(workspace, attempt_id))
        self._guard_authority_paths(workspace, attempt_id)
        existing = [path for path in candidates if _path_lexists(path)]
        if len(existing) != 1:
            raise GovernedAgentArtifactError(
                "stage material is missing or exists in multiple authorities"
            )
        stage_dir = existing[0]
        _require_regular_directory(stage_dir, root=self.runs_root)
        owner = _read_json_object(stage_dir / "owner.json", field="stage owner")
        authority = _canonical_authority(owner.get("execution_authority"))
        binding = {
            "schema_version": GOVERNED_AGENT_STAGE_SCHEMA,
            "workspace_id": workspace,
            "execution_authority": authority,
        }
        binding_digest = _canonical_digest(binding)
        expected_ref = (
            f"agent-stage://{workspace}/{attempt_id}/sha256/"
            f"{binding_digest.split(':', 1)[1]}"
        )
        if (
            owner
            != {**binding, "binding_digest": binding_digest, "stage_ref": expected_ref}
            or expected_ref != stage_ref
            or digest_hex != binding_digest.split(":", 1)[1]
            or _attempt_segment(authority["attempt_id"]) != attempt_id
            or _workspace_id_for(
                graph_id=authority["graph_id"],
                graph_revision=authority["graph_revision"],
            )
            != workspace
        ):
            raise GovernedAgentArtifactError("stage owner attribution drifted")
        return GovernedAgentArtifactStage(
            stage_ref=expected_ref,
            stage_dir=stage_dir,
            workspace_id=workspace,
            attempt_id=attempt_id,
            binding_digest=binding_digest,
            execution_authority=authority,
        )

    def _governed_root(self) -> Path:
        return self.runs_root / "governed"

    def _stage_dir(self, workspace_id: str, attempt_id: str) -> Path:
        return self._governed_root() / "staging" / workspace_id / attempt_id

    def _accepted_dir(self, workspace_id: str, attempt_id: str) -> Path:
        return self._governed_root() / "accepted" / workspace_id / attempt_id

    def _tombstone_path(self, workspace_id: str, attempt_id: str) -> Path:
        return (
            self._governed_root()
            / "abandoned"
            / workspace_id
            / f"{attempt_id}.json"
        )

    def _write_governed_metadata(
        self,
        path: Path,
        payload: Any,
        *,
        fault_prefix: str,
    ) -> None:
        """Durably replace governance metadata without exposing partial JSON.

        The temporary file lives beside the target, is flushed and fsynced,
        and is then installed with ``os.replace``.  Directory fsync is best
        effort because Windows does not generally allow opening directories
        with ``os.open``; the atomic replace remains the compatibility floor.
        """

        path = self._guard_governed_target(path, final_kind="file")
        self._ensure_governed_directory(path.parent)
        self._guard_governed_target(path, final_kind="file")
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        # Stage metadata is serialized by the exact attempt authority, so one
        # deliberately short deterministic temp avoids Windows MAX_PATH
        # failures. Tombstones share a workspace directory and therefore use
        # a short target-derived suffix to avoid cross-attempt collisions.
        temporary = (
            path.parent / ".tmp"
            if fault_prefix != "tombstone"
            else path.parent
            / f".{hashlib.sha256(path.name.encode('utf-8')).hexdigest()[:8]}.tmp"
        )
        try:
            self._guard_governed_target(temporary, final_kind="file")
            with self._open_governed_binary_for_write(temporary) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._inject_fault(f"before_{fault_prefix}_replace")
            self._guard_governed_target(temporary, final_kind="file")
            self._guard_governed_target(path, final_kind="file")
            os.replace(temporary, path)
            self._guard_governed_target(path, final_kind="file")
            _best_effort_fsync_directory(path.parent)
            self._inject_fault(f"after_{fault_prefix}_replace")
        finally:
            try:
                if _path_lexists(temporary):
                    self._guard_governed_target(temporary, final_kind="file")
                    _require_regular_file(temporary, root=self.runs_root)
                    temporary.unlink()
            except OSError:
                # A stale uniquely named temp is never accepted by inventory
                # validation and can be removed by operational cleanup.
                pass

    def _inject_fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    def write_text(self, path: Path, text: str) -> None:
        if self._is_governed_target(path):
            self._write_governed_raw(
                path,
                str(text or "").encode("utf-8", errors="replace"),
            )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or ""), encoding="utf-8", errors="replace")

    def write_json(self, path: Path, payload: Any) -> None:
        if self._is_governed_target(path):
            self._write_governed_raw(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_governed_raw(self, path: Path, encoded: bytes) -> None:
        target = self._guard_governed_target(path, final_kind="file")
        relative = target.relative_to(self._governed_root())
        if len(relative.parts) != 4 or relative.parts[0] != "staging":
            raise GovernedAgentArtifactError(
                "raw governed artifacts may only be written to an exact staging attempt"
            )
        _require_regular_directory(target.parent, root=self.runs_root)
        # Recheck immediately before opening: the parent and final target may
        # have changed since the caller received its stage object.
        self._guard_governed_target(target, final_kind="file")
        with self._open_governed_binary_for_write(target) as stream:
            stream.write(encoded)

    def _open_governed_binary_for_write(self, path: Path) -> BinaryIO:
        """Open without truncation, revalidate identity, then permit writes.

        This closes the practical prepare-to-write directory-swap window: a
        parent swapped after the pre-open lstat may cause an empty file to be
        opened, but no governed bytes are written or existing bytes truncated
        until the full chain and opened-file identity are checked again.
        """

        target = self._guard_governed_target(path, final_kind="file")
        flags = os.O_WRONLY | os.O_CREAT
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags, 0o600)
        except OSError as exc:
            raise GovernedAgentArtifactError(
                "governed artifact file could not be opened safely"
            ) from exc
        try:
            self._guard_governed_target(target, final_kind="file")
            opened = os.fstat(descriptor)
            current = os.lstat(target)
            if (
                _is_reparse_or_link(target, current)
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or not os.path.samestat(opened, current)
            ):
                raise GovernedAgentArtifactError(
                    "governed artifact file identity changed while opening"
                )
            os.ftruncate(descriptor, 0)
            return os.fdopen(descriptor, "wb")
        except Exception:
            os.close(descriptor)
            raise

    def _is_governed_target(self, path: Path) -> bool:
        lexical_input = Path(path).expanduser()
        if not lexical_input.is_absolute():
            lexical_input = Path.cwd() / lexical_input
        try:
            # Preserve ``..`` here so a caller cannot present a path below a
            # governed stage and normalize it into the legacy namespace.
            lexical_input.relative_to(self._governed_root())
        except ValueError:
            pass
        else:
            return True
        target = _absolute_lexical(path)
        governed_root = self._governed_root()
        if _is_within(target, governed_root):
            return True
        # A lexical legacy path may be an alias back into a governed stage.
        # Physical resolution is used only to upgrade classification into the
        # guarded path; I/O always retains the original lexical target, which
        # the guard then rejects as outside the governed namespace.
        try:
            physical_target = Path(os.path.realpath(target))
            physical_governed_root = Path(os.path.realpath(governed_root))
        except OSError:
            # An uninspectable alias below the configured runs root is not
            # permitted to fall through to the legacy writer.
            return _is_within(target, self.runs_root)
        return _is_within(physical_target, physical_governed_root)

    def _guard_authority_paths(self, workspace_id: str, attempt_id: str) -> None:
        """Reject redirected staging, accepted, or tombstone authorities."""

        self._guard_governed_target(
            self._stage_dir(workspace_id, attempt_id),
            final_kind="directory",
        )
        self._guard_governed_target(
            self._accepted_dir(workspace_id, attempt_id),
            final_kind="directory",
        )
        self._guard_governed_target(
            self._tombstone_path(workspace_id, attempt_id),
            final_kind="file",
        )

    def _guard_governed_target(
        self,
        path: Path,
        *,
        final_kind: str,
    ) -> Path:
        target = _absolute_lexical(path)
        governed_root = self._governed_root()
        if not _is_within(target, governed_root):
            raise GovernedAgentArtifactError(
                "artifact target escaped its governed lexical root"
            )
        relative = target.relative_to(governed_root)
        if relative.parts and relative.parts[0] not in {
            "staging",
            "accepted",
            "abandoned",
        }:
            raise GovernedAgentArtifactError("artifact authority namespace is invalid")
        _validate_existing_ancestor_chain(
            root=self.runs_root,
            target=target,
            final_kind=final_kind,
            require_final=False,
        )
        return target

    def _ensure_governed_directory(self, path: Path) -> Path:
        """Create each missing directory only after its ancestors are safe."""

        target = self._guard_governed_target(path, final_kind="directory")
        if not _path_lexists(self.runs_root):
            # The configured runs root is the trust boundary.  Once created,
            # it is immediately lstat-validated before any governed child is
            # created or opened.
            self.runs_root.mkdir(parents=True, exist_ok=True)
        _validate_existing_ancestor_chain(
            root=self.runs_root,
            target=self.runs_root,
            final_kind="directory",
            require_final=True,
        )
        current = self.runs_root
        for segment in target.relative_to(self.runs_root).parts:
            next_path = current / segment
            _validate_existing_ancestor_chain(
                root=self.runs_root,
                target=current,
                final_kind="directory",
                require_final=True,
            )
            if _path_lexists(next_path):
                _validate_existing_ancestor_chain(
                    root=self.runs_root,
                    target=next_path,
                    final_kind="directory",
                    require_final=True,
                )
            else:
                os.mkdir(next_path)
                _validate_existing_ancestor_chain(
                    root=self.runs_root,
                    target=next_path,
                    final_kind="directory",
                    require_final=True,
                )
            current = next_path
        return target

    def _discard_stale_metadata_temp(self, directory: Path) -> None:
        """Remove only the exact regular attempt-local metadata temp."""

        temporary = directory / ".tmp"
        self._guard_governed_target(temporary, final_kind="file")
        if not _path_lexists(temporary):
            return
        self._guard_governed_target(temporary, final_kind="file")
        _require_regular_file(temporary, root=self.runs_root)
        temporary.unlink()

    def _remove_governed_attempt_directory(self, directory: Path) -> None:
        """Delete a governed attempt without recursive link traversal."""

        target = self._guard_governed_target(directory, final_kind="directory")
        _require_regular_directory(target, root=self.runs_root)
        entries = list(target.iterdir())
        for entry in entries:
            self._guard_governed_target(entry, final_kind="file")
            _require_regular_file(entry, root=self.runs_root)
        for entry in entries:
            self._guard_governed_target(entry, final_kind="file")
            _require_regular_file(entry, root=self.runs_root)
            entry.unlink()
        self._guard_governed_target(target, final_kind="directory")
        _require_regular_directory(target, root=self.runs_root)
        target.rmdir()

    @staticmethod
    def read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return ""


def _canonical_authority(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(_AUTHORITY_FIELDS):
        raise GovernedAgentArtifactError(
            "execution authority must contain the exact frozen fields"
        )
    normalized: dict[str, Any] = {}
    for field in _AUTHORITY_FIELDS:
        raw = value.get(field)
        if field in {"graph_revision", "fencing_token"}:
            if isinstance(raw, bool) or type(raw) is not int or raw < 1:
                raise GovernedAgentArtifactError(f"authority {field} is invalid")
            normalized[field] = raw
        else:
            text = str(raw or "").strip()
            if not text or len(text) > 240:
                raise GovernedAgentArtifactError(f"authority {field} is invalid")
            normalized[field] = text
    return normalized


def _validate_tombstone(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "stage_ref",
        "seal_digest",
        "reason_code",
        "tombstone_ref",
    }:
        raise GovernedAgentArtifactError("stage tombstone fields drifted")
    stage_match = _STAGE_REF.fullmatch(str(value.get("stage_ref") or ""))
    tombstone_match = _TOMBSTONE_REF.fullmatch(
        str(value.get("tombstone_ref") or "")
    )
    seal_digest = str(value.get("seal_digest") or "").strip().lower()
    reason_code = str(value.get("reason_code") or "").strip().upper()
    identity = {
        "schema_version": GOVERNED_AGENT_STAGE_SCHEMA,
        "stage_ref": str(value.get("stage_ref") or ""),
        "seal_digest": seal_digest,
        "reason_code": reason_code,
    }
    expected_digest = _canonical_digest(identity).split(":", 1)[1]
    if (
        value.get("schema_version") != GOVERNED_AGENT_STAGE_SCHEMA
        or stage_match is None
        or tombstone_match is None
        or not _DIGEST.fullmatch(seal_digest)
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", reason_code)
        or tombstone_match.group(1) != stage_match.group(1)
        or tombstone_match.group(2) != stage_match.group(2)
        or tombstone_match.group(3) != expected_digest
        or value != {**identity, "tombstone_ref": value.get("tombstone_ref")}
    ):
        raise GovernedAgentArtifactError("stage tombstone attribution drifted")
    return dict(value)


def _governed_tombstone_record(
    *,
    stage_ref: str,
    seal_digest: str,
    reason_code: str,
) -> tuple[str, str, dict[str, Any]]:
    reason = str(reason_code or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", reason):
        raise GovernedAgentArtifactError("abandonment reason_code is invalid")
    normalized_stage_ref = str(stage_ref or "").strip()
    match = _STAGE_REF.fullmatch(normalized_stage_ref)
    if match is None:
        raise GovernedAgentArtifactError("stage_ref is invalid")
    normalized_seal = str(seal_digest or "").strip().lower()
    if not _DIGEST.fullmatch(normalized_seal):
        raise GovernedAgentArtifactError("stage seal digest is invalid")
    workspace, attempt_id, _ = match.groups()
    identity = {
        "schema_version": GOVERNED_AGENT_STAGE_SCHEMA,
        "stage_ref": normalized_stage_ref,
        "seal_digest": normalized_seal,
        "reason_code": reason,
    }
    digest = _canonical_digest(identity)
    tombstone_ref = (
        f"agent-artifact-abandon://{workspace}/{attempt_id}/sha256/"
        f"{digest.split(':', 1)[1]}"
    )
    return workspace, attempt_id, {
        **identity,
        "tombstone_ref": tombstone_ref,
    }


def _portable_segment(value: Any, *, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > maximum
        or sanitize_segment(text, "") != text
        or text in {".", ".."}
    ):
        raise GovernedAgentArtifactError(f"{field} is not a portable segment")
    return text


def _workspace_id_for(*, graph_id: str, graph_revision: int) -> str:
    digest = hashlib.sha256(
        f"{graph_id}\n{graph_revision}".encode("utf-8")
    ).hexdigest()[:48]
    return f"workspace-{digest}"


def _attempt_segment(attempt_id: Any) -> str:
    identity = str(attempt_id or "").strip()
    if not identity or len(identity) > 240:
        raise GovernedAgentArtifactError("attempt_id is invalid")
    # 128 bits keeps the Workspace-scoped attempt key collision resistant
    # while leaving enough headroom for metadata filenames under Windows'
    # legacy MAX_PATH boundary used by some production Python/Git installs.
    return "attempt-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _best_effort_fsync_directory(path: Path) -> None:
    """Flush a replaced directory entry where the host OS supports it.

    POSIX permits a directory file descriptor to be fsynced.  Windows usually
    rejects the directory open, so durability there relies on the flushed temp
    file plus atomic ``os.replace`` and intentionally degrades without turning
    a successful metadata replacement into a false failure.
    """

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json_object(path: Path, *, field: str) -> dict[str, Any]:
    _require_regular_file(path, root=path.parent)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GovernedAgentArtifactError(f"{field} is unreadable") from exc
    if not isinstance(value, dict):
        raise GovernedAgentArtifactError(f"{field} is not an object")
    return value


def _is_within(path: Path, root: Path) -> bool:
    try:
        _absolute_lexical(path).relative_to(_absolute_lexical(root))
    except ValueError:
        return False
    return True


def _require_regular_directory(path: Path, *, root: Path) -> None:
    _validate_existing_ancestor_chain(
        root=root,
        target=path,
        final_kind="directory",
        require_final=True,
    )


def _require_regular_file(path: Path, *, root: Path) -> None:
    _validate_existing_ancestor_chain(
        root=root,
        target=path,
        final_kind="file",
        require_final=True,
    )


def _require_exact_inventory(path: Path, *, allowed: set[str]) -> None:
    _require_regular_directory(path, root=path.parent.parent.parent.parent)
    actual: set[str] = set()
    for child in path.iterdir():
        if not _is_regular_file_without_reparse(child):
            raise GovernedAgentArtifactError(
                "governed artifact stage contains a non-regular entry"
            )
        actual.add(child.name)
    if actual != allowed:
        raise GovernedAgentArtifactError(
            "governed artifact stage inventory differs from its sealed contract"
        )


def _absolute_lexical(path: Path | str) -> Path:
    """Return an absolute normalized path without following filesystem links."""

    return Path(os.path.abspath(os.fspath(path)))


def _path_lexists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise GovernedAgentArtifactError("artifact path cannot be inspected") from exc
    return True


def _is_reparse_or_link(path: Path, metadata: os.stat_result | None = None) -> bool:
    try:
        details = metadata if metadata is not None else os.lstat(path)
    except OSError as exc:
        raise GovernedAgentArtifactError("artifact path cannot be inspected") from exc
    if stat.S_ISLNK(details.st_mode):
        return True
    attributes = int(getattr(details, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if attributes & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError as exc:
            raise GovernedAgentArtifactError("artifact path cannot be inspected") from exc
    return False


def _is_regular_file_without_reparse(path: Path) -> bool:
    try:
        details = os.lstat(path)
    except OSError:
        return False
    return not _is_reparse_or_link(path, details) and stat.S_ISREG(details.st_mode)


def _validate_existing_ancestor_chain(
    *,
    root: Path,
    target: Path,
    final_kind: str,
    require_final: bool,
) -> None:
    """lstat every existing component from ``root`` through ``target``.

    The target is compared lexically first.  It is never resolved and then
    used for I/O, because that would silently bless a symlink or Windows
    reparse-point redirection.
    """

    root_path = _absolute_lexical(root)
    target_path = _absolute_lexical(target)
    try:
        relative = target_path.relative_to(root_path)
    except ValueError as exc:
        raise GovernedAgentArtifactError(
            "artifact target escaped its governed lexical root"
        ) from exc
    chain = [root_path]
    current = root_path
    for segment in relative.parts:
        current = current / segment
        chain.append(current)
    final_exists = False
    for index, component in enumerate(chain):
        try:
            details = os.lstat(component)
        except FileNotFoundError:
            break
        except OSError as exc:
            raise GovernedAgentArtifactError(
                "artifact ancestor cannot be inspected"
            ) from exc
        if _is_reparse_or_link(component, details):
            raise GovernedAgentArtifactError(
                "governed artifact path contains a symbolic link or reparse point"
            )
        is_final = index == len(chain) - 1
        if is_final:
            final_exists = True
            expected = stat.S_ISDIR if final_kind == "directory" else stat.S_ISREG
            if not expected(details.st_mode):
                raise GovernedAgentArtifactError(
                    "artifact target has the wrong filesystem type"
                )
        elif not stat.S_ISDIR(details.st_mode):
            raise GovernedAgentArtifactError(
                "governed artifact ancestor is not a regular directory"
            )
    if require_final and not final_exists:
        label = "directory" if final_kind == "directory" else "file"
        raise GovernedAgentArtifactError(
            f"artifact {label} is missing or not regular"
        )


__all__ = [
    "GOVERNED_AGENT_STAGE_INVALID",
    "GOVERNED_AGENT_STAGE_SCHEMA",
    "AgentArtifactStore",
    "DEFAULT_CODEX_RUNS_ROOT",
    "GovernedAgentArtifactError",
    "GovernedAgentArtifactSeal",
    "GovernedAgentArtifactStage",
    "REPO_ROOT",
    "governed_attempt_segment",
    "sanitize_segment",
]
