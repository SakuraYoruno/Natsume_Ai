from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tools.gpt_sovits_acoustic_style_miner import (
    RankedCandidate,
    SourceEntry,
    _eligible_clip_ids,
    _load_style_config,
    materialize_review_queue,
    parse_source_list,
    rank_style_candidates,
)


def _prosody(*, duration: float, pitch: float, rate: float) -> dict[str, float]:
    return {
        "duration_s": duration,
        "f0_median_st": pitch,
        "f0_range_st": 2.0,
        "f0_std_st": 1.0,
        "f0_delta_st": 0.2,
        "f0_slope_st_per_s": 0.0,
        "rms_mean_db": -15.0,
        "rms_std_db": 2.0,
        "rms_range_db": 8.0,
        "pause_ratio": 0.15,
        "max_pause_s": 0.2,
        "chars_per_s": rate,
    }


def test_parse_source_list_keeps_text_and_requires_real_audio(tmp_path: Path) -> None:
    wav = tmp_path / "xilian_00001.wav"
    wav.write_bytes(b"RIFF")
    source = tmp_path / "0-source.list"
    source.write_text(f"{wav}|xilian|zh|想和你多待一会儿。\n", encoding="utf-8")

    entries = parse_source_list(source)

    assert entries == [
        SourceEntry(
            clip_id="xilian_00001",
            wav_path=str(wav.resolve()),
            speaker="xilian",
            language="zh",
            text="想和你多待一会儿。",
        )
    ]


def test_rank_style_candidates_uses_acoustics_and_excludes_seed() -> None:
    entries = [
        SourceEntry("seed", "seed.wav", "xilian", "zh", "无关键词"),
        SourceEntry("near", "near.wav", "xilian", "zh", "普通台词"),
        SourceEntry("far", "far.wav", "xilian", "zh", "喜欢爱恋甜蜜"),
    ]
    embeddings = {
        "seed": np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        "near": np.asarray([0.95, 0.05, 0.0], dtype=np.float32),
        "far": np.asarray([0.0, 1.0, 1.0], dtype=np.float32),
    }
    prosody = {
        "seed": _prosody(duration=4.0, pitch=90.0, rate=4.0),
        "near": _prosody(duration=4.1, pitch=90.2, rate=4.1),
        "far": _prosody(duration=4.0, pitch=96.0, rate=7.0),
    }

    ranked = rank_style_candidates(
        entries=entries,
        embeddings_by_id=embeddings,
        prosody_by_id=prosody,
        emotion_labels_by_id={"near": ("其他/other", 0.8), "far": ("开心/happy", 0.9)},
        style_seeds={"SHY_AFFECTION": ("seed",)},
        top_k=2,
    )["SHY_AFFECTION"]

    assert [item.clip_id for item in ranked] == ["near", "far"]
    assert all(item.clip_id != "seed" for item in ranked)
    assert ranked[0].text == "普通台词"


def test_rank_style_candidates_rejects_unknown_seed() -> None:
    entry = SourceEntry("clip", "clip.wav", "xilian", "zh", "台词")
    with pytest.raises(ValueError, match="invalid seeds"):
        rank_style_candidates(
            entries=[entry],
            embeddings_by_id={"clip": np.asarray([1.0])},
            prosody_by_id={"clip": _prosody(duration=4.0, pitch=90.0, rate=4.0)},
            emotion_labels_by_id={},
            style_seeds={"LONGING": ("missing",)},
            top_k=1,
        )


def test_style_config_replaces_xilian_defaults_and_filters_stage_lines(tmp_path: Path) -> None:
    config = tmp_path / "style.json"
    config.write_text(
        '{"profile_id":"nanami","style_seeds":{"QUIET":["nanami_1"]},'
        '"candidate_policy":{"max_exclamation_marks":1,"reject_truncated_lines":true,'
        '"require_text_patterns":["今日|一緒"],"exclude_text_patterns":["戦闘|犯人"]}}',
        encoding="utf-8",
    )

    loaded = _load_style_config(config)
    eligible = _eligible_clip_ids(
        [
            SourceEntry("daily", "daily.wav", "nanami", "ja", "今日はゆっくりしようね"),
            SourceEntry("battle", "battle.wav", "nanami", "ja", "戦闘を開始する！"),
            SourceEntry("shout", "shout.wav", "nanami", "ja", "待って！！"),
            SourceEntry("cut", "cut.wav", "nanami", "ja", "今日は――"),
            SourceEntry("unrelated", "unrelated.wav", "nanami", "ja", "確認します"),
        ],
        loaded["candidate_policy"],
    )

    assert loaded["style_seeds"] == {"QUIET": ["nanami_1"]}
    assert eligible == {"daily"}


def test_rank_style_candidates_honours_eligible_pool() -> None:
    entries = [
        SourceEntry("seed", "seed.wav", "nanami", "ja", "seed"),
        SourceEntry("daily", "daily.wav", "nanami", "ja", "daily"),
        SourceEntry("stage", "stage.wav", "nanami", "ja", "stage"),
    ]
    embeddings = {
        "seed": np.asarray([1.0, 0.0]),
        "daily": np.asarray([0.8, 0.2]),
        "stage": np.asarray([0.99, 0.01]),
    }
    prosody = {
        clip_id: _prosody(duration=4.0, pitch=90.0, rate=4.0)
        for clip_id in ("seed", "daily", "stage")
    }

    ranked = rank_style_candidates(
        entries=entries,
        embeddings_by_id=embeddings,
        prosody_by_id=prosody,
        emotion_labels_by_id={},
        style_seeds={"QUIET": ("seed",)},
        top_k=3,
        eligible_clip_ids={"seed", "daily"},
    )["QUIET"]

    assert [item.clip_id for item in ranked] == ["daily"]


def test_materialized_queue_is_explicitly_human_review_only(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    candidate = RankedCandidate(
        rank=1,
        clip_id="xilian_00002",
        wav_path=str(source),
        text="只是想安静陪着你。",
        score=0.9,
        embedding_similarity=0.8,
        prosody_similarity=0.7,
        top_emotion_label="其他/other",
        top_emotion_score=0.6,
        prosody=_prosody(duration=4.0, pitch=90.0, rate=4.0),
    )

    materialize_review_queue(tmp_path, {"QUIET_INTIMACY": [candidate]})

    index = (tmp_path / "review" / "index.md").read_text(encoding="utf-8")
    assert "候选只用于人工听审" in index
    assert "只是想安静陪着你" in index
    assert (tmp_path / "review" / "quiet_intimacy" / "01_xilian_00002.wav").is_file()
