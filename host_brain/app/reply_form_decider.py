"""reply_form_decider.py — Normalized response-form decision for gateway transport.

The host chain decides *what* form to send; transport only executes that plan.
Planned multimodal forms therefore take precedence over heuristic voice extras.
"""

from __future__ import annotations

import datetime
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MsgType(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    VIDEO = "video"
    FILE = "file"
    STICKER = "image"


@dataclass(frozen=True)
class ResponseFormDecision:
    primary: MsgType
    secondary: Optional[MsgType]
    reason: str

    @property
    def is_dual(self) -> bool:
        return self.secondary is not None


ReplyFormDecision = ResponseFormDecision


_WARMTH_KEYWORDS: frozenset[str] = frozenset({
    "晚安", "早安", "早上好", "晚上好", "加油", "抱抱", "辛苦了", "辛苦",
    "想你", "别担心", "我在呢", "没事的", "好好休息", "休息一下",
    "生日快乐", "节日快乐", "新年快乐", "祝你", "陪着你",
    "おやすみ", "おはよう", "頑張って", "ありがとう",
})

_VOICE_EMOTION_TAGS: frozenset[str] = frozenset({
    "COMFORT", "COQUETTISH", "HAPPINESS", "PLAYFUL", "LOVING", "SURPRISED",
})

_VOICE_PROBABILITY_BY_BOND: dict[str, float] = {
    "FRIEND": 0.10,
    "CLOSE_FRIEND": 0.20,
    "CONFIDANT": 0.30,
    "SOULMATE": 0.30,
    "INTIMATE": 0.30,
}

DEFAULT_DAILY_VOICE_LIMIT = 24
DEFAULT_MIN_VOICE_INTERVAL_MS = 90 * 1000
DEFAULT_MIN_VOICE_REPLY_CHARS = 1
DEFAULT_MAX_VOICE_REPLY_CHARS = 96
_LATE_NIGHT_PROB_SCALE = 0.5
_QUIET_HOUR_START = 23
_QUIET_HOUR_END = 7

_EXPLICIT_VOICE_REQUEST_RE = re.compile(
    r"(发语音|用语音|语音回复|读出来|念出来|说出来|讲出来|voice|voice\s*note|speak)",
    re.IGNORECASE,
)
_TEXT_ONLY_REQUEST_RE = re.compile(
    r"(只发文字|文字回复|打字就行|别发语音|不要语音|不用语音|text\s*only)",
    re.IGNORECASE,
)
_SHORT_INTERJECTION_RE = re.compile(
    r"^(哈+[?？!！。.]?|啊+[?？!！。.]?|嗯+[?？!！。.]?|哦+[?？!！。.]?|喔+[?？!！。.]?|好(?:的|呀)?[?？!！。.]?|收到[?？!！。.]?|ok[?？!！。.]?)$",
    re.IGNORECASE,
)
_EMBODIED_PRESENCE_RE = re.compile(
    r"(我在|在呢|来啦|听到了|知道啦|行了|好啦|别怕|慢慢说|陪你|抱抱|哈+[?？!！。.]?$|嗯+[?？!！。.]?$)",
    re.IGNORECASE,
)
_STRUCTURED_REPLY_MARKERS: tuple[str, ...] = (
    "```",
    "http://",
    "https://",
    "\n- ",
    "\n* ",
    "\n1.",
    "\n1、",
    "|",
)
_VOICE_INPUT_TYPES: frozenset[str] = frozenset({"voice", "audio", "record", "sound"})
_CLOSE_VOICE_BONDS: frozenset[str] = frozenset({"CLOSE_FRIEND", "CONFIDANT", "SOULMATE", "INTIMATE"})


class ResponseFormDecider:
    """Canonical host-side response-form decider.

    Planned primary/secondary forms from the reply plan are authoritative.
    When the plan does not specify a form, text remains the durable transcript
    while voice carries embodied presence for short conversational turns.
    """

    def __init__(
        self,
        *,
        voice_enabled: bool | None = None,
        probability_scale: float | None = None,
        daily_voice_limit: int = DEFAULT_DAILY_VOICE_LIMIT,
        min_voice_interval_ms: int = DEFAULT_MIN_VOICE_INTERVAL_MS,
        min_voice_reply_chars: int | None = None,
        max_voice_reply_chars: int | None = None,
    ) -> None:
        if voice_enabled is None:
            raw = str(os.environ.get("HOST_GATEWAY_VOICE_REPLY_ENABLED") or "").strip().lower()
            self._voice_enabled = raw not in ("0", "false", "no", "off")
        else:
            self._voice_enabled = bool(voice_enabled)

        if probability_scale is None:
            try:
                self._prob_scale = float(os.environ.get("HOST_GATEWAY_VOICE_PROBABILITY_SCALE") or "1.0")
            except (TypeError, ValueError):
                self._prob_scale = 1.0
        else:
            self._prob_scale = max(0.0, float(probability_scale))

        self._daily_voice_limit = max(1, int(daily_voice_limit))
        self._min_voice_interval_ms = max(0, int(min_voice_interval_ms))
        self._min_voice_reply_chars = _read_int_env(
            "HOST_GATEWAY_VOICE_MIN_TEXT_LEN",
            default=DEFAULT_MIN_VOICE_REPLY_CHARS if min_voice_reply_chars is None else int(min_voice_reply_chars),
            minimum=1,
            maximum=80,
        )
        self._max_voice_reply_chars = _read_int_env(
            "HOST_GATEWAY_VOICE_MAX_TEXT_LEN",
            default=DEFAULT_MAX_VOICE_REPLY_CHARS if max_voice_reply_chars is None else int(max_voice_reply_chars),
            minimum=max(10, self._min_voice_reply_chars),
            maximum=500,
        )
        self._voice_stats: dict[str, dict] = {}

    def decide(
        self,
        *,
        user_id: str = "default",
        user_msg_type: MsgType = MsgType.TEXT,
        narrative_zh: str,
        emotion_tag: str = "NEUTRAL",
        bond_stage: str = "FRIEND",
        planned_primary: MsgType | None = None,
        planned_secondary: MsgType | None = None,
        planned_reason: str = "",
        user_text: str = "",
        platform: str = "",
    ) -> ResponseFormDecision:
        if planned_primary is not None:
            return ResponseFormDecision(
                primary=planned_primary,
                secondary=planned_secondary,
                reason=str(planned_reason or "planned_form").strip() or "planned_form",
            )

        user_key = str(user_id or "default").strip() or "default"
        reply_text_raw = str(narrative_zh or "").strip()
        reply_text = _compact_text(narrative_zh)
        input_text = _compact_text(user_text)
        input_type = _normalize_msg_type(user_msg_type)
        now_ts = _now_ms()

        _ = platform
        if not self._voice_enabled:
            return ResponseFormDecision(MsgType.TEXT, None, "voice_disabled_text")
        if not reply_text:
            return ResponseFormDecision(MsgType.TEXT, None, "empty_reply_text")
        if _TEXT_ONLY_REQUEST_RE.search(input_text):
            return ResponseFormDecision(MsgType.TEXT, None, "user_requested_text")
        if not self._voice_candidate_text_ok(reply_text_raw):
            return ResponseFormDecision(MsgType.TEXT, None, "voice_unsuitable_text_shape")

        explicit_voice_request = bool(_EXPLICIT_VOICE_REQUEST_RE.search(input_text))
        if input_type == MsgType.VOICE:
            return self._voice_or_text_rate_limited(
                user_key,
                now_ts,
                "voice_input_echo",
                enforce_limit=False,
            )
        if explicit_voice_request:
            return self._voice_or_text_rate_limited(
                user_key,
                now_ts,
                "explicit_voice_request",
                enforce_limit=False,
            )

        if self._prob_scale <= 0:
            return ResponseFormDecision(MsgType.TEXT, None, "voice_probability_zero")

        emotion = str(emotion_tag or "").strip().upper()
        bond = str(bond_stage or "").strip().upper() or "FRIEND"
        has_warm_signal = any(keyword in reply_text or keyword in input_text for keyword in _WARMTH_KEYWORDS)
        has_voice_emotion = emotion in _VOICE_EMOTION_TAGS
        has_presence_signal = bool(_SHORT_INTERJECTION_RE.match(reply_text) or _EMBODIED_PRESENCE_RE.search(reply_text))
        close_enough = bond in _CLOSE_VOICE_BONDS or (_VOICE_PROBABILITY_BY_BOND.get(bond, 0.0) * self._prob_scale) >= 0.20

        if has_presence_signal:
            reason = "night_embodied_presence_voice" if self._is_quiet_hours(now_ts) else "embodied_presence_voice"
            return self._voice_or_text_rate_limited(user_key, now_ts, reason)
        if has_warm_signal:
            return self._voice_or_text_rate_limited(user_key, now_ts, "warmth_voice_supplement")
        if has_voice_emotion:
            reason = "close_affective_voice_supplement" if close_enough else "affective_voice_supplement"
            return self._voice_or_text_rate_limited(user_key, now_ts, reason)
        if len(reply_text) <= min(72, self._max_voice_reply_chars):
            return self._voice_or_text_rate_limited(user_key, now_ts, "conversational_presence_voice")

        return ResponseFormDecision(MsgType.TEXT, None, "smart_text_only")

    def _voice_candidate_text_ok(self, reply_text: str) -> bool:
        text = str(reply_text or "").strip()
        if len(text) < self._min_voice_reply_chars:
            return False
        if len(text) > self._max_voice_reply_chars:
            return False
        line_count = text.count("\n") + 1
        if line_count >= 3:
            return False
        lowered = text.lower()
        if any(marker in lowered for marker in _STRUCTURED_REPLY_MARKERS):
            return False
        return True

    def _voice_or_text_rate_limited(
        self,
        user_id: str,
        now_ts: int,
        reason: str,
        *,
        enforce_limit: bool = True,
    ) -> ResponseFormDecision:
        if enforce_limit and not self._can_send_voice(user_id, now_ts):
            return ResponseFormDecision(MsgType.TEXT, None, "voice_rate_limited_text")
        self._record_voice(user_id, now_ts)
        return ResponseFormDecision(MsgType.TEXT, MsgType.VOICE, reason)

    def _can_send_voice(self, user_id: str, now_ts: int) -> bool:
        today = _today_str(now_ts)
        stats = self._voice_stats.get(user_id)
        if stats is None:
            return True
        if stats.get("date") != today:
            return True
        if stats.get("count", 0) >= self._daily_voice_limit:
            return False
        last_ts = stats.get("last_voice_ts_ms", 0)
        if last_ts > 0 and (now_ts - last_ts) < self._min_voice_interval_ms:
            return False
        return True

    def _record_voice(self, user_id: str, now_ts: int) -> None:
        today = _today_str(now_ts)
        stats = self._voice_stats.get(user_id)
        if stats is None or stats.get("date") != today:
            self._voice_stats[user_id] = {
                "date": today,
                "count": 1,
                "last_voice_ts_ms": now_ts,
            }
            return
        stats["count"] = stats.get("count", 0) + 1
        stats["last_voice_ts_ms"] = now_ts

    @staticmethod
    def _is_late_night(now_ts: int) -> bool:
        try:
            dt = datetime.datetime.fromtimestamp(now_ts / 1000)
            return dt.hour >= 23 or dt.hour < 2
        except Exception:
            return False

    @staticmethod
    def _is_quiet_hours(now_ts: int) -> bool:
        try:
            dt = datetime.datetime.fromtimestamp(now_ts / 1000)
            return dt.hour >= _QUIET_HOUR_START or dt.hour < _QUIET_HOUR_END
        except Exception:
            return False

    def get_voice_stats(self, user_id: str) -> dict:
        return dict(self._voice_stats.get(str(user_id).strip(), {}))


ReplyFormDecider = ResponseFormDecider

_decider: ResponseFormDecider | None = None


def get_response_form_decider() -> ResponseFormDecider:
    global _decider
    if _decider is None:
        _decider = ResponseFormDecider()
    return _decider


def get_reply_form_decider() -> ResponseFormDecider:
    return get_response_form_decider()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _today_str(now_ts: int) -> str:
    try:
        dt = datetime.datetime.fromtimestamp(now_ts / 1000)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def _compact_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_msg_type(value: MsgType | str | None) -> MsgType:
    if isinstance(value, MsgType):
        return value
    raw = str(value or "").strip().lower()
    if raw in _VOICE_INPUT_TYPES:
        return MsgType.VOICE
    try:
        return MsgType(raw or "text")
    except ValueError:
        return MsgType.TEXT


def _read_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(int(maximum), int(value)))


__all__ = [
    "MsgType",
    "ReplyFormDecision",
    "ReplyFormDecider",
    "ResponseFormDecision",
    "ResponseFormDecider",
    "get_reply_form_decider",
    "get_response_form_decider",
]
