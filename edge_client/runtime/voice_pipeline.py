"""Edge voice pipeline orchestration (ASR upstream + playback linkage)."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import os
import threading
import time
from typing import Any, Callable

from edge_client.actuators.speech import (
    EVENT_TTS_QUEUED,
    EVENT_TTS_PLAYBACK_FINISHED,
    EVENT_TTS_PLAYBACK_INTERRUPTED,
    EVENT_TTS_PLAYBACK_STARTED,
    EVENT_TTS_SYNTH_FAILED,
    SpeechPlaybackQueue,
    SpeechQueueResult,
    BeforePlaybackHook,
)
from edge_client.actuators.vts_driver import VTubeStudioDriver, VtsDriverResult, VtsState
from edge_client.actuators.vts_expression_map import sub_emotion_to_hotkey
from edge_client.rpc.event_reporter import BatchReportResult, EdgeEventReporter
from edge_client.rpc.resource_gate import VoiceResourceGate
from edge_client.rpc.voice import EVENT_ASR_CHUNK, VoiceRpcContract, VoiceRpcResult
from edge_client.runtime.emotion_inference import infer_asr_emotion
from edge_client.tribe.somatic_agent import SomaticAgent
from edge_client.tribe.voice_agent import VoiceAgent
from natsume_contracts.edge.v1 import VOICE_INTERRUPTED, VOICE_PLAYBACK_FAILED, now_ms

LOGGER = logging.getLogger("Natsume.edge.voice_pipeline")


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _vts_reset_to_neutral_enabled() -> bool:
    """Whether automatic VTS hotkey reset is allowed.

    Keep this disabled by default because reset_to_neutral can trigger the
    user's VTS hotkey state, and in the desktop setup Ctrl+0 toggles watermark.
    """
    return _parse_bool_env("EDGE_VTS_RESET_TO_NEUTRAL_ENABLED", default=False)


def _vts_reset_to_neutral_skipped_result() -> VtsDriverResult:
    return VtsDriverResult(
        success=True,
        reason_code=None,
        detail="VTS reset_to_neutral disabled; hotkey reset skipped",
        action="reset_to_neutral",
        expression_id="NEUTRAL",
        emotion_tag="NEUTRAL",
    )


def _parse_float_env(name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    try:
        value = float(str(raw).strip()) if raw is not None and str(raw).strip() else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _parse_float_value(raw: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(raw).strip()) if raw is not None and str(raw).strip() else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    x = _clamp01((float(value) - float(edge0)) / (float(edge1) - float(edge0)))
    return x * x * (3.0 - 2.0 * x)


def _motion_window(progress: float, start: float, end: float, *, softness: float = 0.08) -> float:
    if end <= start:
        return 0.0
    width = end - start
    edge = min(max(float(softness), 0.01), width * 0.5)
    return _smoothstep(start, start + edge, progress) * (1.0 - _smoothstep(end - edge, end, progress))


def _motion_pulse(progress: float, center: float, width: float) -> float:
    half = max(0.01, float(width) * 0.5)
    return _motion_window(progress, center - half, center + half, softness=half)


def _mouth_envelope_config() -> dict[str, float]:
    fps = _parse_float_env("EDGE_VTS_MOUTH_SYNC_FPS", default=24.0, minimum=12.0, maximum=48.0)
    gain = _parse_float_env("EDGE_VTS_MOUTH_SYNC_GAIN", default=4.0, minimum=0.5, maximum=8.0)
    min_open = _parse_float_env("EDGE_VTS_MOUTH_SYNC_MIN_OPEN", default=0.18, minimum=0.0, maximum=0.6)
    silence_threshold = _parse_float_env(
        "EDGE_VTS_MOUTH_SYNC_SILENCE_THRESHOLD",
        default=0.025,
        minimum=0.0,
        maximum=0.25,
    )
    attack = _parse_float_env("EDGE_VTS_MOUTH_SYNC_ATTACK", default=0.78, minimum=0.05, maximum=1.0)
    release = _parse_float_env("EDGE_VTS_MOUTH_SYNC_RELEASE", default=0.42, minimum=0.02, maximum=1.0)
    return {
        "fps": fps,
        "gain": gain,
        "min_open": min_open,
        "silence_threshold": silence_threshold,
        "attack": attack,
        "release": release,
    }


def _speech_motion_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    face_capture_priority = _parse_bool_env("EDGE_VTS_FACE_CAPTURE_PRIORITY", default=False)
    motion_requested = _parse_bool_env("EDGE_VTS_SPEECH_MOTION_ENABLED", default=True)
    body_only_when_face_priority = _parse_bool_env(
        "EDGE_VTS_SPEECH_FACE_PRIORITY_BODY_ONLY_ENABLED",
        default=True,
    )
    enabled = motion_requested and not face_capture_priority
    face_motion_enabled = _parse_bool_env(
        "EDGE_VTS_SPEECH_FACE_MOTION_ENABLED",
        default=True,
    ) and not face_capture_priority
    body_only = bool(face_capture_priority and body_only_when_face_priority)
    gain = _parse_float_env("EDGE_VTS_SPEECH_MOTION_GAIN", default=1.4, minimum=0.0, maximum=3.0)
    nod_degrees = _parse_float_env("EDGE_VTS_SPEECH_MOTION_NOD_DEGREES", default=2.4, minimum=0.0, maximum=8.0)
    sway_degrees = _parse_float_env("EDGE_VTS_SPEECH_MOTION_SWAY_DEGREES", default=3.2, minimum=0.0, maximum=10.0)
    tilt_degrees = _parse_float_env("EDGE_VTS_SPEECH_MOTION_TILT_DEGREES", default=1.8, minimum=0.0, maximum=8.0)
    position_x = _parse_float_env("EDGE_VTS_SPEECH_MOTION_POSITION_X", default=0.025, minimum=0.0, maximum=0.2)
    position_y = _parse_float_env("EDGE_VTS_SPEECH_MOTION_POSITION_Y", default=0.035, minimum=0.0, maximum=0.2)
    position_z = _parse_float_env("EDGE_VTS_SPEECH_MOTION_POSITION_Z", default=0.02, minimum=0.0, maximum=0.2)
    full_body_enabled = _parse_bool_env("EDGE_VTS_SPEECH_FULL_BODY_ENABLED", default=True)
    mocopi_enabled = _parse_bool_env("EDGE_VTS_SPEECH_MOCOPI_BODY_ENABLED", default=True)
    hand_enabled = _parse_bool_env("EDGE_VTS_SPEECH_HAND_GESTURE_ENABLED", default=True)
    arm_enabled = _parse_bool_env("EDGE_VTS_SPEECH_ARM_GESTURE_ENABLED", default=True)
    action_script_enabled = _parse_bool_env("EDGE_VTS_SPEECH_ACTION_SCRIPT_ENABLED", default=True)
    body_degrees = _parse_float_env("EDGE_VTS_SPEECH_BODY_DEGREES", default=7.0, minimum=0.0, maximum=24.0)
    body_position = _parse_float_env("EDGE_VTS_SPEECH_BODY_POSITION", default=0.08, minimum=0.0, maximum=0.35)
    hand_position = _parse_float_env("EDGE_VTS_SPEECH_HAND_POSITION", default=0.52, minimum=0.0, maximum=1.5)
    hand_angle_degrees = _parse_float_env("EDGE_VTS_SPEECH_HAND_ANGLE_DEGREES", default=18.0, minimum=0.0, maximum=60.0)
    arm_angle_degrees = _parse_float_env("EDGE_VTS_SPEECH_ARM_ANGLE_DEGREES", default=22.0, minimum=0.0, maximum=75.0)
    action_complexity = _parse_float_env("EDGE_VTS_SPEECH_ACTION_COMPLEXITY", default=1.0, minimum=0.0, maximum=2.0)
    gesture_gain = _parse_float_env("EDGE_VTS_SPEECH_GESTURE_GAIN", default=1.0, minimum=0.0, maximum=3.0)
    beat_gain = _parse_float_env("EDGE_VTS_SPEECH_BEAT_GAIN", default=1.0, minimum=0.0, maximum=3.0)
    motion_every_n_frames = int(
        _parse_float_env("EDGE_VTS_SPEECH_MOTION_EVERY_N_FRAMES", default=1.0, minimum=1.0, maximum=6.0)
    )

    payload = dict(payload or {})
    emotion_tag = str(payload.get("emotion_tag") or "").strip().upper()
    raw_intensity = payload.get("emotion_intensity")
    try:
        intensity = max(0.0, min(1.0, float(raw_intensity)))
    except (TypeError, ValueError):
        intensity = 0.5
    energetic_tags = {"HAPPY", "JOY", "PLAYFUL", "EXCITED", "EXCITEMENT", "LOVE", "LOVING", "AFFECTION"}
    restrained_tags = {"SAD", "SADNESS", "CALM", "SERIOUS", "SOOTHING", "NOSTALGIC"}
    tense_tags = {"ANXIOUS", "ANGRY", "EMBARRASSED"}
    if emotion_tag in energetic_tags:
        energy = 1.05 + intensity * 0.45
        tempo = 1.08 + intensity * 0.22
    elif emotion_tag in restrained_tags:
        energy = 0.58 + intensity * 0.28
        tempo = 0.72 + intensity * 0.12
    elif emotion_tag in tense_tags:
        energy = 0.85 + intensity * 0.35
        tempo = 1.0 + intensity * 0.28
    else:
        energy = 0.82 + intensity * 0.26
        tempo = 0.9 + intensity * 0.14
    return {
        "enabled": enabled,
        "face_capture_priority": face_capture_priority,
        "body_only": body_only,
        "face_motion_enabled": face_motion_enabled,
        "body_only_when_face_priority": body_only_when_face_priority,
        "gain": gain,
        "energy": min(1.65, energy),
        "tempo": min(1.45, tempo),
        "nod_degrees": nod_degrees,
        "sway_degrees": sway_degrees,
        "tilt_degrees": tilt_degrees,
        "position_x": position_x,
        "position_y": position_y,
        "position_z": position_z,
        "full_body_enabled": full_body_enabled,
        "mocopi_enabled": mocopi_enabled,
        "hand_enabled": hand_enabled,
        "arm_enabled": arm_enabled,
        "action_script_enabled": action_script_enabled,
        "body_degrees": body_degrees,
        "body_position": body_position,
        "hand_position": hand_position,
        "hand_angle_degrees": hand_angle_degrees,
        "arm_angle_degrees": arm_angle_degrees,
        "action_complexity": action_complexity,
        "gesture_gain": gesture_gain,
        "beat_gain": beat_gain,
        "motion_every_n_frames": motion_every_n_frames,
    }


def _life_motion_config() -> dict[str, Any]:
    face_capture_priority = _parse_bool_env("EDGE_VTS_FACE_CAPTURE_PRIORITY", default=False)
    return {
        "enabled": _parse_bool_env("EDGE_VTS_LIFE_MOTION_ENABLED", default=True) and not face_capture_priority,
        "face_capture_priority": face_capture_priority,
        "fps": _parse_float_env("EDGE_VTS_LIFE_MOTION_FPS", default=4.0, minimum=1.0, maximum=12.0),
        "gain": _parse_float_env("EDGE_VTS_LIFE_MOTION_GAIN", default=0.75, minimum=0.0, maximum=2.5),
        "cycle_s": _parse_float_env("EDGE_VTS_LIFE_MOTION_CYCLE_S", default=32.0, minimum=8.0, maximum=120.0),
        "body_enabled": _parse_bool_env("EDGE_VTS_LIFE_MOTION_BODY_ENABLED", default=True),
        "hand_enabled": _parse_bool_env("EDGE_VTS_LIFE_MOTION_HAND_ENABLED", default=True),
        "expression_enabled": _parse_bool_env("EDGE_VTS_LIFE_MOTION_EXPRESSION_ENABLED", default=True),
        "body_degrees": _parse_float_env("EDGE_VTS_LIFE_MOTION_BODY_DEGREES", default=4.0, minimum=0.0, maximum=16.0),
        "body_position": _parse_float_env("EDGE_VTS_LIFE_MOTION_BODY_POSITION", default=0.045, minimum=0.0, maximum=0.25),
        "hand_position": _parse_float_env("EDGE_VTS_LIFE_MOTION_HAND_POSITION", default=0.36, minimum=0.0, maximum=1.2),
        "expression_gain": _parse_float_env("EDGE_VTS_LIFE_MOTION_EXPRESSION_GAIN", default=0.65, minimum=0.0, maximum=2.0),
        "face_angle_gain": _parse_float_env("EDGE_VTS_LIFE_FACE_ANGLE_GAIN", default=3.8, minimum=0.2, maximum=12.0),
        "bound_parameter_only": _parse_bool_env("EDGE_VTS_LIFE_BOUND_PARAMETER_ONLY", default=True),
        "model_move_enabled": _parse_bool_env("EDGE_VTS_LIFE_MODEL_MOVE_ENABLED", default=True),
        "model_move_position": _parse_float_env("EDGE_VTS_LIFE_MODEL_MOVE_POSITION", default=0.055, minimum=0.0, maximum=0.25),
        "model_move_rotation": _parse_float_env("EDGE_VTS_LIFE_MODEL_MOVE_ROTATION", default=2.8, minimum=0.0, maximum=12.0),
    }


_BODY_ACTION_ALIASES = {
    "attention": "lean_in",
    "bounce": "happy_bounce",
    "hello": "wave",
    "look": "look_around",
    "look_left": "look_around",
    "look_right": "look_around",
    "shift": "idle_shift",
    "shy_turn": "shy",
    "small_wave": "wave",
    "wave_hand": "wave",
}
_BODY_ACTION_IDS = {
    "emphasis",
    "happy_bounce",
    "idle_shift",
    "lean_in",
    "look_around",
    "nod",
    "settle",
    "shy",
    "wave",
}
_BODY_ACTION_DEFAULT_DURATION_S = {
    "emphasis": 1.6,
    "happy_bounce": 2.4,
    "idle_shift": 2.2,
    "lean_in": 2.0,
    "look_around": 2.4,
    "nod": 1.5,
    "settle": 1.2,
    "shy": 2.2,
    "wave": 2.2,
}


def _normalize_body_action_id(action_id: Any) -> str:
    raw = str(action_id or "").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = _BODY_ACTION_ALIASES.get(raw, raw)
    return normalized if normalized in _BODY_ACTION_IDS else ""


def _body_action_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    action_id = _normalize_body_action_id(payload.get("action_id"))
    default_duration = _BODY_ACTION_DEFAULT_DURATION_S.get(action_id, 2.0)
    raw_intensity = payload.get("emotion_intensity")
    if raw_intensity is None:
        raw_intensity = payload.get("intensity")
    raw_duration = payload.get("duration_s")
    if raw_duration is None:
        raw_duration = payload.get("duration")
    return {
        "enabled": _parse_bool_env("EDGE_VTS_BODY_ACTION_ENABLED", default=True),
        "fps": _parse_float_env("EDGE_VTS_BODY_ACTION_FPS", default=12.0, minimum=4.0, maximum=30.0),
        "gain": _parse_float_env("EDGE_VTS_BODY_ACTION_GAIN", default=1.0, minimum=0.0, maximum=3.0),
        "duration_s": _parse_float_value(raw_duration, default=default_duration, minimum=0.4, maximum=8.0),
        "intensity": _parse_float_value(raw_intensity, default=0.65, minimum=0.0, maximum=1.0),
        "body_enabled": _parse_bool_env("EDGE_VTS_BODY_ACTION_BODY_ENABLED", default=True),
        "hand_enabled": _parse_bool_env("EDGE_VTS_BODY_ACTION_HAND_ENABLED", default=True),
        "expression_enabled": _parse_bool_env("EDGE_VTS_BODY_ACTION_EXPRESSION_ENABLED", default=True),
        "body_degrees": _parse_float_env("EDGE_VTS_BODY_ACTION_BODY_DEGREES", default=8.0, minimum=0.0, maximum=28.0),
        "body_position": _parse_float_env("EDGE_VTS_BODY_ACTION_BODY_POSITION", default=0.09, minimum=0.0, maximum=0.35),
        "hand_position": _parse_float_env("EDGE_VTS_BODY_ACTION_HAND_POSITION", default=0.56, minimum=0.0, maximum=1.5),
        "hand_angle_degrees": _parse_float_env("EDGE_VTS_BODY_ACTION_HAND_ANGLE_DEGREES", default=24.0, minimum=0.0, maximum=80.0),
        "expression_gain": _parse_float_env("EDGE_VTS_BODY_ACTION_EXPRESSION_GAIN", default=0.85, minimum=0.0, maximum=2.0),
        "bound_parameter_only": _parse_bool_env("EDGE_VTS_BODY_ACTION_BOUND_PARAMETER_ONLY", default=True),
        "model_move_enabled": _parse_bool_env("EDGE_VTS_BODY_ACTION_MODEL_MOVE_ENABLED", default=True),
        "model_move_position": _parse_float_env("EDGE_VTS_BODY_ACTION_MODEL_MOVE_POSITION", default=0.14, minimum=0.0, maximum=0.5),
        "model_move_rotation": _parse_float_env("EDGE_VTS_BODY_ACTION_MODEL_MOVE_ROTATION", default=7.0, minimum=0.0, maximum=24.0),
    }


@dataclass(frozen=True)
class EdgeAsrUpstreamResult:
    success: bool
    rpc_result: VoiceRpcResult
    report_result: BatchReportResult | None
    flush_result: BatchReportResult | None


@dataclass(frozen=True)
class EdgePlaybackResult:
    success: bool
    queue_result: SpeechQueueResult
    start_result: SpeechQueueResult | None
    expression_result: VtsDriverResult | None
    talk_result: VtsDriverResult | None
    mouth_sync_debug: dict[str, Any] | None
    background_start_scheduled: bool = False


@dataclass(frozen=True)
class EdgeBargeInResult:
    success: bool
    barge_result: SpeechQueueResult
    vts_result: VtsDriverResult | None


class EdgeVoicePipeline:
    """Orchestrates ASR upstream reporting, TTS queue flow, and VTS linkage."""

    def __init__(
        self,
        *,
        voice_rpc: VoiceRpcContract,
        speech_queue: SpeechPlaybackQueue,
        event_reporter: EdgeEventReporter,
        vts_driver: VTubeStudioDriver | None = None,
        resource_gate: VoiceResourceGate | None = None,
        tribe_bridge: Any | None = None,
    ) -> None:
        self.voice_rpc = voice_rpc
        self.speech_queue = speech_queue
        self.event_reporter = event_reporter
        self.vts_driver = vts_driver
        self.resource_gate = resource_gate
        self.tribe_bridge = tribe_bridge
        self._somatic_agent = SomaticAgent(bridge=tribe_bridge)
        self._voice_agent = VoiceAgent(bridge=tribe_bridge)
        self._mouth_sync_stop = threading.Event()
        self._mouth_sync_thread: threading.Thread | None = None
        self._mouth_sync_debug: dict[str, Any] = {
            "state": "idle",
            "started": False,
            "reason": "not_started",
        }
        self._life_motion_stop = threading.Event()
        self._life_motion_thread: threading.Thread | None = None
        self._life_motion_debug: dict[str, Any] = {
            "state": "idle",
            "started": False,
            "reason": "not_started",
            "frames_sent": 0,
            "error_count": 0,
        }
        self._life_motion_emotion_tag = "NEUTRAL"
        self._life_motion_model_offset = {"x": 0.0, "y": 0.0, "rotation": 0.0}
        self._body_action_lock = threading.Lock()
        self._body_action_active = threading.Event()
        self._last_body_action: dict[str, Any] = {
            "state": "idle",
            "success": None,
            "reason": "not_started",
        }
        self._last_vts_result: dict[str, Any] = {
            "request_id": None,
            "emotion_tag": None,
            "expression_result": None,
            "talk_result": None,
            "mouth_sync": dict(self._mouth_sync_debug),
        }
        self._speech_background_lock = threading.Lock()
        self._speech_background_thread: threading.Thread | None = None
        self._playback_completion_lock = threading.Lock()
        self._playback_completion_observers: set[Callable[[SpeechQueueResult], Any]] = set()
        # 将 VTS 驱动注册为播放前钩子，确保表情/口型在音频播放前就绪
        self.speech_queue._before_playback_hook = self._vts_before_playback_hook

    def register_playback_completion_observer(
        self,
        observer: Callable[[SpeechQueueResult], Any],
    ) -> Callable[[], None]:
        """Observe physical playback terminals without owning the audio queue."""

        # 观察者只消费播放终态，不能取得队列控制权；返回注销函数使运行时重启或
        # 组件卸载时能解除引用，避免同一物理结果被旧订阅者重复处理。
        if not callable(observer):
            raise TypeError("playback completion observer must be callable")
        with self._playback_completion_lock:
            self._playback_completion_observers.add(observer)

        def unsubscribe() -> None:
            with self._playback_completion_lock:
                self._playback_completion_observers.discard(observer)

        return unsubscribe

    def _publish_playback_completion(self, result: SpeechQueueResult | None) -> None:
        if result is None:
            return
        event_type = str(result.event.get("event_type") or "").strip().upper()
        is_terminal = event_type in {
            EVENT_TTS_PLAYBACK_FINISHED,
            EVENT_TTS_PLAYBACK_INTERRUPTED,
        } or (
            not result.success
            and event_type in {EVENT_TTS_SYNTH_FAILED, EVENT_TTS_PLAYBACK_STARTED}
        )
        # 排队和开始播放都不是最终设备结果；仅向旁路发布完成、打断或明确失败，
        # 让下游不把“已接收”误当作已播完。
        if not is_terminal:
            return
        with self._playback_completion_lock:
            observers = tuple(self._playback_completion_observers)
        # 观察者可能写审计、驱动表情或通知 Host。单个旁路失败必须隔离，不能
        # 回滚已经发生的音频副作用，也不能阻止其他观察者收敛状态。
        for observer in observers:
            try:
                observer(result)
            except Exception as exc:  # physical audio completion must remain isolated
                LOGGER.warning("playback completion observer failed: %s", exc)

    def startup(self) -> None:
        self.start_life_motion()

    def shutdown(self) -> None:
        self.stop_life_motion()

    def start_life_motion(self) -> dict[str, Any]:
        config = _life_motion_config()
        if not bool(config["enabled"]):
            self._life_motion_debug = {
                "state": "disabled",
                "started": False,
                "reason": "disabled_by_config",
                "config": dict(config),
                "frames_sent": 0,
                "error_count": 0,
            }
            return dict(self._life_motion_debug)
        if self.vts_driver is None:
            self._life_motion_debug = {
                "state": "skipped",
                "started": False,
                "reason": "vts_driver_unavailable",
                "config": dict(config),
                "frames_sent": 0,
                "error_count": 0,
            }
            return dict(self._life_motion_debug)
        thread = self._life_motion_thread
        if thread is not None and thread.is_alive():
            return dict(self._life_motion_debug)
        self._life_motion_stop = threading.Event()
        self._life_motion_debug = {
            "state": "starting",
            "started": True,
            "config": dict(config),
            "frames_sent": 0,
            "error_count": 0,
            "expression_frames_sent": 0,
            "paused_for_speech": False,
            "last_phase": "startup",
        }
        self._life_motion_thread = threading.Thread(
            target=self._life_motion_loop,
            args=(config, self._life_motion_stop),
            name="edge-vts-life-motion",
            daemon=True,
        )
        self._life_motion_thread.start()
        return dict(self._life_motion_debug)

    def stop_life_motion(self) -> dict[str, Any]:
        self._life_motion_stop.set()
        thread = self._life_motion_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if thread is not None and thread.is_alive():
            self._life_motion_debug = {
                **self._life_motion_debug,
                "state": "stopping",
                "stop_timed_out": True,
            }
        else:
            self._life_motion_thread = None
            self._life_motion_debug = {
                **self._life_motion_debug,
                "state": "stopped",
                "started": False,
            }
        return dict(self._life_motion_debug)

    def perform_body_action(
        self,
        *,
        action_id: str,
        emotion_tag: str | None = None,
        intensity: float | None = None,
        duration_s: float | None = None,
    ) -> dict[str, Any]:
        """Run a VTS body/expression action without TTS or playback."""
        normalized_action = _normalize_body_action_id(action_id)
        if not normalized_action:
            result = {
                "state": "rejected",
                "success": False,
                "reason": "unknown_action_id",
                "detail": f"unknown body action: {action_id}",
                "action_id": str(action_id or ""),
                "supported_actions": sorted(_BODY_ACTION_IDS),
            }
            self._last_body_action = dict(result)
            return result

        emotion = str(emotion_tag or self._life_motion_emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        config_payload: dict[str, Any] = {"action_id": normalized_action}
        if intensity is not None:
            config_payload["emotion_intensity"] = intensity
        if duration_s is not None:
            config_payload["duration_s"] = duration_s
        config = _body_action_config(config_payload)

        if not bool(config.get("enabled")):
            result = {
                "state": "disabled",
                "success": False,
                "reason": "disabled_by_config",
                "detail": "body action disabled by config",
                "action_id": normalized_action,
                "config": dict(config),
            }
            self._last_body_action = dict(result)
            return result
        if self.vts_driver is None:
            result = {
                "state": "rejected",
                "success": False,
                "reason": "vts_driver_unavailable",
                "detail": "vts driver unavailable",
                "action_id": normalized_action,
                "config": dict(config),
            }
            self._last_body_action = dict(result)
            return result

        mouth_thread = self._mouth_sync_thread
        speech_active = (
            bool(mouth_thread is not None and mouth_thread.is_alive())
            or bool(getattr(self.speech_queue, "active_playback", None))
        )
        if speech_active:
            result = {
                "state": "rejected",
                "success": False,
                "reason": "speech_active",
                "detail": "body action rejected while speech motion is active",
                "action_id": normalized_action,
                "config": dict(config),
            }
            self._last_body_action = dict(result)
            return result

        if not self._body_action_lock.acquire(blocking=False):
            result = {
                "state": "rejected",
                "success": False,
                "reason": "body_action_busy",
                "detail": "another body action is already running",
                "action_id": normalized_action,
                "config": dict(config),
            }
            self._last_body_action = dict(result)
            return result

        self._body_action_active.set()
        self._life_motion_emotion_tag = emotion
        fps = max(1.0, float(config.get("fps") or 12.0))
        duration = max(0.1, float(config.get("duration_s") or 2.0))
        frame_count = max(2, int(round(duration * fps)))
        interval_s = max(0.016, 1.0 / fps)
        summary: dict[str, Any] = {
            "state": "starting",
            "success": False,
            "reason": None,
            "detail": "body action starting",
            "action_id": normalized_action,
            "emotion_tag": emotion,
            "duration_s": round(duration, 4),
            "fps": round(fps, 4),
            "frame_count": frame_count,
            "frames_sent": 0,
            "error_count": 0,
            "last_phase": "startup",
            "motion_parameter_count": 0,
            "speech_required": False,
            "model_move_enabled": bool(config.get("model_move_enabled")),
            "model_move_frames_sent": 0,
            "model_move_error_count": 0,
            "config": dict(config),
        }
        self._last_body_action = dict(summary)
        try:
            if self.vts_driver.state != VtsState.AUTHENTICATED:
                conn_result = self.vts_driver.connect()
                summary["connect_result"] = conn_result.as_dict()
                if not conn_result.success:
                    summary.update(
                        {
                            "state": "rejected",
                            "success": False,
                            "reason": conn_result.reason_code or "vts_connection_failed",
                            "detail": conn_result.detail,
                        }
                    )
                    self._last_body_action = dict(summary)
                    return dict(summary)

            expression_result = None
            if emotion and emotion != "NEUTRAL":
                try:
                    expression_result = self.vts_driver.set_expression(
                        emotion,
                        intensity=float(config.get("intensity") or 0.65),
                    )
                    summary["expression_result"] = expression_result.as_dict()
                except Exception as exc:  # pragma: no cover - defensive VTS runtime guard
                    summary["expression_result"] = {
                        "success": False,
                        "detail": str(exc),
                    }

            started_at = time.monotonic()
            error_samples: list[dict[str, Any]] = []
            previous_model_move = {"x": 0.0, "y": 0.0, "rotation": 0.0}
            for frame_index in range(frame_count):
                target_ts = started_at + frame_index * interval_s
                wait_s = target_ts - time.monotonic()
                if wait_s > 0:
                    time.sleep(wait_s)
                progress = _clamp01(frame_index / max(1.0, float(frame_count - 1)))
                motion_values, phase = self._body_action_values(
                    action_id=normalized_action,
                    frame_index=frame_index,
                    progress=progress,
                    elapsed_s=time.monotonic() - started_at,
                    config=config,
                    emotion_tag=emotion,
                )
                result = self.vts_driver.set_mouth_open_level(
                    0.0,
                    _include_companions=True,
                    _batch_companions=True,
                    _motion_values=motion_values,
                )
                summary["frames_sent"] = int(summary.get("frames_sent") or 0) + 1
                summary["last_phase"] = phase
                summary["motion_parameter_count"] = len(motion_values)
                if not result.success:
                    summary["error_count"] = int(summary.get("error_count") or 0) + 1
                    summary["last_error"] = result.detail
                    if len(error_samples) < 5:
                        error_samples.append(
                            {
                                "frame_index": frame_index,
                                "detail": str(result.detail or ""),
                            }
                        )
                        summary["error_samples"] = list(error_samples)
                else:
                    summary.pop("last_error", None)
                if bool(config.get("model_move_enabled")) and hasattr(self.vts_driver, "move_model"):
                    target_model_move = self._body_action_model_move(
                        action_id=normalized_action,
                        progress=progress,
                        elapsed_s=time.monotonic() - started_at,
                        config=config,
                    )
                    delta_x = target_model_move["x"] - previous_model_move["x"]
                    delta_y = target_model_move["y"] - previous_model_move["y"]
                    delta_rotation = target_model_move["rotation"] - previous_model_move["rotation"]
                    previous_model_move = dict(target_model_move)
                    move_result = self.vts_driver.move_model(
                        position_x=delta_x,
                        position_y=delta_y,
                        rotation=delta_rotation,
                        size=0.0,
                        relative=True,
                        move_time_s=interval_s * 1.15,
                    )
                    summary["model_move_frames_sent"] = int(summary.get("model_move_frames_sent") or 0) + 1
                    summary["last_model_move"] = {
                        "target": {key: round(float(value), 5) for key, value in target_model_move.items()},
                        "delta": {
                            "x": round(float(delta_x), 5),
                            "y": round(float(delta_y), 5),
                            "rotation": round(float(delta_rotation), 4),
                        },
                        "success": bool(move_result.success),
                        "detail": move_result.detail,
                    }
                    if not move_result.success:
                        summary["model_move_error_count"] = int(summary.get("model_move_error_count") or 0) + 1
                if frame_index == 0 or frame_index == frame_count - 1 or frame_index % max(1, int(fps // 2)) == 0:
                    summary["state"] = "running"
                    self._last_body_action = dict(summary)

            if bool(config.get("model_move_enabled")) and hasattr(self.vts_driver, "move_model"):
                if any(abs(float(previous_model_move[key])) > 0.0001 for key in previous_model_move):
                    move_result = self.vts_driver.move_model(
                        position_x=-previous_model_move["x"],
                        position_y=-previous_model_move["y"],
                        rotation=-previous_model_move["rotation"],
                        size=0.0,
                        relative=True,
                        move_time_s=max(0.08, interval_s * 2.0),
                    )
                    summary["model_move_frames_sent"] = int(summary.get("model_move_frames_sent") or 0) + 1
                    summary["last_model_move"] = {
                        "target": {"x": 0.0, "y": 0.0, "rotation": 0.0},
                        "delta": {
                            "x": round(float(-previous_model_move["x"]), 5),
                            "y": round(float(-previous_model_move["y"]), 5),
                            "rotation": round(float(-previous_model_move["rotation"]), 4),
                        },
                        "success": bool(move_result.success),
                        "detail": move_result.detail,
                    }
                    if not move_result.success:
                        summary["model_move_error_count"] = int(summary.get("model_move_error_count") or 0) + 1

            total_errors = int(summary.get("error_count") or 0) + int(summary.get("model_move_error_count") or 0)
            summary["state"] = "finished" if total_errors == 0 else "degraded"
            summary["success"] = int(summary.get("frames_sent") or 0) > 0 and total_errors == 0
            summary["reason"] = None if summary["success"] else "body_action_frame_errors"
            summary["detail"] = (
                f"body action {normalized_action} finished"
                if summary["success"]
                else f"body action {normalized_action} finished with frame errors"
            )
            self._last_body_action = dict(summary)
            return dict(summary)
        finally:
            self._body_action_active.clear()
            self._body_action_lock.release()

    def asr_chunk(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeAsrUpstreamResult:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        merged_payload = dict(payload)
        merged_payload.update(self._resource_metrics())

        rpc_result = self.voice_rpc.asr_chunk(
            request_id=request_id,
            payload=merged_payload,
            ts_ms=event_ts,
        )
        report_result = self._report_asr_rpc_result(
            request_id=request_id,
            rpc_result=rpc_result,
        )
        flush_result = None
        if report_result is not None and report_result.success:
            flush_result = self.event_reporter.flush_cache()
        success = bool(rpc_result.success and (report_result is None or report_result.success))
        return EdgeAsrUpstreamResult(
            success=success,
            rpc_result=rpc_result,
            report_result=report_result,
            flush_result=flush_result,
        )

    def on_asr_rpc_result(self, request_id: str, rpc_result: VoiceRpcResult) -> None:
        report_result = self._report_asr_rpc_result(
            request_id=request_id,
            rpc_result=rpc_result,
        )
        if report_result is not None and report_result.success:
            self.event_reporter.flush_cache()

    def enqueue_speak(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
        auto_start: bool = True,
        background_start: bool = False,
    ) -> EdgePlaybackResult:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        queue_result = self.speech_queue.enqueue_speak(
            request_id=request_id,
            payload=payload,
            ts_ms=event_ts,
            # Queueing and playback start are orchestrated here; keep enqueue side-effect free
            # so we do not trigger start_next() twice on the same utterance.
            auto_start=False,
        )
        self._report_voice_queue_result_background(request_id=request_id, result=queue_result)
        start_result: SpeechQueueResult | None = None
        expression_result: VtsDriverResult | None = None
        talk_result: VtsDriverResult | None = None
        mouth_sync_debug: dict[str, Any] | None = None

        background_start_scheduled = False
        if queue_result.success and auto_start and background_start:
            background_start_scheduled = self._schedule_background_speech_start(request_id=request_id)

        if queue_result.success and auto_start and not background_start:
            # 重置 VTS 钩子的结果缓存，准备接收本次播放的 VTS 结果
            self._pending_vts_result: dict[str, Any] = {}
            start_result = self.speech_queue.start_next(ts_ms=event_ts)
            if self._is_waiting_for_active_playback(start_result):
                start_result = SpeechQueueResult(
                    success=True,
                    status=queue_result.status,
                    reason_code=None,
                    stage=queue_result.stage,
                    detail="waiting for active playback to finish",
                    event={
                        **dict(queue_result.event),
                        "event_type": EVENT_TTS_QUEUED,
                        "waiting_for_active_playback": True,
                        "ts_ms": event_ts,
                    },
                )
            self._report_voice_queue_result_background(request_id=request_id, result=start_result)
            self._publish_playback_completion(start_result)
        if (
            start_result is not None
            and start_result.success
            and start_result.event.get("event_type") == EVENT_TTS_PLAYBACK_STARTED
        ):
            avatar_behavior_deferred = self._schedule_quiet_avatar_behavior(
                behavior_id="speaking",
                payload=dict(start_result.event or payload),
                source="playback_started",
            )
            start_result.event["avatar_behavior_deferred"] = bool(avatar_behavior_deferred)
            # VTS 已在 before_playback_hook 中提前驱动，从缓存中取结果
            vts_cached = getattr(self, "_pending_vts_result", {})
            if not vts_cached:
                # hook 未触发（如 speech_queue 被 mock），手动兜底驱动 VTS
                self._vts_before_playback_hook(
                    speak_event=payload,
                    synth_event={},
                )
                vts_cached = getattr(self, "_pending_vts_result", {})
            expression_result = vts_cached.get("expression_result")
            talk_result = vts_cached.get("talk_result")
            mouth_sync_debug = vts_cached.get("mouth_sync_debug")

        success = bool(
            queue_result.success
            and (
                start_result is None
                or start_result.success
            )
        )
        return EdgePlaybackResult(
            success=success,
            queue_result=queue_result,
            start_result=start_result,
            expression_result=expression_result,
            talk_result=talk_result,
            mouth_sync_debug=mouth_sync_debug,
            background_start_scheduled=background_start_scheduled,
        )

    def _schedule_background_speech_start(self, *, request_id: str) -> bool:
        with self._speech_background_lock:
            thread = self._speech_background_thread
            if thread is not None and thread.is_alive():
                return True
            delay_s = (
                _parse_float_env(
                    "EDGE_TTS_BACKGROUND_START_DELAY_MS",
                    default=180.0,
                    minimum=0.0,
                    maximum=2000.0,
                )
                / 1000.0
            )
            self._speech_background_thread = threading.Timer(
                delay_s,
                self._run_background_speech_start,
                kwargs={"request_id": request_id},
            )
            self._speech_background_thread.name = "edge-tts-background-start"
            self._speech_background_thread.daemon = True
            self._speech_background_thread.start()
            return True

    def _report_voice_queue_result_background(
        self,
        *,
        request_id: str,
        result: SpeechQueueResult | None,
    ) -> None:
        threading.Thread(
            target=self._report_voice_queue_result,
            kwargs={"request_id": request_id, "result": result},
            name="edge-voice-queue-report",
            daemon=True,
        ).start()

    def _run_background_speech_start(self, *, request_id: str) -> None:
        try:
            while True:
                event_ts = int(now_ms())
                self._pending_vts_result = {}
                start_result = self.speech_queue.start_next(ts_ms=event_ts)
                self._report_voice_queue_result(request_id=request_id, result=start_result)
                self._publish_playback_completion(start_result)
                if start_result is None or start_result.success:
                    return
                if self._is_waiting_for_active_playback(start_result):
                    return
                # ``start_next`` removes a failed synth item before returning.
                # Keep draining so one timeout cannot strand every later
                # sentence (and every later conversational turn) in memory.
                if self.speech_queue.queue_depth <= 0:
                    return
        except Exception as exc:  # pragma: no cover - background safety net
            LOGGER.warning("background TTS start failed request_id=%s: %s", request_id, exc)
        finally:
            with self._speech_background_lock:
                if self._speech_background_thread is threading.current_thread():
                    self._speech_background_thread = None
            # Close the enqueue/worker-exit race: an item can arrive after the
            # loop observes an empty queue but before this thread terminates.
            if (
                self.speech_queue.active_playback is None
                and self.speech_queue.queue_depth > 0
            ):
                self._schedule_background_speech_start(request_id=request_id)

    @staticmethod
    def _is_waiting_for_active_playback(result: SpeechQueueResult | None) -> bool:
        if result is None or result.success:
            return False
        if result.reason_code != VOICE_PLAYBACK_FAILED:
            return False
        return "while playback is active" in str(result.detail or "").strip().lower()

    def complete_playback(self, *, ts_ms: int | None = None) -> SpeechQueueResult | None:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        result = self.speech_queue.complete_active(ts_ms=event_ts)
        self._report_voice_queue_result(
            request_id=str(result.event.get("request_id") or "") if result is not None else "",
            result=result,
        )
        self._publish_playback_completion(result)
        if result is not None and self.speech_queue.queue_depth > 0:
            self._schedule_background_speech_start(
                request_id=str(result.event.get("request_id") or "queued"),
            )
        if (
            result is not None
            and result.success
            and result.event.get("event_type") == EVENT_TTS_PLAYBACK_FINISHED
            and self.vts_driver is not None
        ):
            playback_event = dict(result.event or {})
            avatar_backend = str(getattr(self.vts_driver, "backend", "") or "").strip().lower()
            mouth_sync_state = str((self._mouth_sync_debug or {}).get("state") or "").strip().lower()
            defer_avatar_reset = avatar_backend == "vrm" and mouth_sync_state == "running"
            if defer_avatar_reset:
                self._mouth_sync_debug = {
                    **self._mouth_sync_debug,
                    "playback_finished_deferred_reset": True,
                    "playback_finished_ts_ms": event_ts,
                }
                self._schedule_deferred_avatar_reset(
                    playback_event=playback_event,
                    ts_ms=event_ts,
                )
            else:
                self._stop_mouth_sync()
                talk_end_result = self.vts_driver.talk_end()
                self._log_vts_monitor(
                    action="TALK_END",
                    result=talk_end_result,
                    payload=playback_event,
                    ts_ms=int(playback_event.get("ts_ms") or event_ts),
                )
                reset_result = (
                    self.vts_driver.reset_to_neutral()
                    if _vts_reset_to_neutral_enabled()
                    else _vts_reset_to_neutral_skipped_result()
                )
                self._log_vts_monitor(
                    action="RESET_TO_NEUTRAL",
                    result=reset_result,
                    payload=playback_event,
                    ts_ms=int(playback_event.get("ts_ms") or event_ts),
                )
            self._apply_quiet_avatar_behavior(
                behavior_id="idle",
                payload=playback_event,
                source="playback_finished",
            )
        if result is not None and result.success:
            # before_playback_hook 会在 start_next 内部自动驱动 VTS，无需额外调用
            next_start_result = self.speech_queue.start_next(ts_ms=event_ts)
            self._report_voice_queue_result(
                request_id=str(result.event.get("request_id") or ""),
                result=next_start_result,
            )
        return result

    def barge_in(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeBargeInResult:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        barge_result = self.speech_queue.handle_barge_in(
            request_id=request_id,
            payload=payload,
            ts_ms=event_ts,
        )
        self._publish_playback_completion(barge_result)
        barge_event = dict(barge_result.event or {})
        LOGGER.info(
            "[EDGE_VOICE_MON][BARGE_IN] ts=%s req=%s session=%s seq=%s status=%s reason=%s stage=%s detail=%s text=\"%s\"",
            self._format_ts_ms(int(barge_event.get("ts_ms") or event_ts)),
            str(barge_event.get("request_id") or request_id or ""),
            str(barge_event.get("session_id") or payload.get("session_id") or ""),
            str(barge_event.get("seq") or payload.get("seq") or ""),
            barge_result.status,
            barge_result.reason_code,
            barge_result.stage,
            str(barge_result.detail or ""),
            self._short_log_text(barge_event.get("text") or payload.get("text"), limit=160),
        )
        vts_result: VtsDriverResult | None = None
        if (
            barge_result.success
            and barge_result.reason_code == VOICE_INTERRUPTED
            and self.vts_driver is not None
        ):
            self._schedule_barge_in_avatar_reset(
                barge_event=barge_event if isinstance(barge_event, dict) else payload,
                ts_ms=int(barge_event.get("ts_ms") or event_ts),
            )
            self._apply_quiet_avatar_behavior(
                behavior_id="interrupted",
                payload=barge_event if isinstance(barge_event, dict) else payload,
                source="barge_in",
            )
            vts_result = VtsDriverResult(
                success=True,
                reason_code=None,
                detail="barge-in avatar reset scheduled",
                action="reset_to_neutral",
            )
        return EdgeBargeInResult(
            success=barge_result.success,
            barge_result=barge_result,
            vts_result=vts_result,
        )

    def _schedule_barge_in_avatar_reset(
        self,
        *,
        barge_event: dict[str, Any],
        ts_ms: int,
    ) -> None:
        """Reset avatar state after barge-in without delaying interruption RPC."""
        def _runner() -> None:
            try:
                self._stop_mouth_sync()
                if self.vts_driver is None:
                    return
                reset_result = (
                    self.vts_driver.reset_to_neutral()
                    if _vts_reset_to_neutral_enabled()
                    else _vts_reset_to_neutral_skipped_result()
                )
                self._log_vts_monitor(
                    action="RESET_ON_BARGE_IN",
                    result=reset_result,
                    payload=barge_event,
                    ts_ms=ts_ms,
                    extra={"source": "barge_in_background_reset"},
                )
                self._apply_quiet_avatar_behavior(
                    behavior_id="interrupted",
                    payload=barge_event,
                    source="barge_in_background_reset",
                )
            except Exception as exc:  # pragma: no cover - background avatar cleanup
                LOGGER.warning("barge-in avatar reset failed: %s", exc)

        threading.Thread(
            target=_runner,
            name="edge-barge-in-avatar-reset",
            daemon=True,
        ).start()

    def _report_asr_rpc_result(
        self,
        *,
        request_id: str,
        rpc_result: VoiceRpcResult,
    ) -> BatchReportResult | None:
        event = dict(rpc_result.event)
        if event.get("event_type") != EVENT_ASR_CHUNK:
            return None
        session_id = str(event.get("session_id") or "").strip()
        if not session_id:
            return None
        emotion_report = self._resolve_asr_emotion_report(event)
        payload = {
            **event,
            **self._resource_metrics(),
        }
        emotion_tag = str(emotion_report.get("emotion_tag") or "").strip().upper() or None
        emotion_intensity = emotion_report.get("emotion_intensity")
        if emotion_tag:
            payload["emotion_tag"] = emotion_tag
        if isinstance(emotion_intensity, (int, float)):
            payload["emotion_intensity"] = max(0.0, min(1.0, float(emotion_intensity)))
        mood_state = str(emotion_report.get("mood_state") or "").strip().lower()
        if mood_state:
            payload["mood_state"] = mood_state
        emotion_source = str(emotion_report.get("emotion_source") or "").strip()
        if emotion_source:
            payload["emotion_source"] = emotion_source
        emotion_modality = str(emotion_report.get("emotion_modality") or "").strip()
        if emotion_modality:
            payload["emotion_modality"] = emotion_modality
        emotion_confidence = emotion_report.get("emotion_confidence")
        if isinstance(emotion_confidence, (int, float)):
            payload["emotion_confidence"] = max(0.0, min(1.0, float(emotion_confidence)))
        if "emotion_inference_degraded" in emotion_report:
            payload["emotion_inference_degraded"] = bool(emotion_report.get("emotion_inference_degraded"))
        if "acoustic_emotion_available" in emotion_report:
            payload["acoustic_emotion_available"] = bool(emotion_report.get("acoustic_emotion_available"))
        inference_note = str(emotion_report.get("inference_note") or "").strip()
        if inference_note:
            payload["emotion_inference_note"] = inference_note
        matched_terms = emotion_report.get("matched_terms")
        if isinstance(matched_terms, list) and matched_terms:
            payload["emotion_terms"] = list(matched_terms)

        report_event = {
            "event_type": EVENT_ASR_CHUNK,
            "ts_ms": int(event.get("ts_ms") or now_ms()),
            "request_id": str(request_id),
            "session_id": session_id,
            "source": "edge",
            "direction": "EDGE_TO_HOST",
            "seq": event.get("seq"),
            "status": rpc_result.status,
            "reason_code": rpc_result.reason_code,
            "stage": rpc_result.stage,
            "detail": rpc_result.detail,
            "payload": payload,
        }
        if emotion_tag:
            report_event["emotion_tag"] = emotion_tag
        if isinstance(emotion_intensity, (int, float)):
            report_event["emotion_intensity"] = max(0.0, min(1.0, float(emotion_intensity)))
        report_result = self.event_reporter.report([report_event])

        self._somatic_agent.publish_body_sense_updated(
            request_id=str(request_id or ""),
            session_id=session_id,
            seq=event.get("seq"),
            ts_ms=int(event.get("ts_ms") or now_ms()),
            resource_metrics=self._resource_metrics(),
            upstream_status=str(rpc_result.status or ""),
            upstream_reason_code=rpc_result.reason_code,
        )
        if emotion_tag or mood_state:
            self._somatic_agent.publish_emotion_inference_updated(
                request_id=str(request_id or ""),
                session_id=session_id,
                seq=event.get("seq"),
                emotion_tag=emotion_tag or "NEUTRAL",
                emotion_intensity=(
                    max(0.0, min(1.0, float(emotion_intensity)))
                    if isinstance(emotion_intensity, (int, float))
                    else None
                ),
                mood_state=mood_state or None,
                emotion_source=emotion_source or None,
                emotion_terms=list(matched_terms) if isinstance(matched_terms, list) else [],
                emotion_modality=emotion_modality or None,
                emotion_confidence=(
                    max(0.0, min(1.0, float(emotion_confidence)))
                    if isinstance(emotion_confidence, (int, float))
                    else None
                ),
                emotion_inference_degraded=(
                    bool(emotion_report.get("emotion_inference_degraded"))
                    if "emotion_inference_degraded" in emotion_report
                    else None
                ),
                acoustic_emotion_available=(
                    bool(emotion_report.get("acoustic_emotion_available"))
                    if "acoustic_emotion_available" in emotion_report
                    else None
                ),
            )
        self._maybe_apply_asr_avatar_behavior(
            request_id=request_id,
            event=payload,
            report_result=report_result,
        )
        return report_result

    def _avatar_auto_state_speech_active(self) -> bool:
        """Return whether avatar speech/mouth-sync state is currently active.

        ASR-driven avatar reactions should not fight with an ongoing TTS/mouth-sync
        session unless explicitly allowed by configuration. Keep this defensive so
        contract tests and degraded runtimes do not crash when optional state
        fields are absent.
        """
        debug = getattr(self, "_mouth_sync_debug", None)
        if isinstance(debug, dict):
            state = str(debug.get("state") or "").strip().lower()
            if state in {"running", "talk_started", "queued", "starting"}:
                return True
            if bool(debug.get("started")):
                return True
        thread = getattr(self, "_mouth_sync_thread", None)
        try:
            if thread is not None and thread.is_alive():
                return True
        except Exception:
            return False
        return False
    def _maybe_apply_asr_avatar_behavior(
        self,
        *,
        request_id: str,
        event: dict[str, Any],
        report_result: BatchReportResult | None,
    ) -> dict[str, Any] | None:
        if not _parse_bool_env("EDGE_AVATAR_BEHAVIOR_ASR_AUTO_STATE_ENABLED", default=True):
            return None
        report_failed = bool(report_result is not None and not report_result.success)
        text = str(event.get("text") or event.get("transcript") or "").strip()
        if not text:
            return None
        if bool(event.get("low_confidence")) and not _parse_bool_env(
            "EDGE_AVATAR_BEHAVIOR_ASR_LOW_CONFIDENCE_STATE_ENABLED",
            default=False,
        ):
            return {
                "success": True,
                "state": "skipped",
                "reason": "asr_low_confidence",
                "detail": "ASR avatar state skipped for low-confidence transcript",
            }
        if self._avatar_auto_state_speech_active() and not _parse_bool_env(
            "EDGE_AVATAR_BEHAVIOR_ASR_DURING_SPEECH_ENABLED",
            default=False,
        ):
            return {
                "success": True,
                "state": "skipped",
                "reason": "speech_active",
                "detail": "ASR avatar state skipped while AI speech is active",
            }

        final = bool(event.get("final") or event.get("is_final"))
        if final:
            if not _parse_bool_env("EDGE_AVATAR_BEHAVIOR_ASR_FINAL_THINKING_ENABLED", default=True):
                return None
            behavior_id = "thinking"
            source = "asr_final"
            min_interval_ms = int(_parse_float_env(
                "EDGE_AVATAR_BEHAVIOR_ASR_FINAL_MIN_INTERVAL_MS",
                default=250.0,
                minimum=0.0,
                maximum=10000.0,
            ))
        else:
            if not _parse_bool_env("EDGE_AVATAR_BEHAVIOR_ASR_PARTIAL_LISTENING_ENABLED", default=True):
                return None
            behavior_id = "listening"
            source = "asr_partial"
            min_interval_ms = int(_parse_float_env(
                "EDGE_AVATAR_BEHAVIOR_ASR_LISTENING_MIN_INTERVAL_MS",
                default=900.0,
                minimum=0.0,
                maximum=10000.0,
            ))

        event_ts = int(event.get("ts_ms") or now_ms())
        session_id = str(event.get("session_id") or "").strip() or "default"
        throttle_key = f"{behavior_id}:{session_id}"
        last_map = getattr(self, "_avatar_auto_state_last_ts", None)
        if not isinstance(last_map, dict):
            last_map = {}
            self._avatar_auto_state_last_ts = last_map
        last_ts = int(last_map.get(throttle_key) or 0)
        if min_interval_ms > 0 and event_ts - last_ts < min_interval_ms:
            return {
                "success": True,
                "state": "skipped",
                "reason": "avatar_state_throttled",
                "detail": f"ASR avatar state throttled for {behavior_id}",
                "behavior_id": behavior_id,
                "source": source,
            }
        last_map[throttle_key] = event_ts

        behavior_payload = {
            **dict(event),
            "request_id": str(request_id or event.get("request_id") or ""),
            "source": source,
        }
        if report_failed:
            behavior_payload["edge_event_report_failed"] = True
            behavior_payload["edge_event_report_reason_code"] = str(getattr(report_result, "reason_code", "") or "")
            behavior_payload["edge_event_report_detail"] = str(getattr(report_result, "detail", "") or "")
        if not final:
            behavior_payload["body_hint_enabled"] = False
        return self._apply_quiet_avatar_behavior(
            behavior_id=behavior_id,
            payload=behavior_payload,
            source=source,
        )

    def _report_voice_queue_result(
        self,
        *,
        request_id: str,
        result: SpeechQueueResult | None,
    ) -> BatchReportResult | None:
        if result is None:
            return None
        event = dict(result.event or {})
        event_type = str(event.get("event_type") or "").strip()
        session_id = str(event.get("session_id") or "").strip()
        if event_type:
            LOGGER.info(
                "[EDGE_VOICE_MON][TTS] ts=%s event=%s req=%s session=%s seq=%s status=%s reason=%s stage=%s detail=%s text=\"%s\"",
                self._format_ts_ms(int(event.get("ts_ms") or now_ms())),
                event_type,
                str(event.get("request_id") or request_id or ""),
                session_id or str(event.get("session_id") or ""),
                str(event.get("seq") or ""),
                result.status,
                result.reason_code,
                result.stage,
                str(result.detail or ""),
                self._short_log_text(event.get("text"), limit=160),
            )
        if not event_type or not session_id:
            return None
        report_event = {
            "event_type": event_type,
            "ts_ms": int(event.get("ts_ms") or now_ms()),
            "request_id": str(event.get("request_id") or request_id or ""),
            "session_id": session_id,
            "source": "edge",
            "direction": "EDGE_TO_HOST",
            "seq": event.get("seq"),
            "status": result.status,
            "reason_code": result.reason_code,
            "stage": result.stage,
            "detail": result.detail,
            "payload": {
                **event,
                **self._resource_metrics(),
            },
        }
        report_result = self.event_reporter.report([report_event])
        if report_result.success:
            self.event_reporter.flush_cache()
        if event_type == EVENT_TTS_PLAYBACK_STARTED:
            self._voice_agent.publish_pipeline_started(
                request_id=str(event.get("request_id") or request_id or ""),
                session_id=session_id,
                ts_ms=int(event.get("ts_ms") or now_ms()),
                status=result.status,
                reason_code=result.reason_code,
                stage=result.stage,
            )
        elif event_type == EVENT_TTS_PLAYBACK_FINISHED:
            self._voice_agent.publish_pipeline_ended(
                request_id=str(event.get("request_id") or request_id or ""),
                session_id=session_id,
                ts_ms=int(event.get("ts_ms") or now_ms()),
                status=result.status,
                reason_code=result.reason_code,
                stage=result.stage,
            )
        return report_result

    def _resource_metrics(self) -> dict[str, Any]:
        queue_depth = int(self.speech_queue.queue_depth)
        if self.resource_gate is None:
            return {
                "vram_total_mb": 0,
                "vram_used_mb": 0,
                "queue_depth": queue_depth,
            }
        snapshot = self.resource_gate.snapshot()
        metrics = dict(snapshot.get("metrics") or {})
        return {
            "vram_total_mb": int(metrics.get("vram_total_mb", 0)),
            "vram_used_mb": int(metrics.get("vram_used_mb", 0)),
            "queue_depth": queue_depth,
        }

    def _vts_before_playback_hook(
        self,
        speak_event: dict[str, Any],
        synth_event: dict[str, Any],
    ) -> None:
        """在音频播放启动前驱动 VTS 表情和口型，消除滞后。"""
        payload = dict(speak_event)
        # 合并 emotion_tag / emotion_intensity
        if not payload.get("emotion_tag") and speak_event.get("emotion_tag"):
            payload["emotion_tag"] = str(speak_event["emotion_tag"]).strip().upper()
        _etag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
        _has_audio = self._synth_event_has_audio(synth_event)
        LOGGER.info(
            "VTS start (pre-playback): emotion_tag=%s synth_has_audio=%s",
            _etag, _has_audio,
        )
        expression_result, talk_result, mouth_sync_debug = self._start_vts_for_payload(
            payload,
            synth_event=synth_event,
        )
        self._log_vts_monitor(
            action="PRE_PLAYBACK",
            result=expression_result,
            payload=payload,
            detail=f"emotion_tag={_etag} has_audio={_has_audio}",
            extra={"mouth_sync_state": str((mouth_sync_debug or {}).get("state") or "")},
        )
        # 缓存结果供 enqueue_speak / complete_playback 读取
        self._pending_vts_result = {
            "expression_result": expression_result,
            "talk_result": talk_result,
            "mouth_sync_debug": mouth_sync_debug,
        }
        self._last_vts_result = {
            "request_id": str(payload.get("request_id") or "") or None,
            "emotion_tag": _etag,
            "expression_result": expression_result.as_dict() if expression_result is not None else None,
            "talk_result": talk_result.as_dict() if talk_result is not None else None,
            "mouth_sync": dict(mouth_sync_debug or {}),
        }

    def _schedule_vts_background_command(
        self,
        *,
        action: str,
        callback: Any,
        payload: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> None:
        def _runner() -> None:
            try:
                result = callback()
                self._log_vts_monitor(
                    action=action,
                    result=result,
                    payload=payload,
                    extra={
                        **dict(extra or {}),
                        "source": "vrm_pre_playback_fast_path",
                        "background": True,
                    },
                )
            except Exception as exc:  # pragma: no cover - defensive background bridge guard
                self._log_vts_monitor(
                    action=action,
                    payload=payload,
                    detail=f"background VTS command failed: {exc}",
                    extra={
                        **dict(extra or {}),
                        "source": "vrm_pre_playback_fast_path",
                        "background": True,
                        "error": str(exc),
                    },
                )

        threading.Thread(
            target=_runner,
            name=f"edge-vts-bg-{action.lower()}",
            daemon=True,
        ).start()

    def _start_vts_for_payload(
        self,
        payload: dict[str, Any],
        *,
        synth_event: dict[str, Any] | None = None,
    ) -> tuple[VtsDriverResult | None, VtsDriverResult | None, dict[str, Any] | None]:
        if self.vts_driver is None:
            self._mouth_sync_debug = {
                "state": "skipped",
                "started": False,
                "reason": "vts_driver_unavailable",
            }
            self._log_vts_monitor(
                action="VTS_UNAVAILABLE",
                payload=payload,
                detail="vts_driver_unavailable",
                extra={"mouth_sync_state": "skipped"},
            )
            return None, None, dict(self._mouth_sync_debug)
        avatar_backend = str(getattr(self.vts_driver, "backend", "") or "").strip().lower()
        vrm_fast_preplayback = avatar_backend == "vrm" and _parse_bool_env(
            "EDGE_VRM_PRE_PLAYBACK_FAST_PATH_ENABLED",
            default=True,
        )
        if not self._stop_mouth_sync():
            mouth_sync_debug = {
                "state": "blocked",
                "started": False,
                "reason": "previous_mouth_sync_still_stopping",
                "detail": "previous VTS mouth sync thread did not stop before next playback",
            }
            self._mouth_sync_debug = dict(mouth_sync_debug)
            self._log_vts_monitor(
                action="MOUTH_SYNC",
                payload=payload,
                detail="previous_mouth_sync_still_stopping",
                extra=mouth_sync_debug,
            )
            return None, None, dict(mouth_sync_debug)
        if self.vts_driver.state != VtsState.AUTHENTICATED:
            if vrm_fast_preplayback:
                self._schedule_vts_background_command(
                    action="CONNECT",
                    callback=self.vts_driver.connect,
                    payload=payload,
                    extra={"deferred_reason": "avoid_pre_playback_http_connect"},
                )
            else:
                conn_result = self.vts_driver.connect()
                self._log_vts_monitor(
                    action="CONNECT",
                    result=conn_result,
                    payload=payload,
                )
                if not conn_result.success:
                    self._mouth_sync_debug = {
                        "state": "blocked",
                        "started": False,
                        "reason": "vts_connect_failed",
                        "detail": conn_result.detail,
                    }
                    return conn_result, None, dict(self._mouth_sync_debug)
        emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
        self._life_motion_emotion_tag = emotion_tag
        raw_intensity = payload.get("emotion_intensity")
        intensity = 0.5
        if raw_intensity is not None:
            try:
                intensity = max(0.0, min(1.0, float(raw_intensity)))
            except (TypeError, ValueError):
                intensity = 0.5
        voice_expression_enabled = _parse_bool_env("EDGE_VTS_VOICE_EXPRESSION_ENABLED", default=True)
        if not voice_expression_enabled:
            expression_result = VtsDriverResult(
                success=True,
                reason_code=None,
                detail="voice expression disabled; expression hotkey skipped",
                action="set_expression",
                expression_id=None,
                emotion_tag=emotion_tag,
            )
        elif vrm_fast_preplayback:
            transition_frames = payload.get("transition_frames")
            if emotion_tag == "NEUTRAL":
                expression_callback = (
                    self.vts_driver.reset_to_neutral
                    if _vts_reset_to_neutral_enabled()
                    else _vts_reset_to_neutral_skipped_result
                )
            elif transition_frames and isinstance(transition_frames, list) and len(transition_frames) > 1:
                expression_callback = lambda: self.vts_driver.transition_expression(
                    emotion_tag,
                    transition_frames=transition_frames,
                )
            else:
                expression_callback = lambda: self.vts_driver.set_expression(
                    emotion_tag,
                    intensity=intensity,
                )
            self._schedule_vts_background_command(
                action="SET_EXPRESSION",
                callback=expression_callback,
                payload=payload,
                extra={
                    "emotion_tag": emotion_tag,
                    "intensity": round(float(intensity), 4),
                    "voice_expression_enabled": bool(voice_expression_enabled),
                    "deferred_reason": "avoid_pre_playback_http_expression",
                },
            )
            expression_result = VtsDriverResult(
                success=True,
                reason_code=None,
                detail="vrm fast pre-playback: expression scheduled in background",
                action="set_expression",
                expression_id=emotion_tag,
                emotion_tag=emotion_tag,
            )
        elif emotion_tag == "NEUTRAL":
            expression_result = (
                self.vts_driver.reset_to_neutral()
                if _vts_reset_to_neutral_enabled()
                else _vts_reset_to_neutral_skipped_result()
            )
        else:
            # 方向 C：优先使用过渡帧（payload 中含 transition_frames 时）
            transition_frames = payload.get("transition_frames")
            if transition_frames and isinstance(transition_frames, list) and len(transition_frames) > 1:
                expression_result = self.vts_driver.transition_expression(
                    emotion_tag,
                    transition_frames=transition_frames,
                )
            else:
                expression_result = self.vts_driver.set_expression(emotion_tag, intensity=intensity)
        self._log_vts_monitor(
            action="SET_EXPRESSION",
            result=expression_result,
            payload=payload,
            extra={
                "emotion_tag": emotion_tag,
                "intensity": round(float(intensity), 4),
                "voice_expression_enabled": bool(voice_expression_enabled),
            },
        )
        sub_emotion = payload.get("sub_emotion")
        if sub_emotion and voice_expression_enabled:
            hotkey_id = sub_emotion_to_hotkey(sub_emotion)
            if hotkey_id:
                if vrm_fast_preplayback:
                    self._schedule_vts_background_command(
                        action="TRIGGER_HOTKEY",
                        callback=lambda: self.vts_driver.trigger_hotkey(hotkey_id),
                        payload=payload,
                        extra={
                            "hotkey_id": str(hotkey_id),
                            "deferred_reason": "avoid_pre_playback_http_hotkey",
                        },
                    )
                else:
                    hotkey_result = self.vts_driver.trigger_hotkey(hotkey_id)
                    self._log_vts_monitor(
                        action="TRIGGER_HOTKEY",
                        result=hotkey_result,
                        payload=payload,
                        extra={"hotkey_id": str(hotkey_id)},
                    )
        speech_text = str(payload.get("text") or payload.get("message") or "").strip()
        display_text_result: VtsDriverResult | None = None
        display_text = getattr(self.vts_driver, "display_text", None)
        display_text_debug = {
            "attempted": False,
            "success": None,
            "detail": None,
        }
        if speech_text and callable(display_text):
            if vrm_fast_preplayback:
                self._schedule_vts_background_command(
                    action="DISPLAY_TEXT",
                    callback=lambda: display_text(speech_text),
                    payload=payload,
                    extra={
                        "text_chars": len(speech_text),
                        "deferred_reason": "avoid_pre_playback_http_display_text",
                    },
                )
                display_text_debug = {
                    "attempted": True,
                    "success": None,
                    "detail": "scheduled in background",
                }
            else:
                display_text_result = display_text(speech_text)
                display_text_debug = {
                    "attempted": True,
                    "success": bool(display_text_result.success),
                    "detail": str(display_text_result.detail or ""),
                }
                self._log_vts_monitor(
                    action="DISPLAY_TEXT",
                    result=display_text_result,
                    payload=payload,
                    extra={"text_chars": len(speech_text)},
                )
        payload["_display_text_debug"] = dict(display_text_debug)
        if vrm_fast_preplayback:
            self._schedule_vts_background_command(
                action="TALK_START",
                callback=self.vts_driver.talk_start,
                payload=payload,
                extra={"deferred_reason": "avoid_pre_playback_http_talk_start"},
            )
            talk_result = VtsDriverResult(
                success=True,
                reason_code=None,
                detail="vrm fast pre-playback: talk_start scheduled in background",
                action="talk_start",
            )
            vts_health = {"mouth_parameters": {}}
        else:
            talk_result = self.vts_driver.talk_start()
            self._log_vts_monitor(
                action="TALK_START",
                result=talk_result,
                payload=payload,
            )
            vts_health = self.vts_driver.health()
        mouth_sync_debug = {
            "state": "talk_started" if talk_result.success else "talk_failed",
            "started": False,
            "emotion_tag": emotion_tag,
            "intensity": round(float(intensity), 4),
            "talk_gate_success": bool(talk_result.success),
            "talk_gate_detail": str(talk_result.detail or ""),
            "display_text": display_text_debug,
            "vts_parameters": dict(vts_health.get("mouth_parameters") or {}),
            "vrm_pre_playback_fast_path": bool(vrm_fast_preplayback),
        }
        has_audio = self._synth_event_has_audio(synth_event)
        if has_audio:
            mouth_sync_debug = self._start_mouth_sync(synth_event, payload=payload)
            mouth_sync_debug["talk_gate_success"] = bool(talk_result.success)
            mouth_sync_debug["display_text"] = display_text_debug
            mouth_sync_debug["vrm_pre_playback_fast_path"] = bool(vrm_fast_preplayback)
            if not talk_result.success:
                mouth_sync_debug["talk_gate_detail"] = str(talk_result.detail or "")
            expressive_timing = (
                dict(synth_event.get("expressive_timing") or {})
                if isinstance(synth_event, dict)
                else {}
            )
            if expressive_timing:
                mouth_sync_debug["expressive_timing"] = expressive_timing
            self._log_vts_monitor(
                action="MOUTH_SYNC",
                payload=payload,
                detail=str(mouth_sync_debug.get("state") or ""),
                extra={
                    "state": str(mouth_sync_debug.get("state") or ""),
                    "started": bool(mouth_sync_debug.get("started", False)),
                    "envelope_points": int(mouth_sync_debug.get("envelope_points") or 0),
                    "expressive_timing": expressive_timing or None,
                },
            )
        elif not has_audio:
            mouth_sync_debug["reason"] = "missing_synth_event"
            LOGGER.warning(
                "VTS mouth sync skipped: no synth_event (Edge needs local TTS with audio_data, e.g. SoVITS, for lip sync)"
            )
            self._log_vts_monitor(
                action="MOUTH_SYNC",
                payload=payload,
                detail="missing_synth_event",
                extra={
                    "state": str(mouth_sync_debug.get("state") or ""),
                    "started": bool(mouth_sync_debug.get("started", False)),
                    "reason": "missing_synth_event",
                },
            )
        self._mouth_sync_debug = dict(mouth_sync_debug)
        return expression_result, talk_result, dict(mouth_sync_debug)

    @staticmethod
    def _synth_event_has_audio(synth_event: dict[str, Any] | None) -> bool:
        if not isinstance(synth_event, dict):
            return False
        if "audio_data" not in synth_event:
            return False
        audio_data = synth_event.get("audio_data")
        if audio_data is None:
            return False
        size_attr = getattr(audio_data, "size", None)
        if isinstance(size_attr, int):
            return size_attr > 0
        if isinstance(audio_data, (bytes, bytearray, memoryview, str)):
            return len(audio_data) > 0
        try:
            return len(audio_data) > 0  # type: ignore[arg-type]
        except Exception:
            return True

    def _stop_mouth_sync(self) -> bool:
        self._mouth_sync_stop.set()
        thread = self._mouth_sync_thread
        if thread is not None and thread.is_alive():
            thread.join(
                timeout=_parse_float_env(
                    "EDGE_VTS_MOUTH_SYNC_STOP_TIMEOUT_S",
                    default=8.0,
                    minimum=0.2,
                    maximum=30.0,
                )
            )
        if thread is not None and thread.is_alive():
            self._mouth_sync_debug = {
                **self._mouth_sync_debug,
                "state": "stopping",
                "stop_timed_out": True,
            }
            return False
        self._mouth_sync_thread = None
        self._mouth_sync_stop = threading.Event()
        current_state = str(self._mouth_sync_debug.get("state") or "")
        if current_state == "running":
            self._mouth_sync_debug = {
                **self._mouth_sync_debug,
                "state": "stopped",
            }
        return True

    def _schedule_deferred_avatar_reset(
        self,
        *,
        playback_event: dict[str, Any],
        ts_ms: int,
    ) -> None:
        """Close VRM/MateEngine talk state after the mouth envelope has ended."""
        avatar_backend = str(getattr(self.vts_driver, "backend", "") or "").strip().lower()
        if avatar_backend != "vrm" or self.vts_driver is None:
            return
        watched_thread = self._mouth_sync_thread
        watched_request_id = str(playback_event.get("request_id") or "").strip()
        timeout_s = _parse_float_env(
            "EDGE_VTS_MOUTH_SYNC_STOP_TIMEOUT_S",
            default=8.0,
            minimum=0.2,
            maximum=30.0,
        )

        def _runner() -> None:
            if watched_thread is not None and watched_thread.is_alive():
                watched_thread.join(timeout=timeout_s)
            if watched_thread is not None and watched_thread.is_alive():
                self._mouth_sync_debug = {
                    **self._mouth_sync_debug,
                    "deferred_avatar_reset": "timed_out",
                    "deferred_avatar_reset_timeout_s": round(float(timeout_s), 4),
                }
                return

            current_request_id = str((self._mouth_sync_debug or {}).get("request_id") or "").strip()
            if watched_request_id and current_request_id and current_request_id != watched_request_id:
                self._mouth_sync_debug = {
                    **self._mouth_sync_debug,
                    "deferred_avatar_reset": "skipped_new_playback",
                    "deferred_avatar_reset_request_id": watched_request_id,
                    "current_request_id": current_request_id,
                }
                return

            try:
                talk_end_result = self.vts_driver.talk_end()
                self._log_vts_monitor(
                    action="TALK_END",
                    result=talk_end_result,
                    payload=playback_event,
                    ts_ms=int(playback_event.get("ts_ms") or ts_ms),
                    extra={"source": "deferred_avatar_reset"},
                )
                reset_result = (
                    self.vts_driver.reset_to_neutral()
                    if _vts_reset_to_neutral_enabled()
                    else _vts_reset_to_neutral_skipped_result()
                )
                self._log_vts_monitor(
                    action="RESET_TO_NEUTRAL",
                    result=reset_result,
                    payload=playback_event,
                    ts_ms=int(playback_event.get("ts_ms") or ts_ms),
                    extra={"source": "deferred_avatar_reset"},
                )
                self._mouth_sync_debug = {
                    **self._mouth_sync_debug,
                    "deferred_avatar_reset": "completed",
                    "deferred_talk_end_success": bool(talk_end_result.success),
                    "deferred_reset_success": bool(reset_result.success),
                }
                self._apply_quiet_avatar_behavior(
                    behavior_id="idle",
                    payload=playback_event,
                    source="deferred_avatar_reset",
                )
            except Exception as exc:  # pragma: no cover - defensive background cleanup
                self._mouth_sync_debug = {
                    **self._mouth_sync_debug,
                    "deferred_avatar_reset": "error",
                    "deferred_avatar_reset_error": str(exc),
                }

        threading.Thread(
            target=_runner,
            name="edge-vrm-deferred-avatar-reset",
            daemon=True,
        ).start()

    def _schedule_quiet_avatar_behavior(
        self,
        *,
        behavior_id: str,
        payload: dict[str, Any] | None = None,
        source: str,
    ) -> bool:
        if not _parse_bool_env("EDGE_AVATAR_BEHAVIOR_AUTO_STATE_ENABLED", default=True):
            return False
        if not _parse_bool_env("EDGE_AVATAR_BEHAVIOR_PLAYBACK_STARTED_ASYNC_ENABLED", default=True):
            return False
        if self.vts_driver is None:
            return False

        behavior_payload = dict(payload or {})

        def _runner() -> None:
            self._apply_quiet_avatar_behavior(
                behavior_id=behavior_id,
                payload=behavior_payload,
                source=source,
            )

        threading.Thread(
            target=_runner,
            name=f"edge-avatar-behavior-{behavior_id}-{source}",
            daemon=True,
        ).start()
        return True

    def _apply_quiet_avatar_behavior(
        self,
        *,
        behavior_id: str,
        payload: dict[str, Any] | None = None,
        source: str,
    ) -> dict[str, Any] | None:
        if not _parse_bool_env("EDGE_AVATAR_BEHAVIOR_AUTO_STATE_ENABLED", default=True):
            return None
        if self.vts_driver is None:
            return None
        try:
            from edge_client.runtime.avatar_behavior import execute_avatar_behavior

            behavior_payload = {
                **dict(payload or {}),
                "behavior_id": behavior_id,
                "source": source,
            }
            result = execute_avatar_behavior(self, behavior_payload)
            self._last_vts_result = {
                **dict(self._last_vts_result or {}),
                "avatar_behavior": {
                    **dict(result),
                    "source": source,
                },
            }
            LOGGER.info(
                "avatar behavior applied: behavior=%s source=%s success=%s state=%s",
                behavior_id,
                source,
                bool(result.get("success")),
                str(result.get("state") or ""),
            )
            return result
        except Exception as exc:  # pragma: no cover - behavior should never break voice
            LOGGER.warning(
                "avatar behavior failed: behavior=%s source=%s error=%s",
                behavior_id,
                source,
                exc,
            )
            return {
                "success": False,
                "state": "error",
                "reason": "avatar_behavior_error",
                "detail": str(exc),
                "behavior_id": behavior_id,
                "source": source,
            }

    def _life_motion_loop(self, config: dict[str, Any], stop_event: threading.Event) -> None:
        fps = max(1.0, float(config.get("fps") or 4.0))
        interval_s = max(0.08, 1.0 / fps)
        started_at = time.monotonic()
        frame_index = 0
        previous_model_move = dict(self._life_motion_model_offset)
        self._life_motion_debug = {
            **self._life_motion_debug,
            "state": "running",
            "started": True,
            "interval_s": round(interval_s, 4),
        }
        while not stop_event.is_set():
            try:
                if self.vts_driver is None:
                    self._life_motion_debug = {
                        **self._life_motion_debug,
                        "state": "skipped",
                        "reason": "vts_driver_unavailable",
                    }
                    break
                if self.vts_driver.state != VtsState.AUTHENTICATED:
                    reconnect_result = None
                    if (
                        self.vts_driver.state in (VtsState.DISCONNECTED, VtsState.ERROR)
                        and hasattr(self.vts_driver, "trigger_reconnect")
                    ):
                        # A VTS cold start can finish after Edge has already
                        # entered its motion loop.  Hand recovery to the
                        # driver's bounded background backoff instead of
                        # waiting forever or retrying every motion frame.
                        reconnect_result = self.vts_driver.trigger_reconnect()
                    self._life_motion_debug = {
                        **self._life_motion_debug,
                        "state": "waiting_for_vts",
                        "vts_state": str(getattr(self.vts_driver.state, "value", self.vts_driver.state)),
                        "reconnect_requested": bool(
                            reconnect_result is not None and reconnect_result.success
                        ),
                        "reconnect_detail": (
                            reconnect_result.detail if reconnect_result is not None else None
                        ),
                    }
                    stop_event.wait(interval_s)
                    continue

                mouth_thread = self._mouth_sync_thread
                speech_active = (
                    bool(mouth_thread is not None and mouth_thread.is_alive())
                    or bool(getattr(self.speech_queue, "active_playback", None))
                )
                body_action_active = self._body_action_active.is_set()
                if speech_active or body_action_active:
                    self._life_motion_debug = {
                        **self._life_motion_debug,
                        "state": "paused_for_body_action" if body_action_active else "paused_for_speech",
                        "paused_for_speech": bool(speech_active),
                        "paused_for_body_action": bool(body_action_active),
                    }
                    stop_event.wait(interval_s)
                    continue

                elapsed_s = time.monotonic() - started_at
                motion_values, phase = self._life_motion_values(
                    frame_index=frame_index,
                    elapsed_s=elapsed_s,
                    config=config,
                    emotion_tag=self._life_motion_emotion_tag,
                )
                result = self.vts_driver.set_mouth_open_level(
                    0.0,
                    _include_companions=True,
                    _batch_companions=True,
                    _motion_values=motion_values,
                )
                debug = dict(self._life_motion_debug)
                debug.update(
                    {
                        "state": "running",
                        # Refresh this on every attempted live frame.  Keeping
                        # the last pre-recovery value made health report
                        # CONNECTING even after the shared VTS driver had
                        # authenticated a replacement WebSocket.
                        "vts_state": str(
                            getattr(
                                self.vts_driver.state,
                                "value",
                                self.vts_driver.state,
                            )
                        ),
                        "paused_for_speech": False,
                        "paused_for_body_action": False,
                        "reconnect_requested": False,
                        "reconnect_detail": None,
                        "frames_sent": int(debug.get("frames_sent") or 0) + 1,
                        "last_phase": phase,
                        "last_emotion_tag": self._life_motion_emotion_tag,
                        "last_motion_parameter_count": len(motion_values),
                    }
                )
                if bool(config.get("expression_enabled")):
                    debug["expression_frames_sent"] = int(debug.get("expression_frames_sent") or 0) + 1
                if not result.success:
                    debug["error_count"] = int(debug.get("error_count") or 0) + 1
                    debug["last_error"] = result.detail
                else:
                    debug.pop("last_error", None)
                if bool(config.get("model_move_enabled")) and hasattr(self.vts_driver, "move_model"):
                    target_model_move = self._life_model_move(
                        elapsed_s=elapsed_s,
                        config=config,
                        emotion_tag=self._life_motion_emotion_tag,
                    )
                    delta_x = target_model_move["x"] - previous_model_move["x"]
                    delta_y = target_model_move["y"] - previous_model_move["y"]
                    delta_rotation = target_model_move["rotation"] - previous_model_move["rotation"]
                    previous_model_move = dict(target_model_move)
                    self._life_motion_model_offset = dict(previous_model_move)
                    move_result = self.vts_driver.move_model(
                        position_x=delta_x,
                        position_y=delta_y,
                        rotation=delta_rotation,
                        size=0.0,
                        relative=True,
                        move_time_s=interval_s * 1.15,
                    )
                    debug["model_move_enabled"] = True
                    debug["model_move_frames_sent"] = int(debug.get("model_move_frames_sent") or 0) + 1
                    debug["last_model_move"] = {
                        "target": {key: round(float(value), 5) for key, value in target_model_move.items()},
                        "delta": {
                            "x": round(float(delta_x), 5),
                            "y": round(float(delta_y), 5),
                            "rotation": round(float(delta_rotation), 4),
                        },
                        "success": bool(move_result.success),
                    }
                    if not move_result.success:
                        debug["model_move_error_count"] = int(debug.get("model_move_error_count") or 0) + 1
                        debug["last_model_move"]["detail"] = move_result.detail
                    else:
                        debug.pop("last_model_move_error", None)
                self._life_motion_debug = debug
                frame_index += 1
            except Exception as exc:  # pragma: no cover - defensive background loop
                self._life_motion_debug = {
                    **self._life_motion_debug,
                    "state": "error",
                    "error_count": int(self._life_motion_debug.get("error_count") or 0) + 1,
                    "last_error": str(exc),
            }
            stop_event.wait(interval_s)
        if self.vts_driver is not None and hasattr(self.vts_driver, "move_model"):
            try:
                if any(abs(float(previous_model_move[key])) > 0.0001 for key in previous_model_move):
                    self.vts_driver.move_model(
                        position_x=-previous_model_move["x"],
                        position_y=-previous_model_move["y"],
                        rotation=-previous_model_move["rotation"],
                        size=0.0,
                        relative=True,
                        move_time_s=0.2,
                    )
                    self._life_motion_model_offset = {"x": 0.0, "y": 0.0, "rotation": 0.0}
            except Exception:
                pass
        self._life_motion_debug = {
            **self._life_motion_debug,
            "state": "stopped",
            "started": False,
        }

    def _start_mouth_sync(self, synth_event: dict[str, Any], *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        if self.vts_driver is None:
            debug = {
                "state": "skipped",
                "started": False,
                "reason": "vts_driver_unavailable",
            }
            self._mouth_sync_debug = dict(debug)
            return debug
        audio_data = synth_event.get("audio_data")
        sample_rate_raw = synth_event.get("sample_rate")
        try:
            sample_rate = int(sample_rate_raw)
        except (TypeError, ValueError):
            sample_rate = 0
        envelope = self._build_mouth_envelope(audio_data, sample_rate)
        debug = {
            "state": "prepared",
            "started": False,
            "request_id": str(payload.get("request_id") or ""),
            "session_id": str(payload.get("session_id") or ""),
            "audio_type": type(audio_data).__name__,
            "sample_rate": sample_rate,
            "envelope_points": len(envelope),
            "source": "synth_event.audio_data",
        }
        if not envelope:
            debug["state"] = "skipped"
            debug["reason"] = "empty_envelope"
            self._mouth_sync_debug = dict(debug)
            LOGGER.warning(
                "VTS mouth sync skipped: empty_envelope (synth_event has no audio_data or sample_rate; "
                "ensure Edge TTS returns audio_data, e.g. SoVITS synthesizer)"
            )
            return debug
        debug["envelope_min"] = round(min(envelope), 4)
        debug["envelope_max"] = round(max(envelope), 4)
        debug["envelope_avg"] = round(sum(envelope) / len(envelope), 4)
        envelope_config = _mouth_envelope_config()
        debug["envelope_config"] = {
            key: round(float(value), 4)
            for key, value in envelope_config.items()
        }
        payload = dict(payload or {})
        motion_config = _speech_motion_config(payload)
        avatar_backend = str(getattr(self.vts_driver, "backend", "") or "").strip().lower()
        vrm_lightweight_mouth = avatar_backend == "vrm" and _parse_bool_env(
            "EDGE_VRM_MOUTH_SYNC_LIGHTWEIGHT",
            default=True,
        )
        vrm_lightweight_allows_motion = _parse_bool_env(
            "EDGE_VRM_MOUTH_SYNC_LIGHTWEIGHT_ALLOW_SPEECH_MOTION",
            default=True,
        )
        if vrm_lightweight_mouth and not vrm_lightweight_allows_motion:
            motion_config["enabled"] = False
        motion_config["vrm_lightweight_motion_allowed"] = bool(vrm_lightweight_allows_motion)
        motion_config["frame_count"] = max(1, len(envelope))
        debug["motion_config"] = {
            key: (bool(value) if isinstance(value, bool) else round(float(value), 4))
            for key, value in motion_config.items()
        }
        debug["avatar_backend"] = avatar_backend or "unknown"
        debug["vrm_lightweight_mouth"] = bool(vrm_lightweight_mouth)
        if isinstance(payload.get("_display_text_debug"), dict):
            debug["display_text"] = dict(payload.get("_display_text_debug") or {})
        debug["motion_emotion_tag"] = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
        probe_value = max(0.18, min(0.92, float(envelope[0])))
        debug["probe_value"] = round(probe_value, 4)
        skip_probe = avatar_backend == "vrm" and _parse_bool_env(
            "EDGE_VRM_MOUTH_SYNC_SKIP_PROBE",
            default=True,
        )
        debug["probe_skipped"] = bool(skip_probe)
        if not skip_probe:
            probe_result = self.vts_driver.set_mouth_open_level(
                probe_value,
                _include_companions=True,
                _batch_companions=True,
            )
            if not probe_result.success:
                debug["state"] = "blocked"
                debug["reason"] = "mouth_param_unwritable"
                debug["detail"] = probe_result.detail
                self._mouth_sync_debug = dict(debug)
                LOGGER.warning(
                    "VTS mouth sync blocked: %s",
                    probe_result.detail,
                )
                return debug
            self.vts_driver.set_mouth_open_level(
                0.0,
                _include_companions=True,
                _batch_companions=True,
            )

        stop_event = self._mouth_sync_stop
        frame_interval_s = max(0.016, min(0.1, 1.0 / max(1.0, float(envelope_config["fps"]))))
        debug["frame_interval_s"] = round(frame_interval_s, 4)
        debug["state"] = "running"
        debug["started"] = True
        debug["frames_sent"] = 0
        debug["error_count"] = 0
        debug["per_frame_companions"] = not vrm_lightweight_mouth
        debug["per_frame_batch"] = not vrm_lightweight_mouth or bool(motion_config["enabled"])
        debug["motion_enabled"] = bool(motion_config["enabled"])
        motion_every_n_frames = max(1, int(float(motion_config.get("motion_every_n_frames") or 1)))
        debug["motion_every_n_frames"] = motion_every_n_frames
        if bool(motion_config["enabled"]):
            debug["motion_parameter_count"] = len(
                self._speech_motion_values(
                    frame_index=0,
                    mouth_value=probe_value,
                    fps=float(envelope_config["fps"]),
                    config=motion_config,
                )
            )
        else:
            debug["motion_parameter_count"] = 0
        debug["motion_frames_sent"] = 0
        debug["error_samples"] = []
        debug["last_value"] = 0.0
        self._mouth_sync_debug = dict(debug)

        def _runner() -> None:
            started_at = time.monotonic()
            for frame_index, value in enumerate(envelope):
                if stop_event.is_set():
                    break
                target_ts = started_at + frame_index * frame_interval_s
                wait_s = target_ts - time.monotonic()
                if wait_s > 0:
                    time.sleep(wait_s)
                if stop_event.is_set():
                    break
                should_send_motion = (
                    bool(motion_config["enabled"])
                    and (
                        frame_index == 0
                        or frame_index == len(envelope) - 1
                        or frame_index % motion_every_n_frames == 0
                    )
                )
                result = self.vts_driver.set_mouth_open_level(
                    value,
                    _include_companions=not vrm_lightweight_mouth,
                    _batch_companions=not vrm_lightweight_mouth or should_send_motion,
                    _motion_values=(
                        self._speech_motion_values(
                            frame_index=frame_index,
                            mouth_value=float(value),
                            fps=float(envelope_config["fps"]),
                            config=motion_config,
                        )
                        if should_send_motion
                        else None
                    ),
                )
                debug["frames_sent"] = int(debug.get("frames_sent") or 0) + 1
                debug["last_value"] = round(float(value), 4)
                if should_send_motion:
                    debug["motion_frames_sent"] = int(debug.get("motion_frames_sent") or 0) + 1
                if not result.success:
                    debug["error_count"] = int(debug.get("error_count") or 0) + 1
                    debug["last_error"] = result.detail
                    samples = list(debug.get("error_samples") or [])
                    if len(samples) < 5:
                        samples.append(
                            {
                                "frame_index": frame_index,
                                "motion": bool(should_send_motion),
                                "detail": str(result.detail or ""),
                            }
                        )
                        debug["error_samples"] = samples
                else:
                    debug.pop("last_error", None)
                frames_sent = int(debug.get("frames_sent") or 0)
                if frames_sent == 1 or frames_sent % 6 == 0:
                    self._mouth_sync_debug = dict(debug)
            if not stop_event.is_set():
                self.vts_driver.set_mouth_open_level(
                    0.0,
                    _include_companions=not vrm_lightweight_mouth,
                    _batch_companions=not vrm_lightweight_mouth or bool(motion_config["enabled"]),
                    _motion_values=(
                        self._speech_motion_values(
                            frame_index=len(envelope),
                            mouth_value=0.0,
                            fps=float(envelope_config["fps"]),
                            config={**motion_config, "enabled": False},
                        )
                        if bool(motion_config["enabled"])
                        else None
                    ),
                )
                debug["state"] = "finished"
            else:
                debug["state"] = "stopped"
            self._mouth_sync_debug = dict(debug)

        plan = getattr(self.vts_driver, "_mouth_parameter_plan", None) or {}
        param_names = list(plan.get("selected") or plan.get("mouth") or [])
        LOGGER.info(
            "mouth sync started: envelope_points=%s param_names=%s",
            len(envelope), param_names or "(fallback)",
        )
        self._mouth_sync_thread = threading.Thread(
            target=_runner,
            name="edge-vts-mouth-sync",
            daemon=True,
        )
        self._mouth_sync_thread.start()
        return dict(debug)

    @staticmethod
    def _life_motion_values(
        *,
        frame_index: int,
        elapsed_s: float,
        config: dict[str, Any],
        emotion_tag: str,
    ) -> tuple[dict[str, float], str]:
        cycle_s = max(1.0, float(config.get("cycle_s") or 32.0))
        progress = _clamp01((float(elapsed_s) % cycle_s) / cycle_s)
        gain = float(config.get("gain") or 1.0)
        body_degrees = float(config.get("body_degrees") or 0.0) * gain
        body_position = float(config.get("body_position") or 0.0) * gain
        hand_position = float(config.get("hand_position") or 0.0) * gain
        expression_gain = float(config.get("expression_gain") or 1.0) * gain
        face_angle_gain = float(config.get("face_angle_gain") or 1.0) * gain
        emotion = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        energy = 1.0
        if emotion in {"HAPPY", "JOY", "PLAYFUL", "EXCITED", "EXCITEMENT", "LOVE", "LOVING"}:
            energy = 1.18
        elif emotion in {"SAD", "SADNESS", "CALM", "SERIOUS", "SOOTHING"}:
            energy = 0.72

        t = float(elapsed_s)
        breathe = math.sin(t * 0.65)
        tiny = math.sin(t * 1.35 + 0.4)
        glance_left = _motion_window(progress, 0.12, 0.22, softness=0.035)
        glance_right = _motion_window(progress, 0.34, 0.45, softness=0.04)
        lean_in = _motion_window(progress, 0.52, 0.64, softness=0.045)
        small_wave = _motion_window(progress, 0.72, 0.84, softness=0.04) * energy
        settle = _motion_window(progress, 0.88, 0.98, softness=0.04)
        phase = "breathe"
        if glance_left > 0.15:
            phase = "glance_left"
        elif glance_right > 0.15:
            phase = "glance_right"
        elif lean_in > 0.15:
            phase = "lean_in"
        elif small_wave > 0.15:
            phase = "small_wave"
        elif settle > 0.15:
            phase = "settle"

        yaw = (glance_right - glance_left) * 3.8 + math.sin(t * 0.42) * 0.9
        pitch = breathe * 0.75 + lean_in * 1.2 - settle * 0.35
        roll = math.sin(t * 0.31 + 1.1) * 0.55 + (glance_left - glance_right) * 0.45
        values: dict[str, float] = {
            "FaceAngleX": round(pitch * face_angle_gain, 4),
            "FaceAngleY": round(yaw * face_angle_gain, 4),
            "FaceAngleZ": round(roll * face_angle_gain, 4),
        }
        if not bool(config.get("bound_parameter_only")):
            values.update(
                {
                    "FacePositionX": round((math.sin(t * 0.33 + 0.6) * 0.018 + (glance_right - glance_left) * 0.018) * gain, 4),
                    "FacePositionY": round((breathe * 0.018 + lean_in * 0.018) * gain, 4),
                    "FacePositionZ": round((lean_in * 0.035 + tiny * 0.006 - settle * 0.012) * gain, 4),
                }
            )
        if bool(config.get("body_enabled")) and not bool(config.get("bound_parameter_only")):
            values.update(
                {
                    "MocopiConnected": 1.0,
                    "MocopiBodyAngleX": round((pitch * 0.7 + lean_in * 1.0) * body_degrees, 4),
                    "MocopiBodyAngleY": round((yaw * 0.55 + (glance_right - glance_left) * 0.7) * body_degrees, 4),
                    "MocopiBodyAngleZ": round((roll * 0.6 + (glance_left - glance_right) * 0.55) * body_degrees, 4),
                    "MocopiBodyPositionX": round((math.sin(t * 0.28) * 0.22 + (glance_right - glance_left) * 0.18) * body_position, 4),
                    "MocopiBodyPositionY": round((breathe * 0.22 + lean_in * 0.18) * body_position, 4),
                    "MocopiBodyPositionZ": round((lean_in * 0.38 + math.sin(t * 0.37) * 0.1 - settle * 0.12) * body_position, 4),
                    "MocopiHipAngleZ": round((roll * 0.35 + (glance_left - glance_right) * 0.3) * body_degrees, 4),
                }
            )
        if bool(config.get("hand_enabled")) and not bool(config.get("bound_parameter_only")):
            wave = small_wave
            rest_left_y = hand_position * 0.22
            rest_right_y = hand_position * 0.24
            values.update(
                {
                    "BothHandsFound": 1.0,
                    "HandLeftFound": 1.0,
                    "HandRightFound": 1.0,
                    "HandDistance": round(0.52 + wave * 0.12 + lean_in * 0.08, 4),
                    "HandLeftPositionX": round(-hand_position * 0.48 + math.sin(t * 0.5) * 0.025, 4),
                    "HandLeftPositionY": round(rest_left_y + math.sin(t * 0.72 + 1.5) * 0.025 + glance_right * 0.05, 4),
                    "HandLeftPositionZ": round(math.sin(t * 0.42 + 0.8) * 0.035, 4),
                    "HandRightPositionX": round(hand_position * 0.48 + math.sin(t * 0.48 + 0.3) * 0.025, 4),
                    "HandRightPositionY": round(rest_right_y + wave * 0.28 + lean_in * 0.05 + math.sin(t * 0.8) * 0.025, 4),
                    "HandRightPositionZ": round(wave * 0.07 + math.sin(t * 0.43) * 0.035, 4),
                    "HandLeftAngleZ": round((-0.2 + glance_right * 0.25 + math.sin(t * 0.64) * 0.08) * 18.0 * gain, 4),
                    "HandRightAngleZ": round((0.18 - wave * 0.85 - glance_left * 0.2 + math.sin(t * 0.7 + 0.5) * 0.08) * 18.0 * gain, 4),
                    "HandLeftOpen": round(max(0.15, min(1.0, 0.68 + math.sin(t * 0.9) * 0.08)), 4),
                    "HandRightOpen": round(max(0.15, min(1.0, 0.7 + wave * 0.12 + math.sin(t * 1.0 + 0.4) * 0.08)), 4),
                }
            )
        if bool(config.get("expression_enabled")):
            smile_base = 0.18 if energy >= 1.0 else 0.08
            smile = max(0.0, min(1.0, smile_base + small_wave * 0.16 + lean_in * 0.1 + math.sin(t * 0.23) * 0.04))
            values.update(
                {
                    "MouthSmile": round(smile * expression_gain, 4),
                    "Brows": round((0.06 + lean_in * 0.16 + small_wave * 0.08 - settle * 0.05) * expression_gain, 4),
                    "EyeOpenLeft": round(max(0.55, min(1.0, 0.82 + tiny * 0.04 - lean_in * 0.04)), 4),
                    "EyeOpenRight": round(max(0.55, min(1.0, 0.82 + math.sin(t * 1.28 + 0.8) * 0.04 - lean_in * 0.04)), 4),
                    "EyeLeftX": round((glance_right - glance_left) * 0.18 + math.sin(t * 0.21) * 0.025, 4),
                    "EyeRightX": round((glance_right - glance_left) * 0.18 + math.sin(t * 0.21) * 0.025, 4),
                    "EyeLeftY": round(lean_in * 0.08 + breathe * 0.025, 4),
                    "EyeRightY": round(lean_in * 0.08 + breathe * 0.025, 4),
                    "CheekPuff": round(max(0.0, small_wave * 0.08 * expression_gain), 4),
                }
            )
        return values, phase

    @staticmethod
    def _body_action_values(
        *,
        action_id: str,
        frame_index: int,
        progress: float,
        elapsed_s: float,
        config: dict[str, Any],
        emotion_tag: str,
    ) -> tuple[dict[str, float], str]:
        action = _normalize_body_action_id(action_id) or "idle_shift"
        gain = float(config.get("gain") or 1.0)
        intensity = float(config.get("intensity") or 0.65)
        energy = 0.72 + intensity * 0.72
        emotion = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        if emotion in {"HAPPY", "JOY", "PLAYFUL", "EXCITED", "EXCITEMENT", "LOVE", "LOVING", "AFFECTION"}:
            energy *= 1.18
        elif emotion in {"SAD", "SADNESS", "CALM", "SERIOUS", "SOOTHING", "NOSTALGIC"}:
            energy *= 0.72
        elif emotion in {"ANXIOUS", "ANGRY", "EMBARRASSED"}:
            energy *= 0.95

        t = float(elapsed_s)
        p = _clamp01(progress)
        ease = math.sin(math.pi * p)
        settle = 1.0 - _smoothstep(0.76, 1.0, p)
        pulse_a = _motion_pulse(p, 0.28, 0.22)
        pulse_b = _motion_pulse(p, 0.58, 0.24)
        body_degrees = float(config.get("body_degrees") or 0.0) * gain * energy
        body_position = float(config.get("body_position") or 0.0) * gain * energy
        hand_position = float(config.get("hand_position") or 0.0) * gain
        hand_angle = float(config.get("hand_angle_degrees") or 0.0) * gain * energy
        expression_gain = float(config.get("expression_gain") or 1.0) * gain

        face_pitch = math.sin(t * 1.9 + 0.2) * 0.25 * ease
        face_yaw = math.sin(t * 1.25 + 0.5) * 0.18 * ease
        face_roll = math.sin(t * 1.45 + 1.1) * 0.16 * ease
        body_pitch = 0.0
        body_yaw = 0.0
        body_roll = 0.0
        body_x = 0.0
        body_y = 0.0
        body_z = 0.0
        right_raise = 0.0
        left_raise = 0.0
        right_open = 0.72
        left_open = 0.72
        hand_distance = 0.54
        smile = 0.14
        brows = 0.06
        cheek = 0.0
        eye_x = math.sin(t * 0.9) * 0.025 * ease
        eye_y = math.sin(t * 0.75 + 0.5) * 0.018 * ease
        phase = action

        if action == "wave":
            wave = _motion_window(p, 0.12, 0.88, softness=0.1)
            flutter = math.sin(p * math.pi * 7.0)
            right_raise = wave * (0.78 + 0.08 * flutter)
            right_open = 0.82 + wave * 0.12
            hand_distance = 0.58 + wave * 0.12
            body_yaw = -0.12 * wave
            body_roll = -0.16 * wave + 0.08 * flutter * wave
            face_yaw = -0.18 * wave + 0.08 * flutter * wave
            smile = 0.24 + wave * 0.28
            brows = 0.08 + wave * 0.12
            phase = "wave_raise" if p < 0.32 else ("wave_swing" if p < 0.78 else "wave_settle")
        elif action == "lean_in":
            lean = _motion_window(p, 0.06, 0.86, softness=0.14)
            body_pitch = 0.38 * lean
            body_z = 0.72 * lean
            body_y = 0.22 * lean
            face_pitch = 0.3 * lean + math.sin(t * 1.8) * 0.08
            right_raise = 0.26 * lean
            left_raise = 0.18 * lean
            smile = 0.2 + lean * 0.2
            brows = 0.08 + lean * 0.18
            eye_y = 0.08 * lean
            phase = "lean_in"
        elif action == "emphasis":
            hit = max(pulse_a, pulse_b)
            body_pitch = 0.3 * hit
            body_y = 0.45 * hit
            body_z = 0.38 * hit
            face_pitch = 0.26 * hit + math.sin(t * 3.2) * 0.08 * ease
            right_raise = max(0.18 * ease, 0.58 * hit)
            left_raise = max(0.12 * ease, 0.35 * pulse_b)
            right_open = 0.68 - hit * 0.18
            smile = 0.18 + hit * 0.22
            brows = 0.1 + hit * 0.24
            phase = "emphasis_hit" if hit > 0.45 else "emphasis_prepare"
        elif action == "nod":
            nod = math.sin(p * math.pi * 4.0) * ease
            body_pitch = 0.12 * nod + 0.12 * ease
            face_pitch = 0.46 * nod + 0.1 * ease
            smile = 0.16 + 0.12 * ease
            brows = 0.08 + 0.08 * ease
            phase = "nod"
        elif action == "look_around":
            left = _motion_window(p, 0.1, 0.42, softness=0.08)
            right = _motion_window(p, 0.48, 0.82, softness=0.08)
            face_yaw = (right - left) * 0.65
            body_yaw = (right - left) * 0.34
            body_x = (right - left) * 0.18
            eye_x = (right - left) * 0.28
            face_roll = (left - right) * 0.12
            smile = 0.12 + 0.08 * ease
            brows = 0.08 + 0.12 * max(left, right)
            phase = "look_left" if left > right else ("look_right" if right > 0.1 else "look_settle")
        elif action == "happy_bounce":
            bounce = max(0.0, math.sin(p * math.pi * 5.0)) * ease
            body_y = 0.48 * bounce
            body_z = 0.16 * ease
            body_roll = math.sin(p * math.pi * 2.0) * 0.24 * ease
            face_pitch = 0.18 * bounce
            right_raise = 0.28 + bounce * 0.3
            left_raise = 0.22 + bounce * 0.24
            hand_distance = 0.58 + bounce * 0.08
            smile = 0.34 + bounce * 0.26
            brows = 0.12 + bounce * 0.12
            cheek = bounce * 0.12
            phase = "happy_bounce"
        elif action == "shy":
            turn = _motion_window(p, 0.08, 0.8, softness=0.13)
            body_yaw = -0.34 * turn
            face_yaw = -0.44 * turn
            face_roll = 0.18 * turn
            body_z = -0.12 * turn
            right_raise = 0.24 * turn
            left_raise = 0.32 * turn
            hand_distance = 0.42
            smile = 0.16 + 0.14 * turn
            brows = -0.02 + 0.08 * turn
            eye_x = -0.14 * turn
            eye_y = -0.05 * turn
            cheek = 0.16 * turn
            phase = "shy_turn"
        elif action == "settle":
            calm = settle * ease
            body_pitch = -0.08 * calm
            body_y = -0.12 * calm
            face_pitch = -0.08 * calm
            smile = 0.08 + 0.04 * calm
            brows = 0.03
            phase = "settle"
        else:
            shift = _motion_window(p, 0.08, 0.88, softness=0.12)
            body_yaw = math.sin(p * math.pi * 2.0) * 0.18 * shift
            body_roll = math.sin(p * math.pi * 1.5 + 0.6) * 0.16 * shift
            body_x = math.sin(p * math.pi * 2.0) * 0.16 * shift
            face_yaw = math.sin(p * math.pi * 2.0 + 0.4) * 0.22 * shift
            right_raise = 0.12 * shift
            left_raise = 0.1 * shift
            smile = 0.12 + 0.08 * shift
            phase = "idle_shift"

        values: dict[str, float] = {
            "FaceAngleX": round(face_pitch * body_degrees, 4),
            "FaceAngleY": round(face_yaw * body_degrees, 4),
            "FaceAngleZ": round(face_roll * body_degrees, 4),
            "FacePositionX": round(body_x * body_position * 0.7, 4),
            "FacePositionY": round((body_y * 0.55 + math.sin(t * 1.4) * 0.025 * ease) * body_position, 4),
            "FacePositionZ": round((body_z * 0.65 + math.sin(t * 1.1 + 0.8) * 0.018 * ease) * body_position, 4),
        }
        if bool(config.get("body_enabled")) and not bool(config.get("bound_parameter_only")):
            values.update(
                {
                    "MocopiConnected": 1.0,
                    "MocopiAngleX": round(body_pitch * body_degrees * 0.35, 4),
                    "MocopiAngleY": round(body_yaw * body_degrees * 0.35, 4),
                    "MocopiAngleZ": round(body_roll * body_degrees * 0.35, 4),
                    "MocopiBodyAngleX": round(body_pitch * body_degrees, 4),
                    "MocopiBodyAngleY": round(body_yaw * body_degrees, 4),
                    "MocopiBodyAngleZ": round(body_roll * body_degrees, 4),
                    "MocopiBodyPositionX": round(body_x * body_position, 4),
                    "MocopiBodyPositionY": round(body_y * body_position, 4),
                    "MocopiBodyPositionZ": round(body_z * body_position, 4),
                    "MocopiHipAngleZ": round(body_roll * body_degrees * 0.52, 4),
                    "MocopiUpperArmLeftAngleY": round((left_raise * 0.6 - body_yaw * 0.16) * hand_angle, 4),
                    "MocopiUpperArmLeftAngleZ": round((left_raise * 0.46 + body_roll * 0.12) * hand_angle, 4),
                    "MocopiUpperArmRightAngleY": round((-right_raise * 0.72 - body_yaw * 0.16) * hand_angle, 4),
                    "MocopiUpperArmRightAngleZ": round((-right_raise * 0.54 - body_roll * 0.12) * hand_angle, 4),
                    "MocopiLowerArmLeftAngleX": round(left_raise * hand_angle * 0.55, 4),
                    "MocopiLowerArmRightAngleX": round(right_raise * hand_angle * 0.62, 4),
                }
            )
        if bool(config.get("hand_enabled")) and not bool(config.get("bound_parameter_only")):
            values.update(
                {
                    "BothHandsFound": 1.0,
                    "HandLeftFound": 1.0,
                    "HandRightFound": 1.0,
                    "HandDistance": round(max(0.16, min(1.15, hand_distance)), 4),
                    "HandLeftPositionX": round(-hand_position * hand_distance + body_x * 0.04, 4),
                    "HandLeftPositionY": round(hand_position * (0.22 + left_raise * 0.42), 4),
                    "HandLeftPositionZ": round(body_z * 0.05 + left_raise * 0.04 + math.sin(t * 1.1) * 0.02 * ease, 4),
                    "HandRightPositionX": round(hand_position * hand_distance + body_x * 0.04, 4),
                    "HandRightPositionY": round(hand_position * (0.22 + right_raise * 0.48), 4),
                    "HandRightPositionZ": round(body_z * 0.05 + right_raise * 0.06 + math.sin(t * 1.25 + 0.6) * 0.02 * ease, 4),
                    "HandLeftAngleX": round(math.sin(t * 2.0 + 0.8) * hand_angle * 0.08 * ease, 4),
                    "HandLeftAngleZ": round((-0.18 + left_raise * 0.72 + body_roll * 0.12) * hand_angle, 4),
                    "HandRightAngleX": round(math.sin(t * 2.1 + 1.5) * hand_angle * 0.08 * ease, 4),
                    "HandRightAngleZ": round((0.18 - right_raise * 0.82 - body_roll * 0.12) * hand_angle, 4),
                    "HandLeftOpen": round(max(0.1, min(1.0, left_open)), 4),
                    "HandRightOpen": round(max(0.1, min(1.0, right_open)), 4),
                }
            )
        if bool(config.get("expression_enabled")):
            blink = _motion_pulse(p, 0.08, 0.04) + _motion_pulse(p, 0.72, 0.05)
            values.update(
                {
                    "MouthSmile": round(max(0.0, min(1.0, smile)) * expression_gain, 4),
                    "Brows": round(brows * expression_gain, 4),
                    "EyeOpenLeft": round(max(0.45, min(1.0, 0.84 - blink * 0.22 + ease * 0.04)), 4),
                    "EyeOpenRight": round(max(0.45, min(1.0, 0.84 - blink * 0.2 + ease * 0.04)), 4),
                    "EyeLeftX": round(eye_x, 4),
                    "EyeRightX": round(eye_x, 4),
                    "EyeLeftY": round(eye_y, 4),
                    "EyeRightY": round(eye_y, 4),
                    "CheekPuff": round(max(0.0, min(1.0, cheek)) * expression_gain, 4),
                }
            )
        if p >= 0.995:
            for key in (
                "MocopiUpperArmLeftAngleY",
                "MocopiUpperArmLeftAngleZ",
                "MocopiUpperArmRightAngleY",
                "MocopiUpperArmRightAngleZ",
                "MocopiLowerArmLeftAngleX",
                "MocopiLowerArmRightAngleX",
            ):
                if key in values:
                    values[key] = round(values[key] * 0.35, 4)
        _ = frame_index
        return values, phase

    @staticmethod
    def _life_model_move(
        *,
        elapsed_s: float,
        config: dict[str, Any],
        emotion_tag: str,
    ) -> dict[str, float]:
        gain = float(config.get("gain") or 1.0)
        position = float(config.get("model_move_position") or 0.0) * gain
        rotation = float(config.get("model_move_rotation") or 0.0) * gain
        emotion = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        energy = 1.0
        if emotion in {"HAPPY", "JOY", "PLAYFUL", "EXCITED", "EXCITEMENT", "LOVE", "LOVING"}:
            energy = 1.16
        elif emotion in {"SAD", "SADNESS", "CALM", "SERIOUS", "SOOTHING"}:
            energy = 0.72
        t = float(elapsed_s)
        return {
            "x": round(math.sin(t * 0.42 + 0.6) * position * 0.55 * energy, 5),
            "y": round(math.sin(t * 0.68) * position * 0.38 * energy, 5),
            "rotation": round(math.sin(t * 0.31 + 1.1) * rotation * 0.42 * energy, 4),
        }

    @staticmethod
    def _body_action_model_move(
        *,
        action_id: str,
        progress: float,
        elapsed_s: float,
        config: dict[str, Any],
    ) -> dict[str, float]:
        action = _normalize_body_action_id(action_id) or "idle_shift"
        p = _clamp01(progress)
        ease = math.sin(math.pi * p)
        t = float(elapsed_s)
        position = float(config.get("model_move_position") or 0.0) * float(config.get("gain") or 1.0)
        rotation = float(config.get("model_move_rotation") or 0.0) * float(config.get("gain") or 1.0)
        x = math.sin(t * 1.1 + 0.4) * position * 0.12 * ease
        y = math.sin(t * 1.3) * position * 0.08 * ease
        rot = math.sin(t * 1.0 + 0.7) * rotation * 0.1 * ease
        if action == "wave":
            wave = _motion_window(p, 0.12, 0.88, softness=0.1)
            x += -position * 0.26 * wave
            y += position * 0.1 * wave + math.sin(p * math.pi * 6.0) * position * 0.04 * wave
            rot += -rotation * 0.38 * wave + math.sin(p * math.pi * 5.0) * rotation * 0.12 * wave
        elif action == "lean_in":
            lean = _motion_window(p, 0.06, 0.86, softness=0.14)
            y += position * 0.28 * lean
            rot += math.sin(p * math.pi) * rotation * 0.1
        elif action == "emphasis":
            hit = max(_motion_pulse(p, 0.28, 0.22), _motion_pulse(p, 0.58, 0.24))
            y += position * 0.32 * hit
            rot += rotation * 0.22 * hit
        elif action == "nod":
            y += math.sin(p * math.pi * 4.0) * position * 0.18 * ease
            rot += math.sin(p * math.pi * 4.0) * rotation * 0.12 * ease
        elif action == "look_around":
            left = _motion_window(p, 0.1, 0.42, softness=0.08)
            right = _motion_window(p, 0.48, 0.82, softness=0.08)
            x += (right - left) * position * 0.34
            rot += (right - left) * rotation * 0.55
        elif action == "happy_bounce":
            bounce = max(0.0, math.sin(p * math.pi * 5.0)) * ease
            y += position * 0.36 * bounce
            rot += math.sin(p * math.pi * 2.0) * rotation * 0.26 * ease
        elif action == "shy":
            turn = _motion_window(p, 0.08, 0.8, softness=0.13)
            x += -position * 0.18 * turn
            y += -position * 0.08 * turn
            rot += rotation * 0.32 * turn
        elif action == "settle":
            y -= position * 0.08 * ease
            rot *= 0.2
        else:
            x += math.sin(p * math.pi * 2.0) * position * 0.18 * ease
            rot += math.sin(p * math.pi * 1.5) * rotation * 0.22 * ease
        return {
            "x": round(x, 5),
            "y": round(y, 5),
            "rotation": round(rot, 4),
        }

    @staticmethod
    def _speech_motion_values(
        *,
        frame_index: int,
        mouth_value: float,
        fps: float,
        config: dict[str, Any],
    ) -> dict[str, float]:
        face_motion_enabled = bool(config.get("face_motion_enabled", True)) and not bool(config.get("body_only"))
        if not bool(config.get("enabled")):
            reset_values: dict[str, float] = {}
            if face_motion_enabled:
                reset_values.update(
                    {
                        "FaceAngleX": 0.0,
                        "FaceAngleY": 0.0,
                        "FaceAngleZ": 0.0,
                        "FacePositionX": 0.0,
                        "FacePositionY": 0.0,
                        "FacePositionZ": 0.0,
                    }
                )
            if bool(config.get("full_body_enabled")):
                reset_values.update(
                    {
                        "MocopiConnected": 0.0,
                        "MocopiHipAngleZ": 0.0,
                        "MocopiAngleX": 0.0,
                        "MocopiAngleY": 0.0,
                        "MocopiAngleZ": 0.0,
                        "MocopiBodyAngleX": 0.0,
                        "MocopiBodyAngleY": 0.0,
                        "MocopiBodyAngleZ": 0.0,
                        "MocopiBodyPositionX": 0.0,
                        "MocopiBodyPositionY": 0.0,
                        "MocopiBodyPositionZ": 0.0,
                        "MocopiUpperArmLeftAngleY": 0.0,
                        "MocopiUpperArmLeftAngleZ": 0.0,
                        "MocopiUpperArmRightAngleY": 0.0,
                        "MocopiUpperArmRightAngleZ": 0.0,
                        "MocopiLowerArmLeftAngleX": 0.0,
                        "MocopiLowerArmLeftAngleY": 0.0,
                        "MocopiLowerArmLeftAngleZ": 0.0,
                        "MocopiLowerArmRightAngleX": 0.0,
                        "MocopiLowerArmRightAngleY": 0.0,
                        "MocopiLowerArmRightAngleZ": 0.0,
                    }
                )
            if bool(config.get("hand_enabled")):
                reset_values.update(
                    {
                        "BothHandsFound": 0.0,
                        "HandLeftFound": 0.0,
                        "HandRightFound": 0.0,
                        "HandLeftPositionX": 0.0,
                        "HandLeftPositionY": 0.0,
                        "HandLeftPositionZ": 0.0,
                        "HandRightPositionX": 0.0,
                        "HandRightPositionY": 0.0,
                        "HandRightPositionZ": 0.0,
                        "HandLeftAngleX": 0.0,
                        "HandLeftAngleZ": 0.0,
                        "HandRightAngleX": 0.0,
                        "HandRightAngleZ": 0.0,
                        "HandLeftOpen": 0.0,
                        "HandRightOpen": 0.0,
                        "HandDistance": 0.0,
                    }
                )
            return reset_values
        t = float(frame_index) / max(1.0, float(fps))
        frame_count = max(1, int(float(config.get("frame_count") or 1)))
        progress = _clamp01(float(frame_index) / max(1.0, float(frame_count - 1)))
        gain = float(config.get("gain") or 1.0)
        energy = float(config.get("energy") or 1.0)
        tempo = float(config.get("tempo") or 1.0)
        complexity = float(config.get("action_complexity") or 0.0) if bool(config.get("action_script_enabled")) else 0.0
        mouth = max(0.0, min(1.0, float(mouth_value)))
        speech = max(0.0, min(1.0, (mouth - 0.05) / 0.95))
        beat_gain = float(config.get("beat_gain") or 1.0)
        beat = max(0.0, math.sin(t * 6.2 * tempo + 0.35)) * speech * beat_gain
        phrase = math.sin(t * 1.05 * tempo + 0.25)
        counter = math.sin(t * 1.72 * tempo + 1.4)
        opening = _motion_window(progress, 0.02, 0.2, softness=0.07) * complexity
        turn_left = _motion_window(progress, 0.24, 0.43, softness=0.06) * complexity
        turn_right = _motion_window(progress, 0.48, 0.7, softness=0.07) * complexity
        closing = _motion_window(progress, 0.78, 0.98, softness=0.08) * complexity
        emphasis = max(
            _motion_pulse(progress, 0.22, 0.13),
            _motion_pulse(progress, 0.39, 0.12),
            _motion_pulse(progress, 0.58, 0.14),
            _motion_pulse(progress, 0.76, 0.12),
        ) * speech * complexity
        nod = float(config.get("nod_degrees") or 0.0) * gain * energy
        sway = float(config.get("sway_degrees") or 0.0) * gain * energy
        tilt = float(config.get("tilt_degrees") or 0.0) * gain * energy
        pos_x = float(config.get("position_x") or 0.0) * gain * energy
        pos_y = float(config.get("position_y") or 0.0) * gain * energy
        pos_z = float(config.get("position_z") or 0.0) * gain * energy
        values: dict[str, float] = {}
        if face_motion_enabled:
            values.update(
                {
                    "FaceAngleX": round((math.sin(t * 4.4 * tempo) * 0.3 + (mouth - 0.35) * 0.55 + beat * 0.25 + emphasis * 0.32 - closing * 0.15) * nod, 4),
                    "FaceAngleY": round((phrase * 0.7 + counter * 0.2 + beat * 0.1 + (turn_right - turn_left) * 0.58) * sway, 4),
                    "FaceAngleZ": round((math.sin(t * 1.55 * tempo + 1.1) * 0.65 - phrase * 0.2 + (turn_left - turn_right) * 0.3) * tilt, 4),
                    "FacePositionX": round((math.sin(t * 1.8 * tempo + 1.7) * 0.7 + phrase * 0.25 + (turn_right - turn_left) * 0.35) * pos_x, 4),
                    "FacePositionY": round(math.sin(t * 2.4 * tempo) * pos_y * 0.65 + mouth * pos_y * 0.45 + beat * pos_y * 0.35 + (opening + emphasis * 0.65 - closing * 0.25) * pos_y, 4),
                    "FacePositionZ": round(math.sin(t * 1.2 * tempo + 0.3) * pos_z * 0.7 + mouth * pos_z * 0.35 + (opening + emphasis) * pos_z * 0.45, 4),
                }
            )
        if bool(config.get("full_body_enabled")) and bool(config.get("mocopi_enabled")):
            body_degrees = float(config.get("body_degrees") or 0.0) * gain * energy
            body_position = float(config.get("body_position") or 0.0) * gain * energy
            arm_angle = float(config.get("arm_angle_degrees") or 0.0) * gain * energy
            body_pitch = (
                math.sin(t * 2.0 * tempo + 0.2) * 0.28
                + (mouth - 0.35) * 0.42
                + beat * 0.3
                + opening * 0.52
                + emphasis * 0.42
                - closing * 0.25
            ) * body_degrees
            body_yaw = (
                phrase * 0.62
                + math.sin(t * 2.65 * tempo + 1.1) * 0.2
                + (turn_right - turn_left) * 0.72
            ) * body_degrees
            body_roll = (
                math.sin(t * 1.35 * tempo + 0.8) * 0.55
                - phrase * 0.25
                + (turn_left - turn_right) * 0.45
                + emphasis * 0.18
            ) * body_degrees
            body_bounce = (
                math.sin(t * 3.1 * tempo) * body_position * 0.22
                + speech * body_position * 0.28
                + beat * body_position * 0.34
                + emphasis * body_position * 0.5
            )
            body_lean = (
                math.sin(t * 1.18 * tempo + 0.5) * body_position * 0.35
                + mouth * body_position * 0.12
                + (opening + emphasis * 0.6 - closing * 0.2) * body_position * 0.45
            )
            right_action = max(opening * 0.75, emphasis, _motion_pulse(progress, 0.62, 0.18) * speech * complexity)
            left_action = max(_motion_pulse(progress, 0.38, 0.18), _motion_pulse(progress, 0.78, 0.16)) * speech * complexity
            values.update(
                {
                    "MocopiConnected": 1.0,
                    "MocopiHipAngleZ": round(body_roll * 0.55, 4),
                    "MocopiAngleX": round(body_pitch * 0.35, 4),
                    "MocopiAngleY": round(body_yaw * 0.35, 4),
                    "MocopiAngleZ": round(body_roll * 0.35, 4),
                    "MocopiBodyAngleX": round(body_pitch, 4),
                    "MocopiBodyAngleY": round(body_yaw, 4),
                    "MocopiBodyAngleZ": round(body_roll, 4),
                    "MocopiBodyPositionX": round(math.sin(t * 1.7 * tempo + 0.9) * body_position * 0.35, 4),
                    "MocopiBodyPositionY": round(body_bounce, 4),
                    "MocopiBodyPositionZ": round(body_lean, 4),
                }
            )
            if bool(config.get("arm_enabled")):
                values.update(
                    {
                        "MocopiUpperArmLeftAngleY": round((-0.18 + left_action * 0.75 + turn_left * 0.18) * arm_angle, 4),
                        "MocopiUpperArmLeftAngleZ": round((0.2 + left_action * 0.55 - phrase * 0.08) * arm_angle, 4),
                        "MocopiUpperArmRightAngleY": round((0.18 - right_action * 0.8 - turn_right * 0.18) * arm_angle, 4),
                        "MocopiUpperArmRightAngleZ": round((-0.22 - right_action * 0.62 + phrase * 0.08) * arm_angle, 4),
                        "MocopiLowerArmLeftAngleX": round((left_action * 0.7 + beat * 0.18) * arm_angle, 4),
                        "MocopiLowerArmLeftAngleY": round(math.sin(t * 2.0 * tempo + 1.3) * arm_angle * 0.1, 4),
                        "MocopiLowerArmLeftAngleZ": round((left_action * 0.35 - turn_right * 0.12) * arm_angle, 4),
                        "MocopiLowerArmRightAngleX": round((right_action * 0.75 + beat * 0.22) * arm_angle, 4),
                        "MocopiLowerArmRightAngleY": round(math.sin(t * 2.1 * tempo + 0.6) * arm_angle * 0.1, 4),
                        "MocopiLowerArmRightAngleZ": round((-right_action * 0.38 + turn_left * 0.12) * arm_angle, 4),
                    }
                )
        if bool(config.get("full_body_enabled")) and bool(config.get("hand_enabled")):
            hand_position = float(config.get("hand_position") or 0.0) * gain
            hand_angle = float(config.get("hand_angle_degrees") or 0.0) * gain * energy
            gesture_gain = float(config.get("gesture_gain") or 1.0)
            scripted_right = max(opening * 0.9, emphasis, _motion_pulse(progress, 0.62, 0.2) * speech * complexity)
            scripted_left = max(_motion_pulse(progress, 0.36, 0.18), _motion_pulse(progress, 0.76, 0.18)) * speech * complexity
            right_gesture = min(1.8, (max(0.0, math.sin(t * 2.4 * tempo + 0.5)) * speech + scripted_right) * gesture_gain)
            left_gesture = min(1.8, (max(0.0, math.sin(t * 2.1 * tempo + 2.1)) * speech + scripted_left) * gesture_gain)
            hand_distance = max(0.15, min(1.0, 0.55 + (right_gesture + left_gesture) * 0.12 + opening * 0.08))
            values.update(
                {
                    "BothHandsFound": 1.0,
                    "HandLeftFound": 1.0,
                    "HandRightFound": 1.0,
                    "HandDistance": round(hand_distance, 4),
                    "HandLeftPositionX": round(-hand_position * hand_distance + math.sin(t * 1.4 * tempo) * 0.045 - turn_left * 0.04, 4),
                    "HandLeftPositionY": round(hand_position * 0.32 + left_gesture * 0.26 + beat * 0.04 + turn_right * 0.08, 4),
                    "HandLeftPositionZ": round(math.sin(t * 1.15 * tempo + 1.3) * 0.07 + left_gesture * 0.045, 4),
                    "HandRightPositionX": round(hand_position * hand_distance + math.sin(t * 1.55 * tempo + 0.7) * 0.045 + turn_right * 0.04, 4),
                    "HandRightPositionY": round(hand_position * 0.3 + right_gesture * 0.32 + beat * 0.06 + opening * 0.1, 4),
                    "HandRightPositionZ": round(math.sin(t * 1.05 * tempo + 0.2) * 0.07 + right_gesture * 0.055, 4),
                    "HandLeftAngleX": round(math.sin(t * 2.25 * tempo + 0.7) * hand_angle * 0.25, 4),
                    "HandLeftAngleZ": round((-0.28 + left_gesture * 0.78 + phrase * 0.12 + turn_right * 0.16) * hand_angle, 4),
                    "HandRightAngleX": round(math.sin(t * 2.45 * tempo + 1.8) * hand_angle * 0.25, 4),
                    "HandRightAngleZ": round((0.28 - right_gesture * 0.86 - phrase * 0.12 - turn_left * 0.16) * hand_angle, 4),
                    "HandLeftOpen": round(max(0.1, min(1.0, 0.72 + math.sin(t * 3.0 * tempo) * 0.12 - left_gesture * 0.18)), 4),
                    "HandRightOpen": round(max(0.1, min(1.0, 0.74 + math.sin(t * 3.3 * tempo + 1.0) * 0.12 - right_gesture * 0.2)), 4),
                }
            )
        return values

    @staticmethod
    def _build_mouth_envelope(audio_data: Any, sample_rate: int) -> list[float]:
        if audio_data is None or sample_rate <= 0:
            return []
        samples = EdgeVoicePipeline._coerce_audio_samples(audio_data)
        if not samples:
            return []

        peak = max(samples)
        if peak <= 0.0:
            return []

        config = _mouth_envelope_config()
        fps = max(1.0, float(config["fps"]))
        gain = float(config["gain"])
        min_open = float(config["min_open"])
        silence_threshold = float(config["silence_threshold"])
        attack = float(config["attack"])
        release = float(config["release"])

        frame_size = max(1, int(sample_rate / fps))
        values: list[float] = []
        smooth = 0.0
        for start in range(0, len(samples), frame_size):
            chunk = samples[start : start + frame_size]
            if not chunk:
                continue
            amplitude = (sum(chunk) / len(chunk)) / peak
            target = max(0.0, min(1.0, amplitude * gain))
            if target <= silence_threshold:
                target = 0.0
            elif min_open > 0.0:
                target = max(min_open, target)
            smoothing = attack if target >= smooth else release
            smooth = (smooth * (1.0 - smoothing)) + (target * smoothing)
            values.append(round(max(0.0, min(1.0, smooth)), 4))
        return values

    @staticmethod
    def _coerce_audio_samples(audio_data: Any) -> list[float]:
        try:
            import numpy as np  # type: ignore[import]

            arr = np.asarray(audio_data, dtype=float)
            if arr.size == 0:
                return []
            if arr.ndim > 1:
                arr = np.mean(np.abs(arr), axis=1)
            else:
                arr = np.abs(arr)
            return [float(value) for value in arr.tolist()]
        except Exception:
            pass

        raw = audio_data
        if hasattr(raw, "tolist"):
            try:
                raw = raw.tolist()
            except Exception:
                return []
        if isinstance(raw, (bytes, bytearray, memoryview)):
            return []
        if not isinstance(raw, (list, tuple)):
            return [abs(float(raw))] if isinstance(raw, (int, float)) else []

        values: list[float] = []
        for item in raw:
            if isinstance(item, (int, float)):
                values.append(abs(float(item)))
                continue
            if isinstance(item, (list, tuple)):
                channel_values = [
                    abs(float(channel))
                    for channel in item
                    if isinstance(channel, (int, float))
                ]
                if channel_values:
                    values.append(sum(channel_values) / len(channel_values))
        return values

    @staticmethod
    def _short_log_text(value: Any, *, limit: int = 160) -> str:
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 3)]}..."

    @staticmethod
    def _format_ts_ms(ts_ms: int) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts_ms) / 1000.0))
        except Exception:
            return "unknown"

    def _log_vts_monitor(
        self,
        *,
        action: str,
        result: VtsDriverResult | None = None,
        payload: dict[str, Any] | None = None,
        ts_ms: int | None = None,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        row = dict(payload or {})
        event_ts = int(ts_ms if ts_ms is not None else row.get("ts_ms") or now_ms())
        request_id = str(row.get("request_id") or "")
        session_id = str(row.get("session_id") or "")
        seq = str(row.get("seq") or "")
        status = "SKIP"
        reason_code = None
        result_detail = ""
        emotion_tag = str(row.get("emotion_tag") or "")
        expression_id = ""
        if result is not None:
            status = "OK" if bool(result.success) else "FAIL"
            reason_code = result.reason_code
            result_detail = str(result.detail or "")
            emotion_tag = str(result.emotion_tag or emotion_tag or "")
            expression_id = str(result.expression_id or "")
        merged_detail = str(detail or result_detail or "").strip()
        extra_text = self._short_log_text(str(extra or {}), limit=220) if extra else "{}"
        LOGGER.info(
            "[EDGE_VOICE_MON][VTS] ts=%s action=%s req=%s session=%s seq=%s status=%s reason=%s emotion=%s expression=%s detail=%s extra=%s",
            self._format_ts_ms(event_ts),
            str(action or "").strip().upper(),
            request_id,
            session_id,
            seq,
            status,
            reason_code,
            emotion_tag,
            expression_id,
            merged_detail,
            extra_text,
        )

    def health(self) -> dict[str, Any]:
        thread = self._mouth_sync_thread
        life_thread = self._life_motion_thread
        return {
            "mouth_sync": dict(self._mouth_sync_debug),
            "mouth_sync_thread_alive": bool(thread is not None and thread.is_alive()),
            "life_motion": dict(self._life_motion_debug),
            "life_motion_thread_alive": bool(life_thread is not None and life_thread.is_alive()),
            "body_action_active": bool(self._body_action_active.is_set()),
            "last_body_action": dict(self._last_body_action),
            "last_vts_result": dict(self._last_vts_result),
        }

    def _resolve_asr_emotion_report(self, event: dict[str, Any]) -> dict[str, Any]:
        explicit_tag = str(event.get("emotion_tag") or "").strip().upper()
        explicit_intensity = event.get("emotion_intensity")
        if explicit_tag:
            report = {
                "emotion_tag": explicit_tag,
                "emotion_source": "edge_explicit",
                "emotion_modality": str(event.get("emotion_modality") or "explicit").strip() or "explicit",
                "emotion_confidence": 1.0,
                "emotion_inference_degraded": False,
                "acoustic_emotion_available": bool(event.get("acoustic_emotion_available", False)),
                "mood_state": str(event.get("mood_state") or "").strip().lower() or "steady",
                "matched_terms": [],
            }
            if isinstance(explicit_intensity, (int, float)):
                report["emotion_intensity"] = max(0.0, min(1.0, float(explicit_intensity)))
            else:
                report["emotion_intensity"] = 0.5
            return report
        return infer_asr_emotion(
            text=str(event.get("text") or ""),
            confidence=event.get("confidence") if isinstance(event.get("confidence"), (int, float)) else None,
            final=bool(event.get("final", False)),
        )
