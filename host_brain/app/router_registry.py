from __future__ import annotations

import logging
from collections.abc import Callable
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from host_brain.app.agent_routes import router as agent_router
from host_brain.app.automation.scheduler_routes import router as host_scheduler_router
from host_brain.app.bond_routes import (
    router as bond_router,
    set_bond_engine_provider as _set_bond_engine_provider,
    set_bond_writer_provider as _set_bond_writer_provider,
)
from host_brain.app.canon_routes import (
    router as canon_router,
    set_canon_service_provider as _set_canon_service_provider,
    set_ledger_provider as _set_canon_ledger_provider,
)
from host_brain.app.health_routes import router as health_router
from host_brain.app.input_routes import (
    GovernedCommandApplicationProvider,
    create_input_router,
)
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
from host_brain.observability.operator_query_gateway import OperatorQueryGateway
from host_brain.scheduler.scheduler_routes import router as scheduler_router
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
    canon_service_provider: Callable[[], Any | None] | None = None,
) -> None:
    if canon_service_provider is None:
        _set_canon_ledger_provider(get_ledger)
    else:
        _set_canon_service_provider(canon_service_provider)
    _set_tool_providers(
        broker_provider=get_tool_broker,
        catalog_provider=get_tool_catalog,
        ledger_provider=get_ledger,
    )
    _set_bond_engine_provider(get_bond_engine)
    from host_brain.app.dependencies import get_bond_dynamics_writer  # noqa: PLC0415

    _set_bond_writer_provider(get_bond_dynamics_writer)


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
    governed_command_application_provider: (
        GovernedCommandApplicationProvider | None
    ) = None,
    internal_agent_ingress_provider: Callable[[], Any | None] | None = None,
    internal_agent_command_service_provider: Callable[[], Any | None] | None = None,
    scheduler_command_ingress_provider: Callable[[], Any | None] | None = None,
    scheduler_command_service_provider: Callable[[], Any | None] | None = None,
) -> None:
    operator_query_gateway = OperatorQueryGateway.from_environment()
    app.state.operator_query_gateway = operator_query_gateway
    async def require_internal_agent_authority(request: Request) -> Any:
        ingress = (
            internal_agent_ingress_provider()
            if internal_agent_ingress_provider is not None
            else None
        )
        if ingress is None:
            raise HTTPException(status_code=503, detail="INTERNAL_AGENT_INGRESS_UNAVAILABLE")
        authority = ingress.evaluate_http(
            request=request,
            raw_body=await request.body(),
            received_at_ms=int(
                getattr(request.state, "request_started_ms", 0)
                or time.time() * 1000
            ),
        )
        if not authority.admissible:
            status_code = 503 if "ENFORCE" in authority.reason_code else 403
            raise HTTPException(status_code=status_code, detail=authority.reason_code)
        request.state.internal_agent_authority = authority
        return authority

    async def bind_internal_agent_service(request: Request) -> Any:
        service = (
            internal_agent_command_service_provider()
            if internal_agent_command_service_provider is not None
            else None
        )
        if service is None:
            raise HTTPException(status_code=503, detail="INTERNAL_AGENT_RUNTIME_UNAVAILABLE")
        request.state.internal_agent_command_service = service
        return service

    async def require_scheduler_authority(request: Request) -> Any:
        ingress = (
            scheduler_command_ingress_provider()
            if scheduler_command_ingress_provider is not None
            else None
        )
        if ingress is None:
            raise HTTPException(status_code=503, detail="SCHEDULER_COMMAND_INGRESS_UNAVAILABLE")
        authority = ingress.evaluate_http(
            request=request,
            raw_body=await request.body(),
            received_at_ms=int(
                getattr(request.state, "request_started_ms", 0)
                or time.time() * 1000
            ),
        )
        if not authority.admissible:
            status_code = 503 if "ENFORCE" in authority.reason_code else 403
            raise HTTPException(status_code=status_code, detail=authority.reason_code)
        request.state.scheduler_command_authority = authority
        return authority

    async def bind_scheduler_service(request: Request) -> Any:
        service = (
            scheduler_command_service_provider()
            if scheduler_command_service_provider is not None
            else None
        )
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="SCHEDULER_COMMAND_SERVICE_UNAVAILABLE",
            )
        request.state.scheduler_command_service = service
        return service

    app.include_router(canon_router)
    scheduler_dependencies = [
        Depends(require_scheduler_authority),
        Depends(bind_scheduler_service),
    ]
    app.include_router(scheduler_router, dependencies=scheduler_dependencies)
    app.include_router(
        host_scheduler_router,
        prefix="/host-scheduler",
        dependencies=scheduler_dependencies,
    )
    app.include_router(tool_router)
    app.include_router(tool_kits_alias_router)
    app.include_router(prompt_config_router)
    app.include_router(bond_router)
    app.include_router(agent_router)
    app.include_router(
        create_input_router(
            governed_command_application_provider=(
                governed_command_application_provider
            ),
        )
    )
    app.include_router(internal_tribe_router)
    app.include_router(
        internal_codex_router,
        dependencies=[
            Depends(require_internal_agent_authority),
            Depends(bind_internal_agent_service),
        ],
    )
    app.include_router(internal_security_immune_router)
    app.include_router(digital_life_router)
    app.include_router(memory_router)
    app.include_router(mateengine_router)
    app.include_router(
        create_obs_command_http_router(
            ingress_provider=obs_command_ingress_provider,
            operator_query_gateway_provider=lambda: operator_query_gateway,
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
