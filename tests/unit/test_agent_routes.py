"""agent_routes 单元测试。"""

from __future__ import annotations

import json
import os

import anyio
import pytest

from host_brain.app.agent_routes import (
    AgentRunRequest,
    AgentInfoResponse,
    _get_clawdbot_base_url,
    _agent_loop_sse_generator,
)
from host_brain.app.vision.agent_loop import DEFAULT_MAX_ROUNDS, DEFAULT_ROUND_TIMEOUT_SEC, DEFAULT_TOTAL_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# 辅助函数测试
# ---------------------------------------------------------------------------

class TestGetClawdbotBaseUrl:
    def test_default_value(self):
        """未设置环境变量时返回默认地址。"""
        os.environ.pop("CLAWDBOT_BASE_URL", None)
        url = _get_clawdbot_base_url()
        assert url == "http://localhost:7788"

    def test_env_variable(self):
        """读取环境变量。"""
        os.environ["CLAWDBOT_BASE_URL"] = "http://192.168.1.100:9000/"
        url = _get_clawdbot_base_url()
        assert url == "http://192.168.1.100:9000"  # 末尾斜杠被去掉
        os.environ.pop("CLAWDBOT_BASE_URL", None)

    def test_override_parameter(self):
        """请求参数覆盖环境变量。"""
        os.environ["CLAWDBOT_BASE_URL"] = "http://env-host:8000"
        url = _get_clawdbot_base_url("http://override-host:9999/")
        assert url == "http://override-host:9999"
        os.environ.pop("CLAWDBOT_BASE_URL", None)

    def test_empty_override_falls_back_to_env(self):
        """空字符串 override 回退到环境变量。"""
        os.environ["CLAWDBOT_BASE_URL"] = "http://env-host:8000"
        url = _get_clawdbot_base_url("  ")
        assert url == "http://env-host:8000"
        os.environ.pop("CLAWDBOT_BASE_URL", None)


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
# SSE 生成器测试（Mock 模式）
# ---------------------------------------------------------------------------

class TestAgentLoopSseGenerator:
    def test_sse_events_mock_mode(self):
        """Mock 模式下验证 SSE 事件序列包含 AGENT_INIT 和 LOOP_DONE。"""
        async def _run():
            events = []
            async for sse_line in _agent_loop_sse_generator(
                request_id="req-test-001",
                session_id="sess-test-001",
                goal="点击登录按钮",
                max_rounds=2,
                round_timeout_sec=10.0,
                total_timeout_sec=30.0,
                image_upload_consent=False,
                use_mock=True,
                clawdbot_base_url="mock://",
            ):
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            phases = [e["phase"] for e in events]
            # 必须包含 AGENT_INIT
            assert "AGENT_INIT" in phases
            # 必须包含 LOOP_DONE
            assert "LOOP_DONE" in phases

            # AGENT_INIT 事件字段验证
            init_event = next(e for e in events if e["phase"] == "AGENT_INIT")
            assert init_event["request_id"] == "req-test-001"
            assert init_event["session_id"] == "sess-test-001"
            assert init_event["goal"] == "点击登录按钮"
            assert init_event["use_mock"] is True
            assert init_event["clawdbot_base_url"] == "mock://"

            # LOOP_DONE 事件字段验证
            loop_done = next(e for e in events if e["phase"] == "LOOP_DONE")
            assert "goal_achieved" in loop_done
            assert "final_status" in loop_done
            assert "total_latency_ms" in loop_done

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
                clawdbot_base_url="mock://",
            ):
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            assert events[0]["phase"] == "AGENT_INIT"

        anyio.run(_run)
