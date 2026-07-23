"""ASGI-owned lifecycle scope for the compatibility Tribe runtime.

The Tribe agent instances are still process-bound compatibility objects during
NE-01.  Their application ports therefore need one process-wide binding fence,
but the Host's lifecycle ownership, handoff coordinator, subscription catalog,
and Obs state reader must not live in ``ProcessServiceContainer``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from threading import RLock
from typing import Any

from host_brain.tribe.application_ports import (
    TribeApplicationPorts,
    application_port_binding_fence,
)
from host_brain.tribe.handoff import TribeHandoffCoordinator
from host_brain.tribe.registry import HostTribeRegistry
from host_brain.tribe.tribe_state import TribeStateRegistry


TribeAgentProvider = Callable[[], Any]
TribeAgentProviders = tuple[tuple[str, TribeAgentProvider], ...]
TribeAgents = tuple[tuple[str, Any], ...]


@dataclass(slots=True)
class TribeRuntimeScope:
    """Explicit runtime state owned by exactly one ASGI application lifespan.

    The composition root injects both compatibility registries and Agent
    providers.  This scope never imports singleton getters or touches a
    process service locator.  It can therefore fail closed if another Host has
    already bound the still-process-global agent catalog.
    """

    state_registry: TribeStateRegistry
    tribe_registry: HostTribeRegistry
    agent_providers: TribeAgentProviders
    handoff_coordinator: TribeHandoffCoordinator = field(init=False)
    _application_port_owner: Any | None = field(default=None, init=False, repr=False)
    _application_ports: TribeApplicationPorts | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _bound_agents: TribeAgents | None = field(default=None, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.state_registry, TribeStateRegistry):
            raise TypeError("state_registry must be a TribeStateRegistry")
        if not isinstance(self.tribe_registry, HostTribeRegistry):
            raise TypeError("tribe_registry must be a HostTribeRegistry")
        self.agent_providers = _validate_agent_providers(self.agent_providers)
        self.handoff_coordinator = TribeHandoffCoordinator(
            state_registry=self.state_registry,
            tribe_registry=self.tribe_registry,
        )

    @property
    def application_ports(self) -> TribeApplicationPorts | None:
        """Expose the active binding only for lifecycle diagnostics/tests."""

        with self._lock:
            return self._application_ports

    def bind_application_ports(
        self,
        *,
        owner: Any,
        ports: TribeApplicationPorts,
    ) -> TribeApplicationPorts:
        """Bind one Host lifespan without allowing a concurrent overwrite."""

        if owner is None:
            raise TypeError("owner is required")
        if not isinstance(ports, TribeApplicationPorts):
            raise TypeError("ports must be TribeApplicationPorts")
        with self._lock, application_port_binding_fence():
            if self._application_port_owner is not None:
                if self._application_port_owner is owner and self._application_ports is ports:
                    return ports
                raise RuntimeError("TRIBE_APPLICATION_PORTS_ALREADY_BOUND")

            agents = self._resolve_agents()
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
            previous = [
                (agent, getattr(agent, "application_ports", None))
                for _, agent in agents
            ]
            try:
                for _, agent in agents:
                    agent.application_ports = ports
            except Exception:
                for agent, prior in previous:
                    agent.application_ports = prior
                raise
            self._application_port_owner = owner
            self._application_ports = ports
            self._bound_agents = agents
            return ports

    def unbind_application_ports(self, *, owner: Any) -> bool:
        """Release only the binding owned by the exact Host lifespan."""

        if owner is None:
            raise TypeError("owner is required")
        with self._lock, application_port_binding_fence():
            if self._application_port_owner is None:
                return False
            if self._application_port_owner is not owner:
                raise RuntimeError("TRIBE_APPLICATION_PORTS_OWNER_MISMATCH")
            ports = self._application_ports
            agents = self._bound_agents
            if agents is None:
                raise RuntimeError("TRIBE_APPLICATION_PORTS_BINDING_DRIFT")
            mismatched = [
                name
                for name, agent in agents
                if getattr(agent, "application_ports", None) is not ports
            ]
            if mismatched:
                raise RuntimeError(
                    "TRIBE_APPLICATION_PORTS_BINDING_DRIFT:"
                    + ",".join(sorted(mismatched))
                )
            for _, agent in agents:
                agent.application_ports = None
            self._application_port_owner = None
            self._application_ports = None
            self._bound_agents = None
            return True

    def register_subscriptions(self, *, logger: logging.Logger) -> list[str]:
        """Register the explicit catalog after this scope has acquired its lease."""

        with self._lock:
            agents = self._bound_agents
        if agents is None:
            raise RuntimeError("TRIBE_APPLICATION_PORTS_NOT_BOUND")
        errors: list[str] = []
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

    def _resolve_agents(self) -> TribeAgents:
        agents: list[tuple[str, Any]] = []
        for name, provider in self.agent_providers:
            try:
                agent = provider()
            except Exception as exc:
                raise RuntimeError(
                    f"TRIBE_AGENT_PROVIDER_UNAVAILABLE:{name}"
                ) from exc
            if agent is None:
                raise RuntimeError(f"TRIBE_AGENT_PROVIDER_UNAVAILABLE:{name}")
            agents.append((name, agent))
        return tuple(agents)


def _validate_agent_providers(value: Any) -> TribeAgentProviders:
    if not isinstance(value, tuple) or not value:
        raise TypeError("agent_providers must be a non-empty tuple")
    normalized: list[tuple[str, TribeAgentProvider]] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError("agent_providers entries must be (name, provider) tuples")
        name = str(item[0] or "").strip()
        provider = item[1]
        if not name or name in names:
            raise TypeError("agent_providers names must be unique and non-empty")
        if not callable(provider):
            raise TypeError(f"agent provider must be callable: {name}")
        names.add(name)
        normalized.append((name, provider))
    return tuple(normalized)


__all__ = [
    "TribeAgentProvider",
    "TribeAgentProviders",
    "TribeRuntimeScope",
]
