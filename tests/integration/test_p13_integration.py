"""P13 集成测试：tool_registry 工具实现升级 + P12 基线不退化。

验收标准覆盖：
  - 3.1：search_knowledge 工具执行后返回真实检索结果（非占位字符串）
  - 3.2：retrieval_agent allowlist 包含 search_by_keyword / search_by_vector，两者均有真实实现
  - 3.3：P12 基线（85 用例）不退化，tool_registry 接口契约不变
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from host_brain.app.agents.tool_registry import (
    ToolSpec,
    execute_tool,
    get_tools_for_agent,
    is_tool_allowed,
    _TOOL_REGISTRY,
    _AGENT_TOOLS,
)
from host_brain.app.agents.agent_registry import AGENT_ID_RETRIEVAL


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def _make_mock_recaller_with_docs():
    """构造带有预置文档的 mock SemanticRecaller。"""
    from host_brain.app.recall.semantic_recall import SemanticRecaller
    recaller = SemanticRecaller(use_mock=True)
    recaller.add_mock_document("doc-1", "深度学习是机器学习的子领域，专注于神经网络")
    recaller.add_mock_document("doc-2", "卷积神经网络在图像识别中表现优异")
    recaller.add_mock_document("doc-3", "自然语言处理利用深度学习技术处理文本")
    return recaller


def _make_gateway(final_answer: str = "检索完成") -> MagicMock:
    gateway = MagicMock()
    gateway.complete.return_value = {
        "content": json.dumps({
            "thought": "已完成",
            "action_type": "final_answer",
            "final_answer": final_answer,
        }, ensure_ascii=False)
    }
    return gateway


# ---------------------------------------------------------------------------
# 3.1：search_knowledge 真实检索集成验证
# ---------------------------------------------------------------------------

class TestSearchKnowledgeIntegration:
    """search_knowledge 工具真实检索集成验证（验收 3.1）。"""

    def test_search_knowledge_with_real_mock_recaller(self):
        """使用真实 mock SemanticRecaller，search_knowledge 返回真实结果。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_knowledge", {"query": "深度学习"})
        assert result.get("result") is not None
        assert "[知识库检索占位]" not in str(result.get("result", ""))

    def test_search_knowledge_returns_items_list(self):
        """search_knowledge 返回 items 列表（可为空，但字段存在）。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_knowledge", {"query": "神经网络"})
        assert "items" in result
        assert isinstance(result["items"], list)

    def test_search_knowledge_confidence_field(self):
        """search_knowledge 返回 confidence 字段，值在 0.0～1.0。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_knowledge", {"query": "机器学习"})
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0

    def test_search_knowledge_source_field(self):
        """search_knowledge 返回 source='knowledge_base'。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_knowledge", {"query": "测试"})
        assert result.get("source") == "knowledge_base"

    def test_search_knowledge_degraded_gracefully(self):
        """SemanticRecaller 降级时，search_knowledge 不抛异常，返回有意义结果。"""
        from host_brain.app.recall.semantic_recall import SemanticRecaller, SemanticRecallResult
        recaller = MagicMock()

        async def _degraded_recall(**kwargs):
            return SemanticRecallResult(
                hits=[], query="test", top_k=5, threshold=0.7,
                total_found=0, filtered_count=0, degraded=True,
                degraded_reason="test_degraded",
            )

        recaller.recall = _degraded_recall
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_knowledge", {"query": "测试"})
        assert result.get("result") is not None
        assert "[知识库检索占位]" not in str(result.get("result", ""))


# ---------------------------------------------------------------------------
# 3.2：search_by_keyword / search_by_vector 集成验证
# ---------------------------------------------------------------------------

class TestSearchByKeywordIntegration:
    """search_by_keyword 工具集成验证（验收 3.2）。"""

    def test_search_by_keyword_with_real_recaller(self):
        """使用真实 mock SemanticRecaller，search_by_keyword 返回真实结果。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_by_keyword", {"keyword": "深度学习"})
        assert result.get("source") == "keyword_index"
        assert "[知识库检索占位]" not in str(result.get("result", ""))

    def test_search_by_keyword_matched_count(self):
        """search_by_keyword 返回 matched_count 字段。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_by_keyword", {"keyword": "神经网络"})
        assert "matched_count" in result
        assert isinstance(result["matched_count"], int)

    def test_search_by_keyword_no_hits_graceful(self):
        """无命中时 search_by_keyword 返回有意义提示，不抛异常。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_by_keyword", {"keyword": "zzz_nonexistent_xyz"})
        assert result.get("result") is not None
        assert result.get("matched_count") == 0


class TestSearchByVectorIntegration:
    """search_by_vector 工具集成验证（验收 3.2）。"""

    def test_search_by_vector_with_real_recaller(self):
        """使用真实 mock SemanticRecaller，search_by_vector 返回真实结果。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_by_vector", {"query": "深度学习"})
        assert result.get("source") == "vector_store"
        assert "[知识库检索占位]" not in str(result.get("result", ""))

    def test_search_by_vector_top_k_field(self):
        """search_by_vector 返回 top_k 字段。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_by_vector", {"query": "神经网络"})
        assert "top_k" in result

    def test_search_by_vector_custom_threshold(self):
        """search_by_vector 支持自定义 threshold 参数。"""
        recaller = _make_mock_recaller_with_docs()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=recaller):
            result = execute_tool("search_by_vector", {"query": "测试", "threshold": 0.3})
        assert result.get("error") is None


