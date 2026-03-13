"""通知/提醒 Skill 包（P2 优先级）。

提供桌面通知、TTS 语音朗读和定时提醒能力，支撑 PRD S3 场景（桌面留言 + 主动提醒）。
与系统现有 TTS 服务、Scheduler 服务和 Edge 端深度集成。

风险分级说明：
  risk_level=2  只读/展示操作（notify_desktop）
  risk_level=3  写操作（notify_tts_speak / notify_schedule_reminder）

所有 handler 均为轻量归一化层，返回标准化 payload，
由上层运行时（NotificationAgent / EdgeBridge）负责实际推送。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill

# 支持的通知优先级
_NOTIFY_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})

# 支持的 TTS 语速范围
_TTS_RATE_MIN = 0.5
_TTS_RATE_MAX = 2.0

# 支持的提醒重复模式
_REMINDER_REPEAT_MODES = frozenset({"once", "daily", "weekly", "workday"})


class NotificationSkillProvider:
    """通知/提醒 Skill 集合。

    所有方法通过 @skill 装饰器注册元数据，
    由 build_notification_skill_registry() 统一扫描注册。
    """

    @skill(
        skill_name="notify_desktop",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="发送桌面系统通知气泡（Windows Toast / macOS Notification / Linux libnotify）。",
    )
    def notify_desktop(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """发送桌面系统通知。

        Args:
            title (str): 通知标题（必填）
            body (str): 通知正文内容（必填）
            icon (str): 通知图标路径或内置图标名（可选，默认使用 Natsume 头像）
            priority (str): 通知优先级，low / normal / high / urgent（默认 normal）
            duration_ms (int): 通知显示时长毫秒数（默认 5000，0 表示持久显示）
            sound (bool): 是否播放通知音效（默认 True）
            action_label (str): 可选操作按钮文字（如"查看"，可选）
        """
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
            "status": "OK",
            "tool": "notification",
            "action": "desktop",
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

    @skill(
        skill_name="notify_tts_speak",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="触发 Edge TTS 朗读指定文本（与系统 TTS 服务集成）。",
    )
    def notify_tts_speak(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """触发 TTS 语音朗读。

        Args:
            text (str): 要朗读的文本内容（必填）
            voice (str): 语音角色名（可选，默认使用系统配置的 Natsume 声线）
            rate (float): 语速倍率，0.5~2.0（默认 1.0）
            volume (float): 音量，0.0~1.0（默认 0.8）
            pitch (float): 音调，0.5~2.0（默认 1.0）
            interrupt (bool): 是否打断当前正在播放的语音（默认 False）
            emotion_tag (str): 情感标签，影响语音风格（可选，如 HAPPY / SAD / CALM）
        """
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

        # 情感标签：优先从 args 取，其次从 ctx 的 emotion_tag 取
        emotion_tag = str(
            (args or {}).get("emotion_tag")
            or (ctx or {}).get("emotion_tag")
            or "NEUTRAL"
        ).strip().upper()

        return {
            "status": "OK",
            "tool": "notification",
            "action": "tts_speak",
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

    @skill(
        skill_name="notify_schedule_reminder",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="设置定时提醒（写入 Scheduler），到时触发桌面通知或 TTS 朗读。",
    )
    def notify_schedule_reminder(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """设置定时提醒。

        Args:
            title (str): 提醒标题（必填）
            body (str): 提醒内容（必填）
            trigger_at (str): 触发时间，ISO 8601 格式（如 2026-03-13T15:00:00，必填）
            repeat (str): 重复模式，once / daily / weekly / workday（默认 once）
            notify_type (str): 通知方式，desktop / tts / both（默认 both）
            priority (str): 优先级，low / normal / high / urgent（默认 normal）
            reminder_id (str): 自定义提醒 ID（可选，用于后续取消/更新）
        """
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
            "status": "OK",
            "tool": "notification",
            "action": "schedule_reminder",
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


def build_notification_skill_registry(
    provider: NotificationSkillProvider | None = None,
) -> SkillRegistry:
    """构建包含通知/提醒 Skills 的 SkillRegistry。

    通知 Skill 列表：
    - notify_desktop：发送桌面系统通知（risk=2，展示操作）
    - notify_tts_speak：触发 TTS 语音朗读（risk=3，与 TTS 服务集成）
    - notify_schedule_reminder：设置定时提醒（risk=3，写入 Scheduler）

    Returns:
        已注册 3 个通知 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or NotificationSkillProvider())
    return registry
