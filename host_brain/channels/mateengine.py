from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from host_brain.channels.embodiment import queue_mateengine_command


DEFAULT_MATEENGINE_SIGNAL_HOST = "127.0.0.1"
DEFAULT_MATEENGINE_SIGNAL_PORT = 32145
DEFAULT_MATEENGINE_BRIDGE_PORT = 32146
DEFAULT_MATEENGINE_BRIDGE_BASE_URL = "http://127.0.0.1:18770"
NATSUME_TEXT_PREFIX = "NATSUME_TEXT::"

EVENT_MOB_PROXIMITY = "mob_proximity"
EVENT_DAY_START = "day_start"
EVENT_NIGHT_START = "night_start"
EVENT_LOW_HEALTH = "low_health"
EVENT_LOW_HUNGER = "low_hunger"
EVENT_DEATH = "death"
EVENT_RAIN_START = "rain_start"
EVENT_DROWNING = "drowning"
EVENT_SLEEP = "sleep"
EVENT_CRAFTING = "crafting"
EVENT_EAT = "eat"
EVENT_KILL_CONFIRM = "kill_confirm"
EVENT_BIOME_DISCOVERY = "biome_discovery"

SUPPORTED_SIGNAL_EVENTS = {
    EVENT_MOB_PROXIMITY,
    EVENT_DAY_START,
    EVENT_NIGHT_START,
    EVENT_LOW_HEALTH,
    EVENT_LOW_HUNGER,
    EVENT_DEATH,
    EVENT_RAIN_START,
    EVENT_DROWNING,
    EVENT_SLEEP,
    EVENT_CRAFTING,
    EVENT_EAT,
    EVENT_KILL_CONFIRM,
    EVENT_BIOME_DISCOVERY,
}

SUPPORTED_MATEENGINE_ACTIONS = {
    "accessory",
    "action_sequence",
    "animate",
    "animation",
    "choreography",
    "emote",
    "expression",
    "freeze",
    "gesture",
    "hide",
    "idle",
    "listening",
    "move",
    "native_action",
    "pose",
    "reset",
    "say",
    "sequence",
    "show",
    "speak",
    "state",
    "status",
    "stabilize",
    "thinking",
    "touch",
    "transform",
}

MATEENGINE_STATE_GESTURES = {
    "listening": "peek",
    "hearing": "peek",
    "user_speaking": "peek",
    "thinking": "nod",
    "memory_recall": "nod",
    "tool_running": "nod",
    "processing": "nod",
    "focus": "nod",
    "interrupted": "surprised",
    "barge_in": "surprised",
    "surprised": "surprised",
    "error": "shake",
    "failed": "shake",
    "anxious": "shake",
}

MATEENGINE_EMOTION_GESTURES = {
    "HAPPY": "bounce",
    "JOY": "bounce",
    "EXCITED": "jump",
    "LOVE": "bounce",
    "LOVING": "bounce",
    "PROUD": "bounce",
    "SURPRISED": "surprised",
    "CONFUSED": "nod",
    "THINKING": "nod",
    "ANXIOUS": "shake",
    "ANGRY": "shake",
    "FRUSTRATED": "shake",
}

MATEENGINE_POSE_PRESETS: dict[str, dict[str, Any]] = {
    "home": {
        "form": "",
        "value": 0.0,
        "move": (0.0, 0.0),
        "reset": True,
        "freeze": True,
        "detail": "return to stable home pose",
    },
    "normal": {
        "form": "",
        "value": 0.0,
        "move": (0.0, 0.0),
        "reset": True,
        "chibi": False,
        "freeze": True,
        "detail": "normal full-size stable pose",
    },
    "idle": {
        "form": "",
        "value": 0.0,
        "move": (0.0, 0.0),
        "reset": True,
        "freeze": True,
        "detail": "calm idle pose",
    },
    "listen": {
        "form": "Face:LookUp",
        "value": 0.5,
        "move": (-0.28, 0.16),
        "reset": True,
        "freeze": True,
        "detail": "attentive listening pose",
    },
    "listening": {
        "alias": "listen",
    },
    "think": {
        "form": "Face:LookUp",
        "value": 0.82,
        "move": (0.0, 0.22),
        "reset": True,
        "freeze": True,
        "detail": "focused thinking pose",
    },
    "thinking": {
        "alias": "think",
    },
    "focus": {
        "form": "Face:LookUp",
        "value": 0.95,
        "move": (-0.42, 0.26),
        "reset": True,
        "freeze": True,
        "detail": "strong focus pose",
    },
    "speak": {
        "form": "Face:MouthSmile",
        "value": 0.62,
        "move": (0.32, 0.08),
        "reset": True,
        "freeze": True,
        "detail": "speech-ready pose without driving mouth timing",
    },
    "speaking": {
        "alias": "speak",
    },
    "happy": {
        "form": "Face:Joy1",
        "value": 0.88,
        "move": (0.36, 0.12),
        "reset": True,
        "freeze": True,
        "detail": "bright happy pose",
    },
    "cozy": {
        "form": "Face:MouthNeko",
        "value": 0.76,
        "move": (0.42, -0.1),
        "reset": True,
        "freeze": True,
        "detail": "soft cozy desktop-pet pose",
    },
    "surprised": {
        "form": "Face:O",
        "value": 0.78,
        "move": (0.0, 0.3),
        "reset": True,
        "freeze": True,
        "detail": "short surprised pose",
    },
    "interrupted": {
        "alias": "surprised",
    },
    "anxious": {
        "form": "Face:FaceSweat1",
        "value": 0.82,
        "move": (-0.28, 0.0),
        "reset": True,
        "freeze": True,
        "detail": "anxious/error pose",
    },
    "error": {
        "alias": "anxious",
    },
    "chibi": {
        "form": "Face:MouthNeko",
        "value": 0.62,
        "move": (0.0, 0.0),
        "reset": True,
        "chibi": True,
        "scale": 2.4,
        "freeze": True,
        "detail": "optional chibi transform if MateEngine plugin path is available",
    },
    "live": {
        "form": "",
        "value": 0.0,
        "reset": False,
        "freeze": False,
        "detail": "temporarily unfreeze motion",
    },
    "unfreeze": {
        "alias": "live",
    },
}

