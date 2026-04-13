from __future__ import annotations

import asyncio

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.executive_runtime_core import _execute_skill, _execute_skill_dag
from host_brain.app.skill_dag_planner import build_skill_dag_plan
from host_brain.skills.builtin_registry import build_builtin_skill_registry


def test_execute_skill_builtin_writes_skill_exec_audit(monkeypatch) -> None:
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    registry = build_builtin_skill_registry()

    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)
    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: registry)

    outcome = _execute_skill(
        request_body={},
        request_id="req_exec_skill_smoke_001",
        session_id="sess_exec_skill_smoke_001",
        person_id="person_exec_skill_smoke_001",
        intent_id="intent_exec_skill_smoke_001",
        skill_id="session_summary",
        task_text="请总结当前会话",
        user_message="请总结当前会话",
        governor_result={
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": "approval::exec_skill_smoke_001"},
        },
        intent_draft={
            "args": {
                "user_message": "今天先验证技能执行链路",
                "session_id": "sess_exec_skill_smoke_001",
            }
        },
        emotion_context={"mood_state": "CALM", "emotion_tag": "FOCUSED"},
        recall_slot_text={"episodic": "none"},
        render_target_lang="zh-CN",
    )

    assert outcome.success is True
    assert outcome.target_id == "session_summary"
    assert outcome.result_text == "session_summary"
    assert outcome.payload["approval_token_ref"] == "approval::exec_skill_smoke_001"

    trace = ledger.trace_request("req_exec_skill_smoke_001")
    assert len(trace["skill_exec_log"]) == 1
    assert trace["skill_exec_log"][0]["skill_name"] == "session_summary"
    assert trace["skill_exec_log"][0]["result_status"] == "SUCCESS"
    assert trace["skill_exec_log"][0]["approval_token_ref"] == "approval::exec_skill_smoke_001"


def test_execute_skill_dag_parallel_builtin_skills_write_audit(monkeypatch) -> None:
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    registry = build_builtin_skill_registry()

    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)
    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: registry)

    plan = build_skill_dag_plan(
        plan_id="plan::exec_skill_smoke_001",
        steps=[
            {
                "step_index": 1,
                "route": "skill",
                "target_id": "session_summary",
                "description": "总结会话",
                "task_description": "总结当前会话信息",
                "route_reason": "manual_smoke:session_summary",
                "allow_parallel": True,
                "skill_family": "social",
                "risk_level": 1,
            },
            {
                "step_index": 2,
                "route": "skill",
                "target_id": "emotion_query",
                "description": "读取情绪快照",
                "task_description": "读取当前情绪状态",
                "route_reason": "manual_smoke:emotion_query",
                "allow_parallel": True,
                "skill_family": "social",
                "risk_level": 1,
            },
        ],
        parallel_window=4,
    )

    async def _run() -> tuple[object, dict[str, object]]:
        return await _execute_skill_dag(
            plan=plan,
            request_body={},
            request_id="req_exec_plan_smoke_001",
            session_id="sess_exec_plan_smoke_001",
            person_id="person_exec_plan_smoke_001",
            intent_id="intent_exec_plan_smoke_001",
            governor_result={
                "decision": "APPROVE",
                "tokens": {"approval_token_ref": "approval::exec_plan_smoke_001"},
            },
            intent_draft={},
            emotion_context={
                "mood_state": "CALM",
                "emotion_tag": "FOCUSED",
                "emotion_intensity": 0.88,
            },
            recall_slot_text={"episodic": "none"},
            render_target_lang="zh-CN",
        )

    outcome, meta = asyncio.run(_run())

    assert outcome.success is True
    assert meta["parallel_enabled"] is True
    assert "session_summary" in outcome.result_text
    assert "emotion_query" in outcome.result_text

    trace = ledger.trace_request("req_exec_plan_smoke_001")
    assert len(trace["skill_exec_log"]) == 2
    assert [row["skill_name"] for row in trace["skill_exec_log"]] == [
        "session_summary",
        "emotion_query",
    ]
    assert all(row["result_status"] == "SUCCESS" for row in trace["skill_exec_log"])