# ---------------------------------------------------------------------------
# 3.3：P12 基线不退化
# ---------------------------------------------------------------------------

class TestP12BaselineNotDegraded:
    """P12 基线不退化（验收 3.3）。"""

    def test_retrieval_agent_handler_still_works(self):
        """P12 retrieval_agent_handler 在 P13 后仍正常工作。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = {
            "request_id": "p13-p12-baseline-001",
            "user_input": "查找深度学习资料",
            "active_agent_id": AGENT_ID_RETRIEVAL,
            "_gateway": _make_gateway("检索完成"),
        }
        result = retrieval_agent_handler(payload)
        trace = result.get("retrieval_agent_trace") or {}
        assert trace.get("mode") == "react_loop"

    def test_code_agent_handler_still_works(self):
        """P9 code_agent_handler 在 P13 后仍正常工作。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        payload = {"request_id": "p13-p9-baseline-001", "user_input": "写代码"}
        result = code_agent_handler(payload, context={"gateway": _make_gateway("代码草案")})
        assert result.get("code_agent_trace", {}).get("mode") == "react_loop"

    def test_translate_agent_handler_still_works(self):
        """P10 translate_agent_handler 在 P13 后仍正常工作。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        payload = {"request_id": "p13-p10-baseline-001", "user_input": "翻译文字"}
        result = translate_agent_handler(payload, context={"gateway": _make_gateway("Translation")})
        assert result.get("translate_agent_trace", {}).get("mode") == "react_loop"

    def test_react_loop_importable(self):
        """react_loop 模块仍可导入（P13 不改 react_loop）。"""
        from host_brain.app.agents.react_loop import run_react_loop  # noqa: F401

    def test_agent_registry_importable(self):
        """agent_registry 模块仍可导入（P13 不改 agent_registry）。"""
        from host_brain.app.agents.agent_registry import (  # noqa: F401
            AGENT_ID_CODE, AGENT_ID_TRANSLATE, AGENT_ID_RETRIEVAL,
        )

    def test_p12_integration_test_importable(self):
        """P12 集成测试模块仍可导入。"""
        import tests.integration.test_p12_integration  # noqa: F401

    def test_p11_integration_test_importable(self):
        """P11 集成测试模块仍可导入。"""
        import tests.integration.test_p11_integration  # noqa: F401

    def test_tool_registry_interface_unchanged(self):
        """tool_registry 四个核心接口仍可调用（契约不变）。"""
        # ToolSpec 可实例化
        spec = ToolSpec(
            tool_id="test", display_name="测试", description="测试工具",
            parameters={"type": "object", "properties": {}, "required": []},
        )
        assert spec.to_llm_schema()["name"] == "test"
        # execute_tool 可调用
        result = execute_tool("nonexistent", {})
        assert "error" in result
        # get_tools_for_agent 可调用
        tools = get_tools_for_agent("code_agent")
        assert isinstance(tools, list)
        # is_tool_allowed 可调用
        allowed = is_tool_allowed("analyze_code", "code_agent")
        assert isinstance(allowed, bool)

    def test_judge_agent_still_no_tools(self):
        """judge_agent 工具列表（P14 更新为 2 个裁判工具）。"""
        tools = get_tools_for_agent("judge_agent")
        tool_ids = {t.tool_id for t in tools}
        assert "evaluate_artifact" in tool_ids
        assert "compare_artifacts" in tool_ids

    def test_three_agents_coexist_no_interference(self):
        """三个 agent handler 在同一进程中交替调用不互相干扰。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler

        gw = _make_gateway("结果")
        r1 = code_agent_handler({"request_id": "p13-coexist-001", "user_input": "写代码"}, context={"gateway": gw})
        r2 = translate_agent_handler({"request_id": "p13-coexist-002", "user_input": "翻译"}, context={"gateway": gw})
        r3 = retrieval_agent_handler({"request_id": "p13-coexist-003", "user_input": "检索"}, context={"gateway": gw})

        assert r1.get("code_agent_trace", {}).get("mode") == "react_loop"
        assert r2.get("translate_agent_trace", {}).get("mode") == "react_loop"
        assert r3.get("retrieval_agent_trace", {}).get("mode") == "react_loop"


# ---------------------------------------------------------------------------
# 文件完整性
# ---------------------------------------------------------------------------

def test_p13_modified_files_exist():
    """P13 修改/新增文件存在（基线完整性）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "host_brain" / "app" / "agents" / "tool_registry.py",
        root / "tests" / "unit" / "test_tool_registry_p13.py",
        root / "tests" / "integration" / "test_p13_integration.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P13_SCOPE_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P13 文件须存在: {f}"


def test_p12_baseline_files_still_exist():
    """P12 基线文件在 P13 后仍存在（不退化）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "tests" / "integration" / "test_p12_integration.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P12_SCOPE_2026-03.md",
        root / "docs" / "50_project" / "MULTI_AGENT_P12_SEAL_RESULT_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P12 基线文件须存在: {f}"


def test_no_placeholder_in_tool_registry_source():
    """tool_registry.py 源码中不含旧占位字符串。"""
    root = Path(__file__).resolve().parents[2]
    source = (root / "host_brain" / "app" / "agents" / "tool_registry.py").read_text(encoding="utf-8")
    placeholder_patterns = [
        "[知识库检索占位]",
        "[代码分析占位]",
        "[翻译占位]",
    ]
    for pattern in placeholder_patterns:
        assert pattern not in source, f"tool_registry.py 源码仍含占位字符串: {pattern!r}"
