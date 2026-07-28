"""NE-06 proposal-only Feedback, Knowledge and Identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,1023}$"
)


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


def _ref(value: Any, field: str) -> str:
    result = _text(value, field, 1_024)
    if _REF_RE.fullmatch(result) is None:
        raise KnowledgeEvolutionContractError(
            f"{field} must be a bounded durable URI"
        )
    return result


def _optional_ref(value: Any, field: str) -> str:
    result = str(value or "").strip()
    return "" if not result else _ref(result, field)


def _positive_int(value: Any, field: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise KnowledgeEvolutionContractError(
            f"{field} must be an integer >= {minimum}"
        )
    return int(value)


def _refs(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise KnowledgeEvolutionContractError(f"{field} must be a list")
    result = tuple(sorted({_ref(item, field) for item in value}))
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

    @property
    def proposal_digest(self) -> str:
        return _hash(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.feedback-proposal.v1",
            "proposal_id": self.proposal_id,
            "source_kind": self.source_kind,
            "trace_digest": self.trace_digest,
            "outcome_digest": self.outcome_digest,
            "confidence": self.confidence,
            "target_version": self.target_version,
            "change": _copy(self.change),
            "rollback_ref": self.rollback_ref,
            "review_required": True,
            "applied": False,
        }
        if include_digest:
            result["proposal_digest"] = self.proposal_digest
        return result


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
    no_change_release: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "migration_id", _text(self.migration_id, "migration_id", 160))
        for name in ("previous_snapshot_digest", "proposed_snapshot_digest", "diff_digest", "rollback_snapshot_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        object.__setattr__(self, "regression_report_ref", _text(self.regression_report_ref, "regression_report_ref", 1_024))
        object.__setattr__(self, "review_ref", _text(self.review_ref, "review_ref", 1_024))
        if isinstance(self.activation_version, bool) or not isinstance(self.activation_version, int) or self.activation_version < 1:
            raise KnowledgeEvolutionContractError("activation_version must be positive")
        if not isinstance(self.no_change_release, bool):
            raise KnowledgeEvolutionContractError("no_change_release must be bool")
        if self.previous_snapshot_digest == self.proposed_snapshot_digest and not self.no_change_release:
            raise KnowledgeEvolutionContractError("identity migration must describe a change or be explicitly no-op outside this plan")

    @property
    def plan_digest(self) -> str:
        return _hash(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.identity-migration-plan.v1",
            "migration_id": self.migration_id,
            "previous_snapshot_digest": self.previous_snapshot_digest,
            "proposed_snapshot_digest": self.proposed_snapshot_digest,
            "diff_digest": self.diff_digest,
            "regression_report_ref": self.regression_report_ref,
            "review_ref": self.review_ref,
            "rollback_snapshot_digest": self.rollback_snapshot_digest,
            "activation_version": self.activation_version,
            "no_change_release": self.no_change_release,
        }
        if include_digest:
            result["plan_digest"] = self.plan_digest
        return result


@dataclass(frozen=True, slots=True)
class VersionActivationDecisionV1:
    """One immutable, evidence-bound version transition decision."""

    decision_id: str
    operation_id: str
    workspace_id: str
    subject_kind: str
    subject_id: str
    action: str
    target_version: str
    target_digest: str
    previous_version: str = ""
    previous_digest: str = ""
    authority_refs: tuple[str, ...] = ()
    expected_generation: int = 0
    reason_code: str = "EVOLUTION_VERSION_TRANSITION_APPROVED"

    def __post_init__(self) -> None:
        for name, limit in (
            ("decision_id", 255),
            ("operation_id", 255),
            ("workspace_id", 128),
            ("subject_id", 255),
            ("target_version", 128),
            ("reason_code", 128),
        ):
            object.__setattr__(
                self, name, _text(getattr(self, name), name, limit)
            )
        kind = _text(self.subject_kind, "subject_kind", 32).upper()
        if kind not in {"FEEDBACK_POLICY", "KNOWLEDGE", "IDENTITY"}:
            raise KnowledgeEvolutionContractError(
                "subject_kind is unsupported"
            )
        object.__setattr__(self, "subject_kind", kind)
        action = _text(self.action, "action", 32).upper()
        if action not in {
            "APPLY",
            "ROLLBACK",
            "ACTIVATE",
            "DEPRECATE",
        }:
            raise KnowledgeEvolutionContractError("action is unsupported")
        object.__setattr__(self, "action", action)
        object.__setattr__(
            self, "target_digest", _digest(self.target_digest, "target_digest")
        )
        previous_version = str(self.previous_version or "").strip()
        previous_digest = str(self.previous_digest or "").strip().lower()
        if bool(previous_version) != bool(previous_digest):
            raise KnowledgeEvolutionContractError(
                "previous version and digest must be supplied together"
            )
        if previous_version:
            object.__setattr__(
                self,
                "previous_version",
                _text(previous_version, "previous_version", 128),
            )
            object.__setattr__(
                self,
                "previous_digest",
                _digest(previous_digest, "previous_digest"),
            )
        else:
            object.__setattr__(self, "previous_version", "")
            object.__setattr__(self, "previous_digest", "")
        object.__setattr__(
            self,
            "authority_refs",
            _refs(self.authority_refs, "authority_refs"),
        )
        if not self.authority_refs:
            raise KnowledgeEvolutionContractError(
                "authority_refs must not be empty"
            )
        object.__setattr__(
            self,
            "expected_generation",
            _positive_int(
                self.expected_generation,
                "expected_generation",
                minimum=0,
            ),
        )

    @property
    def decision_digest(self) -> str:
        return _hash(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.version-activation-decision.v1",
            "decision_id": self.decision_id,
            "operation_id": self.operation_id,
            "workspace_id": self.workspace_id,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "action": self.action,
            "target_version": self.target_version,
            "target_digest": self.target_digest,
            "previous_version": self.previous_version,
            "previous_digest": self.previous_digest,
            "authority_refs": list(self.authority_refs),
            "expected_generation": self.expected_generation,
            "reason_code": self.reason_code,
        }
        if include_digest:
            result["decision_digest"] = self.decision_digest
        return result


@dataclass(frozen=True, slots=True)
class EvolutionTransitionReceiptV1:
    """Terminal receipt for one canonical evolution transition."""

    receipt_id: str
    operation_id: str
    decision_digest: str
    workspace_id: str
    subject_kind: str
    subject_id: str
    event: str
    target_version: str
    target_digest: str
    previous_version: str
    previous_digest: str
    active_generation: int
    projection_digest: str
    created_at_ms: int

    def __post_init__(self) -> None:
        for name, limit in (
            ("receipt_id", 255),
            ("operation_id", 255),
            ("workspace_id", 128),
            ("subject_id", 255),
            ("target_version", 128),
        ):
            object.__setattr__(
                self, name, _text(getattr(self, name), name, limit)
            )
        object.__setattr__(
            self,
            "decision_digest",
            _digest(self.decision_digest, "decision_digest"),
        )
        kind = _text(self.subject_kind, "subject_kind", 32).upper()
        if kind not in {"FEEDBACK_POLICY", "KNOWLEDGE", "IDENTITY"}:
            raise KnowledgeEvolutionContractError(
                "receipt subject_kind is unsupported"
            )
        object.__setattr__(self, "subject_kind", kind)
        event = _text(self.event, "event", 32).upper()
        if event not in {
            "APPLIED",
            "ROLLED_BACK",
            "ACTIVATED",
            "DEPRECATED",
            "REJECTED",
        }:
            raise KnowledgeEvolutionContractError(
                "receipt event is unsupported"
            )
        object.__setattr__(self, "event", event)
        for name in ("target_digest", "projection_digest"):
            object.__setattr__(
                self, name, _digest(getattr(self, name), name)
            )
        previous_version = str(self.previous_version or "").strip()
        previous_digest = str(self.previous_digest or "").strip()
        if bool(previous_version) != bool(previous_digest):
            raise KnowledgeEvolutionContractError(
                "receipt previous identity is incomplete"
            )
        if previous_version:
            object.__setattr__(
                self,
                "previous_version",
                _text(previous_version, "previous_version", 128),
            )
            object.__setattr__(
                self,
                "previous_digest",
                _digest(previous_digest, "previous_digest"),
            )
        object.__setattr__(
            self,
            "active_generation",
            _positive_int(self.active_generation, "active_generation"),
        )
        object.__setattr__(
            self,
            "created_at_ms",
            _positive_int(self.created_at_ms, "created_at_ms", minimum=0),
        )

    @property
    def receipt_digest(self) -> str:
        return _hash(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.evolution-transition-receipt.v1",
            "receipt_id": self.receipt_id,
            "operation_id": self.operation_id,
            "decision_digest": self.decision_digest,
            "workspace_id": self.workspace_id,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "event": self.event,
            "target_version": self.target_version,
            "target_digest": self.target_digest,
            "previous_version": self.previous_version,
            "previous_digest": self.previous_digest,
            "active_generation": self.active_generation,
            "projection_digest": self.projection_digest,
            "created_at_ms": self.created_at_ms,
        }
        if include_digest:
            result["receipt_digest"] = self.receipt_digest
        return result


@dataclass(frozen=True, slots=True)
class KnowledgeEntryV2:
    """Versioned Knowledge OS entry with an explicit active-winner key."""

    namespace: str
    logical_key: str
    entry_id: str
    workspace_id: str
    owner_id: str
    content_summary: str
    content_digest: str
    provenance_ref: str
    version: int
    validity: str
    confidence: float
    expires_at_ms: int = 0
    conflict_refs: tuple[str, ...] = ()
    decision_refs: tuple[str, ...] = ()
    supersedes: str = ""
    deprecated: bool = False

    def __post_init__(self) -> None:
        namespace = _text(self.namespace, "namespace", 512)
        workspace_id = _text(self.workspace_id, "workspace_id", 128)
        owner_id = _text(self.owner_id, "owner_id", 128)
        expected_project = f"project-knowledge://workspace/{workspace_id}"
        expected_life = f"life-memory://person/{owner_id}"
        if namespace not in {expected_project, expected_life}:
            raise KnowledgeEvolutionContractError(
                "KnowledgeEntryV2 namespace must exactly bind workspace or person"
            )
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "owner_id", owner_id)
        for name, limit in (
            ("logical_key", 255),
            ("content_summary", 4_000),
        ):
            object.__setattr__(
                self, name, _text(getattr(self, name), name, limit)
            )
        object.__setattr__(self, "entry_id", _ref(self.entry_id, "entry_id"))
        if _hash(self.content_summary) != _digest(
            self.content_digest, "content_digest"
        ):
            raise KnowledgeEvolutionContractError(
                "content_digest does not match content_summary"
            )
        object.__setattr__(
            self, "content_digest", _digest(self.content_digest, "content_digest")
        )
        object.__setattr__(
            self,
            "provenance_ref",
            _ref(self.provenance_ref, "provenance_ref"),
        )
        object.__setattr__(
            self, "version", _positive_int(self.version, "version")
        )
        validity = _text(self.validity, "validity", 32).upper()
        if validity not in {"PROPOSED", "VALID", "STALE", "CONFLICTED"}:
            raise KnowledgeEvolutionContractError(
                "KnowledgeEntryV2 validity is unsupported"
            )
        object.__setattr__(self, "validity", validity)
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise KnowledgeEvolutionContractError(
                "knowledge confidence must be within [0, 1]"
            )
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(
            self,
            "expires_at_ms",
            _positive_int(
                self.expires_at_ms,
                "expires_at_ms",
                minimum=0,
            ),
        )
        object.__setattr__(
            self, "conflict_refs", _refs(self.conflict_refs, "conflict_refs")
        )
        object.__setattr__(
            self, "decision_refs", _refs(self.decision_refs, "decision_refs")
        )
        object.__setattr__(
            self,
            "supersedes",
            _optional_ref(self.supersedes, "supersedes"),
        )
        if not isinstance(self.deprecated, bool):
            raise KnowledgeEvolutionContractError("deprecated must be bool")
        if self.deprecated and validity == "VALID":
            raise KnowledgeEvolutionContractError(
                "deprecated entry cannot declare VALID"
            )

    @property
    def entry_digest(self) -> str:
        return _hash(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.knowledge-entry.v2",
            "namespace": self.namespace,
            "logical_key": self.logical_key,
            "entry_id": self.entry_id,
            "workspace_id": self.workspace_id,
            "owner_id": self.owner_id,
            "content_summary": self.content_summary,
            "content_digest": self.content_digest,
            "provenance_ref": self.provenance_ref,
            "version": self.version,
            "validity": self.validity,
            "confidence": self.confidence,
            "expires_at_ms": self.expires_at_ms,
            "conflict_refs": list(self.conflict_refs),
            "decision_refs": list(self.decision_refs),
            "supersedes": self.supersedes,
            "deprecated": self.deprecated,
        }
        if include_digest:
            result["entry_digest"] = self.entry_digest
        return result


@dataclass(frozen=True, slots=True)
class DecisionRecordV1:
    """Structured project ADR source that can enter Knowledge OS."""

    decision_id: str
    decision_key: str
    workspace_id: str
    owner_id: str
    title: str
    decision_summary: str
    rationale_summary: str
    consequence_summary: str
    status: str
    version: int
    evidence_refs: tuple[str, ...]
    supersedes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision_id",
            _ref(self.decision_id, "decision_id"),
        )
        for name, limit in (
            ("decision_key", 255),
            ("workspace_id", 128),
            ("owner_id", 128),
            ("title", 240),
            ("decision_summary", 1_200),
            ("rationale_summary", 1_200),
            ("consequence_summary", 1_200),
        ):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), name, limit),
            )
        status = _text(self.status, "status", 32).upper()
        if status not in {
            "PROPOSED",
            "ACCEPTED",
            "SUPERSEDED",
            "DEPRECATED",
        }:
            raise KnowledgeEvolutionContractError(
                "DecisionRecordV1 status is unsupported"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "version",
            _positive_int(self.version, "version"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _refs(self.evidence_refs, "evidence_refs"),
        )
        object.__setattr__(
            self,
            "supersedes",
            _optional_ref(self.supersedes, "supersedes"),
        )
        if self.version == 1 and self.supersedes:
            raise KnowledgeEvolutionContractError(
                "first DecisionRecordV1 version cannot supersede another"
            )
        if self.version > 1 and not self.supersedes:
            raise KnowledgeEvolutionContractError(
                "later DecisionRecordV1 version must supersede another"
            )

    @property
    def decision_digest(self) -> str:
        return _hash(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.decision-record.v1",
            "decision_id": self.decision_id,
            "decision_key": self.decision_key,
            "workspace_id": self.workspace_id,
            "owner_id": self.owner_id,
            "title": self.title,
            "decision_summary": self.decision_summary,
            "rationale_summary": self.rationale_summary,
            "consequence_summary": self.consequence_summary,
            "status": self.status,
            "version": self.version,
            "evidence_refs": list(self.evidence_refs),
            "supersedes": self.supersedes,
        }
        if include_digest:
            result["decision_digest"] = self.decision_digest
        return result

    def to_knowledge_entry(
        self,
        *,
        previous_entry_id: str = "",
    ) -> KnowledgeEntryV2:
        """Compile the ADR into a project-scoped immutable knowledge entry."""

        prior = _optional_ref(previous_entry_id, "previous_entry_id")
        if bool(prior) != bool(self.supersedes):
            raise KnowledgeEvolutionContractError(
                "DecisionRecordV1 predecessor identity is incomplete"
            )
        summary = (
            f"{self.title}\n"
            f"决定：{self.decision_summary}\n"
            f"理由：{self.rationale_summary}\n"
            f"影响：{self.consequence_summary}"
        )
        record_ref = (
            "decision-record://sha256/" + self.decision_digest
        )
        deprecated = self.status in {"SUPERSEDED", "DEPRECATED"}
        validity = (
            "VALID"
            if self.status == "ACCEPTED"
            else "STALE"
            if deprecated
            else "PROPOSED"
        )
        return KnowledgeEntryV2(
            namespace=(
                "project-knowledge://workspace/"
                f"{self.workspace_id}"
            ),
            logical_key=f"decision:{self.decision_key}",
            entry_id=(
                "project-decision://sha256/"
                f"{self.decision_digest}"
            ),
            workspace_id=self.workspace_id,
            owner_id=self.owner_id,
            content_summary=summary,
            content_digest=_hash(summary),
            provenance_ref=record_ref,
            version=self.version,
            validity=validity,
            confidence=1.0,
            decision_refs=tuple(
                sorted({record_ref, *self.evidence_refs})
            ),
            supersedes=prior,
            deprecated=deprecated,
        )


@dataclass(frozen=True, slots=True)
class IdentityMigrationPlanV2:
    """Digest-only release migration plan bound to authoritative snapshots."""

    migration_id: str
    workspace_id: str
    previous_snapshot_ref: str
    proposed_snapshot_ref: str
    previous_snapshot_digest: str
    proposed_snapshot_digest: str
    diff_digest: str
    lab_report_digest: str
    regression_report_digest: str
    review_id: str
    review_status_version: int
    rollback_snapshot_ref: str
    activation_version: int
    no_change_release: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "migration_id", _text(self.migration_id, "migration_id", 255)
        )
        object.__setattr__(
            self, "workspace_id", _text(self.workspace_id, "workspace_id", 128)
        )
        for name in (
            "previous_snapshot_ref",
            "proposed_snapshot_ref",
            "rollback_snapshot_ref",
        ):
            object.__setattr__(self, name, _ref(getattr(self, name), name))
        for name in (
            "previous_snapshot_digest",
            "proposed_snapshot_digest",
            "diff_digest",
            "lab_report_digest",
            "regression_report_digest",
        ):
            object.__setattr__(
                self, name, _digest(getattr(self, name), name)
            )
        object.__setattr__(
            self, "review_id", _text(self.review_id, "review_id", 255)
        )
        object.__setattr__(
            self,
            "review_status_version",
            _positive_int(
                self.review_status_version, "review_status_version"
            ),
        )
        object.__setattr__(
            self,
            "activation_version",
            _positive_int(self.activation_version, "activation_version"),
        )
        if not isinstance(self.no_change_release, bool):
            raise KnowledgeEvolutionContractError(
                "no_change_release must be bool"
            )
        equal = (
            self.previous_snapshot_digest
            == self.proposed_snapshot_digest
        )
        if equal != self.no_change_release:
            raise KnowledgeEvolutionContractError(
                "no_change_release must match snapshot identity"
            )
        if self.rollback_snapshot_ref != self.previous_snapshot_ref:
            raise KnowledgeEvolutionContractError(
                "rollback_snapshot_ref must be exact previous snapshot"
            )

    @property
    def plan_digest(self) -> str:
        return _hash(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.identity-migration-plan.v2",
            "migration_id": self.migration_id,
            "workspace_id": self.workspace_id,
            "previous_snapshot_ref": self.previous_snapshot_ref,
            "proposed_snapshot_ref": self.proposed_snapshot_ref,
            "previous_snapshot_digest": self.previous_snapshot_digest,
            "proposed_snapshot_digest": self.proposed_snapshot_digest,
            "diff_digest": self.diff_digest,
            "lab_report_digest": self.lab_report_digest,
            "regression_report_digest": self.regression_report_digest,
            "review_id": self.review_id,
            "review_status_version": self.review_status_version,
            "rollback_snapshot_ref": self.rollback_snapshot_ref,
            "activation_version": self.activation_version,
            "no_change_release": self.no_change_release,
        }
        if include_digest:
            result["plan_digest"] = self.plan_digest
        return result


__all__ = [
    "DecisionRecordV1",
    "EvolutionTransitionReceiptV1",
    "FeedbackProposalV1",
    "IdentityMigrationPlanV2",
    "IdentityMigrationPlanV1",
    "KnowledgeEntryV2",
    "KnowledgeEntryV1",
    "KnowledgeEvolutionContractError",
    "VersionActivationDecisionV1",
]
