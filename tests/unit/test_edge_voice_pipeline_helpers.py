from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from edge_client.actuators.speech import (
    EVENT_TTS_PLAYBACK_FINISHED,
    EVENT_TTS_PLAYBACK_STARTED,
    EVENT_TTS_QUEUED,
    S_VOICE_TTS_QUEUE,
    SpeechPlaybackQueue,
    SpeechQueueResult,
)
from edge_client.actuators.vts_driver import VtsDriverResult, VtsState
from edge_client.rpc.event_reporter import BatchReportResult, EdgeEventReporter
from edge_client.rpc.voice import EVENT_ASR_CHUNK, STATUS_ACCEPTED, VoiceRpcContract, VoiceRpcResult
from edge_client.runtime.voice_pipeline import (
    EdgeVoicePipeline,
    _body_action_config,
    _life_motion_config,
    _speech_motion_config,
)
from host_brain.core.errors import VOICE_PLAYBACK_FAILED


def _success_batch(total: int) -> BatchReportResult:
    return BatchReportResult(
        success=True,
        total=total,
        accepted=total,
        rejected=0,
    )


class _MockVtsDriver:
    def __init__(self) -> None:
        self.state = VtsState.AUTHENTICATED
        self.transition_calls: list[tuple[str, list[dict[str, str]]]] = []
        self.expression_calls: list[tuple[str, float]] = []
        self.hotkey_calls: list[str] = []
        self.talk_start_calls = 0
        self.reset_calls = 0
        self.fail_talk_start = False
        self.connect_result = VtsDriverResult(
            success=True,
            reason_code=None,
            detail="connected",
            action="CONNECT",
        )
        self._mouth_parameter_plan = {"selected": ["ParamMouthOpenY"]}
        self.mouth_values: list[float] = []
        self.mouth_include_companion_flags: list[bool] = []
        self.mouth_batch_companion_flags: list[bool] = []
        self.mouth_motion_values: list[dict[str, float]] = []
        self.model_move_calls: list[dict[str, float | bool]] = []
        self.fail_probe = False
        self.reconnect_calls = 0

    def connect(self) -> VtsDriverResult:
        return self.connect_result

    def trigger_reconnect(self) -> VtsDriverResult:
        self.reconnect_calls += 1
        self.state = VtsState.RECONNECTING
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="reconnect started",
            action="trigger_reconnect",
        )

    def reset_to_neutral(self) -> VtsDriverResult:
        self.reset_calls += 1
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="reset",
            action="RESET",
            expression_id="reset_all",
            emotion_tag="NEUTRAL",
        )

    def transition_expression(
        self,
        emotion_tag: str,
        *,
        transition_frames: list[dict[str, str]],
    ) -> VtsDriverResult:
        self.transition_calls.append((emotion_tag, list(transition_frames)))
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="transition",
            action="TRANSITION",
        )

    def set_expression(self, emotion_tag: str, *, intensity: float = 0.5) -> VtsDriverResult:
        self.expression_calls.append((emotion_tag, intensity))
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="set_expression",
            action="SET_EXPRESSION",
        )

    def trigger_hotkey(self, hotkey_id: str) -> VtsDriverResult:
        self.hotkey_calls.append(hotkey_id)
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="trigger_hotkey",
            action="TRIGGER_HOTKEY",
        )

    def talk_start(self) -> VtsDriverResult:
        self.talk_start_calls += 1
        if self.fail_talk_start:
            return VtsDriverResult(
                success=False,
                reason_code="VTS_API_ERROR",
                detail="talk start failed",
                action="TALK_START",
            )
        return VtsDriverResult(success=True, reason_code=None, detail="talk_start", action="TALK_START")

    def health(self) -> dict[str, object]:
        return {"mouth_parameters": {"ParamMouthOpenY": 0.0}}

    def set_mouth_open_level(
        self,
        value: float,
        *,
        _retry_on_reconnect: bool = True,
        _include_companions: bool = True,
        _batch_companions: bool = False,
        _motion_values: dict[str, float] | None = None,
    ) -> VtsDriverResult:
        _ = _retry_on_reconnect
        self.mouth_values.append(float(value))
        self.mouth_include_companion_flags.append(bool(_include_companions))
        self.mouth_batch_companion_flags.append(bool(_batch_companions))
        self.mouth_motion_values.append(dict(_motion_values or {}))
        if self.fail_probe and len(self.mouth_values) == 1:
            return VtsDriverResult(
                success=False,
                reason_code="MOUTH_BLOCKED",
                detail="probe failed",
                action="SET_MOUTH_OPEN_LEVEL",
            )
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="mouth level set",
            action="SET_MOUTH_OPEN_LEVEL",
        )

    def move_model(
        self,
        *,
        position_x: float = 0.0,
        position_y: float = 0.0,
        rotation: float = 0.0,
        size: float = 1.0,
        relative: bool = True,
        move_time_s: float = 0.12,
        _retry_on_reconnect: bool = True,
    ) -> VtsDriverResult:
        _ = _retry_on_reconnect
        self.model_move_calls.append(
            {
                "position_x": float(position_x),
                "position_y": float(position_y),
                "rotation": float(rotation),
                "size": float(size),
                "relative": bool(relative),
                "move_time_s": float(move_time_s),
            }
        )
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="model moved",
            action="MOVE_MODEL",
        )


