"""VTube Studio driver: WebSocket API connection, talk gate, and expression mapping.

Provides deterministic VTS integration with talk gate (TALK_START/TALK_END)
and emotion_tag ?expression_id mapping. Falls back gracefully when VTS
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

# Keep this map aligned with host_brain/channels/vts.py.
EMOTION_TO_EXPRESSION_ID: dict[str, str] = {
    "NEUTRAL": "exp_neutral_01",
    "HAPPY": "exp_happy_01",
    "SAD": "exp_sad_01",
    "ANGRY": "exp_angry_01",
    "SURPRISED": "exp_surprised_01",
    "THINKING": "exp_thinking_01",
    "EXCITED": "exp_excited_01",
    "CALM": "exp_calm_01",
    "FRUSTRATED": "exp_frustrated_01",
    "ANXIOUS": "exp_anxious_01",
    "LOVING": "exp_loving_01",
    "EMBARRASSED": "exp_embarrassed_01",
    "CONFUSED": "exp_confused_01",
    "BORED": "exp_bored_01",
    "PROUD": "exp_proud_01",
    "GRATEFUL": "exp_grateful_01",
}

# VTS mouth/lip related input parameters.
VTS_MOUTH_PARAM = "MouthOpen"
DEFAULT_MOUTH_PARAM_CANDIDATES: tuple[str, ...] = ("MouthOpen", "ParamMouthOpenY")
DEFAULT_MOUTH_VOLUME_PARAM_CANDIDATES: tuple[str, ...] = (
    "VoiceVolumePlusMouthOpen",
    "VoiceVolume",
)
DEFAULT_VOICE_SILENCE_PARAM_CANDIDATES: tuple[str, ...] = ("VoiceSilence",)
VTS_MOUTH_OPEN_VALUE = 1.0
VTS_MOUTH_CLOSE_VALUE = 0.0
EMOTION_HOTKEY_HINTS: dict[str, tuple[str, ...]] = {
    "NEUTRAL": ("neutral", "normal", "idle", "移除所有按键表情", "无语眼", "黑眼", "白眼"),
    "HAPPY": ("happy", "smile", "joy", "grin", "hk_happy", "开心", "高兴", "笑", "脸红"),
    "SAD": ("sad", "cry", "tear", "哭哭", "难过"),
    "ANGRY": ("angry", "mad", "rage", "hk_angry", "生气", "愤怒", "脸黑"),
    "SURPRISED": ("surpris", "wow", "shock", "圈圈眼", "惊讶"),
    "THINKING": ("think", "ponder", "无语眼", "思考"),
    "EXCITED": ("excit", "energetic", "hk_happy", "星星眼"),
    "CALM": ("calm", "relax", "蓝瞳孔"),
    "FRUSTRATED": ("frustrat", "annoy", "despise", "hk_despise", "鄙视"),
    "ANXIOUS": ("anx", "nervous", "worry", "眩晕"),
    "LOVING": ("love", "heart", "hk_love", "爱心"),
    "EMBARRASSED": ("embarrass", "shy", "hk_shy", "害羞"),
    "CONFUSED": ("confus", "question", "无语眼", "圈圈眼"),
    "BORED": ("bore", "tired", "dead", "hk_dead"),
    "PROUD": ("proud", "剪刀眼", "比耶"),
    "GRATEFUL": ("grateful", "thanks", "四叶草"),
}
EXPRESSION_HOTKEY_BLOCKED_TOKENS: tuple[str, ...] = (
    "watermark",
    "logo",
    "remove watermark",
    "remove_watermark",
    "去水印",
    "水印",
    "fly",
    "飞头",
    "color",
    "改色",
    "headphone",
    "耳机",
    "glasses",
    "眼镜",
    "hair",
    "发型",
    "长发",
    "短发",
    "项链",
    "兔兔",
    "鞋",
    "摇杆",
    "屏幕",
    "挑染",
    "麦克风",
    "phone",
    "game",
    "chibi",
    "移除所有按键表情",
    "remove all",
    "reset all",
    "all hotkey",
    "all expression",
)


#   
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
    """VTS WebSocket auto-reconnect configuration."""
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
    mouth_param_candidates: tuple[str, ...] = field(default_factory=lambda: DEFAULT_MOUTH_PARAM_CANDIDATES)
    mouth_volume_param_candidates: tuple[str, ...] = field(
        default_factory=lambda: DEFAULT_MOUTH_VOLUME_PARAM_CANDIDATES
    )
    voice_silence_param_candidates: tuple[str, ...] = field(
        default_factory=lambda: DEFAULT_VOICE_SILENCE_PARAM_CANDIDATES
    )
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
    - Emotion tag ?expression_id deterministic mapping
    - Barge-in recovery (stop talking ?reset to NEUTRAL)
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
        #  ?
        self._reconnect_attempts = 0
        self._total_reconnects = 0
        self._total_reconnect_failures = 0
        self._reconnect_thread: threading.Thread | None = None
        self._reconnect_events: list[dict[str, Any]] = []
        self._shutdown_event = threading.Event()
        self._expression_hotkey_overrides: dict[str, str] = {}
        self._hotkey_catalog: list[dict[str, str]] = []
        initial_mouth = [str(item).strip() for item in self._config.mouth_param_candidates if str(item).strip()]
        if not initial_mouth:
            initial_mouth = list(DEFAULT_MOUTH_PARAM_CANDIDATES)
        initial_volume = [
            str(item).strip()
            for item in self._config.mouth_volume_param_candidates
            if str(item).strip()
        ] or list(DEFAULT_MOUTH_VOLUME_PARAM_CANDIDATES)
        initial_silence = [
            str(item).strip()
            for item in self._config.voice_silence_param_candidates
            if str(item).strip()
        ] or list(DEFAULT_VOICE_SILENCE_PARAM_CANDIDATES)
        self._mouth_parameter_plan: dict[str, Any] = {
            "selected": list(initial_mouth[:1]),
            "mouth": list(initial_mouth),
            "volume": list(initial_volume),
            "silence": list(initial_silence),
            "available": [],
            "last_value": 0.0,
        }

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
        self._shutdown_event.clear()
        result = self.connect()
        if not result.success:
            logger.warning("VTS startup degraded: %s", result.detail)

    def shutdown(self) -> None:
        self.disconnect()

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

                # 
                _authenticate_vts(
                    vts=vts_instance,
                    plugin_name=self._config.plugin_name,
                    plugin_developer=self._config.plugin_developer,
                    token_path=self._config.token_path,
                )
                self._state = VtsState.AUTHENTICATED
                self._refresh_mouth_parameter_plan()
                self._hotkey_catalog = _list_vts_hotkeys(
                    vts=self._vts,
                    timeout_s=self._config.api_timeout_s,
                )
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

        mouth_result = self.set_mouth_open_level(VTS_MOUTH_OPEN_VALUE)
        if not mouth_result.success:
            return VtsDriverResult(
                success=False,
                reason_code=mouth_result.reason_code,
                detail=f"talk start error: {mouth_result.detail}",
                action=TALK_START,
            )
        self._talking = True
        logger.debug("VTS talk gate: START")
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="talk gate started (mouth open)",
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

        mouth_result = self.set_mouth_open_level(VTS_MOUTH_CLOSE_VALUE)
        if not mouth_result.success:
            return VtsDriverResult(
                success=False,
                reason_code=mouth_result.reason_code,
                detail=f"talk end error: {mouth_result.detail}",
                action=TALK_END,
            )
        logger.debug("VTS talk gate: END")
        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail="talk gate ended (mouth closed)",
            action=TALK_END,
        )

    def set_mouth_open_level(self, value: float) -> VtsDriverResult:
        """Drive mouth-open related VTS parameters with runtime fallback."""
        try:
            level = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            level = 0.0

        if self._state != VtsState.AUTHENTICATED:
            conn_result = self.connect()
            if not conn_result.success:
                return VtsDriverResult(
                    success=False,
                    reason_code=VTS_CONNECTION_FAILED,
                    detail=f"cannot set mouth level: {conn_result.detail}",
                    action="set_mouth_open_level",
                )

        mouth_params = self._preferred_mouth_parameters()
        plan = dict(self._mouth_parameter_plan or {})
        available_params = {
            str(item or "").strip()
            for item in (plan.get("available") or [])
            if str(item or "").strip()
        }
        if available_params:
            available_mouth_params = [item for item in mouth_params if item in available_params]
            if available_mouth_params:
                mouth_params = available_mouth_params
        errors: list[str] = []
        selected_primary: str | None = None

        for param in mouth_params:
            try:
                _set_vts_parameter(
                    vts=self._vts,
                    param_name=param,
                    value=level,
                    timeout_s=self._config.api_timeout_s,
                )
                selected_primary = param
                break
            except Exception as exc:  # pragma: no cover - depends on VTS runtime state
                errors.append(f"{param}: {exc}")

        if selected_primary is None:
            detail = "all mouth parameters failed"
            if errors:
                detail = f"{detail}: {'; '.join(errors)}"
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=detail,
                action="set_mouth_open_level",
            )

        # Optional companion parameters: failures here should not fail playback.
        volume_params = [
            str(item)
            for item in (plan.get("volume") or self._candidate_tuple(self._config.mouth_volume_param_candidates))
            if str(item).strip()
        ]
        silence_params = [
            str(item)
            for item in (plan.get("silence") or self._candidate_tuple(self._config.voice_silence_param_candidates))
            if str(item).strip()
        ]
        if available_params:
            filtered_volume = [item for item in volume_params if item in available_params]
            filtered_silence = [item for item in silence_params if item in available_params]
            if filtered_volume:
                volume_params = filtered_volume
            if filtered_silence:
                silence_params = filtered_silence

        volume_params = [item for item in volume_params if item != selected_primary]
        silence_params = [item for item in silence_params if item != selected_primary]

        for volume_param in volume_params:
            try:
                _set_vts_parameter(
                    vts=self._vts,
                    param_name=volume_param,
                    value=level,
                    timeout_s=self._config.api_timeout_s,
                )
            except Exception:
                continue
        for silence_param in silence_params:
            try:
                _set_vts_parameter(
                    vts=self._vts,
                    param_name=silence_param,
                    value=max(0.0, min(1.0, 1.0 - level)),
                    timeout_s=self._config.api_timeout_s,
                )
            except Exception:
                continue

        plan = dict(self._mouth_parameter_plan)
        plan["selected"] = [selected_primary]
        plan["last_value"] = round(level, 4)
        self._mouth_parameter_plan = plan

        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail=f"mouth level set via {selected_primary}: {level:.3f}",
            action="set_mouth_open_level",
        )

    def set_expression(self, emotion_tag: str, *, intensity: float = 0.5) -> VtsDriverResult:
        """Set expression via deterministic emotion_tag -> expression_id mapping.

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
            # Degrade gracefully when VTS is unavailable.
            self._current_expression = expression_id
            return VtsDriverResult(
                success=True,
                reason_code=VTS_CONNECTION_FAILED,
                detail=f"expression queued (VTS not connected): {expression_id}",
                action="set_expression",
                expression_id=expression_id,
                emotion_tag=normalized,
            )

        override_hotkey = str(self._expression_hotkey_overrides.get(normalized) or "").strip()
        if override_hotkey and self._is_disallowed_expression_hotkey(override_hotkey):
            self._expression_hotkey_overrides.pop(normalized, None)
            override_hotkey = ""
        hotkey_id = override_hotkey or expression_id
        try:
            _trigger_vts_hotkey(
                vts=self._vts,
                hotkey_id=hotkey_id,
                timeout_s=self._config.api_timeout_s,
            )
            self._current_expression = hotkey_id
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail=f"expression set: {normalized} -> {hotkey_id}",
                action="set_expression",
                expression_id=hotkey_id,
                emotion_tag=normalized,
            )
        except TimeoutError as e:
            return VtsDriverResult(
                success=False,
                reason_code=VTS_EXPRESSION_TIMEOUT,
                detail=f"expression timeout: {e}",
                action="set_expression",
                expression_id=hotkey_id,
                emotion_tag=normalized,
            )
        except Exception as e:
            fallback_hotkey = self._resolve_hotkey_for_emotion(
                emotion_tag=normalized,
                preferred_hotkey=expression_id,
            )
            if fallback_hotkey and fallback_hotkey != hotkey_id:
                try:
                    _trigger_vts_hotkey(
                        vts=self._vts,
                        hotkey_id=fallback_hotkey,
                        timeout_s=self._config.api_timeout_s,
                    )
                    self._expression_hotkey_overrides[normalized] = fallback_hotkey
                    self._current_expression = fallback_hotkey
                    return VtsDriverResult(
                        success=True,
                        reason_code=None,
                        detail=(
                            f"expression set via fallback: {normalized} -> {fallback_hotkey} "
                            f"(preferred={expression_id})"
                        ),
                        action="set_expression",
                        expression_id=fallback_hotkey,
                        emotion_tag=normalized,
                    )
                except Exception:
                    pass
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"expression error: {e}",
                action="set_expression",
                expression_id=hotkey_id,
                emotion_tag=normalized,
            )

    def reset_to_neutral(self) -> VtsDriverResult:
        """Reset expression to NEUTRAL (used after barge-in or end of talk)."""
        self._talking = False
        result = self.set_expression("NEUTRAL")

        # 
        if self._state == VtsState.AUTHENTICATED:
            try:
                self.set_mouth_open_level(VTS_MOUTH_CLOSE_VALUE)
            except Exception:
                pass

        return result

    def trigger_hotkey(self, hotkey_id: str) -> VtsDriverResult:
        """ VTS 26-Hub_emotion ?
         sub_emotion ?        VTS ?
        Args:
            hotkey_id: VTS  ID?configs/vts/vts_hotkey_config.yaml  hotkey_id?
        Returns:
            VtsDriverResultuccess=True ?        """
        if not hotkey_id or not str(hotkey_id).strip():
            return VtsDriverResult(
                success=False,
                reason_code=AVATAR_TAG_INVALID,
                detail="hotkey_id is required",
                action="trigger_hotkey",
            )

        normalized_id = str(hotkey_id).strip()

        if self._state != VtsState.AUTHENTICATED:
            logger.debug("VTS trigger_hotkey degraded (not connected): %s", normalized_id)
            return VtsDriverResult(
                success=True,
                reason_code=VTS_CONNECTION_FAILED,
                detail=f"hotkey queued (VTS not connected): {normalized_id}",
                action="trigger_hotkey",
            )

        try:
            _trigger_vts_hotkey(
                vts=self._vts,
                hotkey_id=normalized_id,
                timeout_s=self._config.api_timeout_s,
            )
            logger.debug("VTS trigger_hotkey: %s", normalized_id)
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail=f"hotkey triggered: {normalized_id}",
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

    @staticmethod
    def _candidate_tuple(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        out: list[str] = []
        for raw in values or ():
            text = str(raw or "").strip()
            if text:
                out.append(text)
        return tuple(out)

    @staticmethod
    def _pick_parameter_candidates(
        available: list[str],
        *,
        preferred_exact: tuple[str, ...],
        token_groups: tuple[tuple[str, ...], ...],
        fallback_tokens: tuple[str, ...] = (),
        limit: int = 3,
    ) -> list[str]:
        ordered: list[str] = []
        available_set = {str(item or "").strip() for item in available if str(item or "").strip()}
        for exact in preferred_exact:
            text = str(exact or "").strip()
            if text and text in available_set:
                ordered.append(text)
        for item in available:
            text = str(item or "").strip()
            if text:
                ordered.append(text)

        selected: list[str] = []
        seen: set[str] = set()
        for text in ordered:
            if text in seen:
                continue
            lowered = text.lower()
            matched = False
            for tokens in token_groups:
                if not tokens:
                    continue
                if all(token in lowered for token in tokens):
                    matched = True
                    break
            if matched:
                seen.add(text)
                selected.append(text)
            if len(selected) >= max(1, int(limit)):
                break

        if selected:
            return selected
        normalized_fallback_tokens = tuple(
            token.strip().lower()
            for token in fallback_tokens
            if str(token).strip()
        )
        if not normalized_fallback_tokens:
            return selected
        for text in ordered:
            if text in seen:
                continue
            lowered = text.lower()
            if any(token in lowered for token in normalized_fallback_tokens):
                seen.add(text)
                selected.append(text)
            if len(selected) >= max(1, int(limit)):
                break
        return selected

    def _refresh_mouth_parameter_plan(self) -> None:
        available = _list_vts_input_parameter_ids(
            vts=self._vts,
            timeout_s=self._config.api_timeout_s,
        )

        mouth_candidates = self._candidate_tuple(self._config.mouth_param_candidates)
        volume_candidates = self._candidate_tuple(self._config.mouth_volume_param_candidates)
        silence_candidates = self._candidate_tuple(self._config.voice_silence_param_candidates)

        if available:
            available_set = set(available)
            selected_mouth = [item for item in mouth_candidates if item in available_set]
            selected_volume = [item for item in volume_candidates if item in available_set]
            selected_silence = [item for item in silence_candidates if item in available_set]

            if not selected_mouth:
                selected_mouth = self._pick_parameter_candidates(
                    available,
                    preferred_exact=mouth_candidates,
                    token_groups=(
                        ("mouthopen",),
                        ("mouth", "open"),
                        ("param", "mouth", "open"),
                        ("jaw", "open"),
                    ),
                    fallback_tokens=("mouthopen", "jawopen"),
                    limit=3,
                )
            if not selected_volume:
                selected_volume = self._pick_parameter_candidates(
                    available,
                    preferred_exact=volume_candidates,
                    token_groups=(
                        ("voicevolumeplusmouthopen",),
                        ("voice", "volume"),
                        ("volume",),
                    ),
                    fallback_tokens=("volume", "voice"),
                    limit=2,
                )
            if not selected_silence:
                selected_silence = self._pick_parameter_candidates(
                    available,
                    preferred_exact=silence_candidates,
                    token_groups=(
                        ("voicesilence",),
                        ("voice", "silence"),
                        ("silence",),
                    ),
                    fallback_tokens=("silence", "voice"),
                    limit=2,
                )
        else:
            selected_mouth = list(mouth_candidates)
            selected_volume = list(volume_candidates)
            selected_silence = list(silence_candidates)

        if not selected_mouth:
            if selected_volume:
                selected_mouth = [selected_volume[0]]
            else:
                selected_mouth = [VTS_MOUTH_PARAM]

        self._mouth_parameter_plan = {
            "selected": list(selected_mouth),
            "mouth": list(selected_mouth),
            "volume": list(selected_volume),
            "silence": list(selected_silence),
            "available": list(available),
            "last_value": float(self._mouth_parameter_plan.get("last_value") or 0.0),
        }

    def _preferred_mouth_parameters(self) -> list[str]:
        plan = dict(self._mouth_parameter_plan or {})
        selected = [str(item) for item in (plan.get("selected") or []) if str(item).strip()]
        plan_mouth = [str(item) for item in (plan.get("mouth") or []) if str(item).strip()]
        cfg_candidates = [str(item) for item in self._candidate_tuple(self._config.mouth_param_candidates)]
        plan_volume = [str(item) for item in (plan.get("volume") or []) if str(item).strip()]
        cfg_volume = [str(item) for item in self._candidate_tuple(self._config.mouth_volume_param_candidates)]
        ordered = selected + plan_mouth + cfg_candidates + plan_volume + cfg_volume + [VTS_MOUTH_PARAM]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in ordered:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped or [VTS_MOUTH_PARAM]

    def _is_disallowed_expression_hotkey(self, hotkey_id: str, *, name: str = "") -> bool:
        lowered = f"{hotkey_id} {name}".lower()
        return any(token in lowered for token in EXPRESSION_HOTKEY_BLOCKED_TOKENS)

    def _resolve_hotkey_for_emotion(
        self,
        *,
        emotion_tag: str,
        preferred_hotkey: str,
    ) -> str | None:
        catalog = list(self._hotkey_catalog or [])
        if not catalog:
            catalog = _list_vts_hotkeys(vts=self._vts, timeout_s=self._config.api_timeout_s)
            self._hotkey_catalog = list(catalog)
        if not catalog:
            return None

        preferred_norm = str(preferred_hotkey or "").strip().lower()
        if preferred_norm:
            for row in catalog:
                hotkey_id = str(row.get("id") or "").strip()
                name = str(row.get("name") or "").strip()
                if hotkey_id.lower() == preferred_norm and not self._is_disallowed_expression_hotkey(
                    hotkey_id,
                    name=name,
                ):
                    return hotkey_id

        hints = EMOTION_HOTKEY_HINTS.get(str(emotion_tag or "").strip().upper(), ())
        ordered_hints = tuple(hints)
        for hint in ordered_hints:
            hint_norm = str(hint or "").strip().lower()
            if not hint_norm:
                continue
            for row in catalog:
                hotkey_id = str(row.get("id") or "").strip()
                name = str(row.get("name") or "").strip()
                if self._is_disallowed_expression_hotkey(hotkey_id, name=name):
                    continue
                if hint_norm in hotkey_id.lower() or hint_norm in name.lower():
                    return hotkey_id
        return None

    # -----------------------------------------------------------------
    # WebSocket ASK-0080?    # -----------------------------------------------------------------

    def _reconnect_loop(self) -> None:
        """?WebSocket ?
         neutral ?        VTS_RECONNECTED  DISCONNECTED ?        VTS_RECONNECT_EXHAUSTED?        """
        rc = self._config.reconnect
        delay = rc.base_delay_s
        self._reconnect_attempts = 0

        # tate  trigger_reconnect ?lock  RECONNECTING
        # ?
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
                    # ?neutral
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
                    # connect ?state  RECONNECTING
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

            # Wait with backoff; allow shutdown to cancel reconnect loop.
            if self._shutdown_event.wait(timeout=min(delay, rc.max_delay_s)):
                logger.info("VTS reconnect cancelled by shutdown during wait")
                return
            delay = min(delay * 2, rc.max_delay_s)

        # 
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
        """ WebSocket ?
        ?API ?         no-op?        """
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
            # Set state before thread starts to avoid race windows.
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
        """Trigger reconnect when API calls indicate lost connection."""
        rc = self._config.reconnect
        if not rc.auto_reconnect:
            return
        if self._state in (VtsState.RECONNECTING, VtsState.SHUTDOWN):
            return
        logger.warning("VTS connection lost (%s), triggering reconnect", reason)
        self.trigger_reconnect()

    @property
    def reconnect_events(self) -> list[dict[str, Any]]:
        """Return reconnect audit events."""
        return list(self._reconnect_events)

    def health(self) -> dict[str, Any]:
        """Return VTS driver health snapshot."""
        return {
            "state": self._state.value,
            "talking": self._talking,
            "current_expression": self._current_expression,
            "mouth_parameters": {
                "selected": list(self._mouth_parameter_plan.get("selected") or []),
                "mouth": list(self._mouth_parameter_plan.get("mouth") or []),
                "volume": list(self._mouth_parameter_plan.get("volume") or []),
                "silence": list(self._mouth_parameter_plan.get("silence") or []),
                "available": list(self._mouth_parameter_plan.get("available") or []),
                "last_value": float(self._mouth_parameter_plan.get("last_value") or 0.0),
            },
            "config": self._config.as_dict(),
            "connection_error": self._connection_error,
            "emotion_map_size": len(EMOTION_TO_EXPRESSION_ID),
            "hotkey_catalog_size": len(self._hotkey_catalog),
            "expression_overrides": dict(self._expression_hotkey_overrides),
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
        response = await asyncio.wait_for(
            vts.request(request),
            timeout=timeout_s,
        )
        error = _extract_vts_api_error(response, default="")
        if error:
            raise RuntimeError(error)

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
        response = await asyncio.wait_for(
            vts.request(request),
            timeout=timeout_s,
        )
        error = _extract_vts_api_error(response, default="")
        if error:
            raise RuntimeError(error)

    _run_in_vts_loop(_trigger(), timeout_s=timeout_s + 1.0)


def _list_vts_input_parameter_ids(*, vts: Any, timeout_s: float) -> list[str]:
    """Fetch runtime-available VTS input parameter IDs."""
    if vts is None:
        return []

    async def _list() -> list[str]:
        request = vts.vts_request.BaseRequest("InputParameterListRequest", {})
        response = await asyncio.wait_for(vts.request(request), timeout=timeout_s)
        error = _extract_vts_api_error(response, default="")
        if error:
            raise RuntimeError(error)
        data = dict(response.get("data") or {})
        ids: list[str] = []
        for key in ("modelParameters", "defaultParameters", "parameters"):
            rows = data.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                pid = str(row.get("id") or "").strip()
                if pid:
                    ids.append(pid)
        seen: set[str] = set()
        unique: list[str] = []
        for item in ids:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique

    try:
        return list(_run_in_vts_loop(_list(), timeout_s=timeout_s + 1.0) or [])
    except Exception:
        return []


def _list_vts_hotkeys(*, vts: Any, timeout_s: float) -> list[dict[str, str]]:
    """Fetch hotkeys in current VTS model."""
    if vts is None:
        return []

    async def _list() -> list[dict[str, str]]:
        request = vts.vts_request.BaseRequest("HotkeysInCurrentModelRequest", {})
        response = await asyncio.wait_for(vts.request(request), timeout=timeout_s)
        error = _extract_vts_api_error(response, default="")
        if error:
            raise RuntimeError(error)
        data = dict(response.get("data") or {})
        rows = data.get("availableHotkeys")
        if not isinstance(rows, list):
            return []
        out: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            hotkey_id = str(row.get("hotkeyID") or row.get("id") or "").strip()
            if not hotkey_id:
                continue
            out.append(
                {
                    "id": hotkey_id,
                    "name": str(row.get("name") or row.get("hotkeyName") or "").strip(),
                }
            )
        return out

    try:
        return list(_run_in_vts_loop(_list(), timeout_s=timeout_s + 1.0) or [])
    except Exception:
        return []


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
    message = str(data.get("message") or response.get("message") or "").strip()
    error_id = data.get("errorID")
    if error_id is None:
        error_id = response.get("errorID")
    try:
        error_id_int = int(error_id) if error_id is not None else 0
    except (TypeError, ValueError):
        error_id_int = 0

    # VTS uses errorID=0 for success.
    if error_id_int > 0:
        if message:
            return f"VTS API error {error_id_int}: {message}"
        return f"VTS API error {error_id_int}"
    if str(response.get("messageType") or "").strip().lower() == "apierror":
        return f"VTS API error: {message or default}"
    return str(default)

