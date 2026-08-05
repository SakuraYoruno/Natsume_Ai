from __future__ import annotations

import builtins
import json
import os
import sys
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from edge_client.actuators import tts_engine
from edge_client.actuators.tts_engine import (
    _ModernSoVITSAdapter,
    _PersistentProcessSoVITSAdapter,
    SoVITSModelManager,
    TtsConfig,
    TtsState,
    TtsSynthResult,
    _ensure_modern_sdk_paths,
    _ensure_opencc_compat,
    _is_retryable_invalid_argument_error,
    _synthesize_audio,
    make_sovits_synthesizer,
)


class _WritableWorkerStdin:
    def write(self, _value: str) -> None:
        return None

    def flush(self) -> None:
        return None


class _RunningWorkerProcess:
    def __init__(self) -> None:
        self.stdin = _WritableWorkerStdin()

    def poll(self):  # noqa: ANN201
        return None


def test_persistent_worker_is_closed_after_synthesis_timeout(monkeypatch) -> None:
    adapter = _PersistentProcessSoVITSAdapter(config=TtsConfig(device="cpu"))
    process = _RunningWorkerProcess()
    monkeypatch.setattr(adapter, "start", lambda: "ready")
    monkeypatch.setattr(adapter, "_require_process", lambda: process)
    monkeypatch.setattr(
        adapter,
        "_read_message",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("worker timeout")),
    )
    close = MagicMock()
    monkeypatch.setattr(adapter, "close", close)

    with pytest.raises(TimeoutError, match="worker timeout"):
        adapter.synthesize(text="test", language="ja")

    close.assert_called_once_with()


def test_tts_manager_rejects_utterance_language_that_does_not_match_voice(
    monkeypatch,
) -> None:
    monkeypatch.delenv("EDGE_TTS_ALLOW_LANGUAGE_MISMATCH", raising=False)
    manager = SoVITSModelManager(TtsConfig(language="ja", device="cpu"))

    result = manager.synthesize("这是中文。", language="zh")

    assert result.success is False
    assert result.reason_code == "VOICE_SCHEMA_INVALID"
    assert "configured=ja, requested=zh" in result.detail


