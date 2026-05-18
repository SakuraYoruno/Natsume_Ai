from __future__ import annotations

import argparse
import subprocess

from tools.validate_live_acceptance import (
    build_arg_parser,
    build_validation_plan,
    _run_command_step,
    _summary_passed,
    ValidationCommand,
)


def test_live_acceptance_parser_defaults() -> None:
    args = build_arg_parser().parse_args([])

    assert args.organ_iterations == 5
    assert args.max_turns == 4
    assert args.fail_fast is False


def test_live_acceptance_plan_contains_required_steps(tmp_path) -> None:
    args = argparse.Namespace(run_id="unit", organ_iterations=2, max_turns=3)

    plan = build_validation_plan(args, tmp_path)

    assert [item.name for item in plan] == [
        "service_integration_tests",
        "surface_memory_emotion_loop",
        "surface_organ_system",
        "organ_influence_matrix",
        "inner_world_self_loop",
        "full_digital_life_loop",
        "digital_life_background_runtime",
    ]
    full_loop = next(item for item in plan if item.name == "full_digital_life_loop")
    assert "--max-turns" in full_loop.command
    assert "3" in full_loop.command


def test_summary_passed_accepts_boolean_and_pass_status() -> None:
    assert _summary_passed({"passed": True}) is True
    assert _summary_passed({"passed": False}) is False
    assert _summary_passed({"status": "PASS"}) is True
    assert _summary_passed({"status": "FAIL"}) is False
    assert _summary_passed({}) is True


def test_run_command_step_reads_tool_summary(tmp_path) -> None:
    command = ValidationCommand(
        name="fake_tool",
        command=("python", "fake.py"),
        out_dir=tmp_path,
        timeout_seconds=1,
    )

    def runner(*_args):
        (tmp_path / "summary.json").write_text('{"passed": true, "value": 1}', encoding="utf-8")
        return subprocess.CompletedProcess(args=["python", "fake.py"], returncode=0, stdout="ok", stderr="")

    result = _run_command_step(command, runner=runner)

    assert result.passed is True
    assert result.exit_code == 0
    assert result.summary == {"passed": True, "value": 1}
    assert (tmp_path / "stdout.log").read_text(encoding="utf-8") == "ok"
    assert (tmp_path / "acceptance_step.json").exists()
