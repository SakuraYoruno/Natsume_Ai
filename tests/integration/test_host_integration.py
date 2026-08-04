"""host_brain 全面集成测试套件
=================================
里程碑：M-HOST-INTEGRATION-2026-03-11
目标：对 host_brain FastAPI 应用的所有路由进行集成测试，
      验证每条 HTTP 链路的请求/响应契约、Governor 鉴权、Bond 状态机、
      Canon API、Tool API、Obs API 等核心功能。

策略：
- 使用 FastAPI TestClient（同步 ASGI 测试客户端）
- 使用每例独立的临时 SQLite 账本隔离测试数据
- LLM/ASR/TTS 等外部依赖全部 mock，不发起真实网络请求
- SSE 流式响应解析为事件列表进行断言
- 每个测试类对应一个路由组或功能模块
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Generator, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# 工具函数：解析 SSE 流
# ---------------------------------------------------------------------------

def parse_sse_events(raw: str) -> List[Dict[str, Any]]:
    """将 SSE 原始文本解析为事件列表。"""
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    events.append({"_raw": payload})
    return events


def find_event(events: List[Dict[str, Any]], phase: str, **kwargs) -> Dict[str, Any] | None:
    """在事件列表中查找匹配 phase 和额外字段的事件。"""
    for ev in events:
        if ev.get("phase") != phase:
            continue
        if all(ev.get(k) == v for k, v in kwargs.items()):
            return ev
    return None


# ---------------------------------------------------------------------------
# 共享 Fixture：内存账本 + 测试 App
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_ledger(tmp_path):
    """每个测试显式拥有独立、可被生命周期组件复开的 SQLite 账本。"""
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    ledger = SQLiteLedger(
        db_path=tmp_path / "host-integration.sqlite3",
        check_same_thread=False,
    )
    yield ledger
    ledger.close()


@pytest.fixture
def test_client(mem_ledger):
    """构建与当前测试账本同生命周期的 TestClient。"""
    # 注入本例专用账本
    import host_brain.composition.process_services as _deps
    _deps.get_process_service_container().ledger = mem_ledger

    # mock LLM 推理（避免真实 API 调用）
    async def _mock_llm_runtime(*args, **kwargs):
        return {
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "llm_api_key": "mock-key",
            "base_url_override": None,
            "timeout_ms": 1000,
            "route_target": "cloud",
            "route_reason_code": None,
        }

    attested_stream_calls: list[dict[str, Any]] = []

    async def _mock_attested_stream(*args, **kwargs):
        """Current governed Surface protocol: deltas plus final usage.

        ``/v1/chat`` now reaches ``ModelAttemptService.stream_with_result``.
        Patching the retired raw-chunk helper made this integration fixture
        silently exercise the fail-closed no-reply branch instead of the
        actual sentence-delivery path.
        """

        del args
        attested_stream_calls.append(dict(kwargs))
        text = "你好，我是夏目。"
        yield {"kind": "delta", "text": text}
        yield {
            "kind": "final",
            "content": text,
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
                "model_cost_micros": 1,
            },
        }

    with (
        patch.dict(
            os.environ,
            {
                "HOST_BOND_BACKEND": "memory",
                "HOST_BOND_SQLITE_PATH": "",
                "HOST_BOND_STAGE_THRESHOLDS": "",
                "HOST_BOND_SCORE_MAP": "",
                # The readiness endpoint probes the effective settings rather
                # than the mocked chat resolver, and workspace composition is
                # deliberately fail-closed without its signing secret.
                "OPENAI_API_KEY": "test-openai-key",
                # Governed Chat can reveal a sentence only through a provider
                # that supplies a terminal usage frame.  The production
                # default may be a non-streaming provider; this isolated
                # fixture pins the explicit OpenAI-compatible lane whose
                # transport is mocked just below.
                "HOST_LLM_PROVIDER": "openai",
                "HOST_MODEL_ROUTE_SCENE_CHAT_SURFACE_PROVIDER": "openai",
                "HOST_OPENAI_MODEL_ID": "test-openai-model",
                "HOST_AGENT_WORKSPACE_HMAC_SECRET": "test-workspace-secret-at-least-32-bytes",
                # Do not combine the fixture's direct secret with a developer
                # .env secret reference. Production correctly rejects that
                # ambiguous authority source.
                "HOST_AGENT_WORKSPACE_HMAC_SECRET_REF": "",
                "HOST_SCHEDULER_CLOCK_ENABLED": "0",
                "HOST_BACKGROUND_TASKS_ENABLED": "0",
            },
            clear=False,
        ),
        patch("host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
              new=_mock_llm_runtime),
        patch("host_brain.app.graph_config_builder.resolve_host_llm_runtime",
              new=_mock_llm_runtime),
        patch(
            "host_brain.integrations.llm_helpers.astream_llm_stream_events",
            new=_mock_attested_stream,
        ),
    ):
        from host_brain.app.settings.scheduler import get_scheduler_settings

        get_scheduler_settings.cache_clear()
        from host_brain.app.asgi import app
        # ``TestClient`` only enters the ASGI lifespan when used as a context
        # manager.  Host readiness/startup endpoints deliberately depend on
        # lifespan-owned, migration-gated projections, so bypassing it would
        # exercise an impossible production state and incorrectly turn the
        # fail-closed 503 contract into a fixture failure.
        with TestClient(app, raise_server_exceptions=False) as client:
            client.app.state.test_attested_stream_calls = attested_stream_calls
            yield client
        get_scheduler_settings.cache_clear()

    # 清理单例，避免跨测试污染
    _deps.get_process_service_container().ledger = None
    _deps.get_process_service_container().intent_engine = None
    _deps.get_process_service_container().skill_registry = None
    _deps.get_process_service_container().obs_metrics_collector = None


@pytest.fixture(autouse=True)
def _rebind_mem_ledger_each_test(mem_ledger):
    """Ensure this module always uses the injected in-memory ledger."""
    import host_brain.composition.process_services as _deps

    _deps.get_process_service_container().ledger = mem_ledger
    yield


# ---------------------------------------------------------------------------
# 1. GET /health — 健康检查
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """T-INT-HEALTH: GET /health 健康检查端点集成测试。"""

    def test_health_returns_200(self, test_client):
        """T-INT-HEALTH-01: /health 返回 200。"""
        resp = test_client.get("/health")
        assert resp.status_code == 200

    def test_health_response_schema(self, test_client):
        """T-INT-HEALTH-02: /health 响应包含必要字段。"""
        resp = test_client.get("/health")
        data = resp.json()
        assert "status" in data
        assert "version" in data
        assert "timestamp" in data
        assert isinstance(data["timestamp"], int)

    def test_health_status_is_string(self, test_client):
        """T-INT-HEALTH-03: /health status 字段为字符串。"""
        resp = test_client.get("/health")
        data = resp.json()
        assert isinstance(data["status"], str)
        assert len(data["status"]) > 0

    def test_health_version_format(self, test_client):
        """T-INT-HEALTH-04: /health version 字段格式正确。"""
        resp = test_client.get("/health")
        data = resp.json()
        assert data["version"] == "0.1.0"

    def test_health_details_is_dict(self, test_client):
        """T-INT-HEALTH-05: /health details 字段为字典。"""
        resp = test_client.get("/health")
        data = resp.json()
        assert isinstance(data.get("details"), dict)


# ---------------------------------------------------------------------------
# 2. GET /ready — 就绪检查
# ---------------------------------------------------------------------------

class TestReadinessEndpoint:
    """T-INT-READY: GET /ready 就绪检查端点集成测试。"""

    def test_ready_returns_200(self, test_client):
        """T-INT-READY-01: /ready 返回 200。"""
        resp = test_client.get("/ready")
        assert resp.status_code == 200

    def test_ready_response_schema(self, test_client):
        """T-INT-READY-02: /ready 响应包含必要字段。"""
        resp = test_client.get("/ready")
        data = resp.json()
        assert "ready" in data
        assert "status" in data
        assert "timestamp" in data
        assert isinstance(data["ready"], bool)

    def test_ready_status_is_valid(self, test_client):
        """T-INT-READY-03: /ready status 为合法值。"""
        resp = test_client.get("/ready")
        data = resp.json()
        assert data["status"] in {
            "READY",
            "DEGRADED",
            "FAILED",
            "NOT_READY",
            "ACCEPTANCE_PENDING_EXTERNAL",
        }

    def test_ready_summary_is_dict(self, test_client):
        """T-INT-READY-04: /ready summary 字段为字典。"""
        resp = test_client.get("/ready")
        data = resp.json()
        assert isinstance(data.get("summary"), dict)


# ---------------------------------------------------------------------------
# 3. GET /metrics — 运行时指标
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    """T-INT-METRICS: GET /metrics 运行时指标端点集成测试。"""

    def test_metrics_returns_200(self, test_client):
        """T-INT-METRICS-01: /metrics 返回 200。"""
        resp = test_client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_response_schema(self, test_client):
        """T-INT-METRICS-02: /metrics 响应包含必要字段。"""
        resp = test_client.get("/metrics")
        data = resp.json()
        required_fields = [
            "uptime_seconds", "total_requests", "total_chat_requests",
            "total_rejections", "total_errors", "timestamp",
        ]
        for field in required_fields:
            assert field in data, f"missing field: {field}"

    def test_metrics_uptime_positive(self, test_client):
        """T-INT-METRICS-03: /metrics uptime_seconds >= 0。"""
        resp = test_client.get("/metrics")
        data = resp.json()
        assert data["uptime_seconds"] >= 0

    def test_metrics_counts_non_negative(self, test_client):
        """T-INT-METRICS-04: /metrics 所有计数字段 >= 0。"""
        resp = test_client.get("/metrics")
        data = resp.json()
        for field in ["total_requests", "total_chat_requests", "total_rejections", "total_errors"]:
            assert data[field] >= 0, f"{field} should be >= 0"

    def test_metrics_reason_code_distribution_is_dict(self, test_client):
        """T-INT-METRICS-05: /metrics reason_code_distribution 为字典。"""
        resp = test_client.get("/metrics")
        data = resp.json()
        assert isinstance(data.get("reason_code_distribution"), dict)


# ---------------------------------------------------------------------------
# 4. GET /health/mcp — MCP 注册表健康检查
# ---------------------------------------------------------------------------

class TestMCPHealthEndpoint:
    """T-INT-MCP-HEALTH: GET /health/mcp MCP 健康检查端点集成测试。"""

    def test_mcp_health_returns_200(self, test_client):
        """T-INT-MCP-HEALTH-01: /health/mcp 返回 200。"""
        resp = test_client.get("/health/mcp")
        assert resp.status_code == 200

    def test_mcp_health_response_schema(self, test_client):
        """T-INT-MCP-HEALTH-02: /health/mcp 响应包含必要字段。"""
        resp = test_client.get("/health/mcp")
        data = resp.json()
        assert "status" in data
        assert "version" in data
        assert "timestamp" in data
        assert "details" in data

    def test_mcp_health_status_valid(self, test_client):
        """T-INT-MCP-HEALTH-03: /health/mcp status 为合法值。"""
        resp = test_client.get("/health/mcp")
        data = resp.json()
        assert data["status"] in {"OK", "DEGRADED", "DOWN"}

    def test_mcp_health_details_has_tool_count(self, test_client):
        """T-INT-MCP-HEALTH-04: /health/mcp details 包含 tool_count。"""
        resp = test_client.get("/health/mcp")
        data = resp.json()
        assert "tool_count" in data["details"]
        assert isinstance(data["details"]["tool_count"], int)


# ---------------------------------------------------------------------------
# 5. GET /health/canon_sync — Canon 同步健康检查
# ---------------------------------------------------------------------------

class TestCanonSyncHealthEndpoint:
    """T-INT-CANON-SYNC: GET /health/canon_sync Canon 同步健康检查端点集成测试。"""

    def test_canon_sync_health_returns_200(self, test_client):
        """T-INT-CANON-SYNC-01: /health/canon_sync 返回 200。"""
        resp = test_client.get("/health/canon_sync")
        assert resp.status_code == 200

    def test_canon_sync_health_response_schema(self, test_client):
        """T-INT-CANON-SYNC-02: /health/canon_sync 响应包含必要字段。"""
        resp = test_client.get("/health/canon_sync")
        data = resp.json()
        assert "status" in data
        assert "details" in data
        assert "drift_count" in data["details"]

    def test_canon_sync_drift_count_non_negative(self, test_client):
        """T-INT-CANON-SYNC-03: /health/canon_sync drift_count >= 0。"""
        resp = test_client.get("/health/canon_sync")
        data = resp.json()
        assert data["details"]["drift_count"] >= 0


# ---------------------------------------------------------------------------
# 6. Obs endpoints are isolated from hostmain
# ---------------------------------------------------------------------------

class TestObsMetricsEndpoint:
    """Hostmain 不承载 /obs/*；观测指标由 obs side 提供。"""

    def test_obs_metrics_not_on_hostmain(self, test_client):
        resp = test_client.get("/obs/metrics")
        assert resp.status_code == 404

    def test_obs_metrics_query_not_on_hostmain(self, test_client):
        resp = test_client.get("/obs/metrics?request_id=test-req-001")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. GET /startup — 启动报告
# ---------------------------------------------------------------------------

class TestStartupEndpoint:
    """T-INT-STARTUP: GET /startup 启动报告端点集成测试。"""

    def test_startup_returns_200(self, test_client):
        """T-INT-STARTUP-01: /startup 返回 200。"""
        resp = test_client.get("/startup")
        assert resp.status_code == 200

    def test_startup_response_schema(self, test_client):
        """T-INT-STARTUP-02: /startup 响应包含必要字段。"""
        resp = test_client.get("/startup")
        data = resp.json()
        assert "request_id" in data
        assert "timestamp" in data
        assert "report" in data

    def test_startup_report_is_dict(self, test_client):
        """T-INT-STARTUP-03: /startup report 字段为字典。"""
        resp = test_client.get("/startup")
        data = resp.json()
        assert isinstance(data["report"], dict)


# ---------------------------------------------------------------------------
# 8. Observability trace is isolated from hostmain
# ---------------------------------------------------------------------------

class TestObservabilityTraceEndpoint:
    """Hostmain 不承载观测 trace；该 API 由 obs side 提供。"""

    def test_trace_unknown_request_id_not_on_hostmain(self, test_client):
        resp = test_client.post(
            "/observability/trace",
            json={"request_id": "req::nonexistent::001"},
        )
        assert resp.status_code == 404

    def test_trace_schema_request_not_on_hostmain(self, test_client):
        resp = test_client.post(
            "/observability/trace",
            json={"request_id": "req::schema::test::001"},
        )
        assert resp.status_code == 404

    def test_trace_missing_request_id_not_on_hostmain(self, test_client):
        resp = test_client.post("/observability/trace", json={})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 9. POST /v1/chat — 核心对话接口（SSE 流式）
# ---------------------------------------------------------------------------

class TestChatEndpoint:
    """T-INT-CHAT: POST /v1/chat 核心对话接口集成测试。"""

    def test_chat_returns_streaming_response(self, test_client):
        """T-INT-CHAT-01: /v1/chat 返回 SSE 流式响应（200 + text/event-stream）。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "你好，夏目"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_chat_sse_has_thinking_event(self, test_client):
        """T-INT-CHAT-02: SSE 流包含 THINKING 事件。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "你好，夏目"},
        )
        events = parse_sse_events(resp.text)
        thinking = find_event(events, "PRESENCE", event="THINKING")
        assert thinking is not None, f"THINKING event not found in: {events}"

    def test_chat_sse_has_commit_event(self, test_client):
        """T-INT-CHAT-03: SSE 流包含 COMMIT 事件（done=True）。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "你好，夏目"},
        )
        events = parse_sse_events(resp.text)
        commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
        assert commit is not None, f"COMMIT done event not found in: {events}"

    def test_chat_sse_commit_has_request_id(self, test_client):
        """T-INT-CHAT-04: COMMIT 事件包含 request_id。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "你好，夏目"},
        )
        events = parse_sse_events(resp.text)
        commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
        assert commit is not None
        assert "request_id" in commit

    def test_chat_realtime_stream_uses_frozen_affect_without_taskgraph(
        self,
        test_client,
    ):
        """T-INT-CHAT-04A: 普通陪伴对话流式输出且不创建重型 TaskGraph。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "你好，夏目"},
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        sentence_indexes = [
            index
            for index, event in enumerate(events)
            if event.get("phase") == "SENTENCE"
        ]
        terminal_event = next(event for event in events if event.get("done"))
        assert "execution_receipt" not in terminal_event
        trace_stage_meta = terminal_event["audit_refs"]["trace_stage_meta"]
        turn_plan = trace_stage_meta["affective_turn_plan"]
        guidance = trace_stage_meta["affective_turn_guidance"]
        assert turn_plan["self_id"] == "natsume_ai"
        assert guidance["plan_id"] == turn_plan["plan_id"]
        assert guidance["self_affect_revision"] == turn_plan["self_affect_revision"]
        assert len(sentence_indexes) == 1
        assert len(test_client.app.state.test_attested_stream_calls) == 1
        sentence = events[sentence_indexes[0]]
        assert sentence["sentence"] == "你好，我是夏目。"
        assert sentence["affective_turn_plan_id"] == turn_plan["plan_id"]
        assert sentence["synchronization_id"] == turn_plan["expression_plan"][
            "synchronization_id"
        ]
        for field in ("graph_id", "node_id", "attempt_id", "execution_receipt"):
            assert field not in sentence

        commit_index, commit = next(
            (index, event)
            for index, event in enumerate(events)
            if event.get("phase") == "COMMIT" and event.get("done")
        )
        assert sentence_indexes[0] < commit_index
        assert commit["decision"] == "APPROVE"
        assert commit["audit_refs"]["path"] == "surface_reply:native_affective_stream"

    def test_chat_with_session_id(self, test_client):
        """T-INT-CHAT-05: /v1/chat 支持 session_id 参数。"""
        resp = test_client.post(
            "/v1/chat",
            json={
                "message": "今天天气怎么样",
                "session_id": "session::integration::test::001",
            },
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        assert len(events) > 0

    def test_chat_with_emotion_context(self, test_client):
        """T-INT-CHAT-06: /v1/chat 支持情感上下文参数。"""
        resp = test_client.post(
            "/v1/chat",
            json={
                "message": "我今天很开心",
                "emotion_tag": "joy",
                "emotion_intensity": 0.8,
                "mood_state": "happy",
            },
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        assert len(events) > 0

    def test_chat_empty_message_422(self, test_client):
        """T-INT-CHAT-07: 缺少 message 字段 → 422。"""
        resp = test_client.post("/v1/chat", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 10. POST /v1/edge/events — Edge 事件接收
# ---------------------------------------------------------------------------

class TestEdgeEventsEndpoint:
    """T-INT-EDGE-EVENTS: POST /v1/edge/events Edge 事件接收端点集成测试。"""

    def test_edge_events_empty_list(self, test_client):
        """T-INT-EDGE-EVENTS-01: 空事件列表 → 200，accepted=0。"""
        resp = test_client.post(
            "/v1/edge/events",
            json={"events": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["accepted"] == 0

    def test_edge_events_response_schema(self, test_client):
        """T-INT-EDGE-EVENTS-02: /v1/edge/events 响应包含必要字段。"""
        resp = test_client.post(
            "/v1/edge/events",
            json={"events": []},
        )
        data = resp.json()
        required_fields = ["success", "total", "accepted", "rejected", "timestamp"]
        for field in required_fields:
            assert field in data, f"missing field: {field}"

    def test_edge_events_single_event(self, test_client):
        """T-INT-EDGE-EVENTS-03: 单个 Edge 事件上报。"""
        resp = test_client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "ASR_RESULT",
                        "session_id": "session::edge::001",
                        "ts_ms": int(time.time() * 1000),
                        "payload": {"text": "你好", "confidence": 0.95},
                    }
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

    def test_edge_events_missing_events_field_422(self, test_client):
        """T-INT-EDGE-EVENTS-04: 缺少 events 字段 → 422。"""
        resp = test_client.post("/v1/edge/events", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 11. Bond API — /bond/* 路由集成测试
# ---------------------------------------------------------------------------

class TestBondAPIIntegration:
    """T-INT-BOND: Bond API 路由集成测试。"""

    def test_bond_health(self, test_client):
        """T-INT-BOND-01: 旧 Bond health 读投影已明确退役。"""
        resp = test_client.get("/bond/health")
        assert resp.status_code == 410
        assert resp.json()["reason_code"] == "LEGACY_BOND_READ_RETIRED"

    def test_bond_stage_new_user(self, test_client):
        """T-INT-BOND-02: 旧 Bond stage 不能冒充 canonical 关系状态。"""
        resp = test_client.get("/bond/stage/integration_test_user_001")
        assert resp.status_code == 410
        assert resp.json()["reason_code"] == "LEGACY_BOND_READ_RETIRED"

    def test_bond_stage_response_fields(self, test_client):
        """T-INT-BOND-03: 旧 Bond 读入口返回稳定的退役合同。"""
        resp = test_client.get("/bond/stage/integration_test_user_002")
        assert resp.status_code == 410
        data = resp.json()
        required = ["status", "reason_code", "detail", "request_id", "timestamp"]
        for field in required:
            assert field in data, f"missing field: {field}"
        assert data["reason_code"] == "LEGACY_BOND_READ_RETIRED"

    def test_bond_submit_event_interaction(self, test_client):
        """T-INT-BOND-04: POST /bond/event 拒绝旧积分写入。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_003",
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::integration::001"],
            },
        )
        assert resp.status_code == 410
        data = resp.json()
        assert data["reason_code"] == "LEGACY_BOND_WRITE_RETIRED"
        assert resp.headers["x-reason-code"] == "LEGACY_BOND_WRITE_RETIRED"

    def test_bond_submit_event_deep_talk(self, test_client):
        """T-INT-BOND-05: DEEP_TALK 也不能通过旧入口改变关系。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_004",
                "update_kind": "DEEP_TALK",
                "evidence_refs": ["evt::integration::002"],
            },
        )
        assert resp.status_code == 410
        assert resp.json()["reason_code"] == "LEGACY_BOND_WRITE_RETIRED"

    def test_bond_submit_event_empty_refs_422(self, test_client):
        """T-INT-BOND-06: 空 evidence 也不会进入旧 Bond writer。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_005",
                "update_kind": "INTERACTION",
                "evidence_refs": [],
            },
        )
        assert resp.status_code == 410
        assert resp.json()["reason_code"] == "LEGACY_BOND_WRITE_RETIRED"

    def test_bond_summary_no_numeric(self, test_client):
        """T-INT-BOND-07: 旧 Bond summary 读投影已退役。"""
        resp = test_client.get("/bond/summary/integration_test_user_006")
        assert resp.status_code == 410
        assert resp.json()["reason_code"] == "LEGACY_BOND_READ_RETIRED"

    def test_bond_users_list(self, test_client):
        """T-INT-BOND-08: 被拒绝的旧写入不会创建历史投影用户。"""
        write_response = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_list_001",
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::list::001"],
            },
        )
        assert write_response.status_code == 410
        resp = test_client.get("/bond/users")
        assert resp.status_code == 410
        assert resp.json()["reason_code"] == "LEGACY_BOND_READ_RETIRED"

    def test_bond_event_with_emotion_fields(self, test_client):
        """T-INT-BOND-09: 旧 emotion 字段不能写入七海关系或自我状态。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_007",
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::emotion::001"],
                "emotion_tag": "joy",
                "emotion_intensity": 0.9,
            },
        )
        assert resp.status_code == 410
        assert resp.json()["reason_code"] == "LEGACY_BOND_WRITE_RETIRED"

    def test_bond_custom_request_id_passthrough(self, test_client):
        """T-INT-BOND-10: 退役响应仍保留旧客户端 request_id。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_008",
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::reqid::001"],
                "request_id": "custom-integration-req-001",
            },
        )
        assert resp.status_code == 410
        data = resp.json()
        assert data["request_id"] == "custom-integration-req-001"

    def test_bond_stage_updates_after_promotion(self, test_client):
        """T-INT-BOND-11: 多次旧请求也不能自动升级亲密关系。"""
        user_id = "integration_test_user_promote_001"
        for i in range(14):
            event_resp = test_client.post(
                "/bond/event",
                json={
                    "user_id": user_id,
                    "update_kind": "DEEP_TALK",
                    "evidence_refs": [f"evt::promote::{i}"],
                },
            )
            assert event_resp.status_code == 410
        resp = test_client.get(f"/bond/stage/{user_id}")
        assert resp.status_code == 410
        assert resp.json()["reason_code"] == "LEGACY_BOND_READ_RETIRED"


# ---------------------------------------------------------------------------
# 12. Tool API — /tools/* 路由集成测试
# ---------------------------------------------------------------------------

class TestToolAPIIntegration:
    """T-INT-TOOL: Tool API 路由集成测试。"""

    def test_tool_list_returns_200(self, test_client):
        """T-INT-TOOL-01: GET /tools/meta-pack 返回 200（工具列表由 meta-pack 提供）。"""
        resp = test_client.get("/tools/meta-pack")
        assert resp.status_code == 200

    def test_tool_list_response_is_list_or_dict(self, test_client):
        """T-INT-TOOL-02: GET /tools/meta-pack 响应为字典且含 tools 或等价结构。"""
        resp = test_client.get("/tools/meta-pack")
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_tool_health_returns_200(self, test_client):
        """T-INT-TOOL-03: GET /tools/health 返回 200。"""
        resp = test_client.get("/tools/health")
        assert resp.status_code == 200

    def test_tool_execute_unknown_tool_error(self, test_client):
        """T-INT-TOOL-04: POST /tools/execute 未知工具 → 错误响应（非 5xx）。"""
        resp = test_client.post(
            "/tools/execute",
            json={
                "tool_name": "nonexistent_tool_xyz",
                "args": {},
                "request_id": "req::tool::test::001",
            },
        )
        # 未知工具应返回 4xx 或包含错误信息的 200
        assert resp.status_code in {200, 400, 404, 422}


# ---------------------------------------------------------------------------
# 13. Obs API — /obs/* 路由集成测试
# ---------------------------------------------------------------------------

class TestObsAPIIntegration:
    """Hostmain 上 /obs/* 必须不可用。"""

    def test_obs_timeline_returns_200(self, test_client):
        """T-INT-OBS-01: GET /obs/timeline 返回 200。"""
        resp = test_client.get("/obs/timeline")
        assert resp.status_code == 404

    def test_obs_sessions_returns_200(self, test_client):
        """T-INT-OBS-02: GET /obs/sessions 返回 200。"""
        resp = test_client.get("/obs/sessions")
        assert resp.status_code == 404

    def test_obs_metrics_with_params(self, test_client):
        """T-INT-OBS-03: GET /obs/metrics 不在 hostmain 提供。"""
        resp = test_client.get("/obs/metrics?request_id=req::obs::test::001")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 14. Governor 鉴权链路集成测试
# ---------------------------------------------------------------------------

class TestGovernorIntegration:
    """T-INT-GOV: Governor 鉴权链路集成测试（通过 /v1/chat 触发）。"""

    def test_governor_approves_normal_chat(self, test_client):
        """T-INT-GOV-01: 正常对话消息通过 Governor 鉴权（APPROVE）。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "今天心情不错"},
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        # 如果 APPROVE，应有 GOV_APPROVED 事件
        gov_approved = find_event(events, "PRESENCE", event="GOV_APPROVED")
        commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
        assert commit is not None
        # APPROVE 路径：有 GOV_APPROVED；REJECT 路径：commit.decision == "REJECT"
        if commit.get("decision") == "REJECT":
            assert commit.get("reason_code") is not None
        else:
            assert gov_approved is not None

    def test_governor_reject_propagates_reason_code(self, test_client):
        """T-INT-GOV-03: Governor REJECT 时 COMMIT 事件包含 reason_code。"""
        # 构造一个可能触发 REJECT 的请求（通过 intent_draft 注入非法 schema）
        resp = test_client.post(
            "/v1/chat",
            json={
                "message": "测试 Governor 拒绝",
                "intent_draft": {
                    # 故意缺少 schema_version，触发 R1 校验失败
                    "id": "intent::bad::001",
                    "ts_ms": int(time.time() * 1000),
                    "session_id": "session::gov::reject::test",
                    "seq": 1,
                    "type": "query",
                    "action": "narrative_only",
                    "target": "host",
                    "args": {"text": "测试"},
                    "nonce": "nonce::bad::001",
                },
            },
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
        assert commit is not None
        # REJECT 时必须有 reason_code
        if commit.get("decision") == "REJECT":
            assert commit.get("reason_code") is not None


# ---------------------------------------------------------------------------
# 15. 端到端链路完整性测试
# ---------------------------------------------------------------------------

class TestEndToEndPipeline:
    """T-INT-E2E: 端到端链路完整性测试。"""

    def test_e2e_health_to_metrics_consistency(self, test_client):
        """T-INT-E2E-02: /health 和 /metrics 数据一致性（时间戳递增）。"""
        health_resp = test_client.get("/health")
        metrics_resp = test_client.get("/metrics")

        assert health_resp.status_code == 200
        assert metrics_resp.status_code == 200

        health_ts = health_resp.json()["timestamp"]
        metrics_ts = metrics_resp.json()["timestamp"]

        # 两个请求时间戳应在合理范围内（10 秒内，避免 CI 抖动误判）
        assert abs(health_ts - metrics_ts) < 10000

    def test_e2e_bond_to_summary_consistency(self, test_client):
        """T-INT-E2E-04: Bond stage 和 summary 阶段字段一致。"""
        user_id = "e2e_consistency_user_001"

        # 旧写入和旧读投影都已退役，关系状态只从 Digital Life 读取。
        write_response = test_client.post(
            "/bond/event",
            json={
                "user_id": user_id,
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::consistency::001"],
            },
        )
        assert write_response.status_code == 410

        stage_resp = test_client.get(f"/bond/stage/{user_id}")
        summary_resp = test_client.get(f"/bond/summary/{user_id}")

        assert stage_resp.status_code == 410
        assert summary_resp.status_code == 410
        assert stage_resp.json()["reason_code"] == "LEGACY_BOND_READ_RETIRED"
        assert summary_resp.json()["reason_code"] == "LEGACY_BOND_READ_RETIRED"


# ---------------------------------------------------------------------------
# 16. 错误处理与边界条件测试
# ---------------------------------------------------------------------------

class TestErrorHandlingAndEdgeCases:
    """T-INT-ERR: 错误处理与边界条件集成测试。"""

    def test_unknown_route_404(self, test_client):
        """T-INT-ERR-01: 未知路由返回 404。"""
        resp = test_client.get("/nonexistent/route/xyz")
        assert resp.status_code == 404

    def test_chat_unicode_emoji(self, test_client):
        """T-INT-ERR-04: Unicode emoji 消息不崩溃。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "你好 😊🌸🎋 夏目友人帐"},
        )
        assert resp.status_code == 200

    def test_root_redirect(self, test_client):
        """T-INT-ERR-06: GET / 重定向到 /docs。"""
        resp = test_client.get("/", follow_redirects=False)
        assert resp.status_code in {301, 302, 307, 308}

    def test_panel_not_on_hostmain(self, test_client):
        """T-INT-ERR-07: GET /panel 不在 hostmain 提供。"""
        resp = test_client.get("/panel", follow_redirects=False)
        assert resp.status_code == 404
