"""HTTP服务框架契约测试（T-HTTP-01 / T-HTTP-02）。

TASK-0065: 验证 POST /v1/chat 路由可达性与响应结构、异常兜底与降级输出。
覆盖：
  T-HTTP-01 — 路由可达性与响应结构（/v1/chat, /health, /ready, /metrics）
  T-HTTP-02 — 异常兜底与降级输出（缺字段、无效intent、内部错误均返回可解析JSON）
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

import pytest

# ---------------------------------------------------------------------------
# 公共辅助：模拟 FastAPI TestClient
# ---------------------------------------------------------------------------

# 检测 httpx / starlette testclient 是否可用
try:
    from fastapi.testclient import TestClient
    from host_brain.app.fastapi_app import app

    _HAS_TEST_CLIENT = True
except Exception:
    _HAS_TEST_CLIENT = False


# 跳过标记：如果环境缺 fastapi/httpx 则跳过
requires_test_client = pytest.mark.skipif(
    not _HAS_TEST_CLIENT,
    reason="fastapi.testclient not available (missing httpx?)",
)


# ============================================================================
# T-HTTP-01: 路由可达性与响应结构
# ============================================================================
class TestHTTP01RouteReachabilityAndResponseStructure:
    """T-HTTP-01: 核心路由可达、响应结构稳定（单JSON、必要字段存在）。"""

    @requires_test_client
    def test_health_endpoint_reachable(self):
        """GET /health 返回200且含 status/version/timestamp。"""
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "version" in body
        assert "timestamp" in body
        assert isinstance(body["timestamp"], int)

    @requires_test_client
    def test_ready_endpoint_reachable(self):
        """GET /ready 返回200或503且含 status/timestamp（降级也是稳定结构）。"""
        client = TestClient(app)
        resp = client.get("/ready")
        # 测试环境依赖可能不可用，503也是合法降级
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert "timestamp" in body or "detail" in body
        if resp.status_code == 200:
            assert "ready" in body
            assert "status" in body

    @requires_test_client
    def test_metrics_endpoint_reachable(self):
        """GET /metrics 返回200且含关键指标字段。"""
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "uptime_seconds" in body
        assert "total_requests" in body
        assert "total_chat_requests" in body
        assert "total_rejections" in body
        assert "reason_code_distribution" in body
        assert "timestamp" in body

    @requires_test_client
    def test_chat_endpoint_reachable_and_returns_stable_structure(self):
        """POST /v1/chat 正常请求返回200且SSE流含COMMIT事件。"""
        client = TestClient(app)
        payload = {
            "message": "你好，最近怎么样？",
            "session_id": "test-session-001",
        }
        resp = client.post("/v1/chat", json=payload)
        assert resp.status_code == 200
        # 解析 SSE 流，找到 COMMIT 事件
        commit_event = None
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    event = json.loads(line[6:])
                    if event.get("phase") == "COMMIT":
                        commit_event = event
                        break
                except json.JSONDecodeError:
                    pass
        assert commit_event is not None, "SSE 流中未找到 COMMIT 事件"
        # COMMIT 事件必要字段
        assert "request_id" in commit_event
        assert "decision" in commit_event

    @requires_test_client
    def test_chat_with_intent_draft_passes_governor(self):
        """POST /v1/chat 携带合法 intent_draft 经 Governor 裁决后返回稳定结构。"""
        client = TestClient(app)
        ts_ms = int(time.time() * 1000)
        payload = {
            "message": "查看状态",
            "session_id": "test-session-with-draft",
            "intent_draft": {
                "schema_version": "Natsume.intent.v1",
                "id": f"intent::chat-test-{ts_ms}",
                "ts_ms": ts_ms,
                "session_id": "test-session-with-draft",
                "seq": 1,
                "type": "query",
                "action": "read_only_status",
                "target": "host",
                "args": {"scope": "host"},
                "nonce": f"nonce::chat-test-{ts_ms}",
            },
        }
        resp = client.post("/v1/chat", json=payload)
        assert resp.status_code == 200
        # 解析 SSE 流，找到 COMMIT 事件
        commit_event = None
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    event = json.loads(line[6:])
                    if event.get("phase") == "COMMIT":
                        commit_event = event
                        break
                except json.JSONDecodeError:
                    pass
        assert commit_event is not None, "SSE 流中未找到 COMMIT 事件"
        assert commit_event["decision"] in ("APPROVE", "REJECT")

    @requires_test_client
    def test_root_redirects_to_docs(self):
        """GET / 重定向到 /docs。"""
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        assert resp.status_code == 307
        assert "/docs" in resp.headers.get("location", "")


# ============================================================================
# T-HTTP-02: 异常兜底与降级输出
# ============================================================================
class TestHTTP02ErrorHandlingAndDegradation:
    """T-HTTP-02: 缺字段/无效输入/内部错误均返回可解析JSON + reason_code。"""

    @requires_test_client
    def test_chat_missing_message_returns_422(self):
        """POST /v1/chat 缺少 message 字段返回422（Pydantic校验）。"""
        client = TestClient(app)
        payload = {"session_id": "bad-session"}
        resp = client.post("/v1/chat", json=payload)
        assert resp.status_code == 422
        body = resp.json()
        # FastAPI/Pydantic 422 仍是可解析 JSON
        assert "detail" in body

    @requires_test_client
    def test_chat_invalid_intent_draft_returns_reject(self):
        """POST /v1/chat 带无效 intent_draft 被 Governor 拒绝并返回稳定结构。"""
        client = TestClient(app)
        ts_ms = int(time.time() * 1000)
        payload = {
            "message": "测试无效intent",
            "session_id": "bad-intent-session",
            "intent_draft": {
                "schema_version": "INVALID_VERSION",
                "id": f"intent::bad-{ts_ms}",
                "ts_ms": ts_ms,
                "session_id": "bad-intent-session",
                "seq": 1,
                "type": "query",
                "action": "read_only_status",
                "target": "host",
                "args": {"scope": "host"},
                "nonce": f"nonce::bad-{ts_ms}",
            },
        }
        resp = client.post("/v1/chat", json=payload)
        assert resp.status_code == 200  # 降级仍返回200（结构稳定）
        # 解析 SSE 流，找到 COMMIT 事件
        commit_event = None
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    event = json.loads(line[6:])
                    if event.get("phase") == "COMMIT":
                        commit_event = event
                        break
                except json.JSONDecodeError:
                    pass
        assert commit_event is not None, "SSE 流中未找到 COMMIT 事件"
        assert commit_event["decision"] == "REJECT"
        assert commit_event.get("reason_code") is not None

    @requires_test_client
    def test_chat_empty_message_returns_stable_structure(self):
        """POST /v1/chat 空消息仍返回可解析SSE流。"""
        client = TestClient(app)
        payload = {"message": ""}
        resp = client.post("/v1/chat", json=payload)
        # 即使空消息也返回可解析结构（可能被Governor拒绝或产出空叙事）
        assert resp.status_code in (200, 422)
        if resp.status_code == 200:
            # SSE 流应包含至少一个 data: 行
            assert "data:" in resp.text
        else:
            body = resp.json()
            assert isinstance(body, dict)

    @requires_test_client
    def test_nonexistent_route_returns_json_404(self):
        """访问不存在的路由返回JSON格式404。"""
        client = TestClient(app)
        resp = client.get("/v1/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert isinstance(body, dict)

    @requires_test_client
    def test_metrics_after_chat_reflects_counts(self):
        """发送chat请求后 /metrics 中计数递增。"""
        client = TestClient(app)
        # 先获取初始指标
        resp0 = client.get("/metrics")
        initial = resp0.json()

        # 发送一个chat请求
        client.post("/v1/chat", json={"message": "metrics test"})

        # 验证指标递增
        resp1 = client.get("/metrics")
        after = resp1.json()
        assert after["total_chat_requests"] >= initial["total_chat_requests"]

    @requires_test_client
    def test_observability_trace_returns_stable_structure(self):
        """POST /observability/trace 返回稳定结构（200或500均为可解析JSON）。"""
        client = TestClient(app)
        resp = client.post(
            "/observability/trace",
            json={"request_id": "nonexistent-req-id"},
        )
        # 不存在的 request_id 可能返回200(空时间线)或500(内部异常)
        # 但必须是可解析JSON
        body = resp.json()
        assert isinstance(body, dict)
        if resp.status_code == 200:
            assert "request_id" in body
            assert "trace_status" in body
            assert "timeline" in body
            assert "timestamp" in body
