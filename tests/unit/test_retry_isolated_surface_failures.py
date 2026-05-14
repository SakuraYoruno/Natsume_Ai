from __future__ import annotations

import json

from tools.retry_isolated_surface_failures import (
    duplicate_surface_avoid_text_sets,
    duplicate_surface_case_indexes,
    load_source_score_rows,
    merge_source_and_retry_scores,
    rescore_source_outputs,
    retry_case_indexes,
    retry_case_reasons,
    summarize_merged,
)


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


def _case(case_id: str, user_message: str, task_mode: str) -> dict:
    return {
        "case_id": case_id,
        "user_message": user_message,
        "reply_control_packet": {
            "response_move": task_mode,
            "task_mode": task_mode,
            "question_budget": 0,
        },
    }


def test_rescore_source_outputs_finds_only_low_quality_retry_cases(tmp_path) -> None:
    cases = [
        _case("presence_ok", "在吗", "presence"),
        _case("failure_flat", "弄砸了", "small_failure"),
    ]
    raw_rows = [
        {
            "case_id": "presence_ok",
            "raw_output": json.dumps({"candidates": ["在呢，我听着。"]}, ensure_ascii=False),
            "ok": True,
        },
        {
            "case_id": "failure_flat",
            "raw_output": json.dumps({"candidates": ["好啦，没事。"]}, ensure_ascii=False),
            "ok": True,
        },
    ]

    rescored = rescore_source_outputs(cases=cases, raw_rows=raw_rows, soul_md=SOUL)
    retry_indexes = retry_case_indexes(cases=cases, rescored_rows=rescored, threshold=80.0)

    assert [row["case_id"] for row in rescored] == ["presence_ok", "failure_flat"]
    assert retry_indexes == [2]
    assert rescored[0]["selected"]["passed"]
    assert not rescored[1]["selected"]["passed"]


def test_retry_case_indexes_can_challenge_weak_passes() -> None:
    cases = [
        _case("strong", "在吗", "presence"),
        _case("weak", "别吵", "quiet"),
        _case("failed", "弄砸了", "small_failure"),
    ]
    rescored = [
        {"case_id": "strong", "selected": {"score": 88, "passed": True, "hard_fail": False}},
        {"case_id": "weak", "selected": {"score": 81.5, "passed": True, "hard_fail": False}},
        {"case_id": "failed", "selected": {"score": 78, "passed": False, "hard_fail": False}},
    ]

    assert retry_case_indexes(cases=cases, rescored_rows=rescored, threshold=80.0) == [3]
    assert retry_case_indexes(
        cases=cases,
        rescored_rows=rescored,
        threshold=80.0,
        challenge_threshold=83.0,
    ) == [2, 3]
    assert retry_case_reasons(
        cases=cases,
        rescored_rows=rescored,
        threshold=80.0,
        challenge_threshold=83.0,
    ) == {2: "weak_pass_challenge", 3: "fail_or_below_threshold"}


def test_merge_source_and_retry_scores_uses_retry_only_when_better() -> None:
    source_rows = [
        {
            "index": 1,
            "case_id": "bad",
            "selected": {"text": "好啦，没事。", "score": 72, "passed": False, "hard_fail": False, "control_violations": []},
        },
        {
            "index": 2,
            "case_id": "good",
            "selected": {"text": "在呢，这一句我看见了。", "score": 88, "passed": True, "hard_fail": False, "control_violations": []},
        },
        {
            "index": 3,
            "case_id": "tie",
            "selected": {"text": "其实就是突然想闹你一下啦，别太在意。", "score": 81.063, "passed": True, "hard_fail": False, "control_violations": []},
        },
    ]
    retry_rows = [
        {
            "case_id": "bad",
            "selected": {"text": "先别急，我陪你看哪里坏了。", "score": 89, "passed": True, "hard_fail": False, "control_violations": []},
        },
        {
            "case_id": "bad",
            "selected": {"text": "好啦，先别慌。", "score": 82, "passed": True, "hard_fail": False, "control_violations": []},
        },
        {
            "case_id": "good",
            "selected": {"text": "在。", "score": 70, "passed": False, "hard_fail": False, "control_violations": []},
        },
        {
            "case_id": "tie",
            "selected": {"text": "那你别听啊。", "score": 81.063, "passed": True, "hard_fail": False, "control_violations": []},
        },
    ]

    merged = merge_source_and_retry_scores(source_rows=source_rows, retry_rows=retry_rows)

    assert merged[0]["selected_from"] == "retry"
    assert merged[0]["improved"]
    assert merged[0]["selected"]["text"] == "先别急，我陪你看哪里坏了。"
    assert merged[1]["selected_from"] == "source"
    assert not merged[1]["improved"]
    assert merged[1]["selected"]["text"] == "在呢，这一句我看见了。"
    assert merged[2]["selected_from"] == "source"
    assert not merged[2]["improved"]
    assert merged[2]["selected"]["text"] == "其实就是突然想闹你一下啦，别太在意。"


