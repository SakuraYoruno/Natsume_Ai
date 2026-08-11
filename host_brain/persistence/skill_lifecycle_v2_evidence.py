"""Evidence and projection validation helpers for Skill lifecycle V2.

This module owns no connection or transaction.  The canonical SQLite
repository supplies the active transaction and inherits this validation
mixin, keeping one physical writer while separating evidence resolution from
operation orchestration.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

from host_brain.contracts.skill_lifecycle_v2 import (
    SkillActivationEvidenceV2,
    SkillLifecycleV2Error,
)


class SkillLifecycleV2RepositoryError(RuntimeError):
    """Stable repository failure with a machine-readable reason code."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(
            self.reason_code
            if not self.detail
            else f"{self.reason_code}:{self.detail}"
        )


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
        raise SkillLifecycleV2RepositoryError(
            "SKILL_LIFECYCLE_V2_JSON_INVALID"
        ) from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_ref(value: str) -> str:
    return f"sha256:{_sha256(value)}"


def _text(value: Any, field: str, maximum: int = 255) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise SkillLifecycleV2RepositoryError(
            f"SKILL_LIFECYCLE_V2_{field.upper()}_INVALID"
        )
    return normalized


def _hex_digest(value: Any, field: str) -> str:
    normalized = _text(value, field, 64).lower()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise SkillLifecycleV2RepositoryError(
            f"SKILL_LIFECYCLE_V2_{field.upper()}_INVALID"
        )
    return normalized


def activation_review_subject_ref(
    *,
    bundle_id: str,
    bundle_digest: str,
    bundle_version: str,
) -> str:
    """Return the exact ReviewItem subject expected by activation."""

    identity = _sha256(
        "|".join(
            (
                _text(bundle_id, "bundle_id"),
                _text(bundle_digest, "bundle_digest", 71),
                _text(bundle_version, "bundle_version", 128),
            )
        )
    )
    return f"skill-lifecycle-subject://sha256/{identity}"


