"""Authority resolution helpers for NE-06 version activation.

This module owns no connection or transaction.  The V2 repository supplies
the active SQLite transaction so evidence checks and projection changes share
one physical writer boundary.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping


class EvolutionActivationV2Error(RuntimeError):
    """Stable fail-closed error for the NE-06 authority."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(
            self.reason_code
            if not self.detail
            else f"{self.reason_code}:{self.detail}"
        )


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvolutionActivationV2Error(
            "EVOLUTION_ACTIVATION_V2_JSON_INVALID"
        ) from exc


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def payload_digest(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def bounded_text(value: Any, field: str, maximum: int = 255) -> str:
    result = str(value or "").strip()
    if (
        not result
        or len(result) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in result)
    ):
        raise EvolutionActivationV2Error(
            f"EVOLUTION_ACTIVATION_V2_{field.upper()}_INVALID"
        )
    return result


def hex_digest(value: Any, field: str) -> str:
    result = bounded_text(value, field, 71).lower()
    if result.startswith("sha256:"):
        result = result[7:]
    if len(result) != 64 or any(
        char not in "0123456789abcdef" for char in result
    ):
        raise EvolutionActivationV2Error(
            f"EVOLUTION_ACTIVATION_V2_{field.upper()}_INVALID"
        )
    return result


def feedback_subject_ref(
    *,
    workspace_id: str,
    subject_id: str,
    policy_kind: str,
    proposal_digest: str,
) -> str:
    identity = "|".join(
        (
            bounded_text(workspace_id, "workspace_id", 128),
            bounded_text(subject_id, "subject_id", 255),
            bounded_text(policy_kind, "policy_kind", 64),
            hex_digest(proposal_digest, "proposal_digest"),
        )
    )
    return f"evolution-feedback://sha256/{sha256_hex(identity)}"


def knowledge_subject_ref(*, entry_id: str, entry_digest: str) -> str:
    identity = "|".join(
        (
            bounded_text(entry_id, "entry_id", 255),
            hex_digest(entry_digest, "entry_digest"),
        )
    )
    return f"evolution-knowledge://sha256/{sha256_hex(identity)}"


def feedback_target_kind(
    *, workspace_id: str, subject_id: str, policy_kind: str
) -> str:
    identity = "|".join(
        (
            bounded_text(workspace_id, "workspace_id", 128),
            bounded_text(subject_id, "subject_id", 255),
            bounded_text(policy_kind, "policy_kind", 64),
        )
    )
    return f"feedback-policy-sha256:{sha256_hex(identity)}"


def knowledge_target_kind(*, entry_id: str) -> str:
    return "knowledge-entry-sha256:" + sha256_hex(
        bounded_text(entry_id, "entry_id", 255)
    )


def identity_target_kind(*, migration_id: str) -> str:
    return "identity-migration-sha256:" + sha256_hex(
        bounded_text(migration_id, "migration_id", 255)
    )


