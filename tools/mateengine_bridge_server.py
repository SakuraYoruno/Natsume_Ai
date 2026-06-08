from __future__ import annotations

import json
import os
import socket
import threading
import time
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


HTTP_HOST = os.getenv("MATEENGINE_BRIDGE_HTTP_HOST", "0.0.0.0").strip() or "0.0.0.0"
HTTP_PORT = int(os.getenv("MATEENGINE_BRIDGE_HTTP_PORT", "18770").strip() or "18770")
UDP_HOST = os.getenv("MATEENGINE_BRIDGE_UDP_HOST", "0.0.0.0").strip() or "0.0.0.0"
UDP_PORT = int(os.getenv("MATEENGINE_BRIDGE_UDP_PORT", "18771").strip() or "18771")
MATEENGINE_HOST = os.getenv("MATEENGINE_SIGNAL_HOST", "127.0.0.1").strip() or "127.0.0.1"
MATEENGINE_PORT = int(os.getenv("MATEENGINE_SIGNAL_PORT", "32145").strip() or "32145")
MATEENGINE_PLUGIN_HOST = os.getenv("MATEENGINE_PLUGIN_HOST", "127.0.0.1").strip() or "127.0.0.1"
MATEENGINE_PLUGIN_PORT = int(os.getenv("MATEENGINE_PLUGIN_PORT", "32146").strip() or "32146")
WINDOW_MOTION_ENABLED = _env_bool("MATEENGINE_WINDOW_MOTION_ENABLED", False)
WINDOW_TITLE = os.getenv("MATEENGINE_WINDOW_TITLE", "MateEngineX").strip() or "MateEngineX"
WINDOW_MOTION_SCALE_X = _env_float("MATEENGINE_WINDOW_MOTION_SCALE_X", 700.0)
WINDOW_MOTION_SCALE_Y = _env_float("MATEENGINE_WINDOW_MOTION_SCALE_Y", 520.0)
WINDOW_MOTION_MAX_OFFSET_X = _env_int("MATEENGINE_WINDOW_MOTION_MAX_OFFSET_X", 260)
WINDOW_MOTION_MAX_OFFSET_Y = _env_int("MATEENGINE_WINDOW_MOTION_MAX_OFFSET_Y", 180)
WINDOW_HOME_X = os.getenv("MATEENGINE_WINDOW_HOME_X")
WINDOW_HOME_Y = os.getenv("MATEENGINE_WINDOW_HOME_Y")
MATEENGINE_KNOWN_FORM_BLENDSHAPES = [
    "Face:Joy1",
    "Face:Joy2",
    "Face:MouthNeko",
    "Face:FaceStar",
    "Face:FaceHeart",
    "Face:FaceRed",
    "Face:O",
    "Face:EyeWideLeft",
    "Face:EyeWideRight",
    "Face:FaceCry",
    "Face:Sorrow1",
    "Face:Sorrow2",
    "Face:Angry",
    "Face:BrowDownLeft",
    "Face:BrowDownRight",
    "Face:FaceSweat1",
    "Face:Face_XX",
    "Face:LookUp",
    "Face:LookDown",
    "Face:LookRight",
    "Face:FaceDark",
    "Face:MouthSmile",
    "Face:MouthTrouble",
    "Face:MouthStraight",
]

state: dict[str, Any] = {
    "started_at": time.time(),
    "command_count": 0,
    "command_history": [],
    "udp_input_count": 0,
    "udp_count": 0,
    "error_count": 0,
    "last_command": None,
    "last_action_sequence": None,
    "last_motion_sequence": None,
    "last_udp_input": None,
    "last_udp": None,
    "last_error": None,
    "window_motion_count": 0,
    "window_home": None,
    "last_window_motion": None,
    "plugin_count": 0,
    "last_plugin": None,
}


