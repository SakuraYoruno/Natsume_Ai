from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.run_core_release import (
    AcceptanceStage,
    RECEIPT_SCHEMA_VERSION,
    StageOutcome,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    _default_stage_runner,
    _evaluate_architecture_gates,
    _resolve_subprocess_command,
    _run_isolated_migration_backup_restore,
    _run_monitor_ui_interaction_smoke,
    _run_rollback_drill,
    build_acceptance_plan,
    main,
    read_aw08_state,
    resume_acceptance_plan,
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


def _skipped_row(stage: AcceptanceStage) -> dict[str, object]:
    now = "2026-07-13T00:00:02+00:00"
    return StageOutcome(
        stage_id=stage.stage_id,
        status=STATUS_SKIPPED,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        exit_code=None,
        command=stage.command,
        log_ref=None,
        detail="fake skipped outcome",
    ).as_dict()


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


def test_subprocess_command_resolves_windows_npm_shim_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    npm_cmd = tmp_path / "npm.CMD"
    observed: list[tuple[str, str | None]] = []

    def fake_which(executable: str, *, path: str | None = None) -> str | None:
        observed.append((executable, path))
        return str(npm_cmd) if executable == "npm" else None

    monkeypatch.setattr("tools.run_core_release.shutil.which", fake_which)
    resolved = _resolve_subprocess_command(
        ("npm", "run", "build"),
        environment={"PATH": "test-path"},
    )

    assert resolved == (str(npm_cmd.resolve()), "run", "build")
    assert observed == [("npm", "test-path")]
    with pytest.raises(FileNotFoundError, match="could not be resolved"):
        _resolve_subprocess_command(("missing-tool",), environment={"PATH": "test-path"})


def test_command_stage_launches_resolved_executable_and_records_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    npm_cmd = tmp_path / "npm.CMD"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    launched: list[list[str]] = []
    monkeypatch.setattr(
        "tools.run_core_release.shutil.which",
        lambda _executable, *, path=None: str(npm_cmd),
    )

    def fake_run(command: list[str], **_kwargs):
        launched.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("tools.run_core_release.subprocess.run", fake_run)
    stage = AcceptanceStage("monitor_build", "command", ("npm", "run", "build"))
    outcome = _default_stage_runner(stage, tmp_path, run_dir, "npm-resolution-test")

    assert outcome.status == STATUS_PASS
    assert launched == [[str(npm_cmd.resolve()), "run", "build"]]
    resolution = dict((outcome.evidence or {})["executable_resolution"])
    assert resolution == {
        "method": "shutil.which",
        "requested_executable": "npm",
        "resolved_executable": str(npm_cmd.resolve()),
    }


def test_monitor_interaction_smoke_resolves_npm_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "host_brain/app/monitor_ui").mkdir(parents=True)
    npm_cmd = tmp_path / "npm.CMD"
    resolved_requests: list[tuple[str, ...]] = []
    launched: list[list[str]] = []

    class FakeProcess:
        pid = 1234
        returncode: int | None = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):  # noqa: ARG002
            return self.returncode

    def fake_resolve(command, *, environment=None):  # noqa: ANN001, ARG001
        resolved_requests.append(tuple(command))
        return (str(npm_cmd.resolve()), *(str(value) for value in command[1:]))

    monkeypatch.setattr("tools.run_core_release._reserve_loopback_port", lambda: 19001)
    monkeypatch.setattr("tools.run_core_release.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(
        "tools.run_core_release._wait_for_http_json",
        lambda *_args, **_kwargs: {
            "status_code": 200,
            "payload": {"projection": {"schema_version": "test.v1"}},
        },
    )
    monkeypatch.setattr("tools.run_core_release._resolve_subprocess_command", fake_resolve)

    def fake_run(command: list[str], **_kwargs):
        launched.append(command)
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    monkeypatch.setattr("tools.run_core_release.subprocess.run", fake_run)
    result = _run_monitor_ui_interaction_smoke(root=tmp_path, run_id="npm-interaction-test")

    assert resolved_requests == [
        ("npm", "exec", "playwright", "--", "test", "e2e/core-release-real-interaction.spec.js")
    ]
    assert launched[0][0] == str(npm_cmd.resolve())
    assert result["npm_executable_resolution"]["resolved_executable"] == str(npm_cmd.resolve())


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


