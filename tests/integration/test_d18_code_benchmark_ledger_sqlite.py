from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
import subprocess

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.code_benchmark_ledger_repository import (
    CODE_BENCHMARK_MIGRATION_ASSET_KEY,
    CODE_BENCHMARK_MIGRATION_SHA256,
    CODE_BENCHMARK_PREREGISTRATION_REQUIRED,
    CODE_BENCHMARK_REPLAY_DRIFT,
    CODE_BENCHMARK_SCHEMA_NOT_READY,
    CodeBenchmarkLedgerRepositoryError,
    SQLiteCodeHistoricalBenchmarkLedger,
)
from host_brain.planning_search import (
    BenchmarkArm,
    BenchmarkArmOutcome,
    DomainBenchmarkReport,
    DomainBenchmarkWorkload,
    SearchBudget,
)
from host_brain.planning_search.code_benchmark_ledger import (
    CodeBenchmarkExecutionBudget,
    CodeBenchmarkRunReceipt,
    CodeBenchmarkRunSpec,
    CodeBenchmarkWorkloadRegistration,
)
from host_brain.planning_search.code_benchmark_models import (
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkArmEvidence,
    CodeBenchmarkCleanupReceipt,
    CodeHistoricalBenchmarkRun,
    CodeVerifierResult,
)
from host_brain.planning_search.code_benchmark_sandbox import (
    CodeHistoricalBenchmarkSandbox,
    PrivateBareBenchmarkSource,
)
from host_brain.planning_search.code_benchmark_models import CodeBenchmarkPathPolicy


NOW = 2_006_000_000_000
COMMIT = "a" * 40
POLICY_DIGEST = "b" * 64


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "code-benchmark-ledger.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="d18-code-benchmark-ledger",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return path


def _arm(name: str) -> BenchmarkArm:
    return BenchmarkArm(
        arm_id=name,
        selected_candidate_id=f"{name}-candidate",
        selected_path_candidate_ids=(f"{name}-candidate", f"{name}-followup"),
        trace_digest="c" * 64,
        counters={"candidates": 2, "rollouts": 1},
        virtual_state_schema="natsume.planning-search.virtual-state.v1",
        virtual_state_digest="d" * 64,
    )


def _registration(**overrides) -> CodeBenchmarkWorkloadRegistration:
    workload = DomainBenchmarkWorkload(
        workload_id="code-d18-sqlite",
        domain="code",
        workload_ref="benchmark://code/d18/sqlite",
        baseline_ref="benchmark://code/d18/baseline",
        verifier_refs=("verifier://code/d18/fixture",),
        isolation={
            "sandbox_mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "source_ref": "snapshot://code/d18/sqlite",
            "source_commit": COMMIT,
            "path_policy_digest": POLICY_DIGEST,
            "executor_trust": CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
            "network_isolation": CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
        },
        budget=SearchBudget(max_candidates=4, max_expansions=2, max_rollouts=1),
        cohort_key="code-d18-sqlite",
    )
    values = {
        "workload": workload,
        "execution_budget": CodeBenchmarkExecutionBudget(
            max_tool_calls=2,
            max_wall_time_ms=10_000,
        ),
    }
    values.update(overrides)
    return CodeBenchmarkWorkloadRegistration(**values)


def _receipt(
    *,
    registration: CodeBenchmarkWorkloadRegistration | None = None,
    run_id: str = "run-d18-sqlite",
) -> CodeBenchmarkRunReceipt:
    registration = registration or _registration()
    baseline = _arm("baseline")
    selected = _arm("selected")
    spec = CodeBenchmarkRunSpec(
        run_id=run_id,
        registration=registration,
        baseline=baseline,
        selected=selected,
    )
    outcomes: list[BenchmarkArmOutcome] = []
    evidence: list[CodeBenchmarkArmEvidence] = []
    for index, arm in enumerate((baseline, selected), start=3):
        diff = str(index) * 64
        cost = {"tool_call_count": 1}
        outcomes.append(
            BenchmarkArmOutcome(
                arm_id=arm.arm_id,
                verifier_receipt_refs=(f"receipt://code/d18/{arm.arm_id}",),
                success=True,
                latency_ms=5,
                cost=cost,
                workspace_diff_digest=diff,
                executed=True,
            )
        )
        evidence.append(
            CodeBenchmarkArmEvidence(
                arm_id=arm.arm_id,
                source_ref=registration.source_ref,
                source_commit=registration.source_commit,
                path_policy_digest=registration.path_policy_digest,
                changed_file_count=1,
                changed_path_digest=("e" if arm.arm_id == "baseline" else "f") * 64,
                workspace_diff_digest=diff,
                verifier=CodeVerifierResult(
                    success=True,
                    receipt_ref=f"receipt://code/d18/{arm.arm_id}",
                    cost=cost,
                    evidence_digest=("1" if arm.arm_id == "baseline" else "2") * 64,
                ),
            )
        )
    run = CodeHistoricalBenchmarkRun(
        run_id=run_id,
        report=DomainBenchmarkReport(
            workload=registration.workload,
            baseline=baseline,
            selected=selected,
            baseline_outcome=outcomes[0],
            selected_outcome=outcomes[1],
        ),
        baseline_evidence=evidence[0],
        selected_evidence=evidence[1],
        cleanup_receipt=CodeBenchmarkCleanupReceipt(
            run_id=run_id,
            source_before_digest="5" * 64,
            source_after_digest="5" * 64,
            private_bare_removed=True,
            worktrees_removed=True,
            run_root_removed=True,
        ),
    )
    return CodeBenchmarkRunReceipt(spec=spec, run=run)