class MateEngineBridgeHandler(BaseHTTPRequestHandler):
    server_version = "NatsumeMateEngineBridge/0.1"

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/health":
            self._send_json({"success": False, "detail": "not found"}, status=404)
            return
        self._send_json(
            {
                "status": "ok",
                "bridge": "mateengine",
                "started_at": state["started_at"],
                "command_count": state["command_count"],
                "udp_input_count": state["udp_input_count"],
                "udp_count": state["udp_count"],
                "error_count": state["error_count"],
                "last_command": state["last_command"],
                "last_action_sequence": state["last_action_sequence"],
                "last_motion_sequence": state["last_motion_sequence"],
                "recent_commands": state["command_history"][-60:],
                "last_udp_input": state["last_udp_input"],
                "last_udp": state["last_udp"],
                "last_error": state["last_error"],
                "window_motion": {
                    "enabled": WINDOW_MOTION_ENABLED,
                    "title": WINDOW_TITLE,
                    "count": state["window_motion_count"],
                    "home": state["window_home"],
                    "last": state["last_window_motion"],
                    "scale_x": WINDOW_MOTION_SCALE_X,
                    "scale_y": WINDOW_MOTION_SCALE_Y,
                    "max_offset_x": WINDOW_MOTION_MAX_OFFSET_X,
                    "max_offset_y": WINDOW_MOTION_MAX_OFFSET_Y,
                },
                "bridge_udp": {
                    "host": UDP_HOST,
                    "port": UDP_PORT,
                },
                "mateengine": {
                    "host": MATEENGINE_HOST,
                    "port": MATEENGINE_PORT,
                },
                "mateengine_plugin": {
                    "host": MATEENGINE_PLUGIN_HOST,
                    "port": MATEENGINE_PLUGIN_PORT,
                    "count": state["plugin_count"],
                    "last": state["last_plugin"],
                },
            }
        )

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/command":
            self._send_json({"success": False, "detail": "not found"}, status=404)
            return
        payload = self._read_json()
        if not isinstance(payload, dict):
            self._send_json({"success": False, "detail": "invalid json payload"}, status=400)
            return

        state["command_count"] = int(state["command_count"]) + 1
        received_command = {**payload, "received_at": time.time()}
        state["last_command"] = received_command
        history = state["command_history"]
        history.append(received_command)
        del history[:-120]
        result = handle_command(payload)
        status = 200 if result.get("success") else 502
        self._send_json(result, status=status)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def handle_command(payload: dict[str, Any]) -> dict[str, Any]:
    command_type = str(payload.get("type") or payload.get("action") or "").strip().lower()
    if command_type in {"action_sequence", "choreography", "sequence"}:
        return schedule_action_sequence(payload)
    if command_type == "motion_sequence":
        return schedule_motion_sequence(payload)
    if command_type == "mouth" or (not command_type and "value" in payload):
        return send_mouth(value=payload.get("value"))
    if command_type == "talk":
        talking = bool(payload.get("talking"))
        return send_mouth(value=0.16 if talking else 0.0, detail=("talk_start" if talking else "talk_end"))
    if command_type == "reset":
        return reset_mateengine_forms()
    if command_type in {"freeze", "stabilize", "stabilise", "stable", "motion_freeze"}:
        enabled = payload.get("enabled")
        if enabled is None:
            enabled = payload.get("value", True)
        return send_freeze(enabled=enabled)
    if command_type in {"native_action", "body_action", "gesture", "animation", "animate"}:
        return send_native_action(payload)
    if command_type == "move_model":
        return move_mateengine_window(payload)
    if command_type in {"expression", "expression_transition"}:
        return send_form(payload, detail=command_type)
    if command_type in {"form", "shape", "blendshape", "morph", "reset_form"}:
        return send_form(payload, detail=command_type)
    if command_type in {"accessory", "chibi"}:
        return send_plugin_command(dict(payload), detail=command_type)
    if command_type in {"hotkey"}:
        return {
            "success": True,
            "detail": f"MateEngine bridge accepted no-op command: {command_type}",
            "forwarded": False,
            "command_type": command_type,
        }
    if command_type in {"say", "text"}:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            return {"success": False, "detail": "text command requires text"}
        return send_say(text)
    return {
        "success": True,
        "detail": f"MateEngine bridge ignored unsupported command: {command_type or '(empty)'}",
        "forwarded": False,
        "command_type": command_type,
    }


