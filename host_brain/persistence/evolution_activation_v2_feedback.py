"""Feedback policy version transitions for NE-06."""

from __future__ import annotations

import json
from typing import Any, Mapping

from host_brain.contracts.evolution import (
    EvolutionTransitionReceiptV1,
    FeedbackProposalV1,
    VersionActivationDecisionV1,
)

from .evolution_activation_v2_evidence import (
    bounded_text,
    canonical_json,
    feedback_subject_ref,
    feedback_target_kind,
    payload_digest,
    sha256_hex,
)


class EvolutionActivationFeedbackMixin:
    """Own Feedback staging, apply and exact rollback."""

    def register_policy_version(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        policy_kind: str,
        version: str,
        payload: Mapping[str, Any],
        provenance_ref: str,
    ) -> str:
        workspace = bounded_text(workspace_id, "workspace_id", 128)
        subject = bounded_text(subject_id, "subject_id", 255)
        kind = bounded_text(policy_kind, "policy_kind", 64).upper()
        target_version = bounded_text(version, "version", 128)
        if not isinstance(payload, Mapping) or not payload:
            self._invalid("EVOLUTION_POLICY_VERSION_PAYLOAD_INVALID")
        encoded = canonical_json(dict(payload))
        digest = sha256_hex(encoded)
        provenance = bounded_text(
            provenance_ref, "provenance_ref", 1_024
        )
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT *
                FROM evolution_policy_version_v2
                WHERE workspace_id = ? AND subject_id = ?
                  AND policy_kind = ? AND version = ?
                """,
                (workspace, subject, kind, target_version),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["version_digest"]) != digest
                    or str(existing["payload_json"]) != encoded
                ):
                    self._invalid("EVOLUTION_POLICY_VERSION_REPLAY_DRIFT")
                # The version name is content-addressed by ``payload``.  Two
                # independently observed feedback signals may therefore
                # converge on the same immutable version after an interrupted
                # stage.  Keep the first-seen provenance on the version row;
                # every signal still owns its separate proposal/trace record.
                return digest
            conn.execute(
                """
                INSERT INTO evolution_policy_version_v2(
                    workspace_id, subject_id, policy_kind, version,
                    version_digest, payload_json, provenance_ref, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace,
                    subject,
                    kind,
                    target_version,
                    digest,
                    encoded,
                    provenance,
                    int(self._clock()),
                ),
            )
        return digest

    def backfill_policy_projection(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        policy_kind: str,
        version: str,
        migration_ref: str,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        request = {
            "operation": "BACKFILL_POLICY",
            "workspace_id": workspace_id,
            "subject_id": subject_id,
            "policy_kind": policy_kind,
            "version": version,
            "migration_ref": migration_ref,
        }
        with self._transaction() as conn:
            replay = self._replay(conn, idempotency_key, request)
            if replay is not None:
                return replay
            row = self._policy_version(
                conn,
                workspace_id=workspace_id,
                subject_id=subject_id,
                policy_kind=policy_kind,
                version=version,
            )
            existing = self._active_policy(
                conn,
                workspace_id=workspace_id,
                subject_id=subject_id,
                policy_kind=policy_kind,
            )
            if existing is not None:
                self._invalid("EVOLUTION_POLICY_BACKFILL_ALREADY_EXISTS")
            operation_id, decision_id, receipt_id = self._identities(
                idempotency_key
            )
            projection, projection_digest = self._policy_projection(
                row=row,
                generation=1,
            )
            conn.execute(
                """
                INSERT INTO evolution_policy_active_projection_v2(
                    workspace_id, subject_id, policy_kind, active_version,
                    active_digest, payload_json, activation_generation,
                    projection_digest, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["workspace_id"],
                    row["subject_id"],
                    row["policy_kind"],
                    row["version"],
                    row["version_digest"],
                    row["payload_json"],
                    1,
                    projection_digest,
                    int(self._clock()),
                ),
            )
            decision = VersionActivationDecisionV1(
                decision_id=decision_id,
                operation_id=operation_id,
                workspace_id=str(row["workspace_id"]),
                subject_kind="FEEDBACK_POLICY",
                subject_id=str(row["subject_id"]),
                action="APPLY",
                target_version=str(row["version"]),
                target_digest=str(row["version_digest"]),
                authority_refs=(
                    bounded_text(migration_ref, "migration_ref", 1_024),
                ),
                expected_generation=0,
                reason_code="EVOLUTION_POLICY_BACKFILL",
            )
            return self._append_terminal(
                conn,
                decision=decision,
                request=request,
                idempotency_key=idempotency_key,
                receipt_id=receipt_id,
                event="APPLIED",
                generation=1,
                projection_digest=projection_digest,
            )

    def register_feedback_proposal(
        self,
        proposal: FeedbackProposalV1,
        *,
        workspace_id: str,
        subject_id: str,
        policy_kind: str,
    ) -> str:
        if not isinstance(proposal, FeedbackProposalV1):
            self._invalid("EVOLUTION_FEEDBACK_PROPOSAL_V2_INVALID")
        workspace = bounded_text(workspace_id, "workspace_id", 128)
        subject = bounded_text(subject_id, "subject_id", 255)
        kind = bounded_text(policy_kind, "policy_kind", 64).upper()
        encoded = canonical_json(proposal.to_dict())
        with self._transaction() as conn:
            target = self._policy_version(
                conn,
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=kind,
                version=proposal.target_version,
            )
            if str(target["version_digest"]) != payload_digest(
                dict(proposal.change)
            ):
                self._invalid(
                    "EVOLUTION_FEEDBACK_TARGET_VERSION_DRIFT"
                )
            rollback_digest = self._policy_digest_from_ref(
                proposal.rollback_ref
            )
            rollback = conn.execute(
                """
                SELECT *
                FROM evolution_policy_version_v2
                WHERE workspace_id = ? AND subject_id = ?
                  AND policy_kind = ? AND version_digest = ?
                """,
                (workspace, subject, kind, rollback_digest),
            ).fetchone()
            if rollback is None:
                self._invalid(
                    "EVOLUTION_FEEDBACK_ROLLBACK_VERSION_NOT_FOUND"
                )
            existing = conn.execute(
                """
                SELECT *
                FROM evolution_feedback_proposal_v2
                WHERE proposal_id = ?
                """,
                (proposal.proposal_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["proposal_digest"])
                    != proposal.proposal_digest
                    or str(existing["proposal_json"]) != encoded
                    or str(existing["workspace_id"]) != workspace
                    or str(existing["subject_id"]) != subject
                    or str(existing["policy_kind"]) != kind
                ):
                    self._invalid(
                        "EVOLUTION_FEEDBACK_PROPOSAL_V2_REPLAY_DRIFT"
                    )
                return proposal.proposal_digest
            conn.execute(
                """
                INSERT INTO evolution_feedback_proposal_v2(
                    proposal_id, proposal_digest, workspace_id, subject_id,
                    policy_kind, target_version, target_digest,
                    rollback_version, rollback_digest, trace_digest,
                    outcome_digest, proposal_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.proposal_digest,
                    workspace,
                    subject,
                    kind,
                    proposal.target_version,
                    target["version_digest"],
                    rollback["version"],
                    rollback["version_digest"],
                    proposal.trace_digest,
                    proposal.outcome_digest,
                    encoded,
                    int(self._clock()),
                ),
            )
        return proposal.proposal_digest

    def apply_feedback(
        self,
        *,
        proposal_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        return self._transition_feedback(
            proposal_id=proposal_id,
            action="APPLY",
            event="APPLIED",
            lab_report_digest=lab_report_digest,
            review_id=review_id,
            review_status_version=review_status_version,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )

    def rollback_feedback(
        self,
        *,
        proposal_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        return self._transition_feedback(
            proposal_id=proposal_id,
            action="ROLLBACK",
            event="ROLLED_BACK",
            lab_report_digest=lab_report_digest,
            review_id=review_id,
            review_status_version=review_status_version,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )

    def _transition_feedback(
        self,
        *,
        proposal_id: str,
        action: str,
        event: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        request = {
            "operation": f"FEEDBACK_{action}",
            "proposal_id": proposal_id,
            "lab_report_digest": lab_report_digest,
            "review_id": review_id,
            "review_status_version": review_status_version,
            "expected_generation": expected_generation,
        }
        with self._transaction() as conn:
            replay = self._replay(conn, idempotency_key, request)
            if replay is not None:
                return replay
            proposal = conn.execute(
                """
                SELECT *
                FROM evolution_feedback_proposal_v2
                WHERE proposal_id = ?
                """,
                (bounded_text(proposal_id, "proposal_id", 255),),
            ).fetchone()
            if proposal is None:
                self._invalid(
                    "EVOLUTION_FEEDBACK_PROPOSAL_V2_NOT_FOUND"
                )
            active = self._active_policy(
                conn,
                workspace_id=str(proposal["workspace_id"]),
                subject_id=str(proposal["subject_id"]),
                policy_kind=str(proposal["policy_kind"]),
            )
            generation = self._generation(
                active, expected_generation=expected_generation
            )
            if action == "APPLY":
                if (
                    active is None
                    or str(active["active_version"])
                    != str(proposal["rollback_version"])
                    or str(active["active_digest"])
                    != str(proposal["rollback_digest"])
                ):
                    self._invalid(
                        "EVOLUTION_FEEDBACK_PREVIOUS_VERSION_DRIFT"
                    )
                target_version = str(proposal["target_version"])
                target_digest = str(proposal["target_digest"])
            else:
                if (
                    active is None
                    or str(active["active_version"])
                    != str(proposal["target_version"])
                    or str(active["active_digest"])
                    != str(proposal["target_digest"])
                ):
                    self._invalid(
                        "EVOLUTION_FEEDBACK_ROLLBACK_SOURCE_DRIFT"
                    )
                target_version = str(proposal["rollback_version"])
                target_digest = str(proposal["rollback_digest"])
            target = self._policy_version(
                conn,
                workspace_id=str(proposal["workspace_id"]),
                subject_id=str(proposal["subject_id"]),
                policy_kind=str(proposal["policy_kind"]),
                version=target_version,
            )
            subject_ref = feedback_subject_ref(
                workspace_id=str(proposal["workspace_id"]),
                subject_id=str(proposal["subject_id"]),
                policy_kind=str(proposal["policy_kind"]),
                proposal_digest=str(proposal["proposal_digest"]),
            )
            report, lab_proposal, _review = self._resolve_lab_review(
                conn,
                workspace_id=str(proposal["workspace_id"]),
                target_kind=feedback_target_kind(
                    workspace_id=str(proposal["workspace_id"]),
                    subject_id=str(proposal["subject_id"]),
                    policy_kind=str(proposal["policy_kind"]),
                ),
                target_version=target_version,
                lab_report_digest=lab_report_digest,
                review_id=review_id,
                review_status_version=review_status_version,
                review_subject_ref=subject_ref,
                review_subject_version=generation,
                required_evidence_refs={
                    (
                        "evolution-feedback-proposal://sha256/"
                        f"{str(proposal['proposal_digest'])}"
                    ),
                    (
                        "evolution-policy-version://sha256/"
                        f"{target_digest}"
                    ),
                },
                now_ms=int(self._clock()),
            )
            operation_id, decision_id, receipt_id = self._identities(
                idempotency_key
            )
            projection, projection_digest = self._policy_projection(
                row=target,
                generation=generation,
            )
            conn.execute(
                """
                UPDATE evolution_policy_active_projection_v2
                SET active_version = ?, active_digest = ?, payload_json = ?,
                    activation_generation = ?, projection_digest = ?,
                    updated_at_ms = ?
                WHERE workspace_id = ? AND subject_id = ? AND policy_kind = ?
                  AND activation_generation = ?
                """,
                (
                    target_version,
                    target_digest,
                    target["payload_json"],
                    generation,
                    projection_digest,
                    int(self._clock()),
                    proposal["workspace_id"],
                    proposal["subject_id"],
                    proposal["policy_kind"],
                    generation - 1,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                self._invalid(
                    "EVOLUTION_FEEDBACK_ACTIVE_PROJECTION_CAS_FAILED"
                )
            decision = VersionActivationDecisionV1(
                decision_id=decision_id,
                operation_id=operation_id,
                workspace_id=str(proposal["workspace_id"]),
                subject_kind="FEEDBACK_POLICY",
                subject_id=str(proposal["subject_id"]),
                action=action,
                target_version=target_version,
                target_digest=target_digest,
                previous_version=str(active["active_version"]),
                previous_digest=str(active["active_digest"]),
                authority_refs=(
                    f"evolution-lab-report://sha256/{report['report_digest']}",
                    f"evolution-proposal://sha256/{lab_proposal['proposal_digest']}",
                    f"review://{review_id}",
                    f"evolution-feedback-proposal://sha256/{proposal['proposal_digest']}",
                    f"evolution-policy-version://sha256/{target_digest}",
                ),
                expected_generation=generation - 1,
            )
            return self._append_terminal(
                conn,
                decision=decision,
                request=request,
                idempotency_key=idempotency_key,
                receipt_id=receipt_id,
                event=event,
                generation=generation,
                projection_digest=projection_digest,
            )

__all__ = ["EvolutionActivationFeedbackMixin"]
