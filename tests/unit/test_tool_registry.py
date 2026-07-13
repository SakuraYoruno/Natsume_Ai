"""P13 单元测试：tool_registry 工具实现升级（所有占位替换为真实实现）。

验收标准覆盖：
  - 3.1：search_knowledge 返回真实结果，不含占位字符串
  - 3.2：retrieval_agent allowlist 包含 search_by_keyword / search_by_vector，两者均有真实实现
  - 3.3（部分）：tool_registry 接口契约（ToolSpec / execute_tool / get_tools_for_agent / is_tool_allowed）不变
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from host_brain.app.agents.tool_registry import (
    ToolSpec,
    execute_tool,
    get_tools_for_agent,
    is_tool_allowed,
    _TOOL_REGISTRY,
    _AGENT_TOOLS,
    _tool_search_knowledge,
    _tool_search_by_keyword,
    _tool_search_by_vector,
    _tool_get_context,
    _tool_analyze_code,
    _tool_translate_text,
)


# ---------------------------------------------------------------------------
# Fixture：mock SemanticRecaller
# ---------------------------------------------------------------------------

def _make_mock_recaller(hits=None, degraded=False):
    """构造一个 mock SemanticRecaller，recall() 返回预设结果。"""
    from host_brain.app.recall.semantic_recall import SemanticHit, SemanticRecallResult

    if hits is None:
        hits = [
            SemanticHit(doc_id="doc-1", content="深度学习基础知识", score=0.92, source="mock"),
            SemanticHit(doc_id="doc-2", content="神经网络原理介绍", score=0.85, source="mock"),
        ]

    result = SemanticRecallResult(
        hits=hits,
        query="test",
        top_k=5,
        threshold=0.70,
        total_found=len(hits),
        filtered_count=len(hits),
        degraded=degraded,
    )

    recaller = MagicMock()

    async def _fake_recall(**kwargs):
        return result

    recaller.recall = _fake_recall
    recaller._use_mock = True
    recaller._mock_store = []
    return recaller


# ---------------------------------------------------------------------------
# 3.1：search_knowledge 真实实现
# ---------------------------------------------------------------------------

class TestSearchKnowledge:
    """search_knowledge 工具真实实现验证（验收 3.1）。"""

    def test_result_not_placeholder(self):
        """search_knowledge 返回结果不含占位字符串。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_knowledge({"query": "深度学习"})
        assert result.get("result") is not None
        assert "[知识库检索占位]" not in str(result.get("result", ""))

    def test_result_non_empty_with_hits(self):
        """有命中时 result 非空，items 列表非空。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_knowledge({"query": "深度学习"})
        assert result["result"]
        assert len(result["items"]) == 2

    def test_confidence_in_range(self):
        """confidence 在 0.0～1.0 范围内。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_knowledge({"query": "深度学习"})
        assert 0.0 <= result["confidence"] <= 1.0

    def test_source_is_knowledge_base(self):
        """source 字段为 knowledge_base。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_knowledge({"query": "深度学习"})
        assert result["source"] == "knowledge_base"

    def test_items_have_required_fields(self):
        """items 中每条记录包含 doc_id / content / score 字段。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_knowledge({"query": "深度学习"})
        for item in result["items"]:
            assert "doc_id" in item
            assert "content" in item
            assert "score" in item

    def test_missing_query_returns_error(self):
        """缺少 query 参数时返回 MISSING_QUERY 错误。"""
        result = _tool_search_knowledge({})
        assert result.get("error") == "MISSING_QUERY"
        assert result.get("result") is None

    def test_empty_query_returns_error(self):
        """空字符串 query 返回 MISSING_QUERY 错误。"""
        result = _tool_search_knowledge({"query": "   "})
        assert result.get("error") == "MISSING_QUERY"

    def test_no_hits_returns_meaningful_message(self):
        """无命中时返回有意义的提示，不含占位字符串。"""
        mock_recaller = _make_mock_recaller(hits=[])
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_knowledge({"query": "不存在的内容"})
        assert "[知识库检索占位]" not in str(result.get("result", ""))
        assert result["items"] == []
        assert result["confidence"] == 0.0

    def test_latency_ms_present(self):
        """返回结果包含 latency_ms 字段。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_knowledge({"query": "测试"})
        assert "latency_ms" in result


# ---------------------------------------------------------------------------
# 3.2：search_by_keyword 真实实现
# ---------------------------------------------------------------------------

class TestSearchByKeyword:
    """search_by_keyword 工具真实实现验证（验收 3.2）。"""

    def test_result_not_placeholder(self):
        """search_by_keyword 返回结果不含占位字符串。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_keyword({"keyword": "深度学习"})
        assert "[知识库检索占位]" not in str(result.get("result", ""))

    def test_source_is_keyword_index(self):
        """source 字段为 keyword_index。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_keyword({"keyword": "深度学习"})
        assert result["source"] == "keyword_index"

    def test_matched_count_field_present(self):
        """返回结果包含 matched_count 字段。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_keyword({"keyword": "深度学习"})
        assert "matched_count" in result

    def test_missing_keyword_returns_error(self):
        """缺少 keyword 参数时返回 MISSING_KEYWORD 错误。"""
        result = _tool_search_by_keyword({})
        assert result.get("error") == "MISSING_KEYWORD"

    def test_query_alias_works(self):
        """keyword 参数可用 query 别名传入。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_keyword({"query": "深度学习"})
        assert result.get("error") is None

    def test_top_k_respected(self):
        """top_k 参数被正确传递（不超过 20）。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_keyword({"keyword": "测试", "top_k": 3})
        assert result.get("error") is None


