from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.executive_runtime_core import _execute_skill, _execute_skill_dag
from host_brain.app.skill_dag_planner import build_skill_dag_plan
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.memory_registry import build_memory_skill_registry


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

    planned_steps = [
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
    ]
    plan = build_skill_dag_plan(
        plan_id="plan::exec_skill_smoke_001",
        steps=planned_steps,
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
            intent_draft={"planned_steps": planned_steps},
            emotion_context={
                "mood_state": "CALM",
                "emotion_tag": "FOCUSED",
                "emotion_intensity": 0.88,
            },
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


def test_execute_skill_memory_store_injects_runtime_memory_dependencies(monkeypatch) -> None:
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    registry = build_memory_skill_registry()
    writes: list[dict[str, object]] = []

    class _StoreStub:
        def write_memory(
            self,
            *,
            text: str,
            evidence_refs: list[str],
            request_id: str | None = None,
            intent_id: str | None = None,
            emotion_tag: str = "NEUTRAL",
            **_: object,
        ) -> SimpleNamespace:
            writes.append(
                {
                    "text": text,
                    "evidence_refs": list(evidence_refs),
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "emotion_tag": emotion_tag,
                }
            )
            return SimpleNamespace(success=True, reason_code=None, detail="ok", memory_id="mem-001")

    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)
    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: registry)
    monkeypatch.setattr("host_brain.app.dependencies.get_episodic_store", lambda: _StoreStub())
    monkeypatch.setattr("host_brain.app.dependencies.get_semantic_recaller", lambda: object())

    outcome = _execute_skill(
        request_body={},
        request_id="req_exec_skill_memory_001",
        session_id="sess_exec_skill_memory_001",
        person_id="person_exec_skill_memory_001",
        intent_id="intent_exec_skill_memory_001",
        skill_id="memory_store",
        task_text="记住今天我们确认了联调问题",
        user_message="帮我记住今天我们确认了联调问题",
        governor_result={
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": "approval::exec_skill_memory_001"},
        },
        intent_draft={
            "args": {
                "content": "今天我们确认了联调问题",
                "source": "user_request",
                "tags": ["task", "event"],
            }
        },
        emotion_context={"mood_state": "CALM", "emotion_tag": "FOCUSED"},
        render_target_lang="zh-CN",
    )

    assert outcome.success is True
    assert outcome.target_id == "memory_store"
    assert writes[0]["text"] == "今天我们确认了联调问题"
    assert writes[0]["request_id"] == "req_exec_skill_memory_001"
    assert writes[0]["intent_id"] == "intent_exec_skill_memory_001"
    assert writes[0]["emotion_tag"] == "FOCUSED"
    assert "request:req_exec_skill_memory_001" in writes[0]["evidence_refs"]
    assert "session:sess_exec_skill_memory_001" in writes[0]["evidence_refs"]
    assert "source:user_request" in writes[0]["evidence_refs"]


def test_execute_skill_long_term_memory_search_filters_semantic_layer(monkeypatch) -> None:
    registry = build_memory_skill_registry()
    captured: dict[str, object] = {}

    class _SemanticRecallStub:
        async def recall(
            self,
            query: str,
            *,
            top_k: int = 5,
            threshold: float | None = None,
            filter_metadata: dict[str, object] | None = None,
        ) -> SimpleNamespace:
            captured["query"] = query
            captured["top_k"] = top_k
            captured["threshold"] = threshold
            captured["filter_metadata"] = dict(filter_metadata or {})
            return SimpleNamespace(
                hits=[
                    SimpleNamespace(
                        doc_id="semantic::unit-long-term",
                        content="用户偏好技术场景先给可执行步骤。",
                        score=0.97,
                        metadata={"memory_layer": "long_term"},
                        source="chromadb",
                    )
                ]
            )

    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: registry)
    monkeypatch.setattr("host_brain.app.dependencies.get_episodic_store", lambda: None)
    monkeypatch.setattr("host_brain.app.dependencies.get_semantic_recaller", lambda: _SemanticRecallStub())

    outcome = _execute_skill(
        request_body={
            "args": {
                "query": "技术场景怎么帮我",
                "memory_type": "semantic",
                "search_scope": "long_term",
                "top_k": 3,
                "threshold": 0.0,
            }
        },
        request_id="req_exec_skill_long_term_memory_search",
        session_id="sess_exec_skill_long_term_memory_search",
        person_id="person_exec_skill_long_term_memory_search",
        intent_id="intent_exec_skill_long_term_memory_search",
        skill_id="memory_search",
        task_text="检索长期记忆",
        user_message="检索长期记忆",
        governor_result={"decision": "APPROVE", "tokens": {}},
        intent_draft={
            "args": {
                "query": "技术场景怎么帮我",
                "memory_type": "semantic",
                "search_scope": "long_term",
                "top_k": 3,
                "threshold": 0.0,
            }
        },
        emotion_context={"mood_state": "CALM", "emotion_tag": "FOCUSED"},
        render_target_lang="zh-CN",
    )

    detail = outcome.payload["result"]["detail"]  # type: ignore[index]
    assert outcome.success is True
    assert detail["source"] == "semantic_recaller"
    assert detail["search_scope"] == "long_term"
    assert detail["results"][0]["doc_id"] == "semantic::unit-long-term"
    assert captured["filter_metadata"] == {"memory_layer": "long_term"}