def test_migration_creates_checksum_bound_cleanup_gated_ledger(tmp_path: Path) -> None:
    path = _db(tmp_path)
    ledger = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 1)

    with sqlite3.connect(path) as conn:
        asset = conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE asset_key = ?",
            (CODE_BENCHMARK_MIGRATION_ASSET_KEY,),
        ).fetchone()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert asset == (CODE_BENCHMARK_MIGRATION_SHA256,)
    assert {
        "planning_search_code_benchmark_workload",
        "planning_search_code_benchmark_run_spec",
        "planning_search_code_benchmark_arm_execution",
        "planning_search_code_benchmark_cleanup_receipt",
        "planning_search_code_benchmark_receipt",
    } <= tables
    assert ledger.list_completed(cohort_key="code-d18-sqlite") == ()


def test_preregister_then_close_is_exact_across_restart_and_listed_by_cohort(tmp_path: Path) -> None:
    path = _db(tmp_path)
    receipt = _receipt()
    first = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 1)
    assert first.register_workload(receipt.spec.registration).to_dict() == receipt.spec.registration.to_dict()
    assert first.preregister_run(receipt.spec).to_dict() == receipt.spec.to_dict()
    stored = first.record_completed_run(receipt)
    assert stored.to_dict() == receipt.to_dict()

    restarted = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 2)
    assert restarted.register_workload(receipt.spec.registration).to_dict() == receipt.spec.registration.to_dict()
    assert restarted.preregister_run(receipt.spec).to_dict() == receipt.spec.to_dict()
    assert restarted.record_completed_run(receipt).to_dict() == receipt.to_dict()
    assert restarted.get_completed(receipt.spec.run_id).to_dict() == receipt.to_dict()
    assert tuple(item.to_dict() for item in restarted.list_completed(cohort_key="code-d18-sqlite")) == (receipt.to_dict(),)


def test_terminal_evidence_requires_registered_workload_and_preregistered_spec(tmp_path: Path) -> None:
    path = _db(tmp_path)
    receipt = _receipt()
    ledger = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 1)
    with pytest.raises(CodeBenchmarkLedgerRepositoryError) as rejected:
        ledger.record_completed_run(receipt)
    assert rejected.value.reason_code == CODE_BENCHMARK_PREREGISTRATION_REQUIRED

    ledger.register_workload(receipt.spec.registration)
    with pytest.raises(CodeBenchmarkLedgerRepositoryError) as still_rejected:
        ledger.record_completed_run(receipt)
    assert still_rejected.value.reason_code == CODE_BENCHMARK_PREREGISTRATION_REQUIRED


def test_drift_and_append_only_sql_are_rejected(tmp_path: Path) -> None:
    path = _db(tmp_path)
    receipt = _receipt()
    ledger = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 1)
    ledger.register_workload(receipt.spec.registration)
    ledger.preregister_run(receipt.spec)
    ledger.record_completed_run(receipt)

    changed_spec = CodeBenchmarkRunSpec(
        run_id=receipt.spec.run_id,
        registration=receipt.spec.registration,
        baseline=receipt.spec.baseline,
        selected=_arm("replacement"),
    )
    with pytest.raises(CodeBenchmarkLedgerRepositoryError) as drift:
        ledger.preregister_run(changed_spec)
    assert drift.value.reason_code == CODE_BENCHMARK_REPLAY_DRIFT

    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE planning_search_code_benchmark_workload SET cohort_key = 'other'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM planning_search_code_benchmark_receipt")
        with pytest.raises(sqlite3.IntegrityError, match="cleanup requires both"):
            conn.execute(
                """
                INSERT INTO planning_search_code_benchmark_cleanup_receipt(
                    run_id, cleanup_digest, cleanup_status, cleanup_json, created_at_ms
                ) VALUES ('missing-run', ?, 'VERIFIED_CLEAN', '{}', ?)
                """,
                ("9" * 64, NOW + 5),
            )


