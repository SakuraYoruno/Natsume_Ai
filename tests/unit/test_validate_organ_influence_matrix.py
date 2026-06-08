from __future__ import annotations

from tools.validate_organ_influence_matrix import build_arg_parser, run_validation


def test_validate_organ_influence_matrix_fixture(tmp_path) -> None:
    args = build_arg_parser().parse_args(["--out-dir", str(tmp_path)])

    result = run_validation(out_dir=args.out_dir)

    summary = result["summary"]
    rows = result["details"]["rows"]
    assert summary["status"] == "PASS"
    assert summary["evaluation_mode"] == "contract_validation_no_text_quality_scoring"
    assert summary["organ_count"] == 23
    assert summary["effective_organ_count"] == 23
    assert len(rows) == 23
    assert all(row["mapped_self"] for row in rows)
    assert all(row["has_final_packet_effect"] for row in rows)
    assert all(row["effect_hits_allowed_contract"] for row in rows)
    assert all(row["raw_context_dropped"] for row in rows)
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "influence_matrix.jsonl").exists()
    assert (tmp_path / "report.md").exists()