def send_mouth(*, value: Any, detail: str = "mouth") -> dict[str, Any]:
    mouth_value = _clamp01(value)
    payload = {
        "type": "natsume_mouth",
        "distance": mouth_value,
        "ts": int(time.time() * 1000),
    }
    return send_udp(payload, detail=detail)


def send_say(text: str) -> dict[str, Any]:
    payload = {
        "type": "mob_proximity",
        "phase": "enter",
        "name": "NATSUME_TEXT::" + text,
        "id": "natsume_bridge_say",
        "distance": 1.0,
        "ts": int(time.time() * 1000),
    }
    return send_udp(payload, detail="say")


def send_freeze(*, enabled: Any) -> dict[str, Any]:
    enabled_value = _truthy(enabled)
    payload = {
        "type": "natsume_freeze",
        "phase": "set",
        "name": "NATSUME_FREEZE",
        "id": "natsume_bridge_freeze",
        "distance": 1.0 if enabled_value else 0.0,
        "ts": int(time.time() * 1000),
    }
    return send_udp(payload, detail="freeze")


def send_native_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(
        payload.get("native_action")
        or payload.get("gesture")
        or payload.get("preset")
        or payload.get("name")
        or payload.get("target")
        or payload.get("action")
        or ""
    ).strip().lower()
    if not action or action in {"native_action", "body_action", "gesture", "animation", "animate"}:
        action = str(payload.get("state") or payload.get("emotion") or payload.get("emotion_tag") or "").strip().lower()
    if not action:
        return {
            "success": False,
            "detail": "native_action requires action, gesture, preset, name, target, state, or emotion",
            "forwarded": False,
            "command_type": "native_action",
        }
    if not _env_bool("MATEENGINE_ENABLE_NATIVE_ACTION_CONTROL", False):
        return {
            "success": True,
            "detail": "MateEngine native_action control disabled; random/native model motion is left to MateEngine",
            "forwarded": False,
            "command_type": "native_action",
            "action": action,
        }
    raw_value = payload.get("value")
    if raw_value is None:
        raw_value = payload.get("intensity", 1.0)
    action_payload = {
        "type": "natsume_action",
        "phase": "set",
        "name": action,
        "id": f"natsume_action_{action}",
        "distance": _clamp01(raw_value),
        "ts": int(time.time() * 1000),
    }
    return send_udp(action_payload, detail="native_action")


def send_form(payload: dict[str, Any], *, detail: str = "form") -> dict[str, Any]:
    emotion = str(
        payload.get("emotion_tag")
        or payload.get("emotion")
        or payload.get("preset")
        or payload.get("target")
        or "NEUTRAL"
    ).strip().upper() or "NEUTRAL"
    raw_value = payload.get("value")
    if raw_value is None:
        raw_value = 0.0 if emotion in {"", "NEUTRAL", "NONE", "RESET"} else 1.0
    explicit_blendshape = str(payload.get("blendshape") or payload.get("name") or "").strip()
    if explicit_blendshape:
        return send_form_blendshape(
            blendshape=explicit_blendshape,
            value=raw_value,
            detail=detail,
            emotion=emotion,
        )
    if emotion in {"", "NEUTRAL", "NONE", "RESET"}:
        return reset_mateengine_forms()
    preset_shapes = _mateengine_form_blendshapes_for_emotion(emotion)
    active_names = {name for name, _scale in preset_shapes if name}
    clear_results = [
        send_form_blendshape(
            blendshape=name,
            value=0.0,
            detail=f"{detail}:clear:{name}",
            emotion=emotion,
        )
        for name in MATEENGINE_KNOWN_FORM_BLENDSHAPES
        if name and name not in active_names
    ]
    if len(preset_shapes) > 1:
        base_value = _clamp01(raw_value)
        return combine_results(
            detail,
            *clear_results,
            *[
                send_form_blendshape(
                    blendshape=name,
                    value=max(0.0, min(1.0, base_value * scale)),
                    detail=f"{detail}:{name}",
                    emotion=emotion,
                )
                for name, scale in preset_shapes
                if name
            ],
        )
    blendshape = preset_shapes[0][0] if preset_shapes else ""
    return combine_results(
        detail,
        *clear_results,
        send_form_blendshape(
            blendshape=blendshape,
            value=raw_value,
            detail=detail,
            emotion=emotion,
        ),
    )


