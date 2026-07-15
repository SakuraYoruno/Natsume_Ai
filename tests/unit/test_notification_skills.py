from __future__ import annotations

from pathlib import Path

import host_brain.composition.process_services as dependencies
from host_brain.app.process_service_wiring import configure_process_service_ports
from host_brain.app.runtime_port_wiring import build_executive_runtime_application_ports
from host_brain.core.ids import now_ms
from host_brain.executive_runtime.skill_execution import _execute_skill
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


def test_notify_schedule_reminder_fails_closed_through_executive_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from host_brain.audit.sqlite_ledger import SQLiteLedger

    monkeypatch.setenv("HOST_SKILL_MCP_BRIDGE_ENABLED", "0")
    configure_process_service_ports()
    container = dependencies.get_process_service_container()
    ledger = SQLiteLedger(str(tmp_path / "governor.sqlite3"), check_same_thread=False)
    for field_name in (
        "skill_registry",
        "token_service",
        "intent_engine",
        "tool_catalog",
        "tool_broker",
        "capability_runtime_composition",
    ):
        monkeypatch.setattr(container, field_name, None)
    monkeypatch.setattr(container, "ledger", ledger)

    try:
        reminder_args = {
            "title": "运行时提醒验收",
            "body": "通过执行主链落盘",
            "trigger_at": "2030-01-01T10:00:00+08:00",
            "repeat": "once",
            "notify_type": "desktop",
            "reminder_id": "runtime-reminder-001",
        }
        request_id = "unit-notify-runtime"
        session_id = "unit-notify-session"
        person_id = "unit-notify-person"
        intent_id = f"intent::{request_id}"
        timestamp = now_ms()
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": intent_id,
            "ts_ms": timestamp,
            "session_id": session_id,
            "seq": 1,
            "type": "skill_call",
            "action": "notify_schedule_reminder",
            "target": "host",
            "args": dict(reminder_args),
            "nonce": f"nonce::{request_id}",
            "person_id": person_id,
            "principal_id": person_id,
            "route": "skill",
            "target_id": "notify_schedule_reminder",
        }
        governor_result = dependencies.get_intent_engine().evaluate_intent(
            raw_intent=intent,
            request_id=request_id,
            current_ms=timestamp,
            governance_snapshot={"trust_level": 100, "energy_level": 100},
        )
        outcome = _execute_skill(
            ports=build_executive_runtime_application_ports(),
            request_body={"args": dict(reminder_args)},
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            skill_id="notify_schedule_reminder",
            task_text="schedule reminder probe",
            user_message="schedule reminder probe",
            governor_result=governor_result,
            intent_draft=intent,
            emotion_context={"mood_state": "NEUTRAL", "emotion_tag": "NEUTRAL"},
            render_target_lang="zh",
        )
    finally:
        dependencies.get_process_service_container().skill_registry = None
        dependencies.reset_capability_runtime_composition()
        ledger.close()

    raw = outcome.payload.get("result") if isinstance(outcome.payload, dict) else {}
    assert outcome.success is False
    assert raw["status"] == notification_executor.STATUS_REJECTED
    assert raw["reason_code"] == notification_executor.NOTIFY_GOVERNED_SCHEDULER_REQUIRED
    assert raw["scheduler_proposal"]["task_id"] == "reminder::runtime-reminder-001"
