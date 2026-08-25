"""通知 / 提醒 Skill 包。

声明式 Provider：新增通知类技能时，只需补一个 `DeclarativeSkillSpec`
和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec

_NOTIFY_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
_TTS_RATE_MIN = 0.5
_TTS_RATE_MAX = 2.0
_REMINDER_REPEAT_MODES = frozenset({"once", "daily", "weekly", "workday"})


def _normalize_notify_desktop(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    title = str((args or {}).get("title") or "").strip()
    body = str((args or {}).get("body") or "").strip()
    icon = str((args or {}).get("icon") or "").strip() or None
    priority = str((args or {}).get("priority") or "normal").strip().lower()
    if priority not in _NOTIFY_PRIORITIES:
        priority = "normal"
    try:
        duration_ms = max(0, int((args or {}).get("duration_ms") or 5000))
    except (TypeError, ValueError):
        duration_ms = 5000
    sound_raw = (args or {}).get("sound")
    sound = bool(sound_raw) if sound_raw is not None else True
    action_label = str((args or {}).get("action_label") or "").strip() or None

    return {
        "title": title,
        "body": body,
        "icon": icon,
        "priority": priority,
        "duration_ms": duration_ms,
        "sound": sound,
        "action_label": action_label,
        "manifest_ref": f"notify_desktop::{title[:40]}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
    }


def _normalize_notify_tts_speak(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    text = str((args or {}).get("text") or "").strip()
    voice = str((args or {}).get("voice") or "").strip() or None
    try:
        rate = max(_TTS_RATE_MIN, min(_TTS_RATE_MAX, float((args or {}).get("rate") or 1.0)))
    except (TypeError, ValueError):
        rate = 1.0
    try:
        volume = max(0.0, min(1.0, float((args or {}).get("volume") or 0.8)))
    except (TypeError, ValueError):
        volume = 0.8
    try:
        pitch = max(_TTS_RATE_MIN, min(_TTS_RATE_MAX, float((args or {}).get("pitch") or 1.0)))
    except (TypeError, ValueError):
        pitch = 1.0
    interrupt_raw = (args or {}).get("interrupt")
    interrupt = bool(interrupt_raw) if interrupt_raw is not None else False
    emotion_tag = str(
        (args or {}).get("emotion_tag")
        or (ctx or {}).get("emotion_tag")
        or "NEUTRAL"
    ).strip().upper()

    return {
        "text": text,
        "voice": voice,
        "rate": rate,
        "volume": volume,
        "pitch": pitch,
        "interrupt": interrupt,
        "emotion_tag": emotion_tag,
        "manifest_ref": f"notify_tts_speak::{text[:40]}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_notify_schedule_reminder(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    title = str((args or {}).get("title") or "").strip()
    body = str((args or {}).get("body") or "").strip()
    trigger_at = str((args or {}).get("trigger_at") or "").strip()
    repeat = str((args or {}).get("repeat") or "once").strip().lower()
    if repeat not in _REMINDER_REPEAT_MODES:
        repeat = "once"
    notify_type = str((args or {}).get("notify_type") or "both").strip().lower()
    if notify_type not in {"desktop", "tts", "both"}:
        notify_type = "both"
    priority = str((args or {}).get("priority") or "normal").strip().lower()
    if priority not in _NOTIFY_PRIORITIES:
        priority = "normal"
    reminder_id = str((args or {}).get("reminder_id") or "").strip() or None

    return {
        "title": title,
        "body": body,
        "trigger_at": trigger_at,
        "repeat": repeat,
        "notify_type": notify_type,
        "priority": priority,
        "reminder_id": reminder_id,
        "manifest_ref": f"notify_schedule_reminder::{title[:40]}::{trigger_at}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


class NotificationSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="notify_desktop",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="发送桌面系统通知气泡（Windows Toast / macOS Notification / Linux libnotify）。",
            tool="notification",
            action="desktop",
            normalize_args=_normalize_notify_desktop,
        ),
        DeclarativeSkillSpec(
            skill_name="notify_tts_speak",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="触发 Edge TTS 朗读指定文本（与系统 TTS 服务集成）。",
            tool="notification",
            action="tts_speak",
            normalize_args=_normalize_notify_tts_speak,
        ),
        DeclarativeSkillSpec(
            skill_name="notify_schedule_reminder",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="设置定时提醒（写入 Scheduler），到时触发桌面通知或 TTS 朗读。",
            tool="notification",
            action="schedule_reminder",
            normalize_args=_normalize_notify_schedule_reminder,
        ),
    )
