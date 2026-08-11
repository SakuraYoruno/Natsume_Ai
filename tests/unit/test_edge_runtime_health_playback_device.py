from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from edge_client.actuators.tts_engine import AudioPlaybackDevice
from edge_client.runtime.health import build_edge_health_snapshot, collect_health


class _FakeSoundDevice:
    class default:
        device = (1, 5)

    @staticmethod
    def query_devices(index: int):
        return {
            "name": f"device-{index}",
            "hostapi": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        }


def test_audio_playback_device_health_reports_resolved_output_device() -> None:
    device = AudioPlaybackDevice(sample_rate=32000, device_index=7)

    with patch.dict("sys.modules", {"sounddevice": _FakeSoundDevice}), patch.dict(
        "os.environ",
        {"EDGE_TTS_HEALTH_PROBE_DEVICES": "1"},
        clear=False,
    ):
        health = device.health()

    assert health["configured_device_index"] == 7
    assert health["sounddevice_available"] is True
    assert health["resolved_output_device"]["index"] == 7
    assert health["resolved_output_device"]["name"] == "device-7"
    assert health["default_output_device"]["index"] == 5
    assert health["last_playback_error"] is None


def test_audio_playback_device_health_skips_device_probe_by_default() -> None:
    device = AudioPlaybackDevice(sample_rate=32000, device_index=7)

    health = device.health()

    assert health["configured_device_index"] == 7
    assert health["device_probe_skipped"] is True
    assert health["resolved_output_device"] is None


def test_audio_playback_device_limits_audio_before_playback() -> None:
    import numpy as np

    captured: dict[str, object] = {}

    class _PlayableSoundDevice:
        @staticmethod
        def play(audio_data, *, samplerate, device, blocking):  # type: ignore[no-untyped-def]
            captured["audio_data"] = np.asarray(audio_data)
            captured["samplerate"] = samplerate
            captured["device"] = device
            captured["blocking"] = blocking

        @staticmethod
        def wait() -> None:
            captured["waited"] = True

    device = AudioPlaybackDevice(sample_rate=32000, device_index=7)
    audio = np.asarray([2.0, -2.0, 0.5], dtype=np.float32)

    with patch.dict("sys.modules", {"sounddevice": _PlayableSoundDevice}), patch.dict(
        "os.environ",
        {
            "EDGE_TTS_PLAYBACK_TARGET_PEAK": "0.5",
            "EDGE_TTS_PLAYBACK_GAIN": "0.5",
        },
        clear=False,
    ):
        result = device.play(audio, blocking=True)

    assert result.success
    prepared = captured["audio_data"]
    assert float(np.max(np.abs(prepared))) <= 0.26
    health = device.health()
    assert health["last_audio_stats"]["prepared"] is True
    assert health["last_audio_stats"]["peak_before"] == 2.0
    assert health["last_audio_stats"]["peak_after"] <= 0.26


def test_collect_health_includes_tts_playback_device_component() -> None:
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            asr_enabled=False,
            tts_enabled=True,
            fallback_llm_enabled=False,
            resource_gate_enabled=False,
            max_vram_mb=4096,
        ),
        speech_queue=SimpleNamespace(queue_depth=0),
        tts=SimpleNamespace(health=lambda: {"status": "ok"}),
        tts_playback_device=SimpleNamespace(
            health=lambda: {
                "configured_device_index": 3,
                "resolved_output_device": {"index": 3, "name": "Speakers"},
                "last_playback_error": None,
            }
        ),
        asr=None,
        fallback_rpc=None,
        vts=SimpleNamespace(health=lambda: {"state": "AUTHENTICATED"}),
        voice_pipeline=SimpleNamespace(
            health=lambda: {
                "mouth_sync": {"state": "finished", "started": True},
                "last_vts_result": {
                    "request_id": "req_voice_001",
                    "talk_result": {"success": True},
                },
            }
        ),
        event_reporter=SimpleNamespace(health=lambda: {"status": "ok"}),
        resource_gate=None,
    )

    snapshot = collect_health(runtime)

    assert snapshot["components"]["tts_playback_device"]["available"] is True
    health = snapshot["components"]["tts_playback_device"]["health"]
    assert health["configured_device_index"] == 3
    assert health["resolved_output_device"]["name"] == "Speakers"
    assert snapshot["components"]["voice_pipeline"]["health"]["last_vts_result"]["request_id"] == "req_voice_001"
    assert "tts_playback_device" in snapshot["collection_timings_ms"]


def test_collect_health_reports_authenticated_vts_without_expression_hotkeys_as_degraded() -> None:
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            asr_enabled=False,
            tts_enabled=False,
            fallback_llm_enabled=False,
            resource_gate_enabled=False,
            max_vram_mb=4096,
        ),
        speech_queue=SimpleNamespace(queue_depth=0),
        asr=None,
        tts=None,
        tts_playback_device=None,
        fallback_rpc=None,
        vts=SimpleNamespace(
            health=lambda: {
                "status": "degraded",
                "state": "AUTHENTICATED",
                "detail": (
                    "VTS authenticated; mouth/body motion available; "
                    "compatible expression hotkeys unavailable"
                ),
                "hotkey_catalog_size": 3,
                "expression_hotkey_match_count": 0,
                "expression_hotkey_ready": False,
            }
        ),
        voice_pipeline=SimpleNamespace(health=lambda: {"status": "ok"}),
        event_reporter=SimpleNamespace(health=lambda: {"status": "ok"}),
        resource_gate=None,
    )

    snapshot = collect_health(runtime)

    assert snapshot["status"] == "degraded"
    assert snapshot["components"]["vts"]["status"] == "degraded"
    assert "expression hotkeys unavailable" in snapshot["components"]["vts"]["detail"]


def test_build_edge_health_snapshot_reuses_collected_payload() -> None:
    calls = {"count": 0}

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            asr_enabled=False,
            tts_enabled=False,
            fallback_llm_enabled=False,
            resource_gate_enabled=False,
            max_vram_mb=4096,
            as_dict=lambda: {"env": "test"},
            hmac_shared_key_ref="env:test",
            hmac_shared_key="secret",
        ),
        speech_queue=SimpleNamespace(queue_depth=0),
        asr=None,
        tts=None,
        tts_playback_device=None,
        fallback_rpc=None,
        vts=None,
        voice_pipeline=None,
        event_reporter=None,
        resource_gate=None,
        service_statuses=lambda: {"edge.rpc.tts": {"available": False}},
        redacted_config_snapshot=lambda: {"env": "test", "hmac_shared_key_ref": "env:test", "hmac_shared_key_set": True},
    )

    def _collected() -> dict:
        calls["count"] += 1
        return collect_health(runtime, ts_ms=1_800_000_000_000)

    collected = _collected()
    snapshot = build_edge_health_snapshot(runtime, ts_ms=1_800_000_000_000, collected=collected)

    assert calls["count"] == 1
    assert snapshot["components"] == collected["components"]
    assert snapshot["collection_timings_ms"] == collected["collection_timings_ms"]
