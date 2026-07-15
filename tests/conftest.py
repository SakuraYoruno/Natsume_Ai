"""Global pytest configuration and cross-test runtime state isolation."""

from __future__ import annotations

import atexit
import asyncio
import contextlib
import inspect
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest


_PYTEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="natsume-pytest-runtime-"))
_PYTEST_CANONICAL_DB = _PYTEST_RUNTIME_ROOT / "audit.sqlite3"
_PYTEST_DB_ENV_NAMES = (
    "NATSUME_AUDIT_DB",
    "HOST_AUDIT_SQLITE_PATH",
    "HOST_AUDIT_DB_PATH",
    "AUDIT_DB_PATH",
)


def _bind_pytest_persistence_env() -> None:
    for name in _PYTEST_DB_ENV_NAMES:
        os.environ[name] = str(_PYTEST_CANONICAL_DB)


_bind_pytest_persistence_env()
atexit.register(shutil.rmtree, _PYTEST_RUNTIME_ROOT, ignore_errors=True)

# Import the application composition only after the test-owned persistence
# environment exists. Importing ``host_brain.app`` may otherwise cache the
# developer .env database before pytest can isolate it.
from host_brain.app.process_service_wiring import configure_process_service_ports  # noqa: E402

configure_process_service_ports()


_RETIRED_PROMPT_SURFACE_COMPAT_TEST_FILES = {
    "test_action_planner.py",
    "test_agent_handler_prompt_templates.py",
    "test_assistant_followup_surface.py",
    "test_bond_aware_style_helpers.py",
    "test_drift_detector_templates.py",
    "test_emergent_growth_engine_templates.py",
    "test_emotion_agent_handler.py",
    "test_emotion_context_profile_templates.py",
    "test_emotion_llm_inference.py",
    "test_evolution_desire_engine_helpers.py",
    "test_evolution_orchestrator_helpers.py",
    "test_expression_adapter_helpers.py",
    "test_expression_adapter_style_hint_templates.py",
    "test_fallback_emotion.py",
    "test_handler_runtime_templates_extra.py",
    "test_http_hooks_runtime_helper_templates.py",
    "test_immune_agent_templates.py",
    "test_inner_world_templates.py",
    "test_input_routes_worker.py",
    "test_input_service_quality_templates.py",
    "test_intelligent_runtime.py",
    "test_intent_agent_entry_handler_templates.py",
    "test_interaction_pattern_analyzer.py",
    "test_language_system_agent.py",
    "test_long_absent_behavior_matrix.py",
    "test_motive_runtime_background.py",
    "test_persona_engine_phase1.py",
    "test_persona_phase4.py",
    "test_proactive_voice_templates.py",
    "test_prompt_builder_emotion_binding_templates.py",
    "test_prompt_builder_helpers_extra.py",
    "test_prompt_context_emotion_pulse_templates.py",
    "test_react_loop_templates.py",
    "test_repair_agent_handler.py",
    "test_self_model_helpers_extra.py",
    "test_self_reflection_helpers.py",
    "test_sub_agent_protocol_templates.py",
    "test_trait_drift_guard_templates.py",
    "test_vision_ui_handler.py",
}

_CI_UNSTABLE_TEST_FILES = {
    *_RETIRED_PROMPT_SURFACE_COMPAT_TEST_FILES,
}