MATEENGINE_VISUAL_STATE_POSES: dict[str, str] = {
    "idle": "home",
    "waiting": "home",
    "done": "home",
    "end": "home",
    "ended": "home",
    "reset": "home",
    "listening": "listen",
    "hearing": "listen",
    "user_speaking": "listen",
    "thinking": "think",
    "memory_recall": "think",
    "tool_running": "focus",
    "processing": "focus",
    "speaking": "speak",
    "speak": "speak",
    "say": "speak",
    "tts_start": "speak",
    "playback_start": "speak",
    "interrupted": "surprised",
    "barge_in": "surprised",
    "surprised": "surprised",
    "error": "anxious",
    "failed": "anxious",
    "anxious": "anxious",
}

STATE_TO_EMOTION = {
    "idle": "NEUTRAL",
    "listening": "NEUTRAL",
    "thinking": "THINKING",
    "memory_recall": "THINKING",
    "tool_running": "THINKING",
    "speaking": "HAPPY",
    "greet": "HAPPY",
    "happy": "HAPPY",
    "error": "ANXIOUS",
    "interrupted": "SURPRISED",
}

TOUCH_TO_EMOTION = {
    "head": "HAPPY",
    "hair": "SHY",
    "body": "THINKING",
    "poke": "SURPRISED",
    "idle": "NEUTRAL",
}


@dataclass(frozen=True)
class MateEngineSignalResult:
    ok: bool
    status: str
    host: str
    port: int
    payload: dict[str, Any]
    bytes_sent: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MateEngineBridgeResult:
    ok: bool
    status: str
    host: str
    port: int
    payload: dict[str, Any]
    bytes_sent: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MateEngineActionResult:
    ok: bool
    status: str
    backend: str
    target: str
    payload: dict[str, Any]
    results: list[dict[str, Any]] = field(default_factory=list)
    response: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mateengine_enabled() -> bool:
    raw = os.getenv("HOST_MATEENGINE_SIGNAL_ENABLED", "false").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _mateengine_host() -> str:
    return os.getenv("HOST_MATEENGINE_SIGNAL_HOST", DEFAULT_MATEENGINE_SIGNAL_HOST).strip() or DEFAULT_MATEENGINE_SIGNAL_HOST