class _ImmediateThread:
    def __init__(self, *, target, name: str, daemon: bool) -> None:
        self._target = target
        self.name = name
        self.daemon = daemon
        self.join_called = False
        self._alive = False

    def start(self) -> None:
        self._alive = True
        self._target()
        self._alive = False

    def join(self, timeout: float | None = None) -> None:
        self.join_called = True
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


class _AliveThread:
    def __init__(self) -> None:
        self.join_called = False

    def join(self, timeout: float | None = None) -> None:
        self.join_called = True

    def is_alive(self) -> bool:
        return True


class _StubTribeBridge:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def publish_event(self, **kwargs):  # noqa: ANN003
        self.events.append(dict(kwargs))
        return {"ok": True}


class TestEdgeVoicePipelineHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.rpc = VoiceRpcContract(asr_min_interval_ms=0)
        self.queue = SpeechPlaybackQueue(
            rpc=self.rpc,
            synthesizer=lambda _: {"audio_ref": "mem://audio"},
            playback_starter=lambda _s, _a: None,
        )
        self.reporter = EdgeEventReporter(
            transport_fn=lambda events: _success_batch(len(events)),
            max_retry=1,
        )

    def _build_pipeline(
        self,
        *,
        vts_driver: _MockVtsDriver | None = None,
        tribe_bridge: _StubTribeBridge | None = None,
    ) -> EdgeVoicePipeline:
        return EdgeVoicePipeline(
            voice_rpc=self.rpc,
            speech_queue=self.queue,
            event_reporter=self.reporter,
            vts_driver=vts_driver,
            tribe_bridge=tribe_bridge,
        )

    def test_playback_completion_observer_receives_exact_terminal_once(self) -> None:
        pipeline = self._build_pipeline()
        observed: list[SpeechQueueResult] = []
        unsubscribe = pipeline.register_playback_completion_observer(observed.append)
        queued = pipeline.enqueue_speak(
            request_id="embodiment-command-1",
            payload={
                "session_id": "voice-session",
                "utterance_id": "utterance-1",
                "text": "hello",
            },
            auto_start=True,
            background_start=False,
        )
        self.assertTrue(queued.success)

        terminal = pipeline.complete_playback(ts_ms=2_000)
        self.assertIsNotNone(terminal)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].event["request_id"], "embodiment-command-1")
        self.assertEqual(observed[0].event["event_type"], EVENT_TTS_PLAYBACK_FINISHED)

        unsubscribe()
        self.assertIsNone(pipeline.complete_playback(ts_ms=2_001))
        self.assertEqual(len(observed), 1)

    def test_on_asr_rpc_result_flushes_and_report_helpers_guard_missing_session(self) -> None:
        pipeline = self._build_pipeline()
        rpc_result = VoiceRpcResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_VOICE_ASR_INGRESS",
            detail="ok",
            event={"event_type": EVENT_ASR_CHUNK, "seq": 1},
        )
        self.assertIsNone(
            pipeline._report_asr_rpc_result(
                request_id="req-asr-missing-session",
                rpc_result=rpc_result,
            )
        )

        flush_mock = MagicMock()
        pipeline.event_reporter.flush_cache = flush_mock
        with patch.object(
            pipeline,
            "_report_asr_rpc_result",
            return_value=_success_batch(1),
        ) as report_mock:
            pipeline.on_asr_rpc_result("req-asr-flush", rpc_result)

        report_mock.assert_called_once()
        flush_mock.assert_called_once()

    def test_waiting_for_active_playback_and_queue_report_paths(self) -> None:
        pipeline = self._build_pipeline()
        waiting = SpeechQueueResult(
            success=False,
            status="REJECTED",
            reason_code=VOICE_PLAYBACK_FAILED,
            stage=S_VOICE_TTS_QUEUE,
            detail="cannot start while playback is active",
            event={},
        )
        self.assertTrue(pipeline._is_waiting_for_active_playback(waiting))
        self.assertFalse(
            pipeline._is_waiting_for_active_playback(
                SpeechQueueResult(
                    success=False,
                    status="REJECTED",
                    reason_code="OTHER_FAILURE",
                    stage=S_VOICE_TTS_QUEUE,
                    detail="cannot start while playback is active",
                    event={},
                )
            )
        )

        self.assertIsNone(
            pipeline._report_voice_queue_result(
                request_id="req-no-session",
                result=SpeechQueueResult(
                    success=True,
                    status=STATUS_ACCEPTED,
                    reason_code=None,
                    stage=S_VOICE_TTS_QUEUE,
                    detail="queued",
                    event={"event_type": EVENT_TTS_QUEUED},
                ),
            )
        )

        flush_mock = MagicMock()
        pipeline.event_reporter.flush_cache = flush_mock
        report = pipeline._report_voice_queue_result(
            request_id="req-report",
            result=SpeechQueueResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                stage=S_VOICE_TTS_QUEUE,
                detail="queued",
                event={
                    "event_type": EVENT_TTS_QUEUED,
                    "request_id": "req-report",
                    "session_id": "sess-report",
                    "ts_ms": 1234,
                },
            ),
        )
        self.assertIsNotNone(report)
        assert report is not None
        self.assertTrue(report.success)
        flush_mock.assert_called_once()

    def test_background_speech_start_skips_failed_segment_and_drains_next(self) -> None:
        synth_calls: list[str] = []

        def synthesize(event):  # noqa: ANN001
            text = str(event.get("text") or "")
            synth_calls.append(text)
            if text == "first fails":
                raise RuntimeError("synth timeout")
            return {"audio_ref": "mem://second"}

        queue = SpeechPlaybackQueue(
            rpc=self.rpc,
            synthesizer=synthesize,
            playback_starter=lambda _s, _a: None,
        )
        pipeline = EdgeVoicePipeline(
            voice_rpc=self.rpc,
            speech_queue=queue,
            event_reporter=self.reporter,
        )
        for request_id, text in (
            ("req-first", "first fails"),
            ("req-second", "second starts"),
        ):
            queued = pipeline.enqueue_speak(
                request_id=request_id,
                payload={
                    "session_id": "sess-drain",
                    "utterance_id": f"utt-{request_id}",
                    "text": text,
                },
                auto_start=False,
                background_start=False,
            )
            self.assertTrue(queued.success)

        pipeline._run_background_speech_start(request_id="req-first")

        self.assertEqual(synth_calls, ["first fails", "second starts"])
        self.assertEqual(queue.queue_depth, 0)
        self.assertEqual(queue.active_playback["request_id"], "req-second")

    def test_enqueue_speak_uses_manual_vts_hook_when_queue_start_skips_hook(self) -> None:
        pipeline = self._build_pipeline(vts_driver=_MockVtsDriver())
        pipeline.speech_queue.enqueue_speak = MagicMock(
            return_value=SpeechQueueResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                stage=S_VOICE_TTS_QUEUE,
                detail="queued",
                event={
                    "event_type": EVENT_TTS_QUEUED,
                    "request_id": "req-manual-hook",
                    "session_id": "sess-manual-hook",
                    "ts_ms": 1234,
                },
            )
        )
        pipeline.speech_queue.start_next = MagicMock(
            return_value=SpeechQueueResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                stage=S_VOICE_TTS_QUEUE,
                detail="started",
                event={
                    "event_type": EVENT_TTS_PLAYBACK_STARTED,
                    "request_id": "req-manual-hook",
                    "session_id": "sess-manual-hook",
                    "ts_ms": 1235,
                },
            )
        )
        pipeline._report_voice_queue_result = MagicMock(return_value=_success_batch(1))

        def _manual_hook(*, speak_event, synth_event) -> None:
            self.assertEqual("hello voice", speak_event["text"])
            self.assertEqual({}, synth_event)
            pipeline._pending_vts_result = {
                "expression_result": "expr",
                "talk_result": "talk",
                "mouth_sync_debug": {"state": "manual"},
            }

        pipeline._vts_before_playback_hook = _manual_hook

        result = pipeline.enqueue_speak(
            request_id="req-manual-hook",
            payload={
                "session_id": "sess-manual-hook",
                "utterance_id": "utt-manual-hook",
                "text": "hello voice",
            },
            ts_ms=1234,
            auto_start=True,
        )

        self.assertTrue(result.success)
        self.assertEqual("expr", result.expression_result)
        self.assertEqual("talk", result.talk_result)
        self.assertEqual({"state": "manual"}, result.mouth_sync_debug)
        self.assertEqual(2, pipeline._report_voice_queue_result.call_count)

    def test_vts_hook_transition_hotkey_and_missing_audio_reason(self) -> None:
        vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=vts)

        with patch(
            "edge_client.runtime.voice_pipeline.sub_emotion_to_hotkey",
            return_value="hotkey-smile",
        ):
            pipeline._vts_before_playback_hook(
                speak_event={
                    "emotion_tag": "happy",
                    "sub_emotion": "playful",
                    "transition_frames": [
                        {"emotion_tag": "neutral"},
                        {"emotion_tag": "happy"},
                    ],
                },
                synth_event={},
            )

        cached = pipeline._pending_vts_result
        self.assertEqual(1, len(vts.transition_calls))
        self.assertEqual("HAPPY", vts.transition_calls[0][0])
        self.assertEqual(["hotkey-smile"], vts.hotkey_calls)
        self.assertEqual(1, vts.talk_start_calls)
        self.assertEqual("missing_synth_event", cached["mouth_sync_debug"]["reason"])
        self.assertEqual("talk_started", cached["mouth_sync_debug"]["state"])

    def test_start_vts_for_payload_handles_connect_failure_and_invalid_intensity(self) -> None:
        blocked_vts = _MockVtsDriver()
        blocked_vts.state = VtsState.DISCONNECTED
        blocked_vts.connect_result = VtsDriverResult(
            success=False,
            reason_code="VTS_CONNECT_FAILED",
            detail="connect failed",
            action="CONNECT",
        )
        pipeline = self._build_pipeline(vts_driver=blocked_vts)
        expression_result, talk_result, debug = pipeline._start_vts_for_payload(
            {"emotion_tag": "sad"},
            synth_event=None,
        )
        self.assertFalse(expression_result.success)
        self.assertIsNone(talk_result)
        self.assertEqual("vts_connect_failed", debug["reason"])

        healthy_vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=healthy_vts)
        expression_result, talk_result, debug = pipeline._start_vts_for_payload(
            {
                "emotion_tag": "joy",
                "emotion_intensity": "bad-value",
            },
            synth_event={},
        )
        self.assertTrue(expression_result.success)
        self.assertTrue(talk_result.success)
        self.assertEqual(("JOY", 0.5), healthy_vts.expression_calls[0])
        self.assertEqual("missing_synth_event", debug["reason"])

    def test_start_vts_for_payload_can_disable_voice_expression_hotkeys(self) -> None:
        healthy_vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=healthy_vts)

        with patch.dict("os.environ", {"EDGE_VTS_VOICE_EXPRESSION_ENABLED": "false"}):
            expression_result, talk_result, debug = pipeline._start_vts_for_payload(
                {
                    "emotion_tag": "happy",
                    "emotion_intensity": 0.8,
                    "sub_emotion": "playful",
                },
                synth_event={},
            )

        self.assertTrue(expression_result.success)
        self.assertIn("voice expression disabled", expression_result.detail)
        self.assertEqual("HAPPY", expression_result.emotion_tag)
        self.assertTrue(talk_result.success)
        self.assertEqual(0, healthy_vts.reset_calls)
        self.assertEqual([], healthy_vts.expression_calls)
        self.assertEqual([], healthy_vts.hotkey_calls)
        self.assertEqual("missing_synth_event", debug["reason"])

    def test_start_vts_for_payload_without_driver_and_with_audio_path(self) -> None:
        no_vts_pipeline = self._build_pipeline(vts_driver=None)
        expression_result, talk_result, debug = no_vts_pipeline._start_vts_for_payload(
            {"emotion_tag": "sad"},
            synth_event={"audio_data": [0.2], "sample_rate": 24000},
        )
        self.assertIsNone(expression_result)
        self.assertIsNone(talk_result)
        self.assertEqual("vts_driver_unavailable", debug["reason"])

        healthy_vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=healthy_vts)
        with patch.object(
            pipeline,
            "_start_mouth_sync",
            return_value={"state": "running", "started": True},
        ) as mouth_sync_mock:
            expression_result, talk_result, debug = pipeline._start_vts_for_payload(
                {"emotion_tag": "happy", "emotion_intensity": 0.8},
                synth_event={"audio_data": [0.1, 0.4], "sample_rate": 24000},
            )

        self.assertTrue(expression_result.success)
        self.assertTrue(talk_result.success)
        self.assertEqual("running", debug["state"])
        mouth_sync_mock.assert_called_once()

    def test_start_vts_for_payload_still_syncs_mouth_when_talk_gate_fails(self) -> None:
        healthy_vts = _MockVtsDriver()
        healthy_vts.fail_talk_start = True
        pipeline = self._build_pipeline(vts_driver=healthy_vts)

        with patch.object(
            pipeline,
            "_start_mouth_sync",
            return_value={"state": "running", "started": True},
        ) as mouth_sync_mock:
            expression_result, talk_result, debug = pipeline._start_vts_for_payload(
                {"emotion_tag": "happy", "emotion_intensity": 0.8},
                synth_event={"audio_data": [0.1, 0.4], "sample_rate": 24000},
            )

        self.assertTrue(expression_result.success)
        self.assertFalse(talk_result.success)
        self.assertEqual("running", debug["state"])
        self.assertFalse(debug["talk_gate_success"])
        self.assertEqual("talk start failed", debug["talk_gate_detail"])
        mouth_sync_mock.assert_called_once()

    def test_synth_event_has_audio_variants(self) -> None:
        class _SizedAudio:
            def __init__(self, size: int) -> None:
                self.size = size

        class _ExplodingLen:
            def __len__(self) -> int:
                raise RuntimeError("len exploded")

        self.assertFalse(EdgeVoicePipeline._synth_event_has_audio({}))
        self.assertFalse(EdgeVoicePipeline._synth_event_has_audio({"audio_data": None}))
        self.assertFalse(EdgeVoicePipeline._synth_event_has_audio({"audio_data": _SizedAudio(0)}))
        self.assertTrue(EdgeVoicePipeline._synth_event_has_audio({"audio_data": _SizedAudio(4)}))
        self.assertFalse(EdgeVoicePipeline._synth_event_has_audio({"audio_data": b""}))
        self.assertTrue(EdgeVoicePipeline._synth_event_has_audio({"audio_data": b"x"}))
        self.assertTrue(EdgeVoicePipeline._synth_event_has_audio({"audio_data": _ExplodingLen()}))

    def test_start_and_stop_mouth_sync_cover_empty_probe_failure_and_success(self) -> None:
        pipeline = self._build_pipeline(vts_driver=None)
        debug = pipeline._start_mouth_sync({"audio_data": [0.1], "sample_rate": 24000})
        self.assertEqual("vts_driver_unavailable", debug["reason"])

        empty_vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=empty_vts)
        with patch.object(pipeline, "_build_mouth_envelope", return_value=[]):
            debug = pipeline._start_mouth_sync({"audio_data": [0.1], "sample_rate": 24000})
        self.assertEqual("empty_envelope", debug["reason"])

        blocked_vts = _MockVtsDriver()
        blocked_vts.fail_probe = True
        pipeline = self._build_pipeline(vts_driver=blocked_vts)
        with patch.object(pipeline, "_build_mouth_envelope", return_value=[0.3]):
            debug = pipeline._start_mouth_sync({"audio_data": [0.1], "sample_rate": 24000})
        self.assertEqual("mouth_param_unwritable", debug["reason"])

        success_vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=success_vts)
        with patch.object(pipeline, "_build_mouth_envelope", return_value=[0.2, 0.4]):
            with patch("edge_client.runtime.voice_pipeline.time.sleep", return_value=None):
                with patch(
                    "edge_client.runtime.voice_pipeline.threading.Thread",
                    side_effect=lambda **kwargs: _ImmediateThread(**kwargs),
                ):
                    debug = pipeline._start_mouth_sync(
                        {"audio_data": [0.1], "sample_rate": 24000}
                    )

        self.assertTrue(debug["started"])
        self.assertEqual("finished", debug["state"])
        self.assertEqual("finished", pipeline._mouth_sync_debug["state"])
        self.assertEqual(5, len(success_vts.mouth_values))
        self.assertEqual(0, pipeline._mouth_sync_debug["error_count"])
        self.assertEqual(
            [True, True, True, True, True],
            success_vts.mouth_include_companion_flags,
        )
        self.assertEqual(
            [True, True, True, True, True],
            success_vts.mouth_batch_companion_flags,
        )
        self.assertTrue(debug["per_frame_companions"])
        self.assertTrue(debug["per_frame_batch"])
        self.assertTrue(debug["motion_enabled"])
        self.assertGreater(pipeline._mouth_sync_debug["motion_parameter_count"], 20)
        self.assertEqual(2, pipeline._mouth_sync_debug["motion_frames_sent"])
        self.assertIn("FaceAngleY", success_vts.mouth_motion_values[2])
        self.assertIn("FaceAngleY", success_vts.mouth_motion_values[3])
        self.assertIn("MocopiBodyAngleY", success_vts.mouth_motion_values[2])
        self.assertIn("HandRightPositionY", success_vts.mouth_motion_values[2])
        self.assertIn("MocopiUpperArmRightAngleZ", success_vts.mouth_motion_values[2])
        self.assertIn("HandDistance", success_vts.mouth_motion_values[2])

        alive_thread = _AliveThread()
        pipeline._mouth_sync_thread = alive_thread
        pipeline._mouth_sync_debug = {"state": "running"}
        stopped = pipeline._stop_mouth_sync()
        self.assertTrue(alive_thread.join_called)
        self.assertFalse(stopped)
        self.assertEqual("stopping", pipeline._mouth_sync_debug["state"])
        self.assertTrue(pipeline._mouth_sync_debug["stop_timed_out"])

    def test_report_asr_rpc_result_ignores_non_asr_event(self) -> None:
        pipeline = self._build_pipeline()
        rpc_result = VoiceRpcResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_OTHER",
            detail="ignored",
            event={"event_type": "OTHER_EVENT", "session_id": "sess-other"},
        )
        self.assertIsNone(
            pipeline._report_asr_rpc_result(
                request_id="req-other-event",
                rpc_result=rpc_result,
            )
        )

    def test_build_mouth_envelope_variants(self) -> None:
        self.assertEqual([], EdgeVoicePipeline._build_mouth_envelope(None, 24000))
        self.assertEqual([], EdgeVoicePipeline._build_mouth_envelope([0.1, 0.2], 0))
        self.assertEqual([], EdgeVoicePipeline._build_mouth_envelope([0.0, 0.0], 24000))

        envelope = EdgeVoicePipeline._build_mouth_envelope([0.0, 0.5, 1.0], 24)
        self.assertEqual(3, len(envelope))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in envelope))
        self.assertGreater(envelope[-1], envelope[0])

        with patch.dict(
            "os.environ",
            {
                "EDGE_VTS_MOUTH_SYNC_FPS": "24",
                "EDGE_VTS_MOUTH_SYNC_GAIN": "1.0",
                "EDGE_VTS_MOUTH_SYNC_MIN_OPEN": "0.3",
                "EDGE_VTS_MOUTH_SYNC_SILENCE_THRESHOLD": "0.05",
                "EDGE_VTS_MOUTH_SYNC_ATTACK": "1.0",
                "EDGE_VTS_MOUTH_SYNC_RELEASE": "1.0",
            },
        ):
            tuned = EdgeVoicePipeline._build_mouth_envelope([0.0, 0.1, 1.0], 24)
        self.assertEqual([0.0, 0.3, 1.0], tuned)

    def test_speech_motion_values_can_reset_and_move(self) -> None:
        moving = EdgeVoicePipeline._speech_motion_values(
            frame_index=12,
            mouth_value=0.8,
            fps=24.0,
            config={
                "enabled": True,
                "gain": 1.0,
                "nod_degrees": 2.0,
                "sway_degrees": 2.0,
                "tilt_degrees": 1.0,
                "position_x": 0.02,
                "position_y": 0.02,
                "position_z": 0.01,
                "full_body_enabled": True,
                "mocopi_enabled": True,
                "hand_enabled": True,
                "arm_enabled": True,
                "action_script_enabled": True,
                "body_degrees": 6.0,
                "body_position": 0.06,
                "hand_position": 0.5,
                "hand_angle_degrees": 12.0,
                "arm_angle_degrees": 18.0,
                "action_complexity": 1.2,
                "gesture_gain": 1.0,
                "beat_gain": 1.0,
                "energy": 1.2,
                "tempo": 1.1,
                "frame_count": 48,
            },
        )
        self.assertIn("FaceAngleX", moving)
        self.assertNotEqual(0.0, moving["FaceAngleY"])
        self.assertIn("FacePositionX", moving)
        self.assertIn("FacePositionZ", moving)
        self.assertIn("MocopiBodyAngleY", moving)
        self.assertIn("HandRightPositionY", moving)
        self.assertIn("MocopiUpperArmRightAngleZ", moving)
        self.assertIn("HandDistance", moving)
        reset = EdgeVoicePipeline._speech_motion_values(
            frame_index=12,
            mouth_value=0.8,
            fps=24.0,
            config={"enabled": False, "full_body_enabled": True, "hand_enabled": True},
        )
        self.assertIn("MocopiLowerArmRightAngleZ", reset)
        self.assertIn("HandDistance", reset)
        self.assertTrue(all(value == 0.0 for value in reset.values()))

    def test_speech_motion_config_uses_emotion_energy(self) -> None:
        happy = _speech_motion_config({"emotion_tag": "HAPPY", "emotion_intensity": 1.0})
        sad = _speech_motion_config({"emotion_tag": "SAD", "emotion_intensity": 1.0})

        self.assertGreater(float(happy["energy"]), float(sad["energy"]))
        self.assertGreater(float(happy["tempo"]), float(sad["tempo"]))
        self.assertTrue(happy["full_body_enabled"])

    def test_face_capture_priority_disables_scripted_motion_overlays(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "EDGE_VTS_FACE_CAPTURE_PRIORITY": "true",
                "EDGE_VTS_SPEECH_MOTION_ENABLED": "true",
                "EDGE_VTS_LIFE_MOTION_ENABLED": "true",
            },
        ):
            speech = _speech_motion_config({"emotion_tag": "HAPPY", "emotion_intensity": 1.0})
            life = _life_motion_config()

        self.assertTrue(speech["face_capture_priority"])
        self.assertFalse(speech["enabled"])
        self.assertTrue(life["face_capture_priority"])
        self.assertFalse(life["enabled"])

    def test_mouth_sync_skips_motion_values_when_face_capture_has_priority(self) -> None:
        vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=vts)

        with patch.dict("os.environ", {"EDGE_VTS_FACE_CAPTURE_PRIORITY": "true"}):
            with patch.object(pipeline, "_build_mouth_envelope", return_value=[0.2, 0.4]):
                with patch("edge_client.runtime.voice_pipeline.time.sleep", return_value=None):
                    with patch(
                        "edge_client.runtime.voice_pipeline.threading.Thread",
                        side_effect=lambda **kwargs: _ImmediateThread(**kwargs),
                    ):
                        debug = pipeline._start_mouth_sync(
                            {"audio_data": [0.1], "sample_rate": 24000}
                        )

        self.assertTrue(debug["started"])
        self.assertFalse(debug["motion_enabled"])
        self.assertEqual(0, debug["motion_parameter_count"])
        self.assertEqual(0, pipeline._mouth_sync_debug["motion_frames_sent"])
        self.assertTrue(all(not item for item in vts.mouth_motion_values))

    def test_life_motion_values_use_bound_face_parameters_by_default(self) -> None:
        config = _life_motion_config()
        values, phase = EdgeVoicePipeline._life_motion_values(
            frame_index=8,
            elapsed_s=6.0,
            config=config,
            emotion_tag="PLAYFUL",
        )

        self.assertTrue(phase)
        self.assertIn("FaceAngleY", values)
        self.assertIn("MouthSmile", values)
        self.assertNotIn("MocopiBodyAngleY", values)
        self.assertNotIn("HandRightPositionY", values)
        self.assertGreater(abs(float(values["FaceAngleY"])), 1.0)

    def test_body_action_values_use_bound_face_parameters_by_default(self) -> None:
        config = _body_action_config(
            {"action_id": "wave", "emotion_tag": "PLAYFUL", "emotion_intensity": 0.9, "duration_s": 1.0}
        )
        values, phase = EdgeVoicePipeline._body_action_values(
            action_id="wave",
            frame_index=6,
            progress=0.5,
            elapsed_s=0.5,
            config=config,
            emotion_tag="PLAYFUL",
        )

        self.assertTrue(phase)
        self.assertIn("FaceAngleY", values)
        self.assertNotIn("MocopiBodyAngleY", values)
        self.assertNotIn("MocopiUpperArmRightAngleY", values)
        self.assertNotIn("HandRightPositionY", values)
        self.assertIn("MouthSmile", values)

    def test_perform_body_action_does_not_require_speech(self) -> None:
        vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=vts)

        with patch.dict(
            "os.environ",
            {
                "EDGE_VTS_BODY_ACTION_FPS": "4",
                "EDGE_VTS_BODY_ACTION_GAIN": "0.8",
                "EDGE_VTS_BODY_ACTION_MODEL_MOVE_ENABLED": "false",
            },
        ):
            result = pipeline.perform_body_action(
                action_id="wave",
                emotion_tag="PLAYFUL",
                intensity=0.8,
                duration_s=1.0,
            )

        self.assertTrue(result["success"])
        self.assertFalse(result["speech_required"])
        self.assertEqual("wave", result["action_id"])
        self.assertEqual(0, vts.talk_start_calls)
        self.assertGreaterEqual(len(vts.mouth_values), 2)
        self.assertTrue(all(value == 0.0 for value in vts.mouth_values))
        self.assertTrue(all(vts.mouth_batch_companion_flags))
        self.assertTrue(any("FaceAngleY" in item for item in vts.mouth_motion_values))
        self.assertTrue(any("MouthSmile" in item for item in vts.mouth_motion_values))
        self.assertEqual([], vts.model_move_calls)
        health = pipeline.health()
        self.assertFalse(health["body_action_active"])
        self.assertEqual("wave", health["last_body_action"]["action_id"])
        self.assertEqual(0, health["last_body_action"]["model_move_frames_sent"])

    def test_perform_body_action_rejects_unknown_action(self) -> None:
        vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=vts)

        result = pipeline.perform_body_action(action_id="dance_forever")

        self.assertFalse(result["success"])
        self.assertEqual("unknown_action_id", result["reason"])
        self.assertEqual([], vts.mouth_values)

    def test_model_move_helpers_generate_visible_offsets(self) -> None:
        life = EdgeVoicePipeline._life_model_move(
            elapsed_s=2.0,
            config={
                "gain": 1.0,
                "model_move_position": 0.06,
                "model_move_rotation": 3.0,
            },
            emotion_tag="PLAYFUL",
        )
        action = EdgeVoicePipeline._body_action_model_move(
            action_id="look_around",
            progress=0.3,
            elapsed_s=0.6,
            config={
                "gain": 1.0,
                "model_move_position": 0.16,
                "model_move_rotation": 8.0,
            },
        )

        self.assertNotEqual(0.0, life["x"])
        self.assertNotEqual(0.0, life["rotation"])
        self.assertNotEqual(0.0, action["x"])
        self.assertNotEqual(0.0, action["rotation"])

    def test_life_motion_start_stop_and_health(self) -> None:
        vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=vts)

        with patch.dict("os.environ", {"EDGE_VTS_LIFE_MOTION_FPS": "12"}):
            started = pipeline.start_life_motion()
            health = pipeline.health()
            stopped = pipeline.stop_life_motion()

        self.assertTrue(started["started"])
        self.assertTrue(health["life_motion_thread_alive"])
        self.assertIn(health["life_motion"]["state"], {"running", "starting", "waiting_for_vts"})
        self.assertEqual("stopped", stopped["state"])
        self.assertFalse(pipeline.health()["life_motion_thread_alive"])

    def test_life_motion_refreshes_vts_state_after_recovery(self) -> None:
        class _OneFrameEvent:
            def __init__(self) -> None:
                self.stopped = False

            def is_set(self) -> bool:
                return self.stopped

            def wait(self, _timeout: float) -> bool:
                self.stopped = True
                return True

        vts = _MockVtsDriver()
        pipeline = self._build_pipeline(vts_driver=vts)
        pipeline._life_motion_debug["vts_state"] = "CONNECTING"
        config = _life_motion_config()
        config["model_move_enabled"] = False

        pipeline._life_motion_loop(config, _OneFrameEvent())

        self.assertEqual(
            "AUTHENTICATED",
            pipeline.health()["life_motion"]["vts_state"],
        )

    def test_life_motion_requests_bounded_reconnect_when_vts_starts_late(self) -> None:
        class _OneWaitEvent:
            def __init__(self) -> None:
                self.stopped = False

            def is_set(self) -> bool:
                return self.stopped

            def wait(self, _timeout: float) -> bool:
                self.stopped = True
                return True

        vts = _MockVtsDriver()
        vts.state = VtsState.ERROR
        pipeline = self._build_pipeline(vts_driver=vts)

        pipeline._life_motion_loop(_life_motion_config(), _OneWaitEvent())

        health = pipeline.health()["life_motion"]
        self.assertEqual(1, vts.reconnect_calls)
        self.assertEqual("stopped", health["state"])
        self.assertEqual("RECONNECTING", health["vts_state"])
        self.assertTrue(health["reconnect_requested"])

    def test_coerce_audio_samples_covers_numpy_and_fallback_paths(self) -> None:
        class _FakeArray:
            def __init__(self, values):
                self._values = values
                if values and isinstance(values[0], list):
                    self.ndim = 2
                    self.size = sum(len(row) for row in values)
                else:
                    self.ndim = 1
                    self.size = len(values)

            def tolist(self):
                return list(self._values)

        class _FakeNumpy:
            @staticmethod
            def asarray(audio_data, dtype=float):
                del dtype
                return _FakeArray(audio_data)

            @staticmethod
            def abs(arr):
                values = arr._values
                if arr.ndim > 1:
                    return _FakeArray([[abs(item) for item in row] for row in values])
                return _FakeArray([abs(item) for item in values])

            @staticmethod
            def mean(arr, axis=1):
                self.assertEqual(1, axis)
                return _FakeArray([sum(row) / len(row) for row in arr._values])

        with patch.dict("sys.modules", {"numpy": _FakeNumpy()}):
            samples = EdgeVoicePipeline._coerce_audio_samples([[-1.0, 1.0], [2.0, -2.0]])
        self.assertEqual([1.0, 2.0], samples)

        class _BrokenToList:
            def tolist(self):
                raise RuntimeError("tolist exploded")

        self.assertEqual([], EdgeVoicePipeline._coerce_audio_samples(_BrokenToList()))
        self.assertEqual([], EdgeVoicePipeline._coerce_audio_samples(b"\x00\x01"))
        self.assertEqual([2.5], EdgeVoicePipeline._coerce_audio_samples(-2.5))
        self.assertEqual(
            [1.0, 2.0],
            EdgeVoicePipeline._coerce_audio_samples([1.0, (1.0, 3.0), "skip"]),
        )

    def test_health_and_resolve_asr_emotion_report_helpers(self) -> None:
        pipeline = self._build_pipeline(vts_driver=_MockVtsDriver())
        pipeline._mouth_sync_debug = {"state": "running", "started": True}
        pipeline._mouth_sync_thread = _AliveThread()
        health = pipeline.health()
        self.assertTrue(health["mouth_sync_thread_alive"])
        self.assertEqual("running", health["mouth_sync"]["state"])

        explicit = pipeline._resolve_asr_emotion_report(
            {
                "emotion_tag": "happy",
                "emotion_intensity": 2.0,
                "mood_state": "joyful",
            }
        )
        self.assertEqual("HAPPY", explicit["emotion_tag"])
        self.assertEqual("edge_explicit", explicit["emotion_source"])
        self.assertEqual("explicit", explicit["emotion_modality"])
        self.assertEqual(1.0, explicit["emotion_confidence"])
        self.assertFalse(explicit["emotion_inference_degraded"])
        self.assertEqual(1.0, explicit["emotion_intensity"])
        self.assertEqual("joyful", explicit["mood_state"])

        explicit_default = pipeline._resolve_asr_emotion_report(
            {"emotion_tag": "sad", "emotion_intensity": "bad-value"}
        )
        self.assertEqual(0.5, explicit_default["emotion_intensity"])

        with patch(
            "edge_client.runtime.voice_pipeline.infer_asr_emotion",
            return_value={"emotion_tag": "CALM", "emotion_intensity": 0.3, "emotion_source": "inferred"},
        ) as infer_mock:
            inferred = pipeline._resolve_asr_emotion_report(
                {"text": "慢慢说", "confidence": 0.8, "final": True}
            )
        infer_mock.assert_called_once_with(text="慢慢说", confidence=0.8, final=True)
        self.assertEqual("CALM", inferred["emotion_tag"])

    def test_voice_queue_reports_publish_tribe_voice_pipeline_events(self) -> None:
        bridge = _StubTribeBridge()
        pipeline = self._build_pipeline(tribe_bridge=bridge)

        pipeline._report_voice_queue_result(
            request_id="req-voice-start",
            result=SpeechQueueResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                stage=S_VOICE_TTS_QUEUE,
                detail="playback started",
                event={
                    "event_type": EVENT_TTS_PLAYBACK_STARTED,
                    "request_id": "req-voice-start",
                    "session_id": "sess-voice",
                    "ts_ms": 1000,
                },
            ),
        )

        pipeline._report_voice_queue_result(
            request_id="req-voice-end",
            result=SpeechQueueResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=None,
                stage=S_VOICE_TTS_QUEUE,
                detail="playback finished",
                event={
                    "event_type": EVENT_TTS_PLAYBACK_FINISHED,
                    "request_id": "req-voice-end",
                    "session_id": "sess-voice",
                    "ts_ms": 1001,
                },
            ),
        )

        event_types = [str(item.get("event_type")) for item in bridge.events]
        self.assertIn("voice_pipeline_started", event_types)
        self.assertIn("voice_pipeline_ended", event_types)

    def test_asr_report_publishes_emotion_and_body_sense_events(self) -> None:
        bridge = _StubTribeBridge()
        pipeline = self._build_pipeline(tribe_bridge=bridge)
        rpc_result = VoiceRpcResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage="S_VOICE_ASR_INGRESS",
            detail="ok",
            event={
                "event_type": EVENT_ASR_CHUNK,
                "request_id": "req-asr-tribe",
                "session_id": "sess-asr-tribe",
                "ts_ms": 2000,
                "seq": 2,
                "text": "我有点开心",
                "confidence": 0.9,
                "final": True,
            },
        )

        report = pipeline._report_asr_rpc_result(
            request_id="req-asr-tribe",
            rpc_result=rpc_result,
        )

        self.assertIsNotNone(report)
        event_types = [str(item.get("event_type")) for item in bridge.events]
        self.assertIn("body_sense_updated", event_types)
        self.assertIn("emotion_inference_updated", event_types)
        emotion_event = next(
            item for item in bridge.events if str(item.get("event_type")) == "emotion_inference_updated"
        )
        emotion_payload = dict(emotion_event.get("payload") or {})
        self.assertEqual("asr_text_bootstrap", emotion_payload["emotion_source"])
        self.assertEqual("asr_text", emotion_payload["emotion_modality"])
        self.assertFalse(emotion_payload["acoustic_emotion_available"])
        self.assertIn("emotion_confidence", emotion_payload)


if __name__ == "__main__":
    unittest.main()
