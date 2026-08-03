from __future__ import annotations

from edge_client.actuators.vts_driver import (
    VTS_MOUTH_PARAM,
    VtsConfig,
    VtsState,
    VTubeStudioDriver,
    _discover_current_model_mouth_bindings,
    _set_vts_parameter,
    _set_vts_parameters,
)


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


def test_move_model_uses_vts_move_request_and_updates_health(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_move(**kwargs) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr("edge_client.actuators.vts_driver._move_vts_model", _fake_move)

    driver = VTubeStudioDriver()
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()

    result = driver.move_model(
        position_x=0.1,
        position_y=-0.05,
        rotation=3.0,
        relative=True,
        move_time_s=0.2,
    )

    assert result.success is True
    assert calls
    assert calls[0]["position_x"] == 0.1
    assert calls[0]["position_y"] == -0.05
    assert calls[0]["rotation"] == 3.0
    assert driver.health()["last_model_move"]["success"] is True


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
    assert calls == ["BadMouthParam", VTS_MOUTH_PARAM, "VoiceVolumePlusMouthOpen", "ParamMouthOpenY"]
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


def test_set_mouth_open_level_primary_only_does_not_fallback_to_volume_parameter(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_set(*, vts, param_name: str, value: float, timeout_s: float) -> None:
        _ = (vts, value, timeout_s)
        calls.append(param_name)
        raise RuntimeError("parameter busy")

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
    driver._mouth_parameter_plan = {
        "selected": [VTS_MOUTH_PARAM],
        "mouth": [VTS_MOUTH_PARAM],
        "volume": ["VoiceVolumePlusMouthOpen"],
        "silence": ["VoiceSilence"],
        "available": [VTS_MOUTH_PARAM, "VoiceVolumePlusMouthOpen", "VoiceSilence"],
        "last_value": 0.0,
    }

    result = driver.set_mouth_open_level(0.42, _include_companions=False)

    assert result.success is False
    assert calls == [VTS_MOUTH_PARAM]
    assert driver.health()["mouth_parameters"]["selected"] == [VTS_MOUTH_PARAM]


def test_set_mouth_open_level_primary_only_uses_selected_parameter(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_set(*, vts, param_name: str, value: float, timeout_s: float) -> None:
        _ = (vts, value, timeout_s)
        calls.append(param_name)

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameter", _fake_set)

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=("MouthOpen", VTS_MOUTH_PARAM),
            mouth_volume_param_candidates=("VoiceVolume",),
            voice_silence_param_candidates=(),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._mouth_parameter_plan = {
        "selected": ["VoiceVolume"],
        "mouth": ["MouthOpen", VTS_MOUTH_PARAM],
        "volume": ["VoiceVolume"],
        "silence": [],
        "available": ["VoiceVolume", "MouthOpen", VTS_MOUTH_PARAM],
        "last_value": 0.0,
    }

    result = driver.set_mouth_open_level(0.42, _include_companions=False)

    assert result.success is True
    assert calls == ["VoiceVolume"]
    assert driver.health()["mouth_parameters"]["selected"] == ["VoiceVolume"]


def test_set_mouth_open_level_batch_companions_includes_motion_values(monkeypatch) -> None:
    batches: list[dict[str, float]] = []

    def _fake_set_many(*, vts, parameter_values: dict[str, float], timeout_s: float) -> None:
        _ = (vts, timeout_s)
        batches.append(dict(parameter_values))

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameters", _fake_set_many)

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._mouth_parameter_plan = {
        "selected": ["VoiceVolume"],
        "mouth": ["VoiceVolume"],
        "volume": [],
        "silence": [],
        "available": [
            "VoiceVolume",
            "FaceAngleX",
            "FaceAngleY",
            "FacePositionX",
            "FacePositionY",
            "FacePositionZ",
        ],
        "last_value": 0.0,
        "model_open_inputs": ["VoiceVolume"],
    }

    result = driver.set_mouth_open_level(
        0.5,
        _include_companions=True,
        _batch_companions=True,
        _motion_values={
            "FaceAngleX": 1.2,
            "FaceAngleY": -0.7,
            "FacePositionX": 0.02,
            "FacePositionY": 0.03,
            "FacePositionZ": 0.01,
            "BadParam": 9.0,
        },
    )

    assert result.success is True
    assert batches == [
        {
            "VoiceVolume": 0.5,
            "FaceAngleX": 1.2,
            "FaceAngleY": -0.7,
            "FacePositionX": 0.02,
            "FacePositionY": 0.03,
            "FacePositionZ": 0.01,
        }
    ]
    health = driver.health()["mouth_parameters"]
    assert health["last_motion_parameters"] == [
        "FaceAngleX",
        "FaceAngleY",
        "FacePositionX",
        "FacePositionY",
        "FacePositionZ",
    ]
    assert health["last_motion_values"]["FacePositionY"] == 0.03
    assert health["last_motion_batch_parameters"] == list(batches[0].keys())

    close_result = driver.set_mouth_open_level(
        0.0,
        _include_companions=True,
        _batch_companions=True,
    )

    assert close_result.success is True
    health = driver.health()["mouth_parameters"]
    assert health["last_batch_parameters"] == ["VoiceVolume"]
    assert health["last_motion_parameters"] == [
        "FaceAngleX",
        "FaceAngleY",
        "FacePositionX",
        "FacePositionY",
        "FacePositionZ",
    ]


def test_set_mouth_open_level_batch_companions_uses_one_inject(monkeypatch) -> None:
    batches: list[dict[str, float]] = []

    def _fake_set_many(*, vts, parameter_values: dict[str, float], timeout_s: float) -> None:
        _ = (vts, timeout_s)
        batches.append(dict(parameter_values))

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameters", _fake_set_many)

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=("MouthOpen", VTS_MOUTH_PARAM),
            mouth_volume_param_candidates=("VoiceVolumePlusMouthOpen", "VoiceVolume"),
            voice_silence_param_candidates=("VoiceSilence",),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._mouth_parameter_plan = {
        "selected": ["MouthOpen"],
        "mouth": ["VoiceVolume", "MouthOpen"],
        "volume": ["VoiceVolume", "VoiceVolumePlusMouthOpen"],
        "silence": ["VoiceSilence"],
        "available": ["VoiceVolume", "MouthOpen", "VoiceVolumePlusMouthOpen", "VoiceSilence"],
        "last_value": 0.0,
        "model_open_inputs": ["VoiceVolume"],
    }

    result = driver.set_mouth_open_level(
        0.42,
        _include_companions=True,
        _batch_companions=True,
    )

    assert result.success is True
    assert batches == [
        {
            "VoiceVolume": 0.42,
            "MouthOpen": 0.42,
            "VoiceVolumePlusMouthOpen": 0.42,
            "VoiceSilence": 0.5800000000000001,
        }
    ]
    health = driver.health()["mouth_parameters"]
    assert health["selected"] == ["VoiceVolume"]
    assert health["last_batch_parameters"] == [
        "VoiceVolume",
        "MouthOpen",
        "VoiceVolumePlusMouthOpen",
        "VoiceSilence",
    ]


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


def test_discover_current_model_mouth_bindings_reads_vtube_parameter_settings(monkeypatch, tmp_path) -> None:
    vtube_path = tmp_path / "TestModel.vtube.json"
    vtube_path.write_text(
        """
        {
          "ParameterSettings": [
            {"Name": "Mouth Open", "Input": "VoiceVolume", "OutputLive2D": "ParamMouthOpenY"},
            {"Name": "Mouth Smile", "Input": "MouthSmile", "OutputLive2D": "ParamMouthForm2"},
            {"Name": "嘴宽窄", "Input": "MouthPucker", "OutputLive2D": "ParamMouthForm"}
          ]
        }
        """,
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "edge_client.actuators.vts_driver._resolve_current_model_vtube_path",
        lambda: vtube_path,
    )

    result = _discover_current_model_mouth_bindings()

    assert result["vtube_path"] == str(vtube_path)
    assert result["open_inputs"] == ["VoiceVolume"]
    assert result["smile_inputs"] == ["MouthSmile"]
    assert result["form_inputs"] == ["MouthPucker"]


def test_refresh_plan_prioritizes_model_bound_open_input(monkeypatch) -> None:
    monkeypatch.setattr(
        "edge_client.actuators.vts_driver._list_vts_input_parameter_ids",
        lambda *, vts, timeout_s: ["MouthOpen", "VoiceVolume", "VoiceSilence"],
    )
    monkeypatch.setattr(
        "edge_client.actuators.vts_driver._discover_current_model_mouth_bindings",
        lambda: {
            "vtube_path": "D:/Models/Test.vtube.json",
            "model_base_path": "D:/Models",
            "open_inputs": ["VoiceVolume"],
            "form_inputs": [],
            "smile_inputs": [],
            "last_error": None,
        },
    )

    driver = VTubeStudioDriver(
        config=VtsConfig(
            mouth_param_candidates=(VTS_MOUTH_PARAM, "ParamMouthOpenY"),
            mouth_volume_param_candidates=("VoiceVolumePlusMouthOpen",),
            voice_silence_param_candidates=("VoiceSilence",),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()

    driver._refresh_mouth_parameter_plan()

    health = driver.health()["mouth_parameters"]
    assert health["selected"][0] == "VoiceVolume"
    assert health["mouth"][0] == "VoiceVolume"
    assert health["model_open_inputs"] == ["VoiceVolume"]


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
    assert calls == ["hotkey_exp_happy"]


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
    assert calls == ["hotkey_neutral_eye"]


def test_reset_to_neutral_prefers_remove_all_expression_hotkey(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_trigger(*, vts, hotkey_id: str, timeout_s: float) -> None:
        _ = (vts, timeout_s)
        calls.append(hotkey_id)

    monkeypatch.setattr("edge_client.actuators.vts_driver._trigger_vts_hotkey", _fake_trigger)

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._hotkey_catalog = [
        {"id": "hotkey_reset_all", "name": "移除所有按键表情", "action": "RemoveAllExpressions"},
        {"id": "hotkey_neutral_eye", "name": "neutral eye", "action": "ToggleExpression"},
    ]

    result = driver.reset_to_neutral()

    assert result.success is True
    assert result.expression_id == "NEUTRAL"
    assert calls == []


def test_set_expression_caches_broken_hotkey_and_uses_safe_fallback(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_trigger(*, vts, hotkey_id: str, timeout_s: float) -> None:
        _ = (vts, timeout_s)
        calls.append(hotkey_id)
        if hotkey_id == "bad_happy":
            raise RuntimeError("VTS API error 500: Given parameter ParamBrowLY does not exist.")

    monkeypatch.setattr("edge_client.actuators.vts_driver._trigger_vts_hotkey", _fake_trigger)

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._hotkey_catalog = [
        {"id": "bad_happy", "name": "HK_Happy", "action": "ToggleExpression"},
        {"id": "safe_happy", "name": "happy expression", "action": "ToggleExpression"},
    ]

    first = driver.set_expression("HAPPY")
    second = driver.set_expression("HAPPY")

    assert first.success is True
    assert first.expression_id == "safe_happy"
    assert second.success is True
    assert second.expression_id == "safe_happy"
    assert calls == ["bad_happy", "safe_happy", "safe_happy"]
    assert "bad_happy" in driver.health()["bad_expression_hotkeys"]


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
    assert calls == ["hotkey_exp_happy"]


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


def test_set_mouth_open_level_reconnects_once_on_going_away_close(monkeypatch) -> None:
    calls: list[str] = []
    reconnect_calls: list[str] = []

    def _fake_set(*, vts, param_name: str, value: float, timeout_s: float) -> None:
        _ = (param_name, value, timeout_s)
        calls.append(str(vts))
        if str(vts) == "stale-conn":
            raise RuntimeError(
                "received 1001 (going away); then sent 1001 (going away)"
            )

    def _fake_connect(self) -> object:
        reconnect_calls.append("connect")
        self._state = VtsState.AUTHENTICATED
        self._vts = "fresh-conn"
        return type("ReconnectResult", (), {"success": True})()

    monkeypatch.setattr("edge_client.actuators.vts_driver._set_vts_parameter", _fake_set)
    monkeypatch.setattr(VTubeStudioDriver, "connect", _fake_connect)

    driver = VTubeStudioDriver(
        VtsConfig(
            mouth_param_candidates=(VTS_MOUTH_PARAM,),
            mouth_volume_param_candidates=(),
            voice_silence_param_candidates=(),
        )
    )
    driver._state = VtsState.AUTHENTICATED
    driver._vts = "stale-conn"

    result = driver.set_mouth_open_level(0.36)

    assert result.success is True
    assert reconnect_calls == ["connect"]
    assert calls[0] == "stale-conn"
    assert calls[-1] == "fresh-conn"


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


def test_connect_clears_stale_connection_error(monkeypatch) -> None:
    monkeypatch.setattr("edge_client.actuators.vts_driver._create_vts_connection", lambda config: object())
    monkeypatch.setattr("edge_client.actuators.vts_driver._authenticate_vts", lambda **kwargs: None)
    monkeypatch.setattr("edge_client.actuators.vts_driver._list_vts_hotkeys", lambda **kwargs: [])
    monkeypatch.setattr(VTubeStudioDriver, "_refresh_mouth_parameter_plan", lambda self: None)

    driver = VTubeStudioDriver(VtsConfig())
    driver._connection_error = "received 1002 (protocol error)"

    result = driver.connect()

    assert result.success is True
    assert driver.health()["connection_error"] is None


def test_set_vts_parameter_uses_raw_inject_request(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeRequestBuilder:
        def BaseRequest(self, message_type, data):  # noqa: ANN001, N802
            captured["message_type"] = message_type
            captured["data"] = data
            return {"messageType": message_type, "data": data}

    class _FakeVts:
        def __init__(self) -> None:
            self.vts_request = _FakeRequestBuilder()

        async def request(self, request):  # noqa: ANN001
            captured["request"] = request
            return {"data": {}}

    monkeypatch.setattr("edge_client.actuators.vts_driver._run_in_vts_loop", lambda coro, *, timeout_s: __import__("asyncio").run(coro))

    _set_vts_parameter(vts=_FakeVts(), param_name="MouthOpen", value=0.8, timeout_s=1.0)

    assert captured["message_type"] == "InjectParameterDataRequest"
    data = captured["data"]
    assert isinstance(data, dict)
    assert data["faceFound"] is True
    assert data["mode"] == "set"
    assert data["parameterValues"] == [
        {"id": "MouthOpen", "weight": 1.0, "value": 0.8},
    ]


def test_set_expression_prefers_discovered_hotkey_before_legacy_expression_id(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_trigger(*, vts, hotkey_id: str, timeout_s: float) -> None:
        _ = (vts, timeout_s)
        calls.append(hotkey_id)

    monkeypatch.setattr("edge_client.actuators.vts_driver._trigger_vts_hotkey", _fake_trigger)

    driver = VTubeStudioDriver(VtsConfig())
    driver._state = VtsState.AUTHENTICATED
    driver._vts = object()
    driver._hotkey_catalog = [
        {"id": "8bab2dee930d408381db550315a99d51", "name": "HK_Happy"},
    ]

    result = driver.set_expression("HAPPY")

    assert result.success is True
    assert result.expression_id == "8bab2dee930d408381db550315a99d51"
    assert calls == ["8bab2dee930d408381db550315a99d51"]
