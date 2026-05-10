from __future__ import annotations

from tools.chat_ab_judge_codex import (
    build_judge_prompt,
    build_judge_output_schema,
    build_mock_judgements,
    compact_candidate_rows,
    group_candidates,
    judge_candidates,
    select_top_candidates_per_case,
    validate_judgement,
)


def test_build_judge_prompt_contains_case_and_candidates() -> None:
    cases = [{"case_id": "case_1", "user_message": "hello", "expected_behavior": "自然回应"}]
    candidates = group_candidates(
        [
            {"case_id": "case_1", "variant": "current", "text": "哥哥! 👋"},
            {"case_id": "case_1", "variant": "baseline_soul", "text": "尼尼，我来啦。"},
        ]
    )

    prompt = build_judge_prompt(
        cases=cases,
        candidates_by_case=candidates,
        variants=["current", "baseline_soul"],
        gold_by_case={"case_1": {"target_summary": "不要客服", "acceptable_examples": ["突然 hello 什么啦？"]}},
    )

    assert "Chat A/B Judge" in prompt
    assert "case_1" in prompt
    assert "哥哥! 👋" in prompt
    assert "尼尼，我来啦。" in prompt
    assert "不要把“叫哥哥”当作 Soul 契合" in prompt
    assert "sibling_interaction" in prompt
    assert "emotional_authenticity" in prompt
    assert "七情六欲" in prompt
    assert "emotional_flattening" in prompt
    assert "ALL_CANDIDATES_BAD" in prompt
    assert "sora_distinctiveness" in prompt
    assert "service_tone_avoidance" in prompt
    assert "兄妹式援助感" in prompt
    assert "Galgame 私聊化二次元台词感" in prompt
    assert "不要用真人自然度" in prompt
    assert "galgame_line_energy" in prompt
    assert "realism_flattening" in prompt
    assert "Judge-Only Gold Target" in prompt
    assert "不要客服" in prompt
    assert "突然 hello 什么啦" not in prompt
    assert "current/baseline_soul" in prompt


def test_build_judge_output_schema_uses_selected_variants_only() -> None:
    schema = build_judge_output_schema(["current", "baseline_soul"])
    judgement_schema = schema["properties"]["judgements"]["items"]["properties"]
    winner_schema = judgement_schema["winner"]
    scores_schema = judgement_schema["scores"]
    hard_failures_schema = judgement_schema["hard_failures"]

    assert winner_schema["enum"] == ["current", "baseline_soul"]
    assert scores_schema["required"] == ["current", "baseline_soul"]
    assert set(scores_schema["properties"]) == {"current", "baseline_soul"}
    assert set(hard_failures_schema["properties"]) == {"current", "baseline_soul"}
    assert "experimental_old_variant" not in scores_schema["properties"]


def test_compact_candidate_rows_keeps_reply_but_drops_heavy_trace() -> None:
    rows = compact_candidate_rows(
        [
            {
                "case_id": "case_1",
                "variant": "baseline_soul",
                "text": "尼尼，我来啦。",
                "latency_ms": 12,
                "ok": True,
                "envelope": {"kind": "final_reply", "source": "baseline_soul", "response_form": "text", "sendable": True},
                "trace": {"source": "simple_soul_baseline", "events": [{"large": "payload"}], "quality_retry_used": False},
            }
        ]
    )

    assert rows[0]["text"] == "尼尼，我来啦。"
    assert rows[0]["trace_summary"]["source"] == "simple_soul_baseline"
    assert "events" not in rows[0]["trace_summary"]


def test_mock_judge_prefers_baseline() -> None:
    cases = [{"case_id": "case_1", "user_message": "hello"}]
    candidates = group_candidates(
        [
            {"case_id": "case_1", "variant": "current", "text": "current"},
            {"case_id": "case_1", "variant": "baseline_soul", "text": "baseline"},
        ]
    )

    rows = build_mock_judgements(cases=cases, candidates_by_case=candidates)

    assert rows[0]["winner"] == "baseline_soul"


def test_select_top_candidates_per_case_uses_gold_score() -> None:
    rows = select_top_candidates_per_case(
        cases=[{"case_id": "case_1", "user_message": "早"}],
        candidate_rows=[
            {"case_id": "case_1", "variant": "bad", "text": "嗯。", "latency_ms": 1},
            {"case_id": "case_1", "variant": "good", "text": "早呀，先让我占一句。", "latency_ms": 9},
            {"case_id": "case_1", "variant": "also_good", "text": "早，来了。", "latency_ms": 3},
        ],
        gold_by_case={"case_1": {"required_any": [["早"]]}},
        max_candidates_per_case=1,
    )

    assert len(rows) == 1
    assert rows[0]["variant"] == "also_good"


def test_validate_judgement_clamps_scores_and_unknown_winner() -> None:
    row = validate_judgement(
        {
            "case_id": "case_1",
            "winner": "unknown",
            "confidence": 2,
            "scores": {"current": {"soul_fit": 99}, "baseline_soul": {"naturalness": 8, "sibling_interaction": -3}},
            "hard_failures": {"current": ["template"]},
            "reason": "bad current",
        },
        variants=["current", "baseline_soul"],
    )

    assert row["winner"] == ""
    assert row["confidence"] == 1.0
    assert row["scores"]["current"]["soul_fit"] == 10
    assert row["scores"]["baseline_soul"]["naturalness"] == 8
    assert row["scores"]["baseline_soul"]["sibling_interaction"] == 0
    assert row["scores"]["baseline_soul"]["emotional_authenticity"] == 0


def test_judge_candidates_batches_codex_calls(tmp_path, monkeypatch) -> None:
    cases_path = tmp_path / "cases.jsonl"
    candidates_path = tmp_path / "candidates.jsonl"
    schema_path = tmp_path / "schema.json"
    cases_path.write_text(
        "\n".join(
            [
                '{"case_id":"case_1","user_message":"早"}',
                '{"case_id":"case_2","user_message":"你好"}',
                '{"case_id":"case_3","user_message":"在吗"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    candidates_path.write_text(
        "\n".join(
            [
                '{"case_id":"case_1","variant":"baseline_soul","text":"早呀"}',
                '{"case_id":"case_2","variant":"baseline_soul","text":"你好呀"}',
                '{"case_id":"case_3","variant":"baseline_soul","text":"在呀"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path.write_text("{}", encoding="utf-8")
    calls: list[str] = []

    def _fake_run_codex_judge(*, prompt_text, output_path, **_kwargs):  # noqa: ANN001
        calls.append(prompt_text)
        case_ids = []
        for item in ("case_1", "case_2", "case_3"):
            if item in prompt_text:
                case_ids.append(item)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            '{"judgements":['
            + ",".join(
                '{"case_id":"%s","winner":"baseline_soul","scores":{"baseline_soul":{"soul_fit":8}},'
                '"hard_failures":{"baseline_soul":[]},"reason":"ok"}' % case_id
                for case_id in case_ids
            )
            + "]}",
            encoding="utf-8",
        )

    monkeypatch.setattr("tools.chat_ab_judge_codex.run_codex_judge", _fake_run_codex_judge)

    rows = judge_candidates(
        cases_path=cases_path,
        candidates_path=candidates_path,
        out_dir=tmp_path / "out",
        schema_path=schema_path,
        batch_size=2,
    )

    assert len(calls) == 2
    assert [row["case_id"] for row in rows] == ["case_1", "case_2", "case_3"]
