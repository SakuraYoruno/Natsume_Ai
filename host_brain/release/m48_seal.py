"""Fail-closed M48 release-seal contracts (T-NLE-24).

This module validates evidence; it never grants the final release approval.
Even a complete bundle can only become ``READY_FOR_MASTER_SEAL``.  ``SEALED``
is intentionally absent from the state model and remains a MASTER decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    ContractViolation,
    require_version,
)


M48_SEAL_MANIFEST_SCHEMA_VERSION = "Natsume.release.m48_seal_manifest.v1"
M48_SEAL_EVIDENCE_SCHEMA_VERSION = "Natsume.release.m48_seal_evidence.v1"
M48_ROLLBACK_MATRIX_SCHEMA_VERSION = "Natsume.release.m48_rollback_matrix.v1"
M48_SEAL_RESULT_SCHEMA_VERSION = "Natsume.release.m48_seal_result.v1"

M48_SEAL_MANIFEST_INVALID = "M48_SEAL_MANIFEST_INVALID"
M48_SEAL_JSON_INVALID = "M48_SEAL_JSON_INVALID"
M48_SEAL_ARTIFACT_MISSING = "M48_SEAL_ARTIFACT_MISSING"
M48_SEAL_HASH_MISMATCH = "M48_SEAL_HASH_MISMATCH"
M48_SEAL_RELEASE_ID_MISMATCH = "M48_SEAL_RELEASE_ID_MISMATCH"
M48_SEAL_EVIDENCE_INVALID = "M48_SEAL_EVIDENCE_INVALID"
M48_SEAL_EVIDENCE_MISSING = "M48_SEAL_EVIDENCE_MISSING"
M48_SEAL_EVIDENCE_FAILED = "M48_SEAL_EVIDENCE_FAILED"
M48_SEAL_EVIDENCE_SKIPPED = "M48_SEAL_EVIDENCE_SKIPPED"
M48_SEAL_EVIDENCE_BLOCKED = "M48_SEAL_EVIDENCE_BLOCKED"
M48_SEAL_EVIDENCE_UNCLASSIFIED = "M48_SEAL_EVIDENCE_UNCLASSIFIED"
M48_SEAL_IDENTITY_PREVIOUS_REF_MISSING = "M48_SEAL_IDENTITY_PREVIOUS_REF_MISSING"
M48_SEAL_IDENTITY_PREVIOUS_REF_MISMATCH = "M48_SEAL_IDENTITY_PREVIOUS_REF_MISMATCH"
M48_SEAL_ROLLBACK_INVALID = "M48_SEAL_ROLLBACK_INVALID"
M48_SEAL_ROLLBACK_MISSING = "M48_SEAL_ROLLBACK_MISSING"
M48_SEAL_ROLLBACK_COMMAND_MISSING = "M48_SEAL_ROLLBACK_COMMAND_MISSING"
M48_SEAL_ROLLBACK_NOT_REHEARSED = "M48_SEAL_ROLLBACK_NOT_REHEARSED"

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_REF_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_ARTIFACTS = 256
_MAX_RECORDS = 2048
_UNCLASSIFIED_TOKENS = frozenset({"UNCLASSIFIED", "UNKNOWN", "NONE", "N_A"})


class M48SealStatus(StrEnum):
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    READY_FOR_MASTER_SEAL = "READY_FOR_MASTER_SEAL"


class M48EvidenceStatus(StrEnum):
    PASS = "PASS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class M48SealArtifactKind(StrEnum):
    EVIDENCE = "EVIDENCE"
    ROLLBACK_MATRIX = "ROLLBACK_MATRIX"


@dataclass(frozen=True, slots=True)
class M48SealArtifact:
    artifact_id: str
    kind: M48SealArtifactKind
    path: str
    sha256: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "M48SealArtifact":
        data = _strict_mapping(
            value,
            required={"artifact_id", "kind", "path", "sha256"},
            name="M48SealArtifact",
        )
        try:
            kind = M48SealArtifactKind(_require_token(data["kind"], "artifact.kind"))
        except ValueError as exc:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "artifact.kind is unsupported") from exc
        return cls(
            artifact_id=_require_id(data["artifact_id"], "artifact.artifact_id"),
            kind=kind,
            path=_require_relative_path(data["path"], "artifact.path"),
            sha256=_require_sha256(data["sha256"], "artifact.sha256"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind.value,
            "path": self.path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class M48SealManifest:
    schema_version: str
    release_id: str
    milestone: str
    git_commit_hash: str
    identity_snapshot_ref: str
    identity_previous_release_ref: str
    required_evidence_ids: tuple[str, ...]
    required_rollback_ids: tuple[str, ...]
    artifacts: tuple[M48SealArtifact, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "M48SealManifest":
        if isinstance(value, Mapping) and "identity_previous_release_ref" not in value:
            raise ContractViolation(
                M48_SEAL_IDENTITY_PREVIOUS_REF_MISSING,
                "identity_previous_release_ref is required",
            )
        required = {
            "schema_version",
            "release_id",
            "milestone",
            "git_commit_hash",
            "identity_snapshot_ref",
            "identity_previous_release_ref",
            "required_evidence_ids",
            "required_rollback_ids",
            "artifacts",
        }
        data = _strict_mapping(value, required=required, name="M48SealManifest")
        require_version(
            data["schema_version"],
            expected=M48_SEAL_MANIFEST_SCHEMA_VERSION,
            contract_name="M48SealManifest",
        )
        release_id = _require_ref(data["release_id"], "release_id")
        milestone = str(data["milestone"] or "").strip()
        if milestone != "M48-A":
            raise ContractViolation(CONTRACT_FIELD_INVALID, "milestone must be M48-A")
        commit = str(data["git_commit_hash"] or "").strip().lower()
        if not _GIT_COMMIT_PATTERN.fullmatch(commit):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "git_commit_hash must be a full Git SHA")
        current_ref = _require_ref(data["identity_snapshot_ref"], "identity_snapshot_ref")
        previous_raw = str(data["identity_previous_release_ref"] or "").strip()
        if not previous_raw:
            raise ContractViolation(
                M48_SEAL_IDENTITY_PREVIOUS_REF_MISSING,
                "identity_previous_release_ref is required",
            )
        previous_ref = _require_ref(previous_raw, "identity_previous_release_ref")
        if previous_ref == current_ref:
            raise ContractViolation(
                M48_SEAL_IDENTITY_PREVIOUS_REF_MISMATCH,
                "current and previous identity release refs must differ",
            )
        evidence_ids = _require_id_tuple(
            data["required_evidence_ids"],
            "required_evidence_ids",
            nonempty=True,
        )
        rollback_ids = _require_id_tuple(
            data["required_rollback_ids"],
            "required_rollback_ids",
            nonempty=True,
        )
        artifact_values = _require_list(data["artifacts"], "artifacts", nonempty=True)
        if len(artifact_values) > _MAX_ARTIFACTS:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "artifacts exceeds the bounded maximum")
        artifacts = tuple(M48SealArtifact.from_mapping(_require_mapping(item, "artifact")) for item in artifact_values)
        _require_unique((item.artifact_id for item in artifacts), "artifact IDs")
        _require_unique((item.path for item in artifacts), "artifact paths")
        evidence_count = sum(item.kind is M48SealArtifactKind.EVIDENCE for item in artifacts)
        rollback_count = sum(item.kind is M48SealArtifactKind.ROLLBACK_MATRIX for item in artifacts)
        if evidence_count < 1 or rollback_count != 1:
            raise ContractViolation(
                M48_SEAL_MANIFEST_INVALID,
                "manifest requires at least one EVIDENCE artifact and exactly one ROLLBACK_MATRIX",
            )
        return cls(
            schema_version=M48_SEAL_MANIFEST_SCHEMA_VERSION,
            release_id=release_id,
            milestone=milestone,
            git_commit_hash=commit,
            identity_snapshot_ref=current_ref,
            identity_previous_release_ref=previous_ref,
            required_evidence_ids=evidence_ids,
            required_rollback_ids=rollback_ids,
            artifacts=artifacts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "milestone": self.milestone,
            "git_commit_hash": self.git_commit_hash,
            "identity_snapshot_ref": self.identity_snapshot_ref,
            "identity_previous_release_ref": self.identity_previous_release_ref,
            "required_evidence_ids": list(self.required_evidence_ids),
            "required_rollback_ids": list(self.required_rollback_ids),
            "artifacts": [item.to_dict() for item in self.artifacts],
        }


@dataclass(frozen=True, slots=True)
class M48EvidenceRecord:
    evidence_id: str
    classification: str
    status: M48EvidenceStatus
    command: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "M48EvidenceRecord":
        if isinstance(value, Mapping) and not str(value.get("classification") or "").strip():
            raise ContractViolation(
                M48_SEAL_EVIDENCE_UNCLASSIFIED,
                "evidence classification must be explicit",
            )
        data = _strict_mapping(
            value,
            required={"evidence_id", "classification", "status", "command", "evidence_refs"},
            name="M48EvidenceRecord",
        )
        classification = _require_token(data["classification"], "evidence.classification")
        if classification in _UNCLASSIFIED_TOKENS:
            raise ContractViolation(
                M48_SEAL_EVIDENCE_UNCLASSIFIED,
                "evidence classification must be explicit",
            )
        try:
            status = M48EvidenceStatus(_require_token(data["status"], "evidence.status"))
        except ValueError as exc:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "evidence.status is unsupported") from exc
        return cls(
            evidence_id=_require_id(data["evidence_id"], "evidence.evidence_id"),
            classification=classification,
            status=status,
            command=_require_command(data["command"], field_name="evidence.command"),
            evidence_refs=_require_ref_tuple(data["evidence_refs"], "evidence.evidence_refs"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "classification": self.classification,
            "status": self.status.value,
            "command": list(self.command),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class M48SealEvidence:
    schema_version: str
    release_id: str
    records: tuple[M48EvidenceRecord, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "M48SealEvidence":
        data = _strict_mapping(
            value,
            required={"schema_version", "release_id", "records"},
            name="M48SealEvidence",
        )
        require_version(
            data["schema_version"],
            expected=M48_SEAL_EVIDENCE_SCHEMA_VERSION,
            contract_name="M48SealEvidence",
        )
        values = _require_list(data["records"], "records", nonempty=True)
        if len(values) > _MAX_RECORDS:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "evidence records exceed the bounded maximum")
        records = tuple(M48EvidenceRecord.from_mapping(_require_mapping(item, "evidence record")) for item in values)
        _require_unique((item.evidence_id for item in records), "evidence IDs")
        return cls(
            schema_version=M48_SEAL_EVIDENCE_SCHEMA_VERSION,
            release_id=_require_ref(data["release_id"], "release_id"),
            records=records,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "records": [item.to_dict() for item in self.records],
        }


@dataclass(frozen=True, slots=True)
class M48RollbackEntry:
    rollback_id: str
    scope: str
    command: tuple[str, ...]
    rehearsal_status: M48EvidenceStatus
    evidence_ref: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "M48RollbackEntry":
        if isinstance(value, Mapping) and "command" not in value:
            raise ContractViolation(
                M48_SEAL_ROLLBACK_COMMAND_MISSING,
                "rollback.command is required",
            )
        data = _strict_mapping(
            value,
            required={"rollback_id", "scope", "command", "rehearsal_status", "evidence_ref"},
            name="M48RollbackEntry",
        )
        try:
            status = M48EvidenceStatus(
                _require_token(data["rehearsal_status"], "rollback.rehearsal_status")
            )
        except ValueError as exc:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "rollback.rehearsal_status is unsupported") from exc
        return cls(
            rollback_id=_require_id(data["rollback_id"], "rollback.rollback_id"),
            scope=_require_token(data["scope"], "rollback.scope"),
            command=_require_command(
                data["command"],
                field_name="rollback.command",
                missing_reason=M48_SEAL_ROLLBACK_COMMAND_MISSING,
            ),
            rehearsal_status=status,
            evidence_ref=_require_ref(data["evidence_ref"], "rollback.evidence_ref"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollback_id": self.rollback_id,
            "scope": self.scope,
            "command": list(self.command),
            "rehearsal_status": self.rehearsal_status.value,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class M48RollbackMatrix:
    schema_version: str
    release_id: str
    previous_release_ref: str
    entries: tuple[M48RollbackEntry, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "M48RollbackMatrix":
        if isinstance(value, Mapping) and "previous_release_ref" not in value:
            raise ContractViolation(
                M48_SEAL_IDENTITY_PREVIOUS_REF_MISSING,
                "rollback previous_release_ref is required",
            )
        data = _strict_mapping(
            value,
            required={"schema_version", "release_id", "previous_release_ref", "entries"},
            name="M48RollbackMatrix",
        )
        require_version(
            data["schema_version"],
            expected=M48_ROLLBACK_MATRIX_SCHEMA_VERSION,
            contract_name="M48RollbackMatrix",
        )
        previous_raw = str(data["previous_release_ref"] or "").strip()
        if not previous_raw:
            raise ContractViolation(
                M48_SEAL_IDENTITY_PREVIOUS_REF_MISSING,
                "rollback previous_release_ref is required",
            )
        values = _require_list(data["entries"], "entries", nonempty=True)
        if len(values) > _MAX_RECORDS:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "rollback entries exceed the bounded maximum")
        entries = tuple(M48RollbackEntry.from_mapping(_require_mapping(item, "rollback entry")) for item in values)
        _require_unique((item.rollback_id for item in entries), "rollback IDs")
        return cls(
            schema_version=M48_ROLLBACK_MATRIX_SCHEMA_VERSION,
            release_id=_require_ref(data["release_id"], "release_id"),
            previous_release_ref=_require_ref(previous_raw, "previous_release_ref"),
            entries=entries,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "previous_release_ref": self.previous_release_ref,
            "entries": [item.to_dict() for item in self.entries],
        }


@dataclass(frozen=True, slots=True)
class M48SealIssue:
    reason_code: str
    detail: str
    artifact_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "reason_code": self.reason_code,
            "detail": self.detail,
            "artifact_id": self.artifact_id,
        }


@dataclass(frozen=True, slots=True)
class M48SealResult:
    schema_version: str
    release_id: str | None
    milestone: str | None
    status: M48SealStatus
    master_approval_required: bool
    issues: tuple[M48SealIssue, ...]
    checked_artifacts: tuple[str, ...]
    verified_evidence_ids: tuple[str, ...]
    verified_rollback_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, M48SealStatus):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "seal result status is invalid")
        if self.master_approval_required is not True:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "MASTER approval must remain required")
        if self.status is M48SealStatus.READY_FOR_MASTER_SEAL and self.issues:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "ready seal result cannot contain issues")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "milestone": self.milestone,
            "status": self.status.value,
            "master_approval_required": True,
            "issues": [item.to_dict() for item in self.issues],
            "checked_artifacts": list(self.checked_artifacts),
            "verified_evidence_ids": list(self.verified_evidence_ids),
            "verified_rollback_ids": list(self.verified_rollback_ids),
        }


def evaluate_m48_release_seal(
    *,
    root: str | Path,
    manifest: Mapping[str, Any] | str | Path,
) -> M48SealResult:
    """Validate a complete M48 bundle without ever issuing final approval."""

    root_path = Path(root).resolve()
    try:
        manifest_value = (
            _load_json_object(_resolve_manifest_path(root_path, manifest))
            if isinstance(manifest, (str, Path))
            else _require_mapping(manifest, "M48 seal manifest")
        )
        parsed_manifest = M48SealManifest.from_mapping(manifest_value)
    except (ContractViolation, OSError, ValueError, json.JSONDecodeError) as exc:
        issue = _issue_from_exception(exc, default_reason=M48_SEAL_MANIFEST_INVALID)
        return _result(status=M48SealStatus.BLOCKED, issues=(issue,))

    issues: list[M48SealIssue] = []
    checked_artifacts: list[str] = []
    evidence_records: dict[str, M48EvidenceRecord] = {}
    rollback_entries: dict[str, M48RollbackEntry] = {}
    hard_failure = False

    for artifact in parsed_manifest.artifacts:
        artifact_path = _resolve_artifact_path(root_path, artifact.path)
        if artifact_path is None or not artifact_path.is_file():
            issues.append(
                M48SealIssue(
                    M48_SEAL_ARTIFACT_MISSING,
                    f"required artifact is missing: {artifact.path}",
                    artifact.artifact_id,
                )
            )
            continue
        actual_digest = _sha256_file(artifact_path)
        if actual_digest != artifact.sha256:
            issues.append(
                M48SealIssue(
                    M48_SEAL_HASH_MISMATCH,
                    f"artifact hash mismatch: {artifact.path}",
                    artifact.artifact_id,
                )
            )
            continue
        checked_artifacts.append(artifact.artifact_id)
        try:
            payload = _load_json_object(artifact_path)
            if artifact.kind is M48SealArtifactKind.EVIDENCE:
                evidence = M48SealEvidence.from_mapping(payload)
                if evidence.release_id != parsed_manifest.release_id:
                    raise ContractViolation(
                        M48_SEAL_RELEASE_ID_MISMATCH,
                        "evidence release_id does not match manifest",
                    )
                for record in evidence.records:
                    if record.evidence_id in evidence_records:
                        raise ContractViolation(
                            M48_SEAL_EVIDENCE_INVALID,
                            f"duplicate evidence_id across artifacts: {record.evidence_id}",
                        )
                    evidence_records[record.evidence_id] = record
            else:
                rollback = M48RollbackMatrix.from_mapping(payload)
                if rollback.release_id != parsed_manifest.release_id:
                    raise ContractViolation(
                        M48_SEAL_RELEASE_ID_MISMATCH,
                        "rollback release_id does not match manifest",
                    )
                if rollback.previous_release_ref != parsed_manifest.identity_previous_release_ref:
                    raise ContractViolation(
                        M48_SEAL_IDENTITY_PREVIOUS_REF_MISMATCH,
                        "rollback previous_release_ref does not match identity continuity ref",
                    )
                rollback_entries.update((entry.rollback_id, entry) for entry in rollback.entries)
        except (ContractViolation, OSError, ValueError, json.JSONDecodeError) as exc:
            default_reason = (
                M48_SEAL_EVIDENCE_INVALID
                if artifact.kind is M48SealArtifactKind.EVIDENCE
                else M48_SEAL_ROLLBACK_INVALID
            )
            issues.append(
                _issue_from_exception(
                    exc,
                    default_reason=default_reason,
                    artifact_id=artifact.artifact_id,
                )
            )

    for record in evidence_records.values():
        if record.status is M48EvidenceStatus.FAILED:
            issues.append(
                M48SealIssue(M48_SEAL_EVIDENCE_FAILED, f"evidence failed: {record.evidence_id}")
            )
            hard_failure = True
        elif record.status is M48EvidenceStatus.SKIPPED:
            issues.append(
                M48SealIssue(M48_SEAL_EVIDENCE_SKIPPED, f"evidence skipped: {record.evidence_id}")
            )
        elif record.status is M48EvidenceStatus.BLOCKED:
            issues.append(
                M48SealIssue(M48_SEAL_EVIDENCE_BLOCKED, f"evidence blocked: {record.evidence_id}")
            )

    for evidence_id in parsed_manifest.required_evidence_ids:
        if evidence_id not in evidence_records:
            issues.append(
                M48SealIssue(
                    M48_SEAL_EVIDENCE_MISSING,
                    f"required evidence is missing: {evidence_id}",
                )
            )

    for entry in rollback_entries.values():
        if entry.rehearsal_status is not M48EvidenceStatus.PASS:
            issues.append(
                M48SealIssue(
                    M48_SEAL_ROLLBACK_NOT_REHEARSED,
                    f"rollback rehearsal did not pass: {entry.rollback_id}",
                )
            )
            if entry.rehearsal_status is M48EvidenceStatus.FAILED:
                hard_failure = True

    for rollback_id in parsed_manifest.required_rollback_ids:
        if rollback_id not in rollback_entries:
            issues.append(
                M48SealIssue(
                    M48_SEAL_ROLLBACK_MISSING,
                    f"required rollback entry is missing: {rollback_id}",
                )
            )
    status = (
        M48SealStatus.FAILED
        if hard_failure
        else M48SealStatus.BLOCKED
        if issues
        else M48SealStatus.READY_FOR_MASTER_SEAL
    )
    verified_evidence = tuple(
        sorted(
            evidence_id
            for evidence_id in parsed_manifest.required_evidence_ids
            if evidence_id in evidence_records
            and evidence_records[evidence_id].status is M48EvidenceStatus.PASS
        )
    )
    verified_rollback = tuple(
        sorted(
            rollback_id
            for rollback_id in parsed_manifest.required_rollback_ids
            if rollback_id in rollback_entries
            and rollback_entries[rollback_id].rehearsal_status is M48EvidenceStatus.PASS
        )
    )
    return _result(
        status=status,
        manifest=parsed_manifest,
        issues=tuple(issues),
        checked_artifacts=tuple(sorted(checked_artifacts)),
        verified_evidence_ids=verified_evidence,
        verified_rollback_ids=verified_rollback,
    )


def load_m48_seal_manifest(path: str | Path) -> M48SealManifest:
    """Load one strict, versioned manifest JSON document."""

    return M48SealManifest.from_mapping(_load_json_object(Path(path)))


def _result(
    *,
    status: M48SealStatus,
    manifest: M48SealManifest | None = None,
    issues: tuple[M48SealIssue, ...] = (),
    checked_artifacts: tuple[str, ...] = (),
    verified_evidence_ids: tuple[str, ...] = (),
    verified_rollback_ids: tuple[str, ...] = (),
) -> M48SealResult:
    return M48SealResult(
        schema_version=M48_SEAL_RESULT_SCHEMA_VERSION,
        release_id=manifest.release_id if manifest else None,
        milestone=manifest.milestone if manifest else None,
        status=status,
        master_approval_required=True,
        issues=issues,
        checked_artifacts=checked_artifacts,
        verified_evidence_ids=verified_evidence_ids,
        verified_rollback_ids=verified_rollback_ids,
    )


def _issue_from_exception(
    exc: BaseException,
    *,
    default_reason: str,
    artifact_id: str | None = None,
) -> M48SealIssue:
    if isinstance(exc, ContractViolation):
        return M48SealIssue(exc.reason_code, exc.detail, artifact_id)
    if isinstance(exc, json.JSONDecodeError):
        return M48SealIssue(M48_SEAL_JSON_INVALID, "artifact is not valid JSON", artifact_id)
    return M48SealIssue(default_reason, str(exc) or "seal validation failed", artifact_id)


def _resolve_manifest_path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ContractViolation(M48_SEAL_MANIFEST_INVALID, "manifest path escapes repository root") from exc
    return candidate


def _resolve_artifact_path(root: Path, relative: str) -> Path | None:
    candidate = (root / PurePosixPath(relative)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContractViolation(M48_SEAL_ARTIFACT_MISSING, f"JSON file is missing: {path.name}")
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ContractViolation(M48_SEAL_JSON_INVALID, "JSON evidence exceeds the bounded size")

    def pairs_hook(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ContractViolation(M48_SEAL_JSON_INVALID, f"duplicate JSON key: {key}")
            result[key] = item
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs_hook)
    except UnicodeDecodeError as exc:
        raise ContractViolation(M48_SEAL_JSON_INVALID, "JSON evidence must be UTF-8") from exc
    return _require_mapping(value, "JSON document")


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def _strict_mapping(value: Any, *, required: set[str], name: str) -> dict[str, Any]:
    data = _require_mapping(value, name)
    keys = set(data)
    missing = sorted(required - keys)
    unknown = sorted(keys - required)
    if missing:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{name} is missing fields: {', '.join(missing)}")
    if unknown:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{name} contains unsupported fields: {', '.join(unknown)}")
    return data


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a JSON object")
    return dict(value)


def _require_list(value: Any, field_name: str, *, nonempty: bool) -> list[Any]:
    if not isinstance(value, list):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a JSON list")
    if nonempty and not value:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} cannot be empty")
    return value


def _require_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a string")
    resolved = value.strip()
    if not _ID_PATTERN.fullmatch(resolved):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} is invalid")
    return resolved


def _require_id_tuple(value: Any, field_name: str, *, nonempty: bool) -> tuple[str, ...]:
    values = _require_list(value, field_name, nonempty=nonempty)
    resolved = tuple(_require_id(item, field_name) for item in values)
    _require_unique(resolved, field_name)
    return resolved


def _require_token(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a string")
    resolved = value.strip()
    if not _TOKEN_PATTERN.fullmatch(resolved):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be an uppercase token")
    return resolved


def _require_ref(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a string")
    resolved = value.strip()
    if not _REF_PATTERN.fullmatch(resolved):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a durable URI")
    return resolved


def _require_ref_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    values = _require_list(value, field_name, nonempty=True)
    resolved = tuple(_require_ref(item, field_name) for item in values)
    _require_unique(resolved, field_name)
    return resolved


def _require_sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a string")
    resolved = value.strip()
    if not _SHA256_PATTERN.fullmatch(resolved):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a lowercase SHA-256 digest")
    return resolved


def _require_relative_path(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a string")
    resolved = value.strip()
    if not resolved or "\\" in resolved or ":" in resolved:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a portable relative path")
    path = PurePosixPath(resolved)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} escapes the repository root")
    return path.as_posix()


def _require_command(
    value: Any,
    *,
    field_name: str,
    missing_reason: str = CONTRACT_FIELD_INVALID,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ContractViolation(missing_reason, f"{field_name} must be a non-empty argv list")
    if len(value) > 64:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} exceeds the bounded maximum")
    command: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 4096:
            raise ContractViolation(missing_reason, f"{field_name} contains an invalid argument")
        command.append(item)
    return tuple(command)


def _require_unique(values: Sequence[str] | Any, field_name: str) -> None:
    materialized = list(values)
    if len(set(materialized)) != len(materialized):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be unique")


__all__ = [
    "M48EvidenceRecord",
    "M48EvidenceStatus",
    "M48RollbackEntry",
    "M48RollbackMatrix",
    "M48SealArtifact",
    "M48SealArtifactKind",
    "M48SealEvidence",
    "M48SealIssue",
    "M48SealManifest",
    "M48SealResult",
    "M48SealStatus",
    "M48_ROLLBACK_MATRIX_SCHEMA_VERSION",
    "M48_SEAL_EVIDENCE_SCHEMA_VERSION",
    "M48_SEAL_MANIFEST_SCHEMA_VERSION",
    "M48_SEAL_RESULT_SCHEMA_VERSION",
    "evaluate_m48_release_seal",
    "load_m48_seal_manifest",
]
