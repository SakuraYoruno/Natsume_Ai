from __future__ import annotations

from tools.continuity_quality_report import build_report, quality_watch_flags, render_markdown


def test_quality_watch_flags_detect_constraint_language_leak() -> None:
    flags = quality_watch_flags(
        "行行行，你想听什么嘛……啊，不能问问题对吧",
        {"reason_codes": [], "score": 86, "passed": True},
    )

    assert "constraint_language_leak" in flags


def test_build_report_keeps_passed_but_watchable_rows(tmp_path) -> None:
    rows = [
        {
            "case_id": "dialogue_one",
            "turn_id": "t01",
            "turn_index": 1,
            "selected": {
                "text": "行行行，你想听什么嘛……啊，不能问问题对吧",
                "score": 86.5,
                "passed": True,
                "hard_fail": False,
                "control_violations": [],
                "reason_codes": [],
            },
        },
        {
            "case_id": "dialogue_one",
            "turn_id": "t02",
            "turn_index": 2,
            "selected": {
                "text": "嗯，我在听，慢慢说。",
                "score": 89,
                "passed": True,
                "hard_fail": False,
                "control_violations": [],
                "reason_codes": [],
            },
        },
    ]

    report = build_report(rows)
    markdown = render_markdown(report, source=tmp_path / "live_scores.jsonl")

    assert report["pass_count"] == 2
    assert report["watch_flag_counts"]["constraint_language_leak"] == 1
    assert report["watch_rows"][0]["turn_id"] == "t01"
    assert "constraint_language_leak" in markdown
