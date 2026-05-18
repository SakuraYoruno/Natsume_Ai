from __future__ import annotations

import argparse
import asyncio
import json

from tools.validate_surface_memory_emotion_loop import build_arg_parser, run_surface_memory_emotion_loop


def test_surface_memory_emotion_loop_parser_accepts_live_mode() -> None:
    args = build_arg_parser().parse_args(["--mode", "live", "--out-dir", "out"])

    assert args.mode == "live"
    assert args.out_dir == "out"


def test_surface_memory_emotion_loop_fixture_validates_full_contract(tmp_path) -> None:
    result = asyncio.run(
        run_surface_memory_emotion_loop(
            argparse.Namespace(out_dir=str(tmp_path))
        )
    )

    summary = result["summary"]
    details = result["details"]

    assert summary["passed"] is True
    assert summary["evaluation_mode"] == "contract_validation_no_text_quality_scoring"
    assert summary["user_emotion_tag"] == "SAD"
    assert summary["natsume_emotion_tag"] == "CALM"
    assert summary["vts_expression_id"]
    assert summary["bond_status"] == "APPLIED"
    assert summary["bond_update_kind"] == "EMOTIONAL_SUPPORT"
    assert all(summary["checks"].values())

    assert details["memory_write"]["success"] is True
    assert "输入输出" in "\n".join(details["recall_packet"]["facts"])
    assert "episodic" in details["recall_packet"]["route_ids"]
    assert "bond" in details["recall_packet"]["route_ids"]
    assert details["surface_result"]["selected_text"] == "记得，上次卡在输入输出那块。先别急，这轮就看那一处。"
    assert details["bond_state"]["event_count"] == 1
    assert details["bond_state"]["recent_scores"]
    assert details["safe_bond_summary"]["capability_hint"]

    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "report.md").exists()
    saved_summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved_summary["passed"] is True