class SkillLifecycleV2EvidenceMixin:
    """Resolve trusted activation evidence inside the repository transaction."""

    def _resolve_activation_authorities(
        self,
        conn: sqlite3.Connection,
        *,
        evidence: SkillActivationEvidenceV2,
        target: sqlite3.Row,
        now: int,
    ) -> None:
        if (
            str(target["state"]) != "STAGED"
            or str(target["bundle_digest"]) != evidence.bundle_digest
            or str(target["bundle_version"]) != evidence.bundle_version
            or int(target["signature_verified"]) != 1
            or str(target["sbom_digest"]) != evidence.sbom_digest
            or str(target["signature_verification_audit_ref"])
            != evidence.signature_audit_ref
        ):
            self._invalid(
                "SKILL_LIFECYCLE_V2_SUPPLY_CHAIN_EVIDENCE_DRIFT"
            )
        canary = conn.execute(
            """
            SELECT *
            FROM skill_lifecycle_canary_operation_v2
            WHERE result_digest = ?
            """,
            (evidence.canary_receipt_digest,),
        ).fetchone()
        if (
            canary is None
            or str(canary["status"]) != "PASSED"
            or str(canary["bundle_id"]) != evidence.bundle_id
            or str(canary["bundle_digest"]) != evidence.bundle_digest
            or str(canary["workspace_id"]) != evidence.workspace_id
            or str(canary["runtime_adapter_id"])
            != evidence.runtime_adapter_id
            or str(canary["runtime_protocol_version"])
            != evidence.runtime_protocol_version
            or str(canary["sandbox_attestation_digest"])
            != evidence.sandbox_attestation_digest
        ):
            self._invalid(
                "SKILL_LIFECYCLE_V2_CANARY_AUTHORITY_REJECTED"
            )
        report = conn.execute(
            """
            SELECT *
            FROM evolution_lab_trusted_report
            WHERE report_digest = ?
            """,
            (evidence.lab_report_digest,),
        ).fetchone()
        if (
            report is None
            or str(report["workspace_id"]) != evidence.workspace_id
            or int(report["promotion_eligible"]) != 1
            or int(report["review_required"]) != 1
            or int(report["production_influence_enabled"]) != 0
            or str(report["experiment_kind"]).strip().upper()
            not in {
                "SKILL_VALIDATION",
                "SKILL_LIFECYCLE",
                "SKILL_COMPILER",
            }
        ):
            self._invalid("SKILL_LIFECYCLE_V2_LAB_REPORT_REJECTED")
        try:
            report_payload = json.loads(str(report["report_json"]))
        except json.JSONDecodeError as exc:
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_LAB_REPORT_REJECTED"
            ) from exc
        matching_runs = [
            run
            for run in list(
                report_payload.get("runs")
                if isinstance(report_payload, Mapping)
                else []
            )
            if isinstance(run, Mapping)
            and str(run.get("status")) == "PASS"
            and str(run.get("sandbox_attestation_digest"))
            == evidence.sandbox_attestation_digest
        ]
        if (
            not isinstance(report_payload, Mapping)
            or _sha256(_canonical(report_payload))
            != evidence.lab_report_digest
            or len(matching_runs) != 1
        ):
            self._invalid("SKILL_LIFECYCLE_V2_LAB_REPORT_REJECTED")
        matched_run = matching_runs[0]
        run_row = conn.execute(
            """
            SELECT *
            FROM evolution_lab_trusted_run_receipt
            WHERE run_id = ?
            """,
            (str(matched_run.get("run_id") or ""),),
        ).fetchone()
        if (
            run_row is None
            or str(run_row["workspace_id"]) != evidence.workspace_id
            or str(run_row["receipt_digest"])
            != _sha256(_canonical(matched_run))
            or str(run_row["sandbox_attestation_digest"])
            != evidence.sandbox_attestation_digest
            or str(run_row["receipt_json"]) != _canonical(matched_run)
            or int(run_row["production_side_effects"]) != 0
            or int(run_row["benefit_eligible"]) != 1
            or int(run_row["production_influence_enabled"]) != 0
        ):
            self._invalid("SKILL_LIFECYCLE_V2_LAB_RUN_REJECTED")
        target_kind = (
            "skill-bundle-sha256:" f"{_sha256(evidence.bundle_id)}"
        )
        proposal = conn.execute(
            """
            SELECT *
            FROM evolution_lab_trusted_proposal
            WHERE report_digest = ? AND target_kind = ?
              AND target_version = ?
              AND review_required = 1
              AND production_influence_enabled = 0
            ORDER BY created_at_ms DESC, proposal_id DESC
            LIMIT 1
            """,
            (
                evidence.lab_report_digest,
                target_kind,
                evidence.bundle_version,
            ),
        ).fetchone()
        if proposal is None:
            self._invalid(
                "SKILL_LIFECYCLE_V2_LAB_PROPOSAL_REJECTED"
            )
        review = conn.execute(
            """
            SELECT *
            FROM agent_runtime_review
            WHERE review_id = ?
            """,
            (evidence.review_id,),
        ).fetchone()
        subject_ref = activation_review_subject_ref(
            bundle_id=evidence.bundle_id,
            bundle_digest=evidence.bundle_digest,
            bundle_version=evidence.bundle_version,
        )
        required_review_refs = {
            (
                "evolution-lab-report://sha256/"
                f"{evidence.lab_report_digest}"
            ),
            (
                "evolution-proposal://sha256/"
                f"{str(proposal['proposal_digest'])}"
            ),
            (
                "skill-canary://sha256/"
                f"{evidence.canary_receipt_digest}"
            ),
        }
        try:
            review_refs = set(
                json.loads(str(review["evidence_refs_json"]))
                if review is not None
                else []
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_REVIEW_REJECTED"
            ) from exc
        if (
            review is None
            or str(review["status"]) not in {"APPROVED", "CONSUMED"}
            or int(review["status_version"])
            != evidence.review_status_version
            or str(review["review_type"]) != "PRE_EXECUTION_APPROVAL"
            or str(review["subject_ref"]) != subject_ref
            or int(review["subject_version"])
            != int(target["status_version"])
            or int(review["expires_at"]) < now
            or not str(review["decision_actor"] or "").strip()
            or not required_review_refs.issubset(review_refs)
        ):
            self._invalid("SKILL_LIFECYCLE_V2_REVIEW_REJECTED")
        reused = conn.execute(
            """
            SELECT activation_id
            FROM skill_activation_evidence_v2
            WHERE review_id = ? AND activation_id <> ?
            LIMIT 1
            """,
            (evidence.review_id, evidence.activation_id),
        ).fetchone()
        if reused is not None:
            self._invalid("SKILL_LIFECYCLE_V2_REVIEW_ALREADY_USED")

    def _assert_projection_row(self, row: sqlite3.Row) -> None:
        if (
            str(row["state"]) != "ACTIVE"
            or str(row["supply_digest"]) != str(row["bundle_digest"])
            or str(row["supply_version"]) != str(row["bundle_version"])
            or int(row["supply_generation"])
            != int(row["activation_generation"])
        ):
            self._invalid(
                "SKILL_LIFECYCLE_V2_ACTIVE_PROJECTION_DRIFT"
            )
        projection = {
            "bundle_kind": str(row["bundle_kind"]),
            "bundle_name": str(row["bundle_name"]),
            "bundle_id": str(row["bundle_id"]),
            "bundle_digest": str(row["bundle_digest"]),
            "bundle_version": str(row["bundle_version"]),
            "runtime_adapter_id": str(row["runtime_adapter_id"]),
            "runtime_protocol_version": str(
                row["runtime_protocol_version"]
            ),
            "activation_id": str(row["activation_id"]),
            "maturity_decision_id": str(row["maturity_decision_id"]),
            "maturity_level": int(row["maturity_level"]),
            "activation_generation": int(row["activation_generation"]),
        }
        if _sha256(_canonical(projection)) != str(
            row["projection_digest"]
        ):
            self._invalid(
                "SKILL_LIFECYCLE_V2_ACTIVE_PROJECTION_DRIFT"
            )

    @staticmethod
    def _maturity_direction(
        *,
        prior_level: int | None,
        requested: int,
        new_capability: bool,
    ) -> str:
        if prior_level is None:
            return "NEW" if new_capability else "MAINTAIN"
        if requested > prior_level:
            return "UPGRADE"
        if requested < prior_level:
            return "DOWNGRADE"
        return "MAINTAIN"

    def _evidence_by_activation(
        self, conn: sqlite3.Connection, activation_id: str
    ) -> SkillActivationEvidenceV2:
        row = conn.execute(
            """
            SELECT evidence_json
            FROM skill_activation_evidence_v2
            WHERE activation_id = ?
            """,
            (activation_id,),
        ).fetchone()
        if row is None:
            self._invalid("SKILL_LIFECYCLE_V2_EVIDENCE_MISSING")
        try:
            return SkillActivationEvidenceV2.from_mapping(
                json.loads(str(row["evidence_json"]))
            )
        except (json.JSONDecodeError, SkillLifecycleV2Error) as exc:
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_EVIDENCE_DRIFT"
            ) from exc

    def _decision_by_activation(
        self, conn: sqlite3.Connection, activation_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT *
            FROM skill_capability_maturity_decision_v1
            WHERE activation_id = ?
            """,
            (activation_id,),
        ).fetchone()
        if row is None:
            self._invalid(
                "SKILL_LIFECYCLE_V2_MATURITY_DECISION_MISSING"
            )
        return row

    def _require_bundle(
        self, conn: sqlite3.Connection, bundle_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT *
            FROM skill_supply_chain_bundle
            WHERE bundle_id = ?
            """,
            (bundle_id,),
        ).fetchone()
        if row is None:
            self._invalid("SKILL_LIFECYCLE_V2_BUNDLE_NOT_FOUND")
        return row

    def _require_canary(
        self, conn: sqlite3.Connection, operation_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT *
            FROM skill_lifecycle_canary_operation_v2
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if row is None:
            self._invalid("SKILL_LIFECYCLE_V2_CANARY_NOT_FOUND")
        return row

    @staticmethod
    def _canary_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "operation_id": str(row["operation_id"]),
            "workspace_id": str(row["workspace_id"]),
            "bundle_id": str(row["bundle_id"]),
            "bundle_digest": str(row["bundle_digest"]),
            "runtime_adapter_id": str(row["runtime_adapter_id"]),
            "runtime_protocol_version": str(
                row["runtime_protocol_version"]
            ),
            "sandbox_attestation_digest": str(
                row["sandbox_attestation_digest"]
            ),
            "outbox_id": str(row["outbox_id"]),
            "status": str(row["status"]),
            "downstream_receipt_digest": (
                None
                if row["downstream_receipt_digest"] is None
                else str(row["downstream_receipt_digest"])
            ),
            "canary_receipt_digest": (
                None
                if row["result_digest"] is None
                else str(row["result_digest"])
            ),
            "reason_code": str(row["reason_code"]),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    @staticmethod
    def _invalid(
        reason_code: str,
        detail: str = "",
        *,
        cause: BaseException | None = None,
    ) -> None:
        error = SkillLifecycleV2RepositoryError(reason_code, detail)
        if cause is None:
            raise error
        raise error from cause


__all__ = [
    "SkillLifecycleV2EvidenceMixin",
    "SkillLifecycleV2RepositoryError",
    "_canonical",
    "_hex_digest",
    "_sha256",
    "_sha256_ref",
    "_text",
    "activation_review_subject_ref",
]
