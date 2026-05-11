from __future__ import annotations

import json

from tools.retry_isolated_surface_failures import (
    merge_source_and_retry_scores,
    rescore_source_outputs,
    retry_case_indexes,
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
            "raw_output": json.dumps({"candidates": ["在呢，这一句我看见了。"]}, ensure_ascii=False),
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
    ]
    retry_rows = [
        {
            "case_id": "bad",
            "selected": {"text": "先别急，我陪你看哪里坏了。", "score": 89, "passed": True, "hard_fail": False, "control_violations": []},
        },
        {
            "case_id": "good",
            "selected": {"text": "在。", "score": 70, "passed": False, "hard_fail": False, "control_violations": []},
        },
    ]

    merged = merge_source_and_retry_scores(source_rows=source_rows, retry_rows=retry_rows)

    assert merged[0]["selected_from"] == "retry"
    assert merged[0]["improved"]
    assert merged[0]["selected"]["text"] == "先别急，我陪你看哪里坏了。"
    assert merged[1]["selected_from"] == "source"
    assert not merged[1]["improved"]
    assert merged[1]["selected"]["text"] == "在呢，这一句我看见了。"


def test_summarize_merged_reports_retry_savings(tmp_path) -> None:
    merged = [
        {"selected_from": "retry", "improved": True, "selected": {"score": 89, "passed": True, "hard_fail": False}},
        {"selected_from": "source", "improved": False, "selected": {"score": 88, "passed": True, "hard_fail": False}},
    ]
    summary = summarize_merged(
        cases_count=2,
        source_rows=[{}, {}],
        retry_indexes=[1],
        retry_results=[{"ok": True, "latency_ms": 1234}],
        merged_rows=merged,
        started=0.0,
        threshold=80.0,
        out_dir=tmp_path,
    )

    assert summary["retry_case_count"] == 1
    assert summary["retry_generated_count"] == 1
    assert summary["retry_selected_count"] == 1
    assert summary["retry_improved_count"] == 1
    assert summary["pass_rate"] == 1.0