def test_explicit_resume_keeps_pass_prefix_and_continues_from_fail(tmp_path: Path) -> None:
    plan = (
        AcceptanceStage("already_passed", "command", ("fake", "first")),
        AcceptanceStage("failed_before", "command", ("fake", "second")),
        AcceptanceStage("skipped_before", "command", ("fake", "third")),
    )
    initial_calls: list[str] = []

    def initial_runner_without_log(
        stage: AcceptanceStage, _root: Path, _run_dir: Path, _run_id: str
    ) -> StageOutcome:
        initial_calls.append(stage.stage_id)
        status = STATUS_FAIL if stage.stage_id == "failed_before" else STATUS_PASS
        return StageOutcome(
            stage_id=stage.stage_id,
            status=status,
            started_at="2026-07-13T00:00:00+00:00",
            finished_at="2026-07-13T00:00:01+00:00",
            duration_ms=1_000,
            exit_code=0 if status == STATUS_PASS else 1,
            command=stage.command,
            log_ref=None,
            detail="fake initial outcome",
        )

    prior = run_acceptance_plan(
        root=ROOT,
        run_id="resume-contract-pass",
        run_dir=tmp_path,
        stages=plan,
        stage_runner=initial_runner_without_log,
    )
    retained_pass = json.loads(json.dumps(prior["stages"][0]))
    resumed_calls: list[str] = []

    def resumed_runner(
        stage: AcceptanceStage, _root: Path, _run_dir: Path, _run_id: str
    ) -> StageOutcome:
        resumed_calls.append(stage.stage_id)
        return _outcome(stage, STATUS_PASS)

    receipt = resume_acceptance_plan(
        root=tmp_path,
        run_id="resume-contract-pass",
        run_dir=tmp_path,
        prior_receipt=prior,
        stages=plan,
        stage_runner=resumed_runner,
        source_commit="b" * 40,
    )

    assert initial_calls == ["already_passed", "failed_before"]
    assert resumed_calls == ["failed_before", "skipped_before"]
    assert receipt["status"] == STATUS_PASS
    assert receipt["stages"][0] == retained_pass
    assert [row["status"] for row in receipt["stages"]] == [STATUS_PASS] * 3
    assert receipt["automatic_retry_count"] == 0
    assert receipt["resume_count"] == 1
    assert receipt["source_commit"] == "b" * 40
    assert receipt["resumed_from_source_commit"] == prior["source_commit"]
    assert receipt["initial_source_commit"] == prior["source_commit"]
    assert receipt["stage_source_commits"] == {
        "already_passed": prior["source_commit"],
        "failed_before": "b" * 40,
        "skipped_before": "b" * 40,
    }
    history = receipt["resume_history"][0]
    assert history["failed_stage_id"] == "failed_before"
    assert history["stages"][1]["status"] == STATUS_FAIL
    assert history["stages"][2]["status"] == STATUS_SKIPPED
    assert history["stage_source_commits"]["failed_before"] == prior["source_commit"]
    assert (tmp_path / "acceptance_receipt.attempt-1.json").is_file()


def test_explicit_resume_archives_failed_log_and_fails_fast_again(tmp_path: Path) -> None:
    plan = (
        AcceptanceStage("passed", "command", ("fake", "first")),
        AcceptanceStage("retry_me", "command", ("fake", "second")),
        AcceptanceStage("still_skipped", "command", ("fake", "third")),
    )
    run_dir = tmp_path / "runs" / "resume-log-contract"
    run_dir.mkdir(parents=True)
    failed_log = run_dir / "retry_me.log"
    failed_log.write_text("original failure evidence\n", encoding="utf-8")
    prior = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "run_id": "resume-log-contract",
        "status": STATUS_FAIL,
        "started_at": "2026-07-13T00:00:00+00:00",
        "finished_at": "2026-07-13T00:00:03+00:00",
        "source_commit": "a" * 40,
        "stage_count": 3,
        "completed_stage_count": 3,
        "automatic_retry_count": 0,
        "passed_stage_count": 1,
        "failed_stage_count": 1,
        "skipped_stage_count": 1,
        "stages": [
            {**_outcome(plan[0], STATUS_PASS).as_dict(), "log_ref": None},
            {
                **_outcome(plan[1], STATUS_FAIL).as_dict(),
                "log_ref": "runs/resume-log-contract/retry_me.log",
            },
            _skipped_row(plan[2]),
        ],
    }
    calls: list[str] = []

    def runner(stage: AcceptanceStage, _root: Path, _run_dir: Path, _run_id: str) -> StageOutcome:
        calls.append(stage.stage_id)
        return _outcome(stage, STATUS_FAIL)

    receipt = resume_acceptance_plan(
        root=tmp_path,
        run_id="resume-log-contract",
        run_dir=run_dir,
        prior_receipt=prior,
        stages=plan,
        stage_runner=runner,
        source_commit="b" * 40,
    )

    archive = run_dir / "retry_me.attempt-1.log"
    assert archive.read_text(encoding="utf-8") == "original failure evidence\n"
    assert failed_log.read_text(encoding="utf-8") == "original failure evidence\n"
    assert calls == ["retry_me"]
    assert [row["status"] for row in receipt["stages"]] == [
        STATUS_PASS,
        STATUS_FAIL,
        STATUS_SKIPPED,
    ]
    assert receipt["resume_history"][0]["failed_stage_log_ref"].endswith(
        "retry_me.attempt-1.log"
    )
    assert receipt["resume_history"][0]["failed_stage_log_sha256"].startswith("sha256:")
    archived_receipt = json.loads(
        (run_dir / "acceptance_receipt.attempt-1.json").read_text(encoding="utf-8")
    )
    assert archived_receipt["stages"][1]["log_ref"].endswith("retry_me.attempt-1.log")
    assert archived_receipt["archived_failed_log_sha256"] == receipt["resume_history"][0][
        "failed_stage_log_sha256"
    ]


