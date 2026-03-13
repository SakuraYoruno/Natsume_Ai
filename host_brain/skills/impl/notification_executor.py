"""通知/提醒 Skill 执行层。

将 notification_registry.py 的归一化 payload 转化为真正的执行：
  - notify_desktop：调用系统原生通知（plyer / win10toast / notify-send）
  - notify_tts_speak：通过 edge_rpc_client.post_speak() 驱动 Edge TTS
  - notify_schedule_reminder：写入系统 Scheduler（APScheduler / 文件队列）

降级策略：
  - plyer 不可用时，Windows 降级为 PowerShell toast，Linux 降级为 notify-send
  - Edge TTS 不可达时，返回 DEGRADED 状态（不抛异常）
  - Scheduler 不可用时，写入本地 JSON 队列文件
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

NOTIFY_EXEC_ERROR = "NOTIFY_EXEC_ERROR"
NOTIFY_TTS_FAILED = "NOTIFY_TTS_FAILED"
NOTIFY_SCHEDULER_FAILED = "NOTIFY_SCHEDULER_FAILED"

# 本地提醒队列文件（Scheduler 不可用时的降级存储）
_REMINDER_QUEUE_FILE = Path(os.environ.get("NATSUME_REMINDER_QUEUE", "data/reminder_queue.json"))


def _reject(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


# ---------------------------------------------------------------------------
# notify_desktop
# ---------------------------------------------------------------------------

def _send_desktop_notification_windows(title: str, body: str, duration_ms: int) -> None:
    """Windows 桌面通知（PowerShell Toast）。"""
    duration_s = max(1, duration_ms // 1000)
    script = (
        f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        f"ContentType = WindowsRuntime] | Out-Null; "
        f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        f"[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        f"$template.SelectSingleNode('//text[@id=1]').InnerText = '{title}'; "
        f"$template.SelectSingleNode('//text[@id=2]').InnerText = '{body}'; "
        f"$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Natsume').Show($toast)"
    )
    subprocess.run(
        ["powershell", "-WindowStyle", "Hidden", "-Command", script],
        capture_output=True,
        timeout=10,
    )


def _send_desktop_notification_linux(title: str, body: str, duration_ms: int) -> None:
    """Linux 桌面通知（notify-send）。"""
    subprocess.run(
        ["notify-send", "-t", str(duration_ms), title, body],
        capture_output=True,
        timeout=10,
    )


def _send_desktop_notification_macos(title: str, body: str) -> None:
    """macOS 桌面通知（osascript）。"""
    script = f'display notification "{body}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def execute_notify_desktop(payload: dict[str, Any]) -> dict[str, Any]:
    """执行桌面系统通知。"""
    ts = now_ms()
    event_id = new_event_id()
    title = payload.get("title") or "Natsume"
    body = payload.get("body") or ""
    duration_ms = int(payload.get("duration_ms") or 5000)
    request_id = payload.get("session_id") or payload.get("request_id")

    if not body and not title:
        return _reject(event_id=event_id, reason_code="NOTIFY_CONTENT_MISSING",
                       detail="title or body is required", ts_ms=ts, request_id=request_id)

    try:
        # 优先尝试 plyer（跨平台）
        try:
            from plyer import notification as plyer_notify  # type: ignore[import-not-found]
            plyer_notify.notify(
                title=title,
                message=body,
                timeout=max(1, duration_ms // 1000),
            )
        except ImportError:
            # plyer 不可用，按平台降级
            if sys.platform == "win32":
                _send_desktop_notification_windows(title, body, duration_ms)
            elif sys.platform == "darwin":
                _send_desktop_notification_macos(title, body)
            else:
                _send_desktop_notification_linux(title, body, duration_ms)

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "title": title,
            "body": body,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.warning("notify_desktop failed (degraded): %s", exc)
        # 通知失败不阻断流程，返回 DEGRADED
        return {
            "status": STATUS_DEGRADED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "reason_code": NOTIFY_EXEC_ERROR,
            "detail": str(exc),
            "request_id": request_id,
            "ts_ms": ts,
        }


# ---------------------------------------------------------------------------
# notify_tts_speak
# ---------------------------------------------------------------------------

def execute_notify_tts_speak(payload: dict[str, Any]) -> dict[str, Any]:
    """通过 Edge RPC 驱动 TTS 语音朗读。"""
    ts = now_ms()
    event_id = new_event_id()
    text = payload.get("text") or ""
    voice = payload.get("voice") or None
    rate = float(payload.get("rate") or 1.0)
    pitch = float(payload.get("pitch") or 1.0)
    emotion_tag = payload.get("emotion_tag") or "NEUTRAL"
    request_id = payload.get("request_id")

    if not text:
        return _reject(event_id=event_id, reason_code="NOTIFY_TTS_TEXT_MISSING",
                       detail="text is required", ts_ms=ts, request_id=request_id)

    try:
        from host_brain.channels.edge_rpc_client import post_speak as _post_speak  # noqa: PLC0415

        # post_speak 是 async 函数，需要在事件循环中运行
        async def _call() -> dict[str, Any]:
            result = await _post_speak(
                text=text,
                request_id=request_id,
                voice_id=voice,
                speed=rate,
                pitch=pitch - 1.0,  # edge_rpc_client pitch 是偏移量（-1.0~1.0）
                emotion_tag=emotion_tag,
            )
            return result.as_dict()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在已有事件循环中（如 FastAPI），使用 run_coroutine_threadsafe
                import concurrent.futures
                future = asyncio.run_coroutine_threadsafe(_call(), loop)
                tts_result = future.result(timeout=30)
            else:
                tts_result = loop.run_until_complete(_call())
        except RuntimeError:
            tts_result = asyncio.run(_call())

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "text_len": len(text),
            "emotion_tag": emotion_tag,
            "tts_result": tts_result,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.warning("notify_tts_speak failed (degraded): %s", exc)
        return {
            "status": STATUS_DEGRADED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "reason_code": NOTIFY_TTS_FAILED,
            "detail": str(exc),
            "request_id": request_id,
            "ts_ms": ts,
        }


# ---------------------------------------------------------------------------
# notify_schedule_reminder
# ---------------------------------------------------------------------------

def execute_notify_schedule_reminder(payload: dict[str, Any]) -> dict[str, Any]:
    """设置定时提醒（写入 APScheduler 或本地 JSON 队列）。"""
    ts = now_ms()
    event_id = new_event_id()
    title = payload.get("title") or ""
    body = payload.get("body") or ""
    trigger_at = payload.get("trigger_at") or ""
    repeat = payload.get("repeat") or "once"
    notify_type = payload.get("notify_type") or "both"
    priority = payload.get("priority") or "normal"
    reminder_id = payload.get("reminder_id") or event_id
    request_id = payload.get("request_id") or payload.get("session_id")

    if not title or not trigger_at:
        return _reject(event_id=event_id, reason_code="NOTIFY_REMINDER_MISSING_FIELDS",
                       detail="title and trigger_at are required", ts_ms=ts, request_id=request_id)

    reminder_entry = {
        "reminder_id": reminder_id,
        "event_id": event_id,
        "title": title,
        "body": body,
        "trigger_at": trigger_at,
        "repeat": repeat,
        "notify_type": notify_type,
        "priority": priority,
        "created_ts_ms": ts,
        "request_id": request_id,
    }

    # 优先尝试 APScheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-not-found]
        from apscheduler.triggers.date import DateTrigger  # type: ignore[import-not-found]
        import dateutil.parser  # type: ignore[import-not-found]

        scheduler = _get_or_create_scheduler()
        trigger_dt = dateutil.parser.parse(trigger_at)

        def _fire_reminder() -> None:
            _dispatch_reminder(reminder_entry)

        scheduler.add_job(
            _fire_reminder,
            trigger=DateTrigger(run_date=trigger_dt),
            id=reminder_id,
            replace_existing=True,
        )
        logger.info("Reminder scheduled via APScheduler: %s at %s", reminder_id, trigger_at)

    except ImportError:
        # APScheduler 不可用，写入本地 JSON 队列
        _write_reminder_to_queue(reminder_entry)
        logger.info("Reminder queued to file (APScheduler unavailable): %s", reminder_id)
    except Exception as exc:
        logger.warning("Scheduler failed, falling back to file queue: %s", exc)
        _write_reminder_to_queue(reminder_entry)

    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref"),
        "reminder_id": reminder_id,
        "trigger_at": trigger_at,
        "repeat": repeat,
        "request_id": request_id,
        "ts_ms": ts,
    }


_scheduler_instance = None


def _get_or_create_scheduler():
    """获取或创建全局 APScheduler 实例。"""
    global _scheduler_instance
    if _scheduler_instance is None:
        from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-not-found]
        _scheduler_instance = BackgroundScheduler()
        _scheduler_instance.start()
    return _scheduler_instance


def _write_reminder_to_queue(entry: dict[str, Any]) -> None:
    """将提醒写入本地 JSON 队列文件（降级存储）。"""
    _REMINDER_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    queue: list[dict[str, Any]] = []
    if _REMINDER_QUEUE_FILE.exists():
        try:
            queue = json.loads(_REMINDER_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            queue = []
    queue.append(entry)
    _REMINDER_QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def _dispatch_reminder(entry: dict[str, Any]) -> None:
    """触发提醒（到时执行）。"""
    notify_type = entry.get("notify_type") or "both"
    title = entry.get("title") or "提醒"
    body = entry.get("body") or ""

    if notify_type in ("desktop", "both"):
        execute_notify_desktop({
            "title": title,
            "body": body,
            "duration_ms": 8000,
            "manifest_ref": None,
        })

    if notify_type in ("tts", "both"):
        text = f"{title}。{body}" if body else title
        execute_notify_tts_speak({
            "text": text,
            "emotion_tag": "NEUTRAL",
            "rate": 1.0,
            "pitch": 1.0,
            "manifest_ref": None,
        })


# 统一分发入口
_ACTION_MAP = {
    "desktop": execute_notify_desktop,
    "tts_speak": execute_notify_tts_speak,
    "schedule_reminder": execute_notify_schedule_reminder,
}


def execute_notification_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """根据 payload.action 分发到对应的通知执行函数。"""
    action = str(payload.get("action") or "").strip().lower()
    handler = _ACTION_MAP.get(action)
    if handler is None:
        return {
            "status": STATUS_REJECTED,
            "event_id": new_event_id(),
            "reason_code": "NOTIFY_UNKNOWN_ACTION",
            "detail": f"unknown notification action: {action!r}",
            "manifest_ref": None,
            "ts_ms": now_ms(),
        }
    return handler(payload)
