from __future__ import annotations

import json
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import host_brain.composition.process_services as deps
import host_brain.app.asgi as host_api
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.agent_runtime_registry import AgentRuntimeSkillProvider


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


async def _fake_surface_reply_sse_generator(  # noqa: ANN001
    chat_input,
    *,
    ports,
):
    assert ports is not None
    sub_ctx = getattr(chat_input, "sub_agent_context", None)
    result_text = getattr(sub_ctx, "result_text", "") if sub_ctx is not None else ""
    trace_stage_meta = dict(getattr(chat_input, "trace_stage_meta", {}) or {})
    # Governed Chat uses a frozen model budget.  This fixture is deliberately
    # model-free, but it must emulate the same server-owned final measurement
    # shape that a real buffered provider completion emits; otherwise the
    # route executor correctly refuses to turn a synthetic reply into an
    # accepted task result.
    if isinstance(trace_stage_meta.get("governed_model_budget"), dict):
        trace_stage_meta["surface_model_attempt"] = {
            "success": True,
            "attempt_ids": [
                f"model-attempt://http-skill-route/{chat_input.request_id}/1"
            ],
            "budget_measurement": {
                "schema_version": "Natsume.model_runtime.budget_measurement.v1",
                "measurement_ref": (
                    f"model-budget://http-skill-route/{chat_input.request_id}/1"
                ),
                "attempt_count": 1,
                "retry_count": 0,
                "token_count": 18,
                "model_cost_micros": 27,
            },
        }
    payload = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": chat_input.request_id,
        "narrative": f"skill-route-ok::{result_text}",
        "audit_refs": {
            "path": "test:surface_reply_stub",
            "trace_stage_meta": trace_stage_meta,
        },
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


def _governed_commit(events: list[dict[str, Any]]) -> dict[str, Any]:
    assert not any(event.get("phase") == "EXECUTIVE_ROUTE" for event in events)
    commits = [event for event in events if event.get("phase") == "COMMIT"]
    assert commits
    commit = commits[-1]
    assert commit.get("governed") is True
    receipt = dict(commit.get("execution_receipt") or {})
    assert receipt.get("status") == "ACCEPTED"
    assert receipt.get("accepted") is True
    assert receipt.get("graph_id")
    assert receipt.get("attempt_id")
    return commit


def _build_client(monkeypatch, *, canonical_root: Path) -> tuple[TestClient, SQLiteLedger]:
    from host_brain.app.settings.scheduler import get_scheduler_settings

    monkeypatch.setenv("HOST_SCHEDULER_CLOCK_ENABLED", "0")
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "0")
    get_scheduler_settings.cache_clear()
    db_path = canonical_root / "host-canonical.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="http-skill-route-fixture",
    )
    assert migrated.success, migrated.as_dict()
    ledger = SQLiteLedger(
        db_path=db_path,
        check_same_thread=False,
        initialize_schema=False,
    )
    registry = build_builtin_skill_registry()

    monkeypatch.setattr(deps.get_process_service_container(), "ledger", ledger, raising=False)
    monkeypatch.setattr(deps.get_process_service_container(), "skill_registry", registry, raising=False)
    monkeypatch.setattr(deps.get_process_service_container(), "intent_engine", None, raising=False)
    monkeypatch.setattr(deps.get_process_service_container(), "token_service", None, raising=False)
    previous_composition = deps.get_process_service_container().capability_runtime_composition  # noqa: SLF001
    monkeypatch.setattr(deps.get_process_service_container(), "capability_runtime_composition", None)

    stack = ExitStack()
    stack.enter_context(
        patch(
            "host_brain.app.asgi.evaluate_immune_gate_for_chat",
            lambda request_body, **_kwargs: {"decision": "ALLOW", "reason_codes": []},
        )
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
        patch("host_brain.app.surface_reply_runtime.surface_reply_sse_generator", _fake_surface_reply_sse_generator)
    )
    stack.enter_context(
        patch("host_brain.agent_runtime.postprocess._async_post_process", _fake_async_post_process)
    )

    client = TestClient(host_api.app, raise_server_exceptions=False)
    client.__enter__()
    client.__dict__["_skill_smoke_stack"] = stack
    client.__dict__["_skill_smoke_ledger"] = ledger
    client.__dict__["_previous_capability_composition"] = previous_composition
    return client, ledger


