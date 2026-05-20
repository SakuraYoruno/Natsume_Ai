from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

import pytest

from edge_client.actuators import tts_engine
from edge_client.actuators.tts_engine import (
    _ModernSoVITSAdapter,
    SoVITSModelManager,
    TtsConfig,
    TtsState,
    TtsSynthResult,
    _ensure_modern_sdk_paths,
    _ensure_opencc_compat,
    _is_retryable_invalid_argument_error,
    _synthesize_audio,
)


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
        ref_audio_path=r"D:\ref.wav",
        ref_text="こんにちは",
        default_language="ja",
        working_directory=r"D:\AI_Models\GPT-SoVITS",
    )

    result = adapter.synthesize(text="こんにちは", language="ja")

    assert result["sample_rate"] == 32000
    assert chdirs == [r"D:\AI_Models\GPT-SoVITS", r"C:\Users\ROG"]
