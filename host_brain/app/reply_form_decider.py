"""reply_form_decider.py — Normalized response-form decision for gateway transport.

The host chain decides *what* form to send; transport only executes that plan.
Planned multimodal forms therefore take precedence over heuristic voice extras.
"""

from __future__ import annotations

import datetime
import os
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
    "STRANGER": 0.0,
    "ACQUAINTANCE": 0.0,
    "FRIEND": 0.10,
    "CLOSE_FRIEND": 0.20,
    "CONFIDANT": 0.30,
    "SOULMATE": 0.30,
}

DEFAULT_DAILY_VOICE_LIMIT = 3
DEFAULT_MIN_VOICE_INTERVAL_MS = 10 * 60 * 1000
_LATE_NIGHT_PROB_SCALE = 0.5


class ResponseFormDecider:
    """Canonical host-side response-form decider.

    Planned primary/secondary forms from the reply plan are authoritative.
    When the plan does not specify a form, we send text. Voice/media selection
    belongs to ReplyModalityRouter and the reply plan, not transport heuristics.
    """

    def __init__(
        self,
        *,
        voice_enabled: bool | None = None,
        probability_scale: float | None = None,
        daily_voice_limit: int = DEFAULT_DAILY_VOICE_LIMIT,
        min_voice_interval_ms: int = DEFAULT_MIN_VOICE_INTERVAL_MS,
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
        self._voice_stats: dict[str, dict] = {}

    def decide(
        self,
        *,
        user_id: str = "default",
        user_msg_type: MsgType = MsgType.TEXT,
        narrative_zh: str,
        emotion_tag: str = "NEUTRAL",
        bond_stage: str = "STRANGER",
        planned_primary: MsgType | None = None,
        planned_secondary: MsgType | None = None,
        planned_reason: str = "",
    ) -> ResponseFormDecision:
        if planned_primary is not None:
            return ResponseFormDecision(
                primary=planned_primary,
                secondary=planned_secondary,
                reason=str(planned_reason or "planned_form").strip() or "planned_form",
            )

        _ = user_id, user_msg_type, narrative_zh, emotion_tag, bond_stage
        return ResponseFormDecision(MsgType.TEXT, None, "unplanned_default_text")

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


__all__ = [
    "MsgType",
    "ReplyFormDecision",
    "ReplyFormDecider",
    "ResponseFormDecision",
    "ResponseFormDecider",
    "get_reply_form_decider",
    "get_response_form_decider",
]