def _mateengine_port() -> int:
    raw = os.getenv("HOST_MATEENGINE_SIGNAL_PORT", str(DEFAULT_MATEENGINE_SIGNAL_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_MATEENGINE_SIGNAL_PORT
    if port <= 0 or port > 65535:
        return DEFAULT_MATEENGINE_SIGNAL_PORT
    return port


def _mateengine_bridge_port() -> int:
    raw = os.getenv("HOST_MATEENGINE_BRIDGE_PORT", str(DEFAULT_MATEENGINE_BRIDGE_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_MATEENGINE_BRIDGE_PORT
    if port <= 0 or port > 65535:
        return DEFAULT_MATEENGINE_BRIDGE_PORT
    return port


def _mateengine_bridge_enabled() -> bool:
    raw = os.getenv("HOST_MATEENGINE_BRIDGE_ENABLED", "false").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _mateengine_bridge_base_url() -> str:
    raw = (
        os.getenv("HOST_MATEENGINE_BRIDGE_BASE_URL")
        or os.getenv("MATEENGINE_BRIDGE_BASE_URL")
        or DEFAULT_MATEENGINE_BRIDGE_BASE_URL
    )
    return raw.strip().rstrip("/") or DEFAULT_MATEENGINE_BRIDGE_BASE_URL


def _mateengine_bridge_timeout_s() -> float:
    raw = os.getenv("HOST_MATEENGINE_BRIDGE_TIMEOUT_S", "1.5").strip()
    try:
        return max(0.2, min(10.0, float(raw)))
    except ValueError:
        return 1.5


def _normalize_action(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "behavior": "state",
        "bubble": "say",
        "chat": "say",
        "chibi": "transform",
        "emotion": "emote",
        "expression_transition": "expression",
        "form": "expression",
        "motion_freeze": "freeze",
        "stable": "freeze",
        "stabilise": "freeze",
        "stabilize": "freeze",
        "stop_motion": "freeze",
        "mouth": "speak",
        "posture": "pose",
        "talk": "speak",
    }.get(normalized, normalized)


def _normalize_state(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_emotion(value: Any, *, default: str = "NEUTRAL") -> str:
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return normalized or default


def _clamp_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(minimum, min(maximum, number)), 4)


def _resolve_mateengine_pose(name: Any) -> tuple[str, dict[str, Any]]:
    pose = _normalize_state(name or "idle") or "idle"
    visited: set[str] = set()
    while True:
        if pose in visited:
            break
        visited.add(pose)
        preset = MATEENGINE_POSE_PRESETS.get(pose)
        if not isinstance(preset, dict):
            break
        alias = str(preset.get("alias") or "").strip()
        if not alias:
            return pose, dict(preset)
        pose = _normalize_state(alias)
    return "idle", dict(MATEENGINE_POSE_PRESETS["idle"])


def _mateengine_pose_commands(
    *,
    pose: str,
    intensity: float | None,
    scale: float | None,
    position_x: float | None,
    position_y: float | None,
    enabled: bool | None,
) -> list[dict[str, Any]]:
    resolved_pose, preset = _resolve_mateengine_pose(pose)
    commands: list[dict[str, Any]] = []

    if bool(preset.get("reset")):
        commands.append({"type": "reset"})

    if "chibi" in preset:
        commands.append({
            "type": "chibi",
            "enabled": bool(preset.get("chibi")) if enabled is None else bool(enabled),
            "scale": _clamp_float(
                scale if scale is not None else preset.get("scale"),
                default=float(preset.get("scale") or 2.4),
                minimum=0.5,
                maximum=4.0,
            ),
        })

    form = str(preset.get("form") or "").strip()
    if form:
        commands.append({
            "type": "form",
            "name": form,
            "preset": resolved_pose.upper(),
            "value": _clamp_float(
                intensity if intensity is not None else preset.get("value"),
                default=float(preset.get("value") or 0.6),
                minimum=0.0,
                maximum=1.0,
            ),
        })

    move_value = preset.get("move")
    if isinstance(move_value, (list, tuple)) and len(move_value) >= 2:
        default_x = move_value[0]
        default_y = move_value[1]
        commands.append({
            "type": "move_model",
            "position_x": _clamp_float(position_x, default=float(default_x), minimum=-1.0, maximum=1.0),
            "position_y": _clamp_float(position_y, default=float(default_y), minimum=-1.0, maximum=1.0),
            "relative": True,
        })
    elif position_x is not None or position_y is not None:
        commands.append({
            "type": "move_model",
            "position_x": _clamp_float(position_x, default=0.0, minimum=-1.0, maximum=1.0),
            "position_y": _clamp_float(position_y, default=0.0, minimum=-1.0, maximum=1.0),
            "relative": True,
        })

    if "freeze" in preset:
        commands.append({"type": "freeze", "enabled": bool(preset.get("freeze"))})

    for command in commands:
        command.setdefault("pose", resolved_pose)
    return commands


def _mateengine_motion_sequence_steps(gesture: str, *, duration_s: float | None = None) -> list[dict[str, Any]]:
    key = _normalize_state(gesture or "bounce")
    sequences: dict[str, list[dict[str, Any]]] = {
        "bounce": [
            {"position_x": 0.0, "position_y": 0.28, "hold_ms": 95},
            {"position_x": 0.0, "position_y": -0.08, "hold_ms": 80},
            {"position_x": 0.0, "position_y": 0.18, "hold_ms": 80},
            {"position_x": 0.0, "position_y": 0.0, "hold_ms": 120},
        ],
        "jump": [
            {"position_x": 0.0, "position_y": 0.42, "hold_ms": 110},
            {"position_x": 0.0, "position_y": -0.12, "hold_ms": 85},
            {"position_x": 0.0, "position_y": 0.0, "hold_ms": 130},
        ],
        "nod": [
            {"position_x": 0.0, "position_y": -0.16, "hold_ms": 95},
            {"position_x": 0.0, "position_y": 0.18, "hold_ms": 95},
            {"position_x": 0.0, "position_y": -0.10, "hold_ms": 85},
            {"position_x": 0.0, "position_y": 0.0, "hold_ms": 120},
        ],
        "shake": [
            {"position_x": -0.38, "position_y": 0.0, "hold_ms": 85},
            {"position_x": -0.08, "position_y": 0.0, "hold_ms": 85},
            {"position_x": -0.30, "position_y": 0.0, "hold_ms": 75},
            {"position_x": -0.05, "position_y": 0.0, "hold_ms": 75},
            {"position_x": 0.0, "position_y": 0.0, "hold_ms": 120},
        ],
        "peek": [
            {"position_x": -0.52, "position_y": 0.18, "hold_ms": 130},
            {"position_x": -0.28, "position_y": 0.10, "hold_ms": 100},
            {"position_x": 0.0, "position_y": 0.0, "hold_ms": 140},
        ],
        "surprised": [
            {"position_x": 0.0, "position_y": 0.46, "hold_ms": 110},
            {"position_x": -0.18, "position_y": 0.28, "hold_ms": 75},
            {"position_x": 0.18, "position_y": 0.28, "hold_ms": 75},
            {"position_x": 0.0, "position_y": 0.0, "hold_ms": 130},
        ],
    }
    aliases = {
        "happy": "bounce",
        "yes": "nod",
        "no": "shake",
        "listen": "peek",
        "listening": "peek",
        "focus": "nod",
        "thinking": "nod",
        "think": "nod",
        "interrupt": "surprised",
        "interrupted": "surprised",
    }
    steps = list(sequences.get(aliases.get(key, key)) or sequences["bounce"])
    if duration_s is None:
        return steps
    total_ms = sum(max(0, int(step.get("hold_ms") or 0)) for step in steps)
    if total_ms <= 0:
        return steps
    target_ms = _clamp_float(duration_s * 1000.0, default=float(total_ms), minimum=300.0, maximum=6000.0)
    scale_value = target_ms / float(total_ms)
    scaled_steps: list[dict[str, Any]] = []
    for step in steps:
        current = dict(step)
        current["hold_ms"] = max(40, int(round(float(current.get("hold_ms") or 0) * scale_value)))
        scaled_steps.append(current)
    return scaled_steps


def _mateengine_auto_gestures_enabled() -> bool:
    raw = str(os.getenv("HOST_MATEENGINE_AUTO_GESTURES_ENABLED", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _mateengine_auto_gesture_duration_s() -> float:
    raw = str(os.getenv("HOST_MATEENGINE_AUTO_GESTURE_DURATION_S", "1.2")).strip()
    try:
        return max(0.3, min(6.0, float(raw)))
    except ValueError:
        return 1.2


def _mateengine_motion_sequence_command(
    gesture: str,
    *,
    duration_s: float | None = None,
) -> dict[str, Any]:
    clean_gesture = _normalize_state(gesture or "bounce")
    return {
        "type": "motion_sequence",
        "gesture": clean_gesture,
        "pose": clean_gesture,
        "steps": _mateengine_motion_sequence_steps(clean_gesture, duration_s=duration_s),
    }


def _mateengine_choreography_command(
    name: str,
    *,
    emotion_tag: str | None = None,
    state: str | None = None,
    duration_s: float | None = None,
    include_expression: bool = False,
) -> dict[str, Any]:
    clean_name = _normalize_state(name or state or "bounce")
    clean_emotion = _normalize_emotion(emotion_tag or "")
    gesture_duration_s = duration_s if duration_s is not None else _mateengine_auto_gesture_duration_s()
    steps: list[dict[str, Any]] = []
    if include_expression and clean_emotion:
        steps.append({
            "at_ms": 0,
            "label": "expression",
            "command": {
                "type": "expression",
                "preset": clean_emotion,
                "emotion_tag": clean_emotion,
                "value": 0.82,
                "ttl": max(1.0, gesture_duration_s + 0.4),
            },
        })
    if clean_name in {"bounce", "jump", "surprised"}:
        steps.append({
            "at_ms": 0,
            "label": "mouth_open",
            "command": {"type": "mouth", "value": 0.28},
        })
        steps.append({
            "at_ms": int(max(240.0, gesture_duration_s * 1000.0 * 0.55)),
            "label": "mouth_close",
            "command": {"type": "mouth", "value": 0.0},
        })
    steps.append({
        "at_ms": 40,
        "label": "gesture_motion",
        "command": _mateengine_motion_sequence_command(clean_name, duration_s=gesture_duration_s),
    })
    return {
        "type": "action_sequence",
        "name": clean_name,
        "gesture": clean_name,
        "state": _normalize_state(state or ""),
        "emotion_tag": clean_emotion or None,
        "steps": steps,
    }


def _mateengine_auto_gesture_for_state(state: str) -> str:
    return str(MATEENGINE_STATE_GESTURES.get(_normalize_state(state)) or "").strip()


def _mateengine_auto_gesture_for_emotion(emotion_tag: str) -> str:
    return str(MATEENGINE_EMOTION_GESTURES.get(str(emotion_tag or "").strip().upper()) or "").strip()


def list_mateengine_pose_presets() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in sorted(MATEENGINE_POSE_PRESETS):
        preset = MATEENGINE_POSE_PRESETS[name]
        if "alias" in preset:
            rows.append({"pose": name, "alias": preset.get("alias")})
            continue
        rows.append({
            "pose": name,
            "form": preset.get("form") or None,
            "value": preset.get("value"),
            "move": preset.get("move"),
            "chibi": preset.get("chibi"),
            "freeze": preset.get("freeze"),
            "detail": preset.get("detail"),
        })
    return rows


def _apply_mateengine_pose_commands(
    pose: str,
    *,
    intensity: float | None = None,
    scale: float | None = None,
    position_x: float | None = None,
    position_y: float | None = None,
    enabled: bool | None = None,
) -> list[dict[str, Any]]:
    commands = _mateengine_pose_commands(
        pose=pose,
        intensity=intensity,
        scale=scale,
        position_x=position_x,
        position_y=position_y,
        enabled=enabled,
    )
    return [send_mateengine_command(command).to_dict() for command in commands]


def _http_json(
    *,
    method: str,
    url: str,
    scope: str,
    payload: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any], str | None]:
    return False, {}, "HOST_DEVICE_EFFECT_RETIRED_USE_EDGE_EMBODIMENT_COMMAND"


def get_mateengine_bridge_health() -> MateEngineActionResult:
    receipt = queue_mateengine_command(
        action="health",
        payload={},
        request_id=f"mateengine_health_{int(time.time() * 1000)}",
    )
    return MateEngineActionResult(
        ok=receipt.success,
        status=receipt.status.lower(),
        backend="edge_embodiment_v1",
        target="edge://mateengine/health",
        payload={},
        response=dict(receipt.event or {}),
        error=None if receipt.success else receipt.detail,
    )


def send_mateengine_command(command: dict[str, Any]) -> MateEngineActionResult:
    payload = dict(command or {})
    request_id = str(payload.get("request_id") or f"mateengine_{int(time.time() * 1000)}")
    receipt = queue_mateengine_command(
        action="command",
        payload={"command": payload},
        request_id=request_id,
    )
    return MateEngineActionResult(
        ok=receipt.success,
        status=receipt.status.lower(),
        backend="edge_embodiment_v1",
        target="edge://mateengine/command",
        payload=payload,
        response=receipt.as_dict(),
        error=None if receipt.success else receipt.detail,
    )


def send_mateengine_action(
    *,
    action: str = "state",
    state: str | None = None,
    touch_type: str | None = None,
    emotion_tag: str | None = None,
    expression: str | None = None,
    preset: str | None = None,
    text: str | None = None,
    enabled: bool | None = None,
    ttl: float | None = None,
    duration: float | None = None,
    intensity: float | None = None,
    scale: float | None = None,
    position_x: float | None = None,
    position_y: float | None = None,
    request_id: str | None = None,
) -> MateEngineActionResult:
    normalized_action = _normalize_action(action or state)
    normalized_state = _normalize_state(state or normalized_action)
    clean_text = str(text or "").strip()
    clean_touch = _normalize_state(touch_type)
    clean_preset = str(preset or expression or "").strip()
    emotion = _normalize_emotion(
        emotion_tag
        or expression
        or STATE_TO_EMOTION.get(normalized_state)
        or TOUCH_TO_EMOTION.get(clean_touch)
        or clean_preset
        or ("HAPPY" if normalized_action in {"say", "speak"} else "NEUTRAL")
    )
    ttl_value = _clamp_float(ttl, default=8.0, minimum=0.2, maximum=60.0)
    duration_value = _clamp_float(duration, default=ttl_value, minimum=0.2, maximum=60.0)
    intensity_value = _clamp_float(intensity, default=0.8, minimum=0.0, maximum=1.0)

    commands: list[dict[str, Any]] = []
    if normalized_action in {"status"}:
        return get_mateengine_bridge_health()
    if normalized_action == "freeze":
        commands.append({
            "type": "freeze",
            "enabled": True if enabled is None else bool(enabled),
        })
    elif normalized_action in {"idle", "reset"} or normalized_state == "idle":
        commands.append({"type": "reset"})
    elif normalized_action == "pose":
        pose = _normalize_state(clean_preset or state or expression or "idle")
        commands.extend(_mateengine_pose_commands(
            pose=pose,
            intensity=intensity_value if intensity is not None else None,
            scale=scale,
            position_x=position_x,
            position_y=position_y,
            enabled=enabled,
        ))
    elif normalized_action in {"choreography", "action_sequence", "sequence"}:
        choreography = _normalize_state(clean_preset or state or expression or emotion or "bounce")
        commands.append(_mateengine_choreography_command(
            choreography,
            emotion_tag=emotion,
            state=normalized_state,
            duration_s=duration if duration is not None else None,
            include_expression=True,
        ))
    elif normalized_action in {"gesture", "animation", "animate", "native_action"}:
        gesture = _normalize_state(clean_preset or state or expression or "bounce")
        commands.append({
            "type": "native_action",
            "gesture": gesture,
            "value": intensity_value,
            "duration": duration_value,
        })
    elif normalized_action in {"motion", "motion_sequence"}:
        gesture = _normalize_state(clean_preset or state or expression or "bounce")
        commands.append(_mateengine_motion_sequence_command(
            gesture,
            duration_s=duration if duration is not None else None,
        ))
    elif normalized_action in {"state", "thinking", "listening", "speak", "speaking"}:
        if normalized_state in {"idle"}:
            commands.append({"type": "reset"})
        else:
            commands.append({
                "type": "expression",
                "preset": emotion,
                "emotion_tag": emotion,
                "value": intensity_value,
                "ttl": duration_value,
            })
            if _mateengine_auto_gestures_enabled():
                auto_gesture = _mateengine_auto_gesture_for_state(normalized_state)
                if not auto_gesture and normalized_action in {"speak", "speaking"}:
                    auto_gesture = _mateengine_auto_gesture_for_emotion(emotion)
                if auto_gesture:
                    commands.append(_mateengine_choreography_command(
                        auto_gesture,
                        emotion_tag=emotion,
                        state=normalized_state,
                        duration_s=_mateengine_auto_gesture_duration_s(),
                        include_expression=False,
                    ))
    elif normalized_action in {"emote", "expression"}:
        commands.append({
            "type": "expression",
            "preset": emotion,
            "emotion_tag": emotion,
            "value": intensity_value,
            "ttl": duration_value,
        })
        if _mateengine_auto_gestures_enabled():
            auto_gesture = _mateengine_auto_gesture_for_emotion(emotion)
            if auto_gesture:
                commands.append(_mateengine_choreography_command(
                    auto_gesture,
                    emotion_tag=emotion,
                    state=normalized_state,
                    duration_s=_mateengine_auto_gesture_duration_s(),
                    include_expression=False,
                ))
    elif normalized_action == "touch":
        commands.append({
            "type": "expression",
            "preset": emotion,
            "emotion_tag": emotion,
            "value": intensity_value,
            "ttl": min(duration_value, 8.0),
        })
        if _mateengine_auto_gestures_enabled():
            auto_gesture = _mateengine_auto_gesture_for_emotion(emotion)
            if auto_gesture:
                commands.append(_mateengine_choreography_command(
                    auto_gesture,
                    emotion_tag=emotion,
                    state=normalized_state,
                    duration_s=_mateengine_auto_gesture_duration_s(),
                    include_expression=False,
                ))
    elif normalized_action == "transform":
        transform = _normalize_state(clean_preset or state or "chibi")
        if transform in {"reset", "normal", "neutral", "off"}:
            commands.append({"type": "chibi", "enabled": False})
            commands.append({"type": "reset"})
        elif transform in {"chibi", "q", "q_mode", "small"}:
            commands.append({
                "type": "chibi",
                "enabled": True if enabled is None else bool(enabled),
                "scale": _clamp_float(scale, default=2.4, minimum=0.5, maximum=4.0),
            })
        else:
            commands.append({
                "type": "expression",
                "preset": clean_preset or emotion,
                "emotion_tag": emotion,
                "value": intensity_value,
                "ttl": duration_value,
            })
    elif normalized_action == "accessory":
        commands.append({
            "type": "accessory",
            "accessory": clean_preset,
            "enabled": True if enabled is None else bool(enabled),
        })
    elif normalized_action == "move":
        commands.append({
            "type": "move_model",
            "position_x": _clamp_float(position_x, default=0.0, minimum=-1.0, maximum=1.0),
            "position_y": _clamp_float(position_y, default=0.0, minimum=-1.0, maximum=1.0),
            "relative": True,
        })
    elif normalized_action in {"say", "show"}:
        pass
    elif normalized_action == "hide":
        commands.append({"type": "reset"})
    else:
        return MateEngineActionResult(
            ok=False,
            status="rejected",
            backend="mateengine_bridge",
            target=f"{_mateengine_bridge_base_url()}/command",
            payload={
                "action": normalized_action,
                "state": normalized_state,
                "request_id": request_id,
            },
            error=f"unsupported MateEngine action: {normalized_action}",
        )

    text_bubble_enabled = True
    if normalized_action in {"state", "thinking", "listening", "idle", "reset", "freeze"}:
        text_bubble_enabled = _mateengine_behavior_bool_env("HOST_MATEENGINE_STATE_BUBBLES_ENABLED", False)
    if normalized_action in {"speak", "speaking"}:
        text_bubble_enabled = _mateengine_behavior_bool_env("HOST_MATEENGINE_SPEECH_BUBBLES_ENABLED", False)

    if clean_text and normalized_action != "freeze" and text_bubble_enabled:
        commands.append({
            "type": "say",
            "text": clean_text,
            "emotion": emotion,
            "ttl": ttl_value,
        })

    if not commands:
        return MateEngineActionResult(
            ok=True,
            status="noop",
            backend="mateengine_bridge",
            target=f"{_mateengine_bridge_base_url()}/command",
            payload={
                "action": normalized_action,
                "state": normalized_state,
                "request_id": request_id,
            },
            results=[],
        )

    results = [send_mateengine_command(command).to_dict() for command in commands]
    ok = all(bool(row.get("ok")) for row in results)
    return MateEngineActionResult(
        ok=ok,
        status="sent" if ok else "partial",
        backend="mateengine_bridge",
        target=f"{_mateengine_bridge_base_url()}/command",
        payload={
            "action": normalized_action,
            "state": normalized_state,
            "touch_type": clean_touch or None,
            "emotion_tag": emotion,
            "preset": clean_preset or None,
            "text": clean_text or None,
            "request_id": request_id,
        },
        results=results,
        error=None if ok else "; ".join(str(row.get("error") or "") for row in results if row.get("error")),
    )


def build_mateengine_signal_payload(
    *,
    event_type: str,
    name: str | None = None,
    event_id: str | None = None,
    phase: str | None = None,
    distance: float | None = None,
    biome: str | None = None,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    clean_type = (event_type or "").strip()
    if clean_type not in SUPPORTED_SIGNAL_EVENTS:
        raise ValueError(f"unsupported MateEngine signal event: {clean_type!r}")

    payload: dict[str, Any] = {
        "type": clean_type,
        "ts": int(timestamp_ms if timestamp_ms is not None else time.time() * 1000),
    }

    if clean_type == EVENT_MOB_PROXIMITY:
        payload["phase"] = (phase or "enter").strip() or "enter"
        payload["name"] = (name or "Natsume Host").strip() or "Natsume Host"
        payload["id"] = (event_id or "natsume_host").strip() or "natsume_host"
        payload["distance"] = float(distance if distance is not None else 1.0)
    elif clean_type == EVENT_KILL_CONFIRM:
        payload["name"] = (name or "Natsume Host").strip() or "Natsume Host"
        payload["id"] = (event_id or "natsume_host").strip() or "natsume_host"
    elif clean_type == EVENT_BIOME_DISCOVERY:
        payload["biome"] = (biome or name or "Natsume").strip() or "Natsume"
    elif name:
        payload["name"] = name.strip()

    return payload


def send_mateengine_signal(
    *,
    event_type: str = EVENT_MOB_PROXIMITY,
    name: str | None = None,
    event_id: str | None = None,
    phase: str | None = None,
    distance: float | None = None,
    biome: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> MateEngineSignalResult:
    target_host = host or _mateengine_host()
    target_port = port or _mateengine_port()

    try:
        payload = build_mateengine_signal_payload(
            event_type=event_type,
            name=name,
            event_id=event_id,
            phase=phase,
            distance=distance,
            biome=biome,
        )
    except ValueError as exc:
        return MateEngineSignalResult(
            ok=False,
            status="rejected",
            host=target_host,
            port=target_port,
            payload={},
            error=str(exc),
        )

    receipt = queue_mateengine_command(
        action="signal",
        payload={"signal": payload},
        request_id=str(payload.get("id") or f"mateengine_signal_{int(time.time() * 1000)}"),
    )
    event = dict(receipt.event or {})
    return MateEngineSignalResult(
        ok=receipt.success,
        status=receipt.status.lower(),
        host=str(event.get("host") or target_host),
        port=int(event.get("port") or target_port),
        payload=payload,
        bytes_sent=int(event.get("bytes_sent") or 0),
        error=None if receipt.success else receipt.detail,
    )


def send_mateengine_say(
    *,
    text: str,
    emotion: str | None = None,
    ttl: float | None = None,
    host: str | None = None,
    port: int | None = None,
) -> MateEngineBridgeResult:
    target_host = host or _mateengine_host()
    target_port = port or _mateengine_port()
    clean_text = (text or "").strip()
    if not clean_text:
        return MateEngineBridgeResult(
            ok=False,
            status="rejected",
            host=target_host,
            port=target_port,
            payload={},
            error="text is required",
        )

    payload: dict[str, Any] = build_mateengine_signal_payload(
        event_type=EVENT_MOB_PROXIMITY,
        name=NATSUME_TEXT_PREFIX + clean_text,
        event_id="natsume_say",
        phase="enter",
        distance=1.0,
    )
    if emotion:
        payload["emotion"] = emotion.strip()
    if ttl is not None:
        payload["ttl"] = float(ttl)

    receipt = queue_mateengine_command(
        action="signal",
        payload={"signal": payload},
        request_id=f"mateengine_say_{int(time.time() * 1000)}",
    )
    event = dict(receipt.event or {})
    return MateEngineBridgeResult(
        ok=receipt.success,
        status=receipt.status.lower(),
        host=str(event.get("host") or target_host),
        port=int(event.get("port") or target_port),
        payload=payload,
        bytes_sent=int(event.get("bytes_sent") or 0),
        error=None if receipt.success else receipt.detail,
    )

# Desktop pet behavior orchestration layer.
# This wrapper keeps the public Host API stable while adding deterministic local
# MateEngine cues that are hard to express as a single high-level action.
import threading as _mateengine_behavior_threading
import time as _mateengine_behavior_time

_send_mateengine_action_core = send_mateengine_action
_mateengine_speech_mouth_token = 0
_mateengine_speech_mouth_lock = _mateengine_behavior_threading.Lock()
_mateengine_freeze_token = 0
_mateengine_freeze_lock = _mateengine_behavior_threading.Lock()


def _mateengine_behavior_bool_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _mateengine_behavior_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    try:
        value = float(raw) if raw is not None and str(raw).strip() else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _mateengine_behavior_set_form(blendshape: str, value: object) -> None:
    name = str(blendshape or "").strip()
    if not name:
        send_mateengine_command({"type": "form", "preset": "NEUTRAL", "value": 0.0})
        return
    send_mateengine_command({"type": "form", "name": name, "value": value})


def _mateengine_behavior_set_freeze(enabled: bool = True) -> None:
    if not _mateengine_behavior_bool_env("HOST_MATEENGINE_AUTO_FREEZE_ENABLED", True):
        return
    send_mateengine_command({"type": "freeze", "enabled": bool(enabled)})


def _mateengine_behavior_stabilize_then_freeze() -> None:
    global _mateengine_freeze_token
    if not _mateengine_behavior_bool_env("HOST_MATEENGINE_AUTO_FREEZE_ENABLED", True):
        return
    delay_s = _mateengine_behavior_float_env("HOST_MATEENGINE_FREEZE_STABILIZE_DELAY_S", 0.45, 0.0, 2.0)
    with _mateengine_freeze_lock:
        _mateengine_freeze_token += 1
        token = _mateengine_freeze_token
    if delay_s <= 0.0:
        _mateengine_behavior_set_freeze(True)
        return
    send_mateengine_command({"type": "freeze", "enabled": False})

    def _freeze_if_current() -> None:
        with _mateengine_freeze_lock:
            if token != _mateengine_freeze_token:
                return
        try:
            _mateengine_behavior_set_freeze(True)
        except Exception:
            return

    timer = _mateengine_behavior_threading.Timer(delay_s, _freeze_if_current)
    timer.daemon = True
    timer.start()


def _mateengine_behavior_set_speech_form(active: bool) -> None:
    if not _mateengine_behavior_bool_env("HOST_MATEENGINE_SPEECH_FORM_ENABLED", True):
        return
    blendshape = os.getenv("HOST_MATEENGINE_SPEECH_FORM_BLENDSHAPE", "Face:MouthSmile").strip()
    if not blendshape:
        return
    value = _clamp_float(
        os.getenv("HOST_MATEENGINE_SPEECH_FORM_VALUE", "0.45") if active else 0.0,
        default=0.45 if active else 0.0,
        minimum=0.0,
        maximum=1.0,
    )
    _mateengine_behavior_set_form(blendshape, value)


def _mateengine_behavior_apply_visual_state(state: str, *, text: object = "") -> None:
    normalized = str(state or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        normalized = "idle"
    pose = MATEENGINE_VISUAL_STATE_POSES.get(normalized)

    if normalized in {"idle", "waiting", "done", "end", "ended", "reset"}:
        _apply_mateengine_pose_commands(pose or "home")
        return

    if normalized in {"listening", "hearing", "user_speaking"}:
        _mateengine_behavior_close_mouth()
        _apply_mateengine_pose_commands(pose or "listen")
        return

    if normalized in {"thinking", "memory_recall", "tool_running", "processing"}:
        _mateengine_behavior_close_mouth()
        _apply_mateengine_pose_commands(pose or "think")
        return

    if normalized in {"speaking", "speak", "say", "tts_start", "playback_start"}:
        _apply_mateengine_pose_commands(pose or "speak")
        send_mateengine_command({"type": "mouth", "value": 0.82})
        _mateengine_behavior_set_speech_form(True)
        _mateengine_behavior_schedule_mouth_close(_mateengine_behavior_text_duration_s(text), text=text)
        return

    if normalized in {"interrupted", "barge_in", "surprised"}:
        _mateengine_behavior_close_mouth()
        _apply_mateengine_pose_commands(pose or "surprised")
        return

    if normalized in {"error", "failed", "anxious"}:
        _mateengine_behavior_close_mouth()
        _apply_mateengine_pose_commands(pose or "anxious")
        return

    _mateengine_behavior_close_mouth()


def _mateengine_behavior_text_duration_s(text: object) -> float:
    content = str(text or "").strip()
    if not content:
        return 0.75
    return max(0.75, min(6.0, len(content) * 0.055 + 0.35))


def _mateengine_behavior_normalize_energy_frames(frames: object) -> list[float]:
    if frames is None:
        return []
    if isinstance(frames, str):
        try:
            parsed = json.loads(frames)
        except json.JSONDecodeError:
            return []
        frames = parsed
    if not isinstance(frames, (list, tuple)):
        return []

    values: list[float] = []
    for item in frames:
        raw_value = item
        if isinstance(item, dict):
            raw_value = (
                item.get("mouth")
                if item.get("mouth") is not None
                else item.get("value")
                if item.get("value") is not None
                else item.get("energy")
                if item.get("energy") is not None
                else item.get("rms")
                if item.get("rms") is not None
                else item.get("level")
            )
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if value < 0.0:
            continue
        values.append(value)

    if not values:
        return []
    peak = max(values)
    if peak <= 0.0:
        return [0.0 for _ in values]
    if peak > 1.0:
        values = [value / peak for value in values]
    return [round(max(0.0, min(1.0, value)), 4) for value in values]


def _mateengine_behavior_synthetic_energy_frames(text: object, duration_s: float, interval_s: float) -> list[float]:
    content = str(text or "").strip()
    if not content:
        content = "..."
    frame_count = max(3, int(max(0.1, duration_s) / max(0.02, interval_s)))
    pattern = [0.10, 0.46, 0.82, 0.58, 0.24, 0.68, 0.36, 0.14]
    pause_chars = set("，。！？、；：,.!?;:\n\r\t ")
    frames: list[float] = []
    for index in range(frame_count):
        char_index = min(len(content) - 1, int(index * len(content) / max(1, frame_count)))
        ch = content[char_index]
        if ch in pause_chars:
            frames.append(0.04)
            continue
        base = pattern[index % len(pattern)]
        code = ord(ch)
        jitter = (code % 7) * 0.025
        if "a" <= ch.lower() <= "z" and ch.lower() in {"a", "e", "i", "o", "u"}:
            jitter += 0.10
        elif code >= 0x4E00:
            jitter += 0.06
        frames.append(round(max(0.04, min(1.0, base + jitter)), 4))
    return frames


def _mateengine_behavior_extract(args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, object]:
    extracted = dict(kwargs)
    positional = ["action", "state", "emotion_tag", "text", "request_id"]
    for index, name in enumerate(positional):
        if name not in extracted and index < len(args):
            extracted[name] = args[index]
    return extracted


def _mateengine_behavior_schedule_mouth_close(
    duration_s: float,
    *,
    text: object = "",
    energy_frames: object = None,
) -> None:
    global _mateengine_speech_mouth_token
    with _mateengine_speech_mouth_lock:
        _mateengine_speech_mouth_token += 1
        token = _mateengine_speech_mouth_token
    drive_duration_s = max(0.1, float(duration_s))
    interval_s = _mateengine_behavior_float_env("HOST_MATEENGINE_MOUTH_FRAME_INTERVAL_S", 0.08, 0.03, 0.25)
    mouth_min = _mateengine_behavior_float_env("HOST_MATEENGINE_MOUTH_MIN_VALUE", 0.06, 0.0, 0.4)
    mouth_max = _mateengine_behavior_float_env("HOST_MATEENGINE_MOUTH_MAX_VALUE", 0.95, 0.2, 1.0)
    energy_gain = _mateengine_behavior_float_env("HOST_MATEENGINE_MOUTH_ENERGY_GAIN", 0.92, 0.1, 1.5)
    smoothing = _mateengine_behavior_float_env("HOST_MATEENGINE_MOUTH_SMOOTHING", 0.45, 0.0, 0.9)
    frames = _mateengine_behavior_normalize_energy_frames(energy_frames)
    if not frames:
        frames = _mateengine_behavior_synthetic_energy_frames(text, drive_duration_s, interval_s)
    frame_count = max(1, int(drive_duration_s / interval_s))
    deadline = _mateengine_behavior_time.monotonic() + drive_duration_s

    def _is_current() -> bool:
        with _mateengine_speech_mouth_lock:
            if token != _mateengine_speech_mouth_token:
                return False
        return True

    def _close_if_current() -> None:
        if not _is_current():
            return
        try:
            send_mateengine_command({"type": "mouth", "value": 0.0})
            _mateengine_behavior_set_speech_form(False)
            if _mateengine_behavior_bool_env("HOST_MATEENGINE_AUTO_HOME_AFTER_SPEECH_ENABLED", True):
                home_delay_s = _mateengine_behavior_float_env(
                    "HOST_MATEENGINE_AUTO_HOME_AFTER_SPEECH_DELAY_S",
                    0.18,
                    0.0,
                    3.0,
                )

                def _home_if_current() -> None:
                    if not _is_current():
                        return
                    try:
                        _apply_mateengine_pose_commands("home")
                    except Exception:
                        return

                if home_delay_s <= 0.0:
                    _home_if_current()
                else:
                    timer = _mateengine_behavior_threading.Timer(home_delay_s, _home_if_current)
                    timer.daemon = True
                    timer.start()
        except Exception:
            return

    def _frame_value(step: int) -> float:
        if not frames:
            return 0.0
        index = min(len(frames) - 1, int(step * len(frames) / max(1, frame_count)))
        raw = max(0.0, min(1.0, frames[index]))
        if raw <= 0.03:
            return 0.0
        return max(mouth_min, min(mouth_max, raw * energy_gain))

    def _pulse(step: int = 0, previous: float = 0.0) -> None:
        if not _is_current():
            return
        if _mateengine_behavior_time.monotonic() >= deadline or step >= frame_count:
            _close_if_current()
            return
        target_value = _frame_value(step)
        mouth_value = round(previous * smoothing + target_value * (1.0 - smoothing), 4)
        try:
            send_mateengine_command({"type": "mouth", "value": mouth_value})
        except Exception:
            return
        timer = _mateengine_behavior_threading.Timer(interval_s, lambda: _pulse(step + 1, mouth_value))
        timer.daemon = True
        timer.start()

    timer = _mateengine_behavior_threading.Timer(0.04, _pulse)
    timer.daemon = True
    timer.start()


def _mateengine_behavior_close_mouth() -> None:
    global _mateengine_speech_mouth_token
    with _mateengine_speech_mouth_lock:
        _mateengine_speech_mouth_token += 1
    send_mateengine_command({"type": "mouth", "value": 0.0})
    _mateengine_behavior_set_speech_form(False)


def _mateengine_behavior_emit_cues(extracted: dict[str, object]) -> None:
    action = str(extracted.get("action") or "").strip().lower()
    state = str(extracted.get("state") or "").strip().lower()
    text = extracted.get("text")

    if action in {"reset", "status"}:
        if action == "reset":
            _mateengine_behavior_apply_visual_state("reset", text=text)
        return

    if action in {"freeze"}:
        return

    if action in {"speak", "say"} or state == "speaking":
        _mateengine_behavior_apply_visual_state("speaking", text=text)
        return

    if action in {"state", "thinking", "listening", "idle"} or state in {
        "thinking",
        "listening",
        "idle",
        "waiting",
        "memory_recall",
        "tool_running",
        "processing",
        "interrupted",
        "error",
    }:
        _mateengine_behavior_apply_visual_state(state or action, text=text)
        return

    if action in {"emote", "touch", "transform", "accessory", "move", "pose"}:
        return


def signal_mateengine_speech_playback(
    *,
    event: str = "start",
    text: object = "",
    request_id: str | None = None,
    utterance_id: str | None = None,
    duration_s: object = None,
    energy_frames: object = None,
) -> MateEngineActionResult:
    """Synchronize local MateEngine mouth cues with a TTS playback lifecycle.

    This intentionally controls only the mouth channel. Chat bubbles and
    expression changes stay owned by the higher-level Host action path so TTS
    sentence streaming does not spam duplicate bubbles.
    """
    normalized_event = str(event or "start").strip().lower()
    payload = {
        "event": normalized_event,
        "text_len": len(str(text or "")),
        "request_id": request_id,
        "utterance_id": utterance_id,
        "duration_s": duration_s,
        "energy_frame_count": len(_mateengine_behavior_normalize_energy_frames(energy_frames)),
    }
    if normalized_event in {"start", "started", "speaking", "playback_start", "tts_start"}:
        result = send_mateengine_command({"type": "mouth", "value": 0.82})
        _mateengine_behavior_stabilize_then_freeze()
        _mateengine_behavior_set_speech_form(True)
        drive_duration_s = _clamp_float(
            duration_s,
            default=_mateengine_behavior_text_duration_s(text),
            minimum=0.1,
            maximum=30.0,
        )
        _mateengine_behavior_schedule_mouth_close(
            drive_duration_s,
            text=text,
            energy_frames=energy_frames,
        )
        return result
    if normalized_event in {"stop", "stopped", "end", "ended", "playback_end", "tts_end", "failed", "cancel"}:
        result = send_mateengine_command({"type": "mouth", "value": 0.0})
        _mateengine_behavior_apply_visual_state("idle", text=text)
        return result
    return MateEngineActionResult(
        ok=True,
        status="ignored",
        backend="mateengine_bridge",
        target=f"{_mateengine_bridge_base_url()}/command",
        payload=payload,
        error=f"unsupported MateEngine speech playback event: {normalized_event}",
    )


def send_mateengine_action(*args: object, **kwargs: object):
    result = _send_mateengine_action_core(*args, **kwargs)
    try:
        _mateengine_behavior_emit_cues(_mateengine_behavior_extract(args, kwargs))
    except Exception:
        pass
    return result