def send_form_blendshape(*, blendshape: str, value: Any, detail: str, emotion: str) -> dict[str, Any]:
    form_payload = {
        "type": "natsume_form",
        "phase": "set",
        "name": blendshape,
        "id": f"natsume_form_{emotion.lower()}",
        "distance": _clamp01(value),
        "ts": int(time.time() * 1000),
    }
    return send_udp(form_payload, detail=detail)


def reset_mateengine_forms() -> dict[str, Any]:
    results = [send_mouth(value=0.0, detail="reset_mouth")]
    for blendshape in MATEENGINE_KNOWN_FORM_BLENDSHAPES:
        results.append(send_form({
            "type": "form",
            "name": blendshape,
            "value": 0.0,
        }, detail="reset_form"))
    return combine_results("reset", *results)


def _mateengine_form_blendshape_for_emotion(emotion: str) -> str:
    shapes = _mateengine_form_blendshapes_for_emotion(emotion)
    return shapes[0][0] if shapes else ""


def _mateengine_form_blendshapes_for_emotion(emotion: str) -> list[tuple[str, float]]:
    normalized = str(emotion or "").strip().upper()
    mapping = {
        "HAPPY": [("Face:Joy1", 1.0), ("Face:Joy2", 0.55), ("Face:MouthSmile", 0.75), ("Face:FaceRed", 0.22)],
        "HAPPINESS": [("Face:Joy1", 1.0), ("Face:Joy2", 0.55), ("Face:MouthSmile", 0.75), ("Face:FaceRed", 0.22)],
        "JOY": [("Face:Joy1", 1.0), ("Face:Joy2", 0.55), ("Face:MouthSmile", 0.75), ("Face:FaceRed", 0.22)],
        "PLAYFUL": [("Face:MouthNeko", 1.0), ("Face:Joy1", 0.5), ("Face:FaceRed", 0.18)],
        "EXCITED": [("Face:Joy1", 0.95), ("Face:FaceStar", 0.9), ("Face:MouthSmile", 0.7)],
        "EXCITEMENT": [("Face:Joy1", 0.95), ("Face:FaceStar", 0.9), ("Face:MouthSmile", 0.7)],
        "PROUD": [("Face:Joy1", 0.9), ("Face:FaceStar", 0.75), ("Face:MouthSmile", 0.65)],
        "LOVE": [("Face:FaceHeart", 1.0), ("Face:FaceRed", 0.7), ("Face:MouthSmile", 0.65)],
        "LOVING": [("Face:FaceHeart", 1.0), ("Face:FaceRed", 0.7), ("Face:MouthSmile", 0.65)],
        "AFFECTION": [("Face:FaceHeart", 1.0), ("Face:FaceRed", 0.7), ("Face:MouthSmile", 0.65)],
        "COQUETTISH": [("Face:FaceHeart", 1.0), ("Face:FaceRed", 0.7), ("Face:MouthSmile", 0.65)],
        "EMBARRASSED": [("Face:FaceRed", 1.0), ("Face:LookDown", 0.55), ("Face:MouthTrouble", 0.45)],
        "SHY": [("Face:FaceRed", 1.0), ("Face:LookDown", 0.55), ("Face:MouthTrouble", 0.45)],
        "SURPRISED": [("Face:EyeWideLeft", 1.0), ("Face:EyeWideRight", 1.0), ("Face:O", 1.0), ("Face:FaceStar", 0.55)],
        "SURPRISE": [("Face:EyeWideLeft", 1.0), ("Face:EyeWideRight", 1.0), ("Face:O", 1.0), ("Face:FaceStar", 0.55)],
        "SAD": [("Face:Sorrow1", 1.0), ("Face:Sorrow2", 0.55), ("Face:FaceCry", 0.65), ("Face:MouthTrouble", 0.45)],
        "SADNESS": [("Face:Sorrow1", 1.0), ("Face:Sorrow2", 0.55), ("Face:FaceCry", 0.65), ("Face:MouthTrouble", 0.45)],
        "NOSTALGIC": [("Face:Sorrow1", 0.85), ("Face:Sorrow2", 0.45), ("Face:MouthTrouble", 0.32)],
        "ANGRY": [("Face:Angry", 1.0), ("Face:BrowDownLeft", 0.65), ("Face:BrowDownRight", 0.65), ("Face:MouthStraight", 0.5)],
        "FRUSTRATED": [("Face:Angry", 1.0), ("Face:BrowDownLeft", 0.65), ("Face:BrowDownRight", 0.65), ("Face:MouthStraight", 0.5)],
        "ANXIOUS": [("Face:FaceSweat1", 0.9), ("Face:FaceRed", 0.35), ("Face:MouthTrouble", 0.55)],
        "CONFUSED": [("Face:Face_XX", 0.9), ("Face:LookUp", 0.65), ("Face:MouthStraight", 0.45)],
        "THINKING": [("Face:LookUp", 1.0), ("Face:LookRight", 0.55), ("Face:MouthStraight", 0.42)],
        "DARK": [("Face:FaceDark", 1.0), ("Face:MouthStraight", 0.45)],
        "LOW": [("Face:FaceDark", 1.0), ("Face:MouthStraight", 0.45)],
        "NEKO": [("Face:MouthNeko", 1.0), ("Face:Joy1", 0.55)],
        "CAT": [("Face:MouthNeko", 1.0), ("Face:Joy1", 0.55)],
        "NEUTRAL": [("", 0.0)],
        "RESET": [("", 0.0)],
        "NONE": [("", 0.0)],
    }
    if normalized in mapping:
        return mapping[normalized]
    if normalized.startswith("FACE:"):
        return [(normalized, 1.0)]
    return []


