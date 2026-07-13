from __future__ import annotations

from pathlib import Path

from tools.run_core_release import (
    AcceptanceStage,
    StageOutcome,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    _evaluate_architecture_gates,
    _run_isolated_migration_backup_restore,
    _run_rollback_drill,
    build_acceptance_plan,
    read_aw08_state,
    run_acceptance_plan,
)


ROOT = Path(__file__).resolve().parents[2]


def _outcome(stage: AcceptanceStage, status: str) -> StageOutcome:
    return StageOutcome(
        stage_id=stage.stage_id,
        status=status,
        started_at="2026-07-13T00:00:00+00:00",
        finished_at="2026-07-13T00:00:01+00:00",
        duration_ms=1_000,
        exit_code=0 if status == STATUS_PASS else 1,
        command=stage.command,
        log_ref=f"docs/50_release/core/runs/test/{stage.stage_id}.log",
        detail="fake acceptance outcome",
    )


def test_core_release_plan_is_complete_and_has_one_repository_wide_pytest() -> None:
    plan = build_acceptance_plan(python_executable="python")
    stage_ids = [stage.stage_id for stage in plan]

    assert stage_ids == [
        "architecture_static_gates",
        "offline_pytest",
        "authority_lineage_contracts",
        "contract_schema_contracts",
        "state_outbox_contracts",
        "namespace_security_contracts",
        "persistence_boundary_contracts",
        "service_bundle_contracts",
        "operator_query_contracts",
        "golden_replay_chaos_identity",
        "monitor_generated_client",
        "monitor_typecheck",
        "monitor_operator_client_tests",
        "monitor_read_model_tests",
        "monitor_build",
        "monitor_interaction_smoke",
        "edge_standalone",
        "migration_backup_restore",
        "isolated_runtime_http_drill",
        "rollback_drill",
        "security_smoke",
        "performance_smoke",
    ]
    repo_wide_pytest = [
        stage
        for stage in plan
        if stage.command[:3] == ("python", "-m", "pytest")
        and not any(argument.startswith("tests/") for argument in stage.command[3:])
    ]
    assert [stage.stage_id for stage in repo_wide_pytest] == ["offline_pytest"]
    offline = next(stage for stage in plan if stage.stage_id == "offline_pytest")
    assert "not hw and not prod_env and not runtime_probe and not docker" in offline.command
    interaction = next(stage for stage in plan if stage.stage_id == "monitor_interaction_smoke")
    assert interaction.kind == "internal"
    assert "real isolated Obs process" in interaction.description


def test_acceptance_is_fail_fast_and_never_automatically_retries(tmp_path: Path) -> None:
    plan = (
        AcceptanceStage("first", "command", ("fake", "first")),
        AcceptanceStage("fails", "command", ("fake", "fails")),
        AcceptanceStage("must_not_run", "command", ("fake", "last")),
    )
    calls: list[str] = []

    def fake_runner(stage: AcceptanceStage, _root: Path, _run_dir: Path, _run_id: str) -> StageOutcome:
        calls.append(stage.stage_id)
        return _outcome(stage, STATUS_FAIL if stage.stage_id == "fails" else STATUS_PASS)

    receipt = run_acceptance_plan(
        root=ROOT,
        run_id="acceptance-contract-failure",
        run_dir=tmp_path,
        stages=plan,
        stage_runner=fake_runner,
    )

    assert calls == ["first", "fails"]
    assert receipt["status"] == STATUS_FAIL
    assert receipt["automatic_retry_count"] == 0
    assert [row["status"] for row in receipt["stages"]] == [
        STATUS_PASS,
        STATUS_FAIL,
        STATUS_SKIPPED,
    ]
    assert receipt["failed_stage_count"] == 1
    assert receipt["skipped_stage_count"] == 1


def test_acceptance_receipt_binds_one_run_id_across_every_stage(tmp_path: Path) -> None:
    plan = (
        AcceptanceStage("alpha", "command", ("fake", "alpha")),
        AcceptanceStage("beta", "internal"),
    )
    observed_run_ids: list[str] = []
    persisted: list[dict[str, object]] = []

    def fake_runner(stage: AcceptanceStage, _root: Path, _run_dir: Path, run_id: str) -> StageOutcome:
        observed_run_ids.append(run_id)
        return _outcome(stage, STATUS_PASS)

    receipt = run_acceptance_plan(
        root=ROOT,
        run_id="acceptance-contract-pass",
        run_dir=tmp_path,
        stages=plan,
        stage_runner=fake_runner,
        on_update=lambda payload: persisted.append(payload),
    )

    assert receipt["status"] == STATUS_PASS
    assert receipt["run_id"] == "acceptance-contract-pass"
    assert observed_run_ids == ["acceptance-contract-pass", "acceptance-contract-pass"]
    assert receipt["passed_stage_count"] == 2
    assert persisted[-1]["status"] == STATUS_PASS


