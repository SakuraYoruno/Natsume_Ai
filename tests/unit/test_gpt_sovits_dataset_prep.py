from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from tools.gpt_sovits_dataset_prep import inspect_lab_dataset, prepare_lab_dataset


def _write_wav(path: Path, *, duration_s: float, sample_rate: int = 48000) -> None:
    frames = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def _write_pair(root: Path, name: str, text: str, *, duration_s: float) -> None:
    _write_wav(root / f"{name}.wav", duration_s=duration_s)
    (root / f"{name}.lab").write_text(text, encoding="utf-8")


def test_inspect_lab_dataset_counts_usable_and_skipped(tmp_path) -> None:
    _write_pair(tmp_path, "good", "你好呀", duration_s=4.0)
    _write_pair(tmp_path, "short", "太短", duration_s=0.5)

    result = inspect_lab_dataset(tmp_path)

    assert result["wav_count"] == 2
    assert result["usable_count"] == 1
    assert result["skipped_count"] == 1
    assert result["skip_reasons"] == {"too_short": 1}


def test_prepare_lab_dataset_requires_authorization(tmp_path) -> None:
    source = tmp_path / "source"
    gsv = tmp_path / "gsv"
    source.mkdir()
    gsv.mkdir()
    _write_pair(source, "good", "你好呀", duration_s=4.0)

    with pytest.raises(ValueError, match="authorization"):
        prepare_lab_dataset(
            source_root=source,
            gpt_sovits_root=gsv,
            project="Natsume_Ai_Xilian_zh",
            speaker="xilian",
            language="zh",
            source_name="xilian",
        )


def test_prepare_lab_dataset_writes_list_manifest_and_auth(tmp_path) -> None:
    source = tmp_path / "source"
    gsv = tmp_path / "gsv"
    source.mkdir()
    gsv.mkdir()
    _write_pair(source, "good", "你好|呀", duration_s=4.0)
    _write_pair(source, "long", "这句太长", duration_s=13.0)

    manifest = prepare_lab_dataset(
        source_root=source,
        gpt_sovits_root=gsv,
        project="Natsume_Ai_Xilian_zh",
        speaker="xilian",
        language="zh",
        source_name="昔涟",
        user_asserted_authorization="授权公开声源，可用于本地语音模型训练。",
    )

    log_root = gsv / "logs" / "Natsume_Ai_Xilian_zh"
    dataset_root = gsv / "datasets" / "Natsume_Ai_Xilian_zh"
    source_list = (log_root / "0-source.list").read_text(encoding="utf-8")
    saved_manifest = json.loads((log_root / "dataset_manifest.json").read_text(encoding="utf-8"))
    auth = json.loads((log_root / "source_authorization.json").read_text(encoding="utf-8"))

    assert manifest["prepared_count"] == 1
    assert saved_manifest["skipped_count"] == 1
    assert "xilian_00001.wav|xilian|zh|你好｜呀" in source_list
    assert (dataset_root / "wavs" / "xilian_00001.wav").is_file()
    assert auth["authorization_status"] == "user_asserted"


def test_prepare_lab_dataset_can_skip_placeholder_text(tmp_path) -> None:
    source = tmp_path / "source"
    gsv = tmp_path / "gsv"
    source.mkdir()
    gsv.mkdir()
    _write_pair(source, "good", "你好呀", duration_s=4.0)
    _write_pair(source, "placeholder", "{NICKNAME}最好啦", duration_s=4.0)
    _write_pair(source, "music", "送你一朵花♪", duration_s=4.0)

    result = inspect_lab_dataset(
        source,
        skip_placeholder_text=True,
        skip_music_symbols=True,
    )
    manifest = prepare_lab_dataset(
        source_root=source,
        gpt_sovits_root=gsv,
        project="Natsume_Ai_Xilian_zh",
        speaker="xilian",
        language="zh",
        source_name="昔涟",
        user_asserted_authorization="授权公开声源，可用于本地语音模型训练。",
        skip_placeholder_text=True,
        skip_music_symbols=True,
    )

    source_list = (gsv / "logs" / "Natsume_Ai_Xilian_zh" / "0-source.list").read_text(encoding="utf-8")

    assert result["skip_reasons"] == {"music_symbol_text": 1, "placeholder_text": 1}
    assert result["text_filters"]["skip_placeholder_text"] is True
    assert manifest["prepared_count"] == 1
    assert manifest["text_filters"]["skip_music_symbols"] is True
    assert "你好呀" in source_list
    assert "{NICKNAME}" not in source_list
    assert "♪" not in source_list
