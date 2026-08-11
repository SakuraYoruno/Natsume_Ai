"""Fail-closed, query-only reader for trusted Evolution Lab operator evidence.

The writer repository remains the sole authority for registrations, runs,
reports, proposals and suite receipts.  This module reopens the durable SQLite
database in ``mode=ro`` and validates the complete evidence chain before
returning a privacy-bounded operator projection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from host_brain.contracts.evolution_lab import (
    CounterfactualPairV2,
    DatasetManifestV1,
    EvolutionProposalV2,
    ExperimentSpecV2,
    PolicyVariantV1,
)
from host_brain.contracts.evolution_lab_trusted import (
    GoldenExperimentSuiteReceiptV1,
    TrustedExperimentReportV2,
    TrustedExperimentRunReceiptV2,
)


_REQUIRED_TABLES = {
    "evolution_lab_v2_experiment_spec",
    "evolution_lab_trusted_operation_head",
    "evolution_lab_trusted_operation_event",
    "evolution_lab_trusted_run_receipt",
    "evolution_lab_trusted_report",
    "evolution_lab_trusted_proposal",
    "evolution_lab_trusted_redacted_dataset",
    "evolution_lab_golden_suite_receipt",
}
_GOLDEN_KINDS = {
    "memory_write",
    "proactive",
    "research_planning",
}
_MAX_PAGE_SIZE = 100
_MAX_CURSOR_BYTES = 512


class TrustedEvolutionLabQueryError(RuntimeError):
    """Trusted evidence is unavailable, corrupt or outside the read contract."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "EVOLUTION_LAB_QUERY_FAILED")
        self.detail = str(detail or "trusted Evolution Lab query failed")[:400]
        super().__init__(f"{self.reason_code}: {self.detail}")


