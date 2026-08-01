from __future__ import annotations

import json
import wave
import zipfile

import pytest

from tools.gpt_sovits_edge_release import inspect_project, package_release


def _write_wav(path, *, duration_s: float = 5.0, sample_rate: int = 32000) -> None:
    frames = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def _write_project(root, project: str, text: str) -> None:
    (root / "GPT_weights_v2ProPlus").mkdir(parents=True)
    (root / "SoVITS_weights_v2ProPlus").mkdir(parents=True)
    (root / "GPT_weights_v2ProPlus" / f"{project}-e1.ckpt").write_bytes(b"gpt")
    (root / "SoVITS_weights_v2ProPlus" / f"{project}_e1_s1.pth").write_bytes(b"vits")
    wav_dir = root / "logs" / project / "5-wav32k"
    wav_dir.mkdir(parents=True)
    clip_name = "clip_0000000000_0000160000.wav"
    _write_wav(wav_dir / clip_name)
    (root / "logs" / project / "2-name2text.txt").write_text(
        f"{clip_name}\tphones\tNone\t{text}\n",
        encoding="utf-8",
    )


def test_inspect_project_detects_japanese_corpus(tmp_path) -> None:
    _write_project(tmp_path, "Natsume_Ai", "こんにちは。")

    result = inspect_project(tmp_path, "Natsume_Ai")

    assert result["dominant_language"] == "ja"
    assert result["clip_count"] == 1
    assert result["ready_for_packaging"] is True


def test_inspect_project_never_borrows_another_projects_weights(tmp_path) -> None:
    _write_project(tmp_path, "other_project", "你好呀。")
    wav_dir = tmp_path / "logs" / "missing_weights" / "5-wav32k"
    wav_dir.mkdir(parents=True)
    clip_name = "clip_0000000000_0000160000.wav"
    _write_wav(wav_dir / clip_name)
    (tmp_path / "logs" / "missing_weights" / "2-name2text.txt").write_text(
        f"{clip_name}\tphones\tNone\t你好呀。\n",
        encoding="utf-8",
    )

    result = inspect_project(tmp_path, "missing_weights")

    assert result["latest_weights"] == {"t2s": None, "vits": None}
    assert result["ready_for_packaging"] is False


def test_inspect_project_prefers_highest_checkpoint_epoch_over_mtime(tmp_path) -> None:
    project = "Natsume_Ai_zh"
    _write_project(tmp_path, project, "你好呀。")
    high_gpt = tmp_path / "GPT_weights_v2ProPlus" / f"{project}-e10.ckpt"
    high_vits = tmp_path / "SoVITS_weights_v2ProPlus" / f"{project}_e8_s800.pth"
    high_gpt.write_bytes(b"high-gpt")
    high_vits.write_bytes(b"high-vits")
    low_gpt = tmp_path / "GPT_weights_v2ProPlus" / f"{project}-e2.ckpt"
    low_vits = tmp_path / "SoVITS_weights_v2ProPlus" / f"{project}_e2_s200.pth"
    low_gpt.touch()
    low_vits.touch()

    result = inspect_project(tmp_path, project)

    assert result["latest_weights"]["t2s"].endswith(f"{project}-e10.ckpt")
    assert result["latest_weights"]["vits"].endswith(f"{project}_e8_s800.pth")


def test_package_release_refuses_language_mismatch(tmp_path) -> None:
    _write_project(tmp_path, "Natsume_Ai", "こんにちは。")

    with pytest.raises(SystemExit, match="refusing to package"):
        package_release(
            source_root=tmp_path,
            project="Natsume_Ai",
            voice_language="zh",
            output_dir=tmp_path / "out",
            model_id="natsume-test",
            reference_limit=4,
            allow_language_mismatch=False,
        )


def test_package_release_writes_manifest_and_overlay(tmp_path) -> None:
    _write_project(tmp_path, "Natsume_Ai_zh", "你好呀。")

    package_path = package_release(
        source_root=tmp_path,
        project="Natsume_Ai_zh",
        voice_language="zh",
        output_dir=tmp_path / "out",
        model_id="natsume-test",
        reference_limit=4,
        allow_language_mismatch=False,
    )

    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
        assert "natsume_gpt_sovits_model_manifest.json" in names
        assert "GPT_weights_v2ProPlus/Natsume_Ai_zh-e1.ckpt" in names
        assert "SoVITS_weights_v2ProPlus/Natsume_Ai_zh_e1_s1.pth" in names
        manifest = json.loads(archive.read("natsume_gpt_sovits_model_manifest.json"))

    assert manifest["voice_language"] == "zh"
    assert manifest["references"][0]["prompt_lang"] == "zh"


