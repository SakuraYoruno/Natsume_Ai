from __future__ import annotations

from tools.continuity_quality_report import build_report, quality_watch_flags, render_markdown, strict_failed


def test_quality_watch_flags_detect_constraint_language_leak() -> None:
    flags = quality_watch_flags(
        "行行行，你想听什么嘛……啊，不能问问题对吧",
        {"reason_codes": [], "score": 86, "passed": True},
    )

    assert "constraint_language_leak" in flags


def test_quality_watch_flags_do_not_treat_rule_patching_topic_as_constraint_leak() -> None:
    flags = quality_watch_flags(
        "嗯，所以先按问题簇看，别逮一句就补规则。",
        {"reason_codes": [], "score": 89, "passed": True},
    )

    assert "constraint_language_leak" not in flags


def test_quality_watch_flags_do_not_treat_direct_error_paste_as_toolish_by_itself() -> None:
    flags = quality_watch_flags(
        "报错直接贴过来，我帮你看看",
        {"reason_codes": [], "score": 89, "passed": True},
    )

    assert "stiff_or_toolish_surface" not in flags


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


def test_strict_failed_checks_pass_and_watch_gates() -> None:
    clean_report = {
        "pass_rate": 1.0,
        "dialogue_pass_rate": 1.0,
        "watch_rows": [],
    }
    watch_report = {
        "pass_rate": 1.0,
        "dialogue_pass_rate": 1.0,
        "watch_rows": [{"turn_id": "t01"}],
    }
    failed_report = {
        "pass_rate": 0.98,
        "dialogue_pass_rate": 1.0,
        "watch_rows": [],
    }

    assert not strict_failed(clean_report)
    assert strict_failed(watch_report)
    assert strict_failed(failed_report)
    assert not strict_failed(watch_report, max_watch_rows=1)
