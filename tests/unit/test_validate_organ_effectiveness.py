from __future__ import annotations

from tools.validate_organ_effectiveness import build_arg_parser, run_validation


def test_validate_organ_effectiveness_parser_defaults() -> None:
    args = build_arg_parser().parse_args([])

    assert args.mode == "fixture"
    assert args.turns == 4
    assert args.runtime_iterations == 1


def test_validate_organ_effectiveness_fixture(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        [
            "--mode",
            "fixture",
            "--turns",
            "2",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-organ-effectiveness",
        ]
    )

    result = run_validation(
        mode=args.mode,
        turns=args.turns,
        runtime_iterations=args.runtime_iterations,
        out_dir=args.out_dir,
        run_id=args.run_id,
    )

    summary = result["summary"]
    rows = result["details"]["rows"]
    assert summary["status"] == "PASS"
    assert summary["evaluation_mode"] == "organ_effectiveness_contract_validation_no_text_quality_scoring"
    assert summary["organ_count"] == 23
    assert summary["category_counts"] == {"active": 23}
    assert summary["checks"]["all_organs_are_runtime_active"] is True
    assert len(rows) == 23
    assert all(row["category"] == "active" for row in rows)
    assert all(row["ab_reply_control_changed"] for row in rows)
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "organ_effectiveness.jsonl").exists()
    assert (tmp_path / "organ_effectiveness_report.md").exists()
