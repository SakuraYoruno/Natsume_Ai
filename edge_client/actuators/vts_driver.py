"""VTube Studio driver: WebSocket API connection, talk gate, and expression mapping.

Provides deterministic VTS integration with talk gate (TALK_START/TALK_END)
and emotion_tag 鈫?expression_id mapping. Falls back gracefully when VTS
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

from host_brain.channels.vts_runtime_mapping import load_runtime_emotion_mapping
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

logger = logging.getLogger("Natsume.edge.vts")

_VTS_IO_LOOP: asyncio.AbstractEventLoop | None = None
_VTS_IO_THREAD: threading.Thread | None = None
_VTS_IO_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_VTS_HOST = "localhost"
DEFAULT_VTS_PORT = 8001
DEFAULT_VTS_PLUGIN_NAME = "NatsumeAi"
DEFAULT_VTS_PLUGIN_DEVELOPER = "Natsume"
DEFAULT_VTS_TOKEN_PATH = ".vts_token"
DEFAULT_API_TIMEOUT_S = 5.0

TALK_START = "TALK_START"
TALK_END = "TALK_END"

# 纭畾鎬ц〃鎯呮槧灏勮〃锛堜笌 host_brain/channels/vts.py 涓€鑷达級
EMOTION_TO_EXPRESSION_ID: dict[str, str] = load_runtime_emotion_mapping()

# Candidate VTS input parameters used for mouth sync. Some models wire lipsync
# to the standard tracking params, others to the advanced voice-volume params.
DEFAULT_MOUTH_PARAM_CANDIDATES = ("MouthOpen", "ParamMouthOpenY")
DEFAULT_MOUTH_VOLUME_PARAM_CANDIDATES = ("VoiceVolumePlusMouthOpen", "VoiceVolume")
DEFAULT_VOICE_SILENCE_PARAM_CANDIDATES = ("VoiceSilence",)
UNSAFE_GLOBAL_RESET_HOTKEY_SELECTORS = {
    "移除所有按键表情",
    "hotkey_reset_all",
}

# Backward-compatible single-name aliases.
VTS_MOUTH_PARAM = DEFAULT_MOUTH_PARAM_CANDIDATES[0]
VTS_MOUTH_PLUS_VOLUME_PARAM = DEFAULT_MOUTH_VOLUME_PARAM_CANDIDATES[0]
VTS_VOICE_SILENCE_PARAM = DEFAULT_VOICE_SILENCE_PARAM_CANDIDATES[0]
VTS_MOUTH_OPEN_VALUE = 1.0
VTS_MOUTH_CLOSE_VALUE = 0.0


# 鈹€鈹€ 閲嶈繛閰嶇疆甯搁噺 鈹€鈹€
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
    """Configuration for automatic VTS websocket reconnect behavior."""
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
    mouth_param_candidates: tuple[str, ...] = DEFAULT_MOUTH_PARAM_CANDIDATES
    mouth_volume_param_candidates: tuple[str, ...] = DEFAULT_MOUTH_VOLUME_PARAM_CANDIDATES
    voice_silence_param_candidates: tuple[str, ...] = DEFAULT_VOICE_SILENCE_PARAM_CANDIDATES
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "plugin_name": self.plugin_name,
            "plugin_developer": self.plugin_developer,
            "token_path": self.token_path,
            "api_timeout_s": self.api_timeout_s,
            "mouth_param_candidates": list(self.mouth_param_candidates),
            "mouth_volume_param_candidates": list(self.mouth_volume_param_candidates),
            "voice_silence_param_candidates": list(self.voice_silence_param_candidates),
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
    - Emotion tag 鈫?expression_id deterministic mapping
    - Barge-in recovery (stop talking 鈫?reset to NEUTRAL)
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
        # 鈹€鈹€ 閲嶈繛鐘舵€?鈹€鈹€
        self._reconnect_attempts = 0
        self._total_reconnects = 0
        self._total_reconnect_failures = 0
        self._reconnect_thread: threading.Thread | None = None
        self._reconnect_events: list[dict[str, Any]] = []
        self._shutdown_event = threading.Event()
        self._available_input_parameters: set[str] = set()
        self._mouth_parameter_plan: dict[str, Any] = _fallback_mouth_parameter_plan(self._config)

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

                # 璁よ瘉
                _authenticate_vts(
                    vts=vts_instance,
                    plugin_name=self._config.plugin_name,
                    plugin_developer=self._config.plugin_developer,
                    token_path=self._config.token_path,
                )
                self._state = VtsState.AUTHENTICATED
                try:
                    self._refresh_input_parameter_plan()
                except Exception as exc:
                    logger.warning("VTS input parameter discovery failed, using fallback plan: %s", exc)
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
            self._available_input_parameters = set()
            self._mouth_parameter_plan = _fallback_mouth_parameter_plan(self._config)
            logger.info("VTS disconnected")
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="VTS disconnected",
                action="disconnect",
            )

    def talk_start(self) -> VtsDriverResult:
        """Signal talk gate start (open mouth parameter)."""
        result = self.set_mouth_open_level(VTS_MOUTH_OPEN_VALUE)
        if result.success:
            self._talking = True
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="talk gate started (mouth open)",
                action=TALK_START,
            )
        return VtsDriverResult(
            success=False,
            reason_code=result.reason_code,
            detail=f"talk start error: {result.detail}",
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

        result = self.set_mouth_open_level(VTS_MOUTH_CLOSE_VALUE)
        if result.success:
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="talk gate ended (mouth closed)",
                action=TALK_END,
            )
        return VtsDriverResult(
            success=False,
            reason_code=result.reason_code,
            detail=f"talk end error: {result.detail}",
            action=TALK_END,
        )

    def set_expression(self, emotion_tag: str, *, intensity: float = 0.5) -> VtsDriverResult:
        """Resolve an emotion tag to a configured VTS hotkey and trigger it."""
        normalized = str(emotion_tag or "").strip().upper()
        if not normalized:
            return VtsDriverResult(
                success=False,
                reason_code=AVATAR_TAG_INVALID,
                detail="emotion_tag is required",
                action="set_expression",
                emotion_tag=None,
            )

        configured_hotkey = EMOTION_TO_EXPRESSION_ID.get(normalized)
        if not configured_hotkey:
            return VtsDriverResult(
                success=False,
                reason_code=AVATAR_TAG_INVALID,
                detail=f"unsupported emotion_tag: {normalized}",
                action="set_expression",
                emotion_tag=normalized,
            )

        if self._state != VtsState.AUTHENTICATED:
            # Degrade gracefully when VTS is unavailable.
            self._current_expression = configured_hotkey
            return VtsDriverResult(
                success=True,
                reason_code=VTS_CONNECTION_FAILED,
                detail=f"expression queued (VTS not connected): {configured_hotkey}",
                action="set_expression",
                expression_id=configured_hotkey,
                emotion_tag=normalized,
            )

        try:
            resolved_hotkey_id, resolved_label = _resolve_vts_hotkey(
                vts=self._vts,
                configured_hotkey=configured_hotkey,
                timeout_s=self._config.api_timeout_s,
            )
            if self._current_expression in {resolved_label, resolved_hotkey_id, configured_hotkey}:
                return VtsDriverResult(
                    success=True,
                    reason_code=None,
                    detail=f"expression already active: {resolved_label} ({resolved_hotkey_id})",
                    action="set_expression",
                    expression_id=resolved_hotkey_id,
                    emotion_tag=normalized,
                )
            logger.info(
                "VTS expression: emotion_tag=%s -> hotkey_id=%s (%s)",
                normalized, resolved_hotkey_id, resolved_label,
            )
            _trigger_vts_hotkey(
                vts=self._vts,
                hotkey_id=resolved_hotkey_id,
                timeout_s=self._config.api_timeout_s,
            )
            self._current_expression = resolved_label
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail=f"expression set: {normalized} -> {resolved_label} ({resolved_hotkey_id})",
                action="set_expression",
                expression_id=resolved_hotkey_id,
                emotion_tag=normalized,
            )
        except TimeoutError as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_EXPRESSION_TIMEOUT,
                detail=f"expression timeout: {e}",
                action="set_expression",
                expression_id=configured_hotkey,
                emotion_tag=normalized,
            )
        except Exception as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"expression error: {e}",
                action="set_expression",
                expression_id=configured_hotkey,
                emotion_tag=normalized,
            )

    def reset_to_neutral(self) -> VtsDriverResult:
        """Reset expression to NEUTRAL (used after barge-in or end of talk)."""
        self._talking = False
        configured_hotkey = str(EMOTION_TO_EXPRESSION_ID.get("NEUTRAL") or "").strip()
        if _is_unsafe_global_reset_hotkey(configured_hotkey):
            result = VtsDriverResult(
                success=True,
                reason_code=None,
                detail=(
                    "neutral hotkey skipped because it is a global reset "
                    "and may overwrite manual VTS toggles"
                ),
                action="reset_to_neutral",
                expression_id=configured_hotkey or None,
                emotion_tag="NEUTRAL",
            )
        else:
            result = self.set_expression("NEUTRAL")

        # 鍚屾椂鍏抽棴鍢村反
        if self._state == VtsState.AUTHENTICATED:
            try:
                self.set_mouth_open_level(VTS_MOUTH_CLOSE_VALUE)
            except Exception:
                pass

        return result

    def trigger_hotkey(self, hotkey_id: str) -> VtsDriverResult:
        """Trigger a concrete VTS hotkey id without free-form parameters."""
        if not hotkey_id or not str(hotkey_id).strip():
            return VtsDriverResult(
                success=False,
                reason_code=AVATAR_TAG_INVALID,
                detail="hotkey_id is required",
                action="trigger_hotkey",
            )

        normalized_id = str(hotkey_id).strip()

        if self._state != VtsState.AUTHENTICATED:
            # Degrade gracefully when VTS is unavailable.
            logger.debug("VTS trigger_hotkey degraded (not connected): %s", normalized_id)
            return VtsDriverResult(
                success=True,
                reason_code=VTS_CONNECTION_FAILED,
                detail=f"hotkey queued (VTS not connected): {normalized_id}",
                action="trigger_hotkey",
            )

        try:
            resolved_hotkey_id, resolved_label = _resolve_vts_hotkey(
                vts=self._vts,
                configured_hotkey=normalized_id,
                timeout_s=self._config.api_timeout_s,
            )
            _trigger_vts_hotkey(
                vts=self._vts,
                hotkey_id=resolved_hotkey_id,
                timeout_s=self._config.api_timeout_s,
            )
            logger.debug("VTS trigger_hotkey: %s -> %s", resolved_label, resolved_hotkey_id)
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail=f"hotkey triggered: {resolved_label} ({resolved_hotkey_id})",
                action="trigger_hotkey",
            )
        except TimeoutError as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_EXPRESSION_TIMEOUT,
                detail=f"hotkey trigger timeout: {e}",
                action="trigger_hotkey",
            )
        except Exception as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"hotkey trigger error: {e}",
                action="trigger_hotkey",
            )

    def set_mouth_open_level(self, value: float) -> VtsDriverResult:
        """Set VTS mouth open parameter to a concrete normalized value."""
        if self._state != VtsState.AUTHENTICATED:
            conn_result = self.connect()
            if not conn_result.success:
                return VtsDriverResult(
                    success=False,
                    reason_code=VTS_CONNECTION_FAILED,
                    detail=f"cannot set mouth parameter: {conn_result.detail}",
                    action="set_mouth_open_level",
                )
        normalized = max(0.0, min(1.0, float(value)))
        if not self._available_input_parameters:
            try:
                self._refresh_input_parameter_plan()
            except Exception as exc:
                logger.debug("VTS mouth parameter discovery refresh failed: %s", exc)
        plan = self._mouth_parameter_plan
        parameter_payload = _build_mouth_parameter_payload(plan, normalized)
        try:
            _set_vts_parameters(
                vts=self._vts,
                parameters=parameter_payload,
                timeout_s=self._config.api_timeout_s,
            )
            selected = list(self._mouth_parameter_plan.get("selected") or [])
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail=(
                    "mouth parameters set: "
                    + ", ".join(
                        f"{param_name}={param_value:.3f}"
                        for param_name, param_value in parameter_payload.items()
                    )
                    + (f" (selected={selected})" if selected else "")
                ),
                action="set_mouth_open_level",
            )
        except TimeoutError as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_EXPRESSION_TIMEOUT,
                detail=f"mouth parameter timeout: {e}",
                action="set_mouth_open_level",
            )
        except Exception as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"mouth parameter error: {e}",
                action="set_mouth_open_level",
            )

    # -----------------------------------------------------------------
    # WebSocket 鑷姩閲嶈繛锛圱ASK-0080锛?    # -----------------------------------------------------------------

    def _reconnect_loop(self) -> None:
        """Run the exponential backoff reconnect loop for the VTS websocket."""
        rc = self._config.reconnect
        delay = rc.base_delay_s
        self._reconnect_attempts = 0

        # 娉ㄦ剰锛歴tate 宸茬敱 trigger_reconnect 鍦?lock 鍐呰缃负 RECONNECTING
        # 姝ゅ鏃犻渶閲嶅璁剧疆锛岄伩鍏嶇珵鎬?
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
                    # 閲嶈繛鎴愬姛鈥斺€旀仮澶?neutral
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
                else:
                    # connect 澶辫触锛岀‘淇?state 淇濇寔 RECONNECTING
                    with self._lock:
                        if self._state != VtsState.SHUTDOWN:
                            self._state = VtsState.RECONNECTING
            except Exception as exc:
                logger.warning(
                    "VTS reconnect attempt %d failed: %s", attempt, exc,
                )
                with self._lock:
                    if self._state != VtsState.SHUTDOWN:
                        self._state = VtsState.RECONNECTING
            # Wait between reconnect attempts unless shutdown interrupts the backoff.
            if self._shutdown_event.wait(timeout=min(delay, rc.max_delay_s)):
                logger.info("VTS reconnect cancelled by shutdown during wait")
                return
            delay = min(delay * 2, rc.max_delay_s)

        # 鍏ㄩ儴閲嶈繛澶辫触
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
        """Start the non-blocking reconnect loop when auto-reconnect is enabled."""
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
            # Mark reconnecting while still under the lock to avoid races.
            self._state = VtsState.RECONNECTING

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
        """Trigger reconnect after an API failure when reconnect is enabled."""
        rc = self._config.reconnect
        if not rc.auto_reconnect:
            return
        if self._state in (VtsState.RECONNECTING, VtsState.SHUTDOWN):
            return
        logger.warning("VTS connection lost (%s), triggering reconnect", reason)
        self.trigger_reconnect()

    def _refresh_input_parameter_plan(self) -> None:
        if self._state != VtsState.AUTHENTICATED or self._vts is None:
            self._available_input_parameters = set()
            self._mouth_parameter_plan = _fallback_mouth_parameter_plan(self._config)
            return
        available = _list_vts_input_parameters(
            vts=self._vts,
            timeout_s=self._config.api_timeout_s,
        )
        self._available_input_parameters = set(available)
        self._mouth_parameter_plan = _resolve_mouth_parameter_plan(
            config=self._config,
            available_parameters=self._available_input_parameters,
        )
        logger.info("VTS mouth parameter plan resolved: %s", self._mouth_parameter_plan)

    @property
    def reconnect_events(self) -> list[dict[str, Any]]:
        """Return a copy of reconnect lifecycle events."""
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
            "input_parameters": {
                "available_count": len(self._available_input_parameters),
                "sample": sorted(self._available_input_parameters)[:20],
            },
            "mouth_parameters": dict(self._mouth_parameter_plan),
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
            "name": "VTubeStudioPublicAPI",
            "version": "1.0",
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
    async def _auth() -> None:
        await vts.connect()
        token = await vts.read_token()
        if not token:
            token_request = vts.vts_request.authentication_token()
            token_response = await vts.request(token_request)
            token_data = dict(token_response.get("data") or {})
            token = str(token_data.get("authenticationToken") or "").strip()
            if not token:
                raise RuntimeError(_extract_vts_api_error(
                    token_response,
                    default="failed to get authentication token",
                ))
            vts.authentic_token = token
            await vts.write_token()
        else:
            vts.authentic_token = token

        auth_request = vts.vts_request.authentication(vts.authentic_token)
        auth_response = await vts.request(auth_request)
        auth_ok = bool((auth_response.get("data") or {}).get("authenticated"))
        if not auth_ok:
            raise RuntimeError(_extract_vts_api_error(
                auth_response,
                default="authentication rejected by VTube Studio",
            ))

    _run_in_vts_loop(_auth(), timeout_s=30.0)


def _set_vts_parameters(
    *,
    vts: Any,
    parameters: dict[str, float],
    timeout_s: float,
) -> None:
    """Set one or more VTS input parameter values."""
    if vts is None:
        return
    payload = [
        {
            "id": str(param_name),
            "value": float(value),
        }
        for param_name, value in parameters.items()
    ]
    if not payload:
        return

    async def _set() -> None:
        request = vts.vts_request.BaseRequest(
            "InjectParameterDataRequest",
            {
                "faceFound": True,
                "mode": "set",
                "parameterValues": payload,
            },
        )
        await asyncio.wait_for(
            vts.request(request),
            timeout=timeout_s,
        )

    _run_in_vts_loop(_set(), timeout_s=timeout_s + 1.0)


def _trigger_vts_hotkey(
    *,
    vts: Any,
    hotkey_id: str,
    timeout_s: float,
) -> None:
    """Trigger a VTS hotkey by ID."""
    if vts is None:
        return

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

    _run_in_vts_loop(_trigger(), timeout_s=timeout_s + 1.0)


def _list_vts_input_parameters(*, vts: Any, timeout_s: float) -> set[str]:
    """Return currently available VTS input parameter names."""
    if vts is None:
        return set()

    async def _load() -> set[str]:
        request = vts.vts_request.BaseRequest("InputParameterListRequest", {})
        response = await asyncio.wait_for(
            vts.request(request),
            timeout=timeout_s,
        )
        data = dict(response.get("data") or {})
        out: set[str] = set()
        for group_name in ("defaultParameters", "customParameters"):
            for item in list(data.get(group_name) or []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    out.add(name)
        return out

    return _run_in_vts_loop(_load(), timeout_s=timeout_s + 1.0)


def _resolve_vts_hotkey(*, vts: Any, configured_hotkey: str, timeout_s: float) -> tuple[str, str]:
    """Resolve a configured hotkey selector to the current model's concrete VTS hotkey id."""
    if vts is None:
        raise RuntimeError("VTS connection is not available")

    selector = str(configured_hotkey or "").strip()
    if not selector:
        raise RuntimeError("configured hotkey selector is empty")

    async def _load() -> list[dict[str, Any]]:
        response = await asyncio.wait_for(
            vts.request(vts.vts_request.requestHotKeyList()),
            timeout=timeout_s,
        )
        data = dict(response.get("data") or {})
        return list(data.get("availableHotkeys") or [])

    hotkeys = _run_in_vts_loop(_load(), timeout_s=timeout_s + 1.0)
    for item in hotkeys:
        hotkey_id = str(item.get("hotkeyID") or "").strip()
        name = str(item.get("name") or "").strip()
        file_name = str(item.get("file") or "").strip()
        if selector == hotkey_id:
            return hotkey_id, name or selector
        if selector and selector == name:
            return hotkey_id, name
        if selector and selector == file_name:
            return hotkey_id, name or file_name

    raise RuntimeError(f"configured hotkey selector not found in current model: {selector}")


