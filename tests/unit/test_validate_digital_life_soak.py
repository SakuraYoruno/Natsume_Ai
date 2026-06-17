from __future__ import annotations

import asyncio
import json

from tools.validate_digital_life_soak import build_arg_parser, run_digital_life_soak_validation


def test_digital_life_soak_parser_defaults_to_30_plus_turns() -> None:
    args = build_arg_parser().parse_args([])

    assert args.mode == "fixture"
    assert args.turns >= 30
    assert args.min_turns == 30
    assert args.idle_ticks == 8


def test_digital_life_soak_fixture_smoke(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        [
            "--mode",
            "fixture",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-soak",
            "--turns",
            "6",
            "--min-turns",
            "6",
        ]
    )

    result = asyncio.run(run_digital_life_soak_validation(args))

    summary = result["summary"]
    details = result["details"]
    assert summary["status"] == "FAIL"
    assert summary["evaluation_mode"] == "digital_life_soak_contract_validation_no_text_quality_scoring"
    assert summary["turn_count"] == 6
    assert summary["causality_turn_count"] == 6
    assert summary["drift_issue_count"] == 1
    assert summary["checks"]["long_run_validation_passed"] is False
    assert summary["checks"]["soak_has_causality_for_each_turn"] is True
    assert summary["checks"]["soak_all_turns_map_23_organs"] is True
    assert summary["checks"]["soak_no_identity_drift"] is True
    assert summary["checks"]["soak_no_internal_or_private_leak"] is True
    assert summary["checks"]["soak_emotion_varies"] is False
    assert details["drift"]["flags"] == {"emotion_monotony": True}
    assert len(details["causality_traces"]) == 6
    assert all(trace["active_organ_count"] == 23 for trace in details["causality_traces"])

    summary_payload = json.loads((tmp_path / "soak_summary.json").read_text(encoding="utf-8"))
    assert summary_payload["status"] == "FAIL"
    assert (tmp_path / "soak_details.json").exists()
    assert (tmp_path / "soak_timeline.jsonl").exists()
    assert (tmp_path / "soak_causality.jsonl").exists()
    assert (tmp_path / "soak_report.md").exists()
