from __future__ import annotations

import json
from pathlib import Path

from tools.relationship_axis_report import (
    _scene_from_case_id,
    build_relationship_axis_report,
    render_markdown_report,
    write_relationship_axis_report,
)


def _case(case_id: str, relationship_distance: str, task_mode: str = "daily_back") -> dict:
    return {
        "case_id": case_id,
        "user_message": "我回来了",
        "reply_control_packet": {
            "relationship_distance": relationship_distance,
            "task_mode": task_mode,
            "response_move": "welcome_back",
            "question_budget": 0,
        },
    }


def _score(case_id: str, text: str, score: float = 90.0, passed: bool = True) -> dict:
    return {
        "case_id": case_id,
        "selected": {
            "text": text,
            "score": score,
            "passed": passed,
            "hard_fail": False,
            "reason_codes": [],
            "control_violations": [],
        },
    }


def test_relationship_axis_report_detects_axis_oversteps() -> None:
    report = build_relationship_axis_report(
        cases=[
            _case("relstage_back_stranger", "stranger"),
            _case("relstage_back_close_friend", "close_friend"),
            _case("relstage_back_lover_stable", "lover_stable"),
        ],
        score_rows=[
            _score("relstage_back_stranger", "尼尼，想你了，抱一下。", score=55.0, passed=False),
            _score("relstage_back_close_friend", "回来啦，我在，先歇一下。", score=95.0),
            _score("relstage_back_lover_stable", "回来啦，想你了，抱一下。", score=99.0),
        ],
    )

    assert report["total"] == 3
    assert report["violation_count"] == 1
    assert report["stage_summaries"][0]["stage"] == "stranger"
    stranger = next(item for item in report["stage_summaries"] if item["stage"] == "stranger")
    assert stranger["violation_counts"] == {
        "nickname_forbidden": 1,
        "physical_intimacy_overstep": 1,
        "explicit_romance_overstep": 1,
    }
    lover = next(item for item in report["stage_summaries"] if item["stage"] == "lover_stable")
    assert lover["violation_counts"] == {}
    assert lover["marker_rates"]["intimacy_signal"] == 1.0
    assert lover["marker_rates"]["physical_intimacy"] == 1.0
    assert lover["marker_rates"]["explicit_romance"] == 1.0


def test_relationship_axis_report_parses_relaxis_multi_word_scene_ids() -> None:
    assert _scene_from_case_id("relaxis_good_news_lover_stable") == "good_news"
    assert _scene_from_case_id("relaxis_recall_memory_new_acquaintance") == "recall_memory"


def test_relationship_axis_report_groups_axis_values_and_renders_markdown() -> None:
    report = build_relationship_axis_report(
        cases=[
            _case("relstage_tired_new_acquaintance", "new_acquaintance", task_mode="tired"),
            _case("relstage_tired_lover_early", "lover_early", task_mode="tired"),
        ],
        score_rows=[
            _score("relstage_tired_new_acquaintance", "那先缓一缓，别硬撑。", score=96.0),
            _score("relstage_tired_lover_early", "累了就先歇会儿。", score=82.0),
        ],
    )

    affection_values = {
        item["value"]: item for item in report["axis_summaries"]["affection_visibility"]
    }
    assert affection_values["none"]["count"] == 1
    assert affection_values["explicit_short"]["count"] == 1
    assert report["violation_count"] == 1
    assert report["violations"][0]["violations"] == ["lover_affection_under_visible"]

    rendered = render_markdown_report(report)

    assert "# Relationship Axis Report" in rendered
    assert "affection_visibility" in rendered
    assert "lover_affection_under_visible" in rendered


def test_relationship_axis_report_counts_soft_heart_phrases_as_intimacy_signal() -> None:
    report = build_relationship_axis_report(
        cases=[
            _case("relaxis_back_lover_early", "lover_early", task_mode="daily_back"),
            _case("relaxis_recall_memory_lover_stable", "lover_stable", task_mode="recall_memory"),
        ],
        score_rows=[
            _score("relaxis_back_lover_early", "回来啦，心里想着你呢。", score=99.0),
            _score("relaxis_recall_memory_lover_stable", "记得，你周末想看展的事我放在心里呢。", score=99.0),
        ],
    )

    assert report["violation_count"] == 0
    assert all(row["marker_flags"]["intimacy_signal"] for row in report["rows"])


def test_relationship_axis_report_counts_close_companion_presence() -> None:
    report = build_relationship_axis_report(
        cases=[_case("relaxis_bad_mood_lover_stable", "lover_stable", task_mode="bad_mood")],
        score_rows=[_score("relaxis_bad_mood_lover_stable", "别硬撑，我陪着你呢。", score=99.0)],
    )

    assert report["violation_count"] == 0
    assert report["rows"][0]["markers"]["close_presence"] == ["陪着", "陪着你"]


def test_relationship_axis_report_does_not_require_romance_for_creative_edit() -> None:
    report = build_relationship_axis_report(
        cases=[_case("relaxis_creative_edit_lover_stable", "lover_stable", task_mode="creative_edit")],
        score_rows=[_score("relaxis_creative_edit_lover_stable", "你明明就是想偷懒，还跟我装。", score=90.0)],
    )

    assert report["violation_count"] == 0


def test_write_relationship_axis_report_outputs_json_and_markdown(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    scores_path = tmp_path / "scores.jsonl"
    out_dir = tmp_path / "axis_report"
    cases = [_case("relstage_back_lover_stable", "lover_stable")]
    scores = [_score("relstage_back_lover_stable", "回来啦，想你了，抱一下。", score=99.0)]
    cases_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in cases), encoding="utf-8")
    scores_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in scores), encoding="utf-8")

    report = write_relationship_axis_report(cases_path=cases_path, scores_path=scores_path, out_dir=out_dir)

    assert report["total"] == 1
    assert (out_dir / "relationship_axis_summary.json").exists()
    assert (out_dir / "relationship_axis_report.md").exists()
