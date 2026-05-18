from __future__ import annotations

from tools.validate_organ_runtime_integration import build_arg_parser, run_validation


def test_validate_organ_runtime_integration_fixture_mode(tmp_path) -> None:
    args = build_arg_parser().parse_args(["--mode", "fixture", "--out-dir", str(tmp_path)])

    result = run_validation(mode=args.mode, out_dir=args.out_dir)

    summary = result["summary"]
    assert summary["status"] == "PASS"
    assert summary["mode"] == "fixture"
    assert summary["organ_count"] == 23
    assert summary["checks"]["fixture_matrix_passed"] is True
    assert summary["checks"]["fixture_each_organ_has_contract_and_surface_effect"] is True
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "runtime_rows.jsonl").exists()
    assert (tmp_path / "report.md").exists()


def test_validate_organ_runtime_integration_parser_defaults() -> None:
    args = build_arg_parser().parse_args([])

    assert args.mode == "fixture"
    assert args.iterations == 1
