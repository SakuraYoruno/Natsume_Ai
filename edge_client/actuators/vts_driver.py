"""VTube Studio driver: WebSocket API connection, talk gate, and expression mapping.

Provides deterministic VTS integration with talk gate (TALK_START/TALK_END)
and emotion_tag ?expression_id mapping. Falls back gracefully when VTS
is disconnected or API calls fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from natsume_contracts.edge.v1 import (
    AVATAR_FREE_ACTION_BLOCKED,
    AVATAR_TAG_INVALID,
    VTS_API_ERROR,
    VTS_CONNECTION_FAILED,
    VTS_EXPRESSION_TIMEOUT,
    VTS_RECONNECTED,
    VTS_RECONNECT_EXHAUSTED,
    now_ms,
)

logger = logging.getLogger("Natsume.edge.vts")

_VTS_IO_LOOP: asyncio.AbstractEventLoop | None = None
_VTS_IO_THREAD: threading.Thread | None = None
_VTS_IO_LOCK = threading.Lock()
_VTS_REQUEST_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_VTS_HOST = "localhost"
DEFAULT_VTS_PORT = 8001
DEFAULT_VTS_PLUGIN_NAME = "NatsumeAi"
DEFAULT_VTS_PLUGIN_DEVELOPER = "Natsume"
DEFAULT_VTS_TOKEN_PATH = ".vts_token"
DEFAULT_API_TIMEOUT_S = 5.0
VTS_PLUGIN_NAME_ALIASES: dict[str, str] = {
    "NastumeAi": DEFAULT_VTS_PLUGIN_NAME,
}

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
EMOTION_TAG_ALIASES: dict[str, str] = {
    "HAPPINESS": "HAPPY",
    "JOY": "HAPPY",
    "DELIGHT": "HAPPY",
    "SADNESS": "SAD",
    "GRIEF": "SAD",
    "COMFORT": "CALM",
    "SOOTHING": "CALM",
    "EXCITEMENT": "EXCITED",
    "PLAYFUL": "HAPPY",
    "SERIOUS": "THINKING",
    "COQUETTISH": "LOVING",
    "AFFECTION": "LOVING",
    "LOVE": "LOVING",
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
NEUTRAL_RESET_HOTKEY_ACTIONS: tuple[str, ...] = (
    "removeallexpressions",
    "remove_all_expressions",
)
NEUTRAL_RESET_HOTKEY_HINTS: tuple[str, ...] = (
    "移除所有按键表情",
    "remove all expressions",
    "remove all",
    "reset all",
)
BLOCKED_AVATAR_HOTKEY_TOKENS: tuple[str, ...] = (
    "hotkey_remove_watermark",
    "remove_watermark",
    "remove watermark",
    "watermark",
    "去水印",
    "水印",
    "hotkey_reset_all",
    "reset_all",
    "reset all",
    "remove all expressions",
    "移除所有按键表情",
)
BROKEN_HOTKEY_ERROR_HINTS: tuple[str, ...] = (
    "given parameter",
    "does not exist",
    "not found in model",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _vts_reset_to_neutral_enabled() -> bool:
    return _env_bool("EDGE_VTS_RESET_TO_NEUTRAL_ENABLED", False)


def is_blocked_avatar_hotkey_id(hotkey_id: Any, *, name: Any = "", action: Any = "") -> bool:
    if not _env_bool("EDGE_VTS_BLOCK_WATERMARK_HOTKEY_ENABLED", True):
        return False
    text = " ".join(
        str(part or "").strip().lower()
        for part in (hotkey_id, name, action)
        if str(part or "").strip()
    )
    return bool(text and any(token in text for token in BLOCKED_AVATAR_HOTKEY_TOKENS))


#
DEFAULT_MAX_RECONNECT_ATTEMPTS = 10
DEFAULT_RECONNECT_BASE_DELAY_S = 1.0
DEFAULT_RECONNECT_MAX_DELAY_S = 16.0
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


def _dedupe_text_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def normalize_vts_token_path(token_path: str | None) -> str:
    normalized = str(token_path or "").strip() or DEFAULT_VTS_TOKEN_PATH
    candidate = Path(normalized)
    if candidate.is_absolute():
        return str(candidate)
    return str((_DEFAULT_REPO_ROOT / candidate).resolve())


def _find_vts_streaming_assets_root() -> Path | None:
    candidates: list[Path] = []

    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = str(proc.info.get("name") or "").strip()
                exe = str(proc.info.get("exe") or "").strip()
            except Exception:
                continue
            if "VTube Studio" not in name and not exe.lower().endswith("vtube studio.exe"):
                continue
            if exe:
                exe_path = Path(exe)
                candidates.append(exe_path.parent / "VTube Studio_Data" / "StreamingAssets")
    except Exception:
        pass

    local_appdata = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        candidates.append(
            Path(local_appdata) / "Programs" / "VTube Studio" / "VTube Studio_Data" / "StreamingAssets"
        )
    candidates.append(
        Path.home() / "Documents" / "VTube Studio" / "VTube Studio_Data" / "StreamingAssets"
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _resolve_current_model_vtube_path() -> Path | None:
    root = _find_vts_streaming_assets_root()
    if root is None:
        return None

    history_path = root / "Config" / "model_history.json"
    history_data = _load_json_file(history_path)
    if history_data:
        rows = list(history_data.get("HistoryMainModels") or [])
        rows.sort(key=lambda row: int(row.get("LastLoaded") or 0), reverse=True)
        for row in rows:
            file_path = str(row.get("ModelFilePath") or "").strip()
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    return candidate
            base_path = str(row.get("ModelBasePath") or "").strip()
            file_name = str(row.get("ModelFileNameWithExtention") or "").strip()
            if base_path and file_name:
                candidate = Path(base_path) / file_name
                if candidate.exists():
                    return candidate

    config_path = root / "Config" / "vts_config.json"
    config_data = _load_json_file(config_path)
    if config_data:
        for row in config_data.get("StringData") or []:
            if str(row.get("Key") or "").strip() != "Config_LastModelPath":
                continue
            candidate = Path(str(row.get("Value") or "").strip().replace("\\", "/"))
            if candidate.exists():
                return candidate
    return None


def _discover_current_model_mouth_bindings() -> dict[str, Any]:
    vtube_path = _resolve_current_model_vtube_path()
    result: dict[str, Any] = {
        "vtube_path": str(vtube_path) if vtube_path else None,
        "model_base_path": str(vtube_path.parent) if vtube_path else None,
        "open_inputs": [],
        "form_inputs": [],
        "smile_inputs": [],
        "last_error": None,
    }
    if vtube_path is None or not vtube_path.exists():
        return result

    data = _load_json_file(vtube_path)
    if not data:
        result["last_error"] = "unable to parse vtube json"
        return result

    open_inputs: list[str] = []
    form_inputs: list[str] = []
    smile_inputs: list[str] = []
    for row in data.get("ParameterSettings") or []:
        input_name = str(row.get("Input") or "").strip()
        output_name = str(row.get("OutputLive2D") or "").strip()
        display_name = str(row.get("Name") or "").strip().lower()
        if not input_name or not output_name:
            continue
        output_lower = output_name.lower()
        if output_lower.startswith("parammouthopen") or "mouth open" in display_name:
            open_inputs.append(input_name)
            continue
        if output_lower.startswith("parammouthform2") or "mouth smile" in display_name:
            smile_inputs.append(input_name)
            continue
        if output_lower.startswith("parammouthform") or "嘴" in str(row.get("Name") or ""):
            form_inputs.append(input_name)

    result["open_inputs"] = _dedupe_text_list(open_inputs)
    result["form_inputs"] = _dedupe_text_list(form_inputs)
    result["smile_inputs"] = _dedupe_text_list(smile_inputs)
    return result


def normalize_vts_plugin_name(plugin_name: str | None) -> str:
    normalized = str(plugin_name or "").strip() or DEFAULT_VTS_PLUGIN_NAME
    alias = VTS_PLUGIN_NAME_ALIASES.get(normalized)
    if alias is not None:
        logger.warning(
            "VTS plugin_name normalized from %s to %s",
            normalized,
            alias,
        )
        return alias
    return normalized


def normalize_expression_emotion_tag(emotion_tag: str | None) -> str | None:
    normalized = str(emotion_tag or "").strip().upper()
    if not normalized:
        return None
    normalized = normalized.replace("-", "_").replace(" ", "_")
    return EMOTION_TAG_ALIASES.get(normalized, normalized)


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

    def __post_init__(self) -> None:
        self.plugin_name = normalize_vts_plugin_name(self.plugin_name)
        self.token_path = normalize_vts_token_path(self.token_path)

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


def _blocked_hotkey_result(hotkey_id: Any, *, action: str = "trigger_hotkey") -> VtsDriverResult:
    normalized = str(hotkey_id or "").strip()
    return VtsDriverResult(
        success=False,
        reason_code=AVATAR_FREE_ACTION_BLOCKED,
        detail=f"blocked avatar hotkey: {normalized}",
        action=action,
        expression_id=normalized or None,
        emotion_tag=None,
    )


def _reset_to_neutral_skipped_result() -> VtsDriverResult:
    return VtsDriverResult(
        success=True,
        reason_code=None,
        detail="VTS reset_to_neutral disabled; hotkey reset skipped",
        action="reset_to_neutral",
        expression_id="NEUTRAL",
        emotion_tag="NEUTRAL",
    )


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
        self._bad_expression_hotkeys: dict[str, str] = {}
        self._hotkey_catalog: list[dict[str, str]] = []
        self._last_model_move: dict[str, Any] = {
            "success": None,
            "reason": "not_started",
        }
        # 方向 C：记录上一次设置的 emotion_tag，用于过渡帧计算
        self._last_emotion_tag: str | None = None
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
            "last_batch_parameters": [],
            "last_motion_parameters": [],
            "last_motion_values": {},
            "last_motion_batch_parameters": [],
            "model_open_inputs": [],
            "model_form_inputs": [],
            "model_smile_inputs": [],
            "model_vtube_path": None,
            "model_base_path": None,
            "model_binding_error": None,
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
                self._connection_error = None
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

        mouth_result = self.set_mouth_open_level(
            VTS_MOUTH_OPEN_VALUE,
            _include_companions=True,
            _batch_companions=True,
        )
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

        mouth_result = self.set_mouth_open_level(
            VTS_MOUTH_CLOSE_VALUE,
            _include_companions=True,
            _batch_companions=True,
        )
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

    @staticmethod
    def _is_recoverable_runtime_api_error(detail: str | None) -> bool:
        text = str(detail or "").strip().lower()
        if not text:
            return False
        hints = (
            "protocol error",
            "received 1002",
            "sent 1002",
            "connectionclosed",
            "websocket",
            "no close frame",
            "closing handshake",
            "cannot write to closing transport",
            "another coroutine is already running recv",
            "recv_streaming",
            "sent 1000",
            "received 1000",
        )
        return any(hint in text for hint in hints)

    def _force_runtime_reconnect(self, reason: str) -> VtsDriverResult:
        self._connection_error = str(reason or "").strip() or None
        logger.warning("VTS runtime reconnect requested: %s", reason)
        with self._lock:
            self._vts = None
            self._state = VtsState.DISCONNECTED
            self._talking = False
        self._shutdown_event.clear()
        result = self.connect()
        event = {
            "event": VTS_RECONNECTED if result.success else VTS_RECONNECT_EXHAUSTED,
            "attempt": 1,
            "ts_ms": now_ms(),
            "detail": f"runtime reconnect: {reason}",
            "success": bool(result.success),
        }
        self._reconnect_events.append(event)
        if result.success:
            self._total_reconnects += 1
        else:
            self._total_reconnect_failures += 1
        return result

    def set_mouth_open_level(
        self,
        value: float,
        *,
        _retry_on_reconnect: bool = True,
        _include_companions: bool = True,
        _batch_companions: bool = False,
        _motion_values: dict[str, float] | None = None,
    ) -> VtsDriverResult:
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

        preferred_mouth_params = self._preferred_mouth_parameters(
            include_volume_fallback=_include_companions,
        )
        mouth_params = list(preferred_mouth_params)
        plan = dict(self._mouth_parameter_plan or {})
        if not _include_companions:
            selected_params = [
                str(item)
                for item in (plan.get("selected") or [])
                if str(item).strip()
            ]
            if selected_params:
                mouth_params = selected_params
        available_params = {
            str(item or "").strip()
            for item in (plan.get("available") or [])
            if str(item or "").strip()
        }
        if available_params:
            available_mouth_params = [item for item in mouth_params if item in available_params]
            if available_mouth_params:
                mouth_params = available_mouth_params

        if not _include_companions and _batch_companions and _motion_values:
            motion_batch_values = self._build_mouth_parameter_batch_values(
                level=level,
                plan=plan,
                available_params=available_params,
                motion_values=_motion_values,
                include_mouth=False,
            )
            if motion_batch_values:
                try:
                    _set_vts_parameters(
                        vts=self._vts,
                        parameter_values=motion_batch_values,
                        timeout_s=self._config.api_timeout_s,
                    )
                    motion_parameters = [
                        key
                        for key in list((_motion_values or {}).keys())
                        if key in motion_batch_values
                    ]
                    plan = dict(self._mouth_parameter_plan)
                    plan["last_motion_parameters"] = list(motion_parameters)
                    plan["last_motion_values"] = {
                        key: round(float(motion_batch_values[key]), 4)
                        for key in motion_parameters
                    }
                    plan["last_motion_batch_parameters"] = list(motion_batch_values.keys())
                    self._mouth_parameter_plan = plan
                except Exception:
                    pass

        if _include_companions and _batch_companions:
            hold_talking_mouth = bool(
                self._talking
                and level <= 0.001
                and _motion_values is not None
            )
            batch_values = self._build_mouth_parameter_batch_values(
                level=level,
                plan=plan,
                available_params=available_params,
                motion_values=_motion_values,
                include_mouth=not hold_talking_mouth,
            )
            if batch_values:
                try:
                    _set_vts_parameters(
                        vts=self._vts,
                        parameter_values=batch_values,
                        timeout_s=self._config.api_timeout_s,
                    )
                    selected_primary = next(iter(batch_values.keys()))
                    plan = dict(self._mouth_parameter_plan)
                    if hold_talking_mouth:
                        plan["last_value"] = round(float(plan.get("last_value") or 0.0), 4)
                    else:
                        plan["selected"] = [selected_primary]
                        plan["last_value"] = round(level, 4)
                    plan["last_batch_parameters"] = list(batch_values.keys())
                    motion_parameters = [
                        key
                        for key in list((_motion_values or {}).keys())
                        if key in batch_values
                    ]
                    if _motion_values is not None:
                        plan["last_motion_parameters"] = list(motion_parameters)
                        plan["last_motion_values"] = {
                            key: round(float(batch_values[key]), 4)
                            for key in motion_parameters
                        }
                        plan["last_motion_batch_parameters"] = list(batch_values.keys())
                    self._mouth_parameter_plan = plan
                    detail_prefix = (
                        "motion batch set while holding talking mouth via "
                        if hold_talking_mouth
                        else "mouth level batch set via "
                    )
                    return VtsDriverResult(
                        success=True,
                        reason_code=None,
                        detail=f"{detail_prefix}{','.join(batch_values.keys())}: {level:.3f}",
                        action="set_mouth_open_level",
                    )
                except Exception as exc:  # pragma: no cover - depends on VTS runtime state
                    if _retry_on_reconnect and self._is_recoverable_runtime_api_error(str(exc)):
                        reconnect_result = self._force_runtime_reconnect(str(exc))
                        if reconnect_result.success:
                            return self.set_mouth_open_level(
                                level,
                                _retry_on_reconnect=False,
                                _include_companions=_include_companions,
                                _batch_companions=_batch_companions,
                                _motion_values=_motion_values,
                            )

        errors: list[str] = []
        failed_params: set[str] = set()
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
                failed_params.add(param)
                errors.append(f"{param}: {exc}")

        if selected_primary is None:
            detail = "all mouth parameters failed"
            if errors:
                detail = f"{detail}: {'; '.join(errors)}"
            if available_params and mouth_params != preferred_mouth_params:
                for param in preferred_mouth_params:
                    if param in mouth_params:
                        continue
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
                        failed_params.add(param)
                        errors.append(f"{param}: {exc}")
                if selected_primary is None:
                    detail = "all mouth parameters failed after fallback"
                    if errors:
                        detail = f"{detail}: {'; '.join(errors)}"
            if selected_primary is None:
                if _retry_on_reconnect and self._is_recoverable_runtime_api_error(detail):
                    reconnect_result = self._force_runtime_reconnect(detail)
                    if reconnect_result.success:
                        return self.set_mouth_open_level(
                            level,
                            _retry_on_reconnect=False,
                            _include_companions=_include_companions,
                            _batch_companions=_batch_companions,
                            _motion_values=_motion_values,
                        )
                return VtsDriverResult(
                    success=False,
                    reason_code=VTS_API_ERROR,
                    detail=detail,
                    action="set_mouth_open_level",
                )

        # Optional companion parameters: failures here should not fail playback.
        #
        # Some VTS models bind mouth opening to voice-derived inputs
        # (for example VoiceVolume), while others react more visibly to
        # MouthOpen/ParamMouthOpenY. Drive the primary parameter first for
        # correctness, then also fan out to companion mouth/volume parameters
        # so a successful API call is more likely to become perceptible on the
        # avatar.
        if not _include_companions:
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

        companion_mouth_params = [
            str(item)
            for item in (
                list(plan.get("mouth") or [])
                + list(self._candidate_tuple(self._config.mouth_param_candidates))
                + [VTS_MOUTH_PARAM, "ParamMouthOpenY"]
            )
            if str(item).strip()
        ]
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
            filtered_mouth = [item for item in companion_mouth_params if item in available_params]
            filtered_volume = [item for item in volume_params if item in available_params]
            filtered_silence = [item for item in silence_params if item in available_params]
            if filtered_mouth:
                companion_mouth_params = filtered_mouth
            if filtered_volume:
                volume_params = filtered_volume
            if filtered_silence:
                silence_params = filtered_silence

        companion_mouth_params = [item for item in companion_mouth_params if item != selected_primary]
        volume_params = [item for item in volume_params if item != selected_primary]
        silence_params = [item for item in silence_params if item != selected_primary]

        companion_seen: set[str] = set()
        companion_mouth_params = [
            item
            for item in companion_mouth_params
            if item not in failed_params and not (item in companion_seen or companion_seen.add(item))
        ]

        for mouth_param in companion_mouth_params:
            try:
                _set_vts_parameter(
                    vts=self._vts,
                    param_name=mouth_param,
                    value=level,
                    timeout_s=self._config.api_timeout_s,
                )
            except Exception:
                continue
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

        motion_parameters: list[str] = []
        motion_values: dict[str, float] = {}
        for param_name, raw_value in dict(_motion_values or {}).items():
            param = str(param_name or "").strip()
            if not param:
                continue
            if available_params and param not in available_params:
                continue
            try:
                value_to_set = float(raw_value)
                _set_vts_parameter(
                    vts=self._vts,
                    param_name=param,
                    value=value_to_set,
                    timeout_s=self._config.api_timeout_s,
                )
            except Exception:
                continue
            motion_parameters.append(param)
            motion_values[param] = round(value_to_set, 4)

        plan = dict(self._mouth_parameter_plan)
        plan["selected"] = [selected_primary]
        plan["last_value"] = round(level, 4)
        if _motion_values is not None:
            plan["last_motion_parameters"] = list(motion_parameters)
            plan["last_motion_values"] = dict(motion_values)
            plan["last_motion_batch_parameters"] = []
        self._mouth_parameter_plan = plan

        return VtsDriverResult(
            success=True,
            reason_code=None,
            detail=f"mouth level set via {selected_primary}: {level:.3f}",
            action="set_mouth_open_level",
        )

    def move_model(
        self,
        *,
        position_x: float = 0.0,
        position_y: float = 0.0,
        rotation: float = 0.0,
        size: float = 0.0,
        relative: bool = True,
        move_time_s: float = 0.12,
        _retry_on_reconnect: bool = True,
    ) -> VtsDriverResult:
        """Move the whole VTS model, independent of Live2D parameter bindings."""
        if self._state != VtsState.AUTHENTICATED:
            conn_result = self.connect()
            if not conn_result.success:
                result = VtsDriverResult(
                    success=False,
                    reason_code=VTS_CONNECTION_FAILED,
                    detail=f"cannot move model: {conn_result.detail}",
                    action="move_model",
                )
                self._last_model_move = {
                    **result.as_dict(),
                    "position_x": float(position_x),
                    "position_y": float(position_y),
                    "rotation": float(rotation),
                    "size": float(size),
                    "relative": bool(relative),
                    "move_time_s": float(move_time_s),
                }
                return result
        try:
            _move_vts_model(
                vts=self._vts,
                position_x=float(position_x),
                position_y=float(position_y),
                rotation=float(rotation),
                size=float(size),
                relative=bool(relative),
                move_time_s=float(move_time_s),
                timeout_s=self._config.api_timeout_s,
            )
            result = VtsDriverResult(
                success=True,
                reason_code=None,
                detail=(
                    "model moved "
                    f"x={float(position_x):.4f} y={float(position_y):.4f} "
                    f"rot={float(rotation):.3f}"
                ),
                action="move_model",
            )
        except Exception as exc:  # pragma: no cover - depends on VTS runtime state
            if _retry_on_reconnect and self._is_recoverable_runtime_api_error(str(exc)):
                reconnect_result = self._force_runtime_reconnect(str(exc))
                if reconnect_result.success:
                    return self.move_model(
                        position_x=position_x,
                        position_y=position_y,
                        rotation=rotation,
                        size=size,
                        relative=relative,
                        move_time_s=move_time_s,
                        _retry_on_reconnect=False,
                    )
            result = VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"move model error: {exc}",
                action="move_model",
            )
        self._last_model_move = {
            **result.as_dict(),
            "position_x": round(float(position_x), 5),
            "position_y": round(float(position_y), 5),
            "rotation": round(float(rotation), 4),
            "size": round(float(size), 5),
            "relative": bool(relative),
            "move_time_s": round(float(move_time_s), 4),
        }
        return result

    def _build_mouth_parameter_batch_values(
        self,
        *,
        level: float,
        plan: dict[str, Any],
        available_params: set[str],
        motion_values: dict[str, float] | None = None,
        include_mouth: bool = True,
    ) -> dict[str, float]:
        ordered_open_params = [
            str(item)
            for item in (
                list(plan.get("model_open_inputs") or [])
                + list(plan.get("selected") or [])
                + list(plan.get("mouth") or [])
                + list(self._candidate_tuple(self._config.mouth_param_candidates))
                + list(plan.get("volume") or [])
                + list(self._candidate_tuple(self._config.mouth_volume_param_candidates))
                + [VTS_MOUTH_PARAM, "ParamMouthOpenY"]
            )
            if str(item).strip()
        ]
        silence_params = [
            str(item)
            for item in (
                list(plan.get("silence") or [])
                + list(self._candidate_tuple(self._config.voice_silence_param_candidates))
            )
            if str(item).strip()
        ]
        if available_params:
            ordered_open_params = [item for item in ordered_open_params if item in available_params]
            silence_params = [item for item in silence_params if item in available_params]

        values: dict[str, float] = {}
        if include_mouth:
            for param in ordered_open_params:
                values.setdefault(param, level)
            silence_value = max(0.0, min(1.0, 1.0 - level))
            for param in silence_params:
                values.setdefault(param, silence_value)
        for param_name, raw_value in dict(motion_values or {}).items():
            param = str(param_name or "").strip()
            if not param:
                continue
            if available_params and param not in available_params:
                continue
            try:
                values[param] = float(raw_value)
            except (TypeError, ValueError):
                continue
        return values

    def set_expression(
        self,
        emotion_tag: str,
        *,
        intensity: float = 0.5,
        _retry_on_reconnect: bool = True,
    ) -> VtsDriverResult:
        """Set expression via deterministic emotion_tag -> expression_id mapping.

        Rejects unknown tags and free-form parameters.
        """
        normalized = normalize_expression_emotion_tag(emotion_tag)
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
        if override_hotkey and (
            self._is_disallowed_expression_hotkey(override_hotkey)
            or self._is_bad_expression_hotkey(override_hotkey)
        ):
            self._expression_hotkey_overrides.pop(normalized, None)
            override_hotkey = ""
        discovered_hotkey = self._resolve_hotkey_for_emotion(
            emotion_tag=normalized,
            preferred_hotkey=expression_id,
            include_preferred=False,
        )
        hotkey_id = override_hotkey or discovered_hotkey or expression_id
        try:
            _trigger_vts_hotkey(
                vts=self._vts,
                hotkey_id=hotkey_id,
                timeout_s=self._config.api_timeout_s,
            )
            self._current_expression = hotkey_id
            # 方向 C：记录成功设置的 emotion_tag
            self._last_emotion_tag = normalized
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
            if self._is_broken_hotkey_error(str(e)):
                self._mark_expression_hotkey_failed(hotkey_id, str(e))
            if override_hotkey and hotkey_id == override_hotkey:
                self._expression_hotkey_overrides.pop(normalized, None)
            fallback_hotkey = self._resolve_hotkey_for_emotion(
                emotion_tag=normalized,
                preferred_hotkey=expression_id,
                include_preferred=False,
                exclude_hotkeys=(hotkey_id,),
            )
            if not fallback_hotkey:
                fallback_hotkey = self._resolve_hotkey_for_emotion(
                    emotion_tag=normalized,
                    preferred_hotkey=expression_id,
                    include_preferred=False,
                    force_refresh=True,
                    exclude_hotkeys=(hotkey_id,),
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
                    self._last_emotion_tag = normalized
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
                except Exception as fallback_error:
                    if self._is_broken_hotkey_error(str(fallback_error)):
                        self._mark_expression_hotkey_failed(fallback_hotkey, str(fallback_error))
            if _retry_on_reconnect and self._is_recoverable_runtime_api_error(str(e)):
                reconnect_result = self._force_runtime_reconnect(str(e))
                if reconnect_result.success:
                    return self.set_expression(
                        normalized,
                        intensity=intensity,
                        _retry_on_reconnect=False,
                    )
            return VtsDriverResult(
                success=False,
                reason_code=VTS_API_ERROR,
                detail=f"expression error: {e}",
                action="set_expression",
                expression_id=hotkey_id,
                emotion_tag=normalized,
            )

    @property
    def last_emotion_tag(self) -> str | None:
        """方向 C：返回最后成功设置的 emotion_tag（用于过渡帧起始状态）。"""
        return self._last_emotion_tag

    def transition_expression(
        self,
        target_tag: str,
        *,
        transition_frames: list[dict[str, Any]] | None = None,
        frame_interval_s: float = 0.15,
    ) -> VtsDriverResult:
        """方向 C：带过渡帧的表情切换。

        逐帧调用 set_expression，每帧之间间隔 frame_interval_s 秒。
        如果 transition_frames 为空或 None，则退化为普通 set_expression。

        Args:
            target_tag: 目标 emotion_tag
            transition_frames: 过渡帧列表（每个 dict 含 emotion_tag/delay_ms）
            frame_interval_s: 帧间等待秒数（仅当 delay_ms 不可用时使用）

        Returns:
            最后一帧（目标帧）的 set_expression 结果
        """
        if not transition_frames or len(transition_frames) <= 1:
            return self.set_expression(target_tag)

        last_result: VtsDriverResult | None = None
        prev_tag: str | None = None

        for frame in transition_frames:
            frame_tag = str(frame.get("emotion_tag") or target_tag).strip().upper()
            # 跳过连续相同标签（避免冗余 VTS 调用）
            if frame_tag == prev_tag:
                continue
            prev_tag = frame_tag

            delay_ms = frame.get("delay_ms", 0)
            if delay_ms and last_result is not None:
                time.sleep(min(float(delay_ms) / 1000.0, frame_interval_s))

            last_result = self.set_expression(frame_tag)
            if not last_result.success and last_result.reason_code == AVATAR_TAG_INVALID:
                # 过渡帧中出现未知标签，跳过继续
                continue

        # 确保最终设置到目标标签
        if last_result is None or last_result.emotion_tag != target_tag.strip().upper():
            last_result = self.set_expression(target_tag)

        return last_result or self.set_expression(target_tag)

    def reset_to_neutral(self) -> VtsDriverResult:
        """Reset expression to NEUTRAL (used after barge-in or end of talk)."""
        self._talking = False
        if not _vts_reset_to_neutral_enabled():
            result = _reset_to_neutral_skipped_result()
            if self._state == VtsState.AUTHENTICATED:
                try:
                    self.set_mouth_open_level(
                        VTS_MOUTH_CLOSE_VALUE,
                        _include_companions=True,
                        _batch_companions=True,
                    )
                except Exception:
                    pass
            return result

        result: VtsDriverResult | None = None
        if self._state == VtsState.AUTHENTICATED:
            reset_hotkey = self._resolve_neutral_reset_hotkey()
            if not reset_hotkey:
                reset_hotkey = self._resolve_neutral_reset_hotkey(force_refresh=True)
            if reset_hotkey:
                try:
                    _trigger_vts_hotkey(
                        vts=self._vts,
                        hotkey_id=reset_hotkey,
                        timeout_s=self._config.api_timeout_s,
                    )
                    self._current_expression = reset_hotkey
                    self._last_emotion_tag = "NEUTRAL"
                    result = VtsDriverResult(
                        success=True,
                        reason_code=None,
                        detail=f"expression reset via neutral hotkey: {reset_hotkey}",
                        action="set_expression",
                        expression_id=reset_hotkey,
                        emotion_tag="NEUTRAL",
                    )
                except TimeoutError as e:
                    result = VtsDriverResult(
                        success=False,
                        reason_code=VTS_EXPRESSION_TIMEOUT,
                        detail=f"expression reset timeout: {e}",
                        action="set_expression",
                        expression_id=reset_hotkey,
                        emotion_tag="NEUTRAL",
                    )
                except Exception:
                    result = None

        if result is None:
            result = self.set_expression("NEUTRAL")

        #
        if self._state == VtsState.AUTHENTICATED:
            try:
                self.set_mouth_open_level(
                    VTS_MOUTH_CLOSE_VALUE,
                    _include_companions=True,
                    _batch_companions=True,
                )
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
        if is_blocked_avatar_hotkey_id(normalized_id):
            return _blocked_hotkey_result(normalized_id)

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
        seen: set[str] = set()
        for raw in values or ():
            text = str(raw or "").strip()
            if text and text not in seen:
                seen.add(text)
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

        model_binding = _discover_current_model_mouth_bindings()
        model_open_inputs = self._candidate_tuple(model_binding.get("open_inputs"))
        model_form_inputs = self._candidate_tuple(model_binding.get("form_inputs"))
        model_smile_inputs = self._candidate_tuple(model_binding.get("smile_inputs"))

        mouth_candidates = self._candidate_tuple(
            list(model_open_inputs) + list(self._config.mouth_param_candidates)
        )
        volume_candidates = self._candidate_tuple(
            list(model_open_inputs) + list(self._config.mouth_volume_param_candidates)
        )
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
            "model_open_inputs": list(model_open_inputs),
            "model_form_inputs": list(model_form_inputs),
            "model_smile_inputs": list(model_smile_inputs),
            "model_vtube_path": model_binding.get("vtube_path"),
            "model_base_path": model_binding.get("model_base_path"),
            "model_binding_error": model_binding.get("last_error"),
        }

    def _preferred_mouth_parameters(self, *, include_volume_fallback: bool = True) -> list[str]:
        plan = dict(self._mouth_parameter_plan or {})
        selected = [str(item) for item in (plan.get("selected") or []) if str(item).strip()]
        plan_mouth = [str(item) for item in (plan.get("mouth") or []) if str(item).strip()]
        cfg_candidates = [str(item) for item in self._candidate_tuple(self._config.mouth_param_candidates)]
        plan_volume = [str(item) for item in (plan.get("volume") or []) if str(item).strip()]
        cfg_volume = [str(item) for item in self._candidate_tuple(self._config.mouth_volume_param_candidates)]
        ordered = selected + plan_mouth + cfg_candidates + [VTS_MOUTH_PARAM]
        if include_volume_fallback:
            ordered += plan_volume + cfg_volume
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

    def _is_bad_expression_hotkey(self, hotkey_id: str) -> bool:
        normalized = str(hotkey_id or "").strip().lower()
        return bool(normalized and normalized in self._bad_expression_hotkeys)

    @staticmethod
    def _is_broken_hotkey_error(detail: str | None) -> bool:
        text = str(detail or "").strip().lower()
        if not text:
            return False
        return any(hint in text for hint in BROKEN_HOTKEY_ERROR_HINTS)

    def _mark_expression_hotkey_failed(self, hotkey_id: str, detail: str) -> None:
        normalized = str(hotkey_id or "").strip()
        if not normalized:
            return
        self._bad_expression_hotkeys[normalized.lower()] = str(detail or "").strip()[:240]
        for emotion_tag, override in list(self._expression_hotkey_overrides.items()):
            if str(override or "").strip().lower() == normalized.lower():
                self._expression_hotkey_overrides.pop(emotion_tag, None)

    def _resolve_neutral_reset_hotkey(self, *, force_refresh: bool = False) -> str | None:
        catalog = list(self._hotkey_catalog or [])
        if force_refresh or not catalog:
            catalog = _list_vts_hotkeys(vts=self._vts, timeout_s=self._config.api_timeout_s)
            self._hotkey_catalog = list(catalog)
        if not catalog:
            return None

        for row in catalog:
            hotkey_id = str(row.get("id") or "").strip()
            action = str(row.get("action") or "").strip().lower()
            name = str(row.get("name") or "").strip()
            if is_blocked_avatar_hotkey_id(hotkey_id, name=name, action=action):
                continue
            if hotkey_id and action in NEUTRAL_RESET_HOTKEY_ACTIONS:
                return hotkey_id

        for row in catalog:
            hotkey_id = str(row.get("id") or "").strip()
            name = str(row.get("name") or "").strip().lower()
            if not hotkey_id:
                continue
            if is_blocked_avatar_hotkey_id(hotkey_id, name=name):
                continue
            if any(hint in name for hint in NEUTRAL_RESET_HOTKEY_HINTS):
                return hotkey_id
        return None

    def _resolve_hotkey_for_emotion(
        self,
        *,
        emotion_tag: str,
        preferred_hotkey: str,
        include_preferred: bool = True,
        force_refresh: bool = False,
        exclude_hotkeys: tuple[str, ...] = (),
    ) -> str | None:
        catalog = list(self._hotkey_catalog or [])
        excluded = {
            str(item or "").strip().lower()
            for item in exclude_hotkeys
            if str(item or "").strip()
        }
        excluded.update(self._bad_expression_hotkeys.keys())
        if force_refresh or not catalog:
            catalog = _list_vts_hotkeys(vts=self._vts, timeout_s=self._config.api_timeout_s)
            self._hotkey_catalog = list(catalog)
        if not catalog:
            return None

        preferred_norm = str(preferred_hotkey or "").strip().lower()
        if include_preferred and preferred_norm:
            for row in catalog:
                hotkey_id = str(row.get("id") or "").strip()
                name = str(row.get("name") or "").strip()
                if hotkey_id.lower() in excluded:
                    continue
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
                if hotkey_id.lower() in excluded:
                    continue
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
                "last_batch_parameters": list(self._mouth_parameter_plan.get("last_batch_parameters") or []),
                "last_motion_parameters": list(self._mouth_parameter_plan.get("last_motion_parameters") or []),
                "last_motion_values": dict(self._mouth_parameter_plan.get("last_motion_values") or {}),
                "last_motion_batch_parameters": list(self._mouth_parameter_plan.get("last_motion_batch_parameters") or []),
                "model_open_inputs": list(self._mouth_parameter_plan.get("model_open_inputs") or []),
                "model_form_inputs": list(self._mouth_parameter_plan.get("model_form_inputs") or []),
                "model_smile_inputs": list(self._mouth_parameter_plan.get("model_smile_inputs") or []),
                "model_vtube_path": self._mouth_parameter_plan.get("model_vtube_path"),
                "model_base_path": self._mouth_parameter_plan.get("model_base_path"),
                "model_binding_error": self._mouth_parameter_plan.get("model_binding_error"),
            },
            "config": self._config.as_dict(),
            "connection_error": self._connection_error,
            "last_model_move": dict(self._last_model_move),
            "emotion_map_size": len(EMOTION_TO_EXPRESSION_ID),
            "hotkey_catalog_size": len(self._hotkey_catalog),
            "expression_overrides": dict(self._expression_hotkey_overrides),
            "bad_expression_hotkeys": dict(self._bad_expression_hotkeys),
            "reconnect": {
                "total_reconnects": self._total_reconnects,
                "total_reconnect_failures": self._total_reconnect_failures,
                "reconnect_attempts": self._reconnect_attempts,
                "reconnecting": self._state == VtsState.RECONNECTING,
                "reconnect_events_count": len(self._reconnect_events),
            },
        }

    # ── 面部捕捉感知：读取用户表情参数 ──────────────────────────────────────

    def read_face_capture_params(
        self,
        *,
        param_ids: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, float]:
        """读取 VTS 面部捕捉参数值，用于感知用户当前表情状态。

        通过 VTS ParameterValueRequest API 批量读取面部捕捉参数，
        将摄像头捕捉到的用户脸部动作数字化后返回。

        Args:
            param_ids: 要读取的参数 ID 列表，None 时使用默认面捕参数集。
            timeout_s: API 超时秒数，None 时使用配置默认值。

        Returns:
            {param_id: value} 字典，value 范围通常为 [0.0, 1.0] 或 [-1.0, 1.0]。
            VTS 未连接或读取失败时返回空字典。
        """
        if self._vts is None or self._state != VtsState.AUTHENTICATED:
            return {}

        _timeout = float(timeout_s or self._config.api_timeout_s)
        _param_ids = list(param_ids or _DEFAULT_FACE_CAPTURE_PARAMS)

        try:
            return _read_vts_face_params(
                vts=self._vts,
                param_ids=_param_ids,
                timeout_s=_timeout,
            )
        except Exception as exc:
            import logging as _logging
            _logging.getLogger("Natsume.edge.vts_driver").debug(
                "read_face_capture_params 失败: %s", exc
            )
            return {}

    def infer_user_expression(
        self,
        *,
        face_params: dict[str, float] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """从面部捕捉参数推断用户当前表情标签。

        先调用 read_face_capture_params() 获取原始参数（若未传入），
        再通过规则映射推断用户表情标签和置信度。

        Args:
            face_params: 已读取的面部参数字典，None 时自动读取。
            timeout_s: API 超时秒数。

        Returns:
            {
                "expression_tag": str,       # "SMILE"/"FROWN"/"SURPRISED"/"NEUTRAL"/"BLINK" 等
                "confidence": float,         # 推断置信度 [0.0, 1.0]
                "raw_params": dict,          # 原始参数值
                "mouth_open": float,         # 嘴巴张开程度
                "smile": float,              # 笑容程度
                "brow_raise": float,         # 眉毛上扬程度
                "eye_open_avg": float,       # 平均眼睛睁开程度
            }
        """
        params = face_params if face_params is not None else self.read_face_capture_params(
            timeout_s=timeout_s
        )
        return _infer_expression_from_face_params(params)


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

    def _auth_error(response: dict[str, Any], *, default: str) -> str:
        data = dict(response.get("data") or {})
        reason = str(data.get("reason") or "").strip()
        if reason:
            return reason
        return _extract_vts_api_error(response, default=default)

    async def _auth() -> None:
        await vts.connect()

        async def _request_and_store_token(*, detail_default: str) -> str:
            token_request = vts.vts_request.authentication_token()
            token_response = await vts.request(token_request)
            token_data = dict(token_response.get("data") or {})
            token_value = str(token_data.get("authenticationToken") or "").strip()
            if not token_value:
                raise RuntimeError(_auth_error(token_response, default=detail_default))
            vts.authentic_token = token_value
            await vts.write_token()
            return token_value

        async def _request_auth_response(token_value: str) -> dict[str, Any]:
            vts.authentic_token = token_value
            auth_request = vts.vts_request.authentication(vts.authentic_token)
            return await vts.request(auth_request)

        token = await vts.read_token()
        if not token:
            token = await _request_and_store_token(detail_default="failed to get authentication token")

        auth_response = await _request_auth_response(token)
        auth_ok = bool((auth_response.get("data") or {}).get("authenticated"))
        if not auth_ok and token:
            logger.warning(
                "VTS authentication rejected for plugin=%s developer=%s; refreshing token and retrying once",
                plugin_name,
                plugin_developer,
            )
            token = await _request_and_store_token(detail_default="failed to refresh authentication token")
            auth_response = await _request_auth_response(token)
            auth_ok = bool((auth_response.get("data") or {}).get("authenticated"))
        if not auth_ok:
            raise RuntimeError(_auth_error(auth_response, default="authentication rejected by VTube Studio"))

    _run_in_vts_loop(_auth(), timeout_s=30.0)


def _set_vts_parameter(
    *,
    vts: Any,
    param_name: str,
    value: float,
    timeout_s: float,
) -> None:
    """Set a VTS input parameter value."""
    _set_vts_parameters(
        vts=vts,
        parameter_values={param_name: float(value)},
        timeout_s=timeout_s,
    )


def _set_vts_parameters(
    *,
    vts: Any,
    parameter_values: dict[str, float],
    timeout_s: float,
) -> None:
    """Set one or more VTS input parameter values in one request."""
    if vts is None:
        return
    values: list[dict[str, float | str]] = []
    seen: set[str] = set()
    for param_name, value in parameter_values.items():
        param = str(param_name or "").strip()
        if not param or param in seen:
            continue
        seen.add(param)
        values.append(
            {
                "id": param,
                "weight": 1.0,
                "value": float(value),
            }
        )
    if not values:
        return

    async def _set() -> None:
        payload = {
            "faceFound": True,
            "mode": "set",
            "parameterValues": values,
        }
        request_api = getattr(vts, "vts_request", None)
        base_request = getattr(request_api, "BaseRequest", None)
        if base_request is not None:
            request = base_request("InjectParameterDataRequest", payload)
        else:
            request = {"messageType": "InjectParameterDataRequest", "data": payload}
        response = await asyncio.wait_for(
            vts.request(request),
            timeout=timeout_s,
        )
        error = _extract_vts_api_error(response, default="")
        if error:
            raise RuntimeError(error)

    _run_in_vts_loop(_set(), timeout_s=timeout_s + 1.0)


def _move_vts_model(
    *,
    vts: Any,
    position_x: float,
    position_y: float,
    rotation: float,
    size: float,
    relative: bool,
    move_time_s: float,
    timeout_s: float,
) -> None:
    """Move the VTS model in the scene using MoveModelRequest."""
    if vts is None:
        return

    async def _move() -> None:
        request_api = getattr(vts, "vts_request", None)
        request_move = getattr(request_api, "requestMoveModel", None)
        if request_move is not None:
            request = request_move(
                position_x,
                position_y,
                rot=rotation,
                size=size,
                relative=relative,
                move_time=move_time_s,
            )
        else:
            base_request = getattr(request_api, "BaseRequest", None)
            payload = {
                "timeInSeconds": move_time_s,
                "valuesAreRelativeToModel": relative,
                "positionX": position_x,
                "positionY": position_y,
                "rotation": rotation,
                "size": size,
            }
            if base_request is not None:
                request = base_request("MoveModelRequest", payload)
            else:
                request = {"messageType": "MoveModelRequest", "data": payload}
        response = await asyncio.wait_for(vts.request(request), timeout=timeout_s)
        error = _extract_vts_api_error(response, default="")
        if error:
            raise RuntimeError(error)

    _run_in_vts_loop(_move(), timeout_s=timeout_s + 1.0)


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
                pid = str(
                    row.get("id")
                    or row.get("name")
                    or row.get("parameterID")
                    or row.get("parameterId")
                    or ""
                ).strip()
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
                    "action": str(row.get("type") or row.get("action") or row.get("Action") or "").strip(),
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
    with _VTS_REQUEST_LOCK:
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


# ---------------------------------------------------------------------------
# 面部捕捉感知：参数常量与推断逻辑
# ---------------------------------------------------------------------------

# 默认读取的面部捕捉参数集（VTS 标准参数 + Live2D 常用参数）
_DEFAULT_FACE_CAPTURE_PARAMS: tuple[str, ...] = (
    # 嘴部
    "MouthOpen",          # 嘴巴张开 [0, 1]
    "MouthSmile",         # 嘴角上扬/笑容 [-1, 1]
    # 眼部
    "EyeOpenLeft",        # 左眼睁开 [0, 1]
    "EyeOpenRight",       # 右眼睁开 [0, 1]
    "EyeLeftX",           # 左眼球水平 [-1, 1]
    "EyeLeftY",           # 左眼球垂直 [-1, 1]
    # 眉毛
    "BrowLeftY",          # 左眉高度 [-1, 1]
    "BrowRightY",         # 右眉高度 [-1, 1]
    # 头部姿态
    "FaceAngleX",         # 头部左右偏转 [-30, 30]
    "FaceAngleY",         # 头部上下偏转 [-30, 30]
    "FaceAngleZ",         # 头部旋转 [-30, 30]
    # Live2D 别名（部分模型使用）
    "ParamMouthOpenY",
    "ParamMouthForm",
    "ParamEyeLOpen",
    "ParamEyeROpen",
    "ParamBrowLY",
    "ParamBrowRY",
)


def _read_vts_face_params(
    *,
    vts: Any,
    param_ids: list[str],
    timeout_s: float,
) -> dict[str, float]:
    """通过 VTS ParameterValueRequest 批量读取面部捕捉参数值。

    VTS API 每次只能查询单个参数，因此对每个参数 ID 发起独立请求，
    忽略不存在的参数（VTS 返回 errorID > 0 时跳过）。
    """
    if vts is None or not param_ids:
        return {}

    async def _read_all() -> dict[str, float]:
        results: dict[str, float] = {}
        for pid in param_ids:
            try:
                request = vts.vts_request.BaseRequest(
                    "ParameterValueRequest",
                    {"name": pid},
                )
                response = await asyncio.wait_for(
                    vts.request(request),
                    timeout=timeout_s,
                )
                error = _extract_vts_api_error(response, default="")
                if error:
                    continue  # 参数不存在或不可用，跳过
                data = dict(response.get("data") or {})
                value = data.get("value")
                if value is not None:
                    try:
                        results[pid] = float(value)
                    except (TypeError, ValueError):
                        pass
            except Exception:
                continue  # 单个参数失败不影响其他参数
        return results

    try:
        return dict(_run_in_vts_loop(_read_all(), timeout_s=timeout_s + 2.0) or {})
    except Exception:
        return {}


def _infer_expression_from_face_params(params: dict[str, float]) -> dict[str, Any]:
    """从面部捕捉参数推断用户表情标签。

    规则映射（优先级从高到低）：
    1. SURPRISED：嘴巴大开（>0.6）且眉毛上扬（>0.3）
    2. SMILE：嘴角上扬（>0.3）且嘴巴微开或闭合
    3. FROWN：嘴角下压（<-0.2）或眉毛皱起（<-0.3）
    4. BLINK：双眼接近闭合（<0.2）
    5. NEUTRAL：其他情况

    Returns:
        {
            "expression_tag": str,
            "confidence": float,
            "raw_params": dict,
            "mouth_open": float,
            "smile": float,
            "brow_raise": float,
            "eye_open_avg": float,
        }
    """
    if not params:
        return {
            "expression_tag": "NEUTRAL",
            "confidence": 0.0,
            "raw_params": {},
            "mouth_open": 0.0,
            "smile": 0.0,
            "brow_raise": 0.0,
            "eye_open_avg": 1.0,
        }

    def _get(*keys: str, default: float = 0.0) -> float:
        for k in keys:
            v = params.get(k)
            if v is not None:
                return float(v)
        return float(default)

    # 提取关键维度（兼容标准参数名和 Live2D 别名）
    mouth_open = _get("MouthOpen", "ParamMouthOpenY", default=0.0)
    smile = _get("MouthSmile", "ParamMouthForm", default=0.0)
    eye_open_left = _get("EyeOpenLeft", "ParamEyeLOpen", default=1.0)
    eye_open_right = _get("EyeOpenRight", "ParamEyeROpen", default=1.0)
    brow_left = _get("BrowLeftY", "ParamBrowLY", default=0.0)
    brow_right = _get("BrowRightY", "ParamBrowRY", default=0.0)

    eye_open_avg = (eye_open_left + eye_open_right) / 2.0
    brow_raise = (brow_left + brow_right) / 2.0

    # ── 规则推断 ──────────────────────────────────────────────────────────
    expression_tag = "NEUTRAL"
    confidence = 0.5

    if mouth_open > 0.6 and brow_raise > 0.3:
        # 嘴大开 + 眉毛上扬 → 惊讶
        expression_tag = "SURPRISED"
        confidence = min(1.0, (mouth_open - 0.6) * 2.5 + (brow_raise - 0.3) * 2.0)
    elif smile > 0.3:
        # 嘴角上扬 → 微笑
        expression_tag = "SMILE"
        confidence = min(1.0, (smile - 0.3) * 1.4 + 0.4)
    elif smile < -0.2 or brow_raise < -0.3:
        # 嘴角下压或眉毛皱起 → 皱眉/不悦
        expression_tag = "FROWN"
        confidence = min(1.0, max(abs(smile + 0.2) * 1.5, abs(brow_raise + 0.3) * 1.5) + 0.3)
    elif eye_open_avg < 0.2:
        # 双眼接近闭合 → 眨眼/闭眼
        expression_tag = "BLINK"
        confidence = min(1.0, (0.2 - eye_open_avg) * 5.0 + 0.4)
    else:
        # 中性
        expression_tag = "NEUTRAL"
        confidence = 0.6

    return {
        "expression_tag": expression_tag,
        "confidence": round(min(1.0, max(0.0, confidence)), 3),
        "raw_params": dict(params),
        "mouth_open": round(mouth_open, 3),
        "smile": round(smile, 3),
        "brow_raise": round(brow_raise, 3),
        "eye_open_avg": round(eye_open_avg, 3),
    }
