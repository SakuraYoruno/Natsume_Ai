from __future__ import annotations

import json
from pathlib import Path

from tools.relationship_axis_contrast_report import (
    build_relationship_axis_contrast_report,
    render_markdown_report,
    write_relationship_axis_contrast_report,
)


def _case(scene: str, stage: str, task_mode: str = "daily_back") -> dict:
    return {
        "case_id": f"relaxis_{scene}_{stage}",
        "user_message": "我回来了",
        "reply_control_packet": {
            "relationship_distance": stage,
            "task_mode": task_mode,
            "response_move": "welcome_back",
            "question_budget": 0,
        },
    }


def _score(scene: str, stage: str, text: str, score: float = 95.0) -> dict:
    return {
        "case_id": f"relaxis_{scene}_{stage}",
        "selected": {
            "text": text,
            "score": score,
            "passed": True,
            "hard_fail": False,
            "reason_codes": [],
            "control_violations": [],
        },
    }


def test_relationship_axis_contrast_report_flags_duplicate_adjacent_text() -> None:
    cases = [
        _case("back", "stranger"),
        _case("back", "new_acquaintance"),
        _case("back", "familiar"),
    ]
    scores = [
        _score("back", "stranger", "回来啦，先歇一下。"),
        _score("back", "new_acquaintance", "回来啦，先歇一下。"),
        _score("back", "familiar", "回来啦，尼尼，先歇一下。"),
    ]

    report = build_relationship_axis_contrast_report(cases=cases, score_rows=scores)

    assert report["weak_pair_count"] == 1
    assert report["critical_pair_count"] == 1
    assert report["weak_pairs"][0]["reasons"] == ["duplicate_text"]


def test_relationship_axis_contrast_report_flags_missing_lover_intimacy_shift() -> None:
    cases = [
        _case("tired", "pre_confession", task_mode="tired"),
        _case("tired", "lover_early", task_mode="tired"),
    ]
    scores = [
        _score("tired", "pre_confession", "累了就先缓一下，我听着。"),
        _score("tired", "lover_early", "累了就先缓一下。"),
    ]

    report = build_relationship_axis_contrast_report(cases=cases, score_rows=scores)

    assert report["weak_pair_count"] == 1
    assert report["critical_pair_count"] == 1
    assert report["weak_pairs"][0]["reasons"] == ["lover_early_missing_intimacy_signal"]


def test_relationship_axis_contrast_report_allows_healthy_neighbor_shift() -> None:
    cases = [
        _case("back", "pre_confession"),
        _case("back", "lover_early"),
        _case("back", "lover_stable"),
    ]
    scores = [
        _score("back", "pre_confession", "回来啦，先坐一下，我听你说。"),
        _score("back", "lover_early", "回来啦，靠我一下，今天先别硬撑。"),
        _score("back", "lover_stable", "回来啦，想你了，先歇一口气。"),
    ]

    report = build_relationship_axis_contrast_report(cases=cases, score_rows=scores)

    assert report["weak_pair_count"] == 0
    assert report["critical_pair_count"] == 0
    assert report["scene_summaries"][0]["passed"] is True


def test_relationship_axis_contrast_report_fails_scene_with_axis_violation() -> None:
    cases = [_case("praise", "lover_stable", task_mode="praise")]
    scores = [_score("praise", "lover_stable", "哼，算你会说话。")]

    report = build_relationship_axis_contrast_report(cases=cases, score_rows=scores)

    scene = report["scene_summaries"][0]
    assert scene["axis_violation_count"] == 1
    assert scene["axis_violations"][0]["violations"] == ["lover_affection_under_visible"]
    assert scene["passed"] is False


def test_relationship_axis_contrast_report_treats_creative_edit_as_task_priority_scene() -> None:
    cases = [
        _case("creative_edit", "pre_confession", task_mode="creative_edit"),
        _case("creative_edit", "lover_early", task_mode="creative_edit"),
    ]
    scores = [
        _score("creative_edit", "pre_confession", "哼，我那叫优化流程，你不懂。"),
        _score("creative_edit", "lover_early", "哼，尼尼，你就是想偷懒。"),
    ]

    report = build_relationship_axis_contrast_report(cases=cases, score_rows=scores)

    assert report["weak_pair_count"] == 0
    assert report["scene_summaries"][0]["axis_violation_count"] == 0
    assert report["scene_summaries"][0]["passed"] is True


def test_write_relationship_axis_contrast_report_outputs_json_and_markdown(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    scores_path = tmp_path / "scores.jsonl"
    out_dir = tmp_path / "contrast_report"
    cases = [_case("back", "stranger"), _case("back", "new_acquaintance")]
    scores = [
        _score("back", "stranger", "回来啦，先歇一下。"),
        _score("back", "new_acquaintance", "回来啦，先歇一下。"),
    ]
    cases_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in cases), encoding="utf-8")
    scores_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in scores), encoding="utf-8")

    report = write_relationship_axis_contrast_report(cases_path=cases_path, scores_path=scores_path, out_dir=out_dir)
    rendered = render_markdown_report(report)

    assert report["weak_pair_count"] == 1
    assert "# Relationship Axis Contrast Report" in rendered
    assert (out_dir / "relationship_axis_contrast_summary.json").exists()
    assert (out_dir / "relationship_axis_contrast_report.md").exists()
