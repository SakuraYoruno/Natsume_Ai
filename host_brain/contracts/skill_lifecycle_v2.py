"""Authoritative NE-05 Skill lifecycle V2 contracts.

V1 intentionally remains a compatibility/read-only contract.  V2 carries the
identities needed to reopen Lab, Review, supply-chain, canary and maturity
authorities before a bundle can influence the Host runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping


_DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_HEX_DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"\A[a-z][a-z0-9+.-]*://[^\s\x00-\x1f\x7f]{1,255}\Z")
_TOKEN_RE = re.compile(r"\A[a-z][a-z0-9._-]{1,127}\Z")
_REASON_RE = re.compile(r"\A[A-Z][A-Z0-9_]{2,127}\Z")


class SkillLifecycleV2Error(RuntimeError):
    """A V2 contract or authority check failed closed."""

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
        raise SkillLifecycleV2Error(
            f"SKILL_LIFECYCLE_V2_{field.upper()}_INVALID"
        )
    return normalized


def _identifier(value: Any, field: str) -> str:
    normalized = _text(value, field, maximum=128).lower()
    if _TOKEN_RE.fullmatch(normalized) is None:
        raise SkillLifecycleV2Error(
            f"SKILL_LIFECYCLE_V2_{field.upper()}_INVALID"
        )
    return normalized


def _uri(value: Any, field: str) -> str:
    normalized = _text(value, field, maximum=255)
    if _ID_RE.fullmatch(normalized) is None:
        raise SkillLifecycleV2Error(
            f"SKILL_LIFECYCLE_V2_{field.upper()}_INVALID"
        )
    return normalized


def _digest(value: Any, field: str) -> str:
    normalized = _text(value, field, maximum=71)
    if _DIGEST_RE.fullmatch(normalized) is None:
        raise SkillLifecycleV2Error(
            f"SKILL_LIFECYCLE_V2_{field.upper()}_INVALID"
        )
    return normalized


def _hex_digest(value: Any, field: str) -> str:
    normalized = _text(value, field, maximum=64)
    if _HEX_DIGEST_RE.fullmatch(normalized) is None:
        raise SkillLifecycleV2Error(
            f"SKILL_LIFECYCLE_V2_{field.upper()}_INVALID"
        )
    return normalized


def _reason(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if _REASON_RE.fullmatch(normalized) is None:
        raise SkillLifecycleV2Error(
            "SKILL_LIFECYCLE_V2_REASON_CODE_INVALID"
        )
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
        raise SkillLifecycleV2Error(
            "SKILL_LIFECYCLE_V2_NOT_CANONICAL_JSON"
        ) from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SkillActivationEvidenceV2:
    """Caller request whose every authority is reopened by the SQLite owner."""

    activation_id: str
    workspace_id: str
    bundle_id: str
    bundle_digest: str
    bundle_version: str
    runtime_adapter_id: str
    runtime_protocol_version: str
    lab_report_digest: str
    review_id: str
    review_status_version: int
    sbom_digest: str
    signature_audit_ref: str
    sandbox_attestation_digest: str
    canary_receipt_digest: str
    maturity_decision_id: str
    maturity_level: int
    previous_bundle_id: str | None = None
    rollback_target_bundle_id: str | None = None
    new_capability: bool = True
    reason_code: str = "SKILL_ACTIVATION_APPROVED"
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "activation_id", _uri(self.activation_id, "activation_id")
        )
        object.__setattr__(
            self, "workspace_id", _identifier(self.workspace_id, "workspace_id")
        )
        for field in ("bundle_id", "bundle_version", "review_id"):
            object.__setattr__(
                self, field, _text(getattr(self, field), field, maximum=255)
            )
        object.__setattr__(
            self,
            "bundle_digest",
            _digest(self.bundle_digest, "bundle_digest"),
        )
        object.__setattr__(
            self, "sbom_digest", _digest(self.sbom_digest, "sbom_digest")
        )
        for field in ("runtime_adapter_id", "runtime_protocol_version"):
            object.__setattr__(
                self, field, _identifier(getattr(self, field), field)
            )
        for field in (
            "lab_report_digest",
            "sandbox_attestation_digest",
            "canary_receipt_digest",
        ):
            object.__setattr__(
                self, field, _hex_digest(getattr(self, field), field)
            )
        object.__setattr__(
            self,
            "signature_audit_ref",
            _uri(self.signature_audit_ref, "signature_audit_ref"),
        )
        object.__setattr__(
            self,
            "maturity_decision_id",
            _uri(self.maturity_decision_id, "maturity_decision_id"),
        )
        if (
            isinstance(self.review_status_version, bool)
            or not isinstance(self.review_status_version, int)
            or self.review_status_version < 1
        ):
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_REVIEW_STATUS_VERSION_INVALID"
            )
        if (
            isinstance(self.maturity_level, bool)
            or not isinstance(self.maturity_level, int)
            or not 0 <= self.maturity_level <= 5
        ):
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_MATURITY_LEVEL_INVALID"
            )
        if not isinstance(self.new_capability, bool):
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_NEW_CAPABILITY_INVALID"
            )
        if self.new_capability and self.maturity_level > 2:
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_NEW_CAPABILITY_LEVEL_TOO_HIGH"
            )
        for field in ("previous_bundle_id", "rollback_target_bundle_id"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(
                    self, field, _text(value, field, maximum=255)
                )
        if (
            self.rollback_target_bundle_id is not None
            and self.rollback_target_bundle_id == self.bundle_id
        ):
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_ROLLBACK_TARGET_INVALID"
            )
        object.__setattr__(self, "reason_code", _reason(self.reason_code))
        computed = _sha256(_canonical(self.to_dict(include_digest=False)))
        supplied = str(self.evidence_digest or "").strip()
        if supplied and supplied != computed:
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_EVIDENCE_DIGEST_DRIFT"
            )
        object.__setattr__(self, "evidence_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.skill-activation-evidence.v2",
            "activation_id": self.activation_id,
            "workspace_id": self.workspace_id,
            "bundle_id": self.bundle_id,
            "bundle_digest": self.bundle_digest,
            "bundle_version": self.bundle_version,
            "runtime_adapter_id": self.runtime_adapter_id,
            "runtime_protocol_version": self.runtime_protocol_version,
            "lab_report_digest": self.lab_report_digest,
            "review_id": self.review_id,
            "review_status_version": self.review_status_version,
            "sbom_digest": self.sbom_digest,
            "signature_audit_ref": self.signature_audit_ref,
            "sandbox_attestation_digest": self.sandbox_attestation_digest,
            "canary_receipt_digest": self.canary_receipt_digest,
            "maturity_decision_id": self.maturity_decision_id,
            "maturity_level": self.maturity_level,
            "previous_bundle_id": self.previous_bundle_id,
            "rollback_target_bundle_id": self.rollback_target_bundle_id,
            "new_capability": self.new_capability,
            "reason_code": self.reason_code,
        }
        if include_digest:
            result["evidence_digest"] = self.evidence_digest
        return result

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "SkillActivationEvidenceV2":
        return cls(
            activation_id=value.get("activation_id"),
            workspace_id=value.get("workspace_id"),
            bundle_id=value.get("bundle_id"),
            bundle_digest=value.get("bundle_digest"),
            bundle_version=value.get("bundle_version"),
            runtime_adapter_id=value.get("runtime_adapter_id"),
            runtime_protocol_version=value.get("runtime_protocol_version"),
            lab_report_digest=value.get("lab_report_digest"),
            review_id=value.get("review_id"),
            review_status_version=value.get("review_status_version"),
            sbom_digest=value.get("sbom_digest"),
            signature_audit_ref=value.get("signature_audit_ref"),
            sandbox_attestation_digest=value.get(
                "sandbox_attestation_digest"
            ),
            canary_receipt_digest=value.get("canary_receipt_digest"),
            maturity_decision_id=value.get("maturity_decision_id"),
            maturity_level=value.get("maturity_level"),
            previous_bundle_id=value.get("previous_bundle_id"),
            rollback_target_bundle_id=value.get(
                "rollback_target_bundle_id"
            ),
            new_capability=value.get("new_capability", True),
            reason_code=value.get(
                "reason_code", "SKILL_ACTIVATION_APPROVED"
            ),
            evidence_digest=value.get("evidence_digest", ""),
        )


@dataclass(frozen=True, slots=True)
class SkillLifecycleReceiptV2:
    """Atomic terminal receipt returned by the V2 lifecycle owner."""

    receipt_id: str
    operation_id: str
    event: str
    activation_id: str
    bundle_id: str
    evidence_digest: str
    maturity_decision_digest: str
    active_projection_digest: str | None
    previous_bundle_id: str | None
    created_at_ms: int
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for field in ("receipt_id", "operation_id", "activation_id"):
            object.__setattr__(
                self, field, _uri(getattr(self, field), field)
            )
        object.__setattr__(
            self, "bundle_id", _text(self.bundle_id, "bundle_id", maximum=255)
        )
        event = str(self.event or "").strip().upper()
        if event not in {
            "ACTIVATED",
            "DEGRADED",
            "ROLLED_BACK",
            "REJECTED",
        }:
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_EVENT_INVALID"
            )
        object.__setattr__(self, "event", event)
        for field in ("evidence_digest", "maturity_decision_digest"):
            object.__setattr__(
                self, field, _hex_digest(getattr(self, field), field)
            )
        if self.active_projection_digest is not None:
            object.__setattr__(
                self,
                "active_projection_digest",
                _hex_digest(
                    self.active_projection_digest,
                    "active_projection_digest",
                ),
            )
        if self.previous_bundle_id is not None:
            object.__setattr__(
                self,
                "previous_bundle_id",
                _text(
                    self.previous_bundle_id,
                    "previous_bundle_id",
                    maximum=255,
                ),
            )
        if (
            isinstance(self.created_at_ms, bool)
            or not isinstance(self.created_at_ms, int)
            or self.created_at_ms < 0
        ):
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_CREATED_AT_INVALID"
            )
        computed = _sha256(_canonical(self.to_dict(include_digest=False)))
        supplied = str(self.receipt_digest or "").strip()
        if supplied and supplied != computed:
            raise SkillLifecycleV2Error(
                "SKILL_LIFECYCLE_V2_RECEIPT_DIGEST_DRIFT"
            )
        object.__setattr__(self, "receipt_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.skill-lifecycle-receipt.v2",
            "receipt_id": self.receipt_id,
            "operation_id": self.operation_id,
            "event": self.event,
            "activation_id": self.activation_id,
            "bundle_id": self.bundle_id,
            "evidence_digest": self.evidence_digest,
            "maturity_decision_digest": self.maturity_decision_digest,
            "active_projection_digest": self.active_projection_digest,
            "previous_bundle_id": self.previous_bundle_id,
            "created_at_ms": self.created_at_ms,
        }
        if include_digest:
            result["receipt_digest"] = self.receipt_digest
        return result

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "SkillLifecycleReceiptV2":
        return cls(
            receipt_id=value.get("receipt_id"),
            operation_id=value.get("operation_id"),
            event=value.get("event"),
            activation_id=value.get("activation_id"),
            bundle_id=value.get("bundle_id"),
            evidence_digest=value.get("evidence_digest"),
            maturity_decision_digest=value.get(
                "maturity_decision_digest"
            ),
            active_projection_digest=value.get("active_projection_digest"),
            previous_bundle_id=value.get("previous_bundle_id"),
            created_at_ms=value.get("created_at_ms"),
            receipt_digest=value.get("receipt_digest", ""),
        )


__all__ = [
    "SkillActivationEvidenceV2",
    "SkillLifecycleReceiptV2",
    "SkillLifecycleV2Error",
]
