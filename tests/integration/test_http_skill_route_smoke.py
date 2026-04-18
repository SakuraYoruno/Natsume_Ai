from __future__ import annotations

import json
import time
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import host_brain.app.dependencies as deps
import host_brain.app.fastapi_app as fastapi_app
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.ide_registry import IDESkillProvider


class _FakeIntentEngine:
    def evaluate_intent(self, *args, **kwargs) -> dict[str, Any]:  # noqa: ANN002, ANN003
        return {
            "decision": "APPROVE",
            "reason_code": None,
            "intent_id": "intent_http_skill_smoke_001",
            "tokens": {"approval_token_ref": "approval::http_skill_smoke_001"},
        }


async def _fake_run_retrieval(*args, **kwargs) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # noqa: ANN002, ANN003
    return {"status": "SKIPPED", "reason_code": "TEST_STUB"}, []


async def _fake_llm_runtime_async(*args, **kwargs) -> dict[str, Any]:  # noqa: ANN002, ANN003
    return {
        "available": True,
        "provider_name": "mock",
        "model_id": "mock-skill-route",
        "timeout_ms": 1000,
        "route_target": "host",
        "route_reason_code": "TEST_STUB",
    }


async def _fake_chat_agent_sse_generator(chat_input):  # noqa: ANN001
    sub_ctx = getattr(chat_input, "sub_agent_context", None)
    result_text = getattr(sub_ctx, "result_text", "") if sub_ctx is not None else ""
    payload = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": chat_input.request_id,
        "narrative": f"skill-route-ok::{result_text}",
        "audit_refs": {"path": "test:chat_agent_stub"},
    }
    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _fake_async_post_process(**kwargs) -> None:  # noqa: ANN003
    return None


