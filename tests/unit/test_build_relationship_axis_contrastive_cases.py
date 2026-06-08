from __future__ import annotations

import json
from pathlib import Path

from tools.build_relationship_axis_contrastive_cases import (
    CONTRASTIVE_SCENES,
    RELATIONSHIP_STAGES,
    build_relationship_axis_contrastive_cases,
    write_relationship_axis_contrastive_cases,
)


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