def test_schema_readiness_rejects_semantically_wrong_trigger(tmp_path: Path) -> None:
    path = _db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER code_benchmark_receipt_no_update")
        conn.execute(
            """
            CREATE TRIGGER code_benchmark_receipt_no_update
            BEFORE UPDATE ON planning_search_code_benchmark_receipt
            BEGIN
                SELECT 1;
            END
            """
        )
        conn.commit()
    with pytest.raises(CodeBenchmarkLedgerRepositoryError) as rejected:
        SQLiteCodeHistoricalBenchmarkLedger(path)
    assert rejected.value.reason_code == CODE_BENCHMARK_SCHEMA_NOT_READY


def test_catalog_replay_restores_0106_asset_without_mutating_evidence(tmp_path: Path) -> None:
    path = _db(tmp_path)
    receipt = _receipt()
    ledger = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 1)
    ledger.register_workload(receipt.spec.registration)
    ledger.preregister_run(receipt.spec)
    ledger.record_completed_run(receipt)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 106")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 106")
        conn.commit()
    replayed = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="d18-catalog-replay",
        ts_ms=NOW + 10,
    )
    assert replayed.success is True, replayed.as_dict()
    assert SQLiteCodeHistoricalBenchmarkLedger(path).get_completed(receipt.spec.run_id).to_dict() == receipt.to_dict()


def test_private_bare_pair_is_cleanup_receipted_before_durable_terminal_record(
    tmp_path: Path,
) -> None:
    source = tmp_path / "protected-source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "d18@example.invalid")
    _git(source, "config", "user.name", "d18")
    _git(source, "config", "core.autocrlf", "false")
    (source / "src").mkdir()
    (source / "src" / "app.py").write_text("value = 'base'\n", encoding="utf-8")
    _git(source, "add", "src/app.py")
    _git(source, "commit", "-m", "d18 seed")
    commit = _git(source, "rev-parse", "HEAD")
    private_root = tmp_path / "private-bares"
    worktree_root = tmp_path / "worktrees"
    private_root.mkdir()
    worktree_root.mkdir()
    policy = CodeBenchmarkPathPolicy(allowed_paths=("src",), max_changed_files=1)
    workload = DomainBenchmarkWorkload(
        workload_id="code-d18-sandbox",
        domain="code",
        workload_ref="benchmark://code/d18/sandbox",
        baseline_ref="benchmark://code/d18/baseline",
        verifier_refs=("verifier://code/d18/fixture",),
        isolation={
            "sandbox_mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "source_ref": "snapshot://code/d18/sandbox",
            "source_commit": commit,
            "path_policy_digest": policy.digest,
            "executor_trust": CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
            "network_isolation": CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
        },
        budget=SearchBudget(max_candidates=4, max_expansions=2, max_rollouts=1),
        cohort_key="code-d18-sandbox",
    )
    registration = CodeBenchmarkWorkloadRegistration(
        workload=workload,
        execution_budget=CodeBenchmarkExecutionBudget(max_tool_calls=2),
    )
    baseline = _arm("baseline")
    selected = _arm("selected")
    spec = CodeBenchmarkRunSpec(
        run_id="run-d18-sandbox",
        registration=registration,
        baseline=baseline,
        selected=selected,
    )

    class Writer:
        def execute(self, *, workspace, arm):  # noqa: ANN001, ANN201
            workspace.write_text(
                "src/app.py",
                workspace.read_text("src/app.py").replace("base", arm.arm_id),
            )
            return {"tool_call_count": 1}

    class Verifier:
        def verify(self, *, workspace, arm):  # noqa: ANN001, ANN201
            content = workspace.read_text("src/app.py")
            return CodeVerifierResult(
                success=arm.arm_id in content,
                receipt_ref=f"receipt://code/d18/{arm.arm_id}",
                cost={"tool_call_count": 1},
                evidence_digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )

    assert _git(
        source,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    ) == ""
    sandbox = CodeHistoricalBenchmarkSandbox(
        source=PrivateBareBenchmarkSource(
            source_repository=source,
            private_bare_root=private_root,
            worktree_root=worktree_root,
            protected_roots=(source,),
            source_ref="snapshot://code/d18/sandbox",
            source_commit=commit,
        )
    )
    run = sandbox.run_paired(
        run_id=spec.run_id,
        workload=workload,
        baseline=baseline,
        selected=selected,
        path_policy=policy,
        baseline_executor=Writer(),
        selected_executor=Writer(),
        verifier=Verifier(),
    )
    assert run.cleanup_receipt is not None
    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []

    path = _db(tmp_path)
    ledger = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 1)
    ledger.register_workload(registration)
    ledger.preregister_run(spec)
    stored = ledger.record_completed_run(CodeBenchmarkRunReceipt(spec=spec, run=run))
    assert stored.run.cleanup_receipt is not None
    assert stored.run.cleanup_receipt.receipt_digest == run.cleanup_receipt.receipt_digest


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(path), *args),
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()