class _RetryModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def synthesize(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
        if len(self.calls) == 1:
            raise OSError("[Errno 22] Invalid argument")
        return {"audio": [0.1, -0.2, 0.05], "sample_rate": 32000}


def test_synthesize_audio_retries_without_prosody_on_invalid_argument() -> None:
    model = _RetryModel()

    audio, sample_rate = _synthesize_audio(
        model=model,
        text="test",
        language="ja",
        emotion_tag="HAPPY",
        emotion_intensity=0.8,
        timeout_s=5.0,
    )

    assert sample_rate == 32000
    assert len(audio) == 3
    assert len(model.calls) == 2
    assert "speed_factor" in model.calls[0]
    assert "energy_factor" in model.calls[0]
    assert "speed_factor" not in model.calls[1]
    assert "energy_factor" not in model.calls[1]


def test_synthesize_audio_raises_non_retryable_exception() -> None:
    class _FailModel:
        def synthesize(self, **kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs
            raise RuntimeError("backend exploded")

    with pytest.raises(RuntimeError):
        _synthesize_audio(
            model=_FailModel(),
            text="test",
            language="ja",
            emotion_tag=None,
            emotion_intensity=0.5,
            timeout_s=5.0,
        )


def test_retryable_invalid_argument_error_detection() -> None:
    assert _is_retryable_invalid_argument_error(OSError("[Errno 22] Invalid argument"))
    assert _is_retryable_invalid_argument_error(RuntimeError("invalid argument from backend"))
    assert not _is_retryable_invalid_argument_error(RuntimeError("backend exploded"))


def test_ensure_modern_sdk_paths_adds_bundled_runtime_site_packages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    model_root = tmp_path / "GPT-SoVITS"
    (model_root / "GPT_SoVITS" / "eres2net").mkdir(parents=True)
    runtime_site = model_root / "runtime" / "Lib" / "site-packages"
    opencc_bin = runtime_site / "opencc" / "clib" / "bin"
    opencc_bin.mkdir(parents=True)
    dll_calls: list[str] = []
    dll_handle = object()

    monkeypatch.setattr(sys, "path", [])
    monkeypatch.setattr(tts_engine, "_DLL_DIRECTORY_PATHS", set())
    monkeypatch.setattr(tts_engine, "_DLL_DIRECTORY_HANDLES", [])
    monkeypatch.setattr(
        tts_engine.os,
        "add_dll_directory",
        lambda path: dll_calls.append(str(path)) or dll_handle,
        raising=False,
    )

    _ensure_modern_sdk_paths(str(model_root))

    assert str(model_root) in sys.path
    assert str(model_root / "GPT_SoVITS") in sys.path
    assert str(model_root / "GPT_SoVITS" / "eres2net") in sys.path
    assert str(runtime_site) in sys.path
    assert sys.path.index(str(runtime_site)) > sys.path.index(str(model_root / "GPT_SoVITS" / "eres2net"))
    assert dll_calls == [str(opencc_bin)]
    assert tts_engine._DLL_DIRECTORY_HANDLES == [dll_handle]


def test_ensure_opencc_compat_installs_identity_fallback_when_opencc_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "opencc" or str(name).startswith("opencc."):
            raise ImportError("broken opencc")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "opencc", raising=False)
    monkeypatch.delitem(sys.modules, "opencc.clib", raising=False)
    monkeypatch.delitem(sys.modules, "opencc.clib.opencc_clib", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    _ensure_opencc_compat()

    module = sys.modules["opencc"]
    assert module.OpenCC("s2tw").convert("你好") == "你好"  # type: ignore[attr-defined]
    assert module.__version__ == "identity-fallback"


def test_model_manager_reloads_and_retries_after_invalid_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_model = object()
    good_model = object()
    calls: list[object] = []

    mgr = SoVITSModelManager(TtsConfig(device="cpu"))
    mgr._state = TtsState.READY
    mgr._model = bad_model

    def fake_synthesize_audio(*, model, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(model)
        _ = kwargs
        if model is bad_model:
            raise OSError("[Errno 22] Invalid argument")
        return [0.1, -0.2, 0.3], 32000

    def fake_load() -> TtsSynthResult:
        mgr._model = good_model
        mgr._state = TtsState.READY
        return TtsSynthResult(success=True, reason_code=None, detail="reloaded")

    monkeypatch.setattr(tts_engine, "_synthesize_audio", fake_synthesize_audio)
    monkeypatch.setattr(mgr, "load", fake_load)

    result = mgr.synthesize("こんにちは")

    assert result.success
    assert calls == [bad_model, good_model]
    assert mgr.health()["recovery_count"] == 1
    assert "recovered from invalid argument" in str(mgr.health()["last_recovery_detail"])


def test_model_manager_falls_back_to_fresh_process_when_reload_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_model = object()

    mgr = SoVITSModelManager(TtsConfig(device="cpu"))
    mgr._state = TtsState.READY
    mgr._model = bad_model

    def fake_synthesize_audio(*, model, **kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        if model is bad_model:
            raise OSError("[Errno 22] Invalid argument")
        raise AssertionError("unexpected in-process retry")

    def fake_load() -> TtsSynthResult:
        return TtsSynthResult(success=False, reason_code="TTS_MODEL_UNAVAILABLE", detail="reload boom")

    monkeypatch.setattr(tts_engine, "_synthesize_audio", fake_synthesize_audio)
    monkeypatch.setattr(tts_engine, "_allow_tts_subprocess_fallback", lambda: True)
    monkeypatch.setattr(mgr, "load", fake_load)
    monkeypatch.setattr(
        mgr,
        "_synthesize_via_fresh_process",
        lambda **kwargs: ([0.05, -0.05], 32000),
    )

    result = mgr.synthesize("こんにちは")

    assert result.success
    assert result.sample_rate == 32000
    assert mgr.state == TtsState.READY
    assert mgr.health()["backend_mode"] == "subprocess"
    assert mgr.health()["load_error"] is None
    assert mgr.health()["recovery_count"] == 1
    assert "fresh subprocess" in str(mgr.health()["last_recovery_detail"])


def test_model_manager_load_activates_persistent_subprocess_backend_on_invalid_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = SoVITSModelManager(TtsConfig(device="cpu"))

    def fake_load_sovits_model(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        raise OSError("[Errno 22] Invalid argument")

    monkeypatch.setattr(tts_engine, "_load_sovits_model", fake_load_sovits_model)
    monkeypatch.setattr(tts_engine, "_allow_tts_subprocess_fallback", lambda: True)

    result = mgr.load()

    assert result.success
    assert mgr.state == TtsState.READY
    assert mgr.health()["backend_mode"] == "subprocess"
    assert mgr.health()["load_error"] is None
    assert "fresh subprocess backend after load failure" in str(mgr.health()["last_recovery_detail"])


def test_modern_sovits_adapter_anchors_working_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    chdirs: list[str] = []

    class _FakeTTS:
        configs = SimpleNamespace(languages=["ja"])

        @staticmethod
        def run(run_inputs):  # type: ignore[no-untyped-def]
            _ = run_inputs
            yield 32000, [0.1, -0.2, 0.3]

    monkeypatch.setattr(tts_engine.os, "getcwd", lambda: r"C:\Users\ROG")
    monkeypatch.setattr(tts_engine.os.path, "isdir", lambda path: True)
    monkeypatch.setattr(tts_engine.os, "chdir", lambda path: chdirs.append(str(path)))

    adapter = _ModernSoVITSAdapter(
        tts=_FakeTTS(),
        reference_bank={
            "NEUTRAL": tts_engine._TtsReference(  # noqa: SLF001
                audio_path=r"D:\ref.wav",
                prompt_text="こんにちは",
                prompt_lang="ja",
            )
        },
        default_language="ja",
        working_directory=r"D:\AI_Models\GPT-SoVITS",
    )

    result = adapter.synthesize(text="こんにちは", language="ja")

    assert result["sample_rate"] == 32000
    assert chdirs == [r"D:\AI_Models\GPT-SoVITS", r"C:\Users\ROG"]


def test_modern_sovits_adapter_selects_emotion_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeTTS:
        configs = SimpleNamespace(languages=["zh", "ja"])

        @staticmethod
        def run(run_inputs):  # type: ignore[no-untyped-def]
            calls.append(dict(run_inputs))
            yield 32000, [0.1, -0.2, 0.3]

    monkeypatch.setattr(tts_engine.os.path, "isdir", lambda path: True)

    adapter = _ModernSoVITSAdapter(
        tts=_FakeTTS(),
        reference_bank={
            "NEUTRAL": tts_engine._TtsReference(  # noqa: SLF001
                audio_path=r"D:\neutral.wav",
                prompt_text="你好。",
                prompt_lang="zh",
            ),
            "HAPPY": tts_engine._TtsReference(  # noqa: SLF001
                audio_path=r"D:\happy.wav",
                prompt_text="太好啦。",
                prompt_lang="zh",
            ),
        },
        default_language="zh",
        working_directory=r"D:\AI_Models\GPT-SoVITS",
    )

    adapter.synthesize(text="我在呢。", language="zh", emotion="JOY")

    assert calls[0]["ref_audio_path"] == r"D:\happy.wav"
    assert calls[0]["prompt_text"] == "太好啦。"


def test_tts_manifest_language_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDGE_TTS_ALLOW_LANGUAGE_MISMATCH", raising=False)

    with pytest.raises(RuntimeError, match="language mismatch"):
        tts_engine._validate_tts_manifest_language(  # noqa: SLF001
            manifest={"model_id": "natsume-ja", "voice_language": "ja"},
            requested_language="zh",
        )


def test_resolve_modern_weights_prefers_manifest_weights(tmp_path) -> None:
    model_root = tmp_path / "gsv"
    gpt_dir = model_root / "GPT_weights_v2ProPlus"
    sovits_dir = model_root / "SoVITS_weights_v2ProPlus"
    gpt_dir.mkdir(parents=True)
    sovits_dir.mkdir(parents=True)
    selected_gpt = gpt_dir / "selected-e10.ckpt"
    selected_sovits = sovits_dir / "selected_e8_s4168.pth"
    decoy_gpt = gpt_dir / "zzzz-decoy.ckpt"
    decoy_sovits = sovits_dir / "zzzz_decoy.pth"
    for path in (selected_gpt, selected_sovits, decoy_gpt, decoy_sovits):
        path.write_bytes(b"weight")
    manifest = {
        "schema_version": 1,
        "voice_language": "zh",
        "weights": {
            "t2s": selected_gpt.relative_to(model_root).as_posix(),
            "vits": selected_sovits.relative_to(model_root).as_posix(),
        },
    }
    (model_root / "natsume_gpt_sovits_model_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    gpt_path, sovits_path = tts_engine._resolve_modern_weights(str(model_root))  # noqa: SLF001

    assert gpt_path == str(selected_gpt)
    assert sovits_path == str(selected_sovits)


def test_resolve_modern_weights_prefers_voice_bank_manifest(tmp_path) -> None:
    model_root = tmp_path / "gsv"
    gpt_dir = model_root / "GPT_weights_v2ProPlus"
    sovits_dir = model_root / "SoVITS_weights_v2ProPlus"
    gpt_dir.mkdir(parents=True)
    sovits_dir.mkdir(parents=True)

    root_gpt = gpt_dir / "root-e10.ckpt"
    root_sovits = sovits_dir / "root_e8_s1000.pth"
    voice_gpt = gpt_dir / "voice-e10.ckpt"
    voice_sovits = sovits_dir / "voice_e8_s2000.pth"
    for path in (root_gpt, root_sovits, voice_gpt, voice_sovits):
        path.write_bytes(b"weight")

    root_manifest = {
        "schema_version": 1,
        "voice_language": "zh",
        "weights": {
            "t2s": root_gpt.relative_to(model_root).as_posix(),
            "vits": root_sovits.relative_to(model_root).as_posix(),
        },
    }
    voice_manifest = {
        "schema_version": 1,
        "voice_language": "zh",
        "weights": {
            "t2s": voice_gpt.relative_to(model_root).as_posix(),
            "vits": voice_sovits.relative_to(model_root).as_posix(),
        },
    }
    (model_root / "natsume_gpt_sovits_model_manifest.json").write_text(
        json.dumps(root_manifest),
        encoding="utf-8",
    )
    voice_manifest_path = model_root / "natsume_gpt_sovits_model_manifest.aimis.json"
    voice_manifest_path.write_text(json.dumps(voice_manifest), encoding="utf-8")

    gpt_path, sovits_path = tts_engine._resolve_modern_weights(  # noqa: SLF001
        str(model_root),
        voice_bank_path=str(voice_manifest_path),
    )

    assert gpt_path == str(voice_gpt)
    assert sovits_path == str(voice_sovits)


def test_find_first_weight_uses_latest_mtime_not_lexicographic_name(tmp_path) -> None:
    model_root = tmp_path / "gsv"
    weight_dir = model_root / "SoVITS_weights_v2ProPlus"
    weight_dir.mkdir(parents=True)
    old_lexical_winner = weight_dir / "zzzz_old.pth"
    latest = weight_dir / "aaaa_latest.pth"
    old_lexical_winner.write_bytes(b"old")
    latest.write_bytes(b"new")
    os.utime(old_lexical_winner, (1000, 1000))
    os.utime(latest, (2000, 2000))

    selected = tts_engine._find_first_weight(  # noqa: SLF001
        str(model_root),
        [("SoVITS_weights_v2ProPlus", ".pth")],
    )

    assert selected == str(latest)


def test_tts_health_summarizes_manifest_weights_and_valid_references(tmp_path) -> None:
    model_root = tmp_path / "gsv"
    gpt_dir = model_root / "GPT_weights_v2ProPlus"
    sovits_dir = model_root / "SoVITS_weights_v2ProPlus"
    refs_dir = model_root / "reference"
    gpt_dir.mkdir(parents=True)
    sovits_dir.mkdir(parents=True)
    refs_dir.mkdir(parents=True)
    selected_gpt = gpt_dir / "selected-e10.ckpt"
    selected_sovits = sovits_dir / "selected_e8_s4168.pth"
    neutral_ref = refs_dir / "neutral.wav"
    happy_ref = refs_dir / "happy.wav"
    for path in (selected_gpt, selected_sovits, neutral_ref, happy_ref):
        path.write_bytes(b"stub")

    manifest = {
        "schema_version": 2,
        "model_id": "natsume-xilian-emotion-balanced",
        "project": "Natsume_Ai_Xilian_zh_emotion_balanced",
        "voice_language": "zh",
        "weights": {
            "t2s": selected_gpt.relative_to(model_root).as_posix(),
            "vits": selected_sovits.relative_to(model_root).as_posix(),
        },
        "references": [
            {
                "emotion_tag": "NEUTRAL",
                "audio_path": neutral_ref.relative_to(model_root).as_posix(),
                "prompt_text": "我在。",
                "prompt_lang": "zh",
            },
            {
                "emotion_tag": "HAPPY",
                "audio_path": happy_ref.relative_to(model_root).as_posix(),
                "prompt_text": "太好啦。",
                "prompt_lang": "zh",
            },
            {
                "emotion_tag": "BROKEN",
                "audio_path": "reference/missing.wav",
                "prompt_text": "这条不会被计入有效引用。",
                "prompt_lang": "zh",
            },
        ],
    }
    (model_root / "natsume_gpt_sovits_model_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    manager = SoVITSModelManager(config=TtsConfig(model_path=str(model_root)))

    artifacts = manager.health()["model_artifacts"]

    assert artifacts["manifest"]["model_id"] == "natsume-xilian-emotion-balanced"
    assert artifacts["manifest"]["project"] == "Natsume_Ai_Xilian_zh_emotion_balanced"
    assert artifacts["manifest"]["reference_count"] == 3
    assert artifacts["manifest"]["valid_reference_count"] == 3
    assert artifacts["manifest"]["reference_tags"] == ["DEFAULT", "HAPPY", "NEUTRAL"]
    assert artifacts["weights"]["t2s"] == str(selected_gpt)
    assert artifacts["weights"]["vits"] == str(selected_sovits)
    assert artifacts["weights"]["version"] == "v2ProPlus"


def test_sovits_synthesizer_exports_wav_when_requested(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np

    class _FakeEngine:
        @staticmethod
        def synthesize(*args, **kwargs):  # type: ignore[no-untyped-def]
            return TtsSynthResult(
                success=True,
                reason_code=None,
                detail="ok",
                audio_ref="memory://fake",
                audio_data=np.array([0.0, 0.25, -0.25], dtype=np.float32),
                duration_s=3 / 32000,
                sample_rate=32000,
            )

    monkeypatch.setenv("EDGE_TTS_EXPORT_DIR", str(tmp_path))

    event = make_sovits_synthesizer(_FakeEngine())(
        {
            "request_id": "req/export",
            "utterance_id": "utt-001",
            "text": "导出语音。",
            "export_audio": True,
        }
    )

    audio_path = event.get("audio_path")
    assert audio_path
    assert os.path.isfile(str(audio_path))
    with wave.open(str(audio_path), "rb") as handle:
        assert handle.getframerate() == 32000
        assert handle.getnchannels() == 1
        assert handle.getnframes() == 3
