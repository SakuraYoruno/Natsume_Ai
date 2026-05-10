from __future__ import annotations

import asyncio
import json

from tools.chat_ab_generate import BASELINE_VARIANTS, KERNEL_V2_VARIANTS
from tools.chat_ab_workflow import (
    aggregate_decision,
    apply_auto_promote_stop_rule,
    build_language_track_summary,
    build_report,
    expand_auto_promote_variants,
    run_workflow,
)


def _scores(value: int) -> dict[str, int]:
    return {
        "soul_fit": value,
        "galgame_line_energy": value,
        "sibling_interaction": value,
        "emotional_authenticity": value,
        "sora_distinctiveness": value,
        "service_tone_avoidance": value,
        "naturalness": value,
        "context_coherence": value,
        "input_understanding": value,
        "non_regression": value,
    }


def test_aggregate_decision_promotes_baseline_when_rules_pass() -> None:
    candidates = [
        {"case_id": "a", "variant": "current", "latency_ms": 1000},
        {"case_id": "a", "variant": "baseline_soul", "latency_ms": 900},
        {"case_id": "b", "variant": "current", "latency_ms": 1000},
        {"case_id": "b", "variant": "baseline_soul", "latency_ms": 900},
    ]
    judgements = [
        {
            "case_id": "a",
            "winner": "baseline_soul",
            "scores": {
                "current": _scores(5),
                "baseline_soul": _scores(8),
            },
            "hard_failures": {"current": [], "baseline_soul": []},
        },
        {
            "case_id": "b",
            "winner": "baseline_soul",
            "scores": {
                "current": _scores(5),
                "baseline_soul": _scores(8),
            },
            "hard_failures": {"current": [], "baseline_soul": []},
        },
    ]

    decision = aggregate_decision(candidate_rows=candidates, judgement_rows=judgements, variants=["current", "baseline_soul"])

    assert decision["selected_variant"] == "baseline_soul"
    assert decision["candidates"]["baseline_soul"]["promoted"] is True


def test_expand_auto_promote_variants_includes_all_baselines() -> None:
    assert expand_auto_promote_variants(["current", "baseline_soul"], auto_promote=True) == ["current", *BASELINE_VARIANTS]


def test_expand_auto_promote_variants_defaults_to_kernel_v2_chain() -> None:
    assert expand_auto_promote_variants(["current"], auto_promote=True) == ["current", *KERNEL_V2_VARIANTS]
    assert expand_auto_promote_variants(["current", "kernel_v2"], auto_promote=True) == ["current", *KERNEL_V2_VARIANTS]


def test_auto_promote_stop_rule_requires_contiguous_success() -> None:
    decision = {
        "selected_variant": "baseline_soul",
        "candidates": {
            "baseline_soul": {"promoted": True},
        },
    }

    updated = apply_auto_promote_stop_rule(decision)

    assert updated["selected_variant"] == "baseline_soul"
    assert updated["auto_promote"]["stopped_at"] == ""


