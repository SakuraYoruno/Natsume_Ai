from __future__ import annotations

from host_brain.app.runtime_task_registry import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RECOVERING,
    STATUS_RUNNING,
    RuntimeTaskRegistry,
)


def test_runtime_task_registry_tracks_running_and_completed_tasks() -> None:
    registry = RuntimeTaskRegistry()
    task = registry.create_task(
        task_kind="skill",
        target_id="web_search",
        request_id="req-001",
        session_id="session-001",
        person_id="person-001",
        user_message="帮我搜索 OpenAI",
        detail="running web_search",
    )

    assert task.status == STATUS_RUNNING
    completed = registry.mark_completed(task.task_id, detail="web_search completed", result_text="done")

    assert completed is not None
    assert completed.status == STATUS_COMPLETED
    summary = registry.summarize_for_chat(person_id="person-001", session_id="session-001")
    assert summary is not None
    assert summary["status"] == STATUS_COMPLETED
    assert summary["target_id"] == "web_search"


def test_runtime_task_registry_marks_recovery_and_failure_summary() -> None:
    registry = RuntimeTaskRegistry()
    task = registry.create_task(
        task_kind="skill",
        target_id="browser_navigate",
        request_id="req-002",
        session_id="session-002",
        person_id="person-001",
        user_message="打开网页",
        detail="running browser_navigate",
    )
    registry.mark_failed(task.task_id, detail="browser_navigate failed", error="BROWSER_UNAVAILABLE", result_code="BROWSER_UNAVAILABLE")
    recovering = registry.mark_recovering(
        task.task_id,
        detail="正在自动补救中。",
        recovery_plan={"agent_id": "intent_agent"},
    )
    registry.attach_recovery_result(
        task.task_id,
        recovery_status="DONE",
        recovery_result_text="建议先安装浏览器后端，再重试。",
    )

    assert recovering is not None
    assert recovering.status == STATUS_RECOVERING
    summary = registry.summarize_for_chat(person_id="person-001", session_id="session-002")
    assert summary is not None
    assert summary["status"] == STATUS_RECOVERING
    assert summary["recovery_status"] == "DONE"
    assert "浏览器后端" in summary["recovery_result_text"]


def test_runtime_task_registry_filters_by_person_and_excludes_request() -> None:
    registry = RuntimeTaskRegistry()
    first = registry.create_task(
        task_kind="skill",
        target_id="memory_store",
        request_id="req-a",
        session_id="session-a",
        person_id="person-a",
        user_message="记住这个",
    )
    second = registry.create_task(
        task_kind="skill",
        target_id="memory_store",
        request_id="req-b",
        session_id="session-b",
        person_id="person-b",
        user_message="记住那个",
    )
    registry.mark_failed(first.task_id, detail="failed", error="FAILED", result_code="FAILED")
    registry.mark_failed(second.task_id, detail="failed", error="FAILED", result_code="FAILED")

    summary = registry.summarize_for_chat(person_id="person-a", session_id="session-a", exclude_request_id="req-a")

    assert summary is None



def test_runtime_task_registry_summary_includes_child_tasks() -> None:
    registry = RuntimeTaskRegistry()
    parent = registry.create_task(
        task_kind="plan",
        target_id="plan_execution",
        request_id="req-parent",
        session_id="session-parent",
        person_id="person-parent",
        user_message="先A再B",
    )
    child = registry.create_task(
        task_kind="plan_step:skill",
        target_id="web_search",
        request_id="req-child",
        session_id="session-parent",
        person_id="person-parent",
        user_message="搜索",
        parent_task_id=parent.task_id,
        meta={"step_index": 1, "description": "搜索 OpenAI 官网"},
    )
    registry.mark_completed(child.task_id, detail="done", result_text="ok")
    summary = registry.summarize_for_chat(person_id="person-parent", session_id="session-parent")

    assert summary is not None
    assert len(summary["child_tasks"]) == 1
    assert summary["child_tasks"][0]["target_id"] == "web_search"
