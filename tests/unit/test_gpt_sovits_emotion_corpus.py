from __future__ import annotations

import json
import wave
from pathlib import Path

from tools.gpt_sovits_emotion_corpus import (
    build_balanced_training_project,
    build_emotion_corpus,
    classify_text_emotion,
    parse_source_list,
)


def _write_wav(path: Path, *, duration_s: float, sample_rate: int = 48000) -> None:
    frames = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def _write_project(tmp_path: Path) -> tuple[Path, str, Path]:
    gsv = tmp_path / "gsv"
    project = "Natsume_Ai_Xilian_zh_clean_lively"
    log_root = gsv / "logs" / project
    wav_root = gsv / "datasets" / project / "wavs"
    log_root.mkdir(parents=True)
    wav_root.mkdir(parents=True)

    samples = [
        ("xilian_00001.wav", "不仅如此，我们还都是开朗，美丽，乐观的青春少女，真是不可思议的巧合！", 5.0),
        ("xilian_00002.wav", "嘻，那就当是人家自己的心愿吧！", 3.5),
        ("xilian_00003.wav", "别担心，我会慢慢陪着你。", 4.0),
        ("xilian_00004.wav", "今天的天气很好。", 3.0),
    ]
    list_lines: list[str] = []
    for name, text, duration_s in samples:
        wav_path = wav_root / name
        _write_wav(wav_path, duration_s=duration_s)
        list_lines.append(f"{wav_path}|xilian|zh|{text}")
    (log_root / "0-source.list").write_text("\n".join(list_lines) + "\n", encoding="utf-8")

    skipped_wav = tmp_path / "source" / "archive_cyrene_1.wav"
    skipped_wav.parent.mkdir()
    _write_wav(skipped_wav, duration_s=18.0)
    manifest = {
        "schema_version": 1,
        "project": project,
        "source_name": "昔涟",
        "skipped": [
            {
                "wav_path": str(skipped_wav),
                "lab_path": str(skipped_wav.with_suffix(".lab")),
                "rel_path": "archive_cyrene_1.wav",
                "text": "这是命运的邂逅吗，还是…久别重逢呢？真让人心跳加速呀！",
                "duration_s": 18.0,
                "status": "skipped",
                "reason": "too_long",
            },
            {
                "wav_path": str(tmp_path / "source" / "music.wav"),
                "lab_path": str(tmp_path / "source" / "music.lab"),
                "rel_path": "music.wav",
                "text": "哆、徕、咪、发、嗦、啦、嘻，像数不尽的歌谣♪",
                "duration_s": 7.0,
                "status": "skipped",
                "reason": "music_symbol_text",
            },
            {
                "wav_path": str(tmp_path / "source" / "placeholder_long.wav"),
                "lab_path": str(tmp_path / "source" / "placeholder_long.lab"),
                "rel_path": "placeholder_long.wav",
                "text": "多亏有{NICKNAME}在，终于能为它们续写浪漫的结局了呀♪",
                "duration_s": 18.0,
                "status": "skipped",
                "reason": "too_long",
            },
        ],
    }
    (log_root / "dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return gsv, project, log_root


def test_classify_text_emotion_uses_chinese_expression_cues() -> None:
    assert classify_text_emotion("嘻，那就当是人家自己的心愿吧！").primary_emotion == "playful"
    assert classify_text_emotion("别担心，我会慢慢陪着你。").primary_emotion == "comfort"
    assert classify_text_emotion("好久不见，记得那时候的梦吗？").primary_emotion == "nostalgic"
    assert classify_text_emotion("今天的天气很好。").primary_emotion == "neutral"


def test_parse_source_list_preserves_native_gpt_sovits_columns(tmp_path) -> None:
    source_list = tmp_path / "0-source.list"
    source_list.write_text("C:\\voice\\a.wav|xilian|zh|你好｜呀\n", encoding="utf-8")

    entries = parse_source_list(source_list)

    assert len(entries) == 1
    assert str(entries[0].wav_path) == "C:\\voice\\a.wav"
    assert entries[0].speaker == "xilian"
    assert entries[0].language == "zh"
    assert entries[0].text == "你好｜呀"


def test_build_emotion_corpus_writes_lists_candidates_and_recovery(tmp_path) -> None:
    gsv, project, log_root = _write_project(tmp_path)

    summary = build_emotion_corpus(
        gpt_sovits_root=gsv,
        project=project,
        top_reference_count=2,
    )

    output_dir = log_root / "emotion_corpus"
    reference_candidates = json.loads((output_dir / "reference_candidates.json").read_text(encoding="utf-8"))
    recovery_candidates = json.loads((output_dir / "recovery_candidates.json").read_text(encoding="utf-8"))
    happy_list = (output_dir / "source_lists" / "happy.list").read_text(encoding="utf-8")
    playful_list = (output_dir / "source_lists" / "playful.list").read_text(encoding="utf-8")

    assert summary["prepared_count"] == 4
    assert summary["skipped_count"] == 3
    assert summary["emotion_counts_prepared"]["happy"] == 1
    assert summary["emotion_counts_prepared"]["playful"] == 1
    assert summary["emotion_counts_prepared"]["comfort"] == 1
    assert summary["emotion_counts_prepared"]["neutral"] == 1
    assert reference_candidates["happy"][0]["text"].startswith("不仅如此")
    assert "xilian_00001.wav|xilian|zh|" in happy_list
    assert "xilian_00002.wav|xilian|zh|" in playful_list
    assert {item["recommended_action"] for item in recovery_candidates} == {
        "sanitize_music_symbol_or_keep_as_style_reference",
        "split_at_punctuation",
    }
    assert len(recovery_candidates) == 2
    assert all("{NICKNAME}" not in item["text"] for item in recovery_candidates)
    assert summary["files"]["emotion_summary"] == str(output_dir / "emotion_summary.json")


def test_build_balanced_training_project_caps_neutral_and_writes_review_artifacts(tmp_path) -> None:
    gsv, project, _log_root = _write_project(tmp_path)
    build_emotion_corpus(gpt_sovits_root=gsv, project=project, top_reference_count=2)

    manifest = build_balanced_training_project(
        gpt_sovits_root=gsv,
        source_project=project,
        output_project="Natsume_Ai_Xilian_zh_emotion_balanced",
        target_emotion_count=10,
        target_neutral_ratio=0.25,
    )

    output_root = gsv / "logs" / "Natsume_Ai_Xilian_zh_emotion_balanced"
    source_list = (output_root / "0-source.list").read_text(encoding="utf-8")
    report = json.loads((output_root / "emotion_balance" / "emotion_balance_report.json").read_text(encoding="utf-8"))
    selected = json.loads((output_root / "emotion_balance" / "selected_records.json").read_text(encoding="utf-8"))
    neutral_list = (output_root / "emotion_balance" / "source_lists" / "neutral.list").read_text(encoding="utf-8")

    assert manifest["selected_counts_by_emotion"]["happy"] == 1
    assert manifest["selected_counts_by_emotion"]["playful"] == 1
    assert manifest["selected_counts_by_emotion"]["comfort"] == 1
    assert manifest["selected_counts_by_emotion"]["neutral"] == 1
    assert manifest["actual_neutral_ratio"] == 0.25
    assert manifest["shortage_to_target_by_emotion"]["loving"] == 10
    assert "xilian_00004.wav|xilian|zh|今天的天气很好。" in neutral_list
    assert len(selected) == 4
    assert source_list.count("|xilian|zh|") == manifest["selected_count"]
    assert "manual_recovery_queue_by_emotion" in report
