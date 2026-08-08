"""Run the fixed 24-pair Research PlanningSearch acceptance cohort."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from host_brain.app.application_port_wiring import build_process_application_ports
from host_brain.composition.host_authority_runtime import HostAuthorityRuntimeOwner
from host_brain.contracts.planning_search.research_benefit_cohort import (
    ResearchBenefitCohortSpecV1,
    ResearchCohortWorkloadV1,
)
from host_brain.model_runtime import LedgerAttemptAuditLookup
from host_brain.model_runtime.live_prompts import build_live_production_prompt_registry
from host_brain.persistence.code_benchmark_ledger_repository import (
    SQLiteCodeHistoricalBenchmarkLedger,
)
from host_brain.persistence.planning_search_calibration_repository import (
    SQLitePlanningCalibrationRepository,
)
from host_brain.persistence.planning_search_cohort_authority_resolver import (
    SQLitePlanningSearchCohortAuthorityResolver,
)
from host_brain.persistence.planning_search_cohort_ledger_repository import (
    SQLitePlanningSearchCohortLedger,
)
from host_brain.persistence.research_shadow_ledger_repository import (
    SQLiteResearchShadowLedger,
)
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
)
from host_brain.planning_search.research_benefit_experiment import (
    ResearchBenefitExperimentError,
    execute_prepared_research_workload,
    load_fixed_research_cohort,
    prepare_research_workload,
    sanitized_member_summary,
)
from host_brain.planning_search.research_quality_verifier import (
    research_quality_profile_digest,
    research_quality_rubric_digest,
)
from host_brain.runtime_env import load_env_file


RECEIPT_SCHEMA = "natsume.a15-research-benefit-run.v1"
DEFAULT_CONFIG = (
    REPOSITORY_ROOT
    / "configs"
    / "acceptance"
    / "research_cohort_v1"
    / "cohort.json"
)
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class _RejectingPentestReceiptAuthority:
    def resolve_receipt_digest(self, *, receipt_ref: str) -> str:
        del receipt_ref
        raise RuntimeError("PENTEST_RECEIPT_NOT_AVAILABLE_IN_RESEARCH_RUNNER")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--secret-env-file", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-namespace", default="")
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _commit_sha() -> str:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError("RESEARCH_ACCEPTANCE_COMMIT_SHA_INVALID")
    return value


def _load_environment(args: argparse.Namespace) -> None:
    load_env_file(REPOSITORY_ROOT / ".env", override=False)
    for path in args.env_file:
        load_env_file(path.expanduser().resolve(), override=True, required=True)
    for path in args.secret_env_file:
        load_env_file(path.expanduser().resolve(), override=True, required=True)


def _validate_args(args: argparse.Namespace) -> None:
    if not 1 <= int(args.limit) <= 24:
        raise ValueError("--limit must be within 1..24")
    if args.dry_run:
        return
    if args.db is None or args.output_dir is None:
        raise ValueError("--db and --output-dir are required outside --dry-run")
    namespace = str(args.run_namespace or "").strip().lower()
    if _NAMESPACE_RE.fullmatch(namespace) is None:
        raise ValueError("--run-namespace must be a stable lower-case identifier")
    args.run_namespace = namespace


def _dry_run(args: argparse.Namespace) -> int:
    cohort = load_fixed_research_cohort(args.config)
    registry = build_live_production_prompt_registry()
    prepared = tuple(
        prepare_research_workload(
            cohort=cohort,
            workload=workload,
            prompt_registry=registry,
        )
        for workload in cohort.workloads[: args.limit]
    )
    output = {
        "schema": RECEIPT_SCHEMA,
        "mode": "DRY_RUN_NO_EXTERNAL_EFFECT",
        "commit_sha": _commit_sha(),
        "config_digest": cohort.config_digest,
        "workload_count": len(prepared),
        "topic_categories": sorted(
            {item.definition.topic_category for item in prepared}
        ),
        "all_selected_mcts_used": all(item.selected_mcts_used for item in prepared),
        "all_arm_prompt_digests_distinct": all(
            item.baseline_plan.prompt_digest != item.selected_plan.prompt_digest
            for item in prepared
        ),
        "source_kinds": sorted(
            {kind for item in prepared for kind in item.source_kinds}
        ),
        "external_calls": 0,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


async def _run_live(args: argparse.Namespace) -> int:
    commit_sha = _commit_sha()
    cohort = load_fixed_research_cohort(args.config)
    db_path = args.db.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = output_dir / f"research-{args.run_namespace}-{commit_sha[:10]}.json"
    started_at_ms = int(time.time() * 1000)
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "RUNNING",
        "commit_sha": commit_sha,
        "run_namespace": args.run_namespace,
        "config_digest": cohort.config_digest,
        "cohort_id": cohort.cohort_id,
        "cohort_version": cohort.cohort_version,
        "requested_workloads": args.limit,
        "completed_workloads": 0,
        "started_at_ms": started_at_ms,
        "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
        "members": [],
    }
    _write_json(receipt_path, receipt)

    ports = build_process_application_ports(persistence_db_path=db_path)
    owner = HostAuthorityRuntimeOwner(
        persistence_db_path_provider=ports.persistence_db_path,
        model_credential_resolver=ports.model_credential_resolver,
        model_base_url_resolver=ports.model_base_url_resolver,
        model_transport=ports.model_transport,
    )
    evidence_rows = []
    prepared_rows = []
    try:
        owner.begin()
        runtime = owner.require_model_runtime()
        external_adapter = AttestedExternalResearchAdapter(
            model_attempt_service=runtime.attempt_service,
            attempt_audit_lookup=LedgerAttemptAuditLookup(owner.require_ledger()),
        )
        research_ledger = SQLiteResearchShadowLedger(db_path)
        for index, workload in enumerate(cohort.workloads[: args.limit], start=1):
            prepared = prepare_research_workload(
                cohort=cohort,
                workload=workload,
                prompt_registry=runtime.prompt_registry,
            )
            prepared_rows.append(prepared)
            print(
                f"RESEARCH_WORKLOAD_START {index}/{args.limit} {workload.workload_id}",
                flush=True,
            )
            evidence = await execute_prepared_research_workload(
                prepared=prepared,
                research_ledger=research_ledger,
                external_adapter=external_adapter,
                owner_id=f"a15-research-{args.run_namespace}-{commit_sha[:12]}",
                cohort_run_key=(
                    f"{cohort.cohort_id}:{cohort.cohort_version}:"
                    f"{args.run_namespace}:{commit_sha}:{workload.workload_id}"
                ),
            )
            evidence_rows.append(evidence)
            summary = sanitized_member_summary(evidence)
            receipt["members"].append(summary)
            receipt["completed_workloads"] = len(evidence_rows)
            _write_json(receipt_path, receipt)
            print(
                "RESEARCH_WORKLOAD_DONE "
                f"{index}/{args.limit} {workload.workload_id} "
                f"baseline={summary['baseline']['quality_score']} "
                f"selected={summary['selected']['quality_score']}",
                flush=True,
            )

        if args.limit == 24:
            spec, report = _seal_full_cohort(
                cohort=cohort,
                db_path=db_path,
                run_namespace=args.run_namespace,
                prepared_rows=prepared_rows,
                evidence_rows=evidence_rows,
            )
            receipt["cohort_spec_digest"] = spec.spec_digest
            receipt["cohort_report"] = report.to_dict()
            receipt["status"] = "PASS" if report.benefit_eligible else "FAIL"
        else:
            receipt["status"] = "PASS_PREFLIGHT_ONLY"
            receipt["cohort_report"] = None
        receipt["finished_at_ms"] = int(time.time() * 1000)
        receipt["duration_ms"] = receipt["finished_at_ms"] - started_at_ms
        receipt["receipt_digest"] = _digest_json(
            {key: value for key, value in receipt.items() if key != "receipt_digest"}
        )
        _write_json(receipt_path, receipt)
        print(f"RESEARCH_RECEIPT {receipt_path}", flush=True)
        print(f"RESEARCH_STATUS {receipt['status']}", flush=True)
        return 0 if receipt["status"] in {"PASS", "PASS_PREFLIGHT_ONLY"} else 2
    except BaseException as exc:
        receipt["status"] = "FAIL"
        receipt["failure_type"] = type(exc).__name__
        receipt["failure_detail"] = str(exc)[:500]
        receipt["finished_at_ms"] = int(time.time() * 1000)
        receipt["duration_ms"] = receipt["finished_at_ms"] - started_at_ms
        receipt["receipt_digest"] = _digest_json(
            {key: value for key, value in receipt.items() if key != "receipt_digest"}
        )
        _write_json(receipt_path, receipt)
        print(f"RESEARCH_RECEIPT {receipt_path}", flush=True)
        raise
    finally:
        owner.close()


def _seal_full_cohort(
    *,
    cohort: Any,
    db_path: Path,
    run_namespace: str,
    prepared_rows: list[Any],
    evidence_rows: list[Any],
) -> tuple[ResearchBenefitCohortSpecV1, Any]:
    execution_profiles = {
        (attestation.provider, attestation.model)
        for evidence in evidence_rows
        for attestation in (
            evidence.terminal_receipt.baseline_model.attestation,
            evidence.terminal_receipt.selected_model.attestation,
        )
        if attestation is not None
    }
    verifier_profiles = {
        (attestation.provider, attestation.model)
        for evidence in evidence_rows
        for attestation in (
            evidence.baseline_quality.attestation,
            evidence.selected_quality.attestation,
        )
        if attestation is not None
    }
    if len(execution_profiles) != 1 or len(verifier_profiles) != 1:
        raise ResearchBenefitExperimentError("cohort provider/model profile drifted")
    execution_provider, execution_model = next(iter(execution_profiles))
    verifier_provider, verifier_model = next(iter(verifier_profiles))
    prepared_by_id = {
        item.definition.workload_id: item for item in prepared_rows
    }
    workloads = tuple(
        ResearchCohortWorkloadV1(
            workload_id=evidence.workload_id,
            run_id=evidence.run_id,
            terminal_receipt_digest=evidence.terminal_receipt_digest,
            topic_category=prepared_by_id[evidence.workload_id].definition.topic_category,
            source_kinds=prepared_by_id[evidence.workload_id].source_kinds,
        )
        for evidence in sorted(evidence_rows, key=lambda item: item.workload_id)
    )
    spec = ResearchBenefitCohortSpecV1(
        cohort_id=cohort.cohort_id,
        cohort_version=cohort.cohort_version,
        planning_algorithm_version=cohort.planning_algorithm_version,
        execution_provider=execution_provider,
        execution_model=execution_model,
        verifier_provider=verifier_provider,
        verifier_model=verifier_model,
        verifier_profile_digest=research_quality_profile_digest(),
        rubric_digest=research_quality_rubric_digest(),
        randomization_seed_digest=_digest_json(
            {
                "cohort_id": cohort.cohort_id,
                "cohort_version": cohort.cohort_version,
                "run_namespace": run_namespace,
                "algorithm": "PAIRED_BOOTSTRAP_MEAN_V1",
            }
        ),
        workloads=workloads,
    )
    research_ledger = SQLiteResearchShadowLedger(db_path)
    resolver = SQLitePlanningSearchCohortAuthorityResolver(
        research_ledger=research_ledger,
        code_ledger=SQLiteCodeHistoricalBenchmarkLedger(db_path),
        calibration_repository=SQLitePlanningCalibrationRepository(db_path),
        pentest_receipt_authority=_RejectingPentestReceiptAuthority(),
    )
    cohort_ledger = SQLitePlanningSearchCohortLedger(
        db_path,
        authority_resolver=resolver,
    )
    cohort_ledger.register_research(spec)
    for evidence in sorted(evidence_rows, key=lambda item: item.workload_id):
        cohort_ledger.append_research_member(spec=spec, evidence=evidence)
    report = cohort_ledger.seal_research(spec=spec, evidence=evidence_rows)
    return spec, report


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _digest_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    args = _parse_args()
    _validate_args(args)
    if args.dry_run:
        return _dry_run(args)
    _load_environment(args)
    return asyncio.run(_run_live(args))


if __name__ == "__main__":
    raise SystemExit(main())
