from __future__ import annotations

from edge_client.actuators.vts_driver import VTS_MOUTH_PARAM, VtsConfig, VtsState, VTubeStudioDriver


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


def test_set_mouth_open_level_primary_only_skips_companion_parameters(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_set(*, vts, param_name: str, value: float, timeout_s: float) -> None:
        _ = (vts, value, timeout_s)
        calls.append(param_name)

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameter", _fake_set)

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=(VTS_MOUTH_PARAM,),
            mouth_volume_param_candidates=("VoiceVolumePlusMouthOpen",),
            voice_silence_param_candidates=("VoiceSilence",),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()

    result = driver.set_mouth_open_level(0.51, _include_companions=False)

    assert result.success is True
    assert calls == [VTS_MOUTH_PARAM]
    assert driver.health()["mouth_parameters"]["selected"] == [VTS_MOUTH_PARAM]


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


def test_refresh_plan_accepts_parameter_names_from_vts_response(monkeypatch) -> None:
    monkeypatch.setattr(
        "edge_client.actuators.vts_driver._list_vts_input_parameter_ids",
        lambda *, vts, timeout_s: ["MouthOpen", "VoiceVolumePlusMouthOpen", "VoiceSilence"],
    )

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=("ParamMouthOpenY",),
            mouth_volume_param_candidates=("VoiceVolume",),
            voice_silence_param_candidates=("VoiceSilence",),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()

    driver._refresh_mouth_parameter_plan()

    health = driver.health()["mouth_parameters"]
    assert health["available"][:3] == ["MouthOpen", "VoiceVolumePlusMouthOpen", "VoiceSilence"]
    assert health["mouth"][0] == "MouthOpen"


def test_set_mouth_open_level_retries_unfiltered_candidates_when_available_filter_is_stale(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_set(*, vts, param_name: str, value: float, timeout_s: float) -> None:
        _ = (vts, value, timeout_s)
        calls.append(param_name)
        if param_name == "VoiceVolumePlusMouthOpen":
            raise RuntimeError("parameter locked")

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameter", _fake_set)

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=("ParamMouthOpenY",),
            mouth_volume_param_candidates=("VoiceVolumePlusMouthOpen",),
            voice_silence_param_candidates=(),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._mouth_parameter_plan = {
        "selected": ["VoiceVolumePlusMouthOpen"],
        "mouth": ["VoiceVolumePlusMouthOpen"],
        "volume": ["VoiceVolumePlusMouthOpen"],
        "silence": [],
        "available": ["VoiceVolumePlusMouthOpen"],
        "last_value": 0.0,
    }

    result = driver.set_mouth_open_level(0.81)

    assert result.success is True
    assert calls[:2] == ["VoiceVolumePlusMouthOpen", "ParamMouthOpenY"]
    assert driver.health()["mouth_parameters"]["selected"] == ["ParamMouthOpenY"]


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
        {"id": "hotkey_reset_all", "name": "remove all"},
        {"id": "hotkey_neutral_eye", "name": "neutral eye"},
    ]

    result = driver.set_expression("NEUTRAL")

    assert result.success is True
    assert result.expression_id == "hotkey_neutral_eye"
    assert calls == ["exp_neutral_01", "hotkey_neutral_eye"]


def test_set_expression_refreshes_hotkeys_after_primary_failure(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_trigger(*, vts, hotkey_id: str, timeout_s: float) -> None:
        _ = (vts, timeout_s)
        calls.append(hotkey_id)
        if hotkey_id == "exp_happy_01":
            raise RuntimeError("VTS API error 500: Given parameter ParamBrowRY does not exist.")

    monkeypatch.setattr("edge_client.actuators.vts_driver._trigger_vts_hotkey", _fake_trigger)
    monkeypatch.setattr(
        "edge_client.actuators.vts_driver._list_vts_hotkeys",
        lambda *, vts, timeout_s: [{"id": "hotkey_exp_happy", "name": "happy expression"}],
    )

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._hotkey_catalog = []

    result = driver.set_expression("HAPPY")

    assert result.success is True
    assert result.expression_id == "hotkey_exp_happy"
    assert calls == ["exp_happy_01", "hotkey_exp_happy"]


def test_set_mouth_open_level_reconnects_once_on_protocol_error(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []
    reconnect_calls: list[str] = []

    def _fake_set(*, vts, param_name: str, value: float, timeout_s: float) -> None:
        _ = timeout_s
        calls.append((str(vts), value))
        if str(vts) == "stale-conn":
            raise RuntimeError("received 1002 (protocol error); then sent 1002 (protocol error)")

    def _fake_connect(self) -> object:
        reconnect_calls.append("connect")
        self._state = VtsState.AUTHENTICATED
        self._vts = "fresh-conn"
        return type("ReconnectResult", (), {"success": True})()

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameter", _fake_set)
    monkeypatch.setattr(VTubeStudioDriver, "connect", _fake_connect)

    driver = VTubeStudioDriver(VtsConfig(mouth_param_candidates=(VTS_MOUTH_PARAM,), mouth_volume_param_candidates=(), voice_silence_param_candidates=()))
    driver._state = VtsState.AUTHENTICATED
    driver._vts = "stale-conn"

    result = driver.set_mouth_open_level(0.42)

    assert result.success is True
    assert reconnect_calls == ["connect"]
    assert calls[0][0] == "stale-conn"
    assert calls[-1][0] == "fresh-conn"


def test_set_expression_reconnects_once_on_protocol_error(monkeypatch) -> None:
    calls: list[str] = []
    reconnect_calls: list[str] = []

    def _fake_trigger(*, vts, hotkey_id: str, timeout_s: float) -> None:
        _ = timeout_s
        calls.append(f"{vts}:{hotkey_id}")
        if str(vts) == "stale-conn":
            raise RuntimeError("received 1002 (protocol error); then sent 1002 (protocol error)")

    def _fake_connect(self) -> object:
        reconnect_calls.append("connect")
        self._state = VtsState.AUTHENTICATED
        self._vts = "fresh-conn"
        return type("ReconnectResult", (), {"success": True})()

    monkeypatch.setattr("edge_client.actuators.vts_driver._trigger_vts_hotkey", _fake_trigger)
    monkeypatch.setattr(VTubeStudioDriver, "connect", _fake_connect)

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = "stale-conn"
    driver._hotkey_catalog = []

    result = driver.set_expression("HAPPY")

    assert result.success is True
    assert reconnect_calls == ["connect"]
    assert calls == ["stale-conn:exp_happy_01", "fresh-conn:exp_happy_01"]