def send_plugin_command(payload: dict[str, Any], *, detail: str) -> dict[str, Any]:
    command = dict(payload)
    command["ts"] = int(time.time() * 1000)
    data = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sent = sock.sendto(data, (MATEENGINE_PLUGIN_HOST, MATEENGINE_PLUGIN_PORT))
    except OSError as exc:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = str(exc)
        return {
            "success": False,
            "detail": f"MateEngine plugin forward failed: {exc}",
            "forwarded": False,
            "payload": command,
        }
    state["plugin_count"] = int(state["plugin_count"]) + 1
    state["last_plugin"] = {
        "payload": command,
        "bytes_sent": sent,
        "sent_at": time.time(),
    }
    state["last_error"] = None
    return {
        "success": True,
        "detail": f"MateEngine plugin {detail} forwarded",
        "forwarded": True,
        "bytes_sent": sent,
        "payload": command,
    }


def combine_results(command_type: str, *results: dict[str, Any]) -> dict[str, Any]:
    rows = [dict(row or {}) for row in results]
    success = all(bool(row.get("success")) for row in rows)
    return {
        "success": success,
        "detail": f"MateEngine {command_type} {'forwarded' if success else 'partially failed'}",
        "forwarded": any(bool(row.get("forwarded")) for row in rows),
        "command_type": command_type,
        "results": rows,
    }


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def move_mateengine_window(payload: dict[str, Any]) -> dict[str, Any]:
    if not WINDOW_MOTION_ENABLED:
        return {
            "success": True,
            "detail": "MateEngine window motion disabled",
            "forwarded": False,
            "command_type": "move_model",
        }
    if os.name != "nt":
        return {
            "success": True,
            "detail": "MateEngine window motion is only available on Windows",
            "forwarded": False,
            "command_type": "move_model",
        }
    hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = f"MateEngine window not found: {WINDOW_TITLE}"
        return {
            "success": False,
            "detail": state["last_error"],
            "forwarded": False,
            "command_type": "move_model",
        }

    rect = _Rect()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = "GetWindowRect failed"
        return {
            "success": False,
            "detail": state["last_error"],
            "forwarded": False,
            "command_type": "move_model",
        }

    width = max(320, int(rect.right - rect.left))
    height = max(240, int(rect.bottom - rect.top))
    home = _window_home_from_state_or_rect(rect)
    position_x = _clamp_float(payload.get("position_x"), -1.0, 1.0)
    position_y = _clamp_float(payload.get("position_y"), -1.0, 1.0)
    dx = int(round(_clamp_float(position_x * WINDOW_MOTION_SCALE_X, -WINDOW_MOTION_MAX_OFFSET_X, WINDOW_MOTION_MAX_OFFSET_X)))
    dy = int(round(_clamp_float(-position_y * WINDOW_MOTION_SCALE_Y, -WINDOW_MOTION_MAX_OFFSET_Y, WINDOW_MOTION_MAX_OFFSET_Y)))

    bounds = _virtual_screen_bounds()
    target_x = _clamp_int(int(home["left"]) + dx, bounds["left"], bounds["right"] - width)
    target_y = _clamp_int(int(home["top"]) + dy, bounds["top"], bounds["bottom"] - height)

    ctypes.windll.user32.ShowWindow(hwnd, 9)
    moved = bool(ctypes.windll.user32.SetWindowPos(hwnd, 0, target_x, target_y, width, height, 0x0014))
    motion = {
        "payload": {
            "position_x": round(position_x, 5),
            "position_y": round(position_y, 5),
            "rotation": _safe_float(payload.get("rotation"), 0.0),
            "relative": bool(payload.get("relative", True)),
        },
        "home": dict(home),
        "offset": {"x": dx, "y": dy},
        "target": {"left": target_x, "top": target_y, "width": width, "height": height},
        "bounds": bounds,
        "moved_at": time.time(),
    }
    state["window_motion_count"] = int(state["window_motion_count"]) + 1
    state["last_window_motion"] = motion
    state["last_error"] = None if moved else "SetWindowPos failed"
    return {
        "success": moved,
        "detail": "MateEngine window moved" if moved else "SetWindowPos failed",
        "forwarded": moved,
        "command_type": "move_model",
        "motion": motion,
    }