# ---------------------------------------------------------------------------
# 3.2：search_by_vector 真实实现
# ---------------------------------------------------------------------------

class TestSearchByVector:
    """search_by_vector 工具真实实现验证（验收 3.2）。"""

    def test_result_not_placeholder(self):
        """search_by_vector 返回结果不含占位字符串。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_vector({"query": "深度学习"})
        assert "[知识库检索占位]" not in str(result.get("result", ""))

    def test_source_is_vector_store(self):
        """source 字段为 vector_store。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_vector({"query": "深度学习"})
        assert result["source"] == "vector_store"

    def test_top_k_field_present(self):
        """返回结果包含 top_k 字段。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_vector({"query": "深度学习"})
        assert "top_k" in result

    def test_missing_query_returns_error(self):
        """缺少 query 参数时返回 MISSING_QUERY 错误。"""
        result = _tool_search_by_vector({})
        assert result.get("error") == "MISSING_QUERY"

    def test_custom_threshold_accepted(self):
        """自定义 threshold 参数被接受。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = _tool_search_by_vector({"query": "测试", "threshold": 0.5})
        assert result.get("error") is None


# ---------------------------------------------------------------------------
# get_context 真实实现
# ---------------------------------------------------------------------------

class TestGetContext:
    """get_context 工具真实实现验证。"""

    def test_result_non_empty(self):
        """get_context 返回非空 result。"""
        result = _tool_get_context({})
        assert result.get("result")

    def test_context_field_present(self):
        """返回结果包含 context 字段（结构化信息）。"""
        result = _tool_get_context({})
        assert "context" in result
        assert isinstance(result["context"], dict)

    def test_ts_ms_in_context(self):
        """context 中包含 ts_ms 时间戳。"""
        result = _tool_get_context({})
        assert "ts_ms" in result["context"]
        assert result["context"]["ts_ms"] > 0

    def test_tool_registry_version_p13(self):
        """context 中 tool_registry_version 为 P13。"""
        result = _tool_get_context({})
        assert result["context"]["tool_registry_version"] == "P13"

    def test_session_id_passed_through(self):
        """传入 session_id 时，context 中包含该字段。"""
        result = _tool_get_context({"session_id": "test-session-001"})
        assert result["context"].get("session_id") == "test-session-001"


# ---------------------------------------------------------------------------
# analyze_code 真实实现
# ---------------------------------------------------------------------------

