from __future__ import annotations

import json

from tools.merge_continuity_live_scores import merge_score_rows, summarize_rows


def _row(case_id: str, turn_id: str, score: float, passed: bool) -> dict:
    return {
        "case_id": case_id,
        "turn_id": turn_id,
        "turn_index": 1,
        "selected": {
            "text": f"{case_id}-{turn_id}",
            "score": score,
            "passed": passed,
            "hard_fail": False,
            "control_violations": [],
        },
    }


def _write_jsonl(path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def test_merge_score_rows_prefers_retry_pass_over_source_fail(tmp_path) -> None:
    source = tmp_path / "source.jsonl"
    retry = tmp_path / "retry.jsonl"
    _write_jsonl(source, [_row("case_a", "t01", 79.0, False), _row("case_b", "t01", 88.0, True)])
    _write_jsonl(retry, [_row("case_a", "t01", 86.0, True)])

    rows = merge_score_rows([source, retry])
    summary = summarize_rows(
        rows,
        expected_keys={("case_a", "t01"), ("case_b", "t01")},
        out_dir=tmp_path,
    )

    assert [row["selected"]["score"] for row in rows] == [86.0, 88.0]
    assert summary["pass_count"] == 2
    assert summary["missing_turn_count"] == 0
    assert summary["retry_used_count"] == 1


def test_summarize_rows_reports_missing_expected_turn() -> None:
    rows = [_row("case_a", "t01", 88.0, True)]
    summary = summarize_rows(
        rows,
        expected_keys={("case_a", "t01"), ("case_a", "t02")},
    )

    assert summary["missing_turn_count"] == 1
    assert summary["missing_turns"] == [{"case_id": "case_a", "turn_id": "t02"}]
