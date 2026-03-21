from __future__ import annotations

from unittest.mock import MagicMock

from edge_client.actuators.vts_driver import VTS_MOUTH_PARAM, VtsConfig, VtsState, VTubeStudioDriver
from edge_client.rpc.event_reporter import EdgeEventReporter
from edge_client.rpc.voice import VoiceRpcContract
from edge_client.runtime.voice_pipeline import EdgeVoicePipeline


class _AmbiguousAudio:
    """Array-like payload whose bool() raises (numpy-like)."""

    def __bool__(self) -> bool:  # pragma: no cover - behavior under test
        raise ValueError("ambiguous truth value")

    def __len__(self) -> int:
        return 128


def test_vts_before_playback_hook_accepts_ambiguous_audio_truth_value() -> None:
    speech_queue = MagicMock()
    pipeline = EdgeVoicePipeline(
        voice_rpc=MagicMock(spec=VoiceRpcContract),
        speech_queue=speech_queue,
        event_reporter=MagicMock(spec=EdgeEventReporter),
        vts_driver=None,
    )

    captured: dict[str, object] = {}

    def _fake_start(payload: dict, *, synth_event: dict | None = None):
        captured["emotion_tag"] = payload.get("emotion_tag")
        captured["has_audio"] = pipeline._synth_event_has_audio(synth_event)
        return None, None, {"state": "ok", "started": False}

    pipeline._start_vts_for_payload = _fake_start  # type: ignore[method-assign]

    pipeline._vts_before_playback_hook(
        {"emotion_tag": "HAPPY"},
        {"audio_data": _AmbiguousAudio(), "sample_rate": 32000},
    )

    assert captured["emotion_tag"] == "HAPPY"
    assert captured["has_audio"] is True
    assert isinstance(pipeline._pending_vts_result, dict)


def test_set_mouth_open_level_fallbacks_to_next_parameter(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_set(*, vts, param_name: str, value: float, timeout_s: float) -> None:
        _ = (vts, value, timeout_s)
        calls.append(param_name)
        if param_name == "BadMouthParam":
            raise RuntimeError("unknown parameter")

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameter", _fake_set)

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=("BadMouthParam", VTS_MOUTH_PARAM),
            mouth_volume_param_candidates=(),
            voice_silence_param_candidates=(),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()

    result = driver.set_mouth_open_level(0.73)

    assert result.success is True
    assert calls[:2] == ["BadMouthParam", VTS_MOUTH_PARAM]
    assert driver.health()["mouth_parameters"]["selected"] == [VTS_MOUTH_PARAM]


def test_set_mouth_open_level_falls_back_to_volume_parameter(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_set(*, vts, param_name: str, value: float, timeout_s: float) -> None:
        _ = (vts, value, timeout_s)
        calls.append(param_name)
        if param_name in {"BadMouthParam", VTS_MOUTH_PARAM}:
            raise RuntimeError("unknown parameter")

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameter", _fake_set)

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=("BadMouthParam", VTS_MOUTH_PARAM),
            mouth_volume_param_candidates=("VoiceVolumePlusMouthOpen",),
            voice_silence_param_candidates=(),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._mouth_parameter_plan = {
        "selected": ["BadMouthParam"],
        "mouth": ["BadMouthParam", VTS_MOUTH_PARAM],
        "volume": ["VoiceVolumePlusMouthOpen"],
        "silence": [],
        "available": [],
        "last_value": 0.0,
    }

    result = driver.set_mouth_open_level(0.73)

    assert result.success is True
    assert calls == ["BadMouthParam", VTS_MOUTH_PARAM, "VoiceVolumePlusMouthOpen"]
    assert driver.health()["mouth_parameters"]["selected"] == ["VoiceVolumePlusMouthOpen"]


def test_refresh_plan_infers_mouth_parameter_from_available_list(monkeypatch) -> None:
    monkeypatch.setattr(
        "edge_client.actuators.vts_driver._list_vts_input_parameter_ids",
        lambda *, vts, timeout_s: ["ParamJawOpen", "VoiceVolume", "VoiceSilence"],
    )

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=("NotExists",),
            mouth_volume_param_candidates=("NotVolume",),
            voice_silence_param_candidates=("NotSilence",),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()

    driver._refresh_mouth_parameter_plan()

    health = driver.health()["mouth_parameters"]
    assert health["mouth"][0] == "ParamJawOpen"
    assert health["volume"][0] == "VoiceVolume"
    assert health["silence"][0] == "VoiceSilence"


def test_set_expression_does_not_fallback_to_unrelated_hotkey(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_trigger(*, vts, hotkey_id: str, timeout_s: float) -> None:
        _ = (vts, timeout_s)
        calls.append(hotkey_id)
        if hotkey_id == "exp_happy_01":
            raise RuntimeError("not found")

    monkeypatch.setattr("edge_client.actuators.vts_driver._trigger_vts_hotkey", _fake_trigger)

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._hotkey_catalog = [{"id": "hotkey_remove_watermark", "name": "remove watermark"}]

    result = driver.set_expression("HAPPY")

    assert result.success is False
    assert calls == ["exp_happy_01"]


def test_set_expression_skips_blocked_hotkey_and_uses_safe_hint_match(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_trigger(*, vts, hotkey_id: str, timeout_s: float) -> None:
        _ = (vts, timeout_s)
        calls.append(hotkey_id)
        if hotkey_id == "exp_happy_01":
            raise RuntimeError("not found")

    monkeypatch.setattr("edge_client.actuators.vts_driver._trigger_vts_hotkey", _fake_trigger)

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._hotkey_catalog = [
        {"id": "hotkey_happy_watermark", "name": "happy watermark"},
        {"id": "hotkey_exp_happy", "name": "happy expression"},
    ]

    result = driver.set_expression("HAPPY")

    assert result.success is True
    assert result.expression_id == "hotkey_exp_happy"
    assert calls == ["exp_happy_01", "hotkey_exp_happy"]


def test_set_expression_neutral_skips_reset_all_hotkey(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_trigger(*, vts, hotkey_id: str, timeout_s: float) -> None:
        _ = (vts, timeout_s)
        calls.append(hotkey_id)
        if hotkey_id == "exp_neutral_01":
            raise RuntimeError("not found")

    monkeypatch.setattr("edge_client.actuators.vts_driver._trigger_vts_hotkey", _fake_trigger)

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._hotkey_catalog = [
        {"id": "hotkey_reset_all", "name": "移除所有按键表情"},
        {"id": "hotkey_neutral_eye", "name": "无语眼"},
    ]

    result = driver.set_expression("NEUTRAL")

    assert result.success is True
    assert result.expression_id == "hotkey_neutral_eye"
    assert calls == ["exp_neutral_01", "hotkey_neutral_eye"]