def test_package_release_preserves_emotion_balanced_references(tmp_path) -> None:
    project = "Natsume_Ai_zh_emotion_balanced"
    (tmp_path / "GPT_weights_v2ProPlus").mkdir(parents=True)
    (tmp_path / "SoVITS_weights_v2ProPlus").mkdir(parents=True)
    (tmp_path / "GPT_weights_v2ProPlus" / f"{project}-e10.ckpt").write_bytes(b"gpt")
    (tmp_path / "SoVITS_weights_v2ProPlus" / f"{project}_e8_s8.pth").write_bytes(b"vits")
    wav_dir = tmp_path / "logs" / project / "5-wav32k"
    wav_dir.mkdir(parents=True)
    for name in ("happy.wav", "neutral.wav"):
        _write_wav(wav_dir / name, duration_s=5.0)
    (tmp_path / "logs" / project / "2-name2text.txt").write_text(
        "happy.wav\tphones\tNone\t今天真的很开心呀。\n"
        "neutral.wav\tphones\tNone\t现在我们慢慢确认。\n",
        encoding="utf-8",
    )
    emotion_dir = tmp_path / "logs" / project / "emotion_balance"
    emotion_dir.mkdir(parents=True)
    (emotion_dir / "emotion_balance_report.json").write_text(
        json.dumps(
            {
                "top_selected_by_emotion": {
                    "happy": [
                        {
                            "wav_path": str(wav_dir / "happy.wav"),
                            "text": "今天真的很开心呀。",
                            "duration_s": 5.0,
                            "expressive_score": 5.0,
                            "confidence": 1.0,
                        }
                    ],
                    "neutral": [
                        {
                            "wav_path": str(wav_dir / "neutral.wav"),
                            "text": "现在我们慢慢确认。",
                            "duration_s": 5.0,
                            "expressive_score": 1.0,
                            "confidence": 1.0,
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    package_path = package_release(
        source_root=tmp_path,
        project=project,
        voice_language="zh",
        output_dir=tmp_path / "out",
        model_id="natsume-test",
        reference_limit=4,
        allow_language_mismatch=False,
    )

    with zipfile.ZipFile(package_path) as archive:
        manifest = json.loads(archive.read("natsume_gpt_sovits_model_manifest.json"))
        names = set(archive.namelist())

    tags = {item["emotion_tag"] for item in manifest["references"]}
    assert {"DEFAULT", "NEUTRAL", "HAPPY", "JOY"}.issubset(tags)
    assert "logs/Natsume_Ai_zh_emotion_balanced/5-wav32k/happy.wav" in names
    assert manifest["weights"]["t2s"] == f"GPT_weights_v2ProPlus/{project}-e10.ckpt"


def test_package_release_uses_wav_duration_for_plain_clip_names(tmp_path) -> None:
    project = "Natsume_Ai_zh"
    (tmp_path / "GPT_weights_v2ProPlus").mkdir(parents=True)
    (tmp_path / "SoVITS_weights_v2ProPlus").mkdir(parents=True)
    (tmp_path / "GPT_weights_v2ProPlus" / f"{project}-e1.ckpt").write_bytes(b"gpt")
    (tmp_path / "SoVITS_weights_v2ProPlus" / f"{project}_e1_s1.pth").write_bytes(b"vits")
    wav_dir = tmp_path / "logs" / project / "5-wav32k"
    wav_dir.mkdir(parents=True)
    clip_name = "xilian_00001.wav"
    _write_wav(wav_dir / clip_name, duration_s=4.0)
    (tmp_path / "logs" / project / "2-name2text.txt").write_text(
        f"{clip_name}\tphones\tNone\t你好呀。\n",
        encoding="utf-8",
    )

    result = inspect_project(tmp_path, project)
    package_path = package_release(
        source_root=tmp_path,
        project=project,
        voice_language="zh",
        output_dir=tmp_path / "out",
        model_id="natsume-test",
        reference_limit=4,
        allow_language_mismatch=False,
    )

    assert result["reference_candidate_count"] == 1
    assert package_path.is_file()


def test_package_release_adds_human_approved_romance_reference_bank(tmp_path) -> None:
    project = "Natsume_Ai_Xilian_zh"
    (tmp_path / "GPT_weights_v2ProPlus").mkdir(parents=True)
    (tmp_path / "SoVITS_weights_v2ProPlus").mkdir(parents=True)
    (tmp_path / "GPT_weights_v2ProPlus" / f"{project}-e10.ckpt").write_bytes(b"gpt")
    (tmp_path / "SoVITS_weights_v2ProPlus" / f"{project}_e8_s2400.pth").write_bytes(b"vits")
    wav_dir = tmp_path / "logs" / project / "5-wav32k"
    wav_dir.mkdir(parents=True)
    prompts = {
        "xilian_00025.wav": "那些甜言蜜语，真的很动听。",
        "xilian_00003.wav": "接下来，想如何度过美妙的双人时间呢？",
        "xilian_01292.wav": "分开的这段时间，有没有想念人家？",
        "xilian_01162.wav": "可以待在你身边吗？就这样静静的。",
        "xilian_00048.wav": "听着就让人家怦怦心跳。",
        "xilian_01101.wav": "无论哪种喜欢，都是弥足珍贵的爱。",
    }
    for name in prompts:
        _write_wav(wav_dir / name, duration_s=5.0)
    (tmp_path / "logs" / project / "2-name2text.txt").write_text(
        "".join(
            f"{name}\tphones\tNone\t{prompt}\n"
            for name, prompt in prompts.items()
        ),
        encoding="utf-8",
    )

    package_path = package_release(
        source_root=tmp_path,
        project=project,
        voice_language="zh",
        output_dir=tmp_path / "out",
        model_id="natsume-romance-test",
        reference_limit=8,
        allow_language_mismatch=False,
    )

    with zipfile.ZipFile(package_path) as archive:
        manifest = json.loads(archive.read("natsume_gpt_sovits_model_manifest.json"))
        names = set(archive.namelist())

    romance = {
        item["voice_style"]: item
        for item in manifest["references"]
        if item.get("voice_style")
    }
    assert set(romance) == {
        "ROMANTIC_TENDER",
        "SHY_AFFECTION",
        "FLIRTING",
        "LONGING",
        "QUIET_INTIMACY",
    }
    assert all(item["emotion_tag"] == "LOVING" for item in romance.values())
    assert romance["ROMANTIC_TENDER"]["aux_audio_paths"] == [
        f"logs/{project}/5-wav32k/xilian_01101.wav"
    ]
    assert f"logs/{project}/5-wav32k/xilian_00048.wav" in names
    assert f"logs/{project}/5-wav32k/xilian_01101.wav" in names