class EvolutionActivationEvidenceMixin:
    """Reopen Lab, proposal and Review authorities in the active UoW."""

    def _resolve_lab_review(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        target_kind: str,
        target_version: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        review_subject_ref: str,
        review_subject_version: int,
        required_evidence_refs: set[str],
        now_ms: int,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        report_digest = hex_digest(
            lab_report_digest, "lab_report_digest"
        )
        report = conn.execute(
            """
            SELECT *
            FROM evolution_lab_trusted_report
            WHERE report_digest = ?
            """,
            (report_digest,),
        ).fetchone()
        if (
            report is None
            or str(report["workspace_id"]) != workspace_id
            or int(report["promotion_eligible"]) != 1
            or int(report["review_required"]) != 1
            or int(report["production_influence_enabled"]) != 0
        ):
            self._invalid("EVOLUTION_ACTIVATION_V2_LAB_REPORT_REJECTED")
        try:
            report_payload = json.loads(str(report["report_json"]))
        except json.JSONDecodeError as exc:
            raise EvolutionActivationV2Error(
                "EVOLUTION_ACTIVATION_V2_LAB_REPORT_REJECTED"
            ) from exc
        if (
            not isinstance(report_payload, Mapping)
            or payload_digest(report_payload) != report_digest
        ):
            self._invalid("EVOLUTION_ACTIVATION_V2_LAB_REPORT_REJECTED")
        runs = report_payload.get("runs")
        if not isinstance(runs, list) or len(runs) < 2:
            self._invalid("EVOLUTION_ACTIVATION_V2_LAB_RUN_REJECTED")
        seen_runs: set[str] = set()
        for item in runs:
            if not isinstance(item, Mapping):
                self._invalid("EVOLUTION_ACTIVATION_V2_LAB_RUN_REJECTED")
            run_id = str(item.get("run_id") or "").strip()
            if not run_id or run_id in seen_runs:
                self._invalid("EVOLUTION_ACTIVATION_V2_LAB_RUN_REJECTED")
            seen_runs.add(run_id)
            row = conn.execute(
                """
                SELECT *
                FROM evolution_lab_trusted_run_receipt
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if (
                row is None
                or str(row["workspace_id"]) != workspace_id
                or str(row["receipt_digest"]) != payload_digest(item)
                or str(row["receipt_json"]) != canonical_json(item)
                or int(row["production_side_effects"]) != 0
                or int(row["benefit_eligible"]) != 1
                or int(row["production_influence_enabled"]) != 0
            ):
                self._invalid("EVOLUTION_ACTIVATION_V2_LAB_RUN_REJECTED")

        proposal = conn.execute(
            """
            SELECT *
            FROM evolution_lab_trusted_proposal
            WHERE report_digest = ?
              AND target_kind = ?
              AND target_version = ?
              AND review_required = 1
              AND production_influence_enabled = 0
            ORDER BY created_at_ms DESC, proposal_id DESC
            LIMIT 1
            """,
            (
                report_digest,
                bounded_text(target_kind, "target_kind", 160),
                bounded_text(target_version, "target_version", 160),
            ),
        ).fetchone()
        if proposal is None:
            self._invalid("EVOLUTION_ACTIVATION_V2_LAB_PROPOSAL_REJECTED")

        review = conn.execute(
            """
            SELECT *
            FROM agent_runtime_review
            WHERE review_id = ?
            """,
            (bounded_text(review_id, "review_id", 255),),
        ).fetchone()
        try:
            review_refs = set(
                json.loads(str(review["evidence_refs_json"]))
                if review is not None
                else []
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise EvolutionActivationV2Error(
                "EVOLUTION_ACTIVATION_V2_REVIEW_REJECTED"
            ) from exc
        required = {
            (
                "evolution-lab-report://sha256/"
                f"{report_digest}"
            ),
            (
                "evolution-proposal://sha256/"
                f"{str(proposal['proposal_digest'])}"
            ),
            *required_evidence_refs,
        }
        if (
            review is None
            or str(review["status"]) not in {"APPROVED", "CONSUMED"}
            or int(review["status_version"]) != int(review_status_version)
            or str(review["review_type"]) != "PRE_EXECUTION_APPROVAL"
            or str(review["subject_ref"]) != review_subject_ref
            or int(review["subject_version"]) != int(review_subject_version)
            or int(review["expires_at"]) < int(now_ms)
            or not str(review["decision_actor"] or "").strip()
            or not required.issubset(review_refs)
        ):
            self._invalid("EVOLUTION_ACTIVATION_V2_REVIEW_REJECTED")
        review_ref = f"review://{review_id}"
        reused = conn.execute(
            """
            SELECT decision_id
            FROM evolution_version_decision_v1 AS decision,
                 json_each(decision.authority_refs_json) AS authority
            WHERE authority.value = ?
            LIMIT 1
            """,
            (review_ref,),
        ).fetchone()
        if reused is not None:
            self._invalid("EVOLUTION_ACTIVATION_V2_REVIEW_ALREADY_USED")
        return report, proposal, review

    @staticmethod
    def _invalid(reason_code: str, detail: str = "") -> None:
        raise EvolutionActivationV2Error(reason_code, detail)


__all__ = [
    "EvolutionActivationEvidenceMixin",
    "EvolutionActivationV2Error",
    "bounded_text",
    "canonical_json",
    "feedback_subject_ref",
    "feedback_target_kind",
    "hex_digest",
    "identity_target_kind",
    "knowledge_subject_ref",
    "knowledge_target_kind",
    "payload_digest",
    "sha256_hex",
]
