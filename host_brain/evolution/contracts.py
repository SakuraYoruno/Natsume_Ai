"""NE-06 proposal-only Feedback, Knowledge and Identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeEvolutionContractError(ValueError):
    """Evolution proposal or knowledge identity is invalid."""


def _text(value: Any, field: str, limit: int = 240) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise KnowledgeEvolutionContractError(f"{field} must be non-empty and bounded")
    return result


def _digest(value: Any, field: str) -> str:
    result = _text(value, field, 64).lower()
    if not _DIGEST_RE.fullmatch(result):
        raise KnowledgeEvolutionContractError(f"{field} must be a SHA-256 digest")
    return result


def _copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise KnowledgeEvolutionContractError("payload must be JSON-safe") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FeedbackProposalV1:
    proposal_id: str
    source_kind: str
    trace_digest: str
    outcome_digest: str
    confidence: float
    target_version: str
    change: Mapping[str, Any]
    rollback_ref: str
    review_required: bool = True
    applied: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _text(self.proposal_id, "proposal_id", 160))
        object.__setattr__(self, "source_kind", _text(self.source_kind, "source_kind", 80))
        object.__setattr__(self, "trace_digest", _digest(self.trace_digest, "trace_digest"))
        object.__setattr__(self, "outcome_digest", _digest(self.outcome_digest, "outcome_digest"))
        value = float(self.confidence)
        if not 0.0 <= value <= 1.0:
            raise KnowledgeEvolutionContractError("confidence must be within [0, 1]")
        object.__setattr__(self, "confidence", value)
        object.__setattr__(self, "target_version", _text(self.target_version, "target_version", 120))
        if not isinstance(self.change, Mapping) or not self.change:
            raise KnowledgeEvolutionContractError("feedback change must be non-empty")
        object.__setattr__(self, "change", _copy(dict(self.change)))
        object.__setattr__(self, "rollback_ref", _text(self.rollback_ref, "rollback_ref", 1_024))
        if not self.review_required or self.applied:
            raise KnowledgeEvolutionContractError("feedback proposals require Review and cannot be pre-applied")
        object.__setattr__(self, "review_required", True)
        object.__setattr__(self, "applied", False)


@dataclass(frozen=True, slots=True)
class KnowledgeEntryV1:
    namespace: str
    entry_id: str
    workspace_id: str
    content_digest: str
    provenance_ref: str
    version: int
    validity: str
    confidence: float
    conflict_group: str = ""
    supersedes: str = ""
    deprecated: bool = False

    def __post_init__(self) -> None:
        namespace = _text(self.namespace, "namespace", 160)
        if not (namespace.startswith("life-memory://") or namespace.startswith("project-knowledge://")):
            raise KnowledgeEvolutionContractError("KnowledgeEntry namespace must be life-memory:// or project-knowledge://")
        object.__setattr__(self, "namespace", namespace)
        for name in ("entry_id", "workspace_id", "provenance_ref"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 1_024))
        object.__setattr__(self, "content_digest", _digest(self.content_digest, "content_digest"))
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise KnowledgeEvolutionContractError("knowledge version must be positive")
        object.__setattr__(self, "validity", _text(self.validity, "validity", 48).upper())
        value = float(self.confidence)
        if not 0.0 <= value <= 1.0:
            raise KnowledgeEvolutionContractError("knowledge confidence must be within [0, 1]")
        object.__setattr__(self, "confidence", value)
        object.__setattr__(self, "conflict_group", str(self.conflict_group or "").strip())
        object.__setattr__(self, "supersedes", str(self.supersedes or "").strip())

    @property
    def entry_digest(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.knowledge-entry.v1",
            "namespace": self.namespace,
            "entry_id": self.entry_id,
            "workspace_id": self.workspace_id,
            "content_digest": self.content_digest,
            "provenance_ref": self.provenance_ref,
            "version": self.version,
            "validity": self.validity,
            "confidence": self.confidence,
            "conflict_group": self.conflict_group,
            "supersedes": self.supersedes,
            "deprecated": self.deprecated,
        }


@dataclass(frozen=True, slots=True)
class IdentityMigrationPlanV1:
    migration_id: str
    previous_snapshot_digest: str
    proposed_snapshot_digest: str
    diff_digest: str
    regression_report_ref: str
    review_ref: str
    rollback_snapshot_digest: str
    activation_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "migration_id", _text(self.migration_id, "migration_id", 160))
        for name in ("previous_snapshot_digest", "proposed_snapshot_digest", "diff_digest", "rollback_snapshot_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        object.__setattr__(self, "regression_report_ref", _text(self.regression_report_ref, "regression_report_ref", 1_024))
        object.__setattr__(self, "review_ref", _text(self.review_ref, "review_ref", 1_024))
        if isinstance(self.activation_version, bool) or not isinstance(self.activation_version, int) or self.activation_version < 1:
            raise KnowledgeEvolutionContractError("activation_version must be positive")
        if self.previous_snapshot_digest == self.proposed_snapshot_digest:
            raise KnowledgeEvolutionContractError("identity migration must describe a change or be explicitly no-op outside this plan")


__all__ = [
    "FeedbackProposalV1",
    "IdentityMigrationPlanV1",
    "KnowledgeEntryV1",
    "KnowledgeEvolutionContractError",
]
