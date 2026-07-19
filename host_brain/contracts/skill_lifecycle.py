"""Neutral NE-05 Skill activation evidence and receipt contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping


_DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_REF_RE = re.compile(r"\A[a-z][a-z0-9+.-]*://[^\s\x00-\x1f\x7f]{1,1024}\Z")
_ID_RE = re.compile(r"\A[a-z][a-z0-9+.-]*://[^\s\x00-\x1f\x7f]{1,255}\Z")
_REASON_RE = re.compile(r"\A[A-Z][A-Z0-9_]{2,127}\Z")


class SkillLifecycleError(RuntimeError):
    """A Skill lifecycle transition failed closed."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


def _text(value: Any, field: str, *, maximum: int = 1024) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise SkillLifecycleError(f"SKILL_LIFECYCLE_{field.upper()}_INVALID")
    return normalized


def _digest(value: Any, field: str) -> str:
    normalized = _text(value, field, maximum=71)
    if _DIGEST_RE.fullmatch(normalized) is None:
        raise SkillLifecycleError(f"SKILL_LIFECYCLE_{field.upper()}_INVALID")
    return normalized


def _ref(value: Any, field: str) -> str:
    normalized = _text(value, field)
    if _REF_RE.fullmatch(normalized) is None:
        raise SkillLifecycleError(f"SKILL_LIFECYCLE_{field.upper()}_INVALID")
    return normalized


