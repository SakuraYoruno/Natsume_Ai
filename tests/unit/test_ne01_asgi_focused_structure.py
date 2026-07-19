from __future__ import annotations

import inspect

from host_brain.app import asgi
from host_brain.app import host_life_scheduler_wiring
from host_brain.app import host_runtime_context_wiring
from host_brain.app import host_runtime_startup_guards
from host_brain.app import host_runtime_shutdown
from host_brain.app import tool_runtime_composition


def test_asgi_composition_root_and_focused_helpers_stay_below_large_file_gate() -> None:
    assert len(inspect.getsource(asgi).splitlines()) < 1_500
    assert len(inspect.getsource(host_life_scheduler_wiring).splitlines()) < 1_500
    assert len(inspect.getsource(host_runtime_context_wiring).splitlines()) < 1_500
    assert len(inspect.getsource(host_runtime_startup_guards).splitlines()) < 1_500
    assert len(inspect.getsource(host_runtime_shutdown).splitlines()) < 1_500
    assert len(inspect.getsource(tool_runtime_composition).splitlines()) < 1_500
    assert (
        "host_brain.composition.process_services"
        not in inspect.getsource(tool_runtime_composition)
    )


def test_asgi_keeps_lifespan_state_and_reverse_close_order_in_one_scope() -> None:
    source = inspect.getsource(asgi.create_app)
    shutdown_source = inspect.getsource(
        host_runtime_shutdown.shutdown_host_runtime_authority
    )

    assert "async def app_lifespan(application: FastAPI):" in source
    assert "nonlocal security_container, command_ingress_container" in source
    assert source.count("finally:") >= 1
    assert "await shutdown_host_runtime_authority(" in source

    close_steps = (
        "drain_obs_operator_admission(security)",
        "scheduler.stop_clock",
        "task_runtime.aclose",
        "workspace_quiesce(",
        "host_lifecycle.stop_activity_monitor",
        "reset_life_command_authority",
        "embodiment_runtime.close()",
        "set_memory_service_container(None)",
        "memory.close()",
        "command_ingress.aclose()",
        "security.aclose()",
    )
    ordered_shutdown = shutdown_source[
        shutdown_source.index('error = await _attempt(\n        "obs_operator_admission"') :
    ]
    offsets = tuple(ordered_shutdown.index(step) for step in close_steps)
    assert offsets == tuple(sorted(offsets))


def test_focused_helpers_do_not_own_asgi_lifecycle() -> None:
    for module in (
        host_life_scheduler_wiring,
        host_runtime_context_wiring,
        host_runtime_startup_guards,
        tool_runtime_composition,
    ):
        source = inspect.getsource(module)
        assert "def create_app" not in source
        assert "def app_lifespan" not in source
        assert ".aclose()" not in source

    assert callable(asgi._compose_life_command_authority)
    assert callable(asgi._compose_life_dispatchers)
    assert callable(asgi._submit_life_tick_command)
    assert callable(asgi._command_ingress_hmac_secrets)
    assert callable(asgi._compose_host_runtime_context)
