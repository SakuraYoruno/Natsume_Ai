"""Static process composition for Tribe application ports and subscriptions."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from threading import RLock
from typing import Any

from host_brain.composition.process_service_container import get_process_service_container
from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.handoff import TribeHandoffCoordinator
from host_brain.tribe.registry import HostTribeRegistry
from host_brain.tribe.tribe_state import TribeStateRegistry


@dataclass(slots=True)
class ProcessTribeApplicationPortBinding:
    """Fenced lease for the process-global Tribe agents' application ports."""

    owner: Any | None = None
    ports: TribeApplicationPorts | None = None
    lock: RLock = field(default_factory=RLock, repr=False)


@dataclass(frozen=True, slots=True)
class ProcessTribeRuntime:
    """One process-owned Tribe state, catalog, and handoff authority."""

    state_registry: TribeStateRegistry
    tribe_registry: HostTribeRegistry
    handoff_coordinator: TribeHandoffCoordinator
    application_port_binding: ProcessTribeApplicationPortBinding


def compose_process_tribe_runtime(
    *,
    state_registry: TribeStateRegistry,
    tribe_registry: HostTribeRegistry,
) -> ProcessTribeRuntime:
    """Compose Tribe once from the registries selected by the app root."""

    if not isinstance(state_registry, TribeStateRegistry):
        raise TypeError("state_registry must be a TribeStateRegistry")
    if not isinstance(tribe_registry, HostTribeRegistry):
        raise TypeError("tribe_registry must be a HostTribeRegistry")

    process = get_process_service_container()
    with process.tribe_runtime_lock:
        current = process.tribe_runtime_composition
        if current is not None:
            if (
                current.state_registry is not state_registry
                or current.tribe_registry is not tribe_registry
            ):
                raise RuntimeError("PROCESS_TRIBE_AUTHORITY_ALREADY_COMPOSED")
            return current

        runtime = ProcessTribeRuntime(
            state_registry=state_registry,
            tribe_registry=tribe_registry,
            handoff_coordinator=TribeHandoffCoordinator(
                state_registry=state_registry,
                tribe_registry=tribe_registry,
            ),
            application_port_binding=ProcessTribeApplicationPortBinding(),
        )
        process.tribe_runtime_composition = runtime
        return runtime


def require_process_tribe_runtime() -> ProcessTribeRuntime:
    """Return the explicitly composed Tribe authority; never lazy-build it."""

    process = get_process_service_container()
    with process.tribe_runtime_lock:
        runtime = process.tribe_runtime_composition
    if runtime is None:
        raise RuntimeError("PROCESS_TRIBE_AUTHORITY_NOT_COMPOSED")
    return runtime


def clear_process_tribe_runtime(runtime: ProcessTribeRuntime | None = None) -> None:
    """Release the app-owned authority without clearing either shared registry."""

    process = get_process_service_container()
    with process.tribe_runtime_lock:
        if runtime is not None and process.tribe_runtime_composition is not runtime:
            return
        current = process.tribe_runtime_composition
        if current is not None:
            binding = current.application_port_binding
            with binding.lock:
                if binding.owner is not None:
                    raise RuntimeError("PROCESS_TRIBE_APPLICATION_PORTS_STILL_BOUND")
        process.tribe_runtime_composition = None