def _resolve_mouth_parameter_plan(
    *,
    config: VtsConfig,
    available_parameters: set[str],
) -> dict[str, Any]:
    available = {str(item).strip() for item in available_parameters if str(item).strip()}
    if not available:
        return _fallback_mouth_parameter_plan(config)
    mouth_params = tuple(
        candidate
        for candidate in config.mouth_param_candidates
        if candidate in available
    )
    volume_params = tuple(
        candidate
        for candidate in config.mouth_volume_param_candidates
        if candidate in available
    )
    silence_params = tuple(
        candidate
        for candidate in config.voice_silence_param_candidates
        if candidate in available
    )
    selected = tuple((*mouth_params, *volume_params, *silence_params))
    if not selected:
        return _fallback_mouth_parameter_plan(config)
    return {
        "mode": "detected",
        "selected": list(selected),
        "mouth": list(mouth_params),
        "volume": list(volume_params),
        "silence": list(silence_params),
    }


def _fallback_mouth_parameter_plan(config: VtsConfig) -> dict[str, Any]:
    mouth = [config.mouth_param_candidates[0]] if config.mouth_param_candidates else []
    volume = [config.mouth_volume_param_candidates[0]] if config.mouth_volume_param_candidates else []
    silence = [config.voice_silence_param_candidates[0]] if config.voice_silence_param_candidates else []
    return {
        "mode": "fallback",
        "selected": [*mouth, *volume, *silence],
        "mouth": mouth,
        "volume": volume,
        "silence": silence,
    }