def test_resume_revalidates_pass_prefix_when_source_commit_changed(tmp_path: Path) -> None:
    plan = (
        AcceptanceStage("static_pass", "command", ("fake", "static")),
        AcceptanceStage("offline_fail", "command", ("fake", "offline")),
        AcceptanceStage("later", "command", ("fake", "later")),
    )
    run_dir = tmp_path / "runs" / "resume-new-commit"
    run_dir.mkdir(parents=True)
    for stage in plan[:2]:
        (run_dir / f"{stage.stage_id}.log").write_text(
            f"{stage.stage_id} attempt one\n",
            encoding="utf-8",
        )
    prior = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "run_id": "resume-new-commit",
        "status": STATUS_FAIL,
        "started_at": "2026-07-13T00:00:00+00:00",
        "finished_at": "2026-07-13T00:00:03+00:00",
        "source_commit": "a" * 40,
        "stage_count": 3,
        "completed_stage_count": 3,
        "automatic_retry_count": 0,
        "passed_stage_count": 1,
        "failed_stage_count": 1,
        "skipped_stage_count": 1,
        "stages": [
            {**_outcome(plan[0], STATUS_PASS).as_dict(), "log_ref": "runs/resume-new-commit/static_pass.log"},
            {**_outcome(plan[1], STATUS_FAIL).as_dict(), "log_ref": "runs/resume-new-commit/offline_fail.log"},
            _skipped_row(plan[2]),
        ],
    }
    calls: list[str] = []

    def runner(stage: AcceptanceStage, _root: Path, _run_dir: Path, _run_id: str) -> StageOutcome:
        calls.append(stage.stage_id)
        return _outcome(stage, STATUS_PASS)

    receipt = resume_acceptance_plan(
        root=tmp_path,
        run_id="resume-new-commit",
        run_dir=run_dir,
        prior_receipt=prior,
        stages=plan,
        stage_runner=runner,
        source_commit="b" * 40,
        revalidate_pass_prefix_on_source_change=True,
    )

    assert calls == ["static_pass", "offline_fail", "later"]
    assert receipt["status"] == STATUS_PASS
    assert set(receipt["stage_source_commits"].values()) == {"b" * 40}
    history = receipt["resume_history"][0]
    assert history["restart_stage_id"] == "static_pass"
    assert history["revalidated_pass_prefix"] is True
    assert set(history["archived_stage_logs"]) == {"static_pass", "offline_fail"}
    assert (run_dir / "static_pass.attempt-1.log").is_file()
    assert (run_dir / "offline_fail.attempt-1.log").is_file()


def test_resume_rejects_invalid_fail_fast_topology_before_running(tmp_path: Path) -> None:
    plan = (
        AcceptanceStage("first", "command", ("fake", "first")),
        AcceptanceStage("second", "command", ("fake", "second")),
    )
    prior = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "run_id": "invalid-resume",
        "status": STATUS_FAIL,
        "started_at": "2026-07-13T00:00:00+00:00",
        "finished_at": "2026-07-13T00:00:02+00:00",
        "source_commit": "a" * 40,
        "stage_count": 2,
        "completed_stage_count": 2,
        "automatic_retry_count": 0,
        "passed_stage_count": 1,
        "failed_stage_count": 1,
        "skipped_stage_count": 0,
        "stages": [_outcome(plan[0], STATUS_FAIL).as_dict(), _outcome(plan[1], STATUS_PASS).as_dict()],
    }

    with pytest.raises(ValueError, match="only SKIPPED stages after FAIL"):
        resume_acceptance_plan(
            root=tmp_path,
            run_id="invalid-resume",
            run_dir=tmp_path,
            prior_receipt=prior,
            stages=plan,
            stage_runner=lambda *_args: pytest.fail("invalid receipt must not execute a stage"),
            source_commit="b" * 40,
        )


def test_resume_cli_requires_accept_and_explicit_run_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--resume", "--run-id", "existing"]) == 2
    assert "--resume requires --accept" in capsys.readouterr().err
    assert main(["--accept", "--resume"]) == 2
    assert "--resume requires an explicit --run-id" in capsys.readouterr().err


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
