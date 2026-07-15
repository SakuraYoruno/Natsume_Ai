"""Prepare and execute the single Natsume AI V1 Core Release acceptance.

``--prepare`` is intentionally cheap: it freezes a draft manifest, SBOM and
rollback matrix without executing tests.  ``--accept`` is available only once
AW-08 has reached ``CODE_COMPLETE`` (or ``PROGRAM_ACCEPTANCE``) and then runs a
single, fail-fast acceptance plan.  No failed command is retried automatically.
An operator may explicitly continue a completed failed run with
``--accept --resume --run-id <existing-run>``.  A resume preserves the passed
prefix, archives the failed attempt, and starts again at the first failed stage.

All durable output owned by this runner stays under ``docs/50_release/core``.
Subprocesses receive the same run id and every outcome is recorded in one JSON
receipt, including failures and stages skipped after a failure.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RECEIPT_SCHEMA_VERSION = "natsume.core-release-acceptance-receipt.v2"
PREPARE_SCHEMA_VERSION = "natsume.core-release-prepare-receipt.v1"
ROLLBACK_MATRIX_SCHEMA_VERSION = "natsume.core-rollback-matrix.v1"
OUTPUT_RELATIVE = Path("docs/50_release/core")
MASTER_EXECUTION_RELATIVE = Path(
    "docs/30_devspec/NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md"
)
ROLLBACK_CONFIG_RELATIVE = Path("configs/release/core_rollback_map.json")
ARCHITECTURE_POLICY_RELATIVE = Path("configs/release/core_architecture_policy.json")
MANIFEST_NAME = "CORE_RELEASE_MANIFEST.json"
SBOM_NAME = "SBOM.cdx.json"
ROLLBACK_MATRIX_NAME = "CORE_ROLLBACK_MATRIX.json"
PREPARE_RECEIPT_NAME = "PREPARE_RECEIPT.json"
ACCEPTANCE_RECEIPT_NAME = "ACCEPTANCE_RECEIPT.json"
RUN_LOCK_NAME = ".acceptance.lock"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIPPED = "SKIPPED"
STATUS_DRAFT = "DRAFT"
CORE_RELEASE_GATE_COUNT = 15
_ACCEPTABLE_AW08_STATES = frozenset({"CODE_COMPLETE", "PROGRAM_ACCEPTANCE"})
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


@dataclass(frozen=True, slots=True)
class ArchitectureGateSpec:
    gate: int
    title: str
    required_stages: tuple[str, ...] = ()
    require_static: bool = True
    required_checks: tuple[str, ...] = ()


# Gate 1-14 never inherit truth from the repository-wide pytest stage.  Each
# one is bound to a named static result and, where behavior matters, a focused
# dynamic stage.  Gate 15 aggregates the Core Infrastructure release only;
# Universal Governed Runtime completeness is evaluated by the separate
# fail-closed universal_runtime_coverage gate.
ARCHITECTURE_GATE_SPECS: tuple[ArchitectureGateSpec, ...] = (
    ArchitectureGateSpec(1, "package dependency and Edge boundary", ("edge_standalone",)),
    ArchitectureGateSpec(2, "single composition root and app truth boundary"),
    ArchitectureGateSpec(3, "mixed-responsibility production file limits"),
    ArchitectureGateSpec(4, "single grant, lineage and side-effect receipt", ("authority_lineage_contracts", "contract_schema_contracts")),
    ArchitectureGateSpec(5, "verification commit guard", ("authority_lineage_contracts",)),
    ArchitectureGateSpec(6, "single writer and rebuildable projections", ("state_outbox_contracts", "contract_schema_contracts")),
    ArchitectureGateSpec(7, "zero-write surface and durable outbox", ("state_outbox_contracts",)),
    ArchitectureGateSpec(8, "knowledge namespace and promotion boundary", ("namespace_security_contracts",)),
    ArchitectureGateSpec(9, "authenticated and audited write ingress", ("namespace_security_contracts",)),
    ArchitectureGateSpec(10, "migration-only DDL and canonical persistence", ("persistence_boundary_contracts", "migration_backup_restore")),
    ArchitectureGateSpec(11, "single production services and signed bundle supply chain", ("service_bundle_contracts",)),
    ArchitectureGateSpec(12, "standalone Edge and unique physical-output authority", ("edge_standalone", "contract_schema_contracts")),
    ArchitectureGateSpec(13, "pure Operator query and generated UI client", ("operator_query_contracts", "contract_schema_contracts", "monitor_generated_client", "monitor_interaction_smoke")),
    ArchitectureGateSpec(
        14,
        "retired compatibility authorities",
        required_checks=("compatibility_registry_empty", "retired_paths_absent"),
    ),
    ArchitectureGateSpec(
        15,
        "core infrastructure program acceptance",
        (
            "offline_pytest",
            "contract_schema_contracts",
            "golden_replay_chaos_identity",
            "isolated_runtime_http_drill",
            "migration_backup_restore",
            "rollback_drill",
            "security_smoke",
            "performance_smoke",
            "edge_standalone",
            "monitor_interaction_smoke",
        ),
        require_static=False,
    ),
)


@dataclass(frozen=True, slots=True)
class AcceptanceStage:
    stage_id: str
    kind: str
    command: tuple[str, ...] = ()
    cwd: str = "."
    description: str = ""
    timeout_s: int = 1_800

    def __post_init__(self) -> None:
        if not self.stage_id or self.kind not in {"command", "internal"}:
            raise ValueError("invalid acceptance stage")
        if self.kind == "command" and not self.command:
            raise ValueError(f"command stage {self.stage_id} has no command")
        if self.timeout_s < 1:
            raise ValueError(f"acceptance stage {self.stage_id} has invalid timeout")


@dataclass(frozen=True, slots=True)
class StageOutcome:
    stage_id: str
    status: str
    started_at: str
    finished_at: str
    duration_ms: int
    exit_code: int | None
    command: tuple[str, ...]
    log_ref: str | None
    detail: str
    evidence: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["command"] = list(self.command)
        payload["evidence"] = dict(self.evidence or {})
        return payload


class InternalStageEvidenceError(RuntimeError):
    """Internal-stage failure carrying bounded, redacted child evidence."""

    def __init__(self, message: str, *, evidence: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.evidence = dict(evidence)


StageRunner = Callable[[AcceptanceStage, Path, Path, str], StageOutcome]
ReceiptUpdate = Callable[[dict[str, Any]], None]


def build_acceptance_plan(*, python_executable: str | None = None) -> tuple[AcceptanceStage, ...]:
    """Return the one complete, ordered Core Release acceptance plan.

    The first pytest invocation is the only repository-wide test run.  Later
    pytest stages are explicit release smokes and are never used as retries.
    """

    python = str(python_executable or sys.executable)
    monitor = "host_brain/app/monitor_ui"
    return (
        AcceptanceStage(
            "architecture_static_gates",
            "internal",
            description="fail-closed AST and repository-policy evaluation for Architecture Gates 1-14",
        ),
        AcceptanceStage(
            "offline_pytest",
            "command",
            (
                python,
                "-m",
                "pytest",
                "-m",
                "not hw and not prod_env and not runtime_probe and not docker",
            ),
            description="complete offline Python suite",
            timeout_s=7_200,
        ),
        AcceptanceStage(
            "authority_lineage_contracts",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/unit/test_aw04_governor_grant_binding.py",
                "tests/contract/test_nle_verification_gate_contract.py",
                "tests/unit/test_aw02_governed_task_coordinator.py",
            ),
            description="focused grant, task-attempt lineage, receipt and verification commit contracts",
        ),
        AcceptanceStage(
            "contract_schema_contracts",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/contract/test_nle_canonical_contracts.py",
                "tests/contract/test_nle_task_store_contract.py",
                "tests/contract/test_nle_runtime_event_contract.py",
                "tests/contract/test_nle_operator_read_model_contract.py",
                "tests/contract/test_aw06c_edge_transport_contract.py",
            ),
            description="versioned task, runtime-event, Operator and Edge schema contracts",
        ),
        AcceptanceStage(
            "state_outbox_contracts",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/unit/test_aw07_query_projection_spine.py",
                "tests/contract/test_obs_query_only_boundary.py",
                "tests/unit/test_aw02_governed_scheduler_runtime.py",
                "tests/unit/test_aw04_app_life_boundary.py",
            ),
            description="focused single-writer, projection rebuild, surface purity and durable outbox contracts",
        ),
        AcceptanceStage(
            "namespace_security_contracts",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/contract/test_pentest_knowledge_store_contract.py",
                "tests/contract/test_aw03_pentest_knowledge_repository.py",
                "tests/security/test_secure_ingress_identity_contract.py",
                "tests/security/test_obs_secure_command_ingress.py",
            ),
            description="focused namespace, promotion and authenticated write-ingress contracts",
        ),
        AcceptanceStage(
            "persistence_boundary_contracts",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/contract/test_aw03_global_sqlite_boundary.py",
                "tests/contract/test_aw03_observability_named_query_boundary.py",
                "tests/contract/test_aw03_secure_ingress_repository_boundary.py",
            ),
            description="focused inline-DDL, private-connection and repository-boundary contracts",
        ),
        AcceptanceStage(
            "service_bundle_contracts",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/contract/test_task0490_skill_supply_chain_migration_contract.py",
                "tests/security/test_skill_supply_chain.py",
                "tests/security/test_skill_supply_chain_quarantine.py",
                "tests/contract/test_sandbox_contract.py",
            ),
            description="focused single-service, signature, SBOM, quarantine, sandbox and rollback contracts",
        ),
        AcceptanceStage(
            "operator_query_contracts",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/contract/test_obs_query_only_boundary.py",
                "tests/contract/test_aw07_operator_query_privacy_contract.py",
            ),
            description="focused query-purity and privacy contracts",
        ),
        AcceptanceStage(
            "golden_replay_chaos_identity",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/e2e/test_task0495_m48b_p0_golden_fault_matrix.py",
                "tests/contract/test_replay_tool_contract.py",
                "tests/contract/test_nle_identity_snapshot_contract.py",
                "tests/integration/test_dialogue_state_repeat_identity.py",
            ),
            description="dedicated golden, replay, chaos/fault and identity regression evidence",
        ),
        AcceptanceStage(
            "monitor_generated_client",
            "command",
            ("npm", "run", "check:operator-client"),
            cwd=monitor,
            description="generated Operator client drift gate",
        ),
        AcceptanceStage(
            "monitor_typecheck",
            "command",
            ("npm", "run", "typecheck"),
            cwd=monitor,
            description="Monitor TypeScript contract check",
        ),
        AcceptanceStage(
            "monitor_operator_client_tests",
            "command",
            ("npm", "run", "test:operator-client"),
            cwd=monitor,
            description="Monitor generated command client tests",
        ),
        AcceptanceStage(
            "monitor_read_model_tests",
            "command",
            ("npm", "run", "test:read-model"),
            cwd=monitor,
            description="Monitor projection read-model tests",
        ),
        AcceptanceStage(
            "monitor_build",
            "command",
            ("npm", "run", "build"),
            cwd=monitor,
            description="Monitor production build",
        ),
        AcceptanceStage(
            "monitor_interaction_smoke",
            "internal",
            description="unstubbed browser interaction through Monitor preview proxy to a real isolated Obs process",
        ),
        AcceptanceStage(
            "edge_standalone",
            "command",
            (python, "-m", "pytest", "tests/contract/test_aw06_edge_standalone_package.py"),
            description="Edge installs/imports without Host implementation",
        ),
        AcceptanceStage(
            "migration_backup_restore",
            "internal",
            description="empty-database migration and verified offline backup/restore",
        ),
        AcceptanceStage(
            "isolated_runtime_http_drill",
            "internal",
            description="real isolated Obs process, migrated database, HTTP cold-start and crash/restart drill",
            timeout_s=180,
        ),
        AcceptanceStage(
            "rollback_drill",
            "internal",
            description="rollback matrix validation and controlled rollback-conflict drill",
        ),
        AcceptanceStage(
            "security_smoke",
            "command",
            (python, "-m", "pytest", "tests/security"),
            description="release security smoke",
        ),
        AcceptanceStage(
            "performance_smoke",
            "command",
            (
                python,
                "-m",
                "pytest",
                "tests/integration/test_performance_budget.py",
                "tests/contract/test_perf_baseline_contract.py",
                "tests/contract/test_timeline_perf_contract.py",
            ),
            description="release performance budget smoke",
        ),
    )


def prepare_release(*, root: str | Path, run_id: str | None = None) -> dict[str, Any]:
    """Generate draft release artifacts without running acceptance commands."""

    from host_brain.release.compatibility_registry import (
        RETIRED_CORE_PATHS,
        validate_registry,
        validate_retired_paths_absent,
    )
    from host_brain.release.release_manifest import generate_core_release_manifest, generate_core_sbom

    root_path = Path(root).resolve()
    output = _output_root(root_path)
    resolved_run_id = _normalize_run_id(run_id or _new_run_id(root_path, prefix="prepare"))
    generated_at = _utc_now()
    compatibility = validate_registry(root_path, require_empty=True)
    retired_paths = validate_retired_paths_absent(root_path, RETIRED_CORE_PATHS)
    rollback = _generate_rollback_matrix(root_path, run_id=resolved_run_id, generated_at=generated_at)
    sbom = generate_core_sbom(root=root_path)
    gates = _architecture_gate_rows(
        status="PENDING",
        run_id=resolved_run_id,
        receipt_ref=f"{OUTPUT_RELATIVE.as_posix()}/{PREPARE_RECEIPT_NAME}",
    )
    manifest = generate_core_release_manifest(
        root=root_path,
        architecture_gates=gates,
        acceptance_run_id="",
        acceptance_result_ref="",
    )
    manifest["release_status"] = STATUS_DRAFT
    receipt = {
        "schema_version": PREPARE_SCHEMA_VERSION,
        "status": (
            STATUS_DRAFT
            if compatibility.get("status") == STATUS_PASS
            and retired_paths.get("status") == STATUS_PASS
            else STATUS_FAIL
        ),
        "run_id": resolved_run_id,
        "generated_at": generated_at,
        "acceptance_executed": False,
        "compatibility_registry": compatibility,
        "retired_paths": retired_paths,
        "artifacts": {
            "manifest_ref": _repo_ref(root_path, output / MANIFEST_NAME),
            "sbom_ref": _repo_ref(root_path, output / SBOM_NAME),
            "rollback_matrix_ref": _repo_ref(root_path, output / ROLLBACK_MATRIX_NAME),
        },
    }
    _atomic_write_json(output / SBOM_NAME, sbom)
    _atomic_write_json(output / ROLLBACK_MATRIX_NAME, rollback)
    _atomic_write_json(output / MANIFEST_NAME, manifest)
    _atomic_write_json(output / PREPARE_RECEIPT_NAME, receipt)
    return receipt


def run_acceptance_plan(
    *,
    root: str | Path,
    run_id: str,
    run_dir: str | Path,
    stages: Sequence[AcceptanceStage] | None = None,
    stage_runner: StageRunner | None = None,
    on_update: ReceiptUpdate | None = None,
) -> dict[str, Any]:
    """Run every stage once, stopping after the first failure.

    This function is intentionally injectable so its e2e contract can prove
    ordering, receipt semantics and no-retry behavior without recursively
    starting the repository-wide pytest suite.
    """

    root_path = Path(root).resolve()
    run_path = Path(run_dir).resolve()
    run_path.mkdir(parents=True, exist_ok=True)
    resolved_run_id = _normalize_run_id(run_id)
    plan = tuple(stages or build_acceptance_plan())
    runner = stage_runner or _default_stage_runner
    started_at = _utc_now()
    outcomes: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "status": "RUNNING",
        "started_at": started_at,
        "finished_at": None,
        "source_commit": _git_commit(root_path),
        "stage_count": len(plan),
        "stages": outcomes,
        "stage_source_commits": {},
        "automatic_retry_count": 0,
    }
    _notify(on_update, receipt)
    failed = False
    for index, stage in enumerate(plan):
        if failed:
            outcome = _skipped_outcome(stage, "skipped after an earlier acceptance failure")
        else:
            try:
                outcome = runner(stage, root_path, run_path, resolved_run_id)
            except BaseException as exc:  # CLI boundary must preserve a receipt.
                now = _utc_now()
                outcome = StageOutcome(
                    stage_id=stage.stage_id,
                    status=STATUS_FAIL,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    exit_code=None,
                    command=stage.command,
                    log_ref=None,
                    detail=f"stage runner raised {type(exc).__name__}: {exc}",
                )
            if outcome.stage_id != stage.stage_id:
                now = _utc_now()
                outcome = StageOutcome(
                    stage_id=stage.stage_id,
                    status=STATUS_FAIL,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    exit_code=None,
                    command=stage.command,
                    log_ref=None,
                    detail="stage runner returned an outcome for a different stage",
                )
            if outcome.status != STATUS_PASS:
                failed = True
        outcomes.append(outcome.as_dict())
        if outcome.status in {STATUS_PASS, STATUS_FAIL} and receipt["source_commit"]:
            receipt["stage_source_commits"][stage.stage_id] = receipt["source_commit"]
        receipt["completed_stage_count"] = index + 1
        receipt["status"] = STATUS_FAIL if failed else "RUNNING"
        _notify(on_update, receipt)
    receipt["status"] = STATUS_FAIL if failed else STATUS_PASS
    receipt["finished_at"] = _utc_now()
    receipt["passed_stage_count"] = sum(row["status"] == STATUS_PASS for row in outcomes)
    receipt["failed_stage_count"] = sum(row["status"] == STATUS_FAIL for row in outcomes)
    receipt["skipped_stage_count"] = sum(row["status"] == STATUS_SKIPPED for row in outcomes)
    _notify(on_update, receipt)
    return receipt


def resume_acceptance_plan(
    *,
    root: str | Path,
    run_id: str,
    run_dir: str | Path,
    prior_receipt: Mapping[str, Any],
    stages: Sequence[AcceptanceStage] | None = None,
    stage_runner: StageRunner | None = None,
    on_update: ReceiptUpdate | None = None,
    source_commit: str | None = None,
    revalidate_pass_prefix_on_source_change: bool = False,
) -> dict[str, Any]:
    """Explicitly continue one failed fail-fast plan without replaying PASS stages.

    The prior receipt must describe exactly one completed fail-fast attempt:
    a PASS prefix, one FAIL stage, and a SKIPPED suffix.  The failed stage is
    executed again because the operator explicitly requested ``--resume``;
    this is recorded separately and never counted as an automatic retry.  A
    different committed HEAD is allowed and stage-to-commit provenance remains
    explicit; the public ``accept_release`` boundary separately rejects a dirty
    worktree before calling this low-level plan executor.
    """

    root_path = Path(root).resolve()
    run_path = Path(run_dir).resolve()
    _require_under(root_path, run_path)
    if not run_path.is_dir():
        raise RuntimeError(f"Core Release resume run directory does not exist: {run_path}")
    resolved_run_id = _normalize_run_id(run_id)
    plan = tuple(stages or build_acceptance_plan())
    previous = json.loads(json.dumps(dict(prior_receipt), ensure_ascii=False))
    failed_index = _validate_resume_receipt(
        previous,
        run_id=resolved_run_id,
        plan=plan,
    )
    _validate_resume_evidence_paths(
        root=root_path,
        run_dir=run_path,
        stages=previous["stages"],
    )
    current_commit = str(source_commit or _git_commit(root_path) or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", current_commit):
        raise RuntimeError("Core Release resume requires a committed Git HEAD")
    previous_source_commit = str(previous["source_commit"]).lower()
    restart_index = (
        0
        if revalidate_pass_prefix_on_source_change
        and current_commit != previous_source_commit
        else failed_index
    )

    previous_history = previous.get("resume_history", [])
    if not isinstance(previous_history, list) or any(
        not isinstance(row, Mapping) for row in previous_history
    ):
        raise ValueError("Core Release resume_history is invalid")
    for expected_attempt, row in enumerate(previous_history, start=1):
        if row.get("attempt_number") != expected_attempt:
            raise ValueError("Core Release resume_history attempt sequence is invalid")
        if str(row.get("status") or "").upper() != STATUS_FAIL:
            raise ValueError("Core Release resume_history contains a non-FAIL attempt")
        if not re.fullmatch(r"[0-9a-f]{40}", str(row.get("source_commit") or "").lower()):
            raise ValueError("Core Release resume_history contains an invalid source_commit")
        if str(row.get("failed_stage_id") or "") not in {stage.stage_id for stage in plan}:
            raise ValueError("Core Release resume_history contains an invalid failed stage")
    previous_resume_count = previous.get("resume_count", len(previous_history))
    if type(previous_resume_count) is not int or previous_resume_count < 0:
        raise ValueError("Core Release resume_count is invalid")
    if previous_resume_count != len(previous_history):
        raise ValueError("Core Release resume_count does not match resume_history")
    attempt_number = previous_resume_count + 1

    previous_stages = json.loads(json.dumps(previous["stages"], ensure_ascii=False))
    previous_stage_sources = previous.get("stage_source_commits", {})
    if not isinstance(previous_stage_sources, Mapping):
        raise ValueError("Core Release stage_source_commits is invalid")
    stage_source_commits = {
        str(stage_id): str(commit).lower()
        for stage_id, commit in previous_stage_sources.items()
    }
    plan_stage_ids = {stage.stage_id for stage in plan}
    for stage_id, commit in stage_source_commits.items():
        if stage_id not in plan_stage_ids or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Core Release stage_source_commits contains invalid provenance")
    for row in previous_stages:
        if row.get("status") in {STATUS_PASS, STATUS_FAIL}:
            stage_source_commits.setdefault(str(row["stage_id"]), previous_source_commit)

    archived_receipt_path = run_path / f"acceptance_receipt.attempt-{attempt_number}.json"
    if archived_receipt_path.exists():
        raise RuntimeError(f"Core Release resume archive already exists: {archived_receipt_path}")
    archived_stage_logs: dict[str, str] = {}
    for stage_index in range(restart_index, failed_index + 1):
        archived_ref = _archive_stage_log(
            root=root_path,
            run_dir=run_path,
            stage_row=previous_stages[stage_index],
            attempt_number=attempt_number,
        )
        if archived_ref is not None:
            previous_stages[stage_index]["log_ref"] = archived_ref
            archived_stage_logs[plan[stage_index].stage_id] = archived_ref
    archived_log_ref = archived_stage_logs.get(plan[failed_index].stage_id)
    archived_log_sha256 = (
        _sha256((root_path / Path(archived_log_ref)).resolve())
        if archived_log_ref is not None
        else None
    )

    archived_receipt = json.loads(json.dumps(previous, ensure_ascii=False))
    archived_receipt["stages"] = previous_stages
    archived_receipt["archived_for_resume_at"] = _utc_now()
    archived_receipt["archived_failed_log_sha256"] = archived_log_sha256
    _atomic_write_json(archived_receipt_path, archived_receipt)

    history_entry = {
        "attempt_number": attempt_number,
        "status": STATUS_FAIL,
        "source_commit": str(previous["source_commit"]),
        "started_at": previous.get("started_at"),
        "finished_at": previous.get("finished_at"),
        "failed_stage_id": plan[failed_index].stage_id,
        "failed_stage_log_ref": archived_log_ref,
        "failed_stage_log_sha256": archived_log_sha256,
        "restart_stage_id": plan[restart_index].stage_id,
        "revalidated_pass_prefix": restart_index < failed_index,
        "archived_stage_logs": archived_stage_logs,
        "receipt_ref": _repo_ref(root_path, archived_receipt_path),
        "stages": previous_stages,
        "stage_source_commits": dict(stage_source_commits),
    }
    history = json.loads(json.dumps(previous_history, ensure_ascii=False))
    history.append(history_entry)

    resumed_at = _utc_now()
    active_stages = previous_stages[:restart_index]
    active_stages.extend(
        _skipped_outcome(stage, f"pending explicit resume attempt {attempt_number}").as_dict()
        for stage in plan[restart_index:]
    )
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "status": "RUNNING",
        "started_at": previous.get("started_at"),
        "finished_at": None,
        "source_commit": current_commit,
        "initial_source_commit": str(
            previous.get("initial_source_commit") or previous["source_commit"]
        ),
        "resumed_from_source_commit": str(previous["source_commit"]),
        "resume_started_at": resumed_at,
        "resume_count": attempt_number,
        "resume_history": history,
        "stage_count": len(plan),
        "stages": active_stages,
        "stage_source_commits": {
            stage.stage_id: stage_source_commits[stage.stage_id]
            for stage in plan[:restart_index]
        },
        "completed_stage_count": restart_index,
        "automatic_retry_count": 0,
    }
    _set_stage_counts(receipt)
    _notify(on_update, receipt)

    runner = stage_runner or _default_stage_runner
    failed = False
    for index in range(restart_index, len(plan)):
        stage = plan[index]
        execute_stage = not failed
        if not execute_stage:
            outcome = _skipped_outcome(stage, "skipped after a failure in this explicit resume attempt")
        else:
            try:
                outcome = runner(stage, root_path, run_path, resolved_run_id)
            except BaseException as exc:  # CLI boundary must preserve a receipt.
                now = _utc_now()
                outcome = StageOutcome(
                    stage_id=stage.stage_id,
                    status=STATUS_FAIL,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    exit_code=None,
                    command=stage.command,
                    log_ref=None,
                    detail=f"stage runner raised {type(exc).__name__}: {exc}",
                )
            if outcome.stage_id != stage.stage_id:
                now = _utc_now()
                outcome = StageOutcome(
                    stage_id=stage.stage_id,
                    status=STATUS_FAIL,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    exit_code=None,
                    command=stage.command,
                    log_ref=None,
                    detail="stage runner returned an outcome for a different stage",
                )
            elif outcome.status not in {STATUS_PASS, STATUS_FAIL}:
                now = _utc_now()
                outcome = StageOutcome(
                    stage_id=stage.stage_id,
                    status=STATUS_FAIL,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    exit_code=None,
                    command=stage.command,
                    log_ref=None,
                    detail=f"stage runner returned invalid status: {outcome.status}",
                )
            if outcome.status != STATUS_PASS:
                failed = True
        active_stages[index] = outcome.as_dict()
        if execute_stage:
            receipt["stage_source_commits"][stage.stage_id] = current_commit
        receipt["completed_stage_count"] = index + 1
        receipt["status"] = STATUS_FAIL if failed else "RUNNING"
        _set_stage_counts(receipt)
        _notify(on_update, receipt)

    receipt["status"] = STATUS_FAIL if failed else STATUS_PASS
    receipt["finished_at"] = _utc_now()
    _set_stage_counts(receipt)
    _notify(on_update, receipt)
    return receipt


def _validate_resume_receipt(
    receipt: Mapping[str, Any],
    *,
    run_id: str,
    plan: Sequence[AcceptanceStage],
) -> int:
    """Validate a durable fail-fast receipt and return its sole FAIL index."""

    if str(receipt.get("schema_version") or "") != RECEIPT_SCHEMA_VERSION:
        raise ValueError("Core Release resume receipt schema is unsupported")
    if str(receipt.get("run_id") or "") != run_id:
        raise ValueError("Core Release resume receipt run_id does not match --run-id")
    if str(receipt.get("status") or "").upper() != STATUS_FAIL:
        raise ValueError("Core Release resume requires an existing FAIL receipt")
    source_commit = str(receipt.get("source_commit") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Core Release resume receipt has no valid source_commit")
    if receipt.get("finished_at") in {None, ""}:
        raise ValueError("Core Release resume receipt is not a finished fail-fast attempt")
    if receipt.get("started_at") in {None, ""}:
        raise ValueError("Core Release resume receipt has no started_at timestamp")
    if receipt.get("automatic_retry_count") != 0:
        raise ValueError("Core Release resume receipt reports an automatic retry")

    rows = receipt.get("stages")
    if not isinstance(rows, list) or len(rows) != len(plan):
        raise ValueError("Core Release resume receipt stage count does not match the current plan")
    if receipt.get("stage_count") != len(plan):
        raise ValueError("Core Release resume receipt stage_count is invalid")
    if receipt.get("completed_stage_count") != len(plan):
        raise ValueError("Core Release resume receipt is not a completed fail-fast plan")

    failed_indexes: list[int] = []
    for index, (row, stage) in enumerate(zip(rows, plan, strict=True)):
        if not isinstance(row, Mapping):
            raise ValueError(f"Core Release resume stage {index} is not an object")
        if str(row.get("stage_id") or "") != stage.stage_id:
            raise ValueError("Core Release resume receipt stage order does not match the current plan")
        command = row.get("command")
        if not isinstance(command, list) or tuple(str(value) for value in command) != stage.command:
            raise ValueError(f"Core Release resume stage command drifted: {stage.stage_id}")
        status = str(row.get("status") or "").upper()
        if status not in {STATUS_PASS, STATUS_FAIL, STATUS_SKIPPED}:
            raise ValueError(f"Core Release resume stage has invalid status: {stage.stage_id}")
        if status == STATUS_FAIL:
            failed_indexes.append(index)
    if len(failed_indexes) != 1:
        raise ValueError("Core Release resume receipt must contain exactly one FAIL stage")
    failed_index = failed_indexes[0]
    statuses = [str(row["status"]).upper() for row in rows]
    if statuses[:failed_index] != [STATUS_PASS] * failed_index:
        raise ValueError("Core Release resume receipt must have only PASS stages before FAIL")
    if statuses[failed_index + 1 :] != [STATUS_SKIPPED] * (len(plan) - failed_index - 1):
        raise ValueError("Core Release resume receipt must have only SKIPPED stages after FAIL")

    expected_counts = {
        "passed_stage_count": statuses.count(STATUS_PASS),
        "failed_stage_count": 1,
        "skipped_stage_count": statuses.count(STATUS_SKIPPED),
    }
    for key, expected in expected_counts.items():
        if receipt.get(key) != expected:
            raise ValueError(f"Core Release resume receipt {key} is inconsistent")
    return failed_index


def _archive_stage_log(
    *,
    root: Path,
    run_dir: Path,
    stage_row: Mapping[str, Any],
    attempt_number: int,
) -> str | None:
    log_ref = str(stage_row.get("log_ref") or "").strip()
    if not log_ref:
        return None
    source = (root / Path(log_ref)).resolve()
    _require_under(run_dir, source)
    if not source.is_file():
        raise RuntimeError(f"Core Release stage evidence log is missing: {source}")
    suffix = source.suffix or ".log"
    stage_id = str(stage_row.get("stage_id") or "")
    if not _RUN_ID_RE.fullmatch(stage_id):
        raise ValueError("Core Release stage id is unsafe for evidence archival")
    destination = run_dir / f"{stage_id}.attempt-{attempt_number}{suffix}"
    if destination.exists():
        raise RuntimeError(f"Core Release failed-stage evidence archive already exists: {destination}")
    shutil.copy2(source, destination)
    return _repo_ref(root, destination)


def _validate_resume_evidence_paths(
    *,
    root: Path,
    run_dir: Path,
    stages: Sequence[Mapping[str, Any]],
) -> None:
    for row in stages:
        log_ref = str(row.get("log_ref") or "").strip()
        if not log_ref:
            continue
        log_path = (root / Path(log_ref)).resolve()
        _require_under(run_dir, log_path)
        if not log_path.is_file():
            raise RuntimeError(f"Core Release stage evidence log is missing: {log_path}")


def _set_stage_counts(receipt: dict[str, Any]) -> None:
    rows = list(receipt.get("stages") or [])
    receipt["passed_stage_count"] = sum(row.get("status") == STATUS_PASS for row in rows)
    receipt["failed_stage_count"] = sum(row.get("status") == STATUS_FAIL for row in rows)
    receipt["skipped_stage_count"] = sum(row.get("status") == STATUS_SKIPPED for row in rows)


def accept_release(
    *,
    root: str | Path,
    run_id: str | None = None,
    resume: bool = False,
    stages: Sequence[AcceptanceStage] | None = None,
    stage_runner: StageRunner | None = None,
) -> dict[str, Any]:
    """Execute or explicitly resume Core Release acceptance and finalize artifacts."""

    if resume and not run_id:
        raise ValueError("--resume requires both --accept and an explicit --run-id")

    from host_brain.release.compatibility_registry import (
        RETIRED_CORE_PATHS,
        validate_registry,
        validate_retired_paths_absent,
    )
    from host_brain.release.release_manifest import (
        generate_core_release_manifest,
        generate_core_sbom,
        validate_core_release_manifest,
    )

    root_path = Path(root).resolve()
    output = _output_root(root_path)
    state = read_aw08_state(root_path)
    if state not in _ACCEPTABLE_AW08_STATES:
        raise RuntimeError(
            f"AW-08 must be CODE_COMPLETE before acceptance; current state is {state or '<missing>'}"
        )
    compatibility = validate_registry(root_path, require_empty=True)
    if compatibility.get("status") != STATUS_PASS:
        raise RuntimeError(f"compatibility registry is not release-clean: {compatibility}")
    retired_paths = validate_retired_paths_absent(root_path, RETIRED_CORE_PATHS)
    if retired_paths.get("status") != STATUS_PASS:
        raise RuntimeError(f"retired Core paths are still present: {retired_paths}")

    resolved_run_id = _normalize_run_id(run_id or _new_run_id(root_path, prefix="accept"))
    run_dir = (output / "runs" / resolved_run_id).resolve()
    _require_under(output, run_dir)
    plan = tuple(stages or build_acceptance_plan())
    receipt_path = run_dir / "acceptance_receipt.json"
    prior_receipt: dict[str, Any] | None = None
    if resume:
        if not run_dir.is_dir():
            raise RuntimeError(f"Core Release resume run does not exist: {run_dir}")
        if not receipt_path.is_file():
            raise RuntimeError(f"Core Release resume receipt does not exist: {receipt_path}")
        try:
            decoded = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Core Release resume receipt is unreadable: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ValueError("Core Release resume receipt must be a JSON object")
        prior_receipt = decoded
        _validate_resume_receipt(prior_receipt, run_id=resolved_run_id, plan=plan)
    else:
        run_dir.mkdir(parents=True, exist_ok=False)
    generated_at = _utc_now()
    rollback = _generate_rollback_matrix(root_path, run_id=resolved_run_id, generated_at=generated_at)
    sbom = generate_core_sbom(root=root_path)
    draft = generate_core_release_manifest(
        root=root_path,
        architecture_gates=_architecture_gate_rows(
            status="PENDING",
            run_id=resolved_run_id,
            receipt_ref=_repo_ref(root_path, run_dir / "acceptance_receipt.json"),
        ),
    )
    if list(dict(draft.get("git") or {}).get("dirty_paths") or []):
        action = "resume" if resume else "acceptance"
        raise RuntimeError(
            f"Core Release {action} requires a clean committed tree; commit the repair and "
            f"existing receipt evidence first: {draft['git']['dirty_paths'][:10]}"
        )

    _atomic_write_json(output / SBOM_NAME, sbom)
    _atomic_write_json(output / ROLLBACK_MATRIX_NAME, rollback)
    lock = output / RUN_LOCK_NAME
    _acquire_lock(lock, run_id=resolved_run_id, source_commit=str(draft["git"]["commit"]))

    def persist_progress(payload: dict[str, Any]) -> None:
        snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        snapshot["program_acceptance_finalized"] = False
        if snapshot.get("status") == STATUS_PASS:
            snapshot["status"] = "STAGES_PASS_PENDING_MANIFEST"
        _atomic_write_json(receipt_path, snapshot)
        _atomic_write_json(output / ACCEPTANCE_RECEIPT_NAME, snapshot)

    def persist_final(payload: dict[str, Any]) -> None:
        snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        snapshot["program_acceptance_finalized"] = True
        _atomic_write_json(receipt_path, snapshot)
        _atomic_write_json(output / ACCEPTANCE_RECEIPT_NAME, snapshot)

    try:
        if resume:
            assert prior_receipt is not None
            receipt = resume_acceptance_plan(
                root=root_path,
                run_id=resolved_run_id,
                run_dir=run_dir,
                prior_receipt=prior_receipt,
                stages=plan,
                stage_runner=stage_runner,
                on_update=persist_progress,
                source_commit=str(draft["git"]["commit"]),
                revalidate_pass_prefix_on_source_change=True,
            )
        else:
            receipt = run_acceptance_plan(
                root=root_path,
                run_id=resolved_run_id,
                run_dir=run_dir,
                stages=plan,
                stage_runner=stage_runner,
                on_update=persist_progress,
            )
        receipt["aw08_state_at_start"] = state
        receipt["compatibility_registry"] = compatibility
        receipt["retired_paths"] = retired_paths
        receipt["artifacts"] = {
            "manifest_ref": _repo_ref(root_path, output / MANIFEST_NAME),
            "sbom_ref": _repo_ref(root_path, output / SBOM_NAME),
            "rollback_matrix_ref": _repo_ref(root_path, output / ROLLBACK_MATRIX_NAME),
            "receipt_ref": _repo_ref(root_path, receipt_path),
        }
        try:
            gates = _evaluate_architecture_gates(
                receipt=receipt,
                compatibility=compatibility,
                retired_paths=retired_paths,
                run_id=resolved_run_id,
                receipt_ref=_repo_ref(root_path, receipt_path),
            )
            receipt["architecture_gates"] = gates
            failed_gates = [row["gate"] for row in gates if row["status"] != STATUS_PASS]
            if failed_gates:
                receipt["status"] = STATUS_FAIL
                receipt["architecture_gate_failures"] = failed_gates
            if receipt["status"] == STATUS_PASS:
                manifest = generate_core_release_manifest(
                    root=root_path,
                    architecture_gates=gates,
                    acceptance_run_id=resolved_run_id,
                    acceptance_result_ref=_repo_ref(root_path, receipt_path),
                )
                manifest["release_status"] = "PROGRAM_ACCEPTANCE_PASS"
                validation = validate_core_release_manifest(
                    root=root_path,
                    manifest=manifest,
                    require_acceptance=True,
                )
                receipt["manifest_validation"] = validation
                if validation.get("status") != STATUS_PASS:
                    receipt["status"] = STATUS_FAIL
                _atomic_write_json(output / MANIFEST_NAME, manifest)
            else:
                receipt["manifest_validation"] = {
                    "status": STATUS_SKIPPED,
                    "issues": [
                        "acceptance stages and all 15 independently evaluated architecture gates did not all pass"
                    ],
                }
        except BaseException as exc:
            receipt["status"] = STATUS_FAIL
            receipt["manifest_validation"] = {
                "status": STATUS_FAIL,
                "issues": [f"release finalization raised {type(exc).__name__}: {exc}"],
            }
        persist_final(receipt)
        return receipt
    finally:
        lock.unlink(missing_ok=True)


def read_aw08_state(root: str | Path) -> str | None:
    path = Path(root).resolve() / MASTER_EXECUTION_RELATIVE
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(
        r"^### AW-08[^\n]*\n\s*\n状态：`([^`]+)`",
        text,
        flags=re.MULTILINE,
    )
    return str(match.group(1)).strip() if match else None


def _resolve_subprocess_command(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Resolve an executable before launch, including Windows ``.cmd`` shims."""

    if not command or not str(command[0]).strip():
        raise ValueError("acceptance subprocess command has no executable")
    requested = str(command[0]).strip()
    search_path = None if environment is None else environment.get("PATH")
    resolved = shutil.which(requested, path=search_path)
    if not resolved:
        raise FileNotFoundError(
            f"acceptance executable could not be resolved with shutil.which: {requested}"
        )
    return (str(Path(resolved).resolve()), *(str(argument) for argument in command[1:]))