def _close_client(client: TestClient, ledger: SQLiteLedger) -> None:
    from host_brain.app.settings.scheduler import get_scheduler_settings

    client.__exit__(None, None, None)
    stack = client.__dict__.pop("_skill_smoke_stack", None)
    if stack is not None:
        stack.close()
    deps.reset_capability_runtime_composition(
        client.__dict__.pop("_previous_capability_composition", None)
    )
    get_scheduler_settings.cache_clear()
    ledger.close()


def test_http_chat_route_executes_direct_skill(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
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
        commit_event = _governed_commit(events)

        assert commit_event["route_type"] == "skill"
        assert commit_event["target_id"] == "session_summary"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert trace["skill_exec_log"] == []
    finally:
        _close_client(client, ledger)


def test_http_chat_route_infers_session_summary_from_natural_language(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
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
        commit_event = _governed_commit(events)

        assert commit_event["route_type"] == "skill"
        assert commit_event["target_id"] == "session_summary"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")

        request_id = str(commit_event["request_id"])
        trace = ledger.trace_request(request_id)
        assert trace["skill_exec_log"] == []
    finally:
        _close_client(client, ledger)


def test_http_chat_route_pauses_inferred_agent_worker_for_review(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
    deps.get_process_service_container().skill_registry.register_decorated(AgentRuntimeSkillProvider())
    captured: dict[str, Any] = {}

    def _fake_dispatch(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured["payload"] = dict(payload)
        return {
            "status": "EXECUTED",
            "result_text": "```python\nprint('fixed')\n```",
            "tool": payload.get("tool"),
        }

    monkeypatch.setattr(
        "host_brain.app.governed_skill_execution.dispatch_executor_payload",
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
        commit_event = [event for event in events if event.get("phase") == "COMMIT"][-1]

        assert commit_event["decision"] == "PAUSE"
        assert commit_event["execution_state"] == "PAUSED_REVIEW"
        assert commit_event["reason_code"] == "GOV_AIRLOCK_REVIEW"
        assert commit_event["committed_route_token"] == "GOV_AIRLOCK_REVIEW"
        assert captured == {}
    finally:
        _close_client(client, ledger)


def test_http_chat_route_uses_lightweight_codex_mode_for_simple_snippet_request(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
    deps.get_process_service_container().skill_registry.register_decorated(AgentRuntimeSkillProvider())
    captured: dict[str, Any] = {}

    def _fake_dispatch(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured["payload"] = dict(payload)
        return {
            "status": "EXECUTED",
            "result_text": "def dedupe_by_frequency(arr):\n    return []",
            "tool": payload.get("tool"),
        }

    monkeypatch.setattr(
        "host_brain.app.governed_skill_execution.dispatch_executor_payload",
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
        commit_event = _governed_commit(events)

        assert commit_event["route_type"] == "skill"
        assert commit_event["target_id"] == "agent_inspect"
        assert commit_event["decision"] == "APPROVE"
        assert "dedupe_by_frequency" in str(commit_event.get("narrative") or "")
        work_order = captured["payload"]["work_order"]
        assert work_order["run_mode"] == "inspect"
        assert work_order["workspace"] == {"strategy": "temp_workspace"}
    finally:
        _close_client(client, ledger)


def test_http_chat_route_pauses_inferred_agent_self_heal_for_review(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
    deps.get_process_service_container().skill_registry.register_decorated(AgentRuntimeSkillProvider())
    captured: dict[str, Any] = {}

    def _fake_dispatch(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured["payload"] = dict(payload)
        return {
            "status": "EXECUTED",
            "result_text": "self-heal-ready",
            "tool": payload.get("tool"),
        }

    monkeypatch.setattr(
        "host_brain.app.governed_skill_execution.dispatch_executor_payload",
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
        commit_event = [event for event in events if event.get("phase") == "COMMIT"][-1]

        assert commit_event["decision"] == "PAUSE"
        assert commit_event["execution_state"] == "PAUSED_REVIEW"
        assert commit_event["reason_code"] == "GOV_AIRLOCK_REVIEW"
        assert commit_event["committed_route_token"] == "GOV_AIRLOCK_REVIEW"
        assert captured == {}
    finally:
        _close_client(client, ledger)


def test_http_chat_route_real_governor_uses_canonical_execution_receipt(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
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
        commit_event = _governed_commit(events)

        assert commit_event["route_type"] == "skill"
        assert commit_event["target_id"] == "session_summary"
        assert commit_event["decision"] == "APPROVE"
        assert "session_summary" in str(commit_event.get("narrative") or "")

        receipt = dict(commit_event["execution_receipt"])
        assert receipt["status"] == "ACCEPTED"
        assert receipt["accepted"] is True
        assert receipt["graph_id"]
        assert receipt["attempt_id"]
        assert ledger.trace_request(str(commit_event["request_id"]))["skill_exec_log"] == []
    finally:
        _close_client(client, ledger)


def test_http_chat_route_streams_affective_reflection_without_taskgraph(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "first summarize the current session then read your emotion snapshot",
                "session_id": "sess_http_plan_smoke_nl_001",
            },
        )

        assert response.status_code == 200

        events = _parse_sse_events(response)
        commit_event = [
            event for event in events if event.get("phase") == "COMMIT"
        ][-1]

        assert commit_event["decision"] == "APPROVE"
        assert commit_event.get("governed") is not True
        assert "execution_receipt" not in commit_event
        trace_stage_meta = commit_event["audit_refs"]["trace_stage_meta"]
        assert trace_stage_meta["runtime_path"] == "realtime_companion"
        assert "governed_model_budget" not in trace_stage_meta
    finally:
        _close_client(client, ledger)


def test_http_chat_route_rejects_caller_provided_skill_plan(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
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
                            "target_id": "memory_recall",
                            "description": "recall session context",
                            "task_description": "recall what we already discussed",
                            "route_reason": "test:http_plan:memory_recall",
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
        commit_event = [event for event in events if event.get("phase") == "COMMIT"][-1]

        assert commit_event["decision"] == "REJECT"
        assert commit_event["reason_code"] == "GOVERNED_ROUTE_UNSUPPORTED"
        assert not any(event.get("governed") is True for event in events)
    finally:
        _close_client(client, ledger)


def test_http_chat_route_rejects_caller_provided_skill_plan(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
    deps.get_process_service_container().skill_registry.register_decorated(AgentRuntimeSkillProvider())
    captured: list[dict[str, Any]] = []

    def _fake_dispatch(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured.append(dict(payload))
        return {
            "status": "EXECUTED",
            "result_text": "```python\nprint('plan-fixed')\n```",
            "tool": payload.get("tool"),
        }

    monkeypatch.setattr(
        "host_brain.app.governed_skill_execution.dispatch_executor_payload",
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
                            "target_id": "agent_worker",
                            "description": "fix the failing test",
                            "task_description": "fix the failing test",
                            "route_reason": "test:http_plan:agent_worker",
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
        commit_event = [event for event in events if event.get("phase") == "COMMIT"][-1]

        assert commit_event["decision"] == "REJECT"
        assert commit_event["reason_code"] == "GOVERNED_ROUTE_UNSUPPORTED"
        assert captured == []
    finally:
        _close_client(client, ledger)


def test_http_chat_route_real_governor_rejects_caller_provided_plan_extensions(monkeypatch, tmp_path: Path) -> None:
    client, ledger = _build_client(monkeypatch, canonical_root=tmp_path)
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
                            "target_id": "memory_recall",
                            "description": "recall session context",
                            "task_description": "recall what we already discussed",
                            "route_reason": "test:http_plan_real:memory_recall",
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
        commit_event = [event for event in events if event.get("phase") == "COMMIT"][-1]

        assert commit_event["decision"] == "REJECT"
        assert commit_event["reason_code"] == "GOVERNED_ROUTE_UNSUPPORTED"
        assert not any(event.get("governed") is True for event in events)
    finally:
        _close_client(client, ledger)
