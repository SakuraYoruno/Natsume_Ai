from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor


@dataclass(frozen=True, slots=True)
class TribeFamilySpec:
    family_id: str
    display_name: str
    side: str = "host"
    domain: str = "brain"


_DEFAULT_FAMILY_SPECS: tuple[TribeFamilySpec, ...] = (
    TribeFamilySpec("cortex", "CortexAgent"),
    TribeFamilySpec("thalamus", "ThalamusAgent"),
    TribeFamilySpec("ans", "ANSAgent"),
    TribeFamilySpec("hippocampus", "HippocampusAgent"),
    TribeFamilySpec("limbic", "LimbicAgent"),
    TribeFamilySpec("prefrontal", "PrefrontalAgent"),
    TribeFamilySpec("social_brain", "SocialBrainAgent"),
    TribeFamilySpec("endocrine", "EndocrineAgent"),
    TribeFamilySpec("immune", "ImmuneAgent"),
    TribeFamilySpec("pain_reward", "PainRewardAgent"),
    TribeFamilySpec("language", "LanguageSystemAgent"),
    TribeFamilySpec("somatic", "SomaticAgent"),
    TribeFamilySpec("peripheral", "PeripheralAgent"),
    TribeFamilySpec("circadian", "CircadianAgent"),
    TribeFamilySpec("proprioception", "ProprioceptionAgent"),
    TribeFamilySpec("initiative_governor", "InitiativeGovernorAgent"),
    TribeFamilySpec("default_mode", "DefaultModeAgent"),
    TribeFamilySpec("mirror_neuron", "MirrorNeuronAgent"),
    TribeFamilySpec("interoception", "InteroceptionAgent"),
    TribeFamilySpec("motor_cortex", "MotorCortexAgent"),
    TribeFamilySpec("cerebellum", "CerebellumAgent"),
    TribeFamilySpec("glymphatic", "GlymphaticAgent"),
    TribeFamilySpec("vestibular", "VestibularAgent"),
)