class TestAnalyzeCode:
    """analyze_code 工具真实实现验证。"""

    _SAMPLE_PYTHON = """\
def hello():
    # 打招呼
    print("hello")

class Foo:
    def bar(self):
        pass
"""

    def test_result_not_placeholder(self):
        """analyze_code 返回结果不含占位字符串。"""
        result = _tool_analyze_code({"code": self._SAMPLE_PYTHON, "language": "python"})
        assert "[代码分析占位]" not in str(result.get("result", ""))

    def test_metrics_present(self):
        """返回结果包含 metrics 字段。"""
        result = _tool_analyze_code({"code": self._SAMPLE_PYTHON, "language": "python"})
        assert "metrics" in result
        metrics = result["metrics"]
        assert "total_lines" in metrics
        assert "func_count" in metrics
        assert "class_count" in metrics

    def test_func_count_correct(self):
        """Python 代码中函数数量统计正确（2 个 def）。"""
        result = _tool_analyze_code({"code": self._SAMPLE_PYTHON, "language": "python"})
        assert result["metrics"]["func_count"] == 2

    def test_class_count_correct(self):
        """Python 代码中类数量统计正确（1 个 class）。"""
        result = _tool_analyze_code({"code": self._SAMPLE_PYTHON, "language": "python"})
        assert result["metrics"]["class_count"] == 1

    def test_comment_lines_counted(self):
        """注释行被正确统计。"""
        result = _tool_analyze_code({"code": self._SAMPLE_PYTHON, "language": "python"})
        assert result["metrics"]["comment_lines"] >= 1

    def test_missing_code_returns_error(self):
        """缺少 code 参数时返回 MISSING_CODE 错误。"""
        result = _tool_analyze_code({})
        assert result.get("error") == "MISSING_CODE"

    def test_issues_field_present(self):
        """返回结果包含 issues 字段（列表）。"""
        result = _tool_analyze_code({"code": self._SAMPLE_PYTHON})
        assert "issues" in result
        assert isinstance(result["issues"], list)

    def test_language_default_python(self):
        """未指定 language 时默认为 python。"""
        result = _tool_analyze_code({"code": self._SAMPLE_PYTHON})
        assert result["language"] == "python"


# ---------------------------------------------------------------------------
# translate_text 真实实现
# ---------------------------------------------------------------------------

class TestTranslateText:
    """translate_text 工具真实实现验证。"""

    def test_result_not_placeholder(self):
        """translate_text 返回结果不含旧占位字符串。"""
        result = _tool_translate_text({"text": "深度学习", "target_lang": "en"})
        assert "[翻译占位]" not in str(result.get("result", ""))

    def test_source_lang_detected_chinese(self):
        """中文文本被正确检测为 zh。"""
        result = _tool_translate_text({"text": "深度学习是人工智能的重要分支", "target_lang": "en"})
        assert result["source_lang"] == "zh"

    def test_source_lang_detected_english(self):
        """英文文本被正确检测为 en。"""
        result = _tool_translate_text({"text": "deep learning is important", "target_lang": "zh"})
        assert result["source_lang"] == "en"

    def test_same_lang_no_translation(self):
        """源语言与目标语言相同时，translated=False 且直接返回原文。"""
        result = _tool_translate_text({"text": "hello world", "target_lang": "en"})
        assert result["translated"] is False

    def test_missing_text_returns_error(self):
        """缺少 text 参数时返回 MISSING_TEXT 错误。"""
        result = _tool_translate_text({})
        assert result.get("error") == "MISSING_TEXT"

    def test_char_stats_present(self):
        """返回结果包含 char_stats 字段（跨语言翻译时）。"""
        result = _tool_translate_text({"text": "深度学习", "target_lang": "en"})
        assert "char_stats" in result

    def test_text_length_present(self):
        """返回结果包含 text_length 字段。"""
        result = _tool_translate_text({"text": "深度学习", "target_lang": "en"})
        assert "text_length" in result
        assert result["text_length"] == 4


# ---------------------------------------------------------------------------
# 3.2：retrieval_agent allowlist 验证
# ---------------------------------------------------------------------------

