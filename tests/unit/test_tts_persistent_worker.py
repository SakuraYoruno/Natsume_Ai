from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from edge_client.actuators import tts_persistent_worker as worker


def test_model_worker_keeps_application_imports_out_of_python39_boundary() -> None:
    source_path = Path(worker.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert "edge_client" not in imported_roots
    assert "natsume_contracts" not in imported_roots


def test_worker_resolves_manifest_weights_and_emotion_reference(tmp_path: Path) -> None:
    gpt = tmp_path / "GPT_weights_v2ProPlus" / "voice-e10.ckpt"
    sovits = tmp_path / "SoVITS_weights_v2ProPlus" / "voice_e8_s2400.pth"
    reference = tmp_path / "refs" / "playful.wav"
    for path in (gpt, sovits, reference):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stub")
    manifest_path = tmp_path / "voice.json"
    manifest_path.write_text(
        json.dumps(
            {
                "voice_language": "zh",
                "weights": {
                    "t2s": gpt.relative_to(tmp_path).as_posix(),
                    "vits": sovits.relative_to(tmp_path).as_posix(),
                },
                "references": [
                    {
                        "emotion_tag": "PLAYFUL",
                        "audio_path": reference.relative_to(tmp_path).as_posix(),
                        "prompt_text": "被我抓到了吧。",
                        "prompt_lang": "zh",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = worker._load_manifest(model_root=str(tmp_path), voice_bank_path=str(manifest_path))
    assert worker._resolve_weights(model_root=str(tmp_path), manifest=manifest) == (str(gpt), str(sovits))
    bank = worker._load_reference_bank(
        model_root=str(tmp_path),
        manifest=manifest,
        ref_audio_path="",
        ref_text="",
        language="zh",
    )

    assert worker._select_reference(bank, "PLAYFUL")["audio_path"] == str(reference)
    assert worker._select_reference(bank, "UNKNOWN")["audio_path"] == str(reference)


def test_worker_refuses_manifest_language_mismatch(tmp_path: Path) -> None:
    manifest_path = tmp_path / "voice.json"
    manifest_path.write_text(
        json.dumps({"voice_language": "ja"}),
        encoding="utf-8",
    )

    manifest = worker._load_manifest(model_root=str(tmp_path), voice_bank_path=str(manifest_path))

    with pytest.raises(RuntimeError, match="language mismatch"):
        worker._validate_manifest_language(manifest=manifest, requested_language="zh-CN")


def test_worker_never_falls_back_when_manifest_checkpoint_is_missing(tmp_path: Path) -> None:
    fallback_gpt = tmp_path / "GPT_weights_v2ProPlus" / "another-project-e10.ckpt"
    fallback_sovits = tmp_path / "SoVITS_weights_v2ProPlus" / "another-project_e8_s1.pth"
    for path in (fallback_gpt, fallback_sovits):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"other")
    manifest = {
        "weights": {
            "t2s": "GPT_weights_v2ProPlus/selected-e10.ckpt",
            "vits": "SoVITS_weights_v2ProPlus/selected_e8_s1.pth",
        }
    }

    with pytest.raises(RuntimeError, match="refusing cross-project fallback"):
        worker._resolve_weights(model_root=str(tmp_path), manifest=manifest)