def _tribe_agents() -> tuple[tuple[str, Any], ...]:
    """Resolve every production Tribe agent through explicit imports."""

    from host_brain.tribe.ans_agent import get_ans_agent
    from host_brain.tribe.cerebellum_agent import get_cerebellum_agent
    from host_brain.tribe.circadian_agent import get_circadian_agent
    from host_brain.tribe.cortex_agent import get_cortex_agent
    from host_brain.tribe.default_mode_agent import get_default_mode_agent
    from host_brain.tribe.endocrine_agent import get_endocrine_agent
    from host_brain.tribe.glymphatic_agent import get_glymphatic_agent
    from host_brain.tribe.hippocampus_agent import get_hippocampus_agent
    from host_brain.tribe.immune_agent import get_immune_agent
    from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent
    from host_brain.tribe.interoception_agent import get_interoception_agent
    from host_brain.tribe.language_system_agent import get_language_system_agent
    from host_brain.tribe.limbic_agent import get_limbic_agent
    from host_brain.tribe.mirror_neuron_agent import get_mirror_neuron_agent
    from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent
    from host_brain.tribe.pain_reward_agent import get_pain_reward_agent
    from host_brain.tribe.peripheral_agent import get_peripheral_agent
    from host_brain.tribe.prefrontal_agent import get_prefrontal_agent
    from host_brain.tribe.proprioception_agent import get_proprioception_agent
    from host_brain.tribe.social_brain_agent import get_social_brain_agent
    from host_brain.tribe.somatic_agent import get_somatic_agent
    from host_brain.tribe.thalamus_agent import get_thalamus_agent
    from host_brain.tribe.vestibular_agent import get_vestibular_agent

    return (
        ("limbic_agent", get_limbic_agent()),
        ("prefrontal_agent", get_prefrontal_agent()),
        ("social_brain_agent", get_social_brain_agent()),
        ("cortex_agent", get_cortex_agent()),
        ("pain_reward_agent", get_pain_reward_agent()),
        ("endocrine_agent", get_endocrine_agent()),
        ("immune_agent", get_immune_agent()),
        ("hippocampus_agent", get_hippocampus_agent()),
        ("ans_agent", get_ans_agent()),
        ("language_system_agent", get_language_system_agent()),
        ("somatic_agent", get_somatic_agent()),
        ("peripheral_agent", get_peripheral_agent()),
        ("circadian_agent", get_circadian_agent()),
        ("proprioception_agent", get_proprioception_agent()),
        ("initiative_governor_agent", get_initiative_governor_agent()),
        ("default_mode_agent", get_default_mode_agent()),
        ("mirror_neuron_agent", get_mirror_neuron_agent()),
        ("interoception_agent", get_interoception_agent()),
        ("motor_cortex_agent", get_motor_cortex_agent()),
        ("cerebellum_agent", get_cerebellum_agent()),
        ("glymphatic_agent", get_glymphatic_agent()),
        ("vestibular_agent", get_vestibular_agent()),
        ("thalamus_agent", get_thalamus_agent()),
    )


def bind_process_tribe_application_ports(
    *,
    owner: Any,
    ports: TribeApplicationPorts,
) -> TribeApplicationPorts:
    """Bind one live Host owner without allowing another app to overwrite it."""

    if owner is None:
        raise TypeError("owner is required")
    if not isinstance(ports, TribeApplicationPorts):
        raise TypeError("ports must be TribeApplicationPorts")
    binding = require_process_tribe_runtime().application_port_binding
    with binding.lock:
        if binding.owner is not None:
            if binding.owner is owner and binding.ports is ports:
                return ports
            raise RuntimeError("PROCESS_TRIBE_APPLICATION_PORTS_ALREADY_BOUND")
        agents = _tribe_agents()
        previous = [
            (agent, getattr(agent, "application_ports", None))
            for _, agent in agents
        ]
        try:
            for _name, agent in agents:
                agent.application_ports = ports
        except Exception:
            for agent, prior in previous:
                agent.application_ports = prior
            raise
        binding.owner = owner
        binding.ports = ports
        return ports


def unbind_process_tribe_application_ports(*, owner: Any) -> bool:
    """Release only the exact live owner; a different app cannot unbind it."""

    if owner is None:
        raise TypeError("owner is required")
    binding = require_process_tribe_runtime().application_port_binding
    with binding.lock:
        if binding.owner is None:
            return False
        if binding.owner is not owner:
            raise RuntimeError("PROCESS_TRIBE_APPLICATION_PORTS_OWNER_MISMATCH")
        ports = binding.ports
        for _name, agent in _tribe_agents():
            if getattr(agent, "application_ports", None) is ports:
                agent.application_ports = None
        binding.owner = None
        binding.ports = None
        return True


def register_tribe_subscriptions(*, logger: logging.Logger) -> list[str]:
    """Register production subscriptions without reflection or dynamic imports."""

    errors: list[str] = []
    for agent_name, agent in _tribe_agents():
        try:
            agent.register_subscriptions()
            logger.info("Tribe 订阅已注册: %s", agent_name)
        except Exception as exc:
            errors.append(f"{agent_name}:{exc}")
            logger.warning("Tribe 订阅注册失败（降级）: agent=%s exc=%s", agent_name, exc)
    return errors


__all__ = [
    "ProcessTribeRuntime",
    "ProcessTribeApplicationPortBinding",
    "bind_process_tribe_application_ports",
    "clear_process_tribe_runtime",
    "compose_process_tribe_runtime",
    "register_tribe_subscriptions",
    "require_process_tribe_runtime",
    "unbind_process_tribe_application_ports",
]
