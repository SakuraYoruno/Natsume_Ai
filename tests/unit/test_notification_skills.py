from __future__ import annotations

import builtins
import json
from pathlib import Path

import host_brain.app.dependencies as dependencies
from host_brain.app.executive_runtime_core import _execute_skill
from host_brain.skills.impl import notification_executor


def test_windows_notification_quotes_powershell_strings(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):  # noqa: ANN001
        calls.append(list(cmd))
        return object()

    monkeypatch.setattr(notification_executor.subprocess, "run", fake_run)

    notification_executor._send_desktop_notification_windows(
        "Natsume's note",
        "It's time",
        5000,
    )

    script = calls[0][-1]
    assert "Natsume''s note" in script
    assert "It''s time" in script


def test_macos_notification_quotes_osascript_strings(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):  # noqa: ANN001
        calls.append(list(cmd))
        return object()

    monkeypatch.setattr(notification_executor.subprocess, "run", fake_run)

    notification_executor._send_desktop_notification_macos(
        'Quote "title"',
        'Body with "quotes"',
    )

    script = calls[0][-1]
    assert '\\"title\\"' in script
    assert '\\"quotes\\"' in script


def test_schedule_reminder_falls_back_to_queue_file(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "reminder_queue.json"
    monkeypatch.setattr(notification_executor, "_REMINDER_QUEUE_FILE", queue_path)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # noqa: ANN001
        if str(name).startswith("apscheduler"):
            raise ImportError("apscheduler unavailable in unit test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = notification_executor.execute_notification_skill(
        {
            "action": "schedule_reminder",
            "title": "验收提醒",
            "body": "确认提醒队列落盘",
            "trigger_at": "2030-01-01T09:00:00+08:00",
            "repeat": "once",
            "notify_type": "desktop",
            "reminder_id": "unit-reminder-001",
        }
    )

    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    assert result["status"] == notification_executor.STATUS_EXECUTED
    assert result["reminder_id"] == "unit-reminder-001"
    assert queue[0]["title"] == "验收提醒"
    assert queue[0]["notify_type"] == "desktop"


def test_notify_schedule_reminder_runs_through_executive_runtime(tmp_path, monkeypatch) -> None:
    queue_path = tmp_path / "runtime_reminder_queue.json"
    monkeypatch.setattr(notification_executor, "_REMINDER_QUEUE_FILE", queue_path)
    monkeypatch.setenv("HOST_SKILL_HUB_ENABLED", "0")
    monkeypatch.setenv("HOST_SKILL_CUSTOM_PROVIDERS_ENABLED", "0")
    monkeypatch.setenv("HOST_SKILL_MCP_BRIDGE_ENABLED", "0")
    dependencies._skill_registry = None

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # noqa: ANN001
        if str(name).startswith("apscheduler"):
            raise ImportError("apscheduler unavailable in unit test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        reminder_args = {
            "title": "运行时提醒验收",
            "body": "通过执行主链落盘",
            "trigger_at": "2030-01-01T10:00:00+08:00",
            "repeat": "once",
            "notify_type": "desktop",
            "reminder_id": "runtime-reminder-001",
        }
        outcome = _execute_skill(
            request_body={
                "args": dict(reminder_args)
            },
            request_id="unit-notify-runtime",
            session_id="unit-notify-session",
            person_id="unit-notify-person",
            intent_id="intent::unit-notify-runtime",
            skill_id="notify_schedule_reminder",
            task_text="schedule reminder probe",
            user_message="schedule reminder probe",
            governor_result={"decision": "approve", "tokens": {}},
            intent_draft={"route": "skill", "target_id": "notify_schedule_reminder", "args": dict(reminder_args)},
            emotion_context={"mood_state": "NEUTRAL", "emotion_tag": "NEUTRAL"},
            render_target_lang="zh",
        )
    finally:
        dependencies._skill_registry = None

    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    raw = outcome.payload.get("result") if isinstance(outcome.payload, dict) else {}
    assert outcome.success is True
    assert raw["status"] == notification_executor.STATUS_EXECUTED
    assert queue[0]["reminder_id"] == "runtime-reminder-001"
