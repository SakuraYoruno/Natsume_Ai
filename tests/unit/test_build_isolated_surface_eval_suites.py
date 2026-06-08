from __future__ import annotations

from host_brain.persona.isolated_surface_harness import build_isolated_surface_input
from tools.build_isolated_surface_eval_suites import (
    CHALLENGE_GROUP_COUNTS,
    build_challenge_cases,
    build_heldout_cases,
    build_summary,
)


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


def test_build_heldout_cases_has_expected_count_and_unique_ids() -> None:
    cases = build_heldout_cases(200)

    assert len(cases) == 200
    assert len({case["case_id"] for case in cases}) == 200
    assert cases[0]["case_id"].startswith("heldout_daily_good_morning_")
    assert cases[-1]["case_id"].startswith("heldout_miss_you_")


def test_build_challenge_cases_has_expected_group_counts() -> None:
    cases = build_challenge_cases()

    assert len(cases) == 300
    assert len({case["case_id"] for case in cases}) == 300
    for group_id, count in CHALLENGE_GROUP_COUNTS.items():
        assert sum(1 for case in cases if case["case_id"].startswith(f"challenge_{group_id}_")) == count


def test_eval_suite_cases_keep_isolated_surface_valid() -> None:
    cases = [*build_heldout_cases(25), *build_challenge_cases({"identity": 3, "short_ack": 3, "advice_continue": 3})]

    for case in cases:
        request = build_isolated_surface_input({**case, "soul_md": SOUL})
        assert request.user_message
        assert request.control.style_anchor
        assert "熟人私聊" in request.control.style_anchor


def test_eval_summary_tracks_total_with_regression(tmp_path) -> None:
    regression = tmp_path / "regression.jsonl"
    regression.write_text('{"case_id":"a"}\n{"case_id":"b"}\n', encoding="utf-8")
    heldout = build_heldout_cases(5)
    challenge = build_challenge_cases({"identity": 2})

    summary = build_summary(
        out_dir=tmp_path,
        regression_cases_path=regression,
        heldout_cases=heldout,
        challenge_cases=challenge,
    )

    assert summary["regression_cases"] == 2
    assert summary["heldout_cases"] == 5
    assert summary["challenge_cases"] == 2
    assert summary["total_eval_cases_if_combined_with_regression"] == 9
    assert summary["challenge_group_counts"] == {"identity": 2}
