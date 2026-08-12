"""HTTP driver for the local VRM bridge runtime.

The public methods intentionally mirror the VTube Studio driver enough for the
existing Edge voice pipeline to drive a 3D avatar without changing the upstream
emotion, speech, and body-action contracts.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from edge_client.actuators.vts_driver import (
    AVATAR_FREE_ACTION_BLOCKED,
    VtsDriverResult,
    VtsState,
    is_blocked_avatar_hotkey_id,
    normalize_expression_emotion_tag,
)


DEFAULT_VRM_BRIDGE_HOST = "127.0.0.1"
DEFAULT_VRM_BRIDGE_PORT = 18770
DEFAULT_VRM_BRIDGE_TIMEOUT_S = 1.5
DEFAULT_VRM_BRIDGE_MOUTH_UDP_PORT = 18771


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _default_mouth_udp_port() -> int | None:
    raw = os.getenv("EDGE_VRM_BRIDGE_MOUTH_UDP_PORT")
    if raw is None or not raw.strip():
        return None
    try:
        port = int(raw.strip())
    except ValueError:
        return None
    return port if port > 0 else None


def _default_mouth_udp_enabled() -> bool:
    return _env_bool("EDGE_VRM_BRIDGE_MOUTH_UDP_ENABLED", _default_mouth_udp_port() is not None)


@dataclass
class VrmBridgeConfig:
    """Configuration for the local VRM avatar bridge process."""

    host: str = DEFAULT_VRM_BRIDGE_HOST
    port: int = DEFAULT_VRM_BRIDGE_PORT
    api_timeout_s: float = DEFAULT_VRM_BRIDGE_TIMEOUT_S
    model_path: str | None = None
    mouth_udp_port: int | None = field(default_factory=_default_mouth_udp_port)
    mouth_udp_enabled: bool = field(default_factory=_default_mouth_udp_enabled)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{int(self.port)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": int(self.port),
            "api_timeout_s": float(self.api_timeout_s),
            "model_path": self.model_path,
            "base_url": self.base_url,
            "mouth_udp_port": self.mouth_udp_port,
            "mouth_udp_enabled": bool(self.mouth_udp_enabled and self.mouth_udp_port),
        }


class VrmBridgeDriver:
    """Sends avatar commands to the local VRM bridge HTTP endpoint."""

    backend = "vrm"

    def __init__(self, config: VrmBridgeConfig | None = None) -> None:
        self._config = config or VrmBridgeConfig()
        self._state = VtsState.DISCONNECTED
        self._lock = threading.Lock()
        self._connection_error: str | None = None
        self._current_expression: str | None = None
        self._talking = False
        self._last_command: dict[str, Any] | None = None
        self._last_health: dict[str, Any] | None = None
        self._last_model_move: dict[str, Any] = {"success": None, "reason": "not_started"}
        self._commands_sent = 0
        self._command_errors = 0
        self._mouth_udp_socket: socket.socket | None = None

    @property
    def state(self) -> VtsState:
        return self._state

    @property
    def talking(self) -> bool:
        return self._talking

    @property
    def current_expression(self) -> str | None:
        return self._current_expression

    def startup(self) -> None:
        self.connect()

    def shutdown(self) -> None:
        self.disconnect()

    def connect(self) -> VtsDriverResult:
        # 认证状态由本地 bridge 的健康响应确认，而非仅凭配置推断；失败时转入 ERROR，
        # 让上游收到可恢复的执行收据而不是假装命令已经抵达形象进程。
        with self._lock:
            self._state = VtsState.CONNECTING
            health, error = self._request_json("GET", "/health")
            if error is not None:
                self._state = VtsState.ERROR
                self._connection_error = error
                return VtsDriverResult(
                    success=False,
                    reason_code="VRM_BRIDGE_UNAVAILABLE",
                    detail=f"VRM bridge unavailable: {error}",
                    action="connect",
                )
            self._state = VtsState.AUTHENTICATED
            self._connection_error = None
            self._last_health = dict(health or {})
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="vrm bridge connected",
                action="connect",
            )

    def disconnect(self) -> VtsDriverResult:
        # UDP socket 属于此进程的短生命周期资源；断开时主动关闭，避免重连后继续复用
        # 指向旧 bridge 会话的本地描述符。
        self._state = VtsState.DISCONNECTED
        self._talking = False
        if self._mouth_udp_socket is not None:
            self._mouth_udp_socket.close()
            self._mouth_udp_socket = None
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="vrm bridge disconnected",
            action="disconnect",
        )

    def talk_start(self) -> VtsDriverResult:
        self._talking = True
        return self._send_command(
            "talk_start",
            {
                "type": "talk",
                "talking": True,
            },
        )

    def talk_end(self) -> VtsDriverResult:
        self._talking = False
        return self._send_command(
            "talk_end",
            {
                "type": "talk",
                "talking": False,
            },
        )

    def display_text(self, text: str) -> VtsDriverResult:
        message = str(text or "").strip()
        if not message:
            return VtsDriverResult(
                success=False,
                reason_code="VRM_BRIDGE_TEXT_EMPTY",
                detail="vrm bridge display_text requires non-empty text",
                action="display_text",
            )
        return self._send_command(
            "display_text",
            {
                "type": "say",
                "text": message,
            },
        )

    def set_expression(self, emotion_tag: str | None, *, intensity: float | None = None) -> VtsDriverResult:
        # 只向 bridge 发送规范化后的有限情绪标签，并将强度夹在 0..1；这是从上游
        # 语义事件进入具身执行面的参数边界。
        normalized = normalize_expression_emotion_tag(emotion_tag) or "NEUTRAL"
        safe_intensity = 0.65 if intensity is None else max(0.0, min(1.0, float(intensity)))
        self._current_expression = normalized
        rounded_intensity = round(safe_intensity, 4)
        return self._send_command(
            "set_expression",
            {
                "type": "expression",
                "emotion_tag": normalized,
                "emotion_intensity": rounded_intensity,
                "intensity": rounded_intensity,
                "value": rounded_intensity,
            },
            emotion_tag=normalized,
            expression_id=normalized,
        )

    def transition_expression(
        self,
        emotion_tag: str | None,
        *,
        transition_frames: list[dict[str, Any]] | None = None,
    ) -> VtsDriverResult:
        normalized = normalize_expression_emotion_tag(emotion_tag) or "NEUTRAL"
        self._current_expression = normalized
        frames = list(transition_frames or [])
        final_intensity = _transition_final_intensity(frames)
        return self._send_command(
            "transition_expression",
            {
                "type": "expression_transition",
                "emotion_tag": normalized,
                "emotion_intensity": final_intensity,
                "intensity": final_intensity,
                "value": final_intensity,
                "transition_frames": frames,
            },
            emotion_tag=normalized,
            expression_id=normalized,
        )

    def reset_to_neutral(self) -> VtsDriverResult:
        self._talking = False
        self._current_expression = "NEUTRAL"
        return self._send_command(
            "reset",
            {
                "type": "reset",
            },
            emotion_tag="NEUTRAL",
            expression_id="NEUTRAL",
        )

    def set_mouth_open_level(
        self,
        value: float,
        *,
        _include_companions: bool = False,
        _batch_companions: bool = False,
        _motion_values: dict[str, float] | None = None,
    ) -> VtsDriverResult:
        # 高频口型可选用 UDP 降低延迟；仍先收敛数值和运动字段，避免把任意对象
        # 直接序列化给本地执行器。
        mouth_value = max(0.0, min(1.0, float(value)))
        payload: dict[str, Any] = {
            "type": "mouth",
            "value": round(mouth_value, 4),
            "include_companions": bool(_include_companions),
            "batch_companions": bool(_batch_companions),
        }
        if _motion_values:
            payload["motion_values"] = {
                str(key): round(float(val), 5)
                for key, val in dict(_motion_values).items()
                if _is_number(val)
            }
        if self._config.mouth_udp_enabled and self._config.mouth_udp_port:
            return self._send_mouth_udp("set_mouth_open_level", payload)
        return self._send_command("set_mouth_open_level", payload)

    def move_model(
        self,
        *,
        position_x: float = 0.0,
        position_y: float = 0.0,
        rotation: float = 0.0,
        size: float = 0.0,
        relative: bool = True,
        move_time_s: float = 0.1,
    ) -> VtsDriverResult:
        payload = {
            "type": "move_model",
            "position_x": round(float(position_x), 5),
            "position_y": round(float(position_y), 5),
            "rotation": round(float(rotation), 5),
            "size": round(float(size), 5),
            "relative": bool(relative),
            "move_time_s": round(max(0.0, float(move_time_s)), 4),
        }
        result = self._send_command("move_model", payload)
        self._last_model_move = {
            "success": result.success,
            "detail": result.detail,
            "payload": payload,
        }
        return result

    def trigger_hotkey(self, hotkey_id: str | None) -> VtsDriverResult:
        # 热键可能映射任意本地形象动作，必须在网络请求前执行同一白名单判断，
        # 防止上游文本或配置绕过 VTS/VRM 共用的安全约束。
        hotkey = str(hotkey_id or "").strip()
        if is_blocked_avatar_hotkey_id(hotkey):
            return VtsDriverResult(
                success=False,
                reason_code=AVATAR_FREE_ACTION_BLOCKED,
                detail=f"blocked avatar hotkey: {hotkey}",
                action="trigger_hotkey",
                expression_id=hotkey or None,
            )
        return self._send_command(
            "trigger_hotkey",
            {
                "type": "hotkey",
                "hotkey_id": hotkey,
            },
        )

    def trigger_reconnect(self) -> None:
        self.connect()

    def health(self) -> dict[str, Any]:
        bridge_health, error = self._request_json("GET", "/health")
        if error is None:
            self._last_health = dict(bridge_health or {})
            if self._state not in {VtsState.AUTHENTICATED, VtsState.CONNECTING}:
                self._state = VtsState.AUTHENTICATED
        return {
            "backend": self.backend,
            "state": self._state.value,
            "status": "ok" if self._state == VtsState.AUTHENTICATED else "down",
            "connected": self._state == VtsState.AUTHENTICATED,
            "talking": self._talking,
            "current_expression": self._current_expression,
            "connection_error": error or self._connection_error,
            "commands_sent": self._commands_sent,
            "command_errors": self._command_errors,
            "last_command": self._last_command,
            "last_model_move": self._last_model_move,
            "bridge": bridge_health if error is None else self._last_health,
            "config": self._config.as_dict(),
            "mouth_parameters": {
                "selected": ["aa"],
                "last_value": (
                    (self._last_command or {}).get("value")
                    if (self._last_command or {}).get("type") == "mouth"
                    else None
                ),
                "last_motion_values": (
                    (self._last_command or {}).get("motion_values")
                    if isinstance((self._last_command or {}).get("motion_values"), dict)
                    else {}
                ),
            },
        }

    def _send_mouth_udp(self, action: str, payload: dict[str, Any]) -> VtsDriverResult:
        # UDP 没有服务端确认。默认信任已由本地部署边界保证的 bridge 仅用于口型帧；
        # 若关闭该信任开关，则退回健康检查以避免向未验证端点持续发送数据。
        if self._state != VtsState.AUTHENTICATED:
            if _env_bool("EDGE_VRM_MOUTH_UDP_TRUST_BRIDGE", True):
                self._state = VtsState.AUTHENTICATED
                self._connection_error = None
            else:
                connect_result = self.connect()
                if not connect_result.success:
                    return VtsDriverResult(
                        success=False,
                        reason_code=connect_result.reason_code,
                        detail=connect_result.detail,
                        action=action,
                    )
        command = {
            **dict(payload),
            "action": action,
            "sent_at": time.time(),
            "transport": "udp",
        }
        data = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            sent = self._get_mouth_udp_socket().sendto(
                data,
                (self._config.host, int(self._config.mouth_udp_port or DEFAULT_VRM_BRIDGE_MOUTH_UDP_PORT)),
            )
        except OSError as exc:
            # 将传输故障固化为状态与计数，调用者可据此恢复；不要在此处同步重试，
            # 以免语音帧积压并扩大实时通道的延迟。
            self._last_command = dict(command)
            self._command_errors += 1
            self._state = VtsState.ERROR
            self._connection_error = str(exc)
            return VtsDriverResult(
                success=False,
                reason_code="VRM_BRIDGE_MOUTH_UDP_FAILED",
                detail=f"VRM bridge mouth UDP failed: {exc}",
                action=action,
            )
        self._last_command = dict(command)
        self._last_command["bytes_sent"] = sent
        self._commands_sent += 1
        self._state = VtsState.AUTHENTICATED
        self._connection_error = None
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail=f"vrm bridge mouth frame sent over udp: {sent} bytes",
            action=action,
        )

    def _get_mouth_udp_socket(self) -> socket.socket:
        if self._mouth_udp_socket is None:
            self._mouth_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return self._mouth_udp_socket

    def _send_command(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        emotion_tag: str | None = None,
        expression_id: str | None = None,
    ) -> VtsDriverResult:
        # 所有 HTTP 命令复用同一连接门槛；连接失败会被原样转换为该动作的失败收据，
        # 因此上游能够区分“未执行”与 bridge 已接收的命令。
        if self._state != VtsState.AUTHENTICATED:
            connect_result = self.connect()
            if not connect_result.success:
                return VtsDriverResult(
                    success=False,
                    reason_code=connect_result.reason_code,
                    detail=connect_result.detail,
                    action=action,
                    expression_id=expression_id,
                    emotion_tag=emotion_tag,
                )
        command = {
            **dict(payload),
            "action": action,
            "sent_at": time.time(),
        }
        response, error = self._request_json("POST", "/command", command)
        self._last_command = dict(command)
        if error is not None:
            self._command_errors += 1
            self._state = VtsState.ERROR
            self._connection_error = error
            return VtsDriverResult(
                success=False,
                reason_code="VRM_BRIDGE_COMMAND_FAILED",
                detail=f"VRM bridge command failed: {error}",
                action=action,
                expression_id=expression_id,
                emotion_tag=emotion_tag,
            )
        self._commands_sent += 1
        self._state = VtsState.AUTHENTICATED
        self._connection_error = None
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail=str((response or {}).get("detail") or f"vrm bridge command accepted: {action}"),
            action=action,
            expression_id=expression_id,
            emotion_tag=emotion_tag,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        # 这里是唯一的本地 HTTP 出口：配置仅产生 bridge 基址，调用方只能选择固定
        # 的内部路径与已构造的 JSON 载荷，响应解析失败也被降为可报告错误。
        url = f"{self._config.base_url}{path}"
        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=float(self._config.api_timeout_s)) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return None, str(exc)
        if not body:
            return {}, None
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, f"invalid json response: {exc}"
        return (dict(parsed) if isinstance(parsed, dict) else {"data": parsed}), None


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _transition_final_intensity(frames: list[dict[str, Any]]) -> float:
    for frame in reversed(frames):
        if not isinstance(frame, dict):
            continue
        for key in ("value", "intensity", "emotion_intensity", "distance"):
            if not _is_number(frame.get(key)):
                continue
            return round(max(0.0, min(1.0, float(frame[key]))), 4)
    return 0.65