def _build_mouth_parameter_payload(plan: dict[str, Any], normalized: float) -> dict[str, float]:
    payload: dict[str, float] = {}
    for param_name in list(plan.get("mouth") or []):
        payload[str(param_name)] = normalized
    for param_name in list(plan.get("volume") or []):
        payload[str(param_name)] = normalized
    for param_name in list(plan.get("silence") or []):
        # Keep manual lipsync in control even on models that wire
        # mouth movement through advanced voice-silence gating.
        payload[str(param_name)] = 1.0
    if payload:
        return payload
    return {
        VTS_MOUTH_PARAM: normalized,
        VTS_MOUTH_PLUS_VOLUME_PARAM: normalized,
        VTS_VOICE_SILENCE_PARAM: 1.0,
    }


def _is_unsafe_global_reset_hotkey(selector: str) -> bool:
    normalized = str(selector or "").strip().lower()
    if not normalized:
        return False
    if normalized in {item.lower() for item in UNSAFE_GLOBAL_RESET_HOTKEY_SELECTORS}:
        return True
    return "reset_all" in normalized


def _run_in_vts_loop(coro: Any, *, timeout_s: float) -> Any:
    """Run coroutine on dedicated VTS IO loop to avoid ASGI loop conflicts."""
    loop = _ensure_vts_io_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=max(float(timeout_s), 1.0))


def _ensure_vts_io_loop() -> asyncio.AbstractEventLoop:
    global _VTS_IO_LOOP, _VTS_IO_THREAD
    with _VTS_IO_LOCK:
        if _VTS_IO_LOOP is not None and not _VTS_IO_LOOP.is_closed():
            return _VTS_IO_LOOP

        loop = asyncio.new_event_loop()

        def _runner() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(
            target=_runner,
            name="vts-io-loop",
            daemon=True,
        )
        thread.start()
        _VTS_IO_LOOP = loop
        _VTS_IO_THREAD = thread
        return loop


def _extract_vts_api_error(response: Any, *, default: str) -> str:
    if not isinstance(response, dict):
        return str(default)
    data = dict(response.get("data") or {})
    message = str(data.get("message") or "").strip()
    error_id = data.get("errorID")
    if message and error_id is not None:
        return f"VTS API error {error_id}: {message}"
    if message:
        return f"VTS API error: {message}"
    return str(default)

