from __future__ import annotations

import argparse
import asyncio
import json

from tools.surface_memory_ab import _compare_variants, default_cases, run_surface_memory_ab


def test_surface_memory_ab_default_cases_use_quality_pack() -> None:
    quality_cases = default_cases("quality_pack")
    smoke_cases = default_cases("smoke")

    assert len(quality_cases) > len(smoke_cases) == 8
    assert all(case.scene_id for case in quality_cases)
    assert {case.case_id for case in quality_cases} >= {
        "short_ack_plain",
        "relationship_doubt_direct",
        "negative_feedback_grammar",
    }


def test_surface_memory_ab_does_not_mark_same_visible_text_as_worse() -> None:
    comparison = _compare_variants(
        {
            "affect": {"score": 98, "selected_text": "哼，回来就好。"},
            "memory_affect": {
                "score": 92,
                "selected_text": "哼，回来就好。",
                "source_ref_leak": False,
                "hard_fail": False,
            },
        }
    )

    assert comparison["same_selected_text"] is True
    assert comparison["manual_review_required"] is False
    assert comparison["memory_worse"] is False


def test_surface_memory_ab_treats_score_drop_as_manual_review_not_worse() -> None:
    comparison = _compare_variants(
        {
            "affect": {"score": 98, "selected_text": "嗯，我听着呢，别停。"},
            "memory_affect": {
                "score": 88,
                "selected_text": "哼，别停，往下。",
                "source_ref_leak": False,
                "hard_fail": False,
            },
        }
    )

    assert comparison["diagnostic_score_delta_memory_minus_affect"] == -10.0
    assert comparison["manual_review_required"] is True
    assert comparison["memory_worse"] is False


def test_surface_memory_ab_marks_only_hard_issues_as_worse() -> None:
    comparison = _compare_variants(
        {
            "affect": {"score": 80, "selected_text": "哼，有一点，别笑。"},
            "memory_affect": {
                "score": 96,
                "selected_text": "memory://x 有一点啦。",
                "source_ref_leak": True,
                "hard_fail": False,
            },
        }
    )

    assert comparison["memory_worse"] is True
    assert comparison["manual_review_required"] is False


def test_surface_memory_ab_fixture_outputs_detailed_text(tmp_path) -> None:
    result = asyncio.run(
        run_surface_memory_ab(
            argparse.Namespace(
                mode="fixture",
                provider="deepseek",
                model="",
                base_url="",
                out_dir=str(tmp_path),
                case_set="quality_pack",
                limit=3,
                timeout_ms=1000,
                temperature=0.78,
                top_p=0.9,
                max_tokens=220,
            )
        )
    )

    summary = result["summary"]
    rows = result["results"]

    assert summary["mode"] == "fixture"
    assert summary["case_set"] == "quality_pack"
    assert summary["case_count"] == 3
    assert summary["variant_count"] == 6
    assert 0 <= summary["gate_pass_count"] <= 6
    assert summary["manual_review_required_count"] >= 0
    assert "diagnostic_avg_affect_score" in summary
    assert summary["memory_worse_count"] == 0
    assert summary["source_ref_leak_count"] == 0
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.jsonl").exists()
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "selected_outputs.md").exists()
    review_template_path = tmp_path / "surface_scene_manual_review_template.json"
    assert review_template_path.exists()
    review_template = json.loads(review_template_path.read_text(encoding="utf-8"))
    assert review_template["sample_kind"] == "surface_scene_quality_review_template"
    assert review_template["scenes"][0]["candidate_good_examples"]
    assert review_template["scenes"][0]["candidate_good_examples"][0]["manual_verdict"] == ""
    assert rows[0]["variants"]["affect"]["selected_text"]
    assert rows[0]["variants"]["memory_affect"]["selected_text"]
    assert rows[0]["case"]["scene_id"]
    selected_texts = [
        variant["selected_text"]
        for row in rows
        for variant in row["variants"].values()
    ]
    assert all("memory://" not in text for text in selected_texts)
