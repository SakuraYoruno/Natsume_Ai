from __future__ import annotations

from tools.chat_ab_oracle import build_oracle_report, build_oracle_rows, summarize_oracle


def test_build_oracle_rows_picks_best_candidate_per_case() -> None:
    rows = build_oracle_rows(
        cases=[{"case_id": "case_1", "user_message": "早"}],
        gold_entries={"case_1": {"required_any": [["早"]]}},
        candidate_rows=[
            {"case_id": "case_1", "variant": "current", "text": "嗯。", "latency_ms": 1},
            {"case_id": "case_1", "variant": "baseline_soul", "text": "早呀，先让我占一句。", "latency_ms": 9},
        ],
    )

    assert len(rows) == 1
    assert rows[0]["source_variant"] == "baseline_soul"
    assert rows[0]["text"] == "早呀，先让我占一句。"


def test_oracle_summary_and_report_include_outputs() -> None:
    rows = [
        {
            "case_id": "case_1",
            "user_message": "hello",
            "source_variant": "baseline_soul",
            "text": "今天突然用英文啊？",
            "score": 100,
            "passed": True,
            "hard_failures": [],
        }
    ]

    summary = summarize_oracle(rows)
    report = build_oracle_report(rows, summary)

    assert summary["pass_rate"] == 1.0
    assert "Chat Oracle Best Report" in report
    assert "今天突然用英文啊？" in report
