"""agent_routes 单元测试。"""

from __future__ import annotations

import json

import anyio
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from host_brain.app.agent_routes import (
    AgentRunRequest,
    DEFAULT_MAX_ROUNDS,
    DEFAULT_ROUND_TIMEOUT_SEC,
    DEFAULT_TOTAL_TIMEOUT_SEC,
    VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
    _agent_loop_sse_generator,
    router,
)


# ---------------------------------------------------------------------------
# 请求模型测试
# ---------------------------------------------------------------------------

class TestAgentRunRequest:
    def test_default_values(self):
        req = AgentRunRequest(goal="点击登录按钮")
        assert req.goal == "点击登录按钮"
        assert req.max_rounds == DEFAULT_MAX_ROUNDS
        assert req.round_timeout_sec == DEFAULT_ROUND_TIMEOUT_SEC
        assert req.total_timeout_sec == DEFAULT_TOTAL_TIMEOUT_SEC
        assert req.image_upload_consent is False
        assert req.use_mock is False
        assert req.session_id is None
        assert req.clawdbot_base_url is None

    def test_custom_values(self):
        req = AgentRunRequest(
            goal="填写表单",
            session_id="sess-abc",
            max_rounds=3,
            round_timeout_sec=15.0,
            total_timeout_sec=60.0,
            image_upload_consent=True,
            use_mock=True,
            clawdbot_base_url="http://custom:8888",
        )
        assert req.session_id == "sess-abc"
        assert req.max_rounds == 3
        assert req.image_upload_consent is True
        assert req.use_mock is True

    def test_max_rounds_bounds(self):
        """max_rounds 超出范围应报错。"""
        with pytest.raises(Exception):
            AgentRunRequest(goal="test", max_rounds=0)
        with pytest.raises(Exception):
            AgentRunRequest(goal="test", max_rounds=21)


# ---------------------------------------------------------------------------
# SSE 兼容边界测试
# ---------------------------------------------------------------------------

class TestAgentLoopSseGenerator:
    @pytest.mark.parametrize(
        ("use_mock", "use_edge_rpc", "clawdbot_url", "edge_url"),
        [
            (True, False, "mock://", "http://localhost:17800"),
            (False, True, "http://unused.invalid", "http://attacker.invalid"),
            (False, False, "http://attacker.invalid", "http://unused.invalid"),
        ],
    )
    def test_all_legacy_modes_fail_closed_without_side_effects(
        self,
        use_mock: bool,
        use_edge_rpc: bool,
        clawdbot_url: str,
        edge_url: str,
    ):
        """Mock、Edge 和 ClawdBot 模式都只返回固定拒绝。"""

        async def _run():
            events = []
            async for sse_line in _agent_loop_sse_generator(
                request_id="req-test-001",
                session_id="sess-test-001",
                goal="点击登录按鈕",
                max_rounds=2,
                round_timeout_sec=10.0,
                total_timeout_sec=30.0,
                image_upload_consent=False,
                use_mock=use_mock,
                use_edge_rpc=use_edge_rpc,
                clawdbot_base_url=clawdbot_url,
                edge_rpc_base_url=edge_url,
            ):
                assert sse_line.startswith("data: ")
                assert sse_line.endswith("\n\n")
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            assert [event["phase"] for event in events] == [
                "AGENT_INIT",
                "LOOP_DONE",
            ]

            init_event, loop_done = events
            assert set(init_event) == {
                "phase",
                "request_id",
                "session_id",
                "goal",
                "client_mode",
                "client_url",
                "llm_available",
                "side_effects_executed",
                "reason_code",
            }
            assert init_event["request_id"] == "req-test-001"
            assert init_event["session_id"] == "sess-test-001"
            assert init_event["goal"] == "点击登录按鈕"
            assert init_event["client_mode"] == "governed_admission_required"
            assert init_event["client_url"] is None
            assert init_event["llm_available"] is False
            assert init_event["side_effects_executed"] is False
            assert (
                init_event["reason_code"]
                == VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED
            )

            assert set(loop_done) == {
                "phase",
                "request_id",
                "goal",
                "goal_achieved",
                "final_status",
                "reason_code",
                "side_effects_executed",
                "total_rounds",
                "total_latency_ms",
                "round_summaries",
            }
            assert loop_done["goal_achieved"] is False
            assert loop_done["final_status"] == "REJECTED"
            assert loop_done["total_rounds"] == 0
            assert loop_done["total_latency_ms"] == 0
            assert loop_done["round_summaries"] == []
            assert loop_done["side_effects_executed"] is False
            assert (
                loop_done["reason_code"]
                == VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED
            )

        anyio.run(_run)

    def test_agent_init_is_first_event(self):
        """AGENT_INIT 必须是第一个 SSE 事件。"""
        async def _run():
            events = []
            async for sse_line in _agent_loop_sse_generator(
                request_id="req-test-002",
                session_id="sess-test-002",
                goal="测试顺序",
                max_rounds=1,
                round_timeout_sec=10.0,
                total_timeout_sec=30.0,
                image_upload_consent=False,
                use_mock=True,
                use_edge_rpc=False,
                clawdbot_base_url="mock://",
                edge_rpc_base_url="http://localhost:17800",
            ):
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            assert events[0]["phase"] == "AGENT_INIT"

        anyio.run(_run)

    def test_http_route_ignores_attacker_execution_endpoints(self):
        """生产路由保留协议，但不会连接请求方提供的执行端点。"""

        async def _run():
            app = FastAPI()
            app.include_router(router)
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/v1/agent/run",
                    json={
                        "goal": "执行高风险桌面动作",
                        "use_mock": False,
                        "use_edge_rpc": True,
                        "edge_rpc_base_url": "http://attacker.invalid:17800",
                        "clawdbot_base_url": "http://attacker.invalid:7788",
                    },
                )

            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-accel-buffering"] == "no"
            assert response.headers["x-request-id"]
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert [event["phase"] for event in events] == [
                "AGENT_INIT",
                "LOOP_DONE",
            ]
            assert events[-1]["final_status"] == "REJECTED"
            assert events[-1]["side_effects_executed"] is False
            assert all(
                event["request_id"] == response.headers["x-request-id"]
                for event in events
            )
            assert "attacker.invalid" not in response.text

        anyio.run(_run)

    def test_info_reports_mock_execution_unavailable(self):
        async def _run():
            app = FastAPI()
            app.include_router(router)
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                response = await client.get("/v1/agent/info")

            assert response.status_code == 200
            assert response.json()["execution_available"] is False
            assert (
                response.json()["reason_code"]
                == VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED
            )
            assert response.json()["mock_mode_available"] is False
            assert response.json()["clawdbot_base_url"] == ""

        anyio.run(_run)