class TrustedEvolutionLabOperatorQueryRepository:
    """Reopen and verify trusted Lab evidence without acquiring write authority."""

    def __init__(self, db_path: str | Path) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:":
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_QUERY_DB_REQUIRED",
                "operator query repository requires a durable SQLite path",
            )
        self.db_path = Path(raw).expanduser().resolve()
        if not self.db_path.is_file():
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_QUERY_DB_MISSING",
                "trusted Evolution Lab database does not exist",
            )
        self._uri = f"{self.db_path.as_uri()}?mode=ro"
        self._assert_schema()

    def _connection(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(
                self._uri,
                uri=True,
                timeout=10.0,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_QUERY_OPEN_FAILED",
                str(exc),
            ) from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _assert_schema(self) -> None:
        try:
            with self._connection() as conn:
                query_only = int(conn.execute("PRAGMA query_only").fetchone()[0])
                names = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
        except sqlite3.Error as exc:
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_QUERY_SCHEMA_FAILED",
                str(exc),
            ) from exc
        if query_only != 1:
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_QUERY_NOT_READ_ONLY",
                "SQLite query_only mode was not enabled",
            )
        missing = sorted(_REQUIRED_TABLES - names)
        if missing:
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_QUERY_SCHEMA_NOT_READY",
                f"missing trusted Evolution Lab tables: {', '.join(missing)}",
            )

    def list_suites(
        self,
        *,
        limit: int = 25,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, newest-first page of fully verified safe suites."""

        page_size = _bounded_limit(limit)
        boundary = decode_evolution_lab_suite_cursor(cursor) if cursor else None
        where = ""
        params: list[Any] = []
        if boundary is not None:
            where = (
                "WHERE (created_at_ms < ? OR "
                "(created_at_ms = ? AND suite_id < ?))"
            )
            params.extend(
                [
                    boundary["created_at_ms"],
                    boundary["created_at_ms"],
                    boundary["suite_id"],
                ]
            )
        params.append(page_size + 1)
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT suite_id, commit_sha, migration_head, receipt_digest,
                           receipt_json, status, production_side_effects,
                           production_influence_enabled, created_at_ms
                    FROM evolution_lab_golden_suite_receipt
                    {where}
                    ORDER BY created_at_ms DESC, suite_id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
                report_cache: dict[str, dict[str, Any]] = {}
                items = [
                    self._suite_projection(
                        conn,
                        row=row,
                        report_cache=report_cache,
                    )
                    for row in rows[:page_size]
                ]
        except TrustedEvolutionLabQueryError:
            raise
        except (sqlite3.Error, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_EVIDENCE_INVALID",
                str(exc),
            ) from exc
        next_cursor = None
        if len(rows) > page_size and items:
            tail = items[-1]
            next_cursor = encode_evolution_lab_suite_cursor(
                created_at_ms=int(tail["created_at_ms"]),
                suite_id=str(tail["suite_id"]),
            )
        return {
            "schema_version": "Natsume.evolution_lab.operator_page.v1",
            "read_only": True,
            "proposal_only": True,
            "production_influence_enabled": False,
            "items": items,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "page_size": len(items),
        }

    def _suite_projection(
        self,
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        report_cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        payload = _json_object(row["receipt_json"], "suite receipt")
        receipt = GoldenExperimentSuiteReceiptV1(
            suite_id=payload.get("suite_id"),
            commit_sha=payload.get("commit_sha"),
            migration_head=payload.get("migration_head"),
            engine_identity=payload.get("engine_identity"),
            image_reference=payload.get("image_reference"),
            report_digests=payload.get("report_digests", {}),
            run_receipt_digests=tuple(payload.get("run_receipt_digests", ())),
            created_at_ms=payload.get("created_at_ms"),
        )
        if (
            payload != receipt.to_dict()
            or str(row["suite_id"]) != receipt.suite_id
            or str(row["commit_sha"]) != receipt.commit_sha
            or str(row["migration_head"]) != receipt.migration_head
            or str(row["receipt_digest"]) != receipt.receipt_digest
            or str(row["status"]) != "PASS"
            or int(row["production_side_effects"]) != 0
            or int(row["production_influence_enabled"]) != 0
            or int(row["created_at_ms"]) != receipt.created_at_ms
        ):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_SUITE_BINDING_DRIFT",
                f"suite {receipt.suite_id} does not match its durable index",
            )
        reports: list[dict[str, Any]] = []
        authoritative_runs: set[str] = set()
        for kind in sorted(_GOLDEN_KINDS):
            report_digest = receipt.report_digests[kind]
            report = report_cache.get(report_digest)
            if report is None:
                report = self._report_projection(
                    conn,
                    experiment_kind=kind,
                    report_digest=report_digest,
                    engine_identity=receipt.engine_identity,
                    image_reference=receipt.image_reference,
                )
                report_cache[report_digest] = report
            reports.append(report)
            authoritative_runs.update(
                str(item["receipt_digest"]) for item in report["arms"]
            )
        if authoritative_runs != set(receipt.run_receipt_digests):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_SUITE_RUN_SET_DRIFT",
                f"suite {receipt.suite_id} run set is not authoritative",
            )
        workspace_ids = sorted(
            {str(report["workspace_id"]) for report in reports}
        )
        return {
            "suite_id": receipt.suite_id,
            "commit_sha": receipt.commit_sha,
            "migration_head": receipt.migration_head,
            "receipt_digest": receipt.receipt_digest,
            "status": "PASS",
            "engine_identity": receipt.engine_identity,
            "image_reference": receipt.image_reference,
            "workspace_ids": workspace_ids,
            "report_count": len(reports),
            "run_count": len(authoritative_runs),
            "reports": reports,
            "production_side_effects": False,
            "production_influence_enabled": False,
            "created_at_ms": receipt.created_at_ms,
        }

    def _report_projection(
        self,
        conn: sqlite3.Connection,
        *,
        experiment_kind: str,
        report_digest: str,
        engine_identity: str,
        image_reference: str,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT report_id, experiment_kind, report_digest, spec_digest,
                   workspace_id, report_json, promotion_eligible,
                   review_required, production_influence_enabled, created_at_ms
            FROM evolution_lab_trusted_report
            WHERE report_digest = ?
            """,
            (report_digest,),
        ).fetchone()
        if row is None:
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_REPORT_MISSING",
                f"trusted report {report_digest} is missing",
            )
        payload = _json_object(row["report_json"], "trusted report")
        spec = _spec_from_payload(payload.get("spec"))
        runs_raw = payload.get("runs")
        comparisons_raw = payload.get("comparisons")
        if not isinstance(runs_raw, list) or not isinstance(comparisons_raw, list):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_REPORT_INVALID",
                "trusted report run/comparison arrays are invalid",
            )
        runs = tuple(
            TrustedExperimentRunReceiptV2.from_dict(
                _mapping(item, "trusted run")
            )
            for item in runs_raw
        )
        comparisons = tuple(
            _comparison_from_payload(item) for item in comparisons_raw
        )
        report = TrustedExperimentReportV2(
            report_id=payload.get("report_id"),
            experiment_kind=payload.get("experiment_kind"),
            spec=spec,
            runs=runs,
            comparisons=comparisons,
        )
        if (
            payload != report.to_dict()
            or report.report_digest != report_digest
            or str(row["report_id"]) != report.report_id
            or str(row["experiment_kind"]) != experiment_kind
            or report.experiment_kind != experiment_kind
            or str(row["spec_digest"]) != spec.spec_digest
            or str(row["workspace_id"]) != spec.workspace_id
            or int(row["promotion_eligible"]) != 1
            or int(row["review_required"]) != 1
            or int(row["production_influence_enabled"]) != 0
        ):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_REPORT_BINDING_DRIFT",
                f"trusted report {report_digest} does not match its index",
            )
        self._verify_registered_spec(conn, spec)
        arms = [
            self._run_projection(
                conn,
                run=run,
                variant=spec.variant(run.variant_id),
                engine_identity=engine_identity,
                image_reference=image_reference,
            )
            for run in report.runs
        ]
        proposals = self._proposal_projections(
            conn,
            report_digest=report.report_digest,
        )
        return {
            "report_id": report.report_id,
            "report_digest": report.report_digest,
            "experiment_kind": report.experiment_kind,
            "workspace_id": spec.workspace_id,
            "experiment_id": spec.experiment_id,
            "spec_digest": spec.spec_digest,
            "dataset_manifest_digest": spec.dataset_manifest.manifest_digest,
            "dataset_source_kind": spec.dataset_manifest.source_kind,
            "dataset_record_count": spec.dataset_manifest.record_count,
            "seed": spec.seed,
            "budget": dict(spec.budget),
            "verifier": {
                "identity": spec.verifier_identity,
                "version": spec.verifier_version,
                "digest": spec.verifier_digest,
            },
            "isolation_profile_digest": spec.isolation_profile_digest,
            "arms": arms,
            "comparisons": [
                {
                    "baseline_run_id": item.baseline_run_id,
                    "alternative_run_id": item.alternative_run_id,
                    "outcome_delta": dict(item.outcome_delta),
                    "evidence_delta": dict(item.evidence_delta),
                    "cost_delta": dict(item.cost_delta),
                    "latency_delta": dict(item.latency_delta),
                    "risk_delta": dict(item.risk_delta),
                    "verification_delta": dict(item.verification_delta),
                }
                for item in report.comparisons
            ],
            "promotion_evidence_eligible": True,
            "review_required": True,
            "review_status": (
                "AWAITING_REVIEW" if proposals else "PROPOSAL_MISSING"
            ),
            "review_ref": None,
            "proposals": proposals,
            "production_influence_enabled": False,
            "created_at_ms": int(row["created_at_ms"]),
        }

    def _verify_registered_spec(
        self,
        conn: sqlite3.Connection,
        spec: ExperimentSpecV2,
    ) -> None:
        row = conn.execute(
            """
            SELECT experiment_id, spec_digest, workspace_id,
                   dataset_manifest_digest, spec_json
            FROM evolution_lab_v2_experiment_spec
            WHERE spec_digest = ?
            """,
            (spec.spec_digest,),
        ).fetchone()
        if (
            row is None
            or str(row["experiment_id"]) != spec.experiment_id
            or str(row["workspace_id"]) != spec.workspace_id
            or str(row["dataset_manifest_digest"])
            != spec.dataset_manifest.manifest_digest
            or str(row["spec_json"]) != _canonical(spec.to_dict())
        ):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_SPEC_BINDING_DRIFT",
                f"spec {spec.spec_digest} is not the registered authority",
            )

    def _run_projection(
        self,
        conn: sqlite3.Connection,
        *,
        run: TrustedExperimentRunReceiptV2,
        variant: PolicyVariantV1,
        engine_identity: str,
        image_reference: str,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT operation_id, execution_id, spec_digest, workspace_id,
                   dataset_manifest_digest, variant_id, receipt_digest,
                   sandbox_attestation_digest, verification_receipt_digest,
                   effect_audit_receipt_digest, receipt_json,
                   production_side_effects, benefit_eligible,
                   production_influence_enabled, created_at_ms
            FROM evolution_lab_trusted_run_receipt
            WHERE run_id = ?
            """,
            (run.run_id,),
        ).fetchone()
        sandbox = run.sandbox_attestation
        verification = run.verification_receipt
        if (
            row is None
            or str(row["execution_id"]) != run.execution_id
            or str(row["spec_digest"]) != run.spec_digest
            or str(row["workspace_id"]) != run.workspace_id
            or str(row["dataset_manifest_digest"])
            != run.dataset_manifest_digest
            or str(row["variant_id"]) != run.variant_id
            or str(row["receipt_digest"]) != run.receipt_digest
            or str(row["sandbox_attestation_digest"])
            != sandbox.receipt_digest
            or str(row["verification_receipt_digest"])
            != verification.receipt_digest
            or str(row["effect_audit_receipt_digest"])
            != sandbox.effect_audit.receipt_digest
            or str(row["receipt_json"]) != _canonical(run.to_dict())
            or int(row["production_side_effects"]) != 0
            or int(row["benefit_eligible"]) != 1
            or int(row["production_influence_enabled"]) != 0
            or sandbox.engine_identity != engine_identity
            or sandbox.image_reference != image_reference
        ):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_RUN_BINDING_DRIFT",
                f"run {run.run_id} does not match durable evidence",
            )
        self._verify_operation_chain(
            conn,
            operation_id=str(row["operation_id"]),
            run=run,
        )
        return {
            "run_id": run.run_id,
            "execution_id": run.execution_id,
            "receipt_digest": run.receipt_digest,
            "variant_id": run.variant_id,
            "is_baseline": variant.is_baseline,
            "policy_version": variant.policy_version,
            "code_version": variant.code_version,
            "config_digest": variant.config_digest,
            "status": run.status,
            "model_identity": run.model_identity,
            "usage": dict(run.usage),
            "cost": dict(run.cost),
            "latency_ms": run.latency_ms,
            "sandbox_attestation_digest": sandbox.receipt_digest,
            "container_id": sandbox.container_id,
            "verification_receipt_digest": verification.receipt_digest,
            "verification_status": verification.status,
            "verification_evidence_digest": verification.evidence_digest,
            "outcome": _numeric_projection(verification.outcome),
            "risk": _numeric_projection(verification.risk),
            "metrics": _numeric_projection(verification.metrics),
            "production_side_effects": False,
            "benefit_eligible": True,
            "production_influence_enabled": False,
            "created_at_ms": int(row["created_at_ms"]),
        }

    def _verify_operation_chain(
        self,
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        run: TrustedExperimentRunReceiptV2,
    ) -> None:
        head = conn.execute(
            """
            SELECT operation_id, execution_id, spec_digest, workspace_id,
                   dataset_manifest_digest, variant_id, status, receipt_digest,
                   current_event_sequence, current_event_digest
            FROM evolution_lab_trusted_operation_head
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if (
            head is None
            or str(head["execution_id"]) != run.execution_id
            or str(head["spec_digest"]) != run.spec_digest
            or str(head["workspace_id"]) != run.workspace_id
            or str(head["dataset_manifest_digest"])
            != run.dataset_manifest_digest
            or str(head["variant_id"]) != run.variant_id
            or str(head["status"]) != "COMPLETED"
            or str(head["receipt_digest"]) != run.receipt_digest
        ):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_OPERATION_HEAD_DRIFT",
                f"operation {operation_id} does not bind the completed run",
            )
        events = conn.execute(
            """
            SELECT event_sequence, event_type, payload_json, payload_digest,
                   previous_event_digest, event_digest
            FROM evolution_lab_trusted_operation_event
            WHERE operation_id = ?
            ORDER BY event_sequence ASC
            """,
            (operation_id,),
        ).fetchall()
        previous: str | None = None
        for expected_sequence, event in enumerate(events, start=1):
            payload_json = str(event["payload_json"])
            payload_digest = _sha256(payload_json)
            event_digest = _sha256(
                "|".join(
                    (
                        operation_id,
                        str(expected_sequence),
                        str(event["event_type"]),
                        payload_digest,
                        previous or "",
                    )
                )
            )
            if (
                int(event["event_sequence"]) != expected_sequence
                or str(event["payload_digest"]) != payload_digest
                or (
                    None
                    if event["previous_event_digest"] is None
                    else str(event["previous_event_digest"])
                )
                != previous
                or str(event["event_digest"]) != event_digest
            ):
                raise TrustedEvolutionLabQueryError(
                    "EVOLUTION_LAB_OPERATION_CHAIN_INVALID",
                    f"operation {operation_id} event chain is invalid",
                )
            previous = event_digest
        if (
            not events
            or len(events) != int(head["current_event_sequence"])
            or previous != str(head["current_event_digest"])
            or str(events[-1]["event_type"]) != "COMPLETED"
        ):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_OPERATION_CHAIN_HEAD_DRIFT",
                f"operation {operation_id} chain head is invalid",
            )

    def _proposal_projections(
        self,
        conn: sqlite3.Connection,
        *,
        report_digest: str,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT proposal_id, proposal_digest, report_digest, target_kind,
                   target_version, proposal_json, review_required,
                   production_influence_enabled, created_at_ms
            FROM evolution_lab_trusted_proposal
            WHERE report_digest = ?
            ORDER BY created_at_ms ASC, proposal_id ASC
            """,
            (report_digest,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_object(row["proposal_json"], "trusted proposal")
            proposal = EvolutionProposalV2(
                proposal_id=payload.get("proposal_id"),
                report_digest=payload.get("report_digest"),
                target_kind=payload.get("target_kind"),
                target_version=payload.get("target_version"),
                rationale=payload.get("rationale"),
                review_required=payload.get("review_required"),
                production_influence_enabled=payload.get(
                    "production_influence_enabled"
                ),
            )
            if (
                payload != proposal.to_dict()
                or proposal.report_digest != report_digest
                or str(row["proposal_id"]) != proposal.proposal_id
                or str(row["proposal_digest"]) != proposal.proposal_digest
                or str(row["target_kind"]) != proposal.target_kind
                or str(row["target_version"]) != proposal.target_version
                or int(row["review_required"]) != 1
                or int(row["production_influence_enabled"]) != 0
            ):
                raise TrustedEvolutionLabQueryError(
                    "EVOLUTION_LAB_PROPOSAL_BINDING_DRIFT",
                    f"proposal {proposal.proposal_id} does not match its index",
                )
            result.append(
                {
                    "proposal_id": proposal.proposal_id,
                    "proposal_digest": proposal.proposal_digest,
                    "target_kind": proposal.target_kind,
                    "target_version": proposal.target_version,
                    "review_required": True,
                    "review_status": "AWAITING_REVIEW",
                    "review_ref": None,
                    "production_influence_enabled": False,
                    "created_at_ms": int(row["created_at_ms"]),
                }
            )
        return result


def _spec_from_payload(value: Any) -> ExperimentSpecV2:
    payload = _mapping(value, "experiment spec")
    manifest_raw = _mapping(payload.get("dataset_manifest"), "dataset manifest")
    manifest = DatasetManifestV1(
        workspace_id=manifest_raw.get("workspace_id"),
        dataset_id=manifest_raw.get("dataset_id"),
        dataset_digest=manifest_raw.get("dataset_digest"),
        schema_digest=manifest_raw.get("schema_digest"),
        record_count=manifest_raw.get("record_count"),
        source_kind=manifest_raw.get("source_kind"),
        redaction_receipt_digest=manifest_raw.get("redaction_receipt_digest"),
    )
    variants_raw = payload.get("variants")
    if not isinstance(variants_raw, list):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_SPEC_INVALID",
            "experiment variants must be an array",
        )
    variants = tuple(
        PolicyVariantV1(
            variant_id=item.get("variant_id"),
            policy_version=item.get("policy_version"),
            code_version=item.get("code_version"),
            config_digest=item.get("config_digest"),
            is_baseline=item.get("is_baseline"),
        )
        for item in (
            _mapping(candidate, "policy variant")
            for candidate in variants_raw
        )
    )
    spec = ExperimentSpecV2(
        experiment_id=payload.get("experiment_id"),
        workspace_id=payload.get("workspace_id"),
        dataset_manifest=manifest,
        seed=payload.get("seed"),
        budget=payload.get("budget", {}),
        variants=variants,
        verifier_identity=payload.get("verifier_identity"),
        verifier_version=payload.get("verifier_version"),
        verifier_digest=payload.get("verifier_digest"),
        isolation_profile_digest=payload.get("isolation_profile_digest"),
    )
    if payload != spec.to_dict():
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_SPEC_CANONICAL_DRIFT",
            f"spec {spec.experiment_id} is not canonical",
        )
    return spec


def _comparison_from_payload(value: Any) -> CounterfactualPairV2:
    payload = _mapping(value, "counterfactual comparison")
    comparison = CounterfactualPairV2(
        baseline_run_id=payload.get("baseline_run_id"),
        alternative_run_id=payload.get("alternative_run_id"),
        outcome_delta=payload.get("outcome_delta", {}),
        evidence_delta=payload.get("evidence_delta", {}),
        cost_delta=payload.get("cost_delta", {}),
        latency_delta=payload.get("latency_delta", {}),
        risk_delta=payload.get("risk_delta", {}),
        verification_delta=payload.get("verification_delta", {}),
    )
    if payload != comparison.to_dict():
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_COMPARISON_CANONICAL_DRIFT",
            "counterfactual comparison is not canonical",
        )
    return comparison


def _numeric_projection(value: Mapping[str, Any]) -> dict[str, float | int]:
    """Expose only finite numeric metrics; never pass arbitrary output bodies."""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float | int] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        if (
            not key
            or len(key) > 120
            or isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or not math.isfinite(float(raw_value))
        ):
            continue
        result[key] = raw_value
    return dict(sorted(result.items()))


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_EVIDENCE_INVALID",
            f"{field} must be an object",
        )
    return value


def _json_object(value: Any, field: str) -> dict[str, Any]:
    payload = json.loads(str(value))
    if not isinstance(payload, dict):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_EVIDENCE_INVALID",
            f"{field} must be a JSON object",
        )
    return payload


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_limit(value: Any) -> int:
    if isinstance(value, bool):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_QUERY_LIMIT_INVALID",
            "limit must be an integer",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_QUERY_LIMIT_INVALID",
            "limit must be an integer",
        ) from exc
    if normalized < 1 or normalized > _MAX_PAGE_SIZE:
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_QUERY_LIMIT_INVALID",
            f"limit must be between 1 and {_MAX_PAGE_SIZE}",
        )
    return normalized


def encode_evolution_lab_suite_cursor(
    *,
    created_at_ms: int,
    suite_id: str,
) -> str:
    """Encode an opaque, newest-first suite pagination boundary."""

    raw = _canonical(
        {
            "created_at_ms": max(0, int(created_at_ms)),
            "suite_id": str(suite_id),
            "version": 1,
        }
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_evolution_lab_suite_cursor(value: str) -> dict[str, Any]:
    """Decode and strictly validate an opaque suite pagination boundary."""

    text = str(value or "").strip()
    if not text or len(text.encode("utf-8")) > _MAX_CURSOR_BYTES:
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_QUERY_CURSOR_INVALID",
            "cursor is empty or too large",
        )
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_QUERY_CURSOR_INVALID",
            "cursor is malformed",
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or isinstance(payload.get("created_at_ms"), bool)
        or not isinstance(payload.get("created_at_ms"), int)
        or int(payload["created_at_ms"]) < 0
        or not isinstance(payload.get("suite_id"), str)
        or not str(payload["suite_id"]).strip()
        or len(str(payload["suite_id"])) > 160
    ):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_QUERY_CURSOR_INVALID",
            "cursor fields are invalid",
        )
    return {
        "created_at_ms": int(payload["created_at_ms"]),
        "suite_id": str(payload["suite_id"]),
    }


__all__ = [
    "TrustedEvolutionLabOperatorQueryRepository",
    "TrustedEvolutionLabQueryError",
    "decode_evolution_lab_suite_cursor",
    "encode_evolution_lab_suite_cursor",
]