def _parse_sse_events(response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        raw = stripped[5:].strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _build_client(monkeypatch, *, use_real_governor: bool = False) -> tuple[TestClient, SQLiteLedger]:
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    registry = build_builtin_skill_registry()

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_skill_registry", registry, raising=False)
    monkeypatch.setattr(deps, "_intent_engine", None, raising=False)
    monkeypatch.setattr(fastapi_app, "_ledger", ledger, raising=False)

    stack = ExitStack()
    stack.enter_context(
        patch(
            "host_brain.app.fastapi_app._evaluate_immune_gate_for_chat",
            lambda request_body: {"decision": "ALLOW", "reason_codes": []},
        )
    )
    if not use_real_governor:
        stack.enter_context(
            patch("host_brain.app.chat_handler.get_intent_engine", lambda: _FakeIntentEngine())
        )
    stack.enter_context(
        patch("host_brain.app.agent_runtime_shared.chat_lite_governor", lambda **kwargs: {"decision": "APPROVE"})
    )
    stack.enter_context(
        patch("host_brain.app.chat_retrieval_helpers._run_retrieval", _fake_run_retrieval)
    )
    stack.enter_context(
        patch("host_brain.app.graph_config_builder.resolve_host_llm_runtime_async", _fake_llm_runtime_async)
    )
    stack.enter_context(
        patch("host_brain.app.graph_config_builder.resolve_host_llm_runtime", lambda *args, **kwargs: {"available": True})
    )
    stack.enter_context(
        patch("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent_sse_generator)
    )
    stack.enter_context(
        patch("host_brain.app.agent_runtime_shared._async_post_process", _fake_async_post_process)
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    client.__dict__["_skill_smoke_stack"] = stack
    client.__dict__["_skill_smoke_ledger"] = ledger
    return client, ledger


def _close_client(client: TestClient, ledger: SQLiteLedger) -> None:
    stack = client.__dict__.pop("_skill_smoke_stack", None)
    if stack is not None:
        stack.close()
    ledger.close()


def test_http_chat_route_executes_direct_skill(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch)
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "please summarize the current session",
                "session_id": "sess_http_skill_smoke_001",
                "intent_draft": {
                    "route": "skill",
                    "target_id": "session_summary",
                    "args": {
                        "user_message": "today we verify the HTTP skill route",
                        "session_id": "sess_http_skill_smoke_001",
                    },
                },
            },
        )

        assert response.status_code == 200
        assert "text/event-stream" in str(response.headers.get("content-type", "")).lower()

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "skill"
        assert route_event["target_id"] == "session_summary"
        assert route_event["reason_code"] == "intent_draft:skill"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert len(trace["skill_exec_log"]) == 1
        assert trace["skill_exec_log"][0]["skill_name"] == "session_summary"
        assert trace["skill_exec_log"][0]["result_status"] == "SUCCESS"
    finally:
        _close_client(client, ledger)


def test_http_chat_route_infers_session_summary_from_natural_language(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch)
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "please summarize the current session",
                "session_id": "sess_http_skill_smoke_nl_001",
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "skill"
        assert route_event["target_id"] == "session_summary"
        assert route_event["reason_code"] == "intent_draft:skill"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert len(trace["skill_exec_log"]) == 1
        assert trace["skill_exec_log"][0]["skill_name"] == "session_summary"
        assert trace["skill_exec_log"][0]["result_status"] == "SUCCESS"
    finally:
        _close_client(client, ledger)


def test_http_chat_route_infers_codex_codegen_from_code_request(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch)
    deps._skill_registry.register_decorated(IDESkillProvider())
    captured: dict[str, Any] = {}

    def _fake_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        captured["payload"] = dict(payload)
        return {
            "status": "EXECUTED",
            "result_text": "```python\nprint('fixed')\n```",
            "tool": payload.get("tool"),
        }

    monkeypatch.setattr(
        "host_brain.app.executive_runtime_core.dispatch_executor_payload",
        _fake_dispatch,
    )
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "please fix the failing test and show me the code",
                "session_id": "sess_http_skill_smoke_codex_001",
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "skill"
        assert route_event["target_id"] == "codex_codegen"
        assert commit_event["decision"] == "APPROVE"
        assert "print('fixed')" in str(commit_event.get("narrative") or "")
        assert captured["payload"]["workspace_strategy"] == "runtime_repo"
        assert captured["payload"]["run_mode"] == "worker"

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert len(trace["skill_exec_log"]) == 1
        assert trace["skill_exec_log"][0]["skill_name"] == "codex_codegen"
        assert trace["skill_exec_log"][0]["result_status"] == "SUCCESS"
    finally:
        _close_client(client, ledger)


def test_http_chat_route_uses_lightweight_codex_mode_for_simple_snippet_request(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch)
    deps._skill_registry.register_decorated(IDESkillProvider())
    captured: dict[str, Any] = {}

    def _fake_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        captured["payload"] = dict(payload)
        return {
            "status": "EXECUTED",
            "result_text": "def dedupe_by_frequency(arr):\n    return []",
            "tool": payload.get("tool"),
        }

    monkeypatch.setattr(
        "host_brain.app.executive_runtime_core.dispatch_executor_payload",
        _fake_dispatch,
    )
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "写一个 Python 函数，输入数组后去重并按出现频次降序返回。",
                "session_id": "sess_http_skill_smoke_codex_light_001",
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "skill"
        assert route_event["target_id"] == "codex_codegen"
        assert commit_event["decision"] == "APPROVE"
        assert "dedupe_by_frequency" in str(commit_event.get("narrative") or "")
        assert captured["payload"]["run_mode"] == "inspect"
        assert captured["payload"]["allow_temp_workspace"] is True
        assert str(captured["payload"].get("workspace_strategy") or "").strip() == ""
    finally:
        _close_client(client, ledger)


def test_http_chat_route_infers_codex_self_heal_task(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch)
    deps._skill_registry.register_decorated(IDESkillProvider())
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "host_brain.skills.codex_task_builder._safe_build_runtime_health_snapshot",
        lambda request_id, include_dependency_probes: {
            "runtime_repo_root": "D:/Natsume_Ai",
            "health_status": "DEGRADED",
            "health_reason_code": "EDGE_RPC_DOWN",
            "health_report": {"status": "DEGRADED", "reason_code": "EDGE_RPC_DOWN"},
            "dependency_probe_error": None,
        },
    )

    def _fake_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        captured["payload"] = dict(payload)
        return {
            "status": "EXECUTED",
            "result_text": "self-heal-ready",
            "tool": payload.get("tool"),
        }

    monkeypatch.setattr(
        "host_brain.app.executive_runtime_core.dispatch_executor_payload",
        _fake_dispatch,
    )
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "please self heal the host health check failure",
                "session_id": "sess_http_skill_smoke_self_heal_001",
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")

        assert route_event["route_type"] == "skill"
        assert route_event["target_id"] == "codex_codegen"
        assert captured["payload"]["run_mode"] == "self_heal"
        assert captured["payload"]["workspace_strategy"] == "runtime_repo"
        assert "runtime:service_health" in captured["payload"]["evidence_refs"]
    finally:
        _close_client(client, ledger)


