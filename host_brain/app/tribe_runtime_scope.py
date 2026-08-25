"""ASGI-owned lifecycle scope for the compatibility Tribe runtime.

Agent instances, shared state, registry and event bus are owned by this Host
scope during NE-01.  Legacy Agent getter APIs remain as a compatibility surface
but resolve this scope's catalog only through an execution-local owner binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from threading import RLock
from typing import Any

from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.event_bus import InMemoryTribeEventBus
from host_brain.tribe.handoff import TribeHandoffCoordinator
from host_brain.tribe.registry import HostTribeRegistry
from host_brain.tribe.runtime_context import (
    TribeAgentCatalog,
    TribeAgentProviders,
    TribeAgents,
    TribeRuntimeActivation,
    TribeRuntimeContext,
    activate_tribe_runtime_context,
    active_tribe_runtime_context,
    assert_tribe_runtime_context_owner,
    deactivate_tribe_runtime_context,
)
from host_brain.tribe.tribe_state import TribeStateRegistry


TribeAgentPreviousBinding = tuple[Any, Any, bool, Any]
TribeAgentPreviousBindings = tuple[TribeAgentPreviousBinding, ...]


@dataclass(frozen=True, slots=True)
class TribeExecutionActivation:
    owner: Any
    runtime_activation: TribeRuntimeActivation


@dataclass(slots=True)
class TribeRuntimeScope:
    """Explicit runtime state owned by exactly one ASGI application lifespan.

    The composition root injects fresh shared runtime objects and compatibility
    Agent providers.  This scope never imports singleton getters or touches a
    process service locator.  Independent Host scopes may therefore bind at the
    same time; request and background executions select their exact scope via a
    ``ContextVar`` activation.
    """

    state_registry: TribeStateRegistry
    tribe_registry: HostTribeRegistry
    event_bus: InMemoryTribeEventBus
    agent_providers: TribeAgentProviders
    handoff_coordinator: TribeHandoffCoordinator = field(init=False)
    _application_port_owner: Any | None = field(default=None, init=False, repr=False)
    _application_ports: TribeApplicationPorts | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _bound_agents: TribeAgents | None = field(default=None, init=False, repr=False)
    _previous_agent_bindings: TribeAgentPreviousBindings | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _runtime_context: TribeRuntimeContext = field(init=False, repr=False)
    _agent_catalog: TribeAgentCatalog = field(init=False, repr=False)
    _context_owner: Any | None = field(default=None, init=False, repr=False)
    _active_execution_count: int = field(default=0, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.state_registry, TribeStateRegistry):
            raise TypeError("state_registry must be a TribeStateRegistry")
        if not isinstance(self.tribe_registry, HostTribeRegistry):
            raise TypeError("tribe_registry must be a HostTribeRegistry")
        if not isinstance(self.event_bus, InMemoryTribeEventBus):
            raise TypeError("event_bus must be an InMemoryTribeEventBus")
        self._agent_catalog = TribeAgentCatalog(self.agent_providers)
        self.agent_providers = self._agent_catalog.providers
        self._runtime_context = TribeRuntimeContext(
            state_registry=self.state_registry,
            tribe_registry=self.tribe_registry,
            event_bus=self.event_bus,
            agent_catalog=self._agent_catalog,
            active_owner_provider=self._active_context_owner,
        )
        self.handoff_coordinator = TribeHandoffCoordinator(
            state_registry=self.state_registry,
            tribe_registry=self.tribe_registry,
        )

    @property
    def application_ports(self) -> TribeApplicationPorts | None:
        """Expose the active binding only for lifecycle diagnostics/tests."""

        with self._lock:
            return self._application_ports

    def require_agent(self, agent_name: str) -> Any:
        """Resolve one Agent only in this exact Host execution context."""

        with self._lock:
            if self._application_port_owner is None:
                raise RuntimeError("TRIBE_RUNTIME_SCOPE_NOT_BOUND")
            if active_tribe_runtime_context() is not self._runtime_context:
                raise RuntimeError(
                    "TRIBE_RUNTIME_EXECUTION_CONTEXT_NOT_ACTIVE"
                )
            return self._agent_catalog.resolve(agent_name)

    def activate_execution_context(
        self,
        *,
        owner: Any,
    ) -> TribeExecutionActivation:
        """Borrow this scope in one request or background execution."""

        if owner is None:
            raise TypeError("owner is required")
        with self._lock:
            if self._application_port_owner is None:
                raise RuntimeError("TRIBE_RUNTIME_SCOPE_NOT_BOUND")
            if self._application_port_owner is not owner:
                raise RuntimeError("TRIBE_APPLICATION_PORTS_OWNER_MISMATCH")
            if self._context_owner is not owner:
                raise RuntimeError("TRIBE_RUNTIME_CONTEXT_OWNER_MISMATCH")
            self._active_execution_count += 1
            try:
                runtime_activation = activate_tribe_runtime_context(
                    owner=owner,
                    context=self._runtime_context,
                )
            except BaseException:
                self._active_execution_count -= 1
                raise
            return TribeExecutionActivation(
                owner=owner,
                runtime_activation=runtime_activation,
            )

    def deactivate_execution_context(
        self,
        activation: TribeExecutionActivation,
    ) -> bool:
        """Release one exact execution activation."""

        if not isinstance(activation, TribeExecutionActivation):
            raise TypeError("activation must be TribeExecutionActivation")
        with self._lock:
            if activation.owner is not self._context_owner:
                raise RuntimeError("TRIBE_RUNTIME_CONTEXT_OWNER_MISMATCH")
            if (
                activation.runtime_activation.binding.context
                is not self._runtime_context
            ):
                raise RuntimeError("TRIBE_RUNTIME_CONTEXT_BINDING_DRIFT")
            if self._active_execution_count <= 0:
                raise RuntimeError("TRIBE_RUNTIME_EXECUTION_COUNT_DRIFT")
            released = deactivate_tribe_runtime_context(
                activation.runtime_activation
            )
            self._active_execution_count -= 1
            return released

    def bind_application_ports(
        self,
        *,
        owner: Any,
        ports: TribeApplicationPorts,
    ) -> TribeApplicationPorts:
        """Bind one Host lifespan without publishing process-global state."""

        if owner is None:
            raise TypeError("owner is required")
        if not isinstance(ports, TribeApplicationPorts):
            raise TypeError("ports must be TribeApplicationPorts")
        with self._lock:
            if self._application_port_owner is not None:
                if self._application_port_owner is owner and self._application_ports is ports:
                    return ports
                raise RuntimeError("TRIBE_APPLICATION_PORTS_ALREADY_BOUND")

            previous: TribeAgentPreviousBindings = ()
            self._context_owner = owner
            try:
                activation = activate_tribe_runtime_context(
                    owner=owner,
                    context=self._runtime_context,
                )
                try:
                    agents = self._agent_catalog.resolve_all()
                    prebound = [
                        name
                        for name, agent in agents
                        if getattr(agent, "application_ports", None) is not None
                    ]
                    if prebound:
                        raise RuntimeError(
                            "TRIBE_APPLICATION_PORTS_FOREIGN_BINDING:"
                            + ",".join(sorted(prebound))
                        )
                    previous = tuple(
                        (
                            agent,
                            getattr(agent, "application_ports", None),
                            hasattr(agent, "state_registry"),
                            getattr(agent, "state_registry", None),
                        )
                        for _, agent in agents
                    )
                    for _, agent in agents:
                        agent.application_ports = ports
                        if hasattr(agent, "state_registry"):
                            agent.state_registry = self.state_registry
                finally:
                    deactivate_tribe_runtime_context(activation)
            except BaseException:
                for binding in previous:
                    agent, prior_ports, had_state_registry, prior_state_registry = binding
                    agent.application_ports = prior_ports
                    if had_state_registry:
                        agent.state_registry = prior_state_registry
                self._context_owner = None
                raise
            self._application_port_owner = owner
            self._application_ports = ports
            self._bound_agents = agents
            self._previous_agent_bindings = previous
            return ports

    def unbind_application_ports(self, *, owner: Any) -> bool:
        """Release only the binding owned by the exact Host lifespan."""

        if owner is None:
            raise TypeError("owner is required")
        with self._lock:
            if self._application_port_owner is None:
                return False
            if self._application_port_owner is not owner:
                raise RuntimeError("TRIBE_APPLICATION_PORTS_OWNER_MISMATCH")
            if self._active_execution_count:
                raise RuntimeError(
                    "TRIBE_RUNTIME_EXECUTIONS_ACTIVE:"
                    f"{self._active_execution_count}"
                )
            ports = self._application_ports
            agents = self._bound_agents
            previous = self._previous_agent_bindings
            if agents is None or previous is None:
                raise RuntimeError("TRIBE_APPLICATION_PORTS_BINDING_DRIFT")
            activation = activate_tribe_runtime_context(
                owner=owner,
                context=self._runtime_context,
            )
            try:
                assert_tribe_runtime_context_owner(
                    owner=owner,
                    context=self._runtime_context,
                )
                mismatched = [
                    name
                    for name, agent in agents
                    if (
                        getattr(agent, "application_ports", None) is not ports
                        or (
                            hasattr(agent, "state_registry")
                            and getattr(agent, "state_registry", None)
                            is not self.state_registry
                        )
                    )
                    for name, agent in agents
                ]
                if mismatched:
                    raise RuntimeError(
                        "TRIBE_APPLICATION_PORTS_BINDING_DRIFT:"
                        + ",".join(sorted(mismatched))
                    )
                for (
                    agent,
                    prior_ports,
                    had_state_registry,
                    prior_state_registry,
                ) in previous:
                    agent.application_ports = prior_ports
                    if had_state_registry:
                        agent.state_registry = prior_state_registry
            finally:
                deactivate_tribe_runtime_context(activation)
            self._context_owner = None
            self._application_port_owner = None
            self._application_ports = None
            self._bound_agents = None
            self._previous_agent_bindings = None
            return True

    def register_subscriptions(self, *, logger: logging.Logger) -> list[str]:
        """Register the explicit catalog after this scope has acquired its lease."""

        with self._lock:
            agents = self._bound_agents
            owner = self._application_port_owner
        if agents is None:
            raise RuntimeError("TRIBE_APPLICATION_PORTS_NOT_BOUND")
        activation = self.activate_execution_context(owner=owner)
        errors: list[str] = []
        try:
            for agent_name, agent in agents:
                try:
                    agent.register_subscriptions()
                    logger.info("Tribe 订阅已注册: %s", agent_name)
                except Exception as exc:
                    errors.append(f"{agent_name}:{exc}")
                    logger.warning(
                        "Tribe 订阅注册失败（降级）: agent=%s exc=%s",
                        agent_name,
                        exc,
                    )
            return errors
        finally:
            self.deactivate_execution_context(activation)

    def _active_context_owner(self) -> Any | None:
        with self._lock:
            return self._context_owner


__all__ = [
    "TribeAgentProviders",
    "TribeExecutionActivation",
    "TribeRuntimeScope",
]