class HostTribeRegistry:
    def __init__(self) -> None:
        self._families: dict[str, TribeFamilySpec] = {item.family_id: item for item in _DEFAULT_FAMILY_SPECS}
        self._agents: dict[str, TribeAgentDescriptor] = {}
        self._bootstrap_default_agents()

    def _bootstrap_default_agents(self) -> None:
        default_agents: tuple[TribeAgentDescriptor, ...] = (
            TribeAgentDescriptor("cortex_orchestrator", "cortex", capabilities=("route", "governor", "handoff")),
            TribeAgentDescriptor("executive_router", "cortex", capabilities=("route", "delegate", "skill")),
            TribeAgentDescriptor("chat_agent", "cortex", capabilities=("reply", "style", "render")),
            TribeAgentDescriptor("thalamus_agent", "thalamus", capabilities=("intent_compile", "schema_normalize", "handoff_pack")),
            TribeAgentDescriptor("ans_agent", "ans", capabilities=("scheduler", "mode_transition", "proactive_tick")),
            TribeAgentDescriptor("hippocampus_agent", "hippocampus", capabilities=("episodic_store", "recall_fusion")),
            TribeAgentDescriptor("limbic_agent", "limbic", capabilities=("emotion", "motive", "body_sense")),
            TribeAgentDescriptor("prefrontal_agent", "prefrontal", capabilities=("self_reflection", "drift_guard")),
            TribeAgentDescriptor("social_brain_agent", "social_brain", capabilities=("bond", "style_adapter")),
            TribeAgentDescriptor("endocrine_agent", "endocrine", capabilities=("hormone_baseline", "gate_state")),
            TribeAgentDescriptor("immune_agent", "immune", capabilities=("risk_scan", "prompt_guard", "input_gate")),
            TribeAgentDescriptor("pain_reward_agent", "pain_reward", capabilities=("pain", "reward", "hedonic")),
            TribeAgentDescriptor("language_system_agent", "language", capabilities=("language_style", "tone_adapt")),
            TribeAgentDescriptor("somatic_agent", "somatic", capabilities=("body_sense", "somatic_state")),
            TribeAgentDescriptor("peripheral_agent", "peripheral", capabilities=("platform_adapter", "motor_gate")),
            TribeAgentDescriptor("circadian_agent", "circadian", capabilities=("circadian_phase", "proactive_budget")),
            TribeAgentDescriptor("proprioception_agent", "proprioception", capabilities=("cognitive_load", "session_state")),
            TribeAgentDescriptor("initiative_governor_agent", "initiative_governor", capabilities=("motive", "readiness", "continuity")),
            TribeAgentDescriptor("default_mode_agent", "default_mode", capabilities=("inner_activity", "continuity_stream")),
            TribeAgentDescriptor("mirror_neuron_agent", "mirror_neuron", capabilities=("emotion_resonance", "co_regulation")),
            TribeAgentDescriptor("interoception_agent", "interoception", capabilities=("interoceptive_index", "body_budget", "recovery_urgency")),
            TribeAgentDescriptor("motor_cortex_agent", "motor_cortex", capabilities=("action_plan", "command_dispatch", "execution_trace")),
            TribeAgentDescriptor("cerebellum_agent", "cerebellum", capabilities=("error_correction", "latency_compensation", "retry_strategy")),
            TribeAgentDescriptor("glymphatic_agent", "glymphatic", capabilities=("recovery_cycle", "memory_consolidation", "noise_decay")),
            TribeAgentDescriptor("vestibular_agent", "vestibular", capabilities=("stability_index", "jitter_damping", "route_balance")),
        )
        for descriptor in default_agents:
            self.register_agent(descriptor)

    def list_families(self) -> list[dict[str, Any]]:
        return [
            {
                "family_id": spec.family_id,
                "display_name": spec.display_name,
                "side": spec.side,
                "domain": spec.domain,
            }
            for spec in self._families.values()
        ]

    def register_agent(self, descriptor: TribeAgentDescriptor) -> None:
        family = str(descriptor.family or "").strip() or "cortex"
        if family not in self._families:
            self._families[family] = TribeFamilySpec(family, family)
        self._agents[str(descriptor.agent_id).strip()] = descriptor

    def list_agents(self) -> list[dict[str, Any]]:
        return [descriptor.to_dict() for descriptor in self._agents.values()]

    def resolve_agent_family(self, agent_id: str) -> str:
        key = str(agent_id or "").strip()
        if not key:
            return "cortex"
        descriptor = self._agents.get(key)
        if descriptor is not None:
            return str(descriptor.family).strip() or "cortex"
        return self.resolve_target_family(route="delegate", target_id=key)

    def resolve_target_family(self, *, route: str, target_id: str | None = None) -> str:
        normalized_route = str(route or "").strip().lower()
        normalized_target = str(target_id or "").strip().lower()

        if normalized_route in {"chat", "recent_turn", "task_progress"}:
            return "cortex"
        if normalized_route == "plan":
            return "prefrontal"

        if normalized_target:
            if "memory" in normalized_target or "recall" in normalized_target or "retrieval" in normalized_target:
                return "hippocampus"
            if "episodic" in normalized_target or "canon" in normalized_target or "distill" in normalized_target:
                return "hippocampus"
            if "language" in normalized_target or "wernicke" in normalized_target or "broca" in normalized_target:
                return "language"
            if "thalamus" in normalized_target or "intent_compile" in normalized_target or "schema_normalize" in normalized_target:
                return "thalamus"
            if "pain" in normalized_target or "reward" in normalized_target or "hedonic" in normalized_target:
                return "pain_reward"
            if "emotion" in normalized_target or "motive" in normalized_target or "inner_world" in normalized_target:
                return "limbic"
            if "silence" in normalized_target or "body_sense" in normalized_target:
                return "limbic"
            if "somatic" in normalized_target:
                return "somatic"
            if "peripheral" in normalized_target or "platform" in normalized_target:
                return "peripheral"
            if "circadian" in normalized_target or "day_night" in normalized_target or "sleep_pressure" in normalized_target:
                return "circadian"
            if "proprioception" in normalized_target or "cognitive_load" in normalized_target or "session_load" in normalized_target:
                return "proprioception"
            if "initiative" in normalized_target or "motive" in normalized_target or "readiness" in normalized_target or "continuity" in normalized_target:
                return "initiative_governor"
            if "default_mode" in normalized_target or "idle_reflection" in normalized_target or "inner_activity" in normalized_target:
                return "default_mode"
            if "mirror" in normalized_target or "resonance" in normalized_target or "empathy" in normalized_target:
                return "mirror_neuron"
            if "interoception" in normalized_target or "interoceptive" in normalized_target or "body_budget" in normalized_target:
                return "interoception"
            if "motor_cortex" in normalized_target or "action_plan" in normalized_target or "command_dispatch" in normalized_target:
                return "motor_cortex"
            if "cerebellum" in normalized_target or "latency_compensation" in normalized_target or "error_correction" in normalized_target:
                return "cerebellum"
            if "glymphatic" in normalized_target or "recovery_cycle" in normalized_target or "memory_consolidation" in normalized_target:
                return "glymphatic"
            if "vestibular" in normalized_target or "stability_index" in normalized_target or "route_balance" in normalized_target:
                return "vestibular"
            if "growth" in normalized_target or "evolution" in normalized_target:
                return "prefrontal"
            if "drift" in normalized_target or "self_model" in normalized_target or "reflection" in normalized_target:
                return "prefrontal"
            if "bond" in normalized_target or "style" in normalized_target or "expression" in normalized_target:
                return "social_brain"
            if "scheduler" in normalized_target or "tick" in normalized_target or "mode_transition" in normalized_target:
                return "ans"
            if "endocrine" in normalized_target or "hormone" in normalized_target or "cortisol" in normalized_target:
                return "endocrine"
            if "immune" in normalized_target or "guard" in normalized_target or "injection" in normalized_target:
                return "immune"
            if normalized_target in {"translate_agent", "intent_agent", "vision_agent", "repair_agent"}:
                return "cortex"

        if normalized_route == "skill":
            return "hippocampus"
        if normalized_route == "delegate":
            return "cortex"
        return "cortex"


_HOST_TRIBE_REGISTRY: HostTribeRegistry | None = None


def get_host_tribe_registry() -> HostTribeRegistry:
    global _HOST_TRIBE_REGISTRY
    if _HOST_TRIBE_REGISTRY is None:
        _HOST_TRIBE_REGISTRY = HostTribeRegistry()
    return _HOST_TRIBE_REGISTRY


def set_host_tribe_registry(registry: HostTribeRegistry | None) -> None:
    global _HOST_TRIBE_REGISTRY
    _HOST_TRIBE_REGISTRY = registry


__all__ = [
    "HostTribeRegistry",
    "TribeFamilySpec",
    "get_host_tribe_registry",
    "set_host_tribe_registry",
]