def test_http_chat_route_real_governor_completes_skill_triptych(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch, use_real_governor=True)
    try:
        now_ms = int(time.time() * 1000)
        response = client.post(
            "/v1/chat",
            json={
                "message": "please summarize the current session",
                "session_id": "sess_http_skill_e2e_002",
                "intent_draft": {
                    "schema_version": "Natsume.intent.v1",
                    "id": f"intent::http-skill-e2e::{now_ms}",
                    "ts_ms": now_ms,
                    "session_id": "sess_http_skill_e2e_002",
                    "seq": 1,
                    "type": "query",
                    "action": "session_summary",
                    "target": "host",
                    "args": {
                        "user_message": "today we verify the HTTP skill route",
                        "session_id": "sess_http_skill_e2e_002",
                    },
                    "nonce": f"nonce::http-skill-e2e::{now_ms}",
                },
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "skill"
        assert route_event["target_id"] == "session_summary"
        assert route_event["reason_code"] == "intent_draft:skill"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert trace["status"] == "COMPLETE"
        assert len(trace["intent_log"]) == 1
        assert len(trace["governor_log"]) == 1
        assert len(trace["skill_exec_log"]) == 1
        assert trace["skill_exec_log"][0]["skill_name"] == "session_summary"
        assert trace["skill_exec_log"][0]["result_status"] == "SUCCESS"
    finally:
        _close_client(client, ledger)


def test_http_chat_route_infers_supported_plan_from_natural_language(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch)
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "first summarize the current session then read emotion snapshot",
                "session_id": "sess_http_plan_smoke_nl_001",
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "plan"
        assert route_event["target_id"] == "skill_dag"
        assert route_event["reason_code"] == "intent_draft:plan"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")
        assert "emotion_query" in str(commit_event.get("narrative") or "")

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert sorted(row["skill_name"] for row in trace["skill_exec_log"]) == [
            "emotion_query",
            "session_summary",
        ]
        assert all(row["result_status"] == "SUCCESS" for row in trace["skill_exec_log"])
    finally:
        _close_client(client, ledger)


def test_http_chat_route_executes_skill_plan(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch)
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "run the prepared multi-step skill flow",
                "session_id": "sess_http_plan_smoke_001",
                "intent_draft": {
                    "planned_steps": [
                        {
                            "step_index": 1,
                            "route": "skill",
                            "target_id": "session_summary",
                            "description": "summarize session",
                            "task_description": "summarize current session state",
                            "route_reason": "test:http_plan:session_summary",
                            "allow_parallel": True,
                            "skill_family": "social",
                            "risk_level": 1,
                        },
                        {
                            "step_index": 2,
                            "route": "skill",
                            "target_id": "emotion_query",
                            "description": "read emotion snapshot",
                            "task_description": "read current emotion snapshot",
                            "route_reason": "test:http_plan:emotion_query",
                            "allow_parallel": True,
                            "skill_family": "social",
                            "risk_level": 1,
                        },
                    ]
                },
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "plan"
        assert route_event["target_id"] == "skill_dag"
        assert route_event["reason_code"] == "intent_draft:plan"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")
        assert "emotion_query" in str(commit_event.get("narrative") or "")

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert sorted(row["skill_name"] for row in trace["skill_exec_log"]) == [
            "emotion_query",
            "session_summary",
        ]
        assert all(row["result_status"] == "SUCCESS" for row in trace["skill_exec_log"])
    finally:
        _close_client(client, ledger)


def test_http_chat_route_skill_plan_preserves_codex_workspace_strategy(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch)
    deps._skill_registry.register_decorated(IDESkillProvider())
    captured: list[dict[str, Any]] = []

    def _fake_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        captured.append(dict(payload))
        return {
            "status": "EXECUTED",
            "result_text": "```python\nprint('plan-fixed')\n```",
            "tool": payload.get("tool"),
        }

    monkeypatch.setattr(
        "host_brain.app.executive_runtime_core.dispatch_executor_payload",
        _fake_dispatch,
    )
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "first fix the failing test then summarize the current session",
                "session_id": "sess_http_plan_codex_runtime_repo_001",
                "intent_draft": {
                    "planned_steps": [
                        {
                            "step_index": 1,
                            "route": "skill",
                            "target_id": "codex_codegen",
                            "description": "fix the failing test",
                            "task_description": "fix the failing test",
                            "route_reason": "test:http_plan:codex_codegen",
                            "allow_parallel": True,
                            "skill_family": "code",
                            "risk_level": 2,
                            "args": {
                                "workspace_strategy": "runtime_repo",
                                "task": "fix the failing test",
                            },
                        },
                        {
                            "step_index": 2,
                            "route": "skill",
                            "target_id": "session_summary",
                            "description": "summarize session",
                            "task_description": "summarize current session state",
                            "route_reason": "test:http_plan:session_summary",
                            "allow_parallel": True,
                            "skill_family": "social",
                            "risk_level": 1,
                        },
                    ]
                },
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "plan"
        assert route_event["target_id"] == "skill_dag"
        assert commit_event["decision"] == "APPROVE"
        assert captured[0]["workspace_strategy"] == "runtime_repo"
        assert captured[0]["task"] == "fix the failing test"

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert sorted(row["skill_name"] for row in trace["skill_exec_log"]) == [
            "codex_codegen",
            "session_summary",
        ]
    finally:
        _close_client(client, ledger)


def test_http_chat_route_real_governor_accepts_plan_extensions(monkeypatch) -> None:
    client, ledger = _build_client(monkeypatch, use_real_governor=True)
    try:
        now_ms = int(time.time() * 1000)
        response = client.post(
            "/v1/chat",
            json={
                "message": "first summarize session then read emotion snapshot",
                "session_id": "sess_http_plan_e2e_001",
                    "intent_draft": {
                        "schema_version": "Natsume.intent.v1",
                        "id": f"intent::http-plan-e2e::{now_ms}",
                        "ts_ms": now_ms,
                        "session_id": "sess_http_plan_e2e_001",
                        "seq": 1,
                        "type": "query",
                        "route": "plan",
                        "target_id": "skill_dag",
                        "action": "exec_intent",
                        "target": "host",
                    "args": {
                        "text": "first summarize session then read emotion snapshot",
                    },
                    "nonce": f"nonce::http-plan-e2e::{now_ms}",
                    "planned_steps": [
                        {
                            "step_index": 1,
                            "route": "skill",
                            "target_id": "session_summary",
                            "description": "summarize session",
                            "task_description": "summarize current session state",
                            "route_reason": "test:http_plan_real:session_summary",
                            "allow_parallel": True,
                            "skill_family": "social",
                            "risk_level": 1,
                        },
                        {
                            "step_index": 2,
                            "route": "skill",
                            "target_id": "emotion_query",
                            "description": "read emotion snapshot",
                            "task_description": "read current emotion snapshot",
                            "route_reason": "test:http_plan_real:emotion_query",
                            "allow_parallel": True,
                            "skill_family": "social",
                            "risk_level": 1,
                        },
                    ],
                },
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
        commit_event = next(event for event in events if event.get("phase") == "COMMIT")

        assert route_event["route_type"] == "plan"
        assert route_event["target_id"] == "skill_dag"
        assert route_event["reason_code"] == "intent_draft:plan"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")
        assert "emotion_query" in str(commit_event.get("narrative") or "")

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert trace["status"] == "COMPLETE"
        assert len(trace["intent_log"]) == 1
        assert len(trace["governor_log"]) == 1
        assert sorted(row["skill_name"] for row in trace["skill_exec_log"]) == [
            "emotion_query",
            "session_summary",
        ]
        assert all(row["result_status"] == "SUCCESS" for row in trace["skill_exec_log"])
    finally:
        _close_client(client, ledger)
