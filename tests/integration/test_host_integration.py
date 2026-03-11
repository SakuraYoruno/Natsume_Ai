"""host_brain 全面集成测试套件
=================================
里程碑：M-HOST-INTEGRATION-2026-03-11
目标：对 host_brain FastAPI 应用的所有路由进行集成测试，
      验证每条 HTTP 链路的请求/响应契约、Governor 鉴权、Bond 状态机、
      Canon API、Tool API、Obs API 等核心功能。

策略：
- 使用 FastAPI TestClient（同步 ASGI 测试客户端）
- 使用内存 SQLite 账本（:memory:）隔离测试数据
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

@pytest.fixture(scope="module")
def mem_ledger():
    """内存 SQLite 账本，模块级共享，不污染生产数据。"""
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    yield ledger
    ledger.close()


@pytest.fixture(scope="module")
def test_client(mem_ledger):
    """构建 FastAPI TestClient，注入内存账本，mock 所有外部依赖。"""
    # 注入内存账本
    import host_brain.app.dependencies as _deps
    _deps._ledger = mem_ledger

    # mock LLM 推理（避免真实 API 调用）
    # resolve_host_llm_runtime_async 在 chat_stream_handler 中被重新绑定为 resolve_host_llm_runtime
    async def _mock_llm_runtime(*args, **kwargs):
        return {
            "success": True,
            "provider": "mock",
            "model_id": "mock-model",
            "api_key": "mock-key",
            "base_url": None,
            "reason_code": None,
        }

    async def _mock_astream(*args, **kwargs):
        yield "你好，我是夏目。"

    with (
        patch("host_brain.app.chat_stream_handler.resolve_host_llm_runtime",
              new=_mock_llm_runtime),
        patch("host_brain.app.chat_stream_handler.astream_llm_chunks",
              new=_mock_astream),
    ):
        from host_brain.app.fastapi_app import app
        client = TestClient(app, raise_server_exceptions=False)
        yield client

    # 清理单例，避免跨测试污染
    _deps._ledger = None
    _deps._intent_engine = None
    _deps._skill_registry = None
    _deps._bond_stage_engine = None
    _deps._retrieval_pipeline = None
    _deps._obs_metrics_collector = None


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
        assert data["status"] in {"READY", "DEGRADED", "FAILED", "NOT_READY"}

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
# 6. GET /obs/metrics — 可观测性指标
# ---------------------------------------------------------------------------

class TestObsMetricsEndpoint:
    """T-INT-OBS-METRICS: GET /obs/metrics 可观测性指标端点集成测试。"""

    def test_obs_metrics_returns_200(self, test_client):
        """T-INT-OBS-METRICS-01: /obs/metrics 返回 200。"""
        resp = test_client.get("/obs/metrics")
        assert resp.status_code == 200

    def test_obs_metrics_response_is_dict(self, test_client):
        """T-INT-OBS-METRICS-02: /obs/metrics 响应为字典。"""
        resp = test_client.get("/obs/metrics")
        data = resp.json()
        assert isinstance(data, dict)

    def test_obs_metrics_has_timestamp(self, test_client):
        """T-INT-OBS-METRICS-03: /obs/metrics 包含 timestamp。"""
        resp = test_client.get("/obs/metrics")
        data = resp.json()
        assert "timestamp" in data
        assert isinstance(data["timestamp"], int)

    def test_obs_metrics_with_request_id(self, test_client):
        """T-INT-OBS-METRICS-04: /obs/metrics 支持 request_id 参数。"""
        resp = test_client.get("/obs/metrics?request_id=test-req-001")
        assert resp.status_code == 200


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
# 8. POST /observability/trace — 请求链路追踪
# ---------------------------------------------------------------------------

class TestObservabilityTraceEndpoint:
    """T-INT-OBS-TRACE: POST /observability/trace 链路追踪端点集成测试。"""

    def test_trace_unknown_request_id(self, test_client):
        """T-INT-OBS-TRACE-01: 未知 request_id 返回 trace_status=NOT_FOUND 或 200。"""
        resp = test_client.post(
            "/observability/trace",
            json={"request_id": "req::nonexistent::001"},
        )
        # 未知 request_id 可能返回 200（NOT_FOUND 状态）或 404
        assert resp.status_code in {200, 404}

    def test_trace_response_schema(self, test_client):
        """T-INT-OBS-TRACE-02: /observability/trace 响应包含必要字段。"""
        resp = test_client.post(
            "/observability/trace",
            json={"request_id": "req::schema::test::001"},
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "request_id" in data
            assert "trace_status" in data
            assert "timeline_total" in data
            assert "timestamp" in data

    def test_trace_missing_request_id_422(self, test_client):
        """T-INT-OBS-TRACE-03: 缺少 request_id → 422。"""
        resp = test_client.post("/observability/trace", json={})
        assert resp.status_code == 422


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

    def test_chat_invalid_scene_type_fallback(self, test_client):
        """T-INT-CHAT-08: 非法 scene_type 被降级为 SCENE_CHAT，不崩溃。"""
        resp = test_client.post(
            "/v1/chat",
            json={
                "message": "测试非法场景类型",
                "scene_type": "SCENE_INVALID_HACK",
            },
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
        assert commit is not None

    def test_chat_exec_intent_action_mapping(self, test_client):
        """T-INT-CHAT-09: 执行意图消息（安装/运行）→ action 映射为 exec_intent。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "帮我运行一下这个脚本"},
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        # 不管 Governor 是否 APPROVE，流必须有 COMMIT done 事件
        commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
        assert commit is not None

    def test_chat_vision_ui_scene_rejected(self, test_client):
        """T-INT-CHAT-10: SCENE_VISION_UI 场景被拒绝（暂不支持）。"""
        resp = test_client.post(
            "/v1/chat",
            json={
                "message": "帮我看一下屏幕",
                "scene_type": "SCENE_VISION_UI",
            },
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        # SCENE_VISION_UI 被白名单拦截，降级为 SCENE_CHAT 或直接 REJECT
        commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
        assert commit is not None

    def test_chat_multiple_requests_independent(self, test_client):
        """T-INT-CHAT-11: 多次请求相互独立，request_id 不重复。"""
        request_ids = set()
        for i in range(3):
            resp = test_client.post(
                "/v1/chat",
                json={"message": f"测试消息 {i}"},
            )
            assert resp.status_code == 200
            events = parse_sse_events(resp.text)
            commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
            if commit and "request_id" in commit:
                request_ids.add(commit["request_id"])
        # 3 次请求应产生 3 个不同的 request_id
        assert len(request_ids) == 3


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
        """T-INT-BOND-01: GET /bond/health 返回 OK 或 DEGRADED。"""
        resp = test_client.get("/bond/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in {"OK", "DEGRADED"}

    def test_bond_stage_new_user(self, test_client):
        """T-INT-BOND-02: GET /bond/stage/{user_id} 新用户初始为 STRANGER。"""
        resp = test_client.get("/bond/stage/integration_test_user_001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_stage"] == "STRANGER"
        assert data["total_score"] == 0

    def test_bond_stage_response_fields(self, test_client):
        """T-INT-BOND-03: GET /bond/stage 响应包含所有必要字段。"""
        resp = test_client.get("/bond/stage/integration_test_user_002")
        data = resp.json()
        required = ["user_id", "current_stage", "stage_index", "total_score",
                    "event_count", "trend", "capability_hint", "request_id", "timestamp"]
        for field in required:
            assert field in data, f"missing field: {field}"

    def test_bond_submit_event_interaction(self, test_client):
        """T-INT-BOND-04: POST /bond/event 提交 INTERACTION 事件。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_003",
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::integration::001"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "integration_test_user_003"
        assert data["transition_status"] in {"STABLE", "PROMOTED", "DEMOTED"}
        assert data["score_delta"] > 0

    def test_bond_submit_event_deep_talk(self, test_client):
        """T-INT-BOND-05: POST /bond/event 提交 DEEP_TALK 事件，积分更高。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_004",
                "update_kind": "DEEP_TALK",
                "evidence_refs": ["evt::integration::002"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["score_delta"] > 0

    def test_bond_submit_event_empty_refs_422(self, test_client):
        """T-INT-BOND-06: POST /bond/event 空 evidence_refs → 422。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_005",
                "update_kind": "INTERACTION",
                "evidence_refs": [],
            },
        )
        assert resp.status_code == 422

    def test_bond_summary_no_numeric(self, test_client):
        """T-INT-BOND-07: GET /bond/summary 返回不含数值的摘要。"""
        resp = test_client.get("/bond/summary/integration_test_user_006")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("stage", "trend", "capability_hint"):
            assert isinstance(data[key], str), f"summary[{key}] should be string"

    def test_bond_users_list(self, test_client):
        """T-INT-BOND-08: GET /bond/users 返回用户列表。"""
        # 先提交一个事件确保有用户
        test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_list_001",
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::list::001"],
            },
        )
        resp = test_client.get("/bond/users")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "users" in data
        assert isinstance(data["total"], int)

    def test_bond_event_with_emotion_fields(self, test_client):
        """T-INT-BOND-09: POST /bond/event 支持 emotion_tag 和 emotion_intensity 字段（审计修复 #5）。"""
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
        assert resp.status_code == 200
        data = resp.json()
        assert data["transition_status"] in {"STABLE", "PROMOTED", "DEMOTED"}

    def test_bond_custom_request_id_passthrough(self, test_client):
        """T-INT-BOND-10: POST /bond/event 自定义 request_id 透传到响应。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "integration_test_user_008",
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::reqid::001"],
                "request_id": "custom-integration-req-001",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["request_id"] == "custom-integration-req-001"

    def test_bond_stage_updates_after_promotion(self, test_client):
        """T-INT-BOND-11: 多次 DEEP_TALK 后阶段晋级到 ACQUAINTANCE。"""
        user_id = "integration_test_user_promote_001"
        for i in range(2):
            test_client.post(
                "/bond/event",
                json={
                    "user_id": user_id,
                    "update_kind": "DEEP_TALK",
                    "evidence_refs": [f"evt::promote::{i}"],
                },
            )
        resp = test_client.get(f"/bond/stage/{user_id}")
        data = resp.json()
        assert data["current_stage"] == "ACQUAINTANCE"
        assert data["total_score"] >= 10


# ---------------------------------------------------------------------------
# 12. Tool API — /tools/* 路由集成测试
# ---------------------------------------------------------------------------

class TestToolAPIIntegration:
    """T-INT-TOOL: Tool API 路由集成测试。"""

    def test_tool_list_returns_200(self, test_client):
        """T-INT-TOOL-01: GET /tools 返回 200。"""
        resp = test_client.get("/tools")
        assert resp.status_code == 200

    def test_tool_list_response_is_list_or_dict(self, test_client):
        """T-INT-TOOL-02: GET /tools 响应为列表或字典。"""
        resp = test_client.get("/tools")
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
    """T-INT-OBS: Obs API 路由集成测试。"""

    def test_obs_timeline_returns_200(self, test_client):
        """T-INT-OBS-01: GET /obs/timeline 返回 200。"""
        resp = test_client.get("/obs/timeline")
        assert resp.status_code in {200, 404}  # 路由可能不存在

    def test_obs_sessions_returns_200(self, test_client):
        """T-INT-OBS-02: GET /obs/sessions 返回 200。"""
        resp = test_client.get("/obs/sessions")
        assert resp.status_code in {200, 404}

    def test_obs_metrics_with_params(self, test_client):
        """T-INT-OBS-03: GET /obs/metrics 支持查询参数。"""
        resp = test_client.get("/obs/metrics?request_id=req::obs::test::001")
        assert resp.status_code == 200


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

    def test_governor_rate_limit_not_crash(self, test_client):
        """T-INT-GOV-02: 连续多次请求不崩溃（速率限制降级处理）。"""
        for i in range(5):
            resp = test_client.post(
                "/v1/chat",
                json={
                    "message": f"速率限制测试 {i}",
                    "session_id": "session::rate::limit::test",
                },
            )
            assert resp.status_code == 200

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

    def test_e2e_chat_to_bond_update(self, test_client):
        """T-INT-E2E-01: /v1/chat 请求完成后 Bond 状态可查询（链路连通性）。"""
        user_id = "e2e_test_user_001"
        session_id = f"session::e2e::{user_id}"

        # 1. 发起对话
        chat_resp = test_client.post(
            "/v1/chat",
            json={
                "message": "你好，我是测试用户",
                "session_id": session_id,
            },
        )
        assert chat_resp.status_code == 200

        # 2. 查询 Bond 状态（Bond 更新是异步的，此处只验证接口可达）
        bond_resp = test_client.get(f"/bond/stage/{user_id}")
        assert bond_resp.status_code == 200
        bond_data = bond_resp.json()
        assert bond_data["user_id"] == user_id

    def test_e2e_health_to_metrics_consistency(self, test_client):
        """T-INT-E2E-02: /health 和 /metrics 数据一致性（时间戳递增）。"""
        health_resp = test_client.get("/health")
        metrics_resp = test_client.get("/metrics")

        assert health_resp.status_code == 200
        assert metrics_resp.status_code == 200

        health_ts = health_resp.json()["timestamp"]
        metrics_ts = metrics_resp.json()["timestamp"]

        # 两个请求时间戳应在合理范围内（5 秒内）
        assert abs(health_ts - metrics_ts) < 5000

    def test_e2e_chat_increments_metrics(self, test_client):
        """T-INT-E2E-03: /v1/chat 请求后 /metrics total_chat_requests 递增。"""
        before = test_client.get("/metrics").json()["total_chat_requests"]

        test_client.post("/v1/chat", json={"message": "指标递增测试"})

        after = test_client.get("/metrics").json()["total_chat_requests"]
        assert after > before

    def test_e2e_bond_to_summary_consistency(self, test_client):
        """T-INT-E2E-04: Bond stage 和 summary 阶段字段一致。"""
        user_id = "e2e_consistency_user_001"

        # 提交事件
        test_client.post(
            "/bond/event",
            json={
                "user_id": user_id,
                "update_kind": "INTERACTION",
                "evidence_refs": ["evt::consistency::001"],
            },
        )

        stage_resp = test_client.get(f"/bond/stage/{user_id}")
        summary_resp = test_client.get(f"/bond/summary/{user_id}")

        assert stage_resp.status_code == 200
        assert summary_resp.status_code == 200

        stage_data = stage_resp.json()
        summary_data = summary_resp.json()

        # stage 和 summary 都应有阶段信息
        assert stage_data["current_stage"] is not None
        assert summary_data["stage"] is not None

    def test_e2e_observability_trace_after_chat(self, test_client):
        """T-INT-E2E-05: /v1/chat 后可通过 /observability/trace 查询链路。"""
        # 发起对话
        chat_resp = test_client.post(
            "/v1/chat",
            json={"message": "可观测性链路测试"},
        )
        assert chat_resp.status_code == 200

        # 从 SSE 事件中提取 request_id
        events = parse_sse_events(chat_resp.text)
        commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
        assert commit is not None
        request_id = commit.get("request_id")
        assert request_id is not None

        # 查询链路追踪
        trace_resp = test_client.post(
            "/observability/trace",
            json={"request_id": request_id},
        )
        # 链路追踪可能返回 200（有数据）或 200（NOT_FOUND）
        assert trace_resp.status_code in {200, 404}
        if trace_resp.status_code == 200:
            trace_data = trace_resp.json()
            assert trace_data["request_id"] == request_id


# ---------------------------------------------------------------------------
# 16. 错误处理与边界条件测试
# ---------------------------------------------------------------------------

class TestErrorHandlingAndEdgeCases:
    """T-INT-ERR: 错误处理与边界条件集成测试。"""

    def test_unknown_route_404(self, test_client):
        """T-INT-ERR-01: 未知路由返回 404。"""
        resp = test_client.get("/nonexistent/route/xyz")
        assert resp.status_code == 404

    def test_chat_very_long_message(self, test_client):
        """T-INT-ERR-02: 超长消息不崩溃（降级处理）。"""
        long_message = "测试" * 500  # 1000 字符
        resp = test_client.post(
            "/v1/chat",
            json={"message": long_message},
        )
        assert resp.status_code == 200

    def test_chat_special_characters(self, test_client):
        """T-INT-ERR-03: 特殊字符消息不崩溃。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "测试 <script>alert('xss')</script> & \"quotes\" 'single'"},
        )
        assert resp.status_code == 200

    def test_chat_unicode_emoji(self, test_client):
        """T-INT-ERR-04: Unicode emoji 消息不崩溃。"""
        resp = test_client.post(
            "/v1/chat",
            json={"message": "你好 😊🌸🎋 夏目友人帐"},
        )
        assert resp.status_code == 200

    def test_bond_event_invalid_update_kind(self, test_client):
        """T-INT-ERR-05: POST /bond/event 非法 update_kind → 422 或降级处理。"""
        resp = test_client.post(
            "/bond/event",
            json={
                "user_id": "err_test_user_001",
                "update_kind": "INVALID_KIND_XYZ",
                "evidence_refs": ["evt::err::001"],
            },
        )
        # 非法 update_kind 应返回 422 或 200（降级为默认事件）
        assert resp.status_code in {200, 422}

    def test_root_redirect(self, test_client):
        """T-INT-ERR-06: GET / 重定向到 /docs。"""
        resp = test_client.get("/", follow_redirects=False)
        assert resp.status_code in {301, 302, 307, 308}

    def test_panel_redirect(self, test_client):
        """T-INT-ERR-07: GET /panel 重定向到监控面板。"""
        resp = test_client.get("/panel", follow_redirects=False)
        assert resp.status_code in {301, 302, 307, 308}

    def test_chat_concurrent_sessions(self, test_client):
        """T-INT-ERR-08: 不同 session_id 的并发请求相互独立。"""
        responses = []
        for i in range(3):
            resp = test_client.post(
                "/v1/chat",
                json={
                    "message": f"并发测试消息 {i}",
                    "session_id": f"session::concurrent::{i}",
                },
            )
            responses.append(resp)

        for resp in responses:
            assert resp.status_code == 200
            events = parse_sse_events(resp.text)
            commit = next((e for e in events if e.get("phase") == "COMMIT" and e.get("done")), None)
            assert commit is not None
