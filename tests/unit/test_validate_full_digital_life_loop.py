from __future__ import annotations

import asyncio
import json

from tools.validate_full_digital_life_loop import (
    build_arg_parser,
    run_full_digital_life_loop_validation,
)


def test_full_digital_life_loop_parser_accepts_live_mode() -> None:
    args = build_arg_parser().parse_args(["--mode", "live", "--out-dir", "out", "--max-turns", "2"])

    assert args.mode == "live"
    assert args.out_dir == "out"
    assert args.max_turns == 2


def test_full_digital_life_loop_fixture_validates_contract(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        ["--mode", "fixture", "--out-dir", str(tmp_path), "--run-id", "unit", "--max-turns", "2"]
    )

    result = asyncio.run(run_full_digital_life_loop_validation(args))

    summary = result["summary"]
    details = result["details"]
    assert summary["passed"] is True
    assert summary["evaluation_mode"] == "contract_validation_no_text_quality_scoring"
    assert summary["turn_count"] == 2
    assert summary["min_mapped_organ_count"] == 23
    assert summary["checks"]["surface_selection_is_direct_no_score"] is True
    assert summary["checks"]["mapped_surface_organs_present"] is True
    assert summary["checks"]["inner_world_uses_llm_source_only"] is True
    assert details["turns"][0]["reply_text"]
    assert details["turns"][0]["surface_context_packet"]["surface_influence_packet"]["organ_trace"]["mapped_organ_count"] == 23

    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "report.md").exists()
    saved_summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved_summary["passed"] is True
