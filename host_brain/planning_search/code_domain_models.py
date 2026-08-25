"""Immutable Code planning evidence and catalog-safe input reconstruction.

This module owns the frozen data boundary for the Code domain.  It has no
search engine, compiler, runtime, worktree, model, tool, or verifier import.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any

from host_brain.contracts.planning_search.contracts import SearchCandidate, VirtualStateSnapshot


CODE_DOMAIN = "code"
CODE_SHADOW_MODE = "SHADOW_ONLY"
CODE_NODE_OUTPUT_SCHEMA = "natsume.code-planning.node-output.v1"

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CodePlanningError(ValueError):
    """Raised before a Code planning value can reach the generic kernel."""


@dataclass(frozen=True, slots=True)
class CodePatchSpec:
    """One pre-registered patch option represented only by opaque artifacts."""

    patch_id: str
    patch_ref: str
    affected_artifact_refs: Sequence[str]
    test_ids: Sequence[str]
    estimated_pass_probability: float
    estimated_risk: float
    estimated_cost: float
    requires_lookahead: bool = True

    def __post_init__(self) -> None:
        patch_id = _identifier(self.patch_id, field_name="patch_id")
        patch_ref = _governed_ref(self.patch_ref, field_name="patch_ref")
        affected = _governed_refs(
            self.affected_artifact_refs,
            field_name="affected_artifact_refs",
            allow_empty=False,
        )
        test_ids = _governed_refs(
            self.test_ids,
            field_name="test_ids",
            allow_empty=False,
        )
        object.__setattr__(self, "patch_id", patch_id)
        object.__setattr__(self, "patch_ref", patch_ref)
        object.__setattr__(self, "affected_artifact_refs", affected)
        object.__setattr__(self, "test_ids", test_ids)
        object.__setattr__(
            self,
            "estimated_pass_probability",
            _unit_interval(
                self.estimated_pass_probability,
                field_name="estimated_pass_probability",
            ),
        )
        object.__setattr__(
            self,
            "estimated_risk",
            _unit_interval(self.estimated_risk, field_name="estimated_risk"),
        )
        object.__setattr__(
            self,
            "estimated_cost",
            _unit_interval(self.estimated_cost, field_name="estimated_cost"),
        )
        object.__setattr__(
            self,
            "requires_lookahead",
            _strict_bool(self.requires_lookahead, field_name="requires_lookahead"),
        )

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "patch_ref": self.patch_ref,
            "affected_artifact_refs": list(self.affected_artifact_refs),
            "test_ids": list(self.test_ids),
            "estimated_pass_probability": _rounded(self.estimated_pass_probability),
            "estimated_risk": _rounded(self.estimated_risk),
            "estimated_cost": _rounded(self.estimated_cost),
            "requires_lookahead": self.requires_lookahead,
        }


@dataclass(frozen=True, slots=True)
class CodeTaskSnapshot:
    """Redacted Code task evidence captured before planning begins.

    Filesystem paths, source text, patches, worktree handles, credentials and
    executor callbacks are intentionally absent.  Every material item is an
    opaque governed reference plus a digest or bounded identifier.
    """

    task_ref: str
    workspace_snapshot_ref: str
    workspace_digest: str
    objective_ref: str
    failing_test_ids: Sequence[str]
    target_artifact_digests: Mapping[str, str]
    allowed_path_refs: Sequence[str]
    forbidden_path_refs: Sequence[str]
    max_changed_files: int
    verification_profile_ref: str
    patch_catalog: Sequence[CodePatchSpec]
    source_refs: Sequence[str] = ()
    virtual_model_ready: bool = False

    def __post_init__(self) -> None:
        task_ref = _governed_ref(self.task_ref, field_name="task_ref")
        workspace_ref = _governed_ref(
            self.workspace_snapshot_ref,
            field_name="workspace_snapshot_ref",
        )
        workspace_digest = _sha256(self.workspace_digest, field_name="workspace_digest")
        objective_ref = _governed_ref(
            self.objective_ref,
            field_name="objective_ref",
        )
        failing_tests = _governed_refs(
            self.failing_test_ids,
            field_name="failing_test_ids",
            allow_empty=False,
        )
        if not isinstance(self.target_artifact_digests, Mapping):
            raise CodePlanningError("target_artifact_digests must be an object")
        target_digests: dict[str, str] = {}
        for raw_ref, raw_digest in self.target_artifact_digests.items():
            ref = _governed_ref(raw_ref, field_name="target_artifact_digests key")
            target_digests[ref] = _sha256(
                raw_digest,
                field_name=f"target_artifact_digests[{ref}]",
            )
        if not target_digests:
            raise CodePlanningError("target_artifact_digests must not be empty")
        allowed = _governed_refs(
            self.allowed_path_refs,
            field_name="allowed_path_refs",
            allow_empty=False,
        )
        forbidden = _governed_refs(
            self.forbidden_path_refs,
            field_name="forbidden_path_refs",
            allow_empty=True,
        )
        if set(allowed) & set(forbidden):
            raise CodePlanningError("allowed_path_refs and forbidden_path_refs overlap")
        if (
            isinstance(self.max_changed_files, bool)
            or not isinstance(self.max_changed_files, int)
            or self.max_changed_files < 1
        ):
            raise CodePlanningError("max_changed_files must be a positive integer")
        verification_ref = _governed_ref(
            self.verification_profile_ref,
            field_name="verification_profile_ref",
        )
        patches = tuple(self.patch_catalog)
        if not patches or not all(isinstance(item, CodePatchSpec) for item in patches):
            raise CodePlanningError("patch_catalog must contain CodePatchSpec values")
        if len({item.patch_id for item in patches}) != len(patches):
            raise CodePlanningError("patch_catalog patch_id values must be unique")
        patches = tuple(sorted(patches, key=lambda item: item.patch_id))
        target_artifacts = frozenset(target_digests)
        failing_test_set = frozenset(failing_tests)
        for patch in patches:
            if not set(patch.affected_artifact_refs).issubset(target_artifacts):
                raise CodePlanningError(
                    "patch_catalog affected_artifact_refs must stay within frozen targets"
                )
            if len(patch.affected_artifact_refs) > self.max_changed_files:
                raise CodePlanningError(
                    "patch_catalog exceeds max_changed_files for a candidate"
                )
            if not set(patch.test_ids).issubset(failing_test_set):
                raise CodePlanningError(
                    "patch_catalog test_ids must stay within frozen failing tests"
                )
        sources = _governed_refs(self.source_refs, field_name="source_refs", allow_empty=True)
        all_sources = _dedupe(
            sources,
            (task_ref, workspace_ref, objective_ref, verification_ref),
            tuple(target_digests),
            allowed,
            forbidden,
            tuple(item.patch_ref for item in patches),
            tuple(ref for item in patches for ref in item.affected_artifact_refs),
        )
        object.__setattr__(self, "task_ref", task_ref)
        object.__setattr__(self, "workspace_snapshot_ref", workspace_ref)
        object.__setattr__(self, "workspace_digest", workspace_digest)
        object.__setattr__(self, "objective_ref", objective_ref)
        object.__setattr__(self, "failing_test_ids", failing_tests)
        object.__setattr__(
            self,
            "target_artifact_digests",
            MappingProxyType(dict(sorted(target_digests.items()))),
        )
        object.__setattr__(self, "allowed_path_refs", allowed)
        object.__setattr__(self, "forbidden_path_refs", forbidden)
        object.__setattr__(self, "max_changed_files", self.max_changed_files)
        object.__setattr__(self, "verification_profile_ref", verification_ref)
        object.__setattr__(self, "patch_catalog", patches)
        object.__setattr__(self, "source_refs", all_sources)
        object.__setattr__(
            self,
            "virtual_model_ready",
            _strict_bool(self.virtual_model_ready, field_name="virtual_model_ready"),
        )

    @property
    def snapshot_digest(self) -> str:
        return _sha256_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_ref": self.task_ref,
            "workspace_snapshot_ref": self.workspace_snapshot_ref,
            "workspace_digest": self.workspace_digest,
            "objective_ref": self.objective_ref,
            "failing_test_ids": list(self.failing_test_ids),
            "target_artifact_digests": dict(self.target_artifact_digests),
            "allowed_path_refs": list(self.allowed_path_refs),
            "forbidden_path_refs": list(self.forbidden_path_refs),
            "max_changed_files": self.max_changed_files,
            "verification_profile_ref": self.verification_profile_ref,
            "patch_catalog": [item.to_state_dict() for item in self.patch_catalog],
            "source_refs": list(self.source_refs),
            "virtual_model_ready": self.virtual_model_ready,
        }

    def to_virtual_state(self) -> VirtualStateSnapshot:
        return VirtualStateSnapshot(
            domain=CODE_DOMAIN,
            capabilities=("code.inspect", "code.patch", "code.verify", "code.rollback"),
            facts={
                "task_ref": self.task_ref,
                "workspace_digest": self.workspace_digest,
                "failing_test_ids": list(self.failing_test_ids),
                "target_artifact_digests": dict(self.target_artifact_digests),
                "candidate_patch_catalog": [
                    item.to_state_dict() for item in self.patch_catalog
                ],
                "phase": "root",
                "inspection_strategy": "",
                "selected_patch_id": "",
                "verification_status": "not_started",
            },
            constraints={
                "mode": CODE_SHADOW_MODE,
                "isolated_workspace": True,
                "production_influence_enabled": False,
                "allowed_path_refs": list(self.allowed_path_refs),
                "forbidden_path_refs": list(self.forbidden_path_refs),
                "max_changed_files": self.max_changed_files,
                "verification_profile_ref": self.verification_profile_ref,
                "virtual_model_ready": self.virtual_model_ready,
            },
            source_refs=self.source_refs,
        )

    def candidate_input(self, candidate: SearchCandidate) -> dict[str, Any]:
        """Build the only frozen Agent input for a validated selected node."""

        candidate_id = str(candidate.candidate_id or "").strip()
        action, patch = _parse_candidate_id(candidate_id, self)
        objective = _objective_for(action, self, patch=patch)
        refs = _input_refs_for(action, self, patch=patch)
        args: dict[str, Any] = {
            "task": objective,
            "problem_statement": objective,
            "run_mode": "inspect",
            "workspace_strategy": "temp_workspace",
            "code_shadow_mode": True,
            "code_action": action,
            "task_ref": self.task_ref,
            "workspace_snapshot_ref": self.workspace_snapshot_ref,
            "verification_profile_ref": self.verification_profile_ref,
            "input_refs": list(refs),
        }
        if patch is not None:
            args.update(
                {
                    "patch_id": patch.patch_id,
                    "patch_ref": patch.patch_ref,
                    "failing_test_ids": list(self.failing_test_ids),
                    "verification_test_ids": list(patch.test_ids),
                }
            )
        return {"objective": objective, "args": args}


def _objective_for(
    action: str,
    snapshot: CodeTaskSnapshot,
    *,
    patch: CodePatchSpec | None = None,
) -> str:
    if action == "inspect_targeted":
        return f"Inspect frozen failure evidence for Code task {snapshot.task_ref}."
    if action == "inspect_broad":
        return f"Inspect bounded repository evidence for Code task {snapshot.task_ref}."
    if action == "patch" and patch is not None:
        return f"Assess registered patch candidate {patch.patch_id} for Code task {snapshot.task_ref}."
    if action == "verify_unit" and patch is not None:
        return f"Verify registered patch candidate {patch.patch_id} against frozen unit evidence."
    if action == "rollback" and patch is not None:
        return f"Prepare a no-effect rollback assessment for patch candidate {patch.patch_id}."
    raise CodePlanningError("unsupported Code candidate objective")


def _input_refs_for(
    action: str,
    snapshot: CodeTaskSnapshot,
    *,
    patch: CodePatchSpec | None = None,
) -> tuple[str, ...]:
    base = (
        snapshot.task_ref,
        snapshot.workspace_snapshot_ref,
        snapshot.verification_profile_ref,
    )
    if action.startswith("inspect"):
        return _dedupe(base, tuple(snapshot.target_artifact_digests))
    if patch is not None:
        return _dedupe(base, (patch.patch_ref,), patch.affected_artifact_refs)
    raise CodePlanningError("unsupported Code candidate input refs")


def _parse_candidate_id(
    candidate_id: str,
    snapshot: CodeTaskSnapshot,
) -> tuple[str, CodePatchSpec | None]:
    if candidate_id == "code.inspect.targeted":
        return "inspect_targeted", None
    if candidate_id == "code.inspect.broad":
        return "inspect_broad", None
    for prefix, action in (
        ("code.patch.", "patch"),
        ("code.verify.unit.", "verify_unit"),
        ("code.rollback.", "rollback"),
    ):
        if candidate_id.startswith(prefix):
            patch = next(
                (
                    item
                    for item in snapshot.patch_catalog
                    if item.patch_id == candidate_id.removeprefix(prefix)
                ),
                None,
            )
            if patch is not None:
                return action, patch
    raise CodePlanningError("candidate id is not registered for this Code snapshot")


def _identifier(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=120).lower()
    if not _IDENTIFIER_RE.fullmatch(text):
        raise CodePlanningError(f"{field_name} must be a lowercase opaque identifier")
    return text


def _required_text(value: Any, *, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise CodePlanningError(f"{field_name} must be text")
    text = value.strip()
    if not text or len(text) > limit:
        raise CodePlanningError(f"{field_name} must be non-empty and at most {limit} characters")
    return text


def _governed_ref(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=1_024)
    if (
        "://" not in text
        or text.startswith(("/", "\\"))
        or "\\" in text
        or text.lower().startswith("file://")
    ):
        raise CodePlanningError(f"{field_name} must be an opaque governed ref, not a path")
    return text


def _governed_refs(
    values: Sequence[str],
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise CodePlanningError(f"{field_name} must be an explicit sequence")
    result: list[str] = []
    for index, value in enumerate(values):
        ref = _governed_ref(value, field_name=f"{field_name}[{index}]")
        if ref not in result:
            result.append(ref)
    if not result and not allow_empty:
        raise CodePlanningError(f"{field_name} must not be empty")
    return tuple(result)


def _sha256(value: Any, *, field_name: str) -> str:
    text = _required_text(value, field_name=field_name, limit=64).lower()
    if not _SHA256_RE.fullmatch(text):
        raise CodePlanningError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


def _unit_interval(value: Any, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CodePlanningError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise CodePlanningError(f"{field_name} must be within [0, 1]")
    return number


def _strict_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise CodePlanningError(f"{field_name} must be boolean")
    return value


def _dedupe(*groups: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        for raw in group:
            text = str(raw or "").strip()
            if text and text not in values:
                values.append(text)
    return tuple(values)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rounded(value: float) -> float:
    return round(float(value), 10)


__all__ = [
    "CODE_DOMAIN",
    "CODE_NODE_OUTPUT_SCHEMA",
    "CODE_SHADOW_MODE",
    "CodePatchSpec",
    "CodePlanningError",
    "CodeTaskSnapshot",
]