def _create_windows_kill_on_close_job(process: subprocess.Popen[Any]) -> Any | None:
    """Contain a credential-bearing Windows process and all of its children.

    Closing the returned Job Object kills every process still attached to the
    job.  This is stronger than terminating an ``npm.cmd`` wrapper: Playwright,
    Vite and Chromium are grandchildren and otherwise survive a wrapper
    timeout on Windows.
    """

    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    information = _JobObjectExtendedLimitInformation()
    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    information.BasicLimitInformation.LimitFlags = 0x00002000
    configured = kernel32.SetInformationJobObject(
        job,
        9,  # JobObjectExtendedLimitInformation
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    process_handle_value = int(getattr(process, "_handle", 0) or 0)
    assigned = bool(
        configured
        and process_handle_value
        and kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process_handle_value))
    )
    if not assigned:
        kernel32.CloseHandle(job)
        return None
    return job


def _terminate_contained_process_tree(
    process: subprocess.Popen[Any],
    *,
    windows_job: Any | None,
) -> None:
    """Best-effort, bounded process-tree cleanup for release subprocesses."""

    if os.name == "nt":
        if windows_job is not None:
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
                kernel32.CloseHandle.restype = wintypes.BOOL
                kernel32.CloseHandle(windows_job)
            except BaseException:
                pass
        if process.poll() is None:
            taskkill = (
                Path(os.environ.get("SystemRoot") or r"C:\Windows")
                / "System32"
                / "taskkill.exe"
            )
            try:
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
            except BaseException:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if process.poll() is None:
        try:
            process.wait(timeout=10)
        except BaseException:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except BaseException:
                pass
            try:
                process.wait(timeout=5)
            except BaseException:
                pass


