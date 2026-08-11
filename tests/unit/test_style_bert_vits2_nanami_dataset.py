from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.style_bert_vits2_nanami_dataset import (
    load_plan,
    parse_source_list,
    prepare_dataset,
    select_clips,
)


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source.list"
    lines: list[str] = []
    for clip_id, text in (
        ("nanami_00001", "今日はゆっくり話そうね"),
        ("nanami_00002", "大好き……です"),
        ("nanami_00003", "おやすみ"),
    ):
        wav = tmp_path / f"{clip_id}.wav"
        wav.write_bytes(b"RIFF" + clip_id.encode("ascii"))
        lines.append(f"{wav}|nanami|ja|{text}")
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    prosody = tmp_path / "prosody.json"
    prosody.write_text(
        json.dumps(
            {
                "features": {
                    "nanami_00001": {
                        "duration_s": 4.0,
                        "voiced_ratio": 0.6,
                        "pause_ratio": 0.2,
                    },
                    "nanami_00002": {
                        "duration_s": 4.5,
                        "voiced_ratio": 0.15,
                        "pause_ratio": 0.3,
                    },
                    "nanami_00003": {
                        "duration_s": 1.0,
                        "voiced_ratio": 0.8,
                        "pause_ratio": 0.1,
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source, prosody


def _write_plan(tmp_path: Path, source: Path) -> Path:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "nanami_test",
                "model_name": "NanamiTest",
                "speaker": "Nanami",
                "language": "JP",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "selection": {
                    "min_duration_s": 2.0,
                    "max_duration_s": 10.0,
                    "min_voiced_ratio": 0.2,
                    "max_pause_ratio": 0.55,
                },
                "style_anchors": {
                    "RomanticTender": ["nanami_00002"],
                    "QuietPresence": ["nanami_00003"],
                },
                "runtime_style_map": {
                    "ROMANTIC_TENDER": {"style": "RomanticTender", "weight": 0.75}
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return plan


def test_parse_and_select_forces_reviewed_anchors_through_basic_filter(tmp_path: Path) -> None:
    source, prosody_path = _write_inputs(tmp_path)
    plan = load_plan(_write_plan(tmp_path, source))
    clips = parse_source_list(source)
    prosody = json.loads(prosody_path.read_text(encoding="utf-8"))["features"]

    selected, excluded = select_clips(clips=clips, prosody=prosody, plan=plan)

    assert [(clip.clip_id, style, forced) for clip, style, forced in selected] == [
        ("nanami_00001", "Natural", False),
        ("nanami_00002", "RomanticTender", True),
        ("nanami_00003", "QuietPresence", True),
    ]
    assert excluded == {}


def test_prepare_dataset_writes_relative_jp_list_and_manifest(tmp_path: Path) -> None:
    source, prosody = _write_inputs(tmp_path)
    plan = _write_plan(tmp_path, source)
    output = tmp_path / "Data" / "NanamiTest"

    manifest = prepare_dataset(
        source_list=source,
        prosody_path=prosody,
        plan_path=plan,
        output_dir=output,
    )

    lines = (output / "esd.list").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "Natural/nanami_00001.wav|Nanami|JP|今日はゆっくり話そうね",
        "RomanticTender/nanami_00002.wav|Nanami|JP|大好き……です",
        "QuietPresence/nanami_00003.wav|Nanami|JP|おやすみ",
    ]
    assert manifest["selected_clip_count"] == 3
    assert manifest["style_counts"] == {
        "Natural": 1,
        "QuietPresence": 1,
        "RomanticTender": 1,
    }
    assert manifest["forced_anchor_ids"] == ["nanami_00002", "nanami_00003"]
    assert (output / "raw" / "Natural" / "nanami_00001.wav").is_file()


def test_prepare_dataset_refuses_source_digest_drift(tmp_path: Path) -> None:
    source, prosody = _write_inputs(tmp_path)
    plan = _write_plan(tmp_path, source)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source list digest drift"):
        prepare_dataset(
            source_list=source,
            prosody_path=prosody,
            plan_path=plan,
            output_dir=tmp_path / "out",
        )


def test_plan_rejects_anchor_overlap(tmp_path: Path) -> None:
    source, prosody_path = _write_inputs(tmp_path)
    plan_path = _write_plan(tmp_path, source)
    plan = load_plan(plan_path)
    plan["style_anchors"]["WarmCare"] = ["nanami_00002"]

    with pytest.raises(ValueError, match="appears in both"):
        select_clips(
            clips=parse_source_list(source),
            prosody=json.loads(prosody_path.read_text(encoding="utf-8"))["features"],
            plan=plan,
        )


def test_selection_applies_explicit_g2p_exclusion(tmp_path: Path) -> None:
    source, prosody_path = _write_inputs(tmp_path)
    plan = load_plan(_write_plan(tmp_path, source))
    plan["excluded_clips"] = {"nanami_00001": "unreadable_place_name"}

    selected, excluded = select_clips(
        clips=parse_source_list(source),
        prosody=json.loads(prosody_path.read_text(encoding="utf-8"))["features"],
        plan=plan,
    )

    assert [clip.clip_id for clip, _, _ in selected] == ["nanami_00002", "nanami_00003"]
    assert excluded == {"explicit_exclusion": 1}
