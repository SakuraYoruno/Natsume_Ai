"""Global pytest configuration and cross-test runtime state isolation."""

from __future__ import annotations

import atexit
import asyncio
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

_RETIRED_PROMPT_SURFACE_COMPAT_TEST_FILES = {
    "test_bond_aware_style_helpers.py",
    "test_evolution_desire_engine_helpers.py",
    "test_evolution_orchestrator_helpers.py",
    "test_expression_adapter_helpers.py",
    "test_fallback_emotion.py",
    "test_input_routes_worker.py",
    "test_interaction_pattern_analyzer.py",
    "test_language_system_agent.py",
    "test_long_absent_behavior_matrix.py",
    "test_persona_engine_phase1.py",
    "test_persona_phase4.py",
    "test_prompt_builder_helpers_extra.py",
    "test_repair_agent_handler.py",
    "test_self_model_helpers_extra.py",
    "test_self_reflection_helpers.py",
}

_CI_UNSTABLE_TEST_FILES = {
    *_RETIRED_PROMPT_SURFACE_COMPAT_TEST_FILES,
}


def _reset_host_brain_runtime_state(*, env_snapshot: dict[str, str] | None = None) -> None:
    """Best-effort singleton/runtime reset to reduce cross-test contamination."""

    try:
        from host_brain.pentest.executor_guard import reset_guard

        reset_guard()
    except Exception:
        pass

    try:
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


@pytest.fixture()
def tribe_runtime_context(_reset_host_brain_singletons):
    """Bind one isolated execution-local Tribe graph for legacy behavior tests."""

    from host_brain.app.tribe_composition import (  # noqa: PLC0415
        build_production_tribe_agent_providers,
    )
    from host_brain.tribe.event_bus import InMemoryTribeEventBus  # noqa: PLC0415
    from host_brain.tribe.registry import HostTribeRegistry  # noqa: PLC0415
    from host_brain.tribe.runtime_context import (  # noqa: PLC0415
        TribeAgentCatalog,
        TribeRuntimeContext,
        activate_tribe_runtime_context,
        deactivate_tribe_runtime_context,
    )
    from host_brain.tribe.tribe_state import TribeStateRegistry  # noqa: PLC0415

    owner = object()
    context = TribeRuntimeContext(
        state_registry=TribeStateRegistry(),
        tribe_registry=HostTribeRegistry(),
        event_bus=InMemoryTribeEventBus(max_events=256),
        agent_catalog=TribeAgentCatalog(
            build_production_tribe_agent_providers()
        ),
        active_owner_provider=lambda: owner,
    )
    activation = activate_tribe_runtime_context(
        owner=owner,
        context=context,
    )
    try:
        yield context
    finally:
        deactivate_tribe_runtime_context(activation)


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
