from __future__ import annotations

import asyncio
import json

from tools.validate_long_run_digital_life_loop import (
    build_arg_parser,
    _variation_threshold,
    run_long_run_digital_life_loop_validation,
)


def test_long_run_parser_defaults_to_30_plus_turns() -> None:
    args = build_arg_parser().parse_args([])

    assert args.turns >= 30
    assert args.min_turns == 30


def test_variation_threshold_scales_for_live_smoke_and_long_run() -> None:
    assert _variation_threshold(1) == 1
    assert _variation_threshold(3) == 2
    assert _variation_threshold(5) == 2
    assert _variation_threshold(6) == 3
    assert _variation_threshold(32) == 3


def test_long_run_fixture_validates_timeline_contract(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        [
            "--mode",
            "fixture",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-long",
            "--turns",
            "6",
            "--min-turns",
            "6",
        ]
    )

    result = asyncio.run(run_long_run_digital_life_loop_validation(args))

    summary = result["summary"]
    assert summary["passed"] is True
    assert summary["service_boundary_passed"] is True
    assert summary["idle_loop_passed"] is True
    assert summary["idle_loop_tick_count"] == 8
    assert summary["idle_loop_simulated_hours"] == 24
    assert summary["service_boundary_components"]["chroma"]["runtime_class"] == "mock"
    assert summary["turn_count"] == 6
    assert summary["checks"]["service_boundary.fixture_marks_mock_components"] is True
    assert summary["checks"]["idle_loop.idle_loop_has_no_surface_replies"] is True
    assert summary["checks"]["idle_loop.inner_world_appends_new_private_event_each_tick"] is True
    assert summary["checks"]["long_run.reaches_minimum_turns"] is True
    assert "long_run.reaches_minimum_30_turns" not in summary["checks"]
    assert summary["checks"]["long_run.timeline_has_required_event_types"] is True
    assert summary["checks"]["long_run.each_turn_has_idle_private_thought"] is True
    assert summary["checks"]["long_run.all_turns_map_23_organs"] is True

    timeline = (tmp_path / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(timeline) >= 6 * 8
    first = json.loads(timeline[0])
    assert first["event_type"] == "user_turn"
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "report.md").exists()
