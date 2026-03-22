"""全局测试配置：在每个测试后重置 host_brain 全局单例，防止测试间污染。"""
from __future__ import annotations

import asyncio
import contextlib
import inspect

import pytest


@pytest.fixture(autouse=True)
def _reset_host_brain_singletons():
    """每个测试前后重置 host_brain.app.dependencies 中的全局单例。

    这解决了以下问题：
    - 某些测试修改了 _intent_engine / _ledger / _skill_registry / _policy_cache
    - 导致后续测试使用了被污染的单例
    """
    yield
    # 测试结束后重置
    try:
        import host_brain.app.dependencies as dep
        dep._intent_engine = None
        dep._skill_registry = None
        dep._policy_cache = None
        dep._ledger = None
        dep._bond_stage_engine = None
        dep._retrieval_pipeline = None
        dep._retrieval_orchestrator = None
        dep._obs_metrics_collector = None
        dep._graph_runtime = None
        dep._main_chain = None
        dep._routing_matrix = None
        dep._recall_pack_builder = None
        dep._memory_guard = None
        dep._state_arbiter = None
        dep._distillation_engine = None
        dep._semantic_recaller = None
        dep._context_compressor = None
        dep._emotion_timeline = None
        dep._tool_catalog = None
        dep._tool_broker = None
        dep._mcp_tool_broker = None
        mcp_registry = getattr(dep, "_mcp_tool_registry", None)
        if mcp_registry is not None:
            with contextlib.suppress(Exception):
                mcp_registry.close()
        dep._mcp_tool_registry = None
        bond_queue = getattr(dep, "_bond_update_queue", None)
        if bond_queue is not None:
            with contextlib.suppress(Exception):
                bond_queue.shutdown()
        dep._bond_update_queue = None
    except Exception:
        pass


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: run test in asyncio event loop")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function):
    test_fn = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_fn):
        return None

    fixture_args = {
        arg_name: pyfuncitem.funcargs[arg_name]
        for arg_name in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(test_fn(**fixture_args))
    return True