def _reset_host_brain_runtime_state(*, env_snapshot: dict[str, str] | None = None) -> None:
    """Best-effort singleton/runtime reset to reduce cross-test contamination."""

    try:
        import host_brain.composition.process_services as dep

        container = dep.get_process_service_container()
        capability_composition = container.capability_runtime_composition
        if capability_composition is not None:
            with contextlib.suppress(Exception):
                capability_composition.broker.close()
        mcp_registry = container.mcp_tool_registry
        if mcp_registry is not None:
            with contextlib.suppress(Exception):
                mcp_registry.close()
        bond_queue = container.bond_update_queue
        if bond_queue is not None:
            with contextlib.suppress(Exception):
                bond_queue.shutdown()
        ledger = container.ledger
        if ledger is not None:
            with contextlib.suppress(Exception):
                ledger.close()
        container.clear_service_references()
    except Exception:
        pass

    try:
        from host_brain.pentest.executor_guard import reset_guard

        reset_guard()
    except Exception:
        pass

    try:
        bond_routes = sys.modules.get("host_brain.app.bond_routes")
        asgi_app = sys.modules.get("host_brain.app.asgi")
        if bond_routes is not None:
            if hasattr(bond_routes, "set_bond_writer_provider"):
                bond_routes.set_bond_writer_provider(None)
            if asgi_app is not None and hasattr(asgi_app, "resolve_bond_engine"):
                bond_routes.set_bond_engine_provider(asgi_app.resolve_bond_engine)
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

    try:
        from host_brain.app.settings.persistence import get_persistence_settings
        from host_brain.input_runtime.settings import get_input_settings

        get_persistence_settings.cache_clear()
        get_input_settings.cache_clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_host_brain_singletons():
    """Reset runtime state before and after each test."""

    env_snapshot = dict(os.environ)
    _reset_host_brain_runtime_state()
    # Test-module collection is allowed to import configuration helpers, but
    # it must never redirect the actual test execution back to a developer DB.
    _bind_pytest_persistence_env()
    # Some architecture/static-scan contracts reload the process composition
    # modules.  Rebind the application-owned ports after every reset so later
    # tests never inherit a freshly reloaded, unconfigured container.
    configure_process_service_ports()
    from host_brain.observability.degradation_state import (
        DegradationStateMachine,
        set_global_degradation_state_machine,
    )

    # Production correctly restores the durable latch from audit.sqlite3. A
    # test process must not inherit whichever ignored developer DB happens to
    # exist in its worktree, so the default test boundary is an explicit
    # healthy in-memory machine. Degradation contracts install their own
    # machine and still exercise every fail-closed path.
    set_global_degradation_state_machine(DegradationStateMachine())
    try:
        yield
    finally:
        set_global_degradation_state_machine(None)
        _reset_host_brain_runtime_state(env_snapshot=env_snapshot)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: run test in asyncio event loop")
    # 测试环境强制同步执行 pentest kernel（跑完才返回），
    # 避免后台线程竞态导致断言在 run 完成前执行。
    os.environ.setdefault("PENTEST_BACKGROUND_RUN", "0")


# ─────────────────────────────────────────────────────
# OPTIMIZATION-PLAN-20260420 / OPT-2：按目录与文件名规则自动贴分层 marker
# ─────────────────────────────────────────────────────
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """按路径/文件名模式为测试项自动贴分层 marker。

    规则（优先级从上到下）：
      - tests/e2e/**                      → e2e
      - tests/integration/**              → integration
      - tests/contract/**                 → contract
      - tests/unit/**                     → unit
    附加规则（可与上面叠加）：
      - 文件名含 "_hw_contract" 或 "_hw"  → hw
      - 文件名含 "_prod_env"              → prod_env
      - 文件名含 "_runtime_probe"         → runtime_probe
      - 文件名含 "_docker"                → docker
    """

    for item in items:
        path_str = str(item.fspath).replace("\\", "/")
        filename = path_str.rsplit("/", 1)[-1]

        # 分层主标（互斥）
        if "/tests/e2e/" in path_str:
            item.add_marker(pytest.mark.e2e)
        elif "/tests/integration/" in path_str:
            item.add_marker(pytest.mark.integration)
        elif "/tests/contract/" in path_str:
            item.add_marker(pytest.mark.contract)
        elif "/tests/unit/" in path_str:
            item.add_marker(pytest.mark.unit)

        # 附加能力标（可叠加）
        if "_hw_contract" in filename or filename.endswith("_hw.py"):
            item.add_marker(pytest.mark.hw)
        if "_prod_env" in filename:
            item.add_marker(pytest.mark.prod_env)
        if "_runtime_probe" in filename:
            item.add_marker(pytest.mark.runtime_probe)
        if "_docker" in filename:
            item.add_marker(pytest.mark.docker)
        if filename in _CI_UNSTABLE_TEST_FILES:
            item.add_marker(pytest.mark.ci_unstable)


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