def test_workflow_mock_writes_artifacts(tmp_path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps({"case_id": "case_1", "user_message": "hello", "history_turns": []}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    decision = asyncio.run(
        run_workflow(
            cases_path=cases_path,
            variants=["current", "baseline_soul"],
            judge="mock",
            out_dir=out_dir,
            mock_generate=True,
        )
    )

    assert decision["selected_variant"] == "baseline_soul"
    assert (out_dir / "raw_candidates.jsonl").exists()
    assert (out_dir / "raw_judgements.jsonl").exists()
    assert (out_dir / "decision.json").exists()
    assert (out_dir / "report.md").exists()
    report_text = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "## Dimension Averages" in report_text
    assert "sibling_interaction" in report_text
    assert "## Case Outputs" in report_text
    assert "case_1" in report_text
    assert "current mock reply: hello" in report_text
    assert "baseline_soul mock reply: hello" in report_text


def test_workflow_mock_can_write_gold_guardrail(tmp_path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    gold_path = tmp_path / "gold.jsonl"
    cases_path.write_text(
        json.dumps({"case_id": "case_1", "user_message": "早", "history_turns": []}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    gold_path.write_text(
        json.dumps(
            {
                "case_id": "case_1",
                "target_summary": "自然早安",
                "required_any": [["早"]],
                "acceptable_examples": ["早呀。"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    decision = asyncio.run(
        run_workflow(
            cases_path=cases_path,
            variants=["current", "baseline_soul"],
            judge="mock",
            out_dir=out_dir,
            mock_generate=True,
            gold_path=gold_path,
        )
    )

    assert "gold_summary" in decision
    assert (out_dir / "gold_report.md").exists()
    assert "Gold Score Guardrail" in (out_dir / "report.md").read_text(encoding="utf-8")


def test_build_report_includes_query_and_outputs() -> None:
    decision = {
        "case_count": 1,
        "variants": ["current", "baseline_soul"],
        "selected_variant": "baseline_soul",
        "current": {"avg_score": 10, "p95_latency_ms": 100},
        "candidates": {
            "baseline_soul": {
                "promoted": True,
                "win_rate": 1,
                "hard_fail_rate": 0,
                "avg_score": 40,
                "avg_score_delta": 30,
                "p95_latency_ms": 80,
            }
        },
    }
    cases = [{"case_id": "daily_hello", "user_message": "早", "history_turns": []}]
    candidates = [
        {"case_id": "daily_hello", "variant": "current", "text": "早。", "latency_ms": 100},
        {"case_id": "daily_hello", "variant": "baseline_soul", "text": "尼尼，我来冒个头啦。", "latency_ms": 80},
    ]
    judgements = [
        {
            "case_id": "daily_hello",
            "winner": "baseline_soul",
            "confidence": 0.9,
            "scores": {"current": _scores(4), "baseline_soul": _scores(8)},
            "reason": "baseline better",
        }
    ]

    report = build_report(decision, judgements, candidate_rows=candidates, cases=cases)

    assert "## Dimension Averages" in report
    assert "| sibling_interaction | 4.00 | 8.00 |" in report
    assert "## Case Outputs" in report
    assert "| `daily_hello` | 早 | 早。 | 尼尼，我来冒个头啦。 |" in report
    assert "query=`早`" in report


def test_build_language_track_summary_splits_zh_and_ja() -> None:
    cases = [
        {"case_id": "zh_presence", "language_track": "zh_text", "response_form": "text", "user_message": "在吗"},
        {"case_id": "ja_presence", "language_track": "ja_voice", "response_form": "voice", "user_message": "在吗"},
    ]
    candidates = [
        {"case_id": "zh_presence", "variant": "current", "latency_ms": 100},
        {"case_id": "zh_presence", "variant": "kernel_v2", "latency_ms": 50},
        {"case_id": "ja_presence", "variant": "current", "latency_ms": 200},
        {"case_id": "ja_presence", "variant": "kernel_v2", "latency_ms": 80},
    ]
    judgements = [
        {
            "case_id": "zh_presence",
            "winner": "kernel_v2",
            "scores": {"current": _scores(4), "kernel_v2": _scores(8)},
            "hard_failures": {"current": ["service_tone"], "kernel_v2": []},
        },
        {
            "case_id": "ja_presence",
            "winner": "current",
            "scores": {"current": _scores(8), "kernel_v2": _scores(5)},
            "hard_failures": {"current": [], "kernel_v2": ["wrong_language_track"]},
        },
    ]

    summary = build_language_track_summary(
        cases=cases,
        candidate_rows=candidates,
        judgement_rows=judgements,
        variants=["current", "kernel_v2"],
    )

    assert summary["zh_text"]["case_count"] == 1
    assert summary["zh_text"]["variants"]["kernel_v2"]["wins"] == 1
    assert summary["ja_voice"]["variants"]["current"]["wins"] == 1
    assert summary["ja_voice"]["variants"]["kernel_v2"]["hard_fail_rate"] == 1.0


def test_build_report_includes_language_track_summary() -> None:
    decision = {
        "case_count": 1,
        "variants": ["current", "kernel_v2"],
        "selected_variant": "kernel_v2",
        "current": {"avg_score": 10, "p95_latency_ms": 100},
        "candidates": {
            "kernel_v2": {
                "promoted": True,
                "win_rate": 1,
                "hard_fail_rate": 0,
                "avg_score": 40,
                "avg_score_delta": 30,
                "p95_latency_ms": 80,
            }
        },
        "track_summary": {
            "ja_voice": {
                "case_count": 1,
                "variants": {
                    "current": {"wins": 0, "win_rate": 0, "hard_fail_rate": 1, "avg_score": 10, "p95_latency_ms": 100},
                    "kernel_v2": {"wins": 1, "win_rate": 1, "hard_fail_rate": 0, "avg_score": 40, "p95_latency_ms": 80},
                },
            }
        },
    }

    report = build_report(decision, [], candidate_rows=[], cases=[])

    assert "## Language Track Summary" in report
    assert "| ja_voice | 1 | kernel_v2 | 1 | 100.0% | 0.0% | 40.00 | 80ms |" in report
