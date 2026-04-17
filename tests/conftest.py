"""Global pytest configuration and cross-test runtime state isolation."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import sys

import pytest


def _reset_host_brain_runtime_state(*, env_snapshot: dict[str, str] | None = None) -> None:
    """Best-effort singleton/runtime reset to reduce cross-test contamination."""

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
        dep._state_write_arbiter = None
        dep._distillation_engine = None
        dep._semantic_recaller = None
        dep._context_compressor = None
        dep._emotion_timeline = None
        dep._tool_catalog = None
        dep._tool_broker = None
        dep._mcp_tool_broker = None
        dep._shared_memory_index = None
        dep._milestone_tracker = None
        dep._hard_state = None
        dep._activity_monitor_ref = None

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

    try:
        bond_routes = sys.modules.get("host_brain.app.bond_routes")
        fastapi_app = sys.modules.get("host_brain.app.fastapi_app")
        if bond_routes is not None:
            if fastapi_app is not None and hasattr(fastapi_app, "_get_bond_engine_via_social_brain"):
                bond_routes.set_bond_engine_provider(fastapi_app._get_bond_engine_via_social_brain)
            else:
                bond_routes.set_bond_engine_provider(None)
    except Exception:
        pass

    try:
        obs_routes_sysmode = sys.modules.get("host_brain.app.obs_routes_sysmode")
        obs_routes = sys.modules.get("host_brain.app.obs_routes")
        if obs_routes_sysmode is not None:
            obs_routes_sysmode._mode_transition_service = None
        if obs_routes is not None and obs_routes_sysmode is not None:
            obs_routes._mode_transition_lock = obs_routes_sysmode._mode_transition_lock
            obs_routes._mode_transition_service = obs_routes_sysmode._mode_transition_service
    except Exception:
        pass

    try:
        tribe_state = sys.modules.get("host_brain.tribe.tribe_state")
        if tribe_state is not None:
            tribe_state._GLOBAL_REGISTRY = None

        limbic_agent = sys.modules.get("host_brain.tribe.limbic_agent")
        if limbic_agent is not None:
            limbic_agent._LIMBIC_AGENT = None

        prefrontal_agent = sys.modules.get("host_brain.tribe.prefrontal_agent")
        if prefrontal_agent is not None:
            prefrontal_agent._PREFRONTAL_AGENT = None

        social_brain_agent = sys.modules.get("host_brain.tribe.social_brain_agent")
        if social_brain_agent is not None:
            social_brain_agent._SOCIAL_BRAIN_AGENT = None

        endocrine_agent = sys.modules.get("host_brain.tribe.endocrine_agent")
        if endocrine_agent is not None:
            endocrine_agent._ENDOCRINE_AGENT = None

        pain_reward_agent = sys.modules.get("host_brain.tribe.pain_reward_agent")
        if pain_reward_agent is not None:
            pain_reward_agent._PAIN_REWARD_AGENT = None

        thalamus_agent = sys.modules.get("host_brain.tribe.thalamus_agent")
        if thalamus_agent is not None:
            thalamus_agent._THALAMUS_AGENT = None

        body_sense = sys.modules.get("host_brain.persona.body_sense")
        if body_sense is not None:
            body_sense._GLOBAL_BODY_SENSE = None

        silence = sys.modules.get("host_brain.persona.emotional_silence")
        if silence is not None:
            silence._GLOBAL_SILENCE_ENGINE = None

        inner_world = sys.modules.get("host_brain.persona.inner_world")
        if inner_world is not None:
            inner_world._global_inner_world = None

        response_policy = sys.modules.get("host_brain.persona.response_policy")
        if response_policy is not None:
            response_policy._layer = None

        drift_detector = sys.modules.get("host_brain.persona.drift_detector")
        if drift_detector is not None:
            drift_detector._GLOBAL_DRIFT_DETECTOR = None
    except Exception:
        pass

    try:
        system_handler = sys.modules.get("host_brain.app.system_handler")
        if system_handler is not None:
            system_handler._EDGE_ASR_BRIDGE_RECENT.clear()
            system_handler._EDGE_ASR_BRIDGE_LAST_TEXT.clear()
    except Exception:
        pass

    if env_snapshot is not None:
        try:
            os.environ.clear()
            os.environ.update(env_snapshot)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _reset_host_brain_singletons():
    """Reset runtime state before and after each test."""

    env_snapshot = dict(os.environ)
    _reset_host_brain_runtime_state()
    yield
    _reset_host_brain_runtime_state(env_snapshot=env_snapshot)


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