def schedule_motion_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return {
            "success": False,
            "detail": "motion_sequence requires non-empty steps",
            "forwarded": False,
            "command_type": "motion_sequence",
        }
    max_steps = _env_int("MATEENGINE_MOTION_SEQUENCE_MAX_STEPS", 16)
    step_count = min(len(raw_steps), max_steps)
    thread = threading.Thread(target=run_motion_sequence, args=(dict(payload),), daemon=True)
    thread.start()
    return {
        "success": True,
        "detail": "MateEngine motion sequence scheduled",
        "forwarded": True,
        "async": True,
        "command_type": "motion_sequence",
        "gesture": str(payload.get("gesture") or "").strip() or None,
        "step_count": step_count,
    }


def run_motion_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return {
            "success": False,
            "detail": "motion_sequence requires non-empty steps",
            "forwarded": False,
            "command_type": "motion_sequence",
        }

    native_action_result = None
    gesture = str(payload.get("gesture") or payload.get("preset") or payload.get("name") or "").strip()
    if gesture:
        native_action_result = send_native_action({
            "type": "native_action",
            "gesture": gesture,
            "value": payload.get("intensity", 1.0),
        })
        if (
            native_action_result.get("success")
            and _env_bool("MATEENGINE_NATIVE_ACTION_SUPPRESS_WINDOW_MOTION_FOR_GESTURES", True)
            and not _truthy(payload.get("force_window_motion"))
            and not _truthy(payload.get("window_motion"))
        ):
            result = {
                "success": True,
                "detail": "MateEngine motion sequence handled by native action; window motion suppressed",
                "forwarded": True,
                "command_type": "motion_sequence",
                "gesture": gesture,
                "native_action": native_action_result,
                "step_count": 0,
                "results": [],
            }
            state["last_motion_sequence"] = {**result, "completed_at": time.time()}
            return result

    results: list[dict[str, Any]] = []
    max_steps = _env_int("MATEENGINE_MOTION_SEQUENCE_MAX_STEPS", 16)
    max_hold_ms = _env_int("MATEENGINE_MOTION_SEQUENCE_MAX_HOLD_MS", 650)
    default_hold_ms = _env_int("MATEENGINE_MOTION_SEQUENCE_DEFAULT_HOLD_MS", 110)
    for index, raw_step in enumerate(raw_steps[:max_steps]):
        if not isinstance(raw_step, dict):
            results.append({
                "success": False,
                "detail": f"invalid motion step at index {index}",
                "forwarded": False,
                "command_type": "move_model",
                "index": index,
            })
            continue
        step = dict(raw_step)
        step.setdefault("relative", True)
        step.setdefault("pose", payload.get("pose") or payload.get("gesture") or "motion")
        result = move_mateengine_window(step)
        result["index"] = index
        results.append(result)
        try:
            hold_ms = int(float(step.get("hold_ms", default_hold_ms)))
        except (TypeError, ValueError):
            hold_ms = default_hold_ms
        time.sleep(max(0, min(max_hold_ms, hold_ms)) / 1000.0)

    result = {
        "success": all(bool(item.get("success")) for item in results),
        "detail": "MateEngine motion sequence completed",
        "forwarded": True,
        "command_type": "motion_sequence",
        "gesture": str(payload.get("gesture") or "").strip() or None,
        "native_action": native_action_result,
        "step_count": len(results),
        "results": results,
    }
    state["last_motion_sequence"] = {**result, "completed_at": time.time()}
    return result


