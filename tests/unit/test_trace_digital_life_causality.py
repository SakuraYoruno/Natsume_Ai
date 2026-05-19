from __future__ import annotations

from tools.trace_digital_life_causality import build_arg_parser, run_validation


def test_trace_digital_life_causality_parser_defaults() -> None:
    args = build_arg_parser().parse_args([])

    assert args.mode == "fixture"
    assert args.turns == 4
    assert args.details_json is None


def test_trace_digital_life_causality_fixture(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        [
            "--mode",
            "fixture",
            "--turns",
            "2",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-causality",
        ]
    )

    result = run_validation(mode=args.mode, turns=args.turns, out_dir=args.out_dir, run_id=args.run_id)

    summary = result["summary"]
    traces = result["details"]["traces"]
    assert summary["status"] == "PASS"
    assert summary["evaluation_mode"] == "digital_life_causality_trace_contract_no_text_quality_scoring"
    assert summary["turn_count"] == 2
    assert summary["checks"]["each_turn_has_organ_influence_trace"] is True
    assert summary["checks"]["each_turn_has_control_deltas_and_reply_control"] is True
    assert len(traces) == 2
    assert all(trace["active_organ_count"] == 23 for trace in traces)
    assert all(trace["reply"]["text"] for trace in traces)
    assert all(trace["memory"]["write_success"] for trace in traces)
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "turn_causality.jsonl").exists()
    assert (tmp_path / "causality_report.md").exists()


def test_trace_digital_life_causality_can_parse_existing_details(tmp_path) -> None:
    first = run_validation(mode="fixture", turns=1, out_dir=tmp_path / "source", run_id="unit-causality-source")
    details_json = tmp_path / "source" / "full_loop" / "details.json"

    result = run_validation(details_json=details_json, out_dir=tmp_path / "offline")

    assert first["summary"]["status"] == "PASS"
    assert result["summary"]["status"] == "PASS"
    assert result["summary"]["source_details_json"] == str(details_json)
    assert result["summary"]["turn_count"] == 1