def test_green_stages_cannot_pass_gates_when_per_gate_evidence_is_missing() -> None:
    plan = build_acceptance_plan(python_executable="python")
    receipt = {
        "status": STATUS_PASS,
        "stages": [
            {
                "stage_id": stage.stage_id,
                "status": STATUS_PASS,
                "evidence": {},
            }
            for stage in plan
        ],
    }

    gates = _evaluate_architecture_gates(
        receipt=receipt,
        compatibility={"status": STATUS_PASS, "active_count": 0},
        retired_paths={"status": STATUS_PASS, "remaining_paths": []},
        run_id="missing-gate-evidence",
        receipt_ref="docs/50_release/core/runs/test/acceptance_receipt.json",
    )

    assert [row["gate"] for row in gates] == list(range(1, 16))
    assert [row["status"] for row in gates[:14]] == [STATUS_FAIL] * 14
    assert gates[14]["status"] == STATUS_PASS
    assert "required static Gate 1 evidence is MISSING" in gates[0]["issues"]


def test_required_not_run_or_failed_gate_evidence_is_fail_closed() -> None:
    static_results = {
        str(gate): {"status": STATUS_PASS, "issues": []}
        for gate in range(1, 15)
    }
    stage_rows = [
        {"stage_id": stage.stage_id, "status": STATUS_PASS, "evidence": {}}
        for stage in build_acceptance_plan(python_executable="python")
    ]
    static_stage = next(row for row in stage_rows if row["stage_id"] == "architecture_static_gates")
    static_stage["evidence"] = {"gate_results": static_results}
    next(row for row in stage_rows if row["stage_id"] == "state_outbox_contracts")["status"] = STATUS_SKIPPED

    gates = _evaluate_architecture_gates(
        receipt={"status": STATUS_PASS, "stages": stage_rows},
        compatibility={"status": STATUS_PASS, "active_count": 0},
        retired_paths={"status": STATUS_PASS, "remaining_paths": []},
        run_id="not-run-gate-evidence",
        receipt_ref="docs/50_release/core/runs/test/acceptance_receipt.json",
    )

    assert gates[5]["status"] == STATUS_FAIL
    assert gates[6]["status"] == STATUS_FAIL
    assert any("state_outbox_contracts is SKIPPED" in issue for issue in gates[5]["issues"])


def test_aw08_state_gate_reads_only_the_aw08_section(tmp_path: Path) -> None:
    document = tmp_path / (
        "docs/30_devspec/NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md"
    )
    document.parent.mkdir(parents=True)
    document.write_text(
        "### AW-07：Operator\n\n状态：`DONE`\n\n"
        "### AW-08：兼容层退役与 Core Release\n\n状态：`CODE_COMPLETE`\n",
        encoding="utf-8",
    )

    assert read_aw08_state(tmp_path) == "CODE_COMPLETE"


def test_isolated_migration_backup_restore_uses_current_catalog() -> None:
    result = _run_isolated_migration_backup_restore(
        root=ROOT,
        run_id="acceptance-contract-migration",
    )

    assert result["status"] == STATUS_PASS
    assert result["migration_latest_version"] >= 83
    assert result["migration_asset_count"] > 0
    assert result["restore_integrity_check"] == "ok"
    assert len(result["migration_catalog_digest"]) == 64


def test_rollback_drill_treats_detected_conflict_as_fail_closed_success() -> None:
    result = _run_rollback_drill(root=ROOT, run_id="acceptance-contract-rollback")

    assert result["status"] == STATUS_PASS
    assert result["component_count"] == 5
    assert result["all_components_reversible"] is True
    assert result["conflict_detection"] == {
        "status": "FAILED",
        "stage": "S_RELEASE_CHECKLIST",
        "reason_code": "RELEASE_ROLLBACK_TARGET_MISSING",
    }
