from __future__ import annotations

import json
from pathlib import Path

from tools.build_relationship_axis_contrastive_cases import (
    CONTRASTIVE_SCENES,
    RELATIONSHIP_STAGES,
    build_relationship_axis_contrastive_cases,
    write_relationship_axis_contrastive_cases,
)


def test_build_relationship_axis_contrastive_cases_covers_all_scene_stage_pairs() -> None:
    cases = build_relationship_axis_contrastive_cases()

    assert len(cases) == len(CONTRASTIVE_SCENES) * len(RELATIONSHIP_STAGES) == 108
    assert len({case["case_id"] for case in cases}) == 108

    by_scene = {}
    for case in cases:
        scene = case["case_id"].split("_")[1]
        by_scene.setdefault(scene, []).append(case["reply_control_packet"]["relationship_distance"])

    assert len(by_scene) == len(CONTRASTIVE_SCENES)
    for stages in by_scene.values():
        assert stages == list(RELATIONSHIP_STAGES)


def test_build_relationship_axis_contrastive_cases_uses_only_compressed_control_inputs() -> None:
    case = build_relationship_axis_contrastive_cases()[0]

    assert set(case) == {"case_id", "facts", "must_not_do", "reply_control_packet", "user_message"}
    assert "history" not in json.dumps(case, ensure_ascii=False).lower()
    assert "raw_memory" not in json.dumps(case, ensure_ascii=False).lower()
    assert "organ_outputs" not in json.dumps(case, ensure_ascii=False).lower()
    assert "relationship_distance" in case["reply_control_packet"]


def test_build_relationship_axis_contrastive_cases_keeps_scene_facts_in_control_packet() -> None:
    cases = build_relationship_axis_contrastive_cases()
    recall = next(case for case in cases if case["case_id"] == "relaxis_recall_memory_familiar")
    creative = next(case for case in cases if case["case_id"] == "relaxis_creative_edit_familiar")

    assert recall["reply_control_packet"]["facts"] == ["记忆线索：用户周末想去看展。"]
    assert recall["reply_control_packet"]["must_include"] == ["看展"]
    assert creative["reply_control_packet"]["facts"] == ["待改原句：你明明就是想偷懒。"]
    assert "只给一句改法" in creative["reply_control_packet"]["style_anchor"]


def test_write_relationship_axis_contrastive_cases_outputs_files(tmp_path: Path) -> None:
    summary = write_relationship_axis_contrastive_cases(out_dir=tmp_path)

    assert summary["case_count"] == 108
    assert (tmp_path / "cases.jsonl").exists()
    assert (tmp_path / "summary.json").exists()
    assert len((tmp_path / "cases.jsonl").read_text(encoding="utf-8").splitlines()) == 108