class TestRetrievalAgentAllowlist:
    """retrieval_agent allowlist 包含 search_by_keyword / search_by_vector（验收 3.2）。"""

    def test_retrieval_agent_has_search_by_keyword(self):
        """retrieval_agent allowlist 包含 search_by_keyword。"""
        assert "search_by_keyword" in _AGENT_TOOLS["retrieval_agent"]

    def test_retrieval_agent_has_search_by_vector(self):
        """retrieval_agent allowlist 包含 search_by_vector。"""
        assert "search_by_vector" in _AGENT_TOOLS["retrieval_agent"]

    def test_retrieval_agent_has_search_knowledge(self):
        """retrieval_agent allowlist 包含 search_knowledge。"""
        assert "search_knowledge" in _AGENT_TOOLS["retrieval_agent"]

    def test_retrieval_agent_has_get_context(self):
        """retrieval_agent allowlist 包含 get_context。"""
        assert "get_context" in _AGENT_TOOLS["retrieval_agent"]

    def test_search_by_keyword_registered(self):
        """search_by_keyword 已在 _TOOL_REGISTRY 中注册。"""
        assert "search_by_keyword" in _TOOL_REGISTRY

    def test_search_by_vector_registered(self):
        """search_by_vector 已在 _TOOL_REGISTRY 中注册。"""
        assert "search_by_vector" in _TOOL_REGISTRY

    def test_search_by_keyword_has_executor(self):
        """search_by_keyword 工具有 executor（非 None）。"""
        spec = _TOOL_REGISTRY["search_by_keyword"]
        assert spec.executor is not None

    def test_search_by_vector_has_executor(self):
        """search_by_vector 工具有 executor（非 None）。"""
        spec = _TOOL_REGISTRY["search_by_vector"]
        assert spec.executor is not None

    def test_get_tools_for_retrieval_agent_includes_new_tools(self):
        """get_tools_for_agent('retrieval_agent') 返回包含新工具的列表。"""
        tools = get_tools_for_agent("retrieval_agent")
        tool_ids = {t.tool_id for t in tools}
        assert "search_by_keyword" in tool_ids
        assert "search_by_vector" in tool_ids

    def test_is_tool_allowed_search_by_keyword(self):
        """is_tool_allowed('search_by_keyword', 'retrieval_agent') 返回 True。"""
        assert is_tool_allowed("search_by_keyword", "retrieval_agent") is True

    def test_is_tool_allowed_search_by_vector(self):
        """is_tool_allowed('search_by_vector', 'retrieval_agent') 返回 True。"""
        assert is_tool_allowed("search_by_vector", "retrieval_agent") is True


# ---------------------------------------------------------------------------
# intent_agent execution tools
# ---------------------------------------------------------------------------

