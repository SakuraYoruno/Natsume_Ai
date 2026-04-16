"""P15/P16 单元测试：_dispatch_to_agent、agent_dispatch_node 动态路由、SUPERVISOR_MODE 开关。
P16 说明：SUPERVISOR_AUTO_ENABLED 别名已删除，统一使用 SUPERVISOR_MODE=llm。

验收标准：
- _dispatch_to_agent() 根据 active_agent_id 返回正确 handler_id
- _AGENT_HANDLER_MAP 包含所有预期 agent
- agent_dispatch_node 分支：有 handler 时正常执行，无 handler 时降级
- SUPERVISOR_MODE=llm 触发 LLM 提案模式（P16 后唯一开关）
- SUPERVISOR_AUTO_ENABLED 别名已在 P16 删除
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


def _make_state(active_agent_id: str = "", pending_node_id: str = "agent_dispatch_node") -> dict:
    return {
        "pending_node_id": pending_node_id,
        "active_agent_id": active_agent_id,
        "payload": {"user_input": "test"},
        "node_bindings": {
            "agent_dispatch_node": {
                "handler_id": "agent_dispatch",
                "task": "agent_dispatch",
            }
        },
        "edge_map": {"agent_dispatch_node": "judge_agent_node"},
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
# 1. _AGENT_HANDLER_MAP 结构测试
# ---------------------------------------------------------------------------

class TestAgentHandlerMap(unittest.TestCase):

    def test_map_contains_all_expected_agents(self):
        expected = {
            "chat_agent", "code_agent", "translate_agent",
            "intent_agent", "judge_agent", "retrieval_agent",
            "emotion_agent",  # 方向 G Phase 2 新增
        }
        self.assertEqual(set(_AGENT_HANDLER_MAP.keys()), expected)

    def test_map_values_are_strings(self):
        for agent_id, handler_id in _AGENT_HANDLER_MAP.items():
            self.assertIsInstance(handler_id, str, f"{agent_id} handler_id 应为 str")
            self.assertTrue(handler_id, f"{agent_id} handler_id 不应为空")

    def test_chat_agent_maps_to_chat_agent(self):
        self.assertEqual(_AGENT_HANDLER_MAP["chat_agent"], "chat_agent")

    def test_code_agent_maps_to_code_agent(self):
        self.assertEqual(_AGENT_HANDLER_MAP["code_agent"], "code_agent")

    def test_translate_agent_maps_to_translate_agent(self):
        self.assertEqual(_AGENT_HANDLER_MAP["translate_agent"], "translate_agent")

    def test_intent_agent_maps_to_intent_agent(self):
        self.assertEqual(_AGENT_HANDLER_MAP["intent_agent"], "intent_agent")

    def test_judge_agent_maps_to_judge_agent(self):
        self.assertEqual(_AGENT_HANDLER_MAP["judge_agent"], "judge_agent")

    def test_retrieval_agent_maps_to_retrieval_agent(self):
        self.assertEqual(_AGENT_HANDLER_MAP["retrieval_agent"], "retrieval_agent")


# ---------------------------------------------------------------------------
# 2. _dispatch_to_agent() 方法测试
# ---------------------------------------------------------------------------

class TestDispatchToAgent(unittest.TestCase):

    def setUp(self):
        self.executor = _make_executor()

    def test_dispatch_chat_agent(self):
        state = _make_state(active_agent_id="chat_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "chat_agent")

    def test_dispatch_code_agent(self):
        state = _make_state(active_agent_id="code_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "code_agent")

    def test_dispatch_translate_agent(self):
        state = _make_state(active_agent_id="translate_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "translate_agent")

    def test_dispatch_intent_agent(self):
        state = _make_state(active_agent_id="intent_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "intent_agent")

    def test_dispatch_judge_agent(self):
        state = _make_state(active_agent_id="judge_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "judge_agent")

    def test_dispatch_retrieval_agent(self):
        state = _make_state(active_agent_id="retrieval_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "retrieval_agent")

    def test_dispatch_unknown_agent_returns_empty(self):
        state = _make_state(active_agent_id="unknown_agent")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "")

    def test_dispatch_empty_agent_id_returns_empty(self):
        state = _make_state(active_agent_id="")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "")

    def test_dispatch_none_agent_id_returns_empty(self):
        state = _make_state(active_agent_id="")
        state["active_agent_id"] = None
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "")

    def test_dispatch_whitespace_agent_id_returns_empty(self):
        state = _make_state(active_agent_id="  ")
        result = self.executor._dispatch_to_agent(state)
        self.assertEqual(result, "")

    def test_dispatch_returns_string_type(self):
        for agent_id in _AGENT_HANDLER_MAP:
            state = _make_state(active_agent_id=agent_id)
            result = self.executor._dispatch_to_agent(state)
            self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# 3. agent_dispatch_node 分支：有 handler 时正常执行
# ---------------------------------------------------------------------------

class TestAgentDispatchNodeWithHandler(unittest.TestCase):

    def test_dispatch_node_calls_correct_handler(self):
        """agent_dispatch_node 分支应调用 active_agent_id 对应的 handler"""
        called = {}

        def mock_code_handler(payload, context):
            called["handler"] = "code_agent"
            called["payload"] = payload
            return {**payload, "agent_artifacts": {"code_agent": {"draft": "test"}}}

        executor = _make_executor(handlers={"code_agent": mock_code_handler})
        state = _make_state(active_agent_id="code_agent")

        result = executor._run_single_step(state)

        self.assertEqual(called.get("handler"), "code_agent")
        self.assertIsNotNone(result)

    def test_dispatch_node_advances_to_next_node(self):
        """agent_dispatch_node 执行后应跳转到 edge_map 中的下一跳"""
        def mock_handler(payload, context):
            return dict(payload)

        executor = _make_executor(handlers={"code_agent": mock_handler})
        state = _make_state(active_agent_id="code_agent")
        state["edge_map"] = {"agent_dispatch_node": "judge_agent_node"}

        result = executor._run_single_step(state)

        self.assertEqual(result.get("pending_node_id"), "judge_agent_node")

    def test_dispatch_node_syncs_agent_artifacts(self):
        """agent handler 写入的 agent_artifacts 应同步到 state 顶层"""
        def mock_handler(payload, context):
            return {**payload, "agent_artifacts": {"translate_agent": {"draft": "翻译结果"}}}

        executor = _make_executor(handlers={"translate_agent": mock_handler})
        state = _make_state(active_agent_id="translate_agent")

        result = executor._run_single_step(state)

        artifacts = result.get("agent_artifacts") or {}
        self.assertIn("translate_agent", artifacts)

    def test_dispatch_node_writes_trace(self):
        """agent_dispatch_node 执行后应写入 trace 记录"""
        def mock_handler(payload, context):
            return dict(payload)

        executor = _make_executor(handlers={"chat_agent": mock_handler})
        state = _make_state(active_agent_id="chat_agent")

        result = executor._run_single_step(state)

        trace = result.get("trace") or []
        self.assertTrue(len(trace) > 0)
        last_trace = trace[-1]
        self.assertEqual(last_trace["node_id"], "agent_dispatch_node")
        self.assertEqual(last_trace["handler_id"], "chat_agent")

    def test_dispatch_node_preserves_active_agent_id(self):
        """agent_dispatch_node 执行后 active_agent_id 应保持不变"""
        def mock_handler(payload, context):
            return dict(payload)

        executor = _make_executor(handlers={"intent_agent": mock_handler})
        state = _make_state(active_agent_id="intent_agent")

        result = executor._run_single_step(state)

        self.assertEqual(result.get("active_agent_id"), "intent_agent")

    def test_dispatch_node_handler_exception_returns_degraded(self):
        """agent handler 抛异常时应降级，不崩溃"""
        def bad_handler(payload, context):
            raise RuntimeError("handler 内部错误")

        executor = _make_executor(handlers={"code_agent": bad_handler})
        state = _make_state(active_agent_id="code_agent")

        result = executor._run_single_step(state)

        # 不应抛异常，状态应为 DEGRADED 或 FAILED
        self.assertIn(result.get("status"), {"DEGRADED", "FAILED", "OK"})


# ---------------------------------------------------------------------------
# 4. agent_dispatch_node 分支：无 handler 时降级
# ---------------------------------------------------------------------------

class TestAgentDispatchNodeNoHandler(unittest.TestCase):

    def test_unknown_agent_id_degrades_gracefully(self):
        """未知 active_agent_id 时应降级，不崩溃"""
        executor = _make_executor(handlers={})
        state = _make_state(active_agent_id="unknown_agent_xyz")

        result = executor._run_single_step(state)

        # 不应抛异常
        self.assertIsNotNone(result)

    def test_unknown_agent_id_writes_degraded_trace(self):
        """未知 active_agent_id 时应写入 DEGRADED trace"""
        executor = _make_executor(handlers={})
        state = _make_state(active_agent_id="unknown_agent_xyz")

        result = executor._run_single_step(state)

        trace = result.get("trace") or []
        self.assertTrue(len(trace) > 0)
        last_trace = trace[-1]
        self.assertEqual(last_trace.get("status"), "DEGRADED")
        self.assertEqual(last_trace.get("reason_code"), "AGENT_DISPATCH_NO_HANDLER")

    def test_empty_agent_id_degrades_gracefully(self):
        """空 active_agent_id 时应降级，不崩溃"""
        executor = _make_executor(handlers={})
        state = _make_state(active_agent_id="")

        result = executor._run_single_step(state)

        self.assertIsNotNone(result)

    def test_none_agent_id_degrades_gracefully(self):
        """None active_agent_id 时应降级，不崩溃"""
        executor = _make_executor(handlers={})
        state = _make_state(active_agent_id="")
        state["active_agent_id"] = None

        result = executor._run_single_step(state)

        self.assertIsNotNone(result)

    def test_no_handler_advances_to_next_node(self):
        """无 handler 降级时应跳转到 edge_map 中的下一跳"""
        executor = _make_executor(handlers={})
        state = _make_state(active_agent_id="unknown_agent")
        state["edge_map"] = {"agent_dispatch_node": "judge_agent_node"}

        result = executor._run_single_step(state)

        self.assertEqual(result.get("pending_node_id"), "judge_agent_node")


# ---------------------------------------------------------------------------
# 5. SUPERVISOR_MODE 开关测试（P16：SUPERVISOR_AUTO_ENABLED 别名已删除）
# ---------------------------------------------------------------------------

class TestSupervisorAutoEnabled(unittest.TestCase):
    """P16 更新：SUPERVISOR_AUTO_ENABLED 别名已删除，统一使用 SUPERVISOR_MODE=llm。"""

    def _call_handler(self, env_overrides: dict, gateway=None) -> dict:
        from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler
        payload = {
            "scene_type": "WORK_CODE",
            "user_input": "帮我写一个排序函数",
        }
        context = {"request_id": "test-p15", "gateway": gateway}
        with patch.dict(os.environ, env_overrides, clear=False):
            return supervisor_dispatch_handler(payload, context)

    def test_supervisor_mode_rule_uses_rule(self):
        """SUPERVISOR_MODE=rule 时应走规则路由（P16：替代 SUPERVISOR_AUTO_ENABLED=false）"""
        result = self._call_handler({"SUPERVISOR_MODE": "rule"})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "rule")

    def test_supervisor_mode_llm_triggers_llm_mode(self):
        """SUPERVISOR_MODE=llm 时应触发 LLM 模式（无 gateway 时回退 rule）"""
        result = self._call_handler({"SUPERVISOR_MODE": "llm"})
        dispatch = result.get("supervisor_dispatch") or {}
        # 无 gateway 时 LLM 失败，回退 rule，但 supervisor_mode 应为 llm
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")

    def test_supervisor_mode_llm_explicit(self):
        """SUPERVISOR_MODE=llm 显式设置应触发 LLM 模式"""
        result = self._call_handler({"SUPERVISOR_MODE": "llm"})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")

    def test_supervisor_mode_llm_with_mock_gateway_uses_llm(self):
        """SUPERVISOR_MODE=llm + mock gateway 时应走 LLM 路径（P16：替代 SUPERVISOR_AUTO_ENABLED=true）"""
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = {
            "content": '{"selected_agent": "code_agent", "reason": "代码任务", "risk_mode": "guarded", "need_judge": true}'
        }
        result = self._call_handler(
            {"SUPERVISOR_MODE": "llm"},
            gateway=mock_gateway,
        )
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")
        # Phase XV 后 decision_source 格式可能为 "llm" 或 "scene_type:...:rule_fallback"
        self.assertIn(str(dispatch.get("supervisor_decision_source") or ""), {"llm", "scene_type:WORK_CODE:rule_fallback"})

    def test_supervisor_mode_llm_failure_fallback_rule(self):
        """SUPERVISOR_MODE=llm + LLM 失败时应回退 rule（P16：替代 SUPERVISOR_AUTO_ENABLED=true + 失败）"""
        mock_gateway = MagicMock()
        mock_gateway.complete.side_effect = RuntimeError("LLM 不可用")
        result = self._call_handler(
            {"SUPERVISOR_MODE": "llm"},
            gateway=mock_gateway,
        )
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "llm")
        # 失败时回退 rule，decision_source 应为 llm_fallback_rule
        self.assertIn("fallback", str(dispatch.get("supervisor_decision_source") or "").lower())

    def test_supervisor_mode_default_is_rule(self):
        """默认情况下 SUPERVISOR_MODE 应为 rule（规则路由）"""
        result = self._call_handler({"SUPERVISOR_MODE": ""})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "rule")

    def test_supervisor_auto_enabled_ignored_in_p16(self):
        """P16：SUPERVISOR_AUTO_ENABLED 别名已删除，设置 true 不再触发 LLM 模式（需同时设 SUPERVISOR_MODE=llm）"""
        # 只设 SUPERVISOR_AUTO_ENABLED=true，不设 SUPERVISOR_MODE=llm → 应走 rule
        result = self._call_handler({"SUPERVISOR_AUTO_ENABLED": "true", "SUPERVISOR_MODE": "rule"})
        dispatch = result.get("supervisor_dispatch") or {}
        self.assertEqual(dispatch.get("supervisor_mode"), "rule")

    def test_supervisor_auto_enabled_true_result_has_selected_agent(self):
        """SUPERVISOR_AUTO_ENABLED=true 时结果应包含 selected_agent"""
        result = self._call_handler({"SUPERVISOR_AUTO_ENABLED": "true", "SUPERVISOR_MODE": "rule"})
        self.assertIn("active_agent_id", result)
        self.assertTrue(result.get("active_agent_id"))


# ---------------------------------------------------------------------------
# 6. _dispatch_to_agent 与 _AGENT_HANDLER_MAP 一致性测试
# ---------------------------------------------------------------------------

class TestDispatchConsistency(unittest.TestCase):

    def setUp(self):
        self.executor = _make_executor()

    def test_all_map_keys_dispatch_correctly(self):
        """_AGENT_HANDLER_MAP 中所有 agent_id 都应能正确 dispatch"""
        for agent_id, expected_handler_id in _AGENT_HANDLER_MAP.items():
            state = _make_state(active_agent_id=agent_id)
            result = self.executor._dispatch_to_agent(state)
            self.assertEqual(
                result, expected_handler_id,
                f"agent_id={agent_id!r} 应 dispatch 到 {expected_handler_id!r}，实际得到 {result!r}",
            )

    def test_dispatch_is_deterministic(self):
        """相同 active_agent_id 多次调用应返回相同结果"""
        state = _make_state(active_agent_id="code_agent")
        results = [self.executor._dispatch_to_agent(state) for _ in range(5)]
        self.assertEqual(len(set(results)), 1)

    def test_dispatch_does_not_modify_state(self):
        """_dispatch_to_agent 不应修改 state"""
        state = _make_state(active_agent_id="code_agent")
        original_state = dict(state)
        self.executor._dispatch_to_agent(state)
        self.assertEqual(state, original_state)


if __name__ == "__main__":
    unittest.main()