def schedule_action_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return {
            "success": False,
            "detail": "action_sequence requires non-empty steps",
            "forwarded": False,
            "command_type": "action_sequence",
        }
    max_steps = _env_int("MATEENGINE_ACTION_SEQUENCE_MAX_STEPS", 24)
    step_count = min(len(raw_steps), max_steps)
    thread = threading.Thread(target=run_action_sequence, args=(dict(payload),), daemon=True)
    thread.start()
    return {
        "success": True,
        "detail": "MateEngine action sequence scheduled",
        "forwarded": True,
        "async": True,
        "command_type": "action_sequence",
        "name": str(payload.get("name") or payload.get("gesture") or "").strip() or None,
        "step_count": step_count,
    }


def run_action_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return {
            "success": False,
            "detail": "action_sequence requires non-empty steps",
            "forwarded": False,
            "command_type": "action_sequence",
        }

    max_steps = _env_int("MATEENGINE_ACTION_SEQUENCE_MAX_STEPS", 24)
    normalized_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(raw_steps[:max_steps]):
        if not isinstance(raw_step, dict):
            normalized_steps.append({
                "index": index,
                "at_ms": 0,
                "command": {},
                "invalid": True,
            })
            continue
        command = raw_step.get("command")
        if not isinstance(command, dict):
            command = {
                key: value
                for key, value in raw_step.items()
                if key not in {"at_ms", "hold_ms", "label"}
            }
        try:
            at_ms = int(float(raw_step.get("at_ms", 0)))
        except (TypeError, ValueError):
            at_ms = 0
        normalized_steps.append({
            "index": index,
            "label": str(raw_step.get("label") or "").strip() or None,
            "at_ms": max(0, at_ms),
            "command": dict(command),
            "invalid": not bool(command),
        })

    normalized_steps.sort(key=lambda item: (int(item.get("at_ms") or 0), int(item.get("index") or 0)))
    started_at = time.time()
    results: list[dict[str, Any]] = []
    for item in normalized_steps:
        target_at = started_at + (int(item.get("at_ms") or 0) / 1000.0)
        delay_s = target_at - time.time()
        if delay_s > 0:
            time.sleep(delay_s)
        command = dict(item.get("command") or {})
        if item.get("invalid"):
            result = {
                "success": False,
                "detail": f"invalid action sequence step at index {item.get('index')}",
                "forwarded": False,
                "command_type": "action_sequence_step",
            }
        else:
            result = execute_action_sequence_command(command)
        results.append({
            "index": item.get("index"),
            "label": item.get("label"),
            "at_ms": item.get("at_ms"),
            "command_type": str(command.get("type") or command.get("action") or "").strip().lower(),
            "result": result,
        })

    result = {
        "success": all(bool(item.get("result", {}).get("success")) for item in results),
        "detail": "MateEngine action sequence completed",
        "forwarded": True,
        "command_type": "action_sequence",
        "name": str(payload.get("name") or payload.get("gesture") or "").strip() or None,
        "step_count": len(results),
        "results": results,
        "completed_at": time.time(),
    }
    state["last_action_sequence"] = result
    return result


