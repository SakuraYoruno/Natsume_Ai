from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from edge_client.actuators import style_bert_vits2_worker as worker
from edge_client.actuators import tts_engine


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_release(tmp_path: Path) -> Path:
    model = tmp_path / "nanami_e10.safetensors"
    styles = tmp_path / "style_vectors.npy"
    config = tmp_path / "config.json"
    model.write_bytes(b"model")
    np.save(styles, np.zeros((3, 256), dtype=np.float32))
    config.write_text(
        json.dumps(
            {
                "data": {
                    "style2id": {
                        "Neutral": 0,
                        "Natural": 1,
                        "RomanticTender": 2,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / worker.DEFAULT_MANIFEST
    manifest.write_text(
        json.dumps(
            {
                "schema_version": worker.SCHEMA_VERSION,
                "backend": "style_bert_vits2",
                "model_id": "nanami-ja-test",
                "voice_language": "ja",
                "model_file": model.name,
                "config_file": config.name,
                "style_vectors_file": styles.name,
                "sha256": {
                    "model": _sha256(model),
                    "config": _sha256(config),
                    "styles": _sha256(styles),
                },
                "runtime_style_map": {
                    "STEADY_COMPANION": {"style": "Natural", "weight": 0.45},
                    "ROMANTIC_TENDER": {"style": "RomanticTender", "weight": 0.78},
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_worker_has_no_application_imports() -> None:
    tree = ast.parse(Path(worker.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])

    assert "edge_client" not in roots
    assert "natsume_contracts" not in roots


def test_release_manifest_binds_exact_assets_and_styles(tmp_path: Path) -> None:
    manifest_path = _write_release(tmp_path)

    manifest = worker.load_release_manifest(model_path=str(tmp_path))

    assert manifest["_manifest_path"] == str(manifest_path)
    assert manifest["_assets"]["model"].name == "nanami_e10.safetensors"
    assert worker.resolve_style(manifest, voice_profile="ROMANTIC_TENDER") == (
        "RomanticTender",
        0.78,
        "ROMANTIC_TENDER",
    )
    assert worker.resolve_style(manifest, voice_profile="UNKNOWN") == (
        "Natural",
        0.45,
        "STEADY_COMPANION",
    )


def test_release_manifest_refuses_checkpoint_drift(tmp_path: Path) -> None:
    _write_release(tmp_path)
    (tmp_path / "nanami_e10.safetensors").write_bytes(b"changed")

    with pytest.raises(RuntimeError, match="model digest mismatch"):
        worker.load_release_manifest(model_path=str(tmp_path))


def test_style_worker_normalizes_int16_and_prevents_hard_clip() -> None:
    pcm = np.array([-32768, -16384, 0, 16384, 32767], dtype=np.int16)

    rendered = worker.normalize_audio(pcm, energy_factor=1.4)

    assert rendered.dtype == np.float32
    assert float(np.max(np.abs(rendered))) == pytest.approx(0.98, abs=1e-6)
    assert rendered.tolist() == pytest.approx(
        [-0.98, -0.49, 0.0, 0.49, 0.9799701],
        abs=1e-5,
    )


def test_tts_backend_aliases_choose_the_isolated_worker() -> None:
    assert tts_engine._normalize_tts_backend("sbv2") == "style_bert_vits2"
    assert tts_engine._resolve_tts_worker_script("style-bert-vits2") == (
        "style_bert_vits2_worker.py"
    )
    assert tts_engine._resolve_tts_worker_script("gpt_sovits") == (
        "tts_persistent_worker.py"
    )


def test_style_backend_forces_persistent_worker_even_when_global_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeWorker:
        def __init__(self, *, config: tts_engine.TtsConfig) -> None:
            self.config = config

        def start(self) -> str:
            return "fake style worker ready"

        def close(self) -> None:
            return None

    monkeypatch.setattr(tts_engine, "_PersistentProcessSoVITSAdapter", FakeWorker)
    monkeypatch.setattr(tts_engine, "_use_persistent_tts_worker", lambda: False)
    manager = tts_engine.SoVITSModelManager(
        tts_engine.TtsConfig(backend="style_bert_vits2", model_path=str(tmp_path))
    )

    result = manager.load()

    assert result.success is True
    assert manager.health()["backend"] == "style_bert_vits2"
    assert manager.health()["backend_mode"] == "persistent_worker"
