"""Run the fixed 20-pair Code PlanningSearch acceptance cohort."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from host_brain.app.application_port_wiring import build_process_application_ports
from host_brain.composition.host_authority_runtime import HostAuthorityRuntimeOwner
from host_brain.contracts.planning_search.code_benefit_cohort import (
    CodeBenefitCohortSpecV1,
    CodeCohortWorkloadV1,
)
from host_brain.model_runtime import LedgerAttemptAuditLookup
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
from host_brain.planning_search.code_benefit_experiment import (
    CODE_BENCHMARK_SCENE_TYPE,
    DEFAULT_CODE_BENCHMARK_MODEL_TIMEOUT_MS,
    CODE_HIDDEN_VERIFIER_PROFILE_DIGEST,
    CODE_STATIC_VERIFIER_PROFILE_DIGEST,
    CodeBenefitExperimentError,
    code_execution_profile_digest,
    create_fixed_code_source_repository,
    execute_prepared_code_workload,
    load_fixed_code_cohort,
    prepare_code_workload,
    sanitized_code_member_summary,
)
from host_brain.runtime_env import load_env_file


RECEIPT_SCHEMA = "natsume.a15-code-benefit-run.v1"
DEFAULT_CONFIG = (
    REPOSITORY_ROOT / "configs" / "acceptance" / "code_cohort_v1" / "cohort.json"
)
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,59}$")


class _RejectingPentestReceiptAuthority:
    def resolve_receipt_digest(self, *, receipt_ref: str) -> str:
        del receipt_ref
        raise RuntimeError("PENTEST_RECEIPT_NOT_AVAILABLE_IN_CODE_RUNNER")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--secret-env-file", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-namespace", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--model-timeout-ms",
        type=int,
        default=DEFAULT_CODE_BENCHMARK_MODEL_TIMEOUT_MS,
    )
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
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise RuntimeError("CODE_ACCEPTANCE_COMMIT_SHA_INVALID")
    return value


def _load_environment(args: argparse.Namespace) -> None:
    load_env_file(REPOSITORY_ROOT / ".env", override=False)
    for path in args.env_file:
        load_env_file(path.expanduser().resolve(), override=True, required=True)
    for path in args.secret_env_file:
        load_env_file(path.expanduser().resolve(), override=True, required=True)
    os.environ[
        f"HOST_MODEL_ROUTE_SCENE_{CODE_BENCHMARK_SCENE_TYPE}_TIMEOUT_MS"
    ] = str(args.model_timeout_ms)


def _validate_args(args: argparse.Namespace) -> None:
    if not 1 <= int(args.limit) <= 20:
        raise ValueError("--limit must be within 1..20")
    if not 10_000 <= int(args.model_timeout_ms) <= 90_000:
        raise ValueError("--model-timeout-ms must be within 10000..90000")
    if args.dry_run:
        return
    if args.db is None or args.output_dir is None:
        raise ValueError("--db and --output-dir are required outside --dry-run")
    namespace = str(args.run_namespace or "").strip().lower()
    if _NAMESPACE_RE.fullmatch(namespace) is None:
        raise ValueError("--run-namespace must be a stable lower-case identifier")
    args.run_namespace = namespace


def _dry_run(args: argparse.Namespace) -> int:
    cohort = load_fixed_code_cohort(args.config)
    with tempfile.TemporaryDirectory(prefix="natsume-a15-code-dry-") as directory:
        runtime = create_fixed_code_source_repository(
            cohort=cohort,
            runtime_root=Path(directory) / "runtime",
        )
        prepared = tuple(
            prepare_code_workload(
                cohort=cohort,
                definition=definition,
                runtime=runtime,
                run_namespace="dry-run",
            )
            for definition in cohort.workloads[: args.limit]
        )
    output = {
        "schema": RECEIPT_SCHEMA,
        "mode": "DRY_RUN_NO_EXTERNAL_EFFECT",
        "commit_sha": _commit_sha(),
        "config_digest": cohort.config_digest,
        "workload_count": len(prepared),
        "source_commit": runtime.source_commit,
        "all_selected_mcts_used": all(item.selected_mcts_used for item in prepared),
        "all_baseline_l3_disabled": all(
            "code.patch.safe"
            not in item.run_spec.baseline.selected_path_candidate_ids
            for item in prepared
        ),
        "all_selected_safe_path": all(
            "code.patch.safe" in item.run_spec.selected.selected_path_candidate_ids
            for item in prepared
        ),
        "all_independent_arm_ids": all(
            item.run_spec.baseline.arm_id != item.run_spec.selected.arm_id
            for item in prepared
        ),
        "external_calls": 0,
        "worktrees_executed": 0,
        "model_scene_type": CODE_BENCHMARK_SCENE_TYPE,
        "model_timeout_ms": int(args.model_timeout_ms),
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


async def _run_live(args: argparse.Namespace) -> int:
    commit_sha = _commit_sha()
    cohort = load_fixed_code_cohort(args.config)
    db_path = args.db.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = output_dir / f"code-{args.run_namespace}-{commit_sha[:10]}.json"
    runtime_root = output_dir / f"code-runtime-{args.run_namespace}-{commit_sha[:10]}"
    randomization_seed = secrets.token_bytes(32)
    randomization_seed_digest = hashlib.sha256(randomization_seed).hexdigest()
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
        "randomization_seed_digest": randomization_seed_digest,
        "started_at_ms": started_at_ms,
        "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
        "model_scene_type": CODE_BENCHMARK_SCENE_TYPE,
        "model_timeout_ms": int(args.model_timeout_ms),
        "members": [],
        "planned_arm_orders": {},
    }
    _write_json(receipt_path, receipt)
    runtime = create_fixed_code_source_repository(
        cohort=cohort,
        runtime_root=runtime_root,
    )
    receipt["source_commit"] = runtime.source_commit
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
        model_runtime = owner.require_model_runtime()
        external_adapter = AttestedExternalResearchAdapter(
            model_attempt_service=model_runtime.attempt_service,
            attempt_audit_lookup=LedgerAttemptAuditLookup(owner.require_ledger()),
        )
        code_ledger = SQLiteCodeHistoricalBenchmarkLedger(db_path)
        for index, definition in enumerate(cohort.workloads[: args.limit], start=1):
            prepared = prepare_code_workload(
                cohort=cohort,
                definition=definition,
                runtime=runtime,
                run_namespace=args.run_namespace,
            )
            prepared_rows.append(prepared)
            arm_order = _arm_order(randomization_seed, definition.workload_id)
            receipt["planned_arm_orders"][definition.workload_id] = list(arm_order)
            _write_json(receipt_path, receipt)
            print(
                f"CODE_WORKLOAD_START {index}/{args.limit} "
                f"{definition.workload_id} order={','.join(arm_order)}",
                flush=True,
            )
            evidence = await execute_prepared_code_workload(
                prepared=prepared,
                runtime=runtime,
                ledger=code_ledger,
                external_adapter=external_adapter,
                arm_order=arm_order,
                execution_namespace=args.run_namespace,
                model_timeout_ms=args.model_timeout_ms,
            )
            evidence_rows.append(evidence)
            summary = sanitized_code_member_summary(evidence, arm_order=arm_order)
            receipt["members"].append(summary)
            receipt["completed_workloads"] = len(evidence_rows)
            _write_json(receipt_path, receipt)
            print(
                "CODE_WORKLOAD_DONE "
                f"{index}/{args.limit} {definition.workload_id} "
                f"baseline={summary['baseline']['hidden_test_passed']} "
                f"selected={summary['selected']['hidden_test_passed']}",
                flush=True,
            )

        if args.limit == 20:
            spec, report = _seal_full_cohort(
                cohort=cohort,
                db_path=db_path,
                randomization_seed_digest=randomization_seed_digest,
                prepared_rows=prepared_rows,
                evidence_rows=evidence_rows,
                model_timeout_ms=args.model_timeout_ms,
            )
            receipt["cohort_spec_digest"] = spec.spec_digest
            receipt["cohort_report"] = report.to_dict()
            receipt["status"] = "PASS" if report.benefit_eligible else "FAIL"
        else:
            receipt["cohort_report"] = None
            receipt["status"] = "PASS_PREFLIGHT_ONLY"
        receipt["finished_at_ms"] = int(time.time() * 1000)
        receipt["duration_ms"] = receipt["finished_at_ms"] - started_at_ms
        receipt["receipt_digest"] = _digest_json(
            {key: value for key, value in receipt.items() if key != "receipt_digest"}
        )
        _write_json(receipt_path, receipt)
        print(f"CODE_RECEIPT {receipt_path}", flush=True)
        print(f"CODE_STATUS {receipt['status']}", flush=True)
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
        print(f"CODE_RECEIPT {receipt_path}", flush=True)
        raise
    finally:
        owner.close()


def _seal_full_cohort(
    *,
    cohort: Any,
    db_path: Path,
    randomization_seed_digest: str,
    prepared_rows: list[Any],
    evidence_rows: list[Any],
    model_timeout_ms: int,
) -> tuple[CodeBenefitCohortSpecV1, Any]:
    provider_models = {
        (arm.attestation.provider, arm.attestation.model)
        for evidence in evidence_rows
        for arm in (evidence.baseline, evidence.selected)
        if arm.attestation is not None
    }
    if len(provider_models) != 1:
        raise CodeBenefitExperimentError("Code cohort provider/model profile drifted")
    provider, model = next(iter(provider_models))
    workloads = tuple(
        CodeCohortWorkloadV1(
            workload_id=evidence.workload_id,
            run_id=evidence.run_id,
            terminal_receipt_digest=evidence.terminal_receipt_digest,
            source_commit=evidence.terminal_receipt.spec.registration.source_commit,
            path_policy_digest=evidence.terminal_receipt.spec.registration.path_policy_digest,
        )
        for evidence in sorted(evidence_rows, key=lambda item: item.workload_id)
    )
    budget_digests = {
        item.execution_budget.budget_digest for item in prepared_rows
    }
    if len(budget_digests) != 1:
        raise CodeBenefitExperimentError("Code execution budget drifted")
    spec = CodeBenefitCohortSpecV1(
        cohort_id=cohort.cohort_id,
        cohort_version=cohort.cohort_version,
        planning_algorithm_version=cohort.planning_algorithm_version,
        execution_provider=provider,
        execution_model=model,
        execution_profile_digest=code_execution_profile_digest(
            model_timeout_ms=model_timeout_ms
        ),
        execution_budget_digest=next(iter(budget_digests)),
        hidden_verifier_profile_digest=CODE_HIDDEN_VERIFIER_PROFILE_DIGEST,
        static_verifier_profile_digest=CODE_STATIC_VERIFIER_PROFILE_DIGEST,
        randomization_seed_digest=randomization_seed_digest,
        workloads=workloads,
    )
    code_ledger = SQLiteCodeHistoricalBenchmarkLedger(db_path)
    resolver = SQLitePlanningSearchCohortAuthorityResolver(
        research_ledger=SQLiteResearchShadowLedger(db_path),
        code_ledger=code_ledger,
        calibration_repository=SQLitePlanningCalibrationRepository(db_path),
        pentest_receipt_authority=_RejectingPentestReceiptAuthority(),
    )
    cohort_ledger = SQLitePlanningSearchCohortLedger(
        db_path,
        authority_resolver=resolver,
    )
    cohort_ledger.register_code(spec)
    for evidence in sorted(evidence_rows, key=lambda item: item.workload_id):
        cohort_ledger.append_code_member(spec=spec, evidence=evidence)
    report = cohort_ledger.seal_code(spec=spec, evidence=evidence_rows)
    return spec, report


def _arm_order(seed: bytes, workload_id: str) -> tuple[str, str]:
    bit = hmac.new(seed, workload_id.encode("utf-8"), hashlib.sha256).digest()[0] & 1
    return ("BASELINE", "SELECTED") if bit == 0 else ("SELECTED", "BASELINE")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _digest_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def main() -> int:
    args = _parse_args()
    _validate_args(args)
    if args.dry_run:
        return _dry_run(args)
    _load_environment(args)
    return asyncio.run(_run_live(args))


if __name__ == "__main__":
    raise SystemExit(main())
