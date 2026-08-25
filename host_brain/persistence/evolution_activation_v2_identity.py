"""Identity release snapshot activation and rollback for NE-06."""

from __future__ import annotations

import json
from typing import Any, Mapping

from host_brain.contracts.evolution import (
    EvolutionTransitionReceiptV1,
    IdentityMigrationPlanV2,
    VersionActivationDecisionV1,
)
from host_brain.release.identity_snapshot import (
    IdentitySnapshot,
    diff_identity_snapshots,
)

from .evolution_activation_v2_evidence import (
    bounded_text,
    canonical_json,
    hex_digest,
    identity_target_kind,
    payload_digest,
)


class EvolutionActivationIdentityMixin:
    """Own structured identity snapshots and exact prior rollback."""

    def register_identity_snapshot(
        self,
        snapshot: IdentitySnapshot | Mapping[str, Any],
        *,
        workspace_id: str,
    ) -> str:
        item = (
            snapshot
            if isinstance(snapshot, IdentitySnapshot)
            else IdentitySnapshot.from_mapping(snapshot)
        )
        item.validate()
        workspace = bounded_text(workspace_id, "workspace_id", 128)
        encoded = canonical_json(item.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT *
                FROM evolution_identity_snapshot_v2
                WHERE snapshot_ref = ?
                """,
                (item.snapshot_ref,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["workspace_id"]) != workspace
                    or str(existing["snapshot_json"]) != encoded
                    or str(existing["snapshot_hash"]) != item.snapshot_hash
                ):
                    self._invalid(
                        "EVOLUTION_IDENTITY_SNAPSHOT_V2_REPLAY_DRIFT"
                    )
                return item.snapshot_hash
            conn.execute(
                """
                INSERT INTO evolution_identity_snapshot_v2(
                    snapshot_ref, workspace_id, release_ref,
                    previous_release_ref, identity_digest, snapshot_hash,
                    snapshot_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.snapshot_ref,
                    workspace,
                    item.release_ref,
                    item.previous_release_ref,
                    item.identity_digest,
                    item.snapshot_hash,
                    encoded,
                    int(self._clock()),
                ),
            )
        return item.snapshot_hash

    def register_identity_regression(
        self,
        *,
        report_id: str,
        workspace_id: str,
        previous_snapshot_ref: str,
        proposed_snapshot_ref: str,
        checks: Mapping[str, bool],
    ) -> dict[str, Any]:
        workspace = bounded_text(workspace_id, "workspace_id", 128)
        if not isinstance(checks, Mapping) or not checks:
            self._invalid("EVOLUTION_IDENTITY_REGRESSION_CHECKS_INVALID")
        normalized_checks = {
            bounded_text(name, "check_name", 128): bool(value)
            for name, value in sorted(checks.items())
        }
        with self._transaction() as conn:
            previous = self._identity_snapshot(
                conn, previous_snapshot_ref, workspace
            )
            proposed = self._identity_snapshot(
                conn, proposed_snapshot_ref, workspace
            )
            previous_item = IdentitySnapshot.from_mapping(
                json.loads(str(previous["snapshot_json"]))
            )
            proposed_item = IdentitySnapshot.from_mapping(
                json.loads(str(proposed["snapshot_json"]))
            )
            diff = diff_identity_snapshots(previous_item, proposed_item)
            payload = {
                "schema": "natsume.identity-regression-report.v2",
                "report_id": bounded_text(
                    report_id, "report_id", 255
                ),
                "workspace_id": workspace,
                "previous_snapshot_ref": previous_item.snapshot_ref,
                "proposed_snapshot_ref": proposed_item.snapshot_ref,
                "diff_ref": diff.diff_ref,
                "diff_digest": hex_digest(diff.diff_hash, "diff_digest"),
                "severity": diff.severity.value,
                "checks": normalized_checks,
                "passed": all(normalized_checks.values()),
            }
            digest = payload_digest(payload)
            existing = conn.execute(
                """
                SELECT report_json
                FROM evolution_identity_regression_v2
                WHERE report_id = ?
                """,
                (payload["report_id"],),
            ).fetchone()
            encoded = canonical_json(payload)
            if existing is not None:
                if str(existing["report_json"]) != encoded:
                    self._invalid(
                        "EVOLUTION_IDENTITY_REGRESSION_REPLAY_DRIFT"
                    )
                return {**payload, "report_digest": digest}
            conn.execute(
                """
                INSERT INTO evolution_identity_regression_v2(
                    report_id, report_digest, workspace_id,
                    previous_snapshot_ref, proposed_snapshot_ref,
                    diff_digest, severity, passed, report_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["report_id"],
                    digest,
                    workspace,
                    previous_item.snapshot_ref,
                    proposed_item.snapshot_ref,
                    payload["diff_digest"],
                    payload["severity"],
                    int(payload["passed"]),
                    encoded,
                    int(self._clock()),
                ),
            )
        return {**payload, "report_digest": digest}

    def register_identity_plan(
        self, plan: IdentityMigrationPlanV2
    ) -> str:
        if not isinstance(plan, IdentityMigrationPlanV2):
            self._invalid("EVOLUTION_IDENTITY_PLAN_V2_INVALID")
        encoded = canonical_json(plan.to_dict())
        with self._transaction() as conn:
            previous = self._identity_snapshot(
                conn, plan.previous_snapshot_ref, plan.workspace_id
            )
            proposed = self._identity_snapshot(
                conn, plan.proposed_snapshot_ref, plan.workspace_id
            )
            regression = conn.execute(
                """
                SELECT *
                FROM evolution_identity_regression_v2
                WHERE report_digest = ?
                """,
                (plan.regression_report_digest,),
            ).fetchone()
            if (
                hex_digest(
                    previous["identity_digest"],
                    "previous_snapshot_digest",
                )
                != plan.previous_snapshot_digest
                or hex_digest(
                    proposed["identity_digest"],
                    "proposed_snapshot_digest",
                )
                != plan.proposed_snapshot_digest
                or regression is None
                or str(regression["workspace_id"]) != plan.workspace_id
                or str(regression["previous_snapshot_ref"])
                != plan.previous_snapshot_ref
                or str(regression["proposed_snapshot_ref"])
                != plan.proposed_snapshot_ref
                or str(regression["diff_digest"]) != plan.diff_digest
                or int(regression["passed"]) != 1
            ):
                self._invalid(
                    "EVOLUTION_IDENTITY_PLAN_AUTHORITY_DRIFT"
                )
            lab = conn.execute(
                """
                SELECT 1
                FROM evolution_lab_trusted_report
                WHERE report_digest = ? AND workspace_id = ?
                """,
                (plan.lab_report_digest, plan.workspace_id),
            ).fetchone()
            review = conn.execute(
                """
                SELECT 1
                FROM agent_runtime_review
                WHERE review_id = ?
                """,
                (plan.review_id,),
            ).fetchone()
            if lab is None or review is None:
                self._invalid(
                    "EVOLUTION_IDENTITY_PLAN_AUTHORITY_NOT_FOUND"
                )
            existing = conn.execute(
                """
                SELECT plan_json
                FROM evolution_identity_plan_v2
                WHERE migration_id = ?
                """,
                (plan.migration_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["plan_json"]) != encoded:
                    self._invalid(
                        "EVOLUTION_IDENTITY_PLAN_V2_REPLAY_DRIFT"
                    )
                return plan.plan_digest
            conn.execute(
                """
                INSERT INTO evolution_identity_plan_v2(
                    migration_id, plan_digest, workspace_id,
                    previous_snapshot_ref, proposed_snapshot_ref,
                    previous_snapshot_digest, proposed_snapshot_digest,
                    diff_digest, lab_report_digest,
                    regression_report_digest, review_id,
                    review_status_version, rollback_snapshot_ref,
                    activation_version, no_change_release,
                    plan_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.migration_id,
                    plan.plan_digest,
                    plan.workspace_id,
                    plan.previous_snapshot_ref,
                    plan.proposed_snapshot_ref,
                    plan.previous_snapshot_digest,
                    plan.proposed_snapshot_digest,
                    plan.diff_digest,
                    plan.lab_report_digest,
                    plan.regression_report_digest,
                    plan.review_id,
                    plan.review_status_version,
                    plan.rollback_snapshot_ref,
                    plan.activation_version,
                    int(plan.no_change_release),
                    encoded,
                    int(self._clock()),
                ),
            )
        return plan.plan_digest

    def backfill_identity_projection(
        self,
        *,
        workspace_id: str,
        snapshot_ref: str,
        activation_version: int,
        migration_ref: str,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        request = {
            "operation": "BACKFILL_IDENTITY",
            "workspace_id": workspace_id,
            "snapshot_ref": snapshot_ref,
            "activation_version": activation_version,
            "migration_ref": migration_ref,
        }
        with self._transaction() as conn:
            replay = self._replay(conn, idempotency_key, request)
            if replay is not None:
                return replay
            snapshot = self._identity_snapshot(
                conn, snapshot_ref, workspace_id
            )
            active = conn.execute(
                """
                SELECT 1
                FROM evolution_identity_active_projection_v2
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()
            if active is not None:
                self._invalid(
                    "EVOLUTION_IDENTITY_BACKFILL_ALREADY_EXISTS"
                )
            generation = 1
            _, projection_digest = self._identity_projection(
                snapshot=snapshot,
                activation_version=activation_version,
                generation=generation,
            )
            conn.execute(
                """
                INSERT INTO evolution_identity_active_projection_v2(
                    workspace_id, snapshot_ref, identity_digest,
                    snapshot_hash, activation_version,
                    activation_generation, projection_digest, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    snapshot["snapshot_ref"],
                    snapshot["identity_digest"],
                    snapshot["snapshot_hash"],
                    activation_version,
                    generation,
                    projection_digest,
                    int(self._clock()),
                ),
            )
            operation_id, decision_id, receipt_id = self._identities(
                idempotency_key
            )
            decision = VersionActivationDecisionV1(
                decision_id=decision_id,
                operation_id=operation_id,
                workspace_id=workspace_id,
                subject_kind="IDENTITY",
                subject_id="identity",
                action="ACTIVATE",
                target_version=str(activation_version),
                target_digest=hex_digest(
                    snapshot["identity_digest"], "identity_digest"
                ),
                authority_refs=(
                    bounded_text(migration_ref, "migration_ref", 1_024),
                    str(snapshot["snapshot_ref"]),
                ),
                expected_generation=0,
                reason_code="EVOLUTION_IDENTITY_BACKFILL",
            )
            return self._append_terminal(
                conn,
                decision=decision,
                request=request,
                idempotency_key=idempotency_key,
                receipt_id=receipt_id,
                event="ACTIVATED",
                generation=generation,
                projection_digest=projection_digest,
            )

    def activate_identity(
        self,
        *,
        migration_id: str,
        expected_generation: int,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        return self._transition_identity(
            migration_id=migration_id,
            rollback=False,
            lab_report_digest="",
            review_id="",
            review_status_version=0,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )

    def rollback_identity(
        self,
        *,
        migration_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        return self._transition_identity(
            migration_id=migration_id,
            rollback=True,
            lab_report_digest=lab_report_digest,
            review_id=review_id,
            review_status_version=review_status_version,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )

    def _transition_identity(
        self,
        *,
        migration_id: str,
        rollback: bool,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        request = {
            "operation": (
                "IDENTITY_ROLLBACK" if rollback else "IDENTITY_ACTIVATE"
            ),
            "migration_id": migration_id,
            "lab_report_digest": lab_report_digest,
            "review_id": review_id,
            "review_status_version": review_status_version,
            "expected_generation": expected_generation,
        }
        with self._transaction() as conn:
            replay = self._replay(conn, idempotency_key, request)
            if replay is not None:
                return replay
            plan = conn.execute(
                """
                SELECT *
                FROM evolution_identity_plan_v2
                WHERE migration_id = ?
                """,
                (bounded_text(migration_id, "migration_id", 255),),
            ).fetchone()
            if plan is None:
                self._invalid("EVOLUTION_IDENTITY_PLAN_V2_NOT_FOUND")
            active = conn.execute(
                """
                SELECT *
                FROM evolution_identity_active_projection_v2
                WHERE workspace_id = ?
                """,
                (plan["workspace_id"],),
            ).fetchone()
            generation = self._generation(
                active, expected_generation=expected_generation
            )
            if active is None:
                self._invalid(
                    "EVOLUTION_IDENTITY_ACTIVE_PROJECTION_NOT_FOUND"
                )
            if rollback:
                if str(active["snapshot_ref"]) != str(
                    plan["proposed_snapshot_ref"]
                ):
                    self._invalid(
                        "EVOLUTION_IDENTITY_ROLLBACK_SOURCE_DRIFT"
                    )
                target_ref = str(plan["rollback_snapshot_ref"])
                target_version = f"rollback-{plan['activation_version']}"
                resolved_lab = lab_report_digest
                resolved_review = review_id
                resolved_review_version = review_status_version
                review_subject_version = generation
                event = "ROLLED_BACK"
                action = "ROLLBACK"
            else:
                if str(active["snapshot_ref"]) != str(
                    plan["previous_snapshot_ref"]
                ):
                    self._invalid(
                        "EVOLUTION_IDENTITY_PREVIOUS_SNAPSHOT_DRIFT"
                    )
                target_ref = str(plan["proposed_snapshot_ref"])
                target_version = str(plan["activation_version"])
                resolved_lab = str(plan["lab_report_digest"])
                resolved_review = str(plan["review_id"])
                resolved_review_version = int(
                    plan["review_status_version"]
                )
                review_subject_version = int(plan["activation_version"])
                event = "ACTIVATED"
                action = "ACTIVATE"
            target = self._identity_snapshot(
                conn, target_ref, str(plan["workspace_id"])
            )
            regression = conn.execute(
                """
                SELECT *
                FROM evolution_identity_regression_v2
                WHERE report_digest = ?
                """,
                (plan["regression_report_digest"],),
            ).fetchone()
            if regression is None or int(regression["passed"]) != 1:
                self._invalid(
                    "EVOLUTION_IDENTITY_REGRESSION_REJECTED"
                )
            regression_payload = json.loads(
                str(regression["report_json"])
            )
            required_refs = {
                str(plan["previous_snapshot_ref"]),
                str(plan["proposed_snapshot_ref"]),
                (
                    "identity-regression://sha256/"
                    f"{plan['regression_report_digest']}"
                ),
                str(regression_payload["diff_ref"]),
            }
            report, proposal, _review = self._resolve_lab_review(
                conn,
                workspace_id=str(plan["workspace_id"]),
                target_kind=identity_target_kind(
                    migration_id=str(plan["migration_id"])
                ),
                target_version=target_version,
                lab_report_digest=resolved_lab,
                review_id=resolved_review,
                review_status_version=resolved_review_version,
                review_subject_ref=target_ref,
                review_subject_version=review_subject_version,
                required_evidence_refs=required_refs,
                now_ms=int(self._clock()),
            )
            _, projection_digest = self._identity_projection(
                snapshot=target,
                activation_version=int(plan["activation_version"]),
                generation=generation,
            )
            conn.execute(
                """
                UPDATE evolution_identity_active_projection_v2
                SET snapshot_ref = ?, identity_digest = ?, snapshot_hash = ?,
                    activation_version = ?, activation_generation = ?,
                    projection_digest = ?, updated_at_ms = ?
                WHERE workspace_id = ? AND activation_generation = ?
                """,
                (
                    target["snapshot_ref"],
                    target["identity_digest"],
                    target["snapshot_hash"],
                    plan["activation_version"],
                    generation,
                    projection_digest,
                    int(self._clock()),
                    plan["workspace_id"],
                    generation - 1,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                self._invalid(
                    "EVOLUTION_IDENTITY_ACTIVE_PROJECTION_CAS_FAILED"
                )
            operation_id, decision_id, receipt_id = self._identities(
                idempotency_key
            )
            decision = VersionActivationDecisionV1(
                decision_id=decision_id,
                operation_id=operation_id,
                workspace_id=str(plan["workspace_id"]),
                subject_kind="IDENTITY",
                subject_id="identity",
                action=action,
                target_version=target_version,
                target_digest=hex_digest(
                    target["identity_digest"], "identity_digest"
                ),
                previous_version=str(active["activation_version"]),
                previous_digest=hex_digest(
                    active["identity_digest"], "identity_digest"
                ),
                authority_refs=(
                    f"evolution-lab-report://sha256/{report['report_digest']}",
                    f"evolution-proposal://sha256/{proposal['proposal_digest']}",
                    f"review://{resolved_review}",
                    (
                        "identity-regression://sha256/"
                        f"{plan['regression_report_digest']}"
                    ),
                    str(plan["previous_snapshot_ref"]),
                    str(plan["proposed_snapshot_ref"]),
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

    def active_identity(self, *, workspace_id: str) -> dict[str, Any] | None:
        workspace = bounded_text(workspace_id, "workspace_id", 128)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT projection.*, snapshot.snapshot_json
                FROM evolution_identity_active_projection_v2 AS projection
                JOIN evolution_identity_snapshot_v2 AS snapshot
                  ON snapshot.snapshot_ref = projection.snapshot_ref
                WHERE projection.workspace_id = ?
                """,
                (workspace,),
            ).fetchone()
        if row is None:
            return None
        return {
            "workspace_id": workspace,
            "snapshot": json.loads(str(row["snapshot_json"])),
            "activation_version": int(row["activation_version"]),
            "activation_generation": int(row["activation_generation"]),
            "projection_digest": str(row["projection_digest"]),
        }

__all__ = ["EvolutionActivationIdentityMixin"]