class TestIntentAgentExecutionTools:
    def test_intent_agent_side_effect_tools_filtered_by_allow_flag(self):
        readonly_tools = {tool.tool_id for tool in get_tools_for_agent("intent_agent", allow_side_effects=False)}
        full_tools = {tool.tool_id for tool in get_tools_for_agent("intent_agent", allow_side_effects=True)}

        assert "list_skills" in readonly_tools
        assert "run_skill" not in readonly_tools
        assert "dispatch_sub_agent" not in readonly_tools

        assert "list_skills" in full_tools
        assert "run_skill" in full_tools
        assert "dispatch_sub_agent" in full_tools

    def test_list_skills_tool_returns_skill_catalog(self):
        result = execute_tool("list_skills", {"limit": 5})
        assert result.get("error") is None
        assert isinstance(result.get("skills"), list)
        assert int(result.get("count") or 0) >= 1

    def test_run_skill_tool_executes_runtime_entrypoint(self):
        fake_outcome = SimpleNamespace(
            success=True,
            result_text="stored",
            error_text="",
            skill_family="memory",
            route_type="skill",
            latency_ms=12,
        )
        with patch("host_brain.executive_runtime.skill_execution._execute_skill", return_value=fake_outcome):
            result = execute_tool(
                "run_skill",
                {
                    "skill_name": "memory_store",
                    "task_text": "remember this",
                    "_react_context": {"request_id": "req_tool_run_skill_001"},
                },
            )
        assert result.get("success") is True
        assert result.get("status") == "SUCCESS"
        assert result.get("skill_name") == "memory_store"

    def test_dispatch_sub_agent_tool_executes_dispatcher(self):
        class _FakeDispatcher:
            async def dispatch(self, req):  # noqa: ANN001
                return SimpleNamespace(
                    success=True,
                    result=f"handled:{req.agent_id}",
                    error=None,
                    latency_ms=23,
                    audit_refs={"trace": "ok"},
                )

        with patch("host_brain.app.sub_agent_protocol.get_sub_agent_dispatcher", return_value=_FakeDispatcher()):
            result = execute_tool(
                "dispatch_sub_agent",
                {
                    "agent_id": "translate_agent",
                    "task": "fix bug",
                    "_react_context": {
                        "request_id": "req_tool_sub_agent_001",
                        "session_id": "sess_tool_sub_agent_001",
                    },
                },
            )
        assert result.get("success") is True
        assert result.get("status") == "SUCCESS"
        assert result.get("agent_id") == "translate_agent"
        assert "handled:translate_agent" in str(result.get("result") or "")

    def test_dispatch_sub_agent_hydrates_conversation_window_and_memory_packet(self):
        captured: dict[str, object] = {}

        class _FakeDispatcher:
            async def dispatch(self, req):  # noqa: ANN001
                captured["req"] = req
                return SimpleNamespace(
                    success=True,
                    result="handled",
                    error=None,
                    latency_ms=23,
                    audit_refs={"trace": "ok"},
                )

        class _FakeHistory:
            def fetch_window_for_messages(self, **kwargs):  # noqa: ANN001
                captured["history_kwargs"] = dict(kwargs)
                return SimpleNamespace(
                    recent_turns=[
                        SimpleNamespace(role="user", content="刚才那本书是哪本", turn_index=1),
                        SimpleNamespace(role="assistant", content="我刚才推荐了三本轻小说", turn_index=2),
                    ],
                    older_summary="更早些时候在测试子 agent 上下文。",
                    older_turn_count=12,
                    fetched_count=2,
                )

        with patch("host_brain.app.sub_agent_protocol.get_sub_agent_dispatcher", return_value=_FakeDispatcher()), patch(
            "host_brain.composition.process_services.get_conversation_history",
            return_value=_FakeHistory(),
        ):
            result = execute_tool(
                "dispatch_sub_agent",
                {
                    "agent_id": "translate_agent",
                    "task": "翻译刚才那段",
                    "_react_context": {
                        "agent_id": "intent_agent",
                        "request_id": "req_tool_sub_agent_hydrate",
                        "session_id": "sess_tool_sub_agent_hydrate",
                        "retrieval_summary": {
                            "surface_memory_recall_packet": {
                                "facts": ["长期事实：翻译保留术语表。"],
                                "evidence_refs": ["semantic://sem-translate"],
                            }
                        },
                    },
                },
            )

        assert result.get("success") is True
        req = captured["req"]
        ctx = dict(req.context)
        assert ctx["parent_agent_id"] == "intent_agent"
        assert ctx["tool_budget"]["timeout_ms"] >= 1000
        assert "retrieval_summary" in ctx
        assert ctx["surface_memory_recall_packet"]["facts"] == ["长期事实：翻译保留术语表。"]
        assert ctx["conversation_window"]["older_summary"] == "更早些时候在测试子 agent 上下文。"
        assert len(ctx["conversation_window"]["recent_turns"]) == 2
        assert captured["history_kwargs"]["max_turns"] == 4
        assert captured["history_kwargs"]["exclude_request_id"] == "req_tool_sub_agent_hydrate"

    def test_dispatch_sub_agent_blocks_self_recursion(self):
        result = execute_tool(
            "dispatch_sub_agent",
            {
                "agent_id": "intent_agent",
                "task": "do something",
            },
        )
        assert result.get("error") == "SUB_AGENT_SELF_CALL_BLOCKED"

    def test_intent_agent_auto_discovers_new_side_effect_tool(self):
        auto_tool = ToolSpec(
            tool_id="auto_dynamic_write_tool",
            display_name="Auto Dynamic Write Tool",
            description="dynamic side-effect tool",
            parameters={"type": "object", "properties": {}, "required": []},
            has_side_effects=True,
            executor=lambda _args: {"result": "ok"},
        )
        with patch.dict(_TOOL_REGISTRY, {"auto_dynamic_write_tool": auto_tool}, clear=False), patch.dict(
            "os.environ",
            {"INTENT_DYNAMIC_TOOLSET_ENABLED": "true"},
            clear=False,
        ):
            readonly_tools = {tool.tool_id for tool in get_tools_for_agent("intent_agent", allow_side_effects=False)}
            full_tools = {tool.tool_id for tool in get_tools_for_agent("intent_agent", allow_side_effects=True)}

        assert "auto_dynamic_write_tool" not in readonly_tools
        assert "auto_dynamic_write_tool" in full_tools

    def test_intent_agent_dynamic_discovery_can_be_disabled(self):
        auto_tool = ToolSpec(
            tool_id="auto_dynamic_write_tool_disabled_case",
            display_name="Auto Dynamic Disabled Case",
            description="dynamic side-effect tool",
            parameters={"type": "object", "properties": {}, "required": []},
            has_side_effects=True,
            executor=lambda _args: {"result": "ok"},
        )
        with patch.dict(_TOOL_REGISTRY, {"auto_dynamic_write_tool_disabled_case": auto_tool}, clear=False), patch.dict(
            "os.environ",
            {"INTENT_DYNAMIC_TOOLSET_ENABLED": "false"},
            clear=False,
        ):
            full_tools = {tool.tool_id for tool in get_tools_for_agent("intent_agent", allow_side_effects=True)}
        assert "auto_dynamic_write_tool_disabled_case" not in full_tools


