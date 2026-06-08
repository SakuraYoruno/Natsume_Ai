from __future__ import annotations

from tools.validate_organ_effect_ab import build_arg_parser, run_validation


def test_validate_organ_effect_ab_fixture(tmp_path) -> None:
    args = build_arg_parser().parse_args(["--out-dir", str(tmp_path)])

    result = run_validation(out_dir=args.out_dir)

    summary = result["summary"]
    rows = result["details"]["rows"]
    assert summary["status"] == "PASS"
    assert summary["evaluation_mode"] == "organ_effect_ab_contract_validation_no_text_quality_scoring"
    assert summary["organ_count"] == 23
    assert summary["changed_organ_count"] == 23
    assert len(rows) == 23
    assert all(row["contract_changes_within_allowed_fields"] for row in rows)
    assert all(row["surface_changes_within_allowed_slots"] for row in rows)
    assert all(row["main_chain_effects_within_declared_effects"] for row in rows)
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "effect_ab.jsonl").exists()
    assert (tmp_path / "report.md").exists()
