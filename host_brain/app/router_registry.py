from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from host_brain.app.agent_routes import router as agent_router
from host_brain.app.canon_routes import create_canon_router
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.host_obs_read_routes import (
    HostObsReadPorts,
    create_host_obs_read_router,
)
from host_brain.app.health_routes import (
    DependencyProbeProvider,
    DegradationStateProvider,
    HealthLedgerProvider,
    WorkspaceCleanupHealthProvider,
    create_health_router,
)
from host_brain.input_runtime.contracts_and_normalization import GovernedCommandApplicationProvider
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.input_runtime.worker_dispatch import create_input_router
from host_brain.app.internal_agent_routes import create_internal_agent_router
from host_brain.app.internal_security_immune_routes import (
    create_internal_security_immune_router,
)
from host_brain.app.internal_tribe_routes import router as internal_tribe_router
from host_brain.app.digital_life_routes import create_digital_life_router
from host_brain.app.memory_routes import create_memory_router
from host_brain.app.mateengine_routes import create_mateengine_router
from host_brain.app.pentest_secure_run_routes import (
    PentestSecureRunCompositionProvider,
    create_pentest_secure_run_http_router,
)
from host_brain.app.pentest_routes import router as pentest_router
from host_brain.app.prompt_config_routes import router as prompt_config_router
from host_brain.app.tool_routes import create_tool_router
from host_brain.observability.command_http import (
    ObsCommandIngressProvider,
    create_obs_command_http_router,
)
from host_brain.observability.operator_query_gateway import OperatorQueryGateway
from host_brain.scheduler.scheduler_routes import (
    build_scheduler_router,
    router as scheduler_router,
)
from host_brain.skills.signed_intake_http import (
    SignedSkillProviderIntakeProvider,
    create_signed_skill_provider_intake_router,
)

host_scheduler_router = build_scheduler_router(prefix="")


def register_host_routers(
    app: FastAPI,
    *,
    input_runtime_ports: InputRuntimeApplicationPorts,
    route_ports: HostRoutePorts,
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
    scheduler_admin_runtime_provider: Callable[[], Any | None] | None = None,
    host_obs_read_ports: HostObsReadPorts | None = None,
    degradation_state_provider: DegradationStateProvider | None = None,
    health_ledger_provider: HealthLedgerProvider | None = None,
    health_dependency_probe_provider: DependencyProbeProvider | None = None,
    workspace_cleanup_health_provider: WorkspaceCleanupHealthProvider | None = None,
    degradation_capability_port_provider: Callable[[], Any | None] | None = None,
) -> None:
    operator_query_gateway = OperatorQueryGateway.from_environment()

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

    async def bind_degradation_capability_port(request: Request) -> Any:
        port = (
            degradation_capability_port_provider()
            if degradation_capability_port_provider is not None
            else None
        )
        request.state.degradation_capability_port = port
        return port

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

    async def bind_scheduler_admin_runtime(request: Request) -> Any:
        runtime = (
            scheduler_admin_runtime_provider()
            if scheduler_admin_runtime_provider is not None
            else None
        )
        request.state.scheduler_admin_runtime = runtime
        return runtime

    app.include_router(create_canon_router(route_ports))
    scheduler_dependencies = [
        Depends(require_scheduler_authority),
        Depends(bind_scheduler_service),
        Depends(bind_scheduler_admin_runtime),
    ]
    app.include_router(scheduler_router, dependencies=scheduler_dependencies)
    app.include_router(
        host_scheduler_router,
        prefix="/host-scheduler",
        dependencies=scheduler_dependencies,
    )
    app.include_router(create_tool_router(route_ports))
    app.include_router(prompt_config_router)
    app.include_router(agent_router)
    app.include_router(
        create_input_router(
            application_ports=input_runtime_ports,
            governed_command_application_provider=(
                governed_command_application_provider
            ),
        )
    )
    app.include_router(internal_tribe_router)
    if host_obs_read_ports is not None:
        app.include_router(create_host_obs_read_router(host_obs_read_ports))
    app.include_router(
        create_internal_agent_router(route_ports),
        dependencies=[
            Depends(require_internal_agent_authority),
            Depends(bind_internal_agent_service),
            Depends(bind_degradation_capability_port),
        ],
    )
    app.include_router(create_internal_security_immune_router(route_ports))
    app.include_router(create_digital_life_router(route_ports))
    app.include_router(create_memory_router(route_ports))
    app.include_router(create_mateengine_router(route_ports))
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
    app.include_router(
        create_health_router(
            degradation_state_provider=degradation_state_provider,
            ledger_provider=health_ledger_provider,
            dependency_probe_provider=health_dependency_probe_provider,
            workspace_cleanup_health_provider=workspace_cleanup_health_provider,
        )
    )


__all__ = ["register_host_routers"]