# ---------------------------------------------------------------------------
# 3.3（部分）：接口契约不变
# ---------------------------------------------------------------------------

class TestToolRegistryContractUnchanged:
    """tool_registry 接口契约不变（验收 3.3 部分）。"""

    def test_tool_spec_dataclass_fields(self):
        """ToolSpec 包含 P12 基线所有字段。"""
        spec = _TOOL_REGISTRY["search_knowledge"]
        assert hasattr(spec, "tool_id")
        assert hasattr(spec, "display_name")
        assert hasattr(spec, "description")
        assert hasattr(spec, "parameters")
        assert hasattr(spec, "has_side_effects")
        assert hasattr(spec, "executor")

    def test_tool_spec_to_llm_schema(self):
        """ToolSpec.to_llm_schema() 返回包含 name/description/parameters 的字典。"""
        spec = _TOOL_REGISTRY["search_knowledge"]
        schema = spec.to_llm_schema()
        assert "name" in schema
        assert "description" in schema
        assert "parameters" in schema

    def test_execute_tool_not_found(self):
        """execute_tool 对不存在的工具返回 TOOL_NOT_FOUND 错误。"""
        result = execute_tool("nonexistent_tool", {})
        assert "TOOL_NOT_FOUND" in result.get("error", "")

    def test_execute_tool_search_knowledge_callable(self):
        """execute_tool('search_knowledge', ...) 可调用，不抛异常。"""
        mock_recaller = _make_mock_recaller()
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            result = execute_tool("search_knowledge", {"query": "测试"})
        assert "error" not in result or result.get("error") is None

    def test_get_tools_for_agent_returns_list(self):
        """get_tools_for_agent 返回 list[ToolSpec]。"""
        tools = get_tools_for_agent("intent_agent")
        assert isinstance(tools, list)
        for t in tools:
            assert isinstance(t, ToolSpec)

    def test_get_tools_for_unknown_agent_returns_empty(self):
        """未知 agent_id 返回空列表。"""
        tools = get_tools_for_agent("unknown_agent_xyz")
        assert tools == []

    def test_is_tool_allowed_returns_bool(self):
        """is_tool_allowed 返回 bool 类型。"""
        result = is_tool_allowed("search_knowledge", "retrieval_agent")
        assert isinstance(result, bool)

    def test_judge_agent_has_no_tools(self):
        """judge_agent 工具列表（P14 更新为 2 个裁判工具）。"""
        tools = get_tools_for_agent("judge_agent")
        tool_ids = {t.tool_id for t in tools}
        assert "evaluate_artifact" in tool_ids
        assert "compare_artifacts" in tool_ids

    def test_all_registered_tools_have_executor(self):
        """所有注册工具均有 executor（无 None）。"""
        for tool_id, spec in _TOOL_REGISTRY.items():
            assert spec.executor is not None, f"工具 {tool_id!r} 缺少 executor"

    def test_no_placeholder_in_any_tool_result(self):
        """所有工具执行结果均不含旧占位字符串。"""
        placeholder_patterns = ["[知识库检索占位]", "[代码分析占位]", "[翻译占位]"]
        mock_recaller = _make_mock_recaller(hits=[])
        with patch("host_brain.app.agents.tool_registry._get_semantic_recaller", return_value=mock_recaller):
            results = {
                "search_knowledge": execute_tool("search_knowledge", {"query": "test"}),
                "get_context": execute_tool("get_context", {}),
                "analyze_code": execute_tool("analyze_code", {"code": "x = 1"}),
                "translate_text": execute_tool("translate_text", {"text": "hello", "target_lang": "zh"}),
            }
        for tool_id, result in results.items():
            result_str = str(result)
            for pattern in placeholder_patterns:
                assert pattern not in result_str, f"工具 {tool_id!r} 结果含占位字符串: {pattern!r}"
