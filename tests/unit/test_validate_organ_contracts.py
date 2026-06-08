from __future__ import annotations

from tools.validate_organ_contracts import build_arg_parser, run_validation


def test_validate_organ_contracts_parser_defaults() -> None:
    args = build_arg_parser().parse_args([])

    assert args.runtime_mode == "fixture"
    assert args.iterations == 1


def test_validate_organ_contracts_fixture_runtime(tmp_path) -> None:
    args = build_arg_parser().parse_args(["--runtime-mode", "fixture", "--out-dir", str(tmp_path)])

    result = run_validation(runtime_mode=args.runtime_mode, out_dir=args.out_dir)

    summary = result["summary"]
    rows = result["details"]["matrix_rows"]
    assert summary["status"] == "PASS"
    assert summary["evaluation_mode"] == "organ_contract_matrix_validation_no_text_quality_scoring"
    assert summary["runtime_mode"] == "fixture"
    assert summary["organ_count"] == 23
    assert len(rows) == 23
    assert summary["checks"]["matrix_covers_23_surface_organs"] is True
    assert summary["checks"]["runtime_integration_validation_passed"] is True
    assert summary["checks"]["runtime_rows_cover_contract_matrix"] is True
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "organ_contract_matrix.json").exists()
    assert (tmp_path / "organ_contract_matrix.jsonl").exists()
    assert (tmp_path / "report.md").exists()
