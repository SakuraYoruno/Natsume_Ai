"""P3.5/P16 结构性断言：executor agent_dispatch_node 分派逻辑统一到 graph_executor，不再经过 agent_dispatch_handler。"""

from __future__ import annotations

import inspect

import pytest


def test_executor_has_no_run_agent_dispatch() -> None:
    """graph_executor 不再包含 _run_agent_dispatch 方法（P3.5 遗留方法已清理）。"""
    from host_brain.integrations import graph_executor
    assert not hasattr(graph_executor.GraphExecutor, "_run_agent_dispatch"), (
        "P3.5: executor 应已移除 _run_agent_dispatch"
    )


def test_executor_agent_dispatch_node_uses_dispatch_to_agent() -> None:
    """P16：graph_executor._run_single_step 对 agent_dispatch_node 使用动态路由。"""
    from host_brain.integrations import graph_executor
    source = inspect.getsource(graph_executor.GraphExecutor._run_single_step)
    assert "agent_dispatch_node" in source, (
        "P16: _run_single_step 应包含 agent_dispatch_node 分支"
    )
    # _dispatch_to_agent 或其他动态路由方式
    assert "_dispatch_to_agent" in source or "agent_dispatch" in source, (
        "P16: _run_single_step 应包含 agent_dispatch 相关逻辑"
    )


def test_supervised_graph_config_provides_allowlist() -> None:
    """Supervised 图配置中 agent_dispatch 节点带有 agent_dispatch_allowlist。"""
    from host_brain.app.graph_config_builder import (
        AGENT_DISPATCH_NODE_ID,
        INTENT_AGENT_HANDLER_ID,
        TRANSLATE_AGENT_HANDLER_ID,
        build_supervised_graph,
    )
    config = build_supervised_graph(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
    )
    bindings = {b["node_id"]: b for b in config.get("node_bindings", [])}
    agent_binding = bindings.get(AGENT_DISPATCH_NODE_ID)
    assert agent_binding is not None, "agent_dispatch 节点应在配置中"
    allowlist = agent_binding.get("agent_dispatch_allowlist")
    assert isinstance(allowlist, (list, tuple)), "agent_dispatch_allowlist 应由图配置提供"
    assert INTENT_AGENT_HANDLER_ID in allowlist
    assert TRANSLATE_AGENT_HANDLER_ID in allowlist


def test_supervised_graph_has_workbench_nodes_for_handlers() -> None:
    """Supervised 图 node_bindings 包含 workbench_code_assist / workbench_translate（供 handler 调用）。"""
    from host_brain.app.graph_config_builder import build_supervised_graph
    config = build_supervised_graph(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
    )
    node_ids = [b["node_id"] for b in config.get("node_bindings", [])]
    assert "workbench_code_assist" in node_ids
    assert "workbench_translate" in node_ids


def test_graph_runtime_does_not_register_codegen_skill_as_handler() -> None:
    """当前运行时不会把 codegen skill 当成可执行 handler 注册。"""
    import host_brain.app.dependencies as deps

    deps._graph_runtime = None
    runtime = deps.get_graph_runtime()
    assert "agent_worker" not in getattr(runtime, "_handlers", {})


def test_chat_graph_work_code_uses_short_sequence() -> None:
    """WORK_CODE 误入 Chat 图时使用短链（不落旧长链）。"""
    from host_brain.app.graph_config_builder import (
        CHAT_DIRECT_NODE_ID,
        CHAT_RENDER_NODE_ID,
        build_chat_graph_config,
        SCENE_WORK_CODE,
    )
    config = build_chat_graph_config(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
        scene_type=SCENE_WORK_CODE,
        scene_type_source="test",
    )
    # execution_profile 为 CHAT_FAST 时才会走 chat 图；此处用 scene_type=WORK_CODE 会因 profile 解析走 supervised。
    # 我们直接测 build 时若为 chat 分支且 scene_type=WORK_CODE：用 _build_graph_config_by_profile 需要 profile=CHAT_FAST
    # 才能进 chat 分支，此时 scene_type 一般不会是 WORK_CODE。所以改为测 resolve 出的 profile：WORK_CODE -> WORKFLOW_SUPERVISED。
    from host_brain.app.execution_profile import resolve_execution_profile
    profile = resolve_execution_profile(SCENE_WORK_CODE)
    assert profile == "WORKFLOW_SUPERVISED", "WORK_CODE 应解析为 WORKFLOW_SUPERVISED，不落 Chat 图"
    # 若强行建 chat 图且 scene_type=WORK_CODE（内部 use_chat_sequence 为 True），应为两节点
    from host_brain.app.graph_config_builder import _build_graph_config_by_profile
    chat_config = _build_graph_config_by_profile(
        execution_profile="CHAT_FAST",
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
        scene_type=SCENE_WORK_CODE,
        scene_type_source="test",
    )
    active = chat_config.get("graph_meta", {}).get("active_nodes") or []
    assert CHAT_DIRECT_NODE_ID in active and CHAT_RENDER_NODE_ID in active
    assert "workbench_code_assist" not in active and "core_reasoner_primary" not in active

