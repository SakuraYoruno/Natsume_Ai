from __future__ import annotations

import asyncio
import json

from tools.validate_long_run_digital_life_loop import (
    build_arg_parser,
    run_long_run_digital_life_loop_validation,
)


def test_long_run_parser_defaults_to_30_plus_turns() -> None:
    args = build_arg_parser().parse_args([])

    assert args.turns >= 30
    assert args.min_turns == 30


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
    assert summary["turn_count"] == 6
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
