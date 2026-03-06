"""M33：场景路由专项测试（T-SCENE-ROUTE-01~08，T-SCENE-META-01~02）

验证 ModelPlane.md §4.1/4.2 的场景路由规格：
- CHAT 场景只跑 2 个节点（primary + renderer）
- core_reasoner_secondary 仅作为 fallback-only
- planner_constraints/tool_planner 只在 EXEC_INTENT 场景触发
- workbench_* 只在对应 WORK_* 场景触发
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.fastapi_app import (
    _resolve_scene_node_sequence,
    _build_chat_graph_config,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_WORK_TRANSLATE,
    SCENE_WORK_CODE,
    CHAT_GRAPH_NODE_ORDER,
)


def _make_mock_provider_fn() -> MagicMock:
    return MagicMock(return_value={"status": "OK"})


def _make_graph_config(scene_type: str = SCENE_CHAT) -> dict:
    """构建测试用 graph_config"""
    mock_fn = _make_mock_provider_fn()
    return _build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=mock_fn,
        scene_type=scene_type,
    )


class TestSceneNodeSequence:
    """T-SCENE-ROUTE-01~04：场景节点序列测试"""

    def test_chat_scene_returns_minimal_sequence(self):
        """T-SCENE-ROUTE-01：CHAT 场景节点序列 = [primary, renderer]"""
        seq = _resolve_scene_node_sequence(SCENE_CHAT)
        assert seq == ["core_reasoner_primary", "renderer_ja_c_style"]
        assert len(seq) == 2

    def test_exec_intent_scene_contains_planner_and_tool(self):
        """T-SCENE-ROUTE-02：EXEC_INTENT 场景含 planner_constraints 和 tool_planner"""
        seq = _resolve_scene_node_sequence(SCENE_EXEC_INTENT)
        assert "planner_constraints" in seq
        assert "tool_planner" in seq
        assert "safety_verifier" in seq
        assert "core_reasoner_primary" in seq
        assert "renderer_ja_c_style" in seq
        # 不含 workbench_*
        assert "workbench_translate" not in seq
        assert "workbench_code_assist" not in seq

    def test_work_translate_scene_contains_translate_not_code(self):
        """T-SCENE-ROUTE-03：WORK_TRANSLATE 场景含 workbench_translate，不含 workbench_code_assist"""
        seq = _resolve_scene_node_sequence(SCENE_WORK_TRANSLATE)
        assert "workbench_translate" in seq
        assert "workbench_code_assist" not in seq
        assert "core_reasoner_primary" in seq
        assert "renderer_ja_c_style" in seq

    def test_work_code_scene_contains_code_not_translate(self):
        """T-SCENE-ROUTE-04：WORK_CODE 场景含 workbench_code_assist，不含 workbench_translate"""
        seq = _resolve_scene_node_sequence(SCENE_WORK_CODE)
        assert "workbench_code_assist" in seq
        assert "workbench_translate" not in seq
        assert "core_reasoner_primary" in seq
        assert "renderer_ja_c_style" in seq

    def test_unknown_scene_falls_back_to_chat(self):
        """T-SCENE-ROUTE-08：未知场景退化为 CHAT 序列"""
        seq = _resolve_scene_node_sequence("UNKNOWN_SCENE_XYZ")
        assert seq == ["core_reasoner_primary", "renderer_ja_c_style"]

    def test_empty_scene_falls_back_to_chat(self):
        """空场景退化为 CHAT 序列"""
        seq = _resolve_scene_node_sequence("")
        assert seq == ["core_reasoner_primary", "renderer_ja_c_style"]

    def test_none_scene_falls_back_to_chat(self):
        """None 场景退化为 CHAT 序列"""
        seq = _resolve_scene_node_sequence(None)  # type: ignore
        assert seq == ["core_reasoner_primary", "renderer_ja_c_style"]

    def test_renderer_always_last(self):
        """renderer_ja_c_style 始终是最后一个节点（S2 段）"""
        for scene in [SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            seq = _resolve_scene_node_sequence(scene)
            assert seq[-1] == "renderer_ja_c_style", f"renderer not last in {scene}: {seq}"

    def test_primary_always_first(self):
        """core_reasoner_primary 始终是第一个节点（S1 段入口）"""
        for scene in [SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            seq = _resolve_scene_node_sequence(scene)
            assert seq[0] == "core_reasoner_primary", f"primary not first in {scene}: {seq}"

    def test_secondary_never_in_normal_sequence(self):
        """core_reasoner_secondary 不在任何场景的正常节点序列中"""
        for scene in [SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            seq = _resolve_scene_node_sequence(scene)
            assert "core_reasoner_secondary" not in seq, f"secondary in normal seq for {scene}: {seq}"


class TestGraphConfigSceneRouting:
    """T-SCENE-ROUTE-05~07：graph_config 场景路由测试"""

    def test_chat_scene_node_bindings_count_is_2(self):
        """T-SCENE-ROUTE-05：CHAT 场景 graph_config 的 edges 只有 1 条（primary→renderer），
        node_bindings 包含 2 个正常节点 + 1 个 fallback 节点（secondary）"""
        config = _make_graph_config(SCENE_CHAT)
        node_bindings = config["node_bindings"]
        node_ids = [nb["node_id"] for nb in node_bindings]
        assert "core_reasoner_primary" in node_ids
        assert "renderer_ja_c_style" in node_ids
        # edges 只有 1 条（正常链路）
        assert len(config["edges"]) == 1
        assert config["edges"][0]["from"] == "core_reasoner_primary"
        assert config["edges"][0]["to"] == "renderer_ja_c_style"

    def test_secondary_not_in_chat_edges(self):
        """T-SCENE-ROUTE-06：core_reasoner_secondary 不在 CHAT 场景的 edges（正常链路）中"""
        config = _make_graph_config(SCENE_CHAT)
        edge_nodes = set()
        for e in config["edges"]:
            edge_nodes.add(e["from"])
            edge_nodes.add(e["to"])
        assert "core_reasoner_secondary" not in edge_nodes

    def test_secondary_in_fallback_edges(self):
        """T-SCENE-ROUTE-07：core_reasoner_secondary 在 fallback_edges 中"""
        config = _make_graph_config(SCENE_CHAT)
        fallback_edges = config["fallback_edges"]
        fallback_targets = [e["to"] for e in fallback_edges]
        fallback_sources = [e["from"] for e in fallback_edges]
        # secondary 应该是 primary 的 fallback 目标
        assert "core_reasoner_secondary" in fallback_targets or "core_reasoner_secondary" in fallback_sources

    def test_exec_intent_node_bindings_count(self):
        """EXEC_INTENT 场景 edges 包含 5 个正常节点的链路"""
        config = _make_graph_config(SCENE_EXEC_INTENT)
        node_ids = [nb["node_id"] for nb in config["node_bindings"]]
        assert "planner_constraints" in node_ids
        assert "tool_planner" in node_ids
        assert "safety_verifier" in node_ids
        # edges 有 4 条（5个节点的链路）
        assert len(config["edges"]) == 4

    def test_work_translate_node_bindings(self):
        """WORK_TRANSLATE 场景 node_bindings 包含 workbench_translate"""
        config = _make_graph_config(SCENE_WORK_TRANSLATE)
        node_ids = [nb["node_id"] for nb in config["node_bindings"]]
        assert "workbench_translate" in node_ids
        assert "workbench_code_assist" not in node_ids
        # edges 有 2 条（3个节点的链路）
        assert len(config["edges"]) == 2

    def test_work_code_node_bindings(self):
        """WORK_CODE 场景 node_bindings 包含 workbench_code_assist"""
        config = _make_graph_config(SCENE_WORK_CODE)
        node_ids = [nb["node_id"] for nb in config["node_bindings"]]
        assert "workbench_code_assist" in node_ids
        assert "workbench_translate" not in node_ids
        # edges 有 2 条（3个节点的链路）
        assert len(config["edges"]) == 2

    def test_model_node_overrides_always_has_all_9_nodes(self):
        """model_node_overrides 始终包含全部 9 个节点（向后兼容）"""
        config = _make_graph_config(SCENE_CHAT)
        overrides = config["model_node_overrides"]
        for node_id in CHAT_GRAPH_NODE_ORDER:
            assert node_id in overrides, f"node {node_id} missing from model_node_overrides"

    def test_entry_node_is_primary(self):
        """entry_node 始终是 core_reasoner_primary"""
        for scene in [SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            config = _make_graph_config(scene)
            assert config["entry_node"] == "core_reasoner_primary"

    def test_edges_match_node_sequence(self):
        """edges 与场景节点序列的顺序一致（不含 fallback 节点）"""
        config = _make_graph_config(SCENE_CHAT)
        edges = config["edges"]
        # CHAT 场景只有 1 条边（primary → renderer）
        assert len(edges) == 1
        assert edges[0]["from"] == "core_reasoner_primary"
        assert edges[0]["to"] == "renderer_ja_c_style"


class TestGraphMetaSceneField:
    """T-SCENE-META-01~02：graph_meta 场景字段审计测试"""

    def test_chat_scene_meta_type(self):
        """T-SCENE-META-01：CHAT 场景 graph_meta.scene_type = 'CHAT'"""
        config = _make_graph_config(SCENE_CHAT)
        assert config["graph_meta"]["scene_type"] == "CHAT"

    def test_exec_intent_scene_meta_type(self):
        """T-SCENE-META-02：EXEC_INTENT 场景 graph_meta.scene_type = 'EXEC_INTENT'"""
        config = _make_graph_config(SCENE_EXEC_INTENT)
        assert config["graph_meta"]["scene_type"] == "EXEC_INTENT"

    def test_active_node_count_in_meta(self):
        """graph_meta.active_node_count 与场景节点序列长度一致（不含 fallback 节点）"""
        for scene in [SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            config = _make_graph_config(scene)
            expected_count = len(_resolve_scene_node_sequence(scene))
            assert config["graph_meta"]["active_node_count"] == expected_count, \
                f"scene={scene}: active_node_count={config['graph_meta']['active_node_count']} != {expected_count}"

    def test_online_chain_updated(self):
        """graph_meta.online_chain 包含 scene_routed 标识"""
        config = _make_graph_config(SCENE_CHAT)
        assert "scene_routed" in config["graph_meta"]["online_chain"]
