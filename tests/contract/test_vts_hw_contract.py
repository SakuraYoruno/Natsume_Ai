"""T-VTS-HW-01: Edge VTube Studio hardware integration contract tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("Natsume_AUDIT_DB", ":memory:")

from host_brain.core.errors import (  # noqa: E402
    AVATAR_TAG_INVALID,
    VTS_API_ERROR,
    VTS_CONNECTION_FAILED,
    VTS_EXPRESSION_TIMEOUT,
)
from edge_client.actuators.vts_driver import (  # noqa: E402
    EMOTION_TO_EXPRESSION_ID,
    TALK_END,
    TALK_START,
    VTS_MOUTH_CLOSE_VALUE,
    VTS_MOUTH_OPEN_VALUE,
    VTS_MOUTH_PARAM,
    VtsConfig,
    VtsDriverResult,
    VtsState,
    VTubeStudioDriver,
)


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "voice"


def _load_fixture(subpath: str) -> dict:
    fpath = FIXTURES_DIR / subpath
    with open(fpath, "r", encoding="utf-8-sig") as f:
        return json.load(f)


class TestVtsDriverLifecycle:
    def test_initial_state_is_disconnected(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        assert driver.state == VtsState.DISCONNECTED

    def test_connect_without_pyvts_returns_connection_failed(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.connect()
        assert not result.success
        assert result.reason_code == VTS_CONNECTION_FAILED
        assert driver.state == VtsState.ERROR

    def test_disconnect_resets_state(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._talking = True
        driver._current_expression = EMOTION_TO_EXPRESSION_ID["HAPPY"]
        result = driver.disconnect()
        assert result.success
        assert driver.state == VtsState.DISCONNECTED
        assert not driver.talking
        assert driver.current_expression is None

    def test_health_snapshot_structure(self) -> None:
        driver = VTubeStudioDriver(VtsConfig(host="127.0.0.1", port=9090))
        health = driver.health()
        assert health["state"] == VtsState.DISCONNECTED.value
        assert health["emotion_map_size"] == len(EMOTION_TO_EXPRESSION_ID)
        assert set(health["reconnect"].keys()) == {
            "total_reconnects",
            "total_reconnect_failures",
            "reconnect_attempts",
            "reconnecting",
            "reconnect_events_count",
        }


class TestVtsTalkGate:
    def test_talk_start_when_disconnected_triggers_connect(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.talk_start()
        assert not result.success
        assert result.reason_code == VTS_CONNECTION_FAILED

    def test_talk_end_when_disconnected_is_noop(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.talk_end()
        assert result.success
        assert result.action == TALK_END
        assert not driver.talking

    def test_talk_start_sets_talking_flag(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()
        with patch("edge_client.actuators.vts_driver._set_vts_parameter") as mocked_set:
            result = driver.talk_start()
        assert result.success
        assert driver.talking
        assert mocked_set.call_args_list == [
            call(
                vts=driver._vts,
                param_name=VTS_MOUTH_PARAM,
                value=VTS_MOUTH_OPEN_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceVolumePlusMouthOpen",
                value=VTS_MOUTH_OPEN_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceVolume",
                value=VTS_MOUTH_OPEN_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceSilence",
                value=0.0,
                timeout_s=driver._config.api_timeout_s,
            ),
        ]

    def test_talk_end_clears_talking_flag(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()
        driver._talking = True
        with patch("edge_client.actuators.vts_driver._set_vts_parameter") as mocked_set:
            result = driver.talk_end()
        assert result.success
        assert not driver.talking
        assert mocked_set.call_args_list == [
            call(
                vts=driver._vts,
                param_name=VTS_MOUTH_PARAM,
                value=VTS_MOUTH_CLOSE_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceVolumePlusMouthOpen",
                value=VTS_MOUTH_CLOSE_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceVolume",
                value=VTS_MOUTH_CLOSE_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceSilence",
                value=1.0,
                timeout_s=driver._config.api_timeout_s,
            ),
        ]


class TestVtsExpressionMapping:
    @pytest.mark.parametrize(
        "tag",
        [
            "NEUTRAL",
            "HAPPY",
            "SAD",
            "ANGRY",
            "SURPRISED",
            "THINKING",
        ],
    )
    def test_valid_emotion_tags(self, tag: str) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()
        with patch("edge_client.actuators.vts_driver._trigger_vts_hotkey") as mocked_hotkey:
            result = driver.set_expression(tag)
        assert result.success
        assert result.expression_id == EMOTION_TO_EXPRESSION_ID[tag]
        mocked_hotkey.assert_called_once_with(
            vts=driver._vts,
            hotkey_id=EMOTION_TO_EXPRESSION_ID[tag],
            timeout_s=driver._config.api_timeout_s,
        )

    def test_case_insensitive_tag(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()
        with patch("edge_client.actuators.vts_driver._trigger_vts_hotkey"):
            result = driver.set_expression("happy")
        assert result.success
        assert result.expression_id == EMOTION_TO_EXPRESSION_ID["HAPPY"]

    def test_invalid_tag_rejected(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.set_expression("UNKNOWN_EMOTION")
        assert not result.success
        assert result.reason_code == AVATAR_TAG_INVALID

    def test_empty_tag_rejected(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.set_expression("")
        assert not result.success
        assert result.reason_code == AVATAR_TAG_INVALID

    def test_mapping_table_matches_host_vts(self) -> None:
        from host_brain.channels.vts import EMOTION_TO_EXPRESSION_ID as host_map

        assert EMOTION_TO_EXPRESSION_ID == host_map


class TestVtsBargeInRecovery:
    def test_reset_to_neutral(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()
        driver._talking = True
        driver._current_expression = EMOTION_TO_EXPRESSION_ID["HAPPY"]

        with patch("edge_client.actuators.vts_driver._trigger_vts_hotkey") as mocked_hotkey:
            with patch("edge_client.actuators.vts_driver._set_vts_parameter") as mocked_param:
                result = driver.reset_to_neutral()

        assert result.success
        assert not driver.talking
        assert result.expression_id == EMOTION_TO_EXPRESSION_ID["NEUTRAL"]
        mocked_hotkey.assert_called_once()
        assert mocked_param.call_args_list == [
            call(
                vts=driver._vts,
                param_name=VTS_MOUTH_PARAM,
                value=VTS_MOUTH_CLOSE_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceVolumePlusMouthOpen",
                value=VTS_MOUTH_CLOSE_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceVolume",
                value=VTS_MOUTH_CLOSE_VALUE,
                timeout_s=driver._config.api_timeout_s,
            ),
            call(
                vts=driver._vts,
                param_name="VoiceSilence",
                value=1.0,
                timeout_s=driver._config.api_timeout_s,
            ),
        ]

    def test_reset_when_disconnected_queues_neutral(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        driver._talking = True
        result = driver.reset_to_neutral()
        assert result.success
        assert not driver.talking
        assert driver.current_expression == EMOTION_TO_EXPRESSION_ID["NEUTRAL"]


class TestVtsDisconnectedDegradation:
    def test_expression_queued_when_disconnected(self) -> None:
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.set_expression("HAPPY")
        assert result.success
        assert result.reason_code == VTS_CONNECTION_FAILED
        assert result.expression_id == EMOTION_TO_EXPRESSION_ID["HAPPY"]
        assert "queued" in result.detail


class TestVtsErrorCodeStability:
    def test_error_codes_are_stable_strings(self) -> None:
        assert VTS_CONNECTION_FAILED == "VTS_CONNECTION_FAILED"
        assert VTS_API_ERROR == "VTS_API_ERROR"
        assert VTS_EXPRESSION_TIMEOUT == "VTS_EXPRESSION_TIMEOUT"


class TestVtsConfigContract:
    def test_default_config_values(self) -> None:
        cfg = VtsConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 8001
        assert cfg.plugin_name == "NatsumeAi"

    def test_as_dict_completeness(self) -> None:
        cfg = VtsConfig()
        row = cfg.as_dict()
        expected_keys = {
            "host",
            "port",
            "plugin_name",
            "plugin_developer",
            "token_path",
            "api_timeout_s",
            "reconnect",
        }
        assert expected_keys.issubset(set(row.keys()))


class TestVtsDriverResultContract:
    def test_success_result_as_dict(self) -> None:
        result = VtsDriverResult(
            success=True,
            reason_code=None,
            detail="ok",
            action="set_expression",
            expression_id=EMOTION_TO_EXPRESSION_ID["HAPPY"],
            emotion_tag="HAPPY",
        )
        row = result.as_dict()
        assert row["success"] is True
        assert row["expression_id"] == EMOTION_TO_EXPRESSION_ID["HAPPY"]

    def test_failure_result_as_dict(self) -> None:
        result = VtsDriverResult(
            success=False,
            reason_code=VTS_CONNECTION_FAILED,
            detail="not connected",
            action="connect",
        )
        row = result.as_dict()
        assert row["success"] is False
        assert row["reason_code"] == VTS_CONNECTION_FAILED


class TestVtsFixtureScenarios:
    def test_vts_disconnected_fixture(self) -> None:
        fixture = _load_fixture("bad/vts_hw_disconnected.json")
        assert fixture["scenario"] == "vts_disconnected"
        assert fixture["expected"]["reason_code"] == "VTS_CONNECTION_FAILED"
        assert fixture["expected"]["expression_id"] == EMOTION_TO_EXPRESSION_ID["HAPPY"]

    def test_barge_in_recovery_fixture(self) -> None:
        fixture = _load_fixture("good/tts_hw_barge_in_recovery.json")
        assert fixture["scenario"] == "barge_in_recovery"
        assert fixture["expected"]["vts_reset_to_neutral"] is True
        assert fixture["expected"]["mouth_closed"] is True


class TestVtsHostCompatibility:
    def test_emotion_map_complete_coverage(self) -> None:
        from host_brain.channels.vts import EMOTION_TO_EXPRESSION_ID as host_map

        for tag in host_map:
            assert tag in EMOTION_TO_EXPRESSION_ID

    def test_talk_gate_actions_match_host(self) -> None:
        from host_brain.channels.vts import TALK_END as host_talk_end
        from host_brain.channels.vts import TALK_START as host_talk_start

        assert TALK_START == host_talk_start
        assert TALK_END == host_talk_end
