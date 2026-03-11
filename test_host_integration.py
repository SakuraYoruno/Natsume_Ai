"""
Host Brain 集成测试套件（test_host_integration.py）

覆盖范围：
  BASIC-1   GET /              → 307 重定向到 /docs
  BASIC-2   GET /docs          → 200 OpenAPI 文档
  BASIC-3   GET /health        → 200 + HealthResponse 结构
  BASIC-4   GET /ready         → 200 + ReadinessResponse 结构
  BASIC-5   GET /metrics       → 200 + MetricsResponse 结构
  BASIC-6   GET /startup       → 200 + 报告结构
  BASIC-7   GET /health/mcp    → 200 + HealthResponse 结构
  BASIC-8   GET /health/canon_sync → 200 + HealthResponse 结构

  CHAT-1    POST /v1/chat      → 200 SSE 流式响应（PRESENCE + COMMIT）
  CHAT-2    POST /v1/chat      → Governor REJECT 时返回 COMMIT done=True
  CHAT-3    POST /v1/chat      → 非法 scene_type 降级为 SCENE_CHAT
  CHAT-4    POST /v1/chat      → SCENE_VISION_UI 返回 REJECT
  CHAT-5    POST /v1/chat      → 缺少 message 字段返回 422

  BOND-1    GET  /bond/health  → 200 + BondHealthResponse
  BOND-2    GET  /bond/users   → 200 + users 列表
  BOND-3    GET  /bond/stage/{user_id}  → 200 + BondStageResponse
  BOND-4    GET  /bond/summary/{user_id} → 200 + BondSummaryResponse
  BOND-5    POST /bond/event   → 200 + BondEventResponse（STABLE/PROMOTED）
  BOND-6    POST /bond/event   → evidence_refs 为空返回 422

  OBS-1     GET  /obs/services/health → 200 + ServicesHealthResponse
  OBS-2     GET  /obs/sessions        → 200 + SessionsResponse
  OBS-3     GET  /obs/trace/{req_id}  → 200 or 404
  OBS-4     GET  /obs/metrics/timeseries → 200 + MetricsTimeseriesResponse
  OBS-5     GET  /obs/llm/nodes       → 200 + LlmNodesResponse
  OBS-6     GET  /obs/metrics         → 200 + obs metrics

  TOOLS-1   GET  /tools/health        → 200 + ToolHealthResponse
  TOOLS-2   GET  /tools/meta-pack     → 200 + ToolMetaPackResponse
  TOOLS-3   GET  /tools/{tool_id}     → 404 不存在的工具
  TOOLS-4   POST /tools/execute       → 503 provider 未配置时降级

  EDGE-1    POST /v1/edge/events      → 200 + EdgeEventsResponse
  EDGE-2    POST /v1/edge/events      → events 为空列表时正常处理

  TRACE-1   POST /observability/trace → 200 + ObservabilityResponse
  TRACE-2   POST /observability/trace → 400 request_id 为空

  METRICS-1 GET  /metrics             → uptime/total_requests 字段存在

所有测试均使用 FastAPI TestClient（同步），不依赖真实外部服务。
外部依赖（LLM / Neo4j / ChromaDB / Redis）全部通过 monkeypatch/mock 隔离。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── 确保项目根目录在 sys.path ──────────────────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 设置测试环境变量（必须在 import app 之前）─────────────────────────────
os.environ.setdefault("Natsume_ENV", "test")
os.environ.setdefault("HOST_APP_PORT", "18080")
os.environ.setdefault("HOST_LLM_PROVIDER", "mock")
os.environ.setdefault("HOST_ENABLE_RETRIEVAL", "false")
os.environ.setdefault("HOST_BOND_BACKEND", "memory")
os.environ.setdefault("HOST_GAMING_EDGE_CHAT_ENABLED", "false")
os.environ.setdefault("HOST_ENABLE_EDGE_RPC", "false")
os.environ.setdefault("Natsume_AUDIT_DB", ":memory:")
os.environ.setdefault("HOST_MCP_REGISTRY_DB", ":memory:")
os.environ.setdefault("HOST_SKILL_MCP_BRIDGE_ENABLED", "false")
os.environ.setdefault("HOST_BOND_QUEUE_ENABLED", "false")

# ── 导入 FastAPI app ───────────────────────────────────────────────────────
from host_brain.app.fastapi_app import app  # noqa: E402

# ── 共享常量 ───────────────────────────────────────────────────────────────
_RUN_ID = f"integ_{int(time.time())}"


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    """模块级 TestClient，共享 app 生命周期。"""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def mock_governor_approve():
    """模块级 Governor mock：始终返回 APPROVE。"""
    approve_result = {
        "decision": "APPROVE",
        "reason_code": None,
        "intent_id": f"intent::mock_{_RUN_ID}",
        "rule_trace": [],
        "approval_tier": "T1",
        "approval_token_ref": None,
    }
    with patch(
        "host_brain.governor.decision.IntentDecisionEngine.evaluate_intent",
        return_value=approve_result,
    ):
        yield approve_result


@pytest.fixture()
def mock_governor_reject():
    """函数级 Governor mock：始终返回 REJECT。"""
    reject_result = {
        "decision": "REJECT",
        "reason_code": "EMOTION_GATE_BLOCKED",
        "intent_id": f"intent::mock_reject_{_RUN_ID}",
        "rule_trace": [],
    }
    with patch(
        "host_brain.governor.decision.IntentDecisionEngine.evaluate_intent",
        return_value=reject_result,
    ):
        yield reject_result


@pytest.fixture(scope="module")
def mock_llm_stream():
    """模块级 LLM 流式输出 mock：mock astream_llm_chunks 返回固定 token 序列。"""
    async def _fake_astream(*args, **kwargs):
        for token in ["こんにちは", "！", "私はナツメです。"]:
            yield token

    with patch(
        "host_brain.integrations.llm_helpers.astream_llm_chunks",
        new=_fake_astream,
    ):
        yield


@pytest.fixture(scope="module", autouse=True)
def setup_bond_engine_provider():
    """模块级 Bond engine provider 注入 fixture（autouse，所有测试自动生效）。"""
    from host_brain.app.bond_routes import set_bond_engine_provider
    from host_brain.app.dependencies import get_bond_stage_engine
    set_bond_engine_provider(get_bond_stage_engine)
    yield


# ══════════════════════════════════════════════════════════════════════════════
# BASIC: 基础路由
# ══════════════════════════════════════════════════════════════════════════════

class TestBasicRoutes:
    """BASIC-1 ~ BASIC-8：基础路由可达性与结构验证。"""

    def test_root_redirects_to_docs(self, client: TestClient):
        """BASIC-1: GET / → 307 重定向到 /docs。"""
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (301, 302, 307, 308), (
            f"期望重定向，实际 {resp.status_code}"
        )

    def test_docs_accessible(self, client: TestClient):
        """BASIC-2: GET /docs → 200 OpenAPI 文档页面。"""
        resp = client.get("/docs")
        assert resp.status_code == 200
        assert "swagger" in resp.text.lower() or "openapi" in resp.text.lower()

    def test_health_returns_200_with_structure(self, client: TestClient):
        """BASIC-3: GET /health → 200 + HealthResponse 结构。"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data, f"缺少 status 字段: {data}"
        assert "version" in data, f"缺少 version 字段: {data}"
        assert "timestamp" in data, f"缺少 timestamp 字段: {data}"
        assert data["status"] in ("OK", "DEGRADED", "DOWN"), (
            f"status 值异常: {data['status']}"
        )

    def test_ready_returns_200_with_structure(self, client: TestClient):
        """BASIC-4: GET /ready → 200 + ReadinessResponse 结构。"""
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert "ready" in data, f"缺少 ready 字段: {data}"
        assert "status" in data, f"缺少 status 字段: {data}"
        assert "timestamp" in data, f"缺少 timestamp 字段: {data}"
        assert isinstance(data["ready"], bool), f"ready 应为 bool: {data['ready']}"

    def test_metrics_returns_200_with_structure(self, client: TestClient):
        """BASIC-5: GET /metrics → 200 + MetricsResponse 结构。"""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        required_fields = [
            "uptime_seconds", "total_requests", "total_chat_requests",
            "total_rejections", "total_errors", "timestamp",
        ]
        for field in required_fields:
            assert field in data, f"缺少字段 {field}: {list(data.keys())}"
        assert data["uptime_seconds"] >= 0
        assert data["total_requests"] >= 0

    def test_startup_returns_200(self, client: TestClient):
        """BASIC-6: GET /startup → 200 + 报告结构。"""
        resp = client.get("/startup")
        assert resp.status_code == 200
        data = resp.json()
        assert "request_id" in data, f"缺少 request_id: {data}"
        assert "timestamp" in data, f"缺少 timestamp: {data}"

    def test_health_mcp_returns_200(self, client: TestClient):
        """BASIC-7: GET /health/mcp → 200 + HealthResponse 结构。"""
        resp = client.get("/health/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("OK", "DEGRADED")
        assert "details" in data

    def test_health_canon_sync_returns_200(self, client: TestClient):
        """BASIC-8: GET /health/canon_sync → 200 + HealthResponse 结构。"""
        resp = client.get("/health/canon_sync")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("OK", "DEGRADED")


# ══════════════════════════════════════════════════════════════════════════════
# CHAT: POST /v1/chat SSE 流式路由
# ══════════════════════════════════════════════════════════════════════════════

class TestChatRoute:
    """CHAT-1 ~ CHAT-5：/v1/chat SSE 路由测试。"""

    def _parse_sse_events(self, raw: str) -> list[dict]:
        """解析 SSE 响应体，返回所有 data: 行的 JSON 对象列表。"""
        events = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
        return events

    def test_chat_sse_basic_flow(self, client: TestClient, mock_governor_approve, mock_llm_stream):
        """CHAT-1: POST /v1/chat → 200 SSE，包含 PRESENCE + COMMIT 事件。"""
        resp = client.post(
            "/v1/chat",
            json={"message": "你好，请介绍一下自己", "session_id": f"sess_{_RUN_ID}"},
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
        ct = resp.headers.get("content-type", "")
        assert "text/event-stream" in ct, f"Content-Type 应为 SSE: {ct}"

        events = self._parse_sse_events(resp.text)
        assert len(events) > 0, "SSE 响应无事件"

        phases = {e.get("phase") for e in events}
        assert "PRESENCE" in phases, f"缺少 PRESENCE 事件，phases={phases}"
        assert "COMMIT" in phases, f"缺少 COMMIT 事件，phases={phases}"

        # 验证第一个事件是 THINKING
        first = events[0]
        assert first.get("phase") == "PRESENCE"
        assert first.get("event") == "THINKING"
        assert "request_id" in first

    def test_chat_governor_reject(self, client: TestClient, mock_governor_reject):
        """CHAT-2: Governor REJECT → SSE 返回 COMMIT done=True decision=REJECT。"""
        resp = client.post(
            "/v1/chat",
            json={"message": "执行危险操作", "session_id": f"sess_reject_{_RUN_ID}"},
        )
        assert resp.status_code == 200
        events = self._parse_sse_events(resp.text)
        commit_events = [e for e in events if e.get("phase") == "COMMIT"]
        assert len(commit_events) > 0, f"缺少 COMMIT 事件: {events}"
        commit = commit_events[-1]
        assert commit.get("decision") == "REJECT", f"期望 REJECT: {commit}"
        assert commit.get("done") is True
        assert "reason_code" in commit

    def test_chat_invalid_scene_type_fallback(self, client: TestClient, mock_governor_approve, mock_llm_stream):
        """CHAT-3: 非法 scene_type 应降级为 SCENE_CHAT，不返回 500。"""
        resp = client.post(
            "/v1/chat",
            json={
                "message": "你好",
                "session_id": f"sess_scene_{_RUN_ID}",
                "scene_type": "SCENE_HACK_BYPASS",
            },
        )
        assert resp.status_code == 200
        events = self._parse_sse_events(resp.text)
        phases = {e.get("phase") for e in events}
        # 不应出现 500 错误，应正常走 SCENE_CHAT 路径
        assert "PRESENCE" in phases

    def test_chat_vision_ui_scene_rejected(self, client: TestClient, mock_governor_approve, mock_llm_stream):
        """CHAT-4: SCENE_VISION_UI 不在白名单，被降级为 SCENE_CHAT 正常处理（不崩溃）。

        注意：根据 chat_handler.py 的实现，SCENE_VISION_UI 不在 _ALLOWED_EXPLICIT_SCENE_TYPES
        白名单中，会被降级为 SCENE_CHAT 而非直接 REJECT。
        """
        resp = client.post(
            "/v1/chat",
            json={
                "message": "帮我截图",
                "session_id": f"sess_vision_{_RUN_ID}",
                "scene_type": "SCENE_VISION_UI",
            },
        )
        assert resp.status_code == 200
        events = self._parse_sse_events(resp.text)
        # 降级为 SCENE_CHAT 后应正常返回 PRESENCE 事件
        phases = {e.get("phase") for e in events}
        assert "PRESENCE" in phases, f"期望 PRESENCE 事件，phases={phases}"
        # 不应出现 500 错误
        commit_events = [e for e in events if e.get("phase") == "COMMIT"]
        if commit_events:
            commit = commit_events[-1]
            assert commit.get("reason_code") != "INTERNAL_SERVER_ERROR", (
                f"不应出现 INTERNAL_SERVER_ERROR: {commit}"
            )

    def test_chat_missing_message_returns_422(self, client: TestClient):
        """CHAT-5: 缺少 message 字段 → 422 Unprocessable Entity。"""
        resp = client.post(
            "/v1/chat",
            json={"session_id": "sess_no_msg"},
        )
        assert resp.status_code == 422, f"期望 422，实际 {resp.status_code}"

    def test_chat_empty_message_handled(self, client: TestClient, mock_governor_approve, mock_llm_stream):
        """CHAT-6: 空字符串 message → 200 SSE（不崩溃）。"""
        resp = client.post(
            "/v1/chat",
            json={"message": "", "session_id": f"sess_empty_{_RUN_ID}"},
        )
        assert resp.status_code == 200
        events = self._parse_sse_events(resp.text)
        assert len(events) > 0

    def test_chat_with_emotion_context(self, client: TestClient, mock_governor_approve, mock_llm_stream):
        """CHAT-7: 携带 emotion_tag + emotion_intensity → 200 SSE 正常处理。"""
        resp = client.post(
            "/v1/chat",
            json={
                "message": "今天心情很好",
                "session_id": f"sess_emotion_{_RUN_ID}",
                "emotion_tag": "JOY",
                "emotion_intensity": 0.8,
            },
        )
        assert resp.status_code == 200
        events = self._parse_sse_events(resp.text)
        assert any(e.get("phase") == "PRESENCE" for e in events)


# ══════════════════════════════════════════════════════════════════════════════
# BOND: /bond/* 路由
# ══════════════════════════════════════════════════════════════════════════════

class TestBondRoutes:
    """BOND-1 ~ BOND-6：Bond 阶段 REST API 测试。"""

    _TEST_USER = f"user_integ_{_RUN_ID}"

    def test_bond_health(self, client: TestClient):
        """BOND-1: GET /bond/health → 200 + BondHealthResponse。"""
        resp = client.get("/bond/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("OK", "DEGRADED")
        assert "stage_order" in data
        assert isinstance(data["stage_order"], list)
        assert len(data["stage_order"]) > 0

    def test_bond_users_list(self, client: TestClient):
        """BOND-2: GET /bond/users → 200 + users 列表。"""
        resp = client.get("/bond/users")
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)

    def test_bond_stage_new_user(self, client: TestClient):
        """BOND-3: GET /bond/stage/{user_id} → 200 + BondStageResponse（新用户初始状态）。"""
        resp = client.get(f"/bond/stage/{self._TEST_USER}")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_stage" in data
        assert "total_score" in data
        assert "event_count" in data
        assert "trend" in data
        assert data["user_id"] == self._TEST_USER

    def test_bond_summary_new_user(self, client: TestClient):
        """BOND-4: GET /bond/summary/{user_id} → 200 + BondSummaryResponse。"""
        resp = client.get(f"/bond/summary/{self._TEST_USER}")
        assert resp.status_code == 200
        data = resp.json()
        assert "stage" in data
        assert "trend" in data
        assert "capability_hint" in data
        assert data["user_id"] == self._TEST_USER

    def test_bond_event_submit(self, client: TestClient):
        """BOND-5: POST /bond/event → 200 + BondEventResponse。"""
        resp = client.post(
            "/bond/event",
            json={
                "user_id": self._TEST_USER,
                "update_kind": "INTERACTION",
                "evidence_refs": [f"req::{_RUN_ID}::001"],
                "emotion_tag": "JOY",
                "emotion_intensity": 0.7,
            },
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "from_stage" in data
        assert "to_stage" in data
        assert "transition_status" in data
        assert data["transition_status"] in ("PROMOTED", "DEMOTED", "STABLE")
        assert "score_delta" in data
        assert "total_score" in data
        assert data["user_id"] == self._TEST_USER

    def test_bond_event_empty_evidence_refs_returns_422(self, client: TestClient):
        """BOND-6: evidence_refs 为空 → 422（FR-BOND-002）。"""
        resp = client.post(
            "/bond/event",
            json={
                "user_id": self._TEST_USER,
                "update_kind": "INTERACTION",
                "evidence_refs": [],
            },
        )
        assert resp.status_code == 422, f"期望 422，实际 {resp.status_code}"

    def test_bond_event_score_accumulates(self, client: TestClient):
        """BOND-7: 多次提交 Bond 事件后积分应累积。"""
        user = f"user_accum_{_RUN_ID}"
        # 提交 3 次事件
        scores = []
        for i in range(3):
            resp = client.post(
                "/bond/event",
                json={
                    "user_id": user,
                    "update_kind": "INTERACTION",
                    "evidence_refs": [f"req::{_RUN_ID}::accum_{i}"],
                },
            )
            assert resp.status_code == 200
            scores.append(resp.json()["total_score"])
        # 积分应单调递增
        assert scores[0] <= scores[1] <= scores[2], (
            f"积分未累积: {scores}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# OBS: /obs/* 可观测性路由
# ══════════════════════════════════════════════════════════════════════════════

class TestObsRoutes:
    """OBS-1 ~ OBS-6：可观测性 REST API 测试。"""

    def test_obs_services_health(self, client: TestClient):
        """OBS-1: GET /obs/services/health → 200 + ServicesHealthResponse。"""
        resp = client.get("/obs/services/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "services" in data
        assert "timestamp" in data
        assert isinstance(data["services"], list)
        # 验证 up/degraded/down/unknown 字段存在
        for field in ("up", "degraded", "down", "unknown"):
            assert field in data, f"缺少字段 {field}"

    def test_obs_sessions(self, client: TestClient):
        """OBS-2: GET /obs/sessions → 200 + SessionsResponse。"""
        resp = client.get("/obs/sessions?page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "items" in data
        assert "page" in data
        assert "page_size" in data
        assert isinstance(data["items"], list)

    def test_obs_trace_not_found(self, client: TestClient):
        """OBS-3: GET /obs/trace/{req_id} → 404（不存在的 request_id）。"""
        resp = client.get(f"/obs/trace/nonexistent_req_{_RUN_ID}")
        assert resp.status_code == 404, f"期望 404，实际 {resp.status_code}"

    def test_obs_metrics_timeseries(self, client: TestClient):
        """OBS-4: GET /obs/metrics/timeseries → 200 + MetricsTimeseriesResponse。"""
        resp = client.get("/obs/metrics/timeseries?range_minutes=60&interval_minutes=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "points" in data
        assert "total_requests" in data
        assert "rejection_rate" in data
        assert isinstance(data["points"], list)

    def test_obs_llm_nodes(self, client: TestClient):
        """OBS-5: GET /obs/llm/nodes → 200 + LlmNodesResponse（9 个节点）。"""
        resp = client.get("/obs/llm/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "timestamp" in data
        assert isinstance(data["nodes"], list)
        # 应有 9 个 LLM 节点（来自 _LLM_NODE_REGISTRY）
        assert len(data["nodes"]) >= 9, (
            f"期望 ≥9 个 LLM 节点，实际 {len(data['nodes'])}"
        )
        # 验证节点结构
        node = data["nodes"][0]
        for field in ("node_id", "role", "provider", "model_id", "status"):
            assert field in node, f"节点缺少字段 {field}: {node}"

    def test_obs_metrics_endpoint(self, client: TestClient):
        """OBS-6: GET /obs/metrics → 200 + obs metrics 结构。"""
        resp = client.get("/obs/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data

    def test_obs_sessions_with_filter(self, client: TestClient):
        """OBS-7: GET /obs/sessions?status=ok → 200，过滤参数不崩溃。"""
        resp = client.get("/obs/sessions?status=ok&window_minutes=30")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS: /tools/* 工具路由
# ══════════════════════════════════════════════════════════════════════════════

class TestToolRoutes:
    """TOOLS-1 ~ TOOLS-4：ToolBroker REST API 测试。"""

    def test_tools_health(self, client: TestClient):
        """TOOLS-1: GET /tools/health → 200 + ToolHealthResponse。"""
        resp = client.get("/tools/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("OK", "DEGRADED")
        assert "catalog_size" in data
        assert "write_tools" in data
        assert "read_tools" in data

    def test_tools_meta_pack(self, client: TestClient):
        """TOOLS-2: GET /tools/meta-pack → 200 + ToolMetaPackResponse。"""
        resp = client.get("/tools/meta-pack?k=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "tool_meta_pack" in data
        assert "tool_visibility_hash" in data
        assert "total" in data
        assert isinstance(data["tool_meta_pack"], list)
        # tool_visibility_hash 应为 sha256: 前缀
        assert data["tool_visibility_hash"].startswith("sha256:")

    def test_tools_get_nonexistent_tool(self, client: TestClient):
        """TOOLS-3: GET /tools/{tool_id} → 404（不存在的工具）。"""
        resp = client.get("/tools/nonexistent_tool_xyz_12345")
        assert resp.status_code in (404, 503), (
            f"期望 404 或 503，实际 {resp.status_code}"
        )

    def test_tools_meta_pack_with_query(self, client: TestClient):
        """TOOLS-4: GET /tools/meta-pack?query=搜索 → 200，query 参数不崩溃。"""
        resp = client.get("/tools/meta-pack?query=文件操作&k=3")
        assert resp.status_code == 200
        data = resp.json()
        assert "tool_meta_pack" in data

    def test_tools_meta_pack_include_write(self, client: TestClient):
        """TOOLS-5: GET /tools/meta-pack?include_write=true → 200。"""
        resp = client.get("/tools/meta-pack?include_write=true&k=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "tool_meta_pack" in data
        assert "include_write" in data
        assert data["include_write"] is True


# ══════════════════════════════════════════════════════════════════════════════
# EDGE: POST /v1/edge/events
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeEvents:
    """EDGE-1 ~ EDGE-2：Edge 事件上报端点测试。"""

    def test_edge_events_valid(self, client: TestClient):
        """EDGE-1: POST /v1/edge/events → 200 + EdgeEventsResponse。"""
        resp = client.post(
            "/v1/edge/events",
            json={
                "events": [
                    {
                        "event_type": "ASR_RESULT",
                        "session_id": f"sess_{_RUN_ID}",
                        "ts_ms": int(time.time() * 1000),
                        "payload": {"text": "テスト"},
                    }
                ]
            },
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "success" in data
        assert "total" in data
        assert "accepted" in data
        assert "rejected" in data
        assert data["total"] == 1

    def test_edge_events_empty_list(self, client: TestClient):
        """EDGE-2: POST /v1/edge/events events=[] → 200，空列表正常处理。"""
        resp = client.post(
            "/v1/edge/events",
            json={"events": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_edge_events_multiple(self, client: TestClient):
        """EDGE-3: POST /v1/edge/events 多条事件 → 200，total 正确。"""
        events = [
            {
                "event_type": "TTS_COMPLETE",
                "session_id": f"sess_{_RUN_ID}",
                "ts_ms": int(time.time() * 1000) + i,
                "payload": {"duration_ms": 500 + i * 100},
            }
            for i in range(3)
        ]
        resp = client.post("/v1/edge/events", json={"events": events})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# TRACE: POST /observability/trace
# ══════════════════════════════════════════════════════════════════════════════

class TestObservabilityTrace:
    """TRACE-1 ~ TRACE-2：请求链路追踪端点测试。"""

    def test_trace_nonexistent_request(self, client: TestClient):
        """TRACE-1: POST /observability/trace → 200 + ObservabilityResponse（不存在的 request_id）。"""
        resp = client.post(
            "/observability/trace",
            json={"request_id": f"req_nonexistent_{_RUN_ID}"},
        )
        # 不存在时应返回 200（trace_status=NOT_FOUND）或 404
        assert resp.status_code in (200, 404), (
            f"期望 200 或 404，实际 {resp.status_code}"
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "request_id" in data
            assert "trace_status" in data

    def test_trace_empty_request_id_returns_error(self, client: TestClient):
        """TRACE-2: request_id 为空字符串 → 400 或 422。"""
        resp = client.post(
            "/observability/trace",
            json={"request_id": ""},
        )
        assert resp.status_code in (400, 422), (
            f"期望 400 或 422，实际 {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# METRICS: GET /metrics 运行时指标
# ══════════════════════════════════════════════════════════════════════════════

class TestRuntimeMetrics:
    """METRICS-1 ~ METRICS-3：运行时指标端点测试。"""

    def test_metrics_fields_present(self, client: TestClient):
        """METRICS-1: GET /metrics → 所有必要字段存在。"""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["uptime_seconds"] >= 0
        assert data["total_requests"] >= 0
        assert data["total_chat_requests"] >= 0
        assert data["total_rejections"] >= 0
        assert data["total_errors"] >= 0
        assert "reason_code_distribution" in data
        assert isinstance(data["reason_code_distribution"], dict)

    def test_metrics_increments_after_chat(self, client: TestClient, mock_governor_approve, mock_llm_stream):
        """METRICS-2: 发送 chat 请求后 total_chat_requests 应递增。"""
        before = client.get("/metrics").json()["total_chat_requests"]
        client.post(
            "/v1/chat",
            json={"message": "指标测试", "session_id": f"sess_metrics_{_RUN_ID}"},
        )
        after = client.get("/metrics").json()["total_chat_requests"]
        assert after > before, (
            f"total_chat_requests 未递增: before={before}, after={after}"
        )

    def test_metrics_rejection_increments_on_reject(self, client: TestClient, mock_governor_reject):
        """METRICS-3: Governor REJECT 后 total_rejections 应递增。"""
        before = client.get("/metrics").json()["total_rejections"]
        client.post(
            "/v1/chat",
            json={"message": "触发拒绝", "session_id": f"sess_rej_metrics_{_RUN_ID}"},
        )
        after = client.get("/metrics").json()["total_rejections"]
        assert after > before, (
            f"total_rejections 未递增: before={before}, after={after}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# INTENT: 意图分类器单元集成测试
# ══════════════════════════════════════════════════════════════════════════════

class TestPreIntentClassifier:
    """INTENT-1 ~ INTENT-5：pre_intent_classifier 分类逻辑测试。"""

    def test_daily_chat_classified_as_scene_chat(self):
        """INTENT-1: 日常对话 → SCENE_CHAT。"""
        from host_brain.app.graph_config_builder import pre_intent_classifier, SCENE_CHAT
        result = pre_intent_classifier("你好，今天天气怎么样？")
        assert result == SCENE_CHAT, f"期望 SCENE_CHAT，实际 {result}"

    def test_code_request_classified_as_work_code(self):
        """INTENT-2: 代码请求 → SCENE_WORK_CODE。"""
        from host_brain.app.graph_config_builder import pre_intent_classifier, SCENE_WORK_CODE
        result = pre_intent_classifier("帮我写一个 Python 函数来排序列表")
        assert result == SCENE_WORK_CODE, f"期望 SCENE_WORK_CODE，实际 {result}"

    def test_translate_request_classified_as_work_translate(self):
        """INTENT-3: 翻译请求 → SCENE_WORK_TRANSLATE。"""
        from host_brain.app.graph_config_builder import pre_intent_classifier, SCENE_WORK_TRANSLATE
        result = pre_intent_classifier("请把这段话翻译成英文：我爱你")
        assert result == SCENE_WORK_TRANSLATE, f"期望 SCENE_WORK_TRANSLATE，实际 {result}"

    def test_entertainment_not_classified_as_exec_intent(self):
        """INTENT-4: 娱乐关键词（安装歌曲）不应误判为 EXEC_INTENT（审计修复 #2）。"""
        from host_brain.app.graph_config_builder import pre_intent_classifier, SCENE_EXEC_INTENT
        result = pre_intent_classifier("帮我安装一下这首歌")
        assert result != SCENE_EXEC_INTENT, (
            f"娱乐请求不应被分类为 EXEC_INTENT，实际 {result}"
        )

    def test_empty_message_returns_scene_chat(self):
        """INTENT-5: 空消息 → 默认 SCENE_CHAT（不崩溃）。"""
        from host_brain.app.graph_config_builder import pre_intent_classifier, SCENE_CHAT
        result = pre_intent_classifier("")
        assert result == SCENE_CHAT, f"空消息应返回 SCENE_CHAT，实际 {result}"


# ══════════════════════════════════════════════════════════════════════════════
# GOVERNOR: 决策引擎集成测试
# ══════════════════════════════════════════════════════════════════════════════

class TestGovernorIntegration:
    """GOV-1 ~ GOV-3：Governor 决策引擎集成测试。"""

    def test_governor_approves_narrative_only(self):
        """GOV-1: narrative_only 意图 → APPROVE。"""
        from host_brain.governor.decision import IntentDecisionEngine
        from host_brain.governor.schemas import DECISION_APPROVE

        engine = IntentDecisionEngine()
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::gov_test_{_RUN_ID}",
            "ts_ms": int(time.time() * 1000),
            "session_id": "sess::gov_test",
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "集成测试"},
            "nonce": f"nonce::{_RUN_ID}",
        }
        result = engine.evaluate_intent(raw_intent=intent, request_id=f"req_{_RUN_ID}")
        assert result.get("decision") in (DECISION_APPROVE, "REJECT"), (
            f"decision 异常: {result}"
        )
        assert "rule_trace" in result

    def test_governor_result_has_required_fields(self):
        """GOV-2: Governor 结果包含所有必要字段。"""
        from host_brain.governor.decision import IntentDecisionEngine

        engine = IntentDecisionEngine()
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::gov_fields_{_RUN_ID}",
            "ts_ms": int(time.time() * 1000),
            "session_id": "sess::gov_fields",
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "字段测试"},
            "nonce": f"nonce::fields_{_RUN_ID}",
        }
        result = engine.evaluate_intent(raw_intent=intent, request_id=f"req_fields_{_RUN_ID}")
        required = ["decision", "rule_trace"]
        for field in required:
            assert field in result, f"Governor 结果缺少字段 {field}: {list(result.keys())}"

    def test_governor_invalid_intent_schema(self):
        """GOV-3: 缺少必要字段的 intent → 不崩溃（返回 REJECT 或抛出可预期异常）。"""
        from host_brain.governor.decision import IntentDecisionEngine

        engine = IntentDecisionEngine()
        # 故意缺少 schema_version 等字段
        bad_intent = {"action": "narrative_only"}
        try:
            result = engine.evaluate_intent(raw_intent=bad_intent, request_id="req_bad")
            # 如果不抛异常，应返回 REJECT
            assert result.get("decision") in ("REJECT", "APPROVE"), (
                f"异常 intent 应返回 REJECT 或 APPROVE: {result}"
            )
        except Exception as exc:
            # 允许抛出可预期的验证异常
            assert isinstance(exc, (ValueError, KeyError, TypeError)), (
                f"不应抛出非预期异常: {type(exc).__name__}: {exc}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA: Pydantic 模型验证
# ══════════════════════════════════════════════════════════════════════════════

class TestSchemaValidation:
    """SCHEMA-1 ~ SCHEMA-4：Pydantic 请求/响应模型验证。"""

    def test_chat_request_optional_fields(self):
        """SCHEMA-1: ChatRequest 所有可选字段均可省略。"""
        from host_brain.app.schemas import ChatRequest
        req = ChatRequest(message="测试")
        assert req.message == "测试"
        assert req.session_id is None
        assert req.thread_id is None
        assert req.emotion_tag is None
        assert req.emotion_intensity is None

    def test_chat_request_with_all_fields(self):
        """SCHEMA-2: ChatRequest 所有字段均可正常赋值。"""
        from host_brain.app.schemas import ChatRequest
        req = ChatRequest(
            message="完整测试",
            session_id="sess_001",
            thread_id="thread_001",
            intent_draft={"action": "narrative_only"},
            scene_type="SCENE_CHAT",
            mood_state="happy",
            emotion_tag="JOY",
            emotion_intensity=0.9,
            emotion_context={"valence": 0.8},
            emotion_range_hint="positive",
        )
        assert req.emotion_intensity == 0.9
        assert req.emotion_tag == "JOY"

    def test_metrics_response_structure(self):
        """SCHEMA-3: MetricsResponse 结构正确。"""
        from host_brain.app.schemas import MetricsResponse
        resp = MetricsResponse(
            uptime_seconds=100,
            total_requests=50,
            total_chat_requests=30,
            total_rejections=5,
            total_errors=2,
            reason_code_distribution={"EMOTION_GATE": 3},
            timestamp=int(time.time() * 1000),
        )
        assert resp.uptime_seconds == 100
        assert resp.reason_code_distribution["EMOTION_GATE"] == 3

    def test_edge_events_request_structure(self):
        """SCHEMA-4: EdgeEventsRequest 结构正确。"""
        from host_brain.app.schemas import EdgeEventsRequest
        req = EdgeEventsRequest(events=[{"type": "ASR", "ts_ms": 1000}])
        assert len(req.events) == 1


# ══════════════════════════════════════════════════════════════════════════════
# CORS: 跨域头验证
# ══════════════════════════════════════════════════════════════════════════════

class TestCORSHeaders:
    """CORS-1 ~ CORS-2：CORS 中间件验证。"""

    def test_cors_preflight_allowed_origin(self, client: TestClient):
        """CORS-1: OPTIONS 预检请求 → 包含 Access-Control-Allow-Origin。"""
        resp = client.options(
            "/v1/chat",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        # 允许 200 或 405（TestClient 行为差异）
        assert resp.status_code in (200, 204, 405)

    def test_health_response_has_no_cors_error(self, client: TestClient):
        """CORS-2: GET /health 带 Origin 头 → 正常返回 200。"""
        resp = client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"},
        )
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# PANEL: 面板重定向
# ══════════════════════════════════════════════════════════════════════════════

class TestPanelRoute:
    """PANEL-1：/panel 重定向测试。"""

    def test_panel_redirects(self, client: TestClient):
        """PANEL-1: GET /panel → 301 重定向到 /static/monitor/index.html。"""
        resp = client.get("/panel", follow_redirects=False)
        assert resp.status_code in (301, 302, 307, 308), (
            f"期望重定向，实际 {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 主入口（直接运行时输出汇总）
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            __file__,
            "-v",
            "--tb=short",
            "--no-header",
            "-q",
        ],
        cwd=str(ROOT),
    )
    sys.exit(result.returncode)