def execute_action_sequence_command(command: dict[str, Any]) -> dict[str, Any]:
    command_type = str(command.get("type") or command.get("action") or "").strip().lower()
    if command_type in {"action_sequence", "choreography", "sequence"}:
        return {
            "success": False,
            "detail": "nested action_sequence is not supported",
            "forwarded": False,
            "command_type": command_type,
        }
    if command_type == "motion_sequence":
        return run_motion_sequence(dict(command))
    return handle_command(dict(command))


def _window_home_from_state_or_rect(rect: _Rect) -> dict[str, int]:
    existing = state.get("window_home")
    if isinstance(existing, dict) and "left" in existing and "top" in existing:
        return {
            "left": int(existing["left"]),
            "top": int(existing["top"]),
        }
    left = _safe_int(WINDOW_HOME_X, int(rect.left))
    top = _safe_int(WINDOW_HOME_Y, int(rect.top))
    state["window_home"] = {"left": left, "top": top}
    return dict(state["window_home"])


def _virtual_screen_bounds() -> dict[str, int]:
    user32 = ctypes.windll.user32
    left = int(user32.GetSystemMetrics(76))
    top = int(user32.GetSystemMetrics(77))
    width = int(user32.GetSystemMetrics(78))
    height = int(user32.GetSystemMetrics(79))
    if width <= 0 or height <= 0:
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
        left = 0
        top = 0
    return {
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
    }


def send_udp(payload: dict[str, Any], *, detail: str) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sent = sock.sendto(data, (MATEENGINE_HOST, MATEENGINE_PORT))
    except OSError as exc:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = str(exc)
        return {
            "success": False,
            "detail": f"UDP forward failed: {exc}",
            "forwarded": False,
            "payload": payload,
        }
    state["udp_count"] = int(state["udp_count"]) + 1
    state["last_udp"] = {
        "payload": payload,
        "bytes_sent": sent,
        "sent_at": time.time(),
    }
    state["last_error"] = None
    return {
        "success": True,
        "detail": f"MateEngine {detail} forwarded",
        "forwarded": True,
        "bytes_sent": sent,
        "payload": payload,
    }


def serve_udp_bridge() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((UDP_HOST, UDP_PORT))
            print(
                f"[mateengine-bridge] udp://{UDP_HOST}:{UDP_PORT} -> udp://{MATEENGINE_HOST}:{MATEENGINE_PORT}",
                flush=True,
            )
            while True:
                data, addr = sock.recvfrom(65535)
                handle_udp_bridge_datagram(data, addr)
    except OSError as exc:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = f"UDP bridge failed: {exc}"
        print(f"[mateengine-bridge] UDP bridge failed: {exc}", flush=True)


def handle_udp_bridge_datagram(data: bytes, addr: tuple[str, int]) -> None:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = f"invalid udp payload from {addr[0]}:{addr[1]}: {exc}"
        return
    if not isinstance(payload, dict):
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = f"invalid udp payload type from {addr[0]}:{addr[1]}"
        return
    state["udp_input_count"] = int(state["udp_input_count"]) + 1
    state["last_udp_input"] = {
        "payload": payload,
        "remote": f"{addr[0]}:{addr[1]}",
        "received_at": time.time(),
    }
    result = handle_command(payload)
    if not result.get("success"):
        state["error_count"] = int(state["error_count"]) + 1
        state["last_error"] = str(result.get("detail") or "udp bridge command failed")


def start_udp_bridge_thread() -> None:
    thread = threading.Thread(target=serve_udp_bridge, name="mateengine-bridge-udp", daemon=True)
    thread.start()


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return round(number, 4)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0.0
    normalized = str(value or "").strip().lower()
    return normalized not in {"0", "false", "no", "off", "disable", "disabled", ""}


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    number = _safe_float(value, 0.0)
    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    if maximum < minimum:
        return minimum
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def main() -> None:
    start_udp_bridge_thread()
    server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), MateEngineBridgeHandler)
    print(
        f"[mateengine-bridge] http://{HTTP_HOST}:{HTTP_PORT} -> udp://{MATEENGINE_HOST}:{MATEENGINE_PORT}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