def _canonical(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SkillLifecycleError("SKILL_LIFECYCLE_EVIDENCE_NOT_JSON") from exc


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class SkillActivationEvidenceV1:
    """Immutable evidence required before a bundle can become ACTIVE."""

    activation_id: str
    bundle_id: str
    bundle_digest: str
    bundle_version: str
    lab_report_ref: str
    review_ref: str
    sbom_digest: str
    signature_audit_ref: str
    sandbox_receipt_ref: str
    rollback_ref: str
    maturity_level: int
    previous_bundle_id: str | None = None
    new_capability: bool = True
    reason_code: str = "SKILL_ACTIVATION_APPROVED"
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        activation_id = _text(self.activation_id, "activation_id", maximum=255)
        if _ID_RE.fullmatch(activation_id) is None:
            raise SkillLifecycleError("SKILL_LIFECYCLE_ACTIVATION_ID_INVALID")
        object.__setattr__(self, "activation_id", activation_id)
        for name in ("bundle_id", "bundle_version"):
            object.__setattr__(
                self, name, _text(getattr(self, name), name, maximum=255)
            )
        for name in ("bundle_digest", "sbom_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        for name in (
            "lab_report_ref",
            "review_ref",
            "signature_audit_ref",
            "sandbox_receipt_ref",
            "rollback_ref",
        ):
            object.__setattr__(self, name, _ref(getattr(self, name), name))
        if self.previous_bundle_id is not None:
            previous = _text(
                self.previous_bundle_id, "previous_bundle_id", maximum=255
            )
            if _ID_RE.fullmatch(previous) is None:
                raise SkillLifecycleError(
                    "SKILL_LIFECYCLE_PREVIOUS_BUNDLE_ID_INVALID"
                )
            object.__setattr__(self, "previous_bundle_id", previous)
        if isinstance(self.maturity_level, bool) or not isinstance(
            self.maturity_level, int
        ):
            raise SkillLifecycleError("SKILL_LIFECYCLE_MATURITY_INVALID")
        if not 0 <= self.maturity_level <= 5:
            raise SkillLifecycleError("SKILL_LIFECYCLE_MATURITY_INVALID")
        if not isinstance(self.new_capability, bool):
            raise SkillLifecycleError("SKILL_LIFECYCLE_NEW_CAPABILITY_INVALID")
        if self.new_capability and self.maturity_level > 2:
            raise SkillLifecycleError(
                "SKILL_LIFECYCLE_NEW_CAPABILITY_MATURITY_TOO_HIGH"
            )
        reason = str(self.reason_code or "").strip().upper()
        if _REASON_RE.fullmatch(reason) is None:
            raise SkillLifecycleError("SKILL_LIFECYCLE_REASON_CODE_INVALID")
        object.__setattr__(self, "reason_code", reason)
        computed = _sha256(_canonical(self.to_dict(include_digest=False)))
        supplied = str(self.evidence_digest or "").strip()
        if supplied and supplied != computed:
            raise SkillLifecycleError("SKILL_LIFECYCLE_EVIDENCE_DIGEST_DRIFT")
        object.__setattr__(self, "evidence_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.skill-activation-evidence.v1",
            "activation_id": self.activation_id,
            "bundle_id": self.bundle_id,
            "bundle_digest": self.bundle_digest,
            "bundle_version": self.bundle_version,
            "lab_report_ref": self.lab_report_ref,
            "review_ref": self.review_ref,
            "sbom_digest": self.sbom_digest,
            "signature_audit_ref": self.signature_audit_ref,
            "sandbox_receipt_ref": self.sandbox_receipt_ref,
            "rollback_ref": self.rollback_ref,
            "previous_bundle_id": self.previous_bundle_id,
            "maturity_level": self.maturity_level,
            "new_capability": self.new_capability,
            "reason_code": self.reason_code,
        }
        if include_digest:
            result["evidence_digest"] = self.evidence_digest
        return result


@dataclass(frozen=True, slots=True)
class SkillLifecycleReceiptV1:
    """Append-only receipt for an activation, degradation or rollback event."""

    receipt_id: str
    activation_id: str
    event: str
    bundle_id: str
    evidence_digest: str
    operation_id: str | None
    previous_bundle_id: str | None
    created_at_ms: int
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("receipt_id", "activation_id", "bundle_id"):
            normalized = _text(getattr(self, name), name, maximum=255)
            if _ID_RE.fullmatch(normalized) is None:
                raise SkillLifecycleError(
                    f"SKILL_LIFECYCLE_{name.upper()}_INVALID"
                )
            object.__setattr__(self, name, normalized)
        event = str(self.event or "").strip().upper()
        if event not in {
            "REQUESTED",
            "ACTIVATED",
            "DEGRADED",
            "ROLLED_BACK",
            "REJECTED",
        }:
            raise SkillLifecycleError("SKILL_LIFECYCLE_EVENT_INVALID")
        object.__setattr__(self, "event", event)
        object.__setattr__(
            self,
            "evidence_digest",
            _digest(self.evidence_digest, "evidence_digest"),
        )
        if self.operation_id is not None:
            object.__setattr__(
                self,
                "operation_id",
                _text(self.operation_id, "operation_id", maximum=255),
            )
        if self.previous_bundle_id is not None:
            previous = _text(
                self.previous_bundle_id, "previous_bundle_id", maximum=255
            )
            if _ID_RE.fullmatch(previous) is None:
                raise SkillLifecycleError(
                    "SKILL_LIFECYCLE_PREVIOUS_BUNDLE_ID_INVALID"
                )
            object.__setattr__(self, "previous_bundle_id", previous)
        if (
            isinstance(self.created_at_ms, bool)
            or not isinstance(self.created_at_ms, int)
            or self.created_at_ms < 0
        ):
            raise SkillLifecycleError("SKILL_LIFECYCLE_TIMESTAMP_INVALID")
        computed = _sha256(_canonical(self.to_dict(include_digest=False)))
        supplied = str(self.receipt_digest or "").strip()
        if supplied and supplied != computed:
            raise SkillLifecycleError("SKILL_LIFECYCLE_RECEIPT_DIGEST_DRIFT")
        object.__setattr__(self, "receipt_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.skill-lifecycle-receipt.v1",
            "receipt_id": self.receipt_id,
            "activation_id": self.activation_id,
            "event": self.event,
            "bundle_id": self.bundle_id,
            "evidence_digest": self.evidence_digest,
            "operation_id": self.operation_id,
            "previous_bundle_id": self.previous_bundle_id,
            "created_at_ms": self.created_at_ms,
        }
        if include_digest:
            result["receipt_digest"] = self.receipt_digest
        return result


__all__ = [
    "SkillActivationEvidenceV1",
    "SkillLifecycleError",
    "SkillLifecycleReceiptV1",
]
