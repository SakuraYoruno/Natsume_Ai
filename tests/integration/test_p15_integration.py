"""P15 集成测试：终点封板验收。

验收标准：
P15-3.1：_dispatch_to_agent() 可调用，根据 active_agent_id 返回正确 handler_id
P15-3.2：agent_dispatch_node 路由正确，有 handler 时执行，无 handler 时降级
P15-3.3：SUPERVISOR_AUTO_ENABLED=true 触发 LLM 模式，失败时回退 rule
P15-3.4：P14 基线（152 用例）不退化
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from host_brain.integrations.graph_executor import GraphExecutor, _AGENT_HANDLER_MAP


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_executor(handlers: dict | None = None) -> GraphExecutor:
    gf = MagicMock()
    gf.return_value = MagicMock()
    return GraphExecutor(
        handlers=handlers or {},
        gateway_factory=gf,
        default_timeout_ms=5000,
    )


def _make_dispatch_state(active_agent_id: str, next_node: str = "judge_agent_node") -> dict:
    return {
        "pending_node_id": "agent_dispatch_node",
        "active_agent_id": active_agent_id,
        "payload": {"user_input": "集成测试输入", "scene_type": "WORK_CODE"},
        "node_bindings": {
            "agent_dispatch_node": {
                "handler_id": "agent_dispatch",
                "task": "agent_dispatch",
            }
        },
        "edge_map": {"agent_dispatch_node": next_node},
        "agent_artifacts": {},
        "trace": [],
        "model_calls": [],
        "status": "OK",
        "committed_route_token": None,
        "policy_start_ms": None,
        "policy_timeout_ms": 60000,
        "timeout_ms": 5000,
        "presence_events": [],
        "clock_events": [],
        "node_exec_stats": {},
    }


# ---------------------------------------------------------------------------
# P15-3.1：_dispatch_to_agent 完整验收
# ---------------------------------------------------------------------------

class TestP15DispatchToAgentIntegration(unittest.TestCase):
    """P15-3.1：_dispatch_to_agent() 可调用，根据 active_agent_id 返回正确 handler_id"""

    def setUp(self):
        self.executor = _make_executor()

    def test_all_registered_agents_dispatch_correctly(self):
        """所有注册的 agent_id 都应能正确 dispatch"""
        for agent_id, expected_handler_id in _AGENT_HANDLER_MAP.items():
            state = _make_dispatch_state(active_agent_id=agent_id)
            result = self.executor._dispatch_to_agent(state)
            self.assertEqual(result, expected_handler_id,
                             f"agent_id={agent_id!r} dispatch 失败")

    def test_dispatch_to_agent_is_callable(self):
        """_dispatch_to_agent 应可调用"""
        self.assertTrue(callable(self.executor._dispatch_to_agent))

    def test_dispatch_to_agent_returns_string(self):
        """_dispatch_to_agent 应返回 str"""
        state = _make_dispatch_state(active_agent_id="code_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertIsInstance(result, str)

    def test_dispatch_to_agent_unknown_returns_empty_string(self):
        """未知 agent_id 应返回空字符串"""
        state = _make_dispatch_state(active_agent_id="nonexistent_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "")

    def test_agent_handler_map_has_seven_entries(self):
        """_AGENT_HANDLER_MAP 应包含 7 个 agent（含 emotion_agent）"""
        self.assertEqual(len(_AGENT_HANDLER_MAP), 7)


# ---------------------------------------------------------------------------
# P15-3.2：agent_dispatch_node 路由完整验收
# ---------------------------------------------------------------------------

class TestP15AgentDispatchNodeIntegration(unittest.TestCase):
    """P15-3.2：agent_dispatch_node 路由正确，有 handler 时执行，无 handler 时降级"""

    def test_code_agent_dispatch_full_flow(self):
        """code_agent 完整分发流程"""
        artifact_written = {}

        def code_handler(payload, context):
            artifact_written["called"] = True
            return {
                **payload,
                "agent_artifacts": {
                    "code_agent": {
                        "draft": "def sort(arr): return sorted(arr)",
                        "confidence": 0.9,
                    }
                },
            }

        executor = _make_executor(handlers={"code_agent": code_handler})
        state = _make_dispatch_state(active_agent_id="code_agent")

        result = executor._run_single_step(state)

        self.assertTrue(artifact_written.get("called"))
        self.assertEqual(result.get("pending_node_id"), "judge_agent_node")
        artifacts = result.get("agent_artifacts") or {}
        self.assertIn("code_agent", artifacts)

    def test_translate_agent_dispatch_full_flow(self):
        """translate_agent 完整分发流程"""
        def translate_handler(payload, context):
            return {
                **payload,
                "agent_artifacts": {
                    "translate_agent": {"draft": "翻訳結果", "confidence": 0.85}
                },
            }

        executor = _make_executor(handlers={"translate_agent": translate_handler})
        state = _make_dispatch_state(active_agent_id="translate_agent")

        result = executor._run_single_step(state)

        self.assertEqual(result.get("pending_node_id"), "judge_agent_node")
        artifacts = result.get("agent_artifacts") or {}
        self.assertIn("translate_agent", artifacts)

    def test_unknown_agent_degrades_and_advances(self):
        """未知 agent_id 降级后应跳转到下一跳"""
        executor = _make_executor(handlers={})
        state = _make_dispatch_state(active_agent_id="unknown_xyz")

        result = executor._run_single_step(state)

        self.assertIsNotNone(result)
        self.assertEqual(result.get("pending_node_id"), "judge_agent_node")
        trace = result.get("trace") or []
        degraded = [t for t in trace if t.get("status") == "DEGRADED"]
        self.assertTrue(len(degraded) > 0)

    def test_dispatch_node_trace_contains_handler_id(self):
        """trace 应包含实际调用的 handler_id"""
        def mock_handler(payload, context):
            return dict(payload)

        executor = _make_executor(handlers={"intent_agent": mock_handler})
        state = _make_dispatch_state(active_agent_id="intent_agent")

        result = executor._run_single_step(state)

        trace = result.get("trace") or []
        dispatch_traces = [t for t in trace if t.get("node_id") == "agent_dispatch_node"]
        self.assertTrue(len(dispatch_traces) > 0)
        self.assertEqual(dispatch_traces[-1].get("handler_id"), "intent_agent")

    def test_dispatch_node_does_not_write_next_node_from_handler(self):
        """agent handler 写入的 __next_node__ 应被忽略，由 edge_map 决定"""
        def handler_with_next_node(payload, context):
            return {**payload, "__next_node__": "malicious_node"}

        executor = _make_executor(handlers={"code_agent": handler_with_next_node})
        state = _make_dispatch_state(active_agent_id="code_agent")
        state["edge_map"] = {"agent_dispatch_node": "judge_agent_node"}

        result = executor._run_single_step(state)

        # edge_map 决定下一跳，handler 写的 __next_node__ 被忽略
        self.assertEqual(result.get("pending_node_id"), "judge_agent_node")

    def test_all_agents_in_map_can_be_dispatched(self):
        """_AGENT_HANDLER_MAP 中所有 agent 都应能被 dispatch"""
        for agent_id in _AGENT_HANDLER_MAP:
            def make_handler(aid):
                def h(payload, context):
                    return {**payload, "agent_artifacts": {aid: {"draft": "ok"}}}
                return h

            executor = _make_executor(handlers={agent_id: make_handler(agent_id)})
            state = _make_dispatch_state(active_agent_id=agent_id)
            result = executor._run_single_step(state)
            self.assertIsNotNone(result, f"agent_id={agent_id!r} dispatch 返回 None")


# ---------------------------------------------------------------------------
# P15-3.3：SUPERVISOR_MODE 完整验收（P16：SUPERVISOR_AUTO_ENABLED 别名已删除）
# ---------------------------------------------------------------------------

class TestP15SupervisorAutoEnabledIntegration(unittest.TestCase):
    """P15-3.3：SUPERVISOR_MODE=llm 触发 LLM 模式，失败时回退 rule（P16：SUPERVISOR_AUTO_ENABLED 别名已删除）"""

    def _call_supervisor(self, env_overrides: dict, gateway=None) -> dict:
        from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler
        payload = {
            "scene_type": "WORK_CODE",
            "user_input": "帮我写一个快速排序算法",
        }
        context = {"request_id": "p15-integration", "gateway": gateway}
        with patch.dict(os.environ, env_overrides, clear=False):
            return supervisor_dispatch_handler(payload, context)

    def test_auto_enabled_false_rule_mode(self):
        """SUPERVISOR_MODE=rule → rule 模式（P16：替代 SUPERVISOR_AUTO_ENABLED=false）"""
        result = self._call_supervisor({"SUPERVISOR_MODE": "rule"})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "rule")
        self.assertEqual(dispatch.get("supervisor_decision_source"), "rule")

    def test_auto_enabled_true_llm_mode_no_gateway(self):
        """SUPERVISOR_MODE=llm + 无 gateway → LLM 模式但回退 rule（P16：替代 SUPERVISOR_AUTO_ENABLED=true）"""
        result = self._call_supervisor({"SUPERVISOR_MODE": "llm"})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")
        # 无 gateway 时回退 rule
        self.assertIn("fallback", str(dispatch.get("supervisor_decision_source") or "").lower())

    def test_auto_enabled_true_with_valid_llm_response(self):
        """SUPERVISOR_MODE=llm + 有效 LLM 响应 → LLM 决策（P16：替代 SUPERVISOR_AUTO_ENABLED=true）"""
        mock_gw = MagicMock()
        mock_gw.complete.return_value = {
            "content": '{"selected_agent": "code_agent", "reason": "代码任务", "risk_mode": "guarded", "need_judge": true}'
        }
        result = self._call_supervisor(
            {"SUPERVISOR_MODE": "llm"},
            gateway=mock_gw,
        )
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")
        self.assertEqual(dispatch.get("supervisor_decision_source"), "llm")
        self.assertEqual(result.get("active_agent_id"), "code_agent")

    def test_auto_enabled_true_with_bad_json_fallback(self):
        """SUPERVISOR_MODE=llm + LLM 返回非法 JSON → 回退 rule（P16：替代 SUPERVISOR_AUTO_ENABLED=true）"""
        mock_gw = MagicMock()
        mock_gw.complete.return_value = {"content": "这不是 JSON"}
        result = self._call_supervisor(
            {"SUPERVISOR_MODE": "llm"},
            gateway=mock_gw,
        )
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")
        self.assertIn("fallback", str(dispatch.get("supervisor_decision_source") or "").lower())

    def test_supervisor_mode_llm_env_var_still_works(self):
        """原有 SUPERVISOR_MODE=llm 开关应继续有效"""
        result = self._call_supervisor({"SUPERVISOR_MODE": "llm"})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")

    def test_both_enabled_llm_mode(self):
        """SUPERVISOR_MODE=llm 显式设置 → llm 模式（P16：SUPERVISOR_AUTO_ENABLED 已删除，无影响）"""
        result = self._call_supervisor({"SUPERVISOR_MODE": "llm"})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")

    def test_result_always_has_active_agent_id(self):
        """无论何种模式，结果都应包含 active_agent_id"""
        for env in [
            {"SUPERVISOR_MODE": "rule"},
            {"SUPERVISOR_MODE": "llm"},
        ]:
            result = self._call_supervisor(env)
            self.assertIn("active_agent_id", result)
            self.assertTrue(result.get("active_agent_id"))

    def test_result_always_has_supervisor_dispatch_field(self):
        """结果应包含 supervisor_dispatch 观测字段"""
        result = self._call_supervisor({"SUPERVISOR_MODE": "llm"})
        self.assertIn("supervisor_dispatch", result)
        dispatch = result["supervisor_dispatch"]
        self.assertIn("supervisor_mode", dispatch)
        self.assertIn("supervisor_decision_source", dispatch)

    def test_supervisor_auto_enabled_ignored_in_p16(self):
        """P16：SUPERVISOR_AUTO_ENABLED 别名已删除，单独设置 true 不再触发 LLM 模式"""
        result = self._call_supervisor({"SUPERVISOR_AUTO_ENABLED": "true", "SUPERVISOR_MODE": "rule"})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "rule")


# ---------------------------------------------------------------------------
# P15-3.4：P14 基线不退化（关键接口契约验证）
# ---------------------------------------------------------------------------

class TestP15P14BaselineNotRegressed(unittest.TestCase):
    """P15-3.4：P14 基线不退化"""

    def test_graph_executor_still_importable(self):
        """GraphExecutor 应可正常 import"""
        from host_brain.integrations.graph_executor import GraphExecutor
        self.assertTrue(callable(GraphExecutor))

    def test_agent_handler_map_still_importable(self):
        """_AGENT_HANDLER_MAP 应可正常 import"""
        from host_brain.integrations.graph_executor import _AGENT_HANDLER_MAP
        self.assertIsInstance(_AGENT_HANDLER_MAP, dict)

    def test_supervisor_dispatch_handler_still_importable(self):
        """supervisor_dispatch_handler 应可正常 import"""
        from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler
        self.assertTrue(callable(supervisor_dispatch_handler))

    def test_judge_agent_handler_still_importable(self):
        """judge_agent_handler 应可正常 import（P14 契约不变）"""
        from host_brain.app.handlers.judge_agent_handler import judge_agent_handler
        self.assertTrue(callable(judge_agent_handler))

    def test_tool_registry_still_importable(self):
        """tool_registry 应可正常 import（P13 契约不变）"""
        from host_brain.app.agents.tool_registry import execute_tool, get_tools_for_agent
        self.assertTrue(callable(execute_tool))
        self.assertTrue(callable(get_tools_for_agent))

    def test_judge_agent_tools_still_correct(self):
        """judge_agent 工具列表应仍为 P14 契约（evaluate_artifact + compare_artifacts）"""
        from host_brain.app.agents.tool_registry import get_tools_for_agent
        tools = get_tools_for_agent("judge_agent")
        tool_ids = {t.tool_id for t in tools}
        self.assertIn("evaluate_artifact", tool_ids)
        self.assertIn("compare_artifacts", tool_ids)

    def test_graph_executor_compile_runtime_graph_still_works(self):
        """GraphExecutor.compile_runtime_graph() 应仍可调用"""
        executor = _make_executor()
        graph = executor.compile_runtime_graph()
        self.assertIsNotNone(graph)

    def test_dispatch_to_agent_method_exists(self):
        """GraphExecutor 应有 _dispatch_to_agent 方法"""
        executor = _make_executor()
        self.assertTrue(hasattr(executor, "_dispatch_to_agent"))
        self.assertTrue(callable(executor._dispatch_to_agent))


if __name__ == "__main__":
    unittest.main()