def _run_subprocess_with_tree_cleanup(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess whose descendants cannot outlive the acceptance stage."""

    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_options,
    )
    windows_job = _create_windows_kill_on_close_job(process)
    if os.name == "nt" and windows_job is None:
        _terminate_contained_process_tree(process, windows_job=None)
        raise RuntimeError(
            "could not contain Windows acceptance subprocess in a kill-on-close Job Object"
        )

    cleaned = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _terminate_contained_process_tree(process, windows_job=windows_job)
        cleaned = True
        try:
            stdout, stderr = process.communicate(timeout=5)
        except BaseException:
            stdout = str(exc.output or "")
            stderr = str(exc.stderr or "")
        raise subprocess.TimeoutExpired(
            list(command),
            timeout_s,
            output=stdout,
            stderr=stderr,
        ) from exc
    finally:
        if not cleaned:
            _terminate_contained_process_tree(process, windows_job=windows_job)

    return subprocess.CompletedProcess(
        args=list(command),
        returncode=int(process.returncode or 0),
        stdout=str(stdout or ""),
        stderr=str(stderr or ""),
    )


def _redact_child_log_tail(value: str) -> str:
    text = re.sub(
        r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+",
        r"\1 <redacted>",
        str(value),
    )
    text = re.sub(
        r"(?i)\b(authorization|api[_-]?key|access[_-]?token|token|secret|password)"
        r"(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2<redacted>",
        text,
    )
    text = re.sub(r"(?i)(://)[^/@\s:]+:[^/@\s]+@", r"\1<redacted>@", text)
    return "".join(character if character in "\n\r\t" or ord(character) >= 32 else "?" for character in text)


def _bounded_text_evidence(value: str, *, max_tail_chars: int = 2_000) -> dict[str, Any]:
    raw = str(value).encode("utf-8", errors="replace")
    tail = str(value)[-max(1, int(max_tail_chars)) :]
    return {
        "content_sha256": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "size_bytes": len(raw),
        "tail_redacted": _redact_child_log_tail(tail),
        "tail_truncated": len(str(value)) > max(1, int(max_tail_chars)),
    }


def _bounded_child_log_evidence(path: Path, *, max_tail_bytes: int = 4_000) -> dict[str, Any]:
    if not path.is_file():
        return {
            "available": False,
            "content_sha256": None,
            "size_bytes": 0,
            "tail_redacted": "",
            "tail_truncated": False,
        }
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - max(1, int(max_tail_bytes))))
        tail = handle.read().decode("utf-8", errors="replace")
    return {
        "available": True,
        "content_sha256": _sha256(path),
        "size_bytes": size,
        "tail_redacted": _redact_child_log_tail(tail),
        "tail_truncated": size > max(1, int(max_tail_bytes)),
    }


def _default_stage_runner(stage: AcceptanceStage, root: Path, run_dir: Path, run_id: str) -> StageOutcome:
    started_at = _utc_now()
    started = time.monotonic()
    log_path = run_dir / f"{stage.stage_id}.log"
    if stage.kind == "internal":
        try:
            if stage.stage_id == "architecture_static_gates":
                evidence = _run_architecture_static_gates(root=root)
            elif stage.stage_id == "migration_backup_restore":
                evidence = _run_isolated_migration_backup_restore(root=root, run_id=run_id)
            elif stage.stage_id == "isolated_runtime_http_drill":
                evidence = _run_isolated_runtime_http_drill(root=root, run_id=run_id)
            elif stage.stage_id == "monitor_interaction_smoke":
                evidence = _run_monitor_ui_interaction_smoke(root=root, run_id=run_id)
            elif stage.stage_id == "rollback_drill":
                evidence = _run_rollback_drill(root=root, run_id=run_id)
            else:
                raise ValueError(f"unknown internal release stage: {stage.stage_id}")
            if str(evidence.get("status") or "").upper() != STATUS_PASS:
                detail = json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                _atomic_write_text(log_path, detail)
                return _outcome(
                    stage,
                    status=STATUS_FAIL,
                    started_at=started_at,
                    started=started,
                    exit_code=1,
                    log_ref=_repo_ref(root, log_path),
                    detail="internal acceptance stage returned fail-closed evidence",
                    evidence=evidence,
                )
            detail = json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            _atomic_write_text(log_path, detail)
            return _outcome(
                stage,
                status=STATUS_PASS,
                started_at=started_at,
                started=started,
                exit_code=0,
                log_ref=_repo_ref(root, log_path),
                detail="internal acceptance stage passed",
                evidence=evidence,
            )
        except BaseException as exc:
            failure_evidence = (
                dict(exc.evidence)
                if isinstance(exc, InternalStageEvidenceError)
                else {}
            )
            _atomic_write_text(
                log_path,
                json.dumps(
                    {
                        "error_type": type(exc).__name__,
                        "detail": str(exc),
                        "evidence": failure_evidence,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
            )
            return _outcome(
                stage,
                status=STATUS_FAIL,
                started_at=started_at,
                started=started,
                exit_code=1,
                log_ref=_repo_ref(root, log_path),
                detail=f"internal acceptance stage failed: {type(exc).__name__}: {exc}",
                evidence=failure_evidence,
            )

    cwd = (root / stage.cwd).resolve()
    _require_under(root, cwd)
    environment = dict(os.environ)
    # The Monitor Playwright configuration retries when CI is truthy.  Core
    # Release owns retry policy centrally and never repeats a failed stage.
    environment.pop("CI", None)
    environment.update(
        {
            "NATSUME_CORE_RELEASE_RUN_ID": run_id,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
        }
    )
    print(f"[core-release:{run_id}] START {stage.stage_id}", flush=True)
    resolution_evidence: dict[str, Any] = {
        "method": "shutil.which",
        "requested_executable": str(stage.command[0]),
        "resolved_executable": None,
    }
    try:
        resolved_command = _resolve_subprocess_command(
            stage.command,
            environment=environment,
        )
        resolution_evidence["resolved_executable"] = resolved_command[0]
        with log_path.open("w", encoding="utf-8", newline="\n") as log:
            log.write(
                json.dumps(
                    {"executable_resolution": resolution_evidence},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            log.flush()
            completed = subprocess.run(
                list(resolved_command),
                cwd=cwd,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=stage.timeout_s,
            )
        status = STATUS_PASS if completed.returncode == 0 else STATUS_FAIL
        detail = "command passed" if status == STATUS_PASS else "command returned non-zero"
        print(
            f"[core-release:{run_id}] {status} {stage.stage_id} exit={completed.returncode}",
            flush=True,
        )
        return _outcome(
            stage,
            status=status,
            started_at=started_at,
            started=started,
            exit_code=int(completed.returncode),
            log_ref=_repo_ref(root, log_path),
            detail=detail,
            evidence={"executable_resolution": resolution_evidence},
        )
    except BaseException as exc:
        resolution_evidence["error"] = f"{type(exc).__name__}: {exc}"
        _atomic_write_text(
            log_path,
            json.dumps(
                {"executable_resolution": resolution_evidence},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
        )
        return _outcome(
            stage,
            status=STATUS_FAIL,
            started_at=started_at,
            started=started,
            exit_code=None,
            log_ref=_repo_ref(root, log_path),
            detail=f"command could not start: {type(exc).__name__}: {exc}",
            evidence={"executable_resolution": resolution_evidence},
        )


def _run_architecture_static_gates(*, root: Path) -> dict[str, Any]:
    """Evaluate Gate 1-14 from source, AST and release policy evidence.

    This preflight is deliberately independent from pytest.  A repository-wide
    green suite cannot turn a missing architecture proof into PASS.
    """

    policy_path = (root / ARCHITECTURE_POLICY_RELATIVE).resolve()
    _require_under(root, policy_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    if policy.get("schema_version") != "natsume.core-architecture-policy.v1":
        raise ValueError("unsupported Core architecture policy schema")

    evaluators: dict[int, Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]] = {
        1: _static_gate_dependency_boundaries,
        2: _static_gate_composition,
        3: _static_gate_mixed_files,
        4: _static_gate_authority_lineage,
        5: _static_gate_verification_commit,
        6: _static_gate_projection_writers,
        7: _static_gate_surface_outbox,
        8: _static_gate_namespace,
        9: _static_gate_secure_ingress,
        10: _static_gate_persistence,
        11: _static_gate_services,
        12: _static_gate_edge_authority,
        13: _static_gate_operator_query,
        14: _static_gate_legacy_retirement,
    }
    rows: dict[str, Any] = {}
    for gate in range(1, 15):
        try:
            raw = dict(evaluators[gate](root, policy))
            issues = [str(item) for item in list(raw.pop("issues", [])) if str(item)]
            rows[str(gate)] = {
                "status": STATUS_FAIL if issues else STATUS_PASS,
                "issues": issues,
                "evidence": raw,
            }
        except BaseException as exc:
            rows[str(gate)] = {
                "status": STATUS_FAIL,
                "issues": [f"static evaluator raised {type(exc).__name__}: {exc}"],
                "evidence": {},
            }
    failed = [int(gate) for gate, row in rows.items() if row["status"] != STATUS_PASS]
    return {
        "schema_version": "natsume.core-architecture-static-receipt.v1",
        "status": STATUS_PASS if not failed else STATUS_FAIL,
        "policy_ref": ARCHITECTURE_POLICY_RELATIVE.as_posix(),
        "policy_sha256": _sha256(policy_path),
        "gate_results": rows,
        "failed_gates": failed,
    }


def _static_gate_dependency_boundaries(root: Path, _policy: Mapping[str, Any]) -> Mapping[str, Any]:
    from tools.architecture.edge_contracts_guard import GuardMode, scan_report

    edge_report = scan_report(root, mode=GuardMode.ENFORCE)
    edge_issues = list(edge_report.get("violations") or [])
    reverse_imports: set[str] = set()
    graph: dict[str, set[str]] = {
        "app": set(),
        "composition": set(),
        "application": set(),
        "persistence": set(),
        "domain": set(),
        "contracts": set(),
        "edge": set(),
    }
    for path in _production_python_files(root):
        relative = path.relative_to(root).as_posix()
        source_layer = _architecture_layer(relative)
        for imported in _python_imports(path):
            target_layer = _architecture_layer(imported.replace(".", "/") + ".py")
            if source_layer and target_layer and source_layer != target_layer:
                graph[source_layer].add(target_layer)
            if (
                relative.startswith("host_brain/")
                and not relative.startswith(("host_brain/app/", "host_brain/composition/"))
                and relative != "host_brain/__main__.py"
                and not relative.startswith(
                    ("host_brain/release/", "host_brain/tools/", "host_brain/validation/")
                )
                and imported.startswith("host_brain.app")
            ):
                reverse_imports.add(f"{relative} -> {imported}")
    sccs = [sorted(row) for row in _strongly_connected_components(graph) if len(row) > 1]
    issues: list[str] = []
    if edge_issues:
        issues.append(f"Edge/Host contract violations: {edge_issues[:8]}")
    if reverse_imports:
        issues.append(
            f"business-to-app reverse imports: {sorted(reverse_imports)[:12]}"
        )
    if sccs:
        issues.append(f"target-layer dependency SCCs: {sccs}")
    return {
        "issues": issues,
        "edge_finding_count": int(edge_report.get("finding_count") or 0),
        "edge_violation_count": len(edge_issues),
        "reverse_imports": sorted(reverse_imports),
        "layer_edges": {name: sorted(targets) for name, targets in graph.items()},
        "strongly_connected_components": sccs,
    }


def _static_gate_composition(root: Path, policy: Mapping[str, Any]) -> Mapping[str, Any]:
    inventory = _call_inventory(root)
    issues: list[str] = []
    checked: dict[str, Any] = {}
    for symbol, raw_rule in dict(policy.get("composition_calls") or {}).items():
        rule = dict(raw_rule or {})
        actual = sorted(inventory.get(str(symbol), []))
        allowed = sorted(str(item) for item in list(rule.get("allowed_paths") or []))
        expected_count = int(rule.get("count") or 0)
        checked[str(symbol)] = {"count": len(actual), "paths": actual, "allowed_paths": allowed}
        if len(actual) != expected_count or any(path not in allowed for path in actual):
            issues.append(
                f"composition call {symbol} expected {expected_count} in {allowed}, got {actual}"
            )
    broker_paths = sorted(inventory.get("CapabilityBroker", []))
    if broker_paths != ["host_brain/capability_plane/production_composition.py"]:
        issues.append(f"CapabilityBroker construction is not canonical and unique: {broker_paths}")
    return {"issues": issues, "composition_calls": checked, "capability_broker_paths": broker_paths}


def _static_gate_mixed_files(root: Path, policy: Mapping[str, Any]) -> Mapping[str, Any]:
    allowed: dict[str, dict[str, Any]] = {}
    mixed = {
        str(item).strip().replace("\\", "/")
        for item in list(policy.get("mixed_responsibility_files") or [])
        if str(item).strip()
    }
    issues: list[str] = []
    for raw in list(policy.get("cohesive_large_files") or []):
        row = dict(raw or {})
        path = str(row.get("path") or "").strip().replace("\\", "/")
        responsibility = str(row.get("responsibility") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not path or not responsibility or len(reason) < 24:
            issues.append(f"large-file allowlist entry lacks path/responsibility/reason: {row}")
            continue
        if path in allowed:
            issues.append(f"duplicate large-file allowlist entry: {path}")
            continue
        allowed[path] = row

    inventory: list[dict[str, Any]] = []
    classified: set[str] = set()
    observed_mixed: set[str] = set()
    for path in _production_python_files(root):
        relative = path.relative_to(root).as_posix()
        lines = len(path.read_text(encoding="utf-8-sig").splitlines())
        if lines <= 1_500:
            continue
        row = allowed.get(relative)
        classification = "mixed" if relative in mixed else ("cohesive" if row else "unclassified")
        inventory.append(
            {
                "path": relative,
                "lines": lines,
                "classification": classification,
                "responsibility": str((row or {}).get("responsibility") or "multiple runtime responsibilities"),
                "rationale": str((row or {}).get("reason") or "code-owned mixed-responsibility inventory; split required"),
                "source_sha256": _sha256(path),
            }
        )
        if classification == "mixed":
            observed_mixed.add(relative)
            issues.append(f"mixed-responsibility production file still exceeds 1500 lines: {relative}:{lines}")
            continue
        if row is None:
            issues.append(f"production file >1500 lines has no mixed/cohesive responsibility decision: {relative}:{lines}")
            continue
        classified.add(relative)
        if lines > int(row.get("max_lines") or 0):
            issues.append(f"cohesive file exceeded approved line ceiling: {relative}:{lines}")

    stale_mixed = sorted(mixed - observed_mixed)
    if stale_mixed:
        issues.append(f"mixed-file inventory is stale after split; remove or mark thin: {stale_mixed}")

    new_large = _new_production_python_files(root, minimum_lines=801)
    new_mixed = sorted(path for path in new_large if path in mixed)
    if new_mixed:
        issues.append(f"new >800-line mixed-responsibility production files: {new_mixed}")
    return {
        "issues": issues,
        "large_file_inventory": sorted(inventory, key=lambda row: (-int(row["lines"]), str(row["path"]))),
        "new_over_800_lines": sorted(new_large),
        "new_mixed_over_800_lines": new_mixed,
        "policy_entry_count": len(allowed),
        "mixed_inventory_count": len(mixed),
    }


def _static_gate_authority_lineage(root: Path, _policy: Mapping[str, Any]) -> Mapping[str, Any]:
    anchors = (
        "host_brain/task_runtime/coordinator.py",
        "host_brain/orchestration/governor_grant.py",
        "tests/unit/test_aw04_governor_grant_binding.py",
        "tests/unit/test_aw02_governed_task_coordinator.py",
    )
    missing = [path for path in anchors if not (root / path).is_file()]
    legacy_tokens = _source_token_hits(
        root,
        ("TaskExecutor.LEGACY", "TaskRuntimeMode", "legacy_worker", "shadow_executor"),
    )
    return {
        "issues": ([f"missing authority/lineage anchor: {missing}"] if missing else [])
        + ([f"legacy task lineage authorities remain: {legacy_tokens[:12]}"] if legacy_tokens else []),
        "anchors": list(anchors),
        "legacy_authority_hits": legacy_tokens,
    }


def _static_gate_verification_commit(root: Path, _policy: Mapping[str, Any]) -> Mapping[str, Any]:
    policy_path = root / "host_brain/agent_runtime/verification_policy.py"
    contract = root / "tests/contract/test_nle_verification_gate_contract.py"
    issues: list[str] = []
    if not policy_path.is_file() or not contract.is_file():
        issues.append("verification policy or its dedicated contract is missing")
        text = ""
    else:
        text = policy_path.read_text(encoding="utf-8-sig")
    for token in ("VerificationStatus.FAIL", "VerificationStatus.ERROR", "VerificationStatus.NOT_RUN"):
        if token not in text:
            issues.append(f"verification fail-closed status is not handled: {token}")
    return {
        "issues": issues,
        "policy_ref": "host_brain/agent_runtime/verification_policy.py",
        "contract_ref": "tests/contract/test_nle_verification_gate_contract.py",
    }


def _static_gate_projection_writers(root: Path, _policy: Mapping[str, Any]) -> Mapping[str, Any]:
    anchors = (
        "host_brain/observability/projection_store.py",
        "host_brain/app/observability_runtime/projection_daemon.py",
        "host_brain/audit/migrations/0082_operator_query_projection.sql",
        "host_brain/audit/migrations/0083_host_embodiment_outbox.sql",
        "edge_client/gateway/migrations/0001_embodiment_transport.sql",
        "tests/integration/test_aw07_query_projection_rebuild_reconcile.py",
    )
    missing = [path for path in anchors if not (root / path).is_file()]
    daemon_constructors = sorted(
        path
        for path in _call_inventory(root).get("start_projection_jobs", [])
        if path.startswith("host_brain/app/")
    )
    issues = [f"missing projection/writer anchor: {missing}"] if missing else []
    if len(daemon_constructors) != 1:
        issues.append(f"projection daemon must have one app lifecycle owner, got {daemon_constructors}")
    return {"issues": issues, "anchors": list(anchors), "projection_daemon_start_sites": daemon_constructors}


def _static_gate_surface_outbox(root: Path, _policy: Mapping[str, Any]) -> Mapping[str, Any]:
    surface_sql: list[str] = []
    surface_write_calls: list[str] = []
    forbidden_methods = {"commit", "delete", "enqueue", "execute", "insert", "publish", "save", "update", "write"}
    for path in _production_python_files(root):
        relative = path.relative_to(root).as_posix()
        if "surface" not in relative.lower():
            continue
        for imported in _python_imports(path):
            if imported == "sqlite3":
                surface_sql.append(f"{relative} -> {imported}")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
        except (OSError, SyntaxError, UnicodeError):
            continue
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and ("build" in node.name.lower() or "render" in node.name.lower())
        ):
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr.lower() in forbidden_methods
                    and any(
                        token in _ast_receiver(node.func.value).lower()
                        for token in ("conn", "cursor", "db", "ledger", "outbox", "repo", "store")
                    )
                ):
                    surface_write_calls.append(f"{relative}:{node.lineno}:{function.name}:{node.func.attr}")
    outbox_anchors = (
        "host_brain/persistence/life_command_repository.py",
        "host_brain/persistence/scheduler_repository.py",
        "host_brain/persistence/gateway_transport_repository.py",
    )
    missing = [path for path in outbox_anchors if not (root / path).is_file()]
    issues = ([f"surface imports SQLite authority: {surface_sql[:12]}"] if surface_sql else [])
    if surface_write_calls:
        issues.append(f"surface build/render contains write-like calls: {surface_write_calls[:12]}")
    if missing:
        issues.append(f"durable outbox anchors missing: {missing}")
    return {
        "issues": issues,
        "surface_sqlite_imports": surface_sql,
        "surface_build_write_calls": surface_write_calls,
        "outbox_anchors": list(outbox_anchors),
    }


def _static_gate_namespace(root: Path, _policy: Mapping[str, Any]) -> Mapping[str, Any]:
    anchors = (
        "host_brain/pentest/knowledge_store.py",
        "host_brain/memory/canon_apply_service.py",
        "tests/contract/test_pentest_knowledge_store_contract.py",
    )
    missing = [path for path in anchors if not (root / path).is_file()]
    chat_private_imports: list[str] = []
    for path in (root / "host_brain/app").glob("chat*.py"):
        for imported in _python_imports(path):
            if imported.startswith("host_brain.pentest.knowledge"):
                chat_private_imports.append(f"{path.relative_to(root).as_posix()} -> {imported}")
    issues = [f"missing namespace/promotion anchor: {missing}"] if missing else []
    if chat_private_imports:
        issues.append(f"ordinary chat imports private Pentest knowledge: {chat_private_imports}")
    return {"issues": issues, "anchors": list(anchors), "chat_private_imports": chat_private_imports}


def _static_gate_secure_ingress(root: Path, _policy: Mapping[str, Any]) -> Mapping[str, Any]:
    anchors = (
        "host_brain/observability/secure_command_ingress.py",
        "host_brain/observability/command_http.py",
        "host_brain/composition/command_ingress.py",
        "edge_client/gateway/webhook_security.py",
        "tests/security/test_secure_ingress_identity_contract.py",
    )
    missing = [path for path in anchors if not (root / path).is_file()]
    required_tokens = ("principal", "scope", "signature", "rate", "idempot", "audit")
    corpus = "\n".join(
        (root / path).read_text(encoding="utf-8-sig").lower()
        for path in anchors
        if (root / path).is_file() and path.endswith(".py")
    )
    absent = [token for token in required_tokens if token not in corpus]
    issues = [f"missing secure-ingress anchor: {missing}"] if missing else []
    if absent:
        issues.append(f"secure-ingress evidence lacks required controls: {absent}")
    return {"issues": issues, "anchors": list(anchors), "control_tokens": list(required_tokens)}


def _static_gate_persistence(root: Path, policy: Mapping[str, Any]) -> Mapping[str, Any]:
    private: list[str] = []
    authorized: list[str] = []
    ddl: list[str] = []
    explicit: set[str] = set()
    policy_issues: list[str] = []
    for raw in list(policy.get("private_connection_authorities") or []):
        row = dict(raw or {})
        path = str(row.get("path") or "").strip().replace("\\", "/")
        if not path or not str(row.get("responsibility") or "").strip() or len(str(row.get("reason") or "")) < 24:
            policy_issues.append(f"private-connection authority lacks responsibility rationale: {row}")
            continue
        explicit.add(path)
    ddl_pattern = re.compile(r"(?im)^\s*(?:CREATE|ALTER|DROP)\s+(?:TABLE|INDEX|TRIGGER)\b")
    for path in _production_python_files(root):
        relative = path.relative_to(root).as_posix()
        formal_connection_authority = (
            relative in explicit
            or relative.startswith(("host_brain/audit/", "host_brain/persistence/", "edge_client/persistence/"))
            or "/migrations/" in relative
            or path.stem.endswith(("_repository", "_store"))
            or path.stem.endswith("migration_runner")
        )
        source = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            private.append(f"{relative}:{exc.lineno}: syntax error prevents persistence scan")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = _ast_receiver(node.func.value)
                if node.func.attr == "connect" and receiver in {"sqlite3", "_sqlite3"}:
                    hit = f"{relative}:{node.lineno}: sqlite3.connect"
                    (authorized if formal_connection_authority else private).append(hit)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and ddl_pattern.search(node.value):
                if (
                    relative != "host_brain/bond/neo4j_client.py"
                    and not relative.startswith(("host_brain/audit/", "host_brain/persistence/"))
                    and "/migrations/" not in relative
                ):
                    ddl.append(f"{relative}:{node.lineno}: inline SQLite DDL")
    issues: list[str] = list(policy_issues)
    if private:
        issues.append(f"business-layer private SQLite connections: {private[:16]}")
    if ddl:
        issues.append(f"business-layer inline DDL: {ddl[:16]}")
    return {
        "issues": issues,
        "private_connection_hits": private,
        "authorized_repository_connection_hits": authorized,
        "inline_ddl_hits": ddl,
        "explicit_connection_authorities": sorted(explicit),
    }


def _static_gate_services(root: Path, policy: Mapping[str, Any]) -> Mapping[str, Any]:
    inventory = _call_inventory(root)
    issues: list[str] = []
    checked: dict[str, Any] = {}
    for symbol, raw_rule in dict(policy.get("service_constructors") or {}).items():
        rule = dict(raw_rule or {})
        actual = sorted(inventory.get(str(symbol), []))
        allowed = sorted(str(item) for item in list(rule.get("allowed_paths") or []))
        expected_count = int(rule.get("count") or 0)
        checked[str(symbol)] = {"count": len(actual), "paths": actual, "allowed_paths": allowed}
        if len(actual) != expected_count or any(path not in allowed for path in actual):
            issues.append(f"production service {symbol} expected {expected_count} in {allowed}, got {actual}")
    supply_anchors = (
        "host_brain/skills/supply_chain.py",
        "tests/security/test_skill_supply_chain_quarantine.py",
        "tests/contract/test_sandbox_contract.py",
    )
    missing = [path for path in supply_anchors if not (root / path).is_file()]
    if missing:
        issues.append(f"signed bundle supply-chain anchors missing: {missing}")
    return {"issues": issues, "service_constructors": checked, "supply_chain_anchors": list(supply_anchors)}


def _static_gate_edge_authority(root: Path, _policy: Mapping[str, Any]) -> Mapping[str, Any]:
    host_imports: list[str] = []
    for path in (root / "edge_client").rglob("*.py"):
        for imported in _python_imports(path):
            if imported.startswith("host_brain"):
                host_imports.append(f"{path.relative_to(root).as_posix()} -> {imported}")
    receipt = root / "natsume_contracts/edge/embodiment_v1.py"
    text = receipt.read_text(encoding="utf-8-sig") if receipt.is_file() else ""
    edge_migration = root / "edge_client/gateway/migrations/0001_embodiment_transport.sql"
    issues = [f"Edge imports Host implementation: {host_imports[:12]}"] if host_imports else []
    if "EmbodimentReceipt" not in text or "EmbodimentCommandPort" not in text:
        issues.append("versioned physical-output command/receipt contract is missing")
    if not edge_migration.is_file():
        issues.append("Edge embodiment transport migration is missing")
    return {
        "issues": issues,
        "edge_to_host_imports": host_imports,
        "receipt_contract": receipt.relative_to(root).as_posix(),
        "edge_transport_migration": edge_migration.relative_to(root).as_posix(),
    }


def _static_gate_operator_query(root: Path, policy: Mapping[str, Any]) -> Mapping[str, Any]:
    generated = [str(item) for item in list(policy.get("generated_client_paths") or [])]
    missing = [path for path in generated if not (root / path).is_file()]
    query_contracts = (
        "tests/contract/test_obs_query_only_boundary.py",
        "tests/contract/test_aw07_operator_query_privacy_contract.py",
    )
    missing_contracts = [path for path in query_contracts if not (root / path).is_file()]
    gateway = root / "host_brain/app/monitor_ui/src/shared/api/operatorCommandGateway.js"
    gateway_text = gateway.read_text(encoding="utf-8-sig") if gateway.is_file() else ""
    issues = [f"generated Operator client assets missing: {missing}"] if missing else []
    if missing_contracts:
        issues.append(f"query purity/privacy contracts missing: {missing_contracts}")
    if "/v1/host/operator-command-bff/" not in gateway_text or "generated/operatorCommandClient" not in gateway_text:
        issues.append("Operator mutations do not exclusively use generated contract plus Host BFF")
    return {"issues": issues, "generated_client_paths": generated, "query_contracts": list(query_contracts)}


def _static_gate_legacy_retirement(root: Path, policy: Mapping[str, Any]) -> Mapping[str, Any]:
    flags = tuple(str(item) for item in list(policy.get("retired_flags") or []))
    symbols = tuple(str(item) for item in list(policy.get("retired_symbols") or []))
    flag_hits = _source_token_hits(root, flags)
    symbol_hits = _source_token_hits(root, symbols)
    issues: list[str] = []
    if flag_hits:
        issues.append(f"retired runtime flags remain in production: {flag_hits[:20]}")
    if symbol_hits:
        issues.append(f"retired production symbols remain: {symbol_hits[:20]}")
    return {
        "issues": issues,
        "retired_flags": list(flags),
        "retired_symbols": list(symbols),
        "production_hits": flag_hits,
        "production_symbol_hits": symbol_hits,
    }


@lru_cache(maxsize=4)
def _production_python_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for source_root in ("host_brain", "edge_client", "natsume_contracts"):
        base = root / source_root
        if not base.is_dir():
            continue
        files.extend(
            path
            for path in base.rglob("*.py")
            if "__pycache__" not in path.parts and "migrations" not in path.parts
        )
    return tuple(sorted(files))


@lru_cache(maxsize=8_192)
def _python_imports(path: Path) -> tuple[str, ...]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return ()
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return tuple(imported)


def _architecture_layer(path_or_module: str) -> str | None:
    value = path_or_module.replace("\\", "/")
    if value.startswith("edge_client/"):
        return "edge"
    if value.startswith("natsume_contracts/"):
        return "contracts"
    if not value.startswith("host_brain/"):
        return None
    if value.startswith("host_brain/contracts/"):
        return "contracts"
    if value == "host_brain/__main__.py" or value.startswith(
        ("host_brain/release/", "host_brain/tools/", "host_brain/validation/")
    ):
        return None
    domain_contracts = {
        "host_brain/agent_runtime/acceptance_judge.py",
        "host_brain/agent_runtime/budget_policy.py",
        "host_brain/agent_runtime/contract_errors.py",
        "host_brain/agent_runtime/review_policy.py",
        "host_brain/agent_runtime/review_token_contract.py",
        "host_brain/agent_runtime/task_graph.py",
        "host_brain/agent_runtime/verification_contract.py",
        "host_brain/agent_runtime/work_order.py",
        "host_brain/bond/user_identity.py",
        "host_brain/core/errors.py",
        "host_brain/core/ids.py",
        "host_brain/digital_life/contracts.py",
        "host_brain/memory/life_memory/contracts.py",
        "host_brain/pentest/authority_contract.py",
        "host_brain/secure_ingress/audit_safety.py",
        "host_brain/secure_ingress/models.py",
    }
    if value in domain_contracts:
        return "domain"
    package = value.split("/", 2)[1]
    if package == "app":
        return "app"
    if package == "composition":
        return "composition"
    if package == "persistence":
        return "persistence"
    if package == "audit" and any(
        token in Path(value).stem for token in ("ledger", "migration")
    ):
        return "persistence"
    return "application"


def _strongly_connected_components(graph: Mapping[str, set[str]]) -> list[set[str]]:
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    result: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, set()):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: set[str] = set()
            while stack:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            result.append(component)

    for node in graph:
        if node not in indices:
            visit(node)
    return result


@lru_cache(maxsize=4)
def _call_inventory(root: Path) -> dict[str, list[str]]:
    inventory: dict[str, list[str]] = {}
    for path in _production_python_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name:
                inventory.setdefault(name, []).append(relative)
    return inventory


def _new_production_python_files(root: Path, *, minimum_lines: int) -> set[str]:
    completed = subprocess.run(
        ["git", "diff-tree", "--root", "--no-commit-id", "--name-only", "--diff-filter=A", "-r", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        raise RuntimeError("could not determine newly added production files")
    result: set[str] = set()
    for raw in completed.stdout.splitlines():
        relative = raw.strip().replace("\\", "/")
        if not relative.endswith(".py") or not relative.startswith(("host_brain/", "edge_client/", "natsume_contracts/")):
            continue
        path = root / relative
        if path.is_file() and len(path.read_text(encoding="utf-8-sig").splitlines()) >= minimum_lines:
            result.add(relative)
    return result


def _source_token_hits(
    root: Path,
    tokens: Sequence[str],
    *,
    roots: Sequence[str] = ("host_brain", "edge_client", "natsume_contracts"),
) -> list[str]:
    normalized = tuple(token for token in tokens if token)
    hits: list[str] = []
    for source_root in roots:
        base = root / source_root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
                for token in normalized:
                    if token in line:
                        hits.append(f"{relative}:{line_no}:{token}")
    return sorted(hits)


def _ast_receiver(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _run_isolated_migration_backup_restore(*, root: Path, run_id: str) -> dict[str, Any]:
    from host_brain.audit.migration_runner import SQLiteMigrationRunner
    from host_brain.persistence.sqlite_backup import SQLiteBackupManager

    with tempfile.TemporaryDirectory(prefix="natsume-core-release-migration-") as temporary:
        workspace = Path(temporary)
        source = workspace / "canonical.sqlite3"
        result = SQLiteMigrationRunner(db_path=source).apply_migrations(
            request_id=f"core-release::{run_id}::migration"
        )
        if not result.success:
            raise RuntimeError(f"canonical migration failed: {result.as_dict()}")
        second = SQLiteMigrationRunner(db_path=source).apply_migrations(
            request_id=f"core-release::{run_id}::migration-idempotency"
        )
        if not second.success:
            raise RuntimeError(f"canonical migration idempotency failed: {second.as_dict()}")

        backup = workspace / "backup" / "canonical.sqlite3"
        restored = workspace / "restore" / "canonical.sqlite3"
        manager = SQLiteBackupManager()
        backup_receipt = manager.create_verified_backup(source_path=source, backup_path=backup)
        verified = manager.verify_backup(backup_path=backup)
        restore_receipt = manager.restore_verified_backup(backup_path=backup, target_path=restored)
        conn = sqlite3.connect(f"file:{restored.as_posix()}?mode=ro", uri=True)
        try:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            latest = int(conn.execute("SELECT MAX(migration_id) FROM schema_migrations").fetchone()[0])
        finally:
            conn.close()
        if integrity.lower() != "ok":
            raise RuntimeError(f"restored database integrity check failed: {integrity}")
        if backup_receipt.sha256 != verified.sha256:
            raise RuntimeError("verified backup digest changed")
        if backup_receipt.migration_catalog_digest != restore_receipt.migration_catalog_digest:
            raise RuntimeError("restored migration catalog differs from backup")
        return {
            "status": STATUS_PASS,
            "migration_latest_version": latest,
            "migration_latest_key": backup_receipt.migration_latest_key,
            "migration_asset_count": backup_receipt.migration_asset_count,
            "migration_catalog_digest": backup_receipt.migration_catalog_digest,
            "backup_sha256": backup_receipt.sha256,
            "restore_integrity_check": integrity,
            "temporary_workspace_removed": True,
        }


def _run_isolated_runtime_http_drill(*, root: Path, run_id: str) -> dict[str, Any]:
    """Boot, exercise, crash and restart a real isolated ASGI process.

    This is intentionally not backed by QA fallback builders or scenario
    fixtures.  The receipt is sourced from OS process state, HTTP responses and
    the migrated SQLite catalog created by the child process.
    """

    with tempfile.TemporaryDirectory(prefix="natsume-core-release-runtime-") as temporary:
        workspace = Path(temporary)
        database = workspace / "canonical.sqlite3"
        environment = dict(os.environ)
        environment.update(
            {
                "NATSUME_AUDIT_DB": str(database),
                "HOST_AUDIT_DB_PATH": str(database),
                "HOST_TASK_RUNTIME_DB_PATH": str(database),
                "HOST_AGENT_REVIEW_DB_PATH": str(database),
                "HOST_RUNTIME_EVENT_DB_PATH": str(database),
                "HOST_NLE_OPERATOR_VIEWS_ENABLED": "true",
                "NATSUME_CORE_RELEASE_RUN_ID": run_id,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        boots: list[dict[str, Any]] = []
        for boot_index in (1, 2):
            port = _reserve_loopback_port()
            log_path = workspace / f"obs-boot-{boot_index}.log"
            boot_record: dict[str, Any] | None = None
            boot_error: BaseException | None = None
            with log_path.open("w+", encoding="utf-8", newline="\n") as log:
                process: subprocess.Popen[Any] | None = None
                try:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "host_brain.app.obs_fastapi_app:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(port),
                            "--log-level",
                            "warning",
                        ],
                        cwd=root,
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    health = _wait_for_http_json(
                        f"http://127.0.0.1:{port}/health",
                        process=process,
                        timeout_s=75,
                    )
                    if process.poll() is not None:
                        raise RuntimeError(f"isolated Obs process exited after health: {process.returncode}")
                    rejected = _http_json(
                        f"http://127.0.0.1:{port}/observability/trace",
                        method="POST",
                        payload={},
                        accepted_statuses=(400, 401, 403, 422),
                    )
                    if int(rejected["status_code"]) not in {400, 401, 403, 422}:
                        raise RuntimeError(f"malformed write did not fail closed: {rejected}")
                    boot_record = {
                        "boot_index": boot_index,
                        "pid": process.pid,
                        "port": port,
                        "process_alive_after_health": True,
                        "health_status_code": int(health["status_code"]),
                        "health_payload_status": str(dict(health.get("payload") or {}).get("status") or ""),
                        "malformed_trace_status_code": int(rejected["status_code"]),
                    }
                except BaseException as exc:
                    boot_error = exc
                finally:
                    if process is not None and process.poll() is None:
                        # A forced termination models the process-fault boundary;
                        # the second boot proves cold recovery over the same DB.
                        process.terminate()
                        try:
                            process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
                    log.flush()
                    log.seek(0)
                    log_tail = log.read()[-4_000:]
            if boot_error is not None:
                raise InternalStageEvidenceError(
                    f"isolated Obs boot {boot_index} failed: {type(boot_error).__name__}: {boot_error}",
                    evidence={
                        "child_process": "isolated_obs",
                        "boot_index": boot_index,
                        "child_log": _bounded_child_log_evidence(log_path),
                    },
                ) from boot_error
            assert process is not None
            if process.returncode is None:
                raise RuntimeError("isolated Obs process did not terminate")
            if boot_record is None:
                raise RuntimeError(f"isolated Obs boot {boot_index} produced no HTTP evidence")
            boot_record["termination_exit_code"] = int(process.returncode)
            boot_record["log_tail_sha256"] = f"sha256:{hashlib.sha256(log_tail.encode()).hexdigest()}"
            boots.append(boot_record)

        if not database.is_file() or database.stat().st_size <= 0:
            raise RuntimeError("isolated runtime did not create the canonical database")
        conn = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            migration_count = int(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
            migration_head = int(conn.execute("SELECT MAX(migration_id) FROM schema_migrations").fetchone()[0])
        finally:
            conn.close()
        if integrity.lower() != "ok" or migration_count <= 0:
            raise RuntimeError(
                f"isolated runtime database is not healthy: integrity={integrity} migrations={migration_count}"
            )
        return {
            "status": STATUS_PASS,
            "drill_kind": "isolated_real_process_http_sqlite_crash_restart",
            "fixture_or_qa_fallback_used": False,
            "boot_count": len(boots),
            "boots": boots,
            "database_created": True,
            "database_size_bytes": database.stat().st_size,
            "database_integrity": integrity,
            "migration_count": migration_count,
            "migration_head": migration_head,
            "same_database_reopened_after_process_fault": True,
            "temporary_workspace_removed": True,
        }


def _run_monitor_ui_interaction_smoke(*, root: Path, run_id: str) -> dict[str, Any]:
    monitor = root / "host_brain/app/monitor_ui"
    with tempfile.TemporaryDirectory(prefix="natsume-core-release-ui-") as temporary:
        workspace = Path(temporary)
        database = workspace / "canonical.sqlite3"
        obs_port = _reserve_loopback_port()
        ui_port = _reserve_loopback_port()
        obs_url = f"http://127.0.0.1:{obs_port}"
        operator_token = f"{uuid4().hex}{uuid4().hex}"
        operator_binding = json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(operator_token.encode()).hexdigest(),
                    "principal_id": "core-release.monitor",
                    "tenant_id": "core-release",
                    "key_id": "core-release-monitor",
                    "principal_kind": "service",
                    "scopes": [
                        "obs:operator:read",
                        "obs:health:stream",
                        "obs:task-runtime:read",
                        "obs:session:read",
                        "obs:session:export",
                        "obs:trace:read",
                        "obs:checkpoint:read",
                        "obs:memory:read",
                        "obs:immune:read",
                        "obs:bio-ops:read",
                        "obs:ui:read",
                        "obs:capability-forge:read",
                        "obs:life-evolution:read",
                        "obs:agent-history:read",
                    ],
                }
            ],
            separators=(",", ":"),
        )
        environment = dict(os.environ)
        environment.pop("HOST_MCP_REGISTRY_DB", None)
        environment.update(
            {
                "NATSUME_AUDIT_DB": str(database),
                "HOST_AUDIT_DB_PATH": str(database),
                "HOST_TASK_RUNTIME_DB_PATH": str(database),
                "HOST_AGENT_REVIEW_DB_PATH": str(database),
                "HOST_RUNTIME_EVENT_DB_PATH": str(database),
                "HOST_NLE_OPERATOR_VIEWS_ENABLED": "true",
                "NATSUME_CORE_RELEASE_RUN_ID": run_id,
                "PYTHONDONTWRITEBYTECODE": "1",
                "VITE_DEV_OBS_API_TARGET": obs_url,
                "VITE_DEV_API_TARGET": obs_url,
                "VITE_DEV_HOST_API_TARGET": obs_url,
                "MONITOR_UI_PREVIEW_PORT": str(ui_port),
                "MONITOR_UI_BASE_URL": f"http://127.0.0.1:{ui_port}/monitor/",
                "NATSUME_OPERATOR_QUERY_AUTH_BINDINGS": operator_binding,
            }
        )
        # The real Obs child receives only the digest binding. The raw session
        # credential is scoped to the contained Playwright process tree that
        # injects the host-only HttpOnly cookie and disappears with that tree.
        monitor_environment = dict(environment)
        monitor_environment["MONITOR_UI_OPERATOR_SESSION_TOKEN"] = operator_token
        monitor_environment["MONITOR_UI_PLAYWRIGHT_OUTPUT_DIR"] = str(
            workspace / "playwright-results"
        )
        obs_log = workspace / "obs.log"
        process: subprocess.Popen[Any] | None = None
        monitor_error: BaseException | None = None
        output = ""
        with obs_log.open("w", encoding="utf-8", newline="\n") as log:
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "host_brain.app.obs_fastapi_app:app", "--host", "127.0.0.1", "--port", str(obs_port), "--log-level", "warning"],
                    cwd=root,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                health = _wait_for_http_json(f"{obs_url}/health", process=process, timeout_s=75)
                npm_requested_command = (
                    "npm",
                    "exec",
                    "playwright",
                    "--",
                    "test",
                    "e2e/core-release-real-interaction.spec.js",
                    "--trace=off",
                )
                npm_resolved_command = _resolve_subprocess_command(
                    npm_requested_command,
                    environment=monitor_environment,
                )
                completed = _run_subprocess_with_tree_cleanup(
                    npm_resolved_command,
                    cwd=monitor,
                    environment=monitor_environment,
                    timeout_s=180,
                )
                output = str(completed.stdout or "") + str(completed.stderr or "")
                if completed.returncode != 0:
                    raise RuntimeError(
                        f"unstubbed Monitor interaction failed with exit {completed.returncode}"
                    )
            except BaseException as exc:
                if isinstance(exc, subprocess.TimeoutExpired):
                    output = str(exc.output or "") + str(exc.stderr or "")
                monitor_error = exc
            finally:
                # Remove the raw credential from the parent-side environment as
                # soon as the contained browser tree is gone.  The Obs child
                # never receives this value, only its SHA-256 binding.
                monitor_environment.pop("MONITOR_UI_OPERATOR_SESSION_TOKEN", None)
                operator_token = ""
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                log.flush()
        if monitor_error is not None:
            evidence: dict[str, Any] = {
                "child_process": "monitor_obs",
                "child_log": _bounded_child_log_evidence(obs_log),
            }
            if output:
                evidence["playwright_output"] = _bounded_text_evidence(output)
            raise InternalStageEvidenceError(
                f"Monitor interaction child failed: {type(monitor_error).__name__}: {monitor_error}",
                evidence=evidence,
            ) from monitor_error
        assert process is not None
        return {
            "status": STATUS_PASS,
            "smoke_kind": "unstubbed_browser_via_preview_proxy_to_real_obs_process",
            "obs_process_pid": process.pid,
            "obs_process_terminated": process.returncode is not None,
            "obs_health_status_code": int(health["status_code"]),
            "obs_projection_schema": str(dict(dict(health.get("payload") or {}).get("projection") or {}).get("schema_version") or ""),
            "monitor_preview_port": ui_port,
            "playwright_exit_code": int(completed.returncode),
            "playwright_output_sha256": f"sha256:{hashlib.sha256(output.encode()).hexdigest()}",
            "obs_child_log_sha256": _bounded_child_log_evidence(obs_log)["content_sha256"],
            "npm_executable_resolution": {
                "method": "shutil.which",
                "requested_executable": npm_requested_command[0],
                "resolved_executable": npm_resolved_command[0],
            },
            "api_stubs_used": False,
        }


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _wait_for_http_json(url: str, *, process: subprocess.Popen[Any], timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error = "not started"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"isolated process exited before HTTP readiness: {process.returncode}")
        try:
            return _http_json(url, accepted_statuses=(200,))
        except (OSError, RuntimeError, URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.2)
    raise RuntimeError(f"isolated HTTP endpoint did not become ready: {last_error}")


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    accepted_statuses: Sequence[int] = (200,),
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            status_code = int(response.status)
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        status_code = int(exc.code)
        raw = exc.read().decode("utf-8", errors="replace")
    if status_code not in {int(value) for value in accepted_statuses}:
        raise RuntimeError(f"HTTP {status_code} from {url}: {raw[:500]}")
    try:
        decoded = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"non-JSON HTTP response from {url}: {raw[:500]}") from exc
    return {"status_code": status_code, "payload": decoded}


def _run_rollback_drill(*, root: Path, run_id: str) -> dict[str, Any]:
    from tools.replay.production_drill import run_drill_scenario_from_fixture

    matrix = _generate_rollback_matrix(root, run_id=run_id, generated_at=_utc_now())
    components = list(matrix.get("components") or [])
    if not components or any(bool(row.get("irreversible")) for row in components):
        raise RuntimeError("rollback matrix contains no safe, reversible component set")
    scenario = run_drill_scenario_from_fixture(
        root=root,
        fixture_path="tests/fixtures/drill/bad/rollback_conflict.json",
    )
    expected = {
        "status": "FAILED",
        "stage": "S_RELEASE_CHECKLIST",
        "reason_code": "RELEASE_ROLLBACK_TARGET_MISSING",
    }
    actual = {key: scenario.get(key) for key in expected}
    if actual != expected:
        raise RuntimeError(f"rollback conflict did not fail closed: {actual}")
    return {
        "status": STATUS_PASS,
        "component_count": len(components),
        "all_components_reversible": True,
        "conflict_detection": actual,
        "replay_ref": scenario.get("replay_ref"),
    }


def _generate_rollback_matrix(root: Path, *, run_id: str, generated_at: str) -> dict[str, Any]:
    source = root / ROLLBACK_CONFIG_RELATIVE
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if payload.get("schema_version") != "natsume.core-rollback-map.v1":
        raise ValueError("unsupported core rollback map schema")
    components = list(payload.get("components") or [])
    names: set[str] = set()
    required = {
        "component",
        "target",
        "schema_strategy",
        "drain_condition",
        "irreversible",
    }
    for row in components:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError("core rollback component is incomplete")
        name = str(row.get("component") or "").strip()
        if not name or name in names:
            raise ValueError(f"core rollback component is missing or duplicated: {name}")
        if bool(row.get("irreversible")):
            raise ValueError(f"Core Release rollback is irreversible for {name}")
        names.add(name)
    if not components:
        raise ValueError("core rollback map is empty")
    return {
        "schema_version": ROLLBACK_MATRIX_SCHEMA_VERSION,
        "release_id": str(payload.get("release_id") or ""),
        "run_id": run_id,
        "generated_at": generated_at,
        "source_ref": ROLLBACK_CONFIG_RELATIVE.as_posix(),
        "source_sha256": _sha256(source),
        "component_count": len(components),
        "components": components,
    }


def _architecture_gate_rows(*, status: str, run_id: str, receipt_ref: str) -> list[dict[str, Any]]:
    """Create draft rows only; PASS rows must come from the evaluator below."""

    normalized = str(status or "").upper()
    if normalized not in {"PENDING", STATUS_DRAFT}:
        raise ValueError("architecture gate PASS/FAIL rows require real evidence evaluation")
    return [
        {
            "gate": spec.gate,
            "title": spec.title,
            "status": normalized,
            "run_id": run_id,
            "evidence_refs": [
                f"{receipt_ref}#stage=architecture_static_gates&gate={spec.gate}"
                if spec.require_static
                else f"{receipt_ref}#gate={spec.gate}"
            ],
        }
        for spec in ARCHITECTURE_GATE_SPECS
    ]


def _evaluate_architecture_gates(
    *,
    receipt: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    retired_paths: Mapping[str, Any],
    run_id: str,
    receipt_ref: str,
) -> list[dict[str, Any]]:
    """Derive 15 gate statuses from required evidence, failing on absence/NOT_RUN."""

    stage_rows = {
        str(row.get("stage_id") or ""): dict(row)
        for row in list(receipt.get("stages") or [])
        if isinstance(row, Mapping) and str(row.get("stage_id") or "")
    }
    static_stage = stage_rows.get("architecture_static_gates", {})
    static_results = dict(dict(static_stage.get("evidence") or {}).get("gate_results") or {})
    checks: dict[str, Mapping[str, Any]] = {
        "compatibility_registry_empty": compatibility,
        "retired_paths_absent": retired_paths,
    }
    rows: list[dict[str, Any]] = []
    for spec in ARCHITECTURE_GATE_SPECS:
        evidence: list[dict[str, Any]] = []
        issues: list[str] = []
        if spec.require_static:
            static_gate = dict(static_results.get(str(spec.gate)) or {})
            static_status = str(static_gate.get("status") or "MISSING").upper()
            static_ref = f"{receipt_ref}#stage=architecture_static_gates&gate={spec.gate}"
            evidence.append(
                {
                    "kind": "static_gate",
                    "id": f"architecture_static_gates:{spec.gate}",
                    "status": static_status,
                    "ref": static_ref,
                }
            )
            if static_status != STATUS_PASS:
                issues.append(f"required static Gate {spec.gate} evidence is {static_status}")
                issues.extend(str(item) for item in list(static_gate.get("issues") or []))
        for stage_id in spec.required_stages:
            stage = stage_rows.get(stage_id)
            stage_status = str((stage or {}).get("status") or "MISSING").upper()
            evidence.append(
                {
                    "kind": "stage",
                    "id": stage_id,
                    "status": stage_status,
                    "ref": f"{receipt_ref}#stage={stage_id}",
                }
            )
            if stage_status != STATUS_PASS:
                issues.append(f"required stage {stage_id} is {stage_status}")
        for check_id in spec.required_checks:
            check = dict(checks.get(check_id) or {})
            check_status = str(check.get("status") or "MISSING").upper()
            if check_id == "compatibility_registry_empty" and int(check.get("active_count") or 0) != 0:
                check_status = STATUS_FAIL
            evidence.append(
                {
                    "kind": "check",
                    "id": check_id,
                    "status": check_status,
                    "ref": f"{receipt_ref}#check={check_id}",
                }
            )
            if check_status != STATUS_PASS:
                issues.append(f"required check {check_id} is {check_status}")
        status = STATUS_PASS if evidence and not issues and all(row["status"] == STATUS_PASS for row in evidence) else STATUS_FAIL
        rows.append(
            {
                "gate": spec.gate,
                "title": spec.title,
                "status": status,
                "run_id": run_id,
                "evidence": evidence,
                "evidence_refs": [str(row["ref"]) for row in evidence],
                "issues": issues,
            }
        )
    if [row["gate"] for row in rows] != list(range(1, CORE_RELEASE_GATE_COUNT + 1)):
        raise AssertionError("Core Release architecture gate evaluator drifted")
    return rows


def _outcome(
    stage: AcceptanceStage,
    *,
    status: str,
    started_at: str,
    started: float,
    exit_code: int | None,
    log_ref: str | None,
    detail: str,
    evidence: Mapping[str, Any] | None = None,
) -> StageOutcome:
    return StageOutcome(
        stage_id=stage.stage_id,
        status=status,
        started_at=started_at,
        finished_at=_utc_now(),
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        exit_code=exit_code,
        command=stage.command,
        log_ref=log_ref,
        detail=detail,
        evidence=evidence,
    )


def _skipped_outcome(stage: AcceptanceStage, detail: str) -> StageOutcome:
    now = _utc_now()
    return StageOutcome(
        stage_id=stage.stage_id,
        status=STATUS_SKIPPED,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        exit_code=None,
        command=stage.command,
        log_ref=None,
        detail=detail,
    )


def _notify(callback: ReceiptUpdate | None, payload: dict[str, Any]) -> None:
    if callback is not None:
        callback(json.loads(json.dumps(payload, ensure_ascii=False)))


def _output_root(root: Path) -> Path:
    output = (root / OUTPUT_RELATIVE).resolve()
    _require_under(root, output)
    output.mkdir(parents=True, exist_ok=True)
    return output


def _require_under(parent: Path, child: Path) -> None:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    if child_resolved != parent_resolved and parent_resolved not in child_resolved.parents:
        raise ValueError(f"release path escapes allowed root: {child}")


def _repo_ref(root: Path, path: Path) -> str:
    resolved = path.resolve()
    _require_under(root, resolved)
    return resolved.relative_to(root).as_posix()


def _normalize_run_id(value: str) -> str:
    text = str(value or "").strip()
    if not _RUN_ID_RE.fullmatch(text):
        raise ValueError("run id must contain only letters, numbers, dot, dash and underscore")
    return text


def _new_run_id(root: Path, *, prefix: str) -> str:
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    commit = (_git_commit(root) or "uncommitted")[:12]
    return f"{prefix}-{stamp}-{commit}-{uuid4().hex[:8]}"


def _git_commit(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    value = str(completed.stdout or "").strip().lower()
    return value if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _acquire_lock(path: Path, *, run_id: str, source_commit: str) -> None:
    payload = json.dumps(
        {"run_id": run_id, "source_commit": source_commit, "started_at": _utc_now()},
        ensure_ascii=False,
        sort_keys=True,
    )
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"another Core Release acceptance is already active: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="generate draft release artifacts only")
    mode.add_argument("--accept", action="store_true", help="run Program Acceptance after CODE_COMPLETE")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="continue an existing failed --accept run (requires an explicit --run-id)",
    )
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    parser.add_argument("--run-id", help="optional explicit run id for receipt correlation")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.resume and not args.accept:
            raise ValueError("--resume requires --accept")
        if args.resume and not args.run_id:
            raise ValueError("--resume requires an explicit --run-id")
        if args.accept:
            receipt = accept_release(root=args.root, run_id=args.run_id, resume=args.resume)
            print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
            return 0 if receipt.get("status") == STATUS_PASS else 1
        receipt = prepare_release(root=args.root, run_id=args.run_id)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if receipt.get("status") != STATUS_FAIL else 1
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "schema_version": RECEIPT_SCHEMA_VERSION,
                    "status": STATUS_FAIL,
                    "reason_code": "CORE_RELEASE_RUNNER_FAILED",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
