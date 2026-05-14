from __future__ import annotations

import json

from tools.surface_scene_quality_samples import (
    build_surface_scene_quality_sample_report,
    harvest_surface_scene_quality_samples,
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
