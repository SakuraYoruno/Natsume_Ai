"""通知/提醒 Skill 执行层。

将 notification_registry.py 的归一化 payload 转化为真正的执行：
  - notify_desktop：调用系统原生通知（plyer / win10toast / notify-send）
  - notify_tts_speak：通过 edge_rpc_client.post_speak() 驱动 Edge TTS
  - notify_schedule_reminder：生成 secret-free Governed Scheduler proposal

降级策略：
  - plyer 不可用时，Windows 降级为 PowerShell toast，Linux 降级为 notify-send
  - Edge TTS 不可达时，返回 DEGRADED 状态（不抛异常）
  - 提醒必须由 Secure Ingress + Governed Scheduler application 提交
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import Any

from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

NOTIFY_EXEC_ERROR = "NOTIFY_EXEC_ERROR"
NOTIFY_TTS_FAILED = "NOTIFY_TTS_FAILED"
NOTIFY_SCHEDULER_FAILED = "NOTIFY_SCHEDULER_FAILED"
NOTIFY_GOVERNED_SCHEDULER_REQUIRED = "NOTIFY_GOVERNED_SCHEDULER_REQUIRED"


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
    title_ps = _ps_single_quoted(title)
    body_ps = _ps_single_quoted(body)
    script = (
        f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        f"ContentType = WindowsRuntime] | Out-Null; "
        f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        f"[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        f"$template.SelectSingleNode('//text[@id=1]').InnerText = {title_ps}; "
        f"$template.SelectSingleNode('//text[@id=2]').InnerText = {body_ps}; "
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
    script = f"display notification {_osascript_quoted(body)} with title {_osascript_quoted(title)}"
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def _ps_single_quoted(value: Any) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _osascript_quoted(value: Any) -> str:
    escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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

        # 使用 get_running_loop()（Python 3.7+），避免已废弃的 get_event_loop()
        # （Python 3.12+ 无运行中事件循环时 get_event_loop() 直接报错）
        # 在已有事件循环中（如 FastAPI）：使用 run_coroutine_threadsafe + 线程池，
        # 避免在事件循环线程上调用 future.result() 造成死锁
        try:
            loop = asyncio.get_running_loop()
            # 有运行中的事件循环：在独立线程中执行，避免阻塞事件循环线程
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _call())
                tts_result = future.result(timeout=30)
        except RuntimeError:
            # 无运行中的事件循环（同步上下文）：直接 asyncio.run()
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
    """Return a proposal; this skill layer has no scheduling authority."""
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

    target_id = {
        "desktop": "notify_desktop",
        "tts": "notify_tts_speak",
        "both": "notify_schedule_reminder",
    }.get(str(notify_type), "notify_schedule_reminder")
    proposal = {
        "schema_version": "natsume.scheduler.proposal.v1",
        "task_id": f"reminder::{reminder_id}",
        "trigger_mode": "once" if repeat == "once" else "calendar",
        "trigger_config": {"trigger_at": trigger_at, "repeat": repeat},
        "intent_template": {
            "route": "skill",
            "target_id": target_id,
            "args": {
                "title": title,
                "body": body,
                "notify_type": notify_type,
            },
        },
        "priority": str(priority).upper(),
        "worker_key": "scheduler_production",
    }
    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref"),
        "reminder_id": reminder_id,
        "trigger_at": trigger_at,
        "repeat": repeat,
        "reason_code": NOTIFY_GOVERNED_SCHEDULER_REQUIRED,
        "detail": "submit proposal through Secure Ingress and Governed Scheduler",
        "scheduler_proposal": proposal,
        "request_id": request_id,
        "ts_ms": ts,
    }


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
