from __future__ import annotations

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


def test_schedule_reminder_returns_governed_proposal_and_fails_closed() -> None:
    result = notification_executor.execute_notification_skill(
        {
            "action": "schedule_reminder",
            "title": "验收提醒",
            "body": "确认提醒走治理主链",
            "trigger_at": "2030-01-01T09:00:00+08:00",
            "repeat": "once",
            "notify_type": "desktop",
            "reminder_id": "unit-reminder-001",
        }
    )

    assert result["status"] == notification_executor.STATUS_REJECTED
    assert result["reason_code"] == notification_executor.NOTIFY_GOVERNED_SCHEDULER_REQUIRED
    assert result["reminder_id"] == "unit-reminder-001"
    proposal = result["scheduler_proposal"]
    assert proposal["schema_version"] == "natsume.scheduler.proposal.v1"
    assert proposal["intent_template"]["args"]["title"] == "验收提醒"
    assert "authority" not in repr(proposal).lower()