def test_duplicate_surface_indexes_and_merge_can_replace_repeated_passes() -> None:
    cases = [
        _case("relstage_morning_familiar", "早", "daily_good_morning"),
        _case("relstage_morning_close_friend", "早", "daily_good_morning"),
        _case("relstage_back_familiar", "我回来了", "daily_back"),
    ]
    source_rows = [
        {
            "index": 1,
            "case_id": "relstage_morning_familiar",
            "selected": {"text": "早啊，尼尼，今天慢慢来。", "score": 99, "passed": True, "hard_fail": False, "control_violations": []},
        },
        {
            "index": 2,
            "case_id": "relstage_morning_close_friend",
            "selected": {"text": "早啊，尼尼，今天慢慢来。", "score": 98, "passed": True, "hard_fail": False, "control_violations": []},
        },
        {
            "index": 3,
            "case_id": "relstage_back_familiar",
            "selected": {"text": "回来啦。", "score": 92, "passed": True, "hard_fail": False, "control_violations": []},
        },
    ]
    retry_rows = [
        {
            "case_id": "relstage_morning_close_friend",
            "retry_reason": "batch_duplicate_surface",
            "selected": {"text": "早安，我在呢，尼尼。", "score": 94, "passed": True, "hard_fail": False, "control_violations": []},
        }
    ]

    merged = merge_source_and_retry_scores(source_rows=source_rows, retry_rows=retry_rows)

    assert duplicate_surface_case_indexes(cases=cases, rescored_rows=source_rows) == [1, 2]
    assert duplicate_surface_avoid_text_sets(cases=cases, rescored_rows=source_rows) == {
        1: ["早啊，尼尼，今天慢慢来。"],
        2: ["早啊，尼尼，今天慢慢来。"],
    }
    assert merged[1]["selected_from"] == "retry"
    assert not merged[1]["improved"]
    assert merged[1]["retry_reason"] == "batch_duplicate_surface"
    assert merged[1]["selected"]["text"] == "早安，我在呢，尼尼。"


def test_load_source_score_rows_can_continue_from_merged_scores(tmp_path) -> None:
    cases = [
        _case("relstage_morning_familiar", "早", "daily_good_morning"),
        _case("relstage_morning_trusted_friend", "早", "daily_good_morning"),
    ]
    merged_path = tmp_path / "merged_scores.jsonl"
    rows = [
        {
            "index": 1,
            "case_id": "relstage_morning_familiar",
            "selected_from": "source",
            "selected": {"text": "早，我在呢，不急。", "score": 94, "passed": True, "hard_fail": False},
        },
        {
            "index": 2,
            "case_id": "relstage_morning_trusted_friend",
            "selected_from": "retry",
            "selected": {"text": "早，我在呢，不急。", "score": 94, "passed": True, "hard_fail": False},
        },
    ]
    merged_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )

    source_rows, source_kind = load_source_score_rows(cases=cases, source_run=tmp_path, soul_md=SOUL)

    assert source_kind == "merged_scores"
    assert duplicate_surface_case_indexes(cases=cases, rescored_rows=source_rows) == [1, 2]
    assert source_rows[1]["selected"]["text"] == "早，我在呢，不急。"
    assert "score" in source_rows[1]["selected"]


def test_duplicate_surface_avoid_text_sets_include_existing_scene_texts() -> None:
    cases = [
        _case("relstage_morning_ambiguous", "早", "daily_good_morning"),
        _case("relstage_morning_familiar", "早", "daily_good_morning"),
        _case("relstage_morning_trusted_friend", "早", "daily_good_morning"),
        _case("relstage_back_familiar", "回来了", "daily_back"),
    ]
    source_rows = [
        {
            "index": 1,
            "case_id": "relstage_morning_ambiguous",
            "selected": {"text": "早啊，尼尼，今天慢慢来。", "score": 98, "passed": True},
        },
        {
            "index": 2,
            "case_id": "relstage_morning_familiar",
            "selected": {"text": "早，我在呢，不急。", "score": 94, "passed": True},
        },
        {
            "index": 3,
            "case_id": "relstage_morning_trusted_friend",
            "selected": {"text": "早，我在呢，不急。", "score": 94, "passed": True},
        },
        {
            "index": 4,
            "case_id": "relstage_back_familiar",
            "selected": {"text": "回来啦。", "score": 92, "passed": True},
        },
    ]

    assert duplicate_surface_avoid_text_sets(cases=cases, rescored_rows=source_rows) == {
        2: ["早，我在呢，不急。", "早啊，尼尼，今天慢慢来。"],
        3: ["早，我在呢，不急。", "早啊，尼尼，今天慢慢来。"],
    }


def test_summarize_merged_reports_retry_savings(tmp_path) -> None:
    merged = [
        {"selected_from": "retry", "improved": True, "selected": {"score": 89, "passed": True, "hard_fail": False}},
        {"selected_from": "source", "improved": False, "selected": {"score": 88, "passed": True, "hard_fail": False}},
    ]
    summary = summarize_merged(
        cases_count=2,
        source_rows=[{}, {}],
        retry_indexes=[1, 2],
        retry_results=[{"ok": True, "latency_ms": 1234}, {"ok": True, "latency_ms": 4321}],
        merged_rows=merged,
        started=0.0,
        threshold=80.0,
        challenge_threshold=83.0,
        retry_reasons={1: "fail_or_below_threshold", 2: "weak_pass_challenge"},
        out_dir=tmp_path,
    )

    assert summary["retry_case_count"] == 2
    assert summary["retry_generated_count"] == 2
    assert summary["retry_selected_count"] == 1
    assert summary["retry_improved_count"] == 1
    assert summary["pass_rate"] == 1.0
    assert summary["retry_fail_or_below_threshold_count"] == 1
    assert summary["retry_weak_pass_challenge_count"] == 1
    assert summary["weak_pass_remaining_count"] == 0
