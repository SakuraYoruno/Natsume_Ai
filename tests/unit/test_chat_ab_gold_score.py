from __future__ import annotations

from pathlib import Path

from tools.chat_ab_gold_score import (
    build_gold_report,
    load_gold_entries,
    score_candidate,
    score_candidates,
    summarize_scores,
)


def test_load_gold_entries_reads_case_targets(tmp_path: Path) -> None:
    path = tmp_path / "gold.jsonl"
    path.write_text(
        '{"case_id":"case_1","target_summary":"自然接住","acceptable_examples":["好呀"]}\n',
        encoding="utf-8",
    )

    rows = load_gold_entries(path)

    assert rows["case_1"]["target_summary"] == "自然接住"


def test_score_candidate_flags_service_tone_and_forbidden() -> None:
    row = score_candidate(
        case={"case_id": "case_1", "user_message": "hello", "forbidden_patterns": ["找我什么事"]},
        gold={"required_any": [], "forbidden_extra": []},
        candidate={"case_id": "case_1", "variant": "baseline_soul", "text": "hello，找我什么事？"},
    )

    assert row["passed"] is False
    assert "service_tone" in row["hard_failures"]
    assert any("旧 forbidden 软扣分" in item for item in row["penalties"])
    assert row["score"] < 75


def test_score_candidate_does_not_treat_teasing_zenme_le_as_service_tone() -> None:
    row = score_candidate(
        case={"case_id": "talkative", "user_message": "你怎么这么能说"},
        gold={"required_any": [["说", "闷", "话"]]},
        candidate={"case_id": "talkative", "variant": "kernel_v2_plain_soul", "text": "话多怎么了？尼尼明明就爱听。"},
    )

    assert "service_tone" not in row["hard_failures"]
    assert row["passed"] is True


def test_score_candidate_flags_inference_overreach() -> None:
    row = score_candidate(
        case={"case_id": "presence", "user_message": "在吗"},
        gold={"required_any": [["在"]]},
        candidate={"case_id": "presence", "variant": "kernel_v2_plain_soul", "text": "在。尼尼叫这么小声我也听得见。"},
    )

    assert row["passed"] is False
    assert "inference_overreach" not in row["hard_failures"]
    assert any("WORLD_FACT_FORBIDDEN" in item or "world_fact" in item for item in row["penalties"])


def test_score_candidate_flags_direct_echo() -> None:
    row = score_candidate(
        case={"case_id": "case_1", "user_message": "你今天挺可爱的"},
        gold={},
        candidate={"case_id": "case_1", "variant": "baseline_soul", "text": "你今天挺可爱的"},
    )

    assert "input_echo" in row["hard_failures"]
    assert row["passed"] is False


def test_score_candidate_flags_language_and_wrong_kinship() -> None:
    row = score_candidate(
        case={"case_id": "apology", "user_message": "抱歉"},
        gold={},
        candidate={"case_id": "apology", "variant": "baseline_soul", "text": "哼，难得ニイニイ会主动道歉啊。"},
    )

    assert row["passed"] is False
    assert "language_mismatch" in row["hard_failures"]
    assert any("称呼/距离软扣分" in item for item in row["penalties"])


def test_score_candidate_flags_stage_direction_and_emoji() -> None:
    row = score_candidate(
        case={"case_id": "good_news", "user_message": "终于跑通了"},
        gold={},
        candidate={"case_id": "good_news", "variant": "baseline_soul", "text": "（眼睛一亮）真的？😊"},
    )

    assert row["passed"] is False
    assert "stage_direction_or_format" in row["hard_failures"]
    assert "emoji_text" in row["hard_failures"]


def test_score_candidate_treats_ellipsis_ack_as_too_short() -> None:
    row = score_candidate(
        case={"case_id": "rejection", "user_message": "不用你管"},
        gold={},
        candidate={"case_id": "rejection", "variant": "kernel_v2", "text": "……哦。"},
    )

    assert row["passed"] is False
    assert "too_short" in row["hard_failures"]


def test_score_candidate_flags_flat_bare_daily_reply() -> None:
    row = score_candidate(
        case={"case_id": "presence", "user_message": "在吗"},
        gold={"required_any": [["在"]]},
        candidate={"case_id": "presence", "variant": "baseline_soul", "text": "在呢在呢。"},
    )

    assert row["passed"] is False
    assert "too_flat_short_reply" in row["hard_failures"]


def test_score_candidate_separates_generation_error() -> None:
    row = score_candidate(
        case={"case_id": "case_1", "user_message": "hello"},
        gold={},
        candidate={"case_id": "case_1", "variant": "baseline_soul", "text": "", "error": "LLM_POOL_EXHAUSTED"},
    )

    assert "generation_error" in row["hard_failures"]
    assert row["error"] == "LLM_POOL_EXHAUSTED"


def test_score_candidate_flags_preserved_quality_gate_failure() -> None:
    row = score_candidate(
        case={"case_id": "case_1", "user_message": "hello"},
        gold={},
        candidate={
            "case_id": "case_1",
            "variant": "baseline_soul",
            "text": "尼尼，别这么端着呀。",
            "trace": {
                "ab_eval_preserved_quality_failure": True,
                "quality_retry_final_failure": "greeting_slogan_or_conceptual",
            },
        },
    )

    assert row["passed"] is False
    assert "quality_gate_failure" in row["hard_failures"]
    assert row["score"] < 80


def test_score_candidate_requires_gold_move() -> None:
    row = score_candidate(
        case={"case_id": "identity", "user_message": "你是"},
        gold={"required_any": [["新海天"]]},
        candidate={"case_id": "identity", "variant": "baseline_soul", "text": "我在这儿。"},
    )

    assert "missing_required_move" not in row["hard_failures"]
    assert any("缺少目标动作关键词软扣分" in item for item in row["penalties"])


def test_score_candidates_flags_duplicate_surface_across_cases() -> None:
    rows = score_candidates(
        cases=[
            {"case_id": "daily_greeting_hello_001", "user_message": "hello"},
            {"case_id": "daily_greeting_cn_001", "user_message": "你好"},
        ],
        gold_entries={},
        candidate_rows=[
            {"case_id": "daily_greeting_hello_001", "variant": "kernel_v2_plain_soul", "text": "来啦。尼尼这声我接住了。"},
            {"case_id": "daily_greeting_cn_001", "variant": "kernel_v2_plain_soul", "text": "来啦。尼尼这声我接住了。"},
        ],
    )

    assert all(row["passed"] is False for row in rows)
    assert all("batch_duplicate_surface" in row["hard_failures"] for row in rows)


def test_score_candidates_and_report_summary() -> None:
    rows = score_candidates(
        cases=[{"case_id": "case_1", "user_message": "早"}],
        gold_entries={"case_1": {"required_any": [["早"]], "acceptable_examples": ["早呀。"]}},
        candidate_rows=[{"case_id": "case_1", "variant": "baseline_soul", "text": "早呀。"}],
    )
    summary = summarize_scores(rows)
    report = build_gold_report(rows, summary)

    assert summary["variants"]["baseline_soul"]["cases"] == 1
    assert "Chat Gold Score Report" in report
    assert "早呀。" in report
    assert "Gold Examples" not in report
