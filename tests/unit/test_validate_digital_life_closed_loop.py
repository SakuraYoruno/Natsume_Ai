from __future__ import annotations

import json

from tools.validate_digital_life_closed_loop import (
    build_arg_parser,
    run_digital_life_closed_loop_validation,
)


def test_digital_life_closed_loop_parser_defaults_to_long_run() -> None:
    args = build_arg_parser().parse_args([])

    assert args.mode == "fixture"
    assert args.turns >= 30
    assert args.min_turns == 30
    assert args.effectiveness_turns == 4


def test_digital_life_closed_loop_fixture_smoke(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        [
            "--mode",
            "fixture",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-closed-loop",
            "--turns",
            "6",
            "--min-turns",
            "6",
            "--effectiveness-turns",
            "2",
        ]
    )

    result = run_digital_life_closed_loop_validation(args)

    summary = result["summary"]
    details = result["details"]
    assert summary["status"] == "PASS"
    assert summary["evaluation_mode"] == "digital_life_closed_loop_contract_validation_no_text_quality_scoring"
    assert summary["turn_count"] == 6
    assert summary["causality_turn_count"] == 6
    assert summary["active_organ_count"] == 23
    assert summary["closed_cycle_count"] == summary["cycle_count"]
    assert summary["checks"]["soak_validation_passed"] is True
    assert summary["checks"]["background_runtime_validation_passed"] is True
    assert summary["checks"]["organ_effectiveness_validation_passed"] is True
    assert summary["checks"]["all_declared_closure_cycles_pass"] is True
    assert summary["checks"]["closed_loop_covers_23_organs"] is True
    assert len(details["cycles"]) >= 6
    assert all(cycle["passed"] for cycle in details["cycles"])

    saved_summary = json.loads((tmp_path / "closed_loop_summary.json").read_text(encoding="utf-8"))
    assert saved_summary["status"] == "PASS"
    assert (tmp_path / "closed_loop_details.json").exists()
    assert (tmp_path / "closed_loop_cycles.jsonl").exists()
    assert (tmp_path / "closed_loop_report.md").exists()
