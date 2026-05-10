from __future__ import annotations

import asyncio
import json

from tools.chat_ab_repeat_quality import build_repeat_report, run_repeat_quality, summarize_repeat_runs


def test_summarize_repeat_runs_tracks_minima_and_failures() -> None:
    summary = summarize_repeat_runs(
        run_summaries=[
            {"variants": {"kernel": {"avg_score": 100, "pass_rate": 1.0, "hard_fail_rate": 0, "error_rate": 0, "min_score": 100}}},
            {"variants": {"kernel": {"avg_score": 90, "pass_rate": 0.5, "hard_fail_rate": 0.5, "error_rate": 0, "min_score": 72}}},
        ],
        score_rows_by_run=[
            [{"case_id": "a", "variant": "kernel", "score": 100, "passed": True}],
            [{"case_id": "a", "variant": "kernel", "score": 72, "passed": False, "text": "bad", "hard_failures": ["taste_regression"]}],
        ],
    )

    assert summary["variants"]["kernel"]["avg_score_mean"] == 95
    assert summary["variants"]["kernel"]["pass_rate_min"] == 0.5
    assert summary["top_failed_cases"][0]["case_id"] == "a"
    assert "Failed Cases" in build_repeat_report(summary)


def test_repeat_quality_mock_writes_run_artifacts(tmp_path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    gold_path = tmp_path / "gold.jsonl"
    cases_path.write_text(
        json.dumps({"case_id": "case_1", "user_message": "早", "history_turns": []}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    gold_path.write_text(
        json.dumps({"case_id": "case_1", "required_any": [["早"]]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "repeat"
    summary = asyncio.run(
        run_repeat_quality(
            cases_path=cases_path,
            gold_path=gold_path,
            variants=["kernel_v2_plain_soul"],
            out_dir=out_dir,
            runs=2,
            mock=True,
        )
    )

    assert summary["runs"] == 2
    assert (out_dir / "run_01" / "raw_candidates.jsonl").exists()
    assert (out_dir / "run_02" / "gold_report.md").exists()
    assert (out_dir / "repeat_summary.json").exists()
    assert (out_dir / "repeat_report.md").exists()
