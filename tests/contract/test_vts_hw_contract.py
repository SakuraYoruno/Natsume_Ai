"""T-VTS-HW-01: Edge VTube Studio hardware integration contract tests.

Tests the VTube Studio driver layer including:
- Driver lifecycle (connect/disconnect/health)
- Talk gate (TALK_START/TALK_END)
- Emotion tag → expression_id deterministic mapping
- Invalid tag rejection
- Barge-in recovery (reset to NEUTRAL)
- VTS disconnected degradation
- Error code stability (VTS_CONNECTION_FAILED, VTS_API_ERROR, VTS_EXPRESSION_TIMEOUT)
- Fixture-driven scenarios
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("NATSUME_AUDIT_DB", ":memory:")

from host_brain.core.errors import (
    AVATAR_FREE_ACTION_BLOCKED,
    AVATAR_TAG_INVALID,
    VTS_API_ERROR,
    VTS_CONNECTION_FAILED,
    VTS_EXPRESSION_TIMEOUT,
)
from edge_client.actuators.vts_driver import (
    EMOTION_TO_EXPRESSION_ID,
    TALK_END,
    TALK_START,
    VtsConfig,
    VtsDriverResult,
    VtsState,
    VTubeStudioDriver,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "voice"


def _load_fixture(subpath: str) -> dict:
    fpath = FIXTURES_DIR / subpath
    with open(fpath, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# T-VTS-HW-01.1: Driver Lifecycle
# ---------------------------------------------------------------------------

class TestVtsDriverLifecycle:
    """Verify VTubeStudioDriver state transitions."""

    def test_initial_state_is_disconnected(self):
        driver = VTubeStudioDriver(VtsConfig())
        assert driver.state == VtsState.DISCONNECTED

    def test_connect_without_pyvts_returns_connection_failed(self):
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.connect()
        assert not result.success
        assert result.reason_code == VTS_CONNECTION_FAILED
        assert driver.state == VtsState.ERROR

    def test_disconnect_resets_state(self):
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._talking = True
        driver._current_expression = "exp_happy_01"
        result = driver.disconnect()
        assert result.success
        assert driver.state == VtsState.DISCONNECTED
        assert not driver.talking
        assert driver.current_expression is None

    def test_health_snapshot_structure(self):
        driver = VTubeStudioDriver(VtsConfig(host="127.0.0.1", port=9090))
        health = driver.health()
        assert health["state"] == VtsState.DISCONNECTED.value
        assert "talking" in health
        assert "current_expression" in health
        assert "config" in health
        assert "connection_error" in health
        assert "emotion_map_size" in health
        assert health["emotion_map_size"] == len(EMOTION_TO_EXPRESSION_ID)


# ---------------------------------------------------------------------------
# T-VTS-HW-01.2: Talk Gate (TALK_START / TALK_END)
# ---------------------------------------------------------------------------

class TestVtsTalkGate:
    """Verify talk gate transitions."""

    def test_talk_start_when_disconnected_triggers_connect(self):
        """talk_start when disconnected attempts connect first."""
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.talk_start()
        # Should fail because pyvts not installed
        assert not result.success
        assert result.reason_code == VTS_CONNECTION_FAILED

    def test_talk_end_when_disconnected_is_noop(self):
        """talk_end when disconnected is a graceful no-op."""
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.talk_end()
        assert result.success
        assert not driver.talking

    def test_talk_start_sets_talking_flag(self):
        """When authenticated, talk_start sets talking = True."""
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()

        with patch("edge_client.actuators.vts_driver._set_vts_parameter"):
            result = driver.talk_start()
        assert result.success
        assert driver.talking

    def test_talk_end_clears_talking_flag(self):
        """talk_end clears talking flag."""
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()
        driver._talking = True

        with patch("edge_client.actuators.vts_driver._set_vts_parameter"):
            result = driver.talk_end()
        assert result.success
        assert not driver.talking


# ---------------------------------------------------------------------------
# T-VTS-HW-01.3: Emotion → Expression Deterministic Mapping
# ---------------------------------------------------------------------------

class TestVtsExpressionMapping:
    """Verify deterministic emotion_tag → expression_id mapping."""

    @pytest.mark.parametrize("tag,expected_id", [
        ("NEUTRAL", "exp_neutral_01"),
        ("HAPPY", "exp_happy_01"),
        ("SAD", "exp_sad_01"),
        ("ANGRY", "exp_angry_01"),
        ("SURPRISED", "exp_surprised_01"),
        ("THINKING", "exp_thinking_01"),
    ])
    def test_valid_emotion_tags(self, tag: str, expected_id: str):
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()

        with patch("edge_client.actuators.vts_driver._trigger_vts_hotkey"):
            result = driver.set_expression(tag)
        assert result.success
        assert result.expression_id == expected_id
        assert result.emotion_tag == tag

    def test_case_insensitive_tag(self):
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()

        with patch("edge_client.actuators.vts_driver._trigger_vts_hotkey"):
            result = driver.set_expression("happy")
        assert result.success
        assert result.expression_id == "exp_happy_01"

    def test_invalid_tag_rejected(self):
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.set_expression("UNKNOWN_EMOTION")
        assert not result.success
        assert result.reason_code == AVATAR_TAG_INVALID

    def test_empty_tag_rejected(self):
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.set_expression("")
        assert not result.success
        assert result.reason_code == AVATAR_TAG_INVALID

    def test_mapping_table_matches_host_vts(self):
        """Ensure edge EMOTION_TO_EXPRESSION_ID matches host_brain/channels/vts.py."""
        from host_brain.channels.vts import (
            EMOTION_TO_EXPRESSION_ID as HOST_MAP,
        )
        assert EMOTION_TO_EXPRESSION_ID == HOST_MAP


# ---------------------------------------------------------------------------
# T-VTS-HW-01.4: Barge-in Recovery
# ---------------------------------------------------------------------------

class TestVtsBargeInRecovery:
    """Verify barge-in resets expression to NEUTRAL and closes mouth."""

    def test_reset_to_neutral(self):
        driver = VTubeStudioDriver(VtsConfig())
        driver._state = VtsState.AUTHENTICATED
        driver._vts = MagicMock()
        driver._talking = True
        driver._current_expression = "exp_happy_01"

        with patch("edge_client.actuators.vts_driver._trigger_vts_hotkey"):
            with patch("edge_client.actuators.vts_driver._set_vts_parameter"):
                result = driver.reset_to_neutral()

        assert result.success
        assert not driver.talking
        assert driver.current_expression == "exp_neutral_01"

    def test_reset_when_disconnected_queues_neutral(self):
        driver = VTubeStudioDriver(VtsConfig())
        driver._talking = True
        result = driver.reset_to_neutral()
        assert result.success
        assert result.reason_code == VTS_CONNECTION_FAILED  # 降级
        assert not driver.talking
        assert driver.current_expression == "exp_neutral_01"


# ---------------------------------------------------------------------------
# T-VTS-HW-01.5: VTS Disconnected Degradation
# ---------------------------------------------------------------------------

class TestVtsDisconnectedDegradation:
    """Verify graceful degradation when VTS is not connected."""

    def test_expression_queued_when_disconnected(self):
        """Expression should be remembered but not sent when VTS is down."""
        driver = VTubeStudioDriver(VtsConfig())
        result = driver.set_expression("HAPPY")
        # Should succeed (degraded) with VTS_CONNECTION_FAILED reason
        assert result.success
        assert result.reason_code == VTS_CONNECTION_FAILED
        assert result.expression_id == "exp_happy_01"
        assert "queued" in result.detail


# ---------------------------------------------------------------------------
# T-VTS-HW-01.6: Error Code Stability
# ---------------------------------------------------------------------------

class TestVtsErrorCodeStability:
    """Verify all VTS error codes are stable string constants."""

    def test_error_codes_are_stable_strings(self):
        assert VTS_CONNECTION_FAILED == "VTS_CONNECTION_FAILED"
        assert VTS_API_ERROR == "VTS_API_ERROR"
        assert VTS_EXPRESSION_TIMEOUT == "VTS_EXPRESSION_TIMEOUT"


# ---------------------------------------------------------------------------
# T-VTS-HW-01.7: VtsConfig Structure
# ---------------------------------------------------------------------------

class TestVtsConfig:
    """Verify VtsConfig defaults and serialization."""

    def test_default_config_values(self):
        cfg = VtsConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 8001
        assert cfg.plugin_name == "NatsumeAi"

    def test_as_dict_completeness(self):
        cfg = VtsConfig()
        d = cfg.as_dict()
        expected_keys = {
            "host", "port", "plugin_name", "plugin_developer",
            "token_path", "api_timeout_s",
        }
        assert expected_keys.issubset(set(d.keys()))


# ---------------------------------------------------------------------------
# T-VTS-HW-01.8: VtsDriverResult Structure
# ---------------------------------------------------------------------------

class TestVtsDriverResult:
    """Verify VtsDriverResult serialization."""

    def test_success_result_as_dict(self):
        result = VtsDriverResult(
            success=True,
            reason_code=None,
            detail="ok",
            action="set_expression",
            expression_id="exp_happy_01",
            emotion_tag="HAPPY",
        )
        d = result.as_dict()
        assert d["success"] is True
        assert d["expression_id"] == "exp_happy_01"

    def test_failure_result_as_dict(self):
        result = VtsDriverResult(
            success=False,
            reason_code=VTS_CONNECTION_FAILED,
            detail="not connected",
            action="connect",
        )
        d = result.as_dict()
        assert d["success"] is False
        assert d["reason_code"] == VTS_CONNECTION_FAILED


# ---------------------------------------------------------------------------
# T-VTS-HW-01.9: Fixture-Driven Scenarios
# ---------------------------------------------------------------------------

class TestVtsFixtureScenarios:
    """Validate all VTS fixture scenarios."""

    def test_vts_disconnected_fixture(self):
        fixture = _load_fixture("bad/vts_hw_disconnected.json")
        assert fixture["scenario"] == "vts_disconnected"
        assert fixture["expected"]["reason_code"] == "VTS_CONNECTION_FAILED"
        assert fixture["expected"]["expression_id"] == "exp_happy_01"

    def test_barge_in_recovery_fixture(self):
        fixture = _load_fixture("good/tts_hw_barge_in_recovery.json")
        assert fixture["scenario"] == "barge_in_recovery"
        assert fixture["expected"]["vts_reset_to_neutral"] is True
        assert fixture["expected"]["mouth_closed"] is True


# ---------------------------------------------------------------------------
# T-VTS-HW-01.10: Compatibility with Host VTS Contract (T-Avatar-01)
# ---------------------------------------------------------------------------

class TestVtsHostCompatibility:
    """Verify edge VTS driver is compatible with host_brain VTS contract."""

    def test_emotion_map_complete_coverage(self):
        """All host-side emotion tags are supported in edge driver."""
        from host_brain.channels.vts import EMOTION_TO_EXPRESSION_ID as HOST_MAP
        for tag in HOST_MAP:
            assert tag in EMOTION_TO_EXPRESSION_ID, f"Missing tag: {tag}"

    def test_talk_gate_actions_match_host(self):
        """TALK_START/TALK_END constants match host-side definitions."""
        from host_brain.channels.vts import TALK_START as HOST_TALK_START
        from host_brain.channels.vts import TALK_END as HOST_TALK_END
        assert TALK_START == HOST_TALK_START
        assert TALK_END == HOST_TALK_END
