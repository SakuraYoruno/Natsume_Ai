"""VTube Studio driver: WebSocket API connection, talk gate, and expression mapping.

Provides deterministic VTS integration with talk gate (TALK_START/TALK_END)
and emotion_tag → expression_id mapping. Falls back gracefully when VTS
is disconnected or API calls fail.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from host_brain.core.errors import (
    AVATAR_FREE_ACTION_BLOCKED,
    AVATAR_TAG_INVALID,
    VTS_API_ERROR,
    VTS_CONNECTION_FAILED,
    VTS_EXPRESSION_TIMEOUT,
    VTS_RECONNECTED,
    VTS_RECONNECT_EXHAUSTED,
)
from host_brain.core.ids import new_request_id, now_ms

logger = logging.getLogger("natsume.edge.vts")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_VTS_HOST = "localhost"
DEFAULT_VTS_PORT = 8001
DEFAULT_VTS_PLUGIN_NAME = "NatsumeAi"
DEFAULT_VTS_PLUGIN_DEVELOPER = "natsume"
DEFAULT_VTS_TOKEN_PATH = ".vts_token"
DEFAULT_API_TIMEOUT_S = 5.0

TALK_START = "TALK_START"
TALK_END = "TALK_END"

# 确定性表情映射表（与 host_brain/channels/vts.py 一致）
EMOTION_TO_EXPRESSION_ID: dict[str, str] = {
    "NEUTRAL": "exp_neutral_01",
    "HAPPY": "exp_happy_01",
    "SAD": "exp_sad_01",
    "ANGRY": "exp_angry_01",
    "SURPRISED": "exp_surprised_01",
    "THINKING": "exp_thinking_01",
}

# VTS 参数映射（口型用 mouth_open_level）
VTS_MOUTH_PARAM = "MouthOpen"
VTS_MOUTH_OPEN_VALUE = 1.0
VTS_MOUTH_CLOSE_VALUE = 0.0


# ── 重连配置常量 ──
DEFAULT_MAX_RECONNECT_ATTEMPTS = 10
DEFAULT_RECONNECT_BASE_DELAY_S = 1.0
DEFAULT_RECONNECT_MAX_DELAY_S = 16.0


class VtsState(str, Enum):
    """VTS driver lifecycle states."""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class ReconnectConfig:
    """VTS WebSocket 自动重连配置。"""
    max_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS
    base_delay_s: float = DEFAULT_RECONNECT_BASE_DELAY_S
    max_delay_s: float = DEFAULT_RECONNECT_MAX_DELAY_S
    auto_reconnect: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "base_delay_s": self.base_delay_s,
            "max_delay_s": self.max_delay_s,
            "auto_reconnect": self.auto_reconnect,
        }


@dataclass
class VtsConfig:
    """VTube Studio driver configuration."""
    host: str = DEFAULT_VTS_HOST
    port: int = DEFAULT_VTS_PORT
    plugin_name: str = DEFAULT_VTS_PLUGIN_NAME
    plugin_developer: str = DEFAULT_VTS_PLUGIN_DEVELOPER
    token_path: str = DEFAULT_VTS_TOKEN_PATH
    api_timeout_s: float = DEFAULT_API_TIMEOUT_S
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "plugin_name": self.plugin_name,
            "plugin_developer": self.plugin_developer,
            "token_path": self.token_path,
            "api_timeout_s": self.api_timeout_s,
            "reconnect": self.reconnect.as_dict(),
        }


@dataclass
class VtsDriverResult:
    """Result of a VTS API operation."""
    success: bool
    reason_code: str | None
    detail: str
    action: str = ""
    expression_id: str | None = None
    emotion_tag: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "action": self.action,
            "expression_id": self.expression_id,
            "emotion_tag": self.emotion_tag,
        }


# ---------------------------------------------------------------------------
# VTube Studio Driver
# ---------------------------------------------------------------------------

class VTubeStudioDriver:
    """Manages VTube Studio WebSocket connection and deterministic controls.

    Provides:
    - Connection lifecycle (connect/authenticate/disconnect)
    - Talk gate (TALK_START -> mouth open, TALK_END -> mouth close)
    - Emotion tag → expression_id deterministic mapping
    - Barge-in recovery (stop talking → reset to NEUTRAL)
    - Graceful degradation when VTS is unavailable
    """

    def __init__(self, config: VtsConfig | None = None) -> None:
        self._config = config or VtsConfig()
        self._vts: Any = None
        self._state = VtsState.DISCONNECTED
        self._lock = threading.Lock()
        self._connection_error: str | None = None
        self._current_expression: str | None = None
        self._talking = False
        # ── 重连状态 ──
        self._reconnect_attempts = 0
        self._total_reconnects = 0
        self._total_reconnect_failures = 0
        self._reconnect_thread: threading.Thread | None = None
        self._reconnect_events: list[dict[str, Any]] = []
        self._shutdown_event = threading.Event()

    @property
    def state(self) -> VtsState:
        return self._state

    @property
    def talking(self) -> bool:
        return self._talking

    @property
    def current_expression(self) -> str | None:
        return self._current_expression

    def connect(self) -> VtsDriverResult:
        """Connect to VTube Studio and authenticate."""
        with self._lock:
            if self._state == VtsState.AUTHENTICATED:
                return VtsDriverResult(
                    success=True,
                    reason_code=None,
                    detail="already connected and authenticated",
                    action="connect",
                )

            self._state = VtsState.CONNECTING

            try:
                vts_instance = _create_vts_connection(self._config)
                self._vts = vts_instance
                self._state = VtsState.CONNECTED

                # 认证
                _authenticate_vts(
                    vts=vts_instance,
                    plugin_name=self._config.plugin_name,
                    plugin_developer=self._config.plugin_developer,
                    token_path=self._config.token_path,
                )
                self._state = VtsState.AUTHENTICATED
                logger.info(
                    "VTS connected and authenticated: %s:%d",
                    self._config.host,
                    self._config.port,
                )
                return VtsDriverResult(
                    success=True,
                    reason_code=None,
                    detail=f"connected to VTS at {self._config.host}:{self._config.port}",
                    action="connect",
                )
            except ImportError as e:
                self._state = VtsState.ERROR
                self._connection_error = str(e)
                logger.error("pyvts not installed: %s", e)
                return VtsDriverResult(
                    success=False,
                    reason_code=VTS_CONNECTION_FAILED,
                    detail=f"pyvts package not available: {e}",
                    action="connect",
                )
            except Exception as e:
                self._state = VtsState.ERROR
                self._connection_error = str(e)
                logger.error("VTS connection failed: %s", e)
                return VtsDriverResult(
                    success=False,
                    reason_code=VTS_CONNECTION_FAILED,
                    detail=f"VTS connection error: {e}",
                    action="connect",
                )

    def disconnect(self) -> VtsDriverResult:
        """Disconnect from VTube Studio."""
        with self._lock:
            self._shutdown_event.set()
            self._vts = None
            self._state = VtsState.DISCONNECTED
            self._talking = False
            self._current_expression = None
            logger.info("VTS disconnected")
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="VTS disconnected",
                action="disconnect",
            )

    def talk_start(self) -> VtsDriverResult:
        """Signal talk gate start (open mouth parameter)."""
        if self._state != VtsState.AUTHENTICATED:
            conn_result = self.connect()
            if not conn_result.success:
                return VtsDriverResult(
                    success=False,
                    reason_code=VTS_CONNECTION_FAILED,
                    detail=f"cannot start talk: {conn_result.detail}",
                    action=TALK_START,
                )

        try:
            _set_vts_parameter(
                vts=self._vts,
                param_name=VTS_MOUTH_PARAM,
                value=VTS_MOUTH_OPEN_VALUE,
                timeout_s=self._config.api_timeout_s,
            )
            self._talking = True
            logger.debug("VTS talk gate: START")
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="talk gate started (mouth open)",
                action=TALK_START,
            )
        except TimeoutError as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_EXPRESSION_TIMEOUT,
                detail=f"talk start timeout: {e}",
                action=TALK_START,
            )
        except Exception as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"talk start error: {e}",
                action=TALK_START,
            )

    def talk_end(self) -> VtsDriverResult:
        """Signal talk gate end (close mouth, reset expression to NEUTRAL)."""
        self._talking = False

        if self._state != VtsState.AUTHENTICATED:
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="talk gate ended (VTS not connected, no-op)",
                action=TALK_END,
            )

        try:
            _set_vts_parameter(
                vts=self._vts,
                param_name=VTS_MOUTH_PARAM,
                value=VTS_MOUTH_CLOSE_VALUE,
                timeout_s=self._config.api_timeout_s,
            )
            logger.debug("VTS talk gate: END")
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="talk gate ended (mouth closed)",
                action=TALK_END,
            )
        except Exception as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"talk end error: {e}",
                action=TALK_END,
            )

    def set_expression(self, emotion_tag: str) -> VtsDriverResult:
        """Set expression via deterministic emotion_tag → expression_id mapping.

        Rejects unknown tags and free-form parameters.
        """
        normalized = str(emotion_tag or "").strip().upper()
        if not normalized:
            return VtsDriverResult(
                success=False,
                reason_code=AVATAR_TAG_INVALID,
                detail="emotion_tag is required",
                action="set_expression",
                emotion_tag=None,
            )

        expression_id = EMOTION_TO_EXPRESSION_ID.get(normalized)
        if not expression_id:
            return VtsDriverResult(
                success=False,
                reason_code=AVATAR_TAG_INVALID,
                detail=f"unsupported emotion_tag: {normalized}",
                action="set_expression",
                emotion_tag=normalized,
            )

        if self._state != VtsState.AUTHENTICATED:
            # 降级：记录但不阻断
            self._current_expression = expression_id
            return VtsDriverResult(
                success=True,
                reason_code=VTS_CONNECTION_FAILED,
                detail=f"expression queued (VTS not connected): {expression_id}",
                action="set_expression",
                expression_id=expression_id,
                emotion_tag=normalized,
            )

        try:
            _trigger_vts_hotkey(
                vts=self._vts,
                hotkey_id=expression_id,
                timeout_s=self._config.api_timeout_s,
            )
            self._current_expression = expression_id
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail=f"expression set: {normalized} -> {expression_id}",
                action="set_expression",
                expression_id=expression_id,
                emotion_tag=normalized,
            )
        except TimeoutError as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_EXPRESSION_TIMEOUT,
                detail=f"expression timeout: {e}",
                action="set_expression",
                expression_id=expression_id,
                emotion_tag=normalized,
            )
        except Exception as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"expression error: {e}",
                action="set_expression",
                expression_id=expression_id,
                emotion_tag=normalized,
            )

    def reset_to_neutral(self) -> VtsDriverResult:
        """Reset expression to NEUTRAL (used after barge-in or end of talk)."""
        self._talking = False
        result = self.set_expression("NEUTRAL")

        # 同时关闭嘴巴
        if self._state == VtsState.AUTHENTICATED:
            try:
                _set_vts_parameter(
                    vts=self._vts,
                    param_name=VTS_MOUTH_PARAM,
                    value=VTS_MOUTH_CLOSE_VALUE,
                    timeout_s=self._config.api_timeout_s,
                )
            except Exception:
                pass

        return result

    # -----------------------------------------------------------------
    # WebSocket 自动重连（TASK-0080）
    # -----------------------------------------------------------------

    def _reconnect_loop(self) -> None:
        """指数退避 WebSocket 自动重连循环。

        在独立线程中运行。重连成功后恢复 neutral 表情并上报
        VTS_RECONNECTED 事件；全部失败后进入 DISCONNECTED 并上报
        VTS_RECONNECT_EXHAUSTED。
        """
        rc = self._config.reconnect
        delay = rc.base_delay_s
        self._reconnect_attempts = 0

        with self._lock:
            self._state = VtsState.RECONNECTING

        logger.info(
            "VTS reconnect loop started (max_attempts=%d, base_delay=%.1fs)",
            rc.max_attempts, rc.base_delay_s,
        )

        for attempt in range(1, rc.max_attempts + 1):
            if self._shutdown_event.is_set():
                logger.info("VTS reconnect cancelled by shutdown")
                return

            self._reconnect_attempts = attempt
            logger.info(
                "VTS reconnect attempt %d/%d (delay=%.1fs)",
                attempt, rc.max_attempts, delay,
            )

            try:
                result = self.connect()
                if result.success:
                    # 重连成功——恢复 neutral
                    self._total_reconnects += 1
                    neutral_result = self.reset_to_neutral()
                    event = {
                        "event": VTS_RECONNECTED,
                        "attempt": attempt,
                        "ts_ms": now_ms(),
                        "detail": f"reconnected after {attempt} attempt(s)",
                        "neutral_restored": neutral_result.success,
                    }
                    self._reconnect_events.append(event)
                    logger.info(
                        "VTS reconnected after %d attempt(s)", attempt,
                    )
                    return
            except Exception as exc:
                logger.warning(
                    "VTS reconnect attempt %d failed: %s", attempt, exc,
                )

            # 等待退避时间（可被 shutdown 中断）
            if self._shutdown_event.wait(timeout=min(delay, rc.max_delay_s)):
                logger.info("VTS reconnect cancelled by shutdown during wait")
                return
            delay = min(delay * 2, rc.max_delay_s)

        # 全部重连失败
        self._total_reconnect_failures += 1
        with self._lock:
            self._state = VtsState.DISCONNECTED

        event = {
            "event": VTS_RECONNECT_EXHAUSTED,
            "attempts": rc.max_attempts,
            "ts_ms": now_ms(),
            "detail": f"all {rc.max_attempts} reconnect attempts exhausted",
        }
        self._reconnect_events.append(event)
        logger.error(
            "VTS reconnect exhausted after %d attempts", rc.max_attempts,
        )

    def trigger_reconnect(self) -> VtsDriverResult:
        """触发 WebSocket 自动重连（非阻塞）。

        通常由 API 调用失败时自动触发，也可手动调用。
        如果已在重连中则返回 no-op。
        """
        rc = self._config.reconnect
        if not rc.auto_reconnect:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_CONNECTION_FAILED,
                detail="auto_reconnect is disabled",
                action="trigger_reconnect",
            )

        with self._lock:
            if self._state == VtsState.RECONNECTING:
                return VtsDriverResult(
                    success=True,
                    reason_code=None,
                    detail="reconnect already in progress",
                    action="trigger_reconnect",
                )
            if self._state == VtsState.SHUTDOWN:
                return VtsDriverResult(
                    success=False,
                    reason_code=VTS_CONNECTION_FAILED,
                    detail="driver is shutdown",
                    action="trigger_reconnect",
                )

        self._shutdown_event.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            name="vts-reconnect",
            daemon=True,
        )
        self._reconnect_thread.start()

        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="reconnect started",
            action="trigger_reconnect",
        )

    def _maybe_trigger_reconnect(self, reason: str) -> None:
        """API 调用失败时检查是否需要自动重连。"""
        rc = self._config.reconnect
        if not rc.auto_reconnect:
            return
        if self._state in (VtsState.RECONNECTING, VtsState.SHUTDOWN):
            return
        logger.warning("VTS connection lost (%s), triggering reconnect", reason)
        self.trigger_reconnect()

    @property
    def reconnect_events(self) -> list[dict[str, Any]]:
        """返回重连事件日志（可写入审计）。"""
        return list(self._reconnect_events)

    def health(self) -> dict[str, Any]:
        """Return VTS driver health snapshot."""
        return {
            "state": self._state.value,
            "talking": self._talking,
            "current_expression": self._current_expression,
            "config": self._config.as_dict(),
            "connection_error": self._connection_error,
            "emotion_map_size": len(EMOTION_TO_EXPRESSION_ID),
            "reconnect": {
                "total_reconnects": self._total_reconnects,
                "total_reconnect_failures": self._total_reconnect_failures,
                "reconnect_attempts": self._reconnect_attempts,
                "reconnecting": self._state == VtsState.RECONNECTING,
                "reconnect_events_count": len(self._reconnect_events),
            },
        }


# ---------------------------------------------------------------------------
# Internal: pyvts SDK bridge (isolates actual SDK dependency)
# ---------------------------------------------------------------------------

def _create_vts_connection(config: VtsConfig) -> Any:
    """Create a pyvts VTS instance. Raises ImportError or ConnectionError."""
    try:
        import pyvts  # type: ignore[import-not-found]
    except ImportError:
        raise ImportError(
            "pyvts not found. Install with: pip install pyvts"
        )

    vts = pyvts.vts(
        plugin_info={
            "plugin_name": config.plugin_name,
            "developer": config.plugin_developer,
            "authentication_token_path": config.token_path,
        },
        vts_api_info={
            "host": config.host,
            "port": config.port,
        },
    )
    return vts


def _authenticate_vts(
    *,
    vts: Any,
    plugin_name: str,
    plugin_developer: str,
    token_path: str,
) -> None:
    """Authenticate with VTube Studio API."""
    loop = _get_or_create_event_loop()
    loop.run_until_complete(vts.connect())
    loop.run_until_complete(vts.request_authenticate_token())
    loop.run_until_complete(vts.request_authenticate())


def _set_vts_parameter(
    *,
    vts: Any,
    param_name: str,
    value: float,
    timeout_s: float,
) -> None:
    """Set a VTS input parameter value."""
    if vts is None:
        return

    loop = _get_or_create_event_loop()

    async def _set() -> None:
        request = vts.vts_request.BaseRequest(
            "InjectParameterDataRequest",
            {
                "parameterValues": [
                    {
                        "id": param_name,
                        "value": value,
                    }
                ],
            },
        )
        await asyncio.wait_for(
            vts.request(request),
            timeout=timeout_s,
        )

    loop.run_until_complete(_set())


def _trigger_vts_hotkey(
    *,
    vts: Any,
    hotkey_id: str,
    timeout_s: float,
) -> None:
    """Trigger a VTS hotkey by ID."""
    if vts is None:
        return

    loop = _get_or_create_event_loop()

    async def _trigger() -> None:
        request = vts.vts_request.BaseRequest(
            "HotkeyTriggerRequest",
            {
                "hotkeyID": hotkey_id,
            },
        )
        await asyncio.wait_for(
            vts.request(request),
            timeout=timeout_s,
        )

    loop.run_until_complete(_trigger())


def _get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """Get existing or create new event loop for sync VTS calls."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop
