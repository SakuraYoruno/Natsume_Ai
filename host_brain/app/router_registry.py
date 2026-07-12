from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from host_brain.app.agent_routes import router as agent_router
from host_brain.app.automation.scheduler_routes import router as host_scheduler_router
from host_brain.app.bond_routes import (
    router as bond_router,
    set_bond_engine_provider as _set_bond_engine_provider,
    set_bond_writer_provider as _set_bond_writer_provider,
)
from host_brain.app.canon_routes import router as canon_router, set_ledger_provider as _set_canon_ledger_provider
from host_brain.app.health_routes import router as health_router
from host_brain.app.input_routes import router as input_router
from host_brain.app.internal_agent_routes import router as internal_codex_router
from host_brain.app.internal_security_immune_routes import router as internal_security_immune_router
from host_brain.app.internal_tribe_routes import router as internal_tribe_router
from host_brain.app.digital_life_routes import router as digital_life_router
from host_brain.app.memory_routes import router as memory_router
from host_brain.app.mateengine_routes import router as mateengine_router
from host_brain.app.pentest_secure_run_routes import (
    PentestSecureRunCompositionProvider,
    create_pentest_secure_run_http_router,
)
from host_brain.app.pentest_routes import router as pentest_router
from host_brain.app.prompt_config_routes import router as prompt_config_router
from host_brain.app.tool_routes import router as tool_router, tool_kits_alias_router, set_tool_providers as _set_tool_providers
from host_brain.observability.command_http import (
    ObsCommandIngressProvider,
    create_obs_command_http_router,
)
from host_brain.scheduler.scheduler_routes import router as scheduler_router, set_scheduler_providers as _set_scheduler_providers
from host_brain.skills.signed_intake_http import (
    SignedSkillProviderIntakeProvider,
    create_signed_skill_provider_intake_router,
)


def wire_route_providers(
    *,
    get_ledger: Callable[[], Any],
    get_tool_broker: Callable[[], Any],
    get_tool_catalog: Callable[[], Any],
    get_bond_engine: Callable[[], Any],
    logger: logging.Logger,
) -> None:
    _set_canon_ledger_provider(get_ledger)
    _set_tool_providers(
        broker_provider=get_tool_broker,
        catalog_provider=get_tool_catalog,
        ledger_provider=get_ledger,
    )
    _set_bond_engine_provider(get_bond_engine)
    from host_brain.app.dependencies import get_bond_dynamics_writer  # noqa: PLC0415

    _set_bond_writer_provider(get_bond_dynamics_writer)
    try:
        from host_brain.scheduler.health import SchedulerHealthReporter  # noqa: PLC0415
        from host_brain.scheduler.task_registry import SchedulerTaskRegistry  # noqa: PLC0415

        scheduler_registry = SchedulerTaskRegistry.get_instance()
        scheduler_health = SchedulerHealthReporter(registry=scheduler_registry)
        _set_scheduler_providers(
            registry=scheduler_registry,
            health_reporter=scheduler_health,
        )
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning("scheduler provider wiring degraded: %s", exc)


def register_host_routers(
    app: FastAPI,
    *,
    obs_command_ingress_provider: ObsCommandIngressProvider | None = None,
    pentest_secure_run_composition_provider: (
        PentestSecureRunCompositionProvider | None
    ) = None,
    signed_skill_provider_intake_provider: (
        SignedSkillProviderIntakeProvider | None
    ) = None,
) -> None:
    app.include_router(canon_router)
    app.include_router(scheduler_router)
    app.include_router(host_scheduler_router, prefix="/host-scheduler")
    app.include_router(tool_router)
    app.include_router(tool_kits_alias_router)
    app.include_router(prompt_config_router)
    app.include_router(bond_router)
    app.include_router(agent_router)
    app.include_router(input_router)
    app.include_router(internal_tribe_router)
    app.include_router(internal_codex_router)
    app.include_router(internal_security_immune_router)
    app.include_router(digital_life_router)
    app.include_router(memory_router)
    app.include_router(mateengine_router)
    app.include_router(
        create_obs_command_http_router(
            ingress_provider=obs_command_ingress_provider,
        )
    )
    app.include_router(
        create_pentest_secure_run_http_router(
            composition_provider=pentest_secure_run_composition_provider,
        )
    )
    app.include_router(
        create_signed_skill_provider_intake_router(
            ingress_provider=signed_skill_provider_intake_provider,
        )
    )
    app.include_router(pentest_router)
    app.include_router(health_router)


__all__ = ["register_host_routers", "wire_route_providers"]
