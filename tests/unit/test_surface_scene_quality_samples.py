from __future__ import annotations

import json

import pytest

from tools.surface_scene_quality_samples import (
    build_reviewed_surface_scene_template,
    build_surface_scene_quality_sample_report,
    harvest_surface_scene_quality_samples,
    merge_reviewed_surface_scene_samples,
    validate_reviewed_surface_scene_samples,
)


def _row() -> dict:
    return {
        "case": {
            "scene_id": "short_continue",
            "case_id": "short_ack_continue",
            "user_message": "继续",
        },
        "variants": {
            "memory_affect": {
                "selected_text": "哼，接着说。",
                "score": 94.0,
                "passed": True,
                "reason_codes": [],
                "observation_codes": [],
                "candidates": [
                    {
                        "text": "哼，接着说。",
                        "score": 94.0,
                        "passed": True,
                        "reason_codes": [],
                    },
                    {
                        "text": "好，接着往 surface 主链走。",
                        "score": 61.0,
                        "passed": False,
                        "reason_codes": ["SHORT_CONTINUE_MEMORY_OVERDISPLAY:surface,主链"],
                    },
                    {
                        "text": "memory://bad",
                        "score": 40.0,
                        "passed": False,
                        "reason_codes": ["SURFACE_PROCESS_META_LANGUAGE:memory"],
                    },
                ],
            }
        },
    }


def test_harvest_surface_scene_quality_samples_splits_good_and_bad() -> None:
    scenes = harvest_surface_scene_quality_samples([_row()], source_report="details.json")

    assert len(scenes) == 1
    scene = scenes[0]
    assert scene["scene_id"] == "short_continue"
    assert scene["case_ids"] == ["short_ack_continue"]
    assert [item["text"] for item in scene["good_examples"]] == ["哼，接着说。"]
    assert [item["text"] for item in scene["bad_examples"]] == ["好，接着往 surface 主链走。"]
    assert scene["summary"] == {"good_count": 1, "bad_count": 1}


def test_build_surface_scene_quality_sample_report_reads_details_and_exports_pack(tmp_path) -> None:
    details_path = tmp_path / "details.json"
    details_path.write_text(json.dumps({"results": [_row()]}, ensure_ascii=False), encoding="utf-8")

    report = build_surface_scene_quality_sample_report(details_paths=[details_path])

    assert report["sample_kind"] == "surface_scene_quality_harvest"
    assert report["source_reports"] == [str(details_path)]
    assert report["current_pack"]["sample_kind"] == "surface_scene_quality_pack"
    assert report["harvested"][0]["scene_id"] == "short_continue"
    assert report["harvested"][0]["good_examples"][0]["source_report"] == str(details_path)


def test_reviewed_surface_scene_template_keeps_candidates_unapproved() -> None:
    report = build_surface_scene_quality_sample_report(details_paths=[], include_pack=False)
    report["harvested"] = [
        {
            "scene_id": "short_continue",
            "case_ids": ["short_ack_continue"],
            "good_examples": [{"text": "哼，接着说。", "score": 94}],
            "bad_examples": [{"text": "继续主链。", "score": 62}],
        }
    ]

    template = build_reviewed_surface_scene_template(report)
    scene = template["scenes"][0]

    assert template["sample_kind"] == "surface_scene_quality_review_template"
    assert scene["good_examples"] == []
    assert scene["bad_examples"] == []
    assert scene["candidate_good_examples"][0]["text"] == "哼，接着说。"
    assert scene["candidate_bad_examples"][0]["text"] == "继续主链。"


def test_validate_reviewed_surface_scene_samples_catches_bad_payloads() -> None:
    errors = validate_reviewed_surface_scene_samples(
        {
            "sample_kind": "surface_scene_quality_reviewed",
            "scenes": [
                {
                    "scene_id": "short_continue",
                    "good_examples": ["同一句", "memory://leak"],
                    "bad_examples": ["同一句"],
                },
                {"scene_id": "new_scene", "good_examples": ["行。"]},
            ],
        },
        known_scene_ids={"short_continue"},
    )

    assert any("source-leaking" in error for error in errors)
    assert any("both good and bad" in error for error in errors)
    assert any("new scene" in error for error in errors)


def test_merge_reviewed_surface_scene_samples_appends_without_duplicates() -> None:
    pack = {
        "schema_version": 1,
        "sample_kind": "surface_scene_quality_pack",
        "scenes": [
            {
                "scene_id": "short_continue",
                "title": "短继续",
                "user_markers": ["继续"],
                "required_move": "短接。",
                "good_examples": ["嗯，接着来。"],
                "bad_examples": ["继续主链。"],
                "should_not_surface": ["主链"],
                "source": "manual_gold",
                "source_note": "初始。",
            }
        ],
    }
    reviewed = {
        "schema_version": 1,
        "sample_kind": "surface_scene_quality_reviewed",
        "scenes": [
            {
                "scene_id": "short_continue",
                "good_examples": ["嗯，接着来。", {"text": "哼，接着说。"}],
                "bad_examples": ["好，接着往 surface 主链走。"],
                "should_not_surface": ["surface"],
                "source_note": "review batch 1。",
            }
        ],
    }

    merged = merge_reviewed_surface_scene_samples(pack, reviewed)
    scene = merged["scenes"][0]

    assert scene["good_examples"] == ["嗯，接着来。", "哼，接着说。"]
    assert scene["bad_examples"] == ["继续主链。", "好，接着往 surface 主链走。"]
    assert scene["should_not_surface"] == ["主链", "surface"]
    assert scene["source"] == "manual_gold+reviewed"


def test_merge_reviewed_surface_scene_samples_rejects_template_payload() -> None:
    pack = {
        "schema_version": 1,
        "sample_kind": "surface_scene_quality_pack",
        "scenes": [
            {
                "scene_id": "short_continue",
                "good_examples": ["嗯，接着来。"],
                "bad_examples": ["继续主链。"],
            }
        ],
    }
    template = {
        "sample_kind": "surface_scene_quality_review_template",
        "scenes": [
            {
                "scene_id": "short_continue",
                "candidate_good_examples": [{"text": "哼，接着说。"}],
                "good_examples": [],
                "bad_examples": [],
            }
        ],
    }

    with pytest.raises(ValueError, match="surface_scene_quality_reviewed"):
        merge_reviewed_surface_scene_samples(pack, template)
