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
    TribeFamilySpec("ans", "ANSAgent"),
    TribeFamilySpec("hippocampus", "HippocampusAgent"),
    TribeFamilySpec("limbic", "LimbicAgent"),
    TribeFamilySpec("prefrontal", "PrefrontalAgent"),
    TribeFamilySpec("social_brain", "SocialBrainAgent"),
    TribeFamilySpec("endocrine", "EndocrineAgent"),
    TribeFamilySpec("immune", "ImmuneAgent"),
)


class HostTribeRegistry:
    def __init__(self) -> None:
        self._families: dict[str, TribeFamilySpec] = {item.family_id: item for item in _DEFAULT_FAMILY_SPECS}
        self._agents: dict[str, TribeAgentDescriptor] = {}
        self._bootstrap_default_agents()

    def _bootstrap_default_agents(self) -> None:
        default_agents: tuple[TribeAgentDescriptor, ...] = (
            TribeAgentDescriptor("top_agent", "cortex", capabilities=("route", "governor", "handoff")),
            TribeAgentDescriptor("dispatch_agent", "cortex", capabilities=("route", "delegate", "skill")),
            TribeAgentDescriptor("chat_agent", "cortex", capabilities=("reply", "style", "render")),
            TribeAgentDescriptor("ans_agent", "ans", capabilities=("scheduler", "mode_transition", "proactive_tick")),
            TribeAgentDescriptor("hippocampus_agent", "hippocampus", capabilities=("episodic_store", "recall_fusion")),
            TribeAgentDescriptor("limbic_agent", "limbic", capabilities=("emotion", "motive", "body_sense")),
            TribeAgentDescriptor("prefrontal_agent", "prefrontal", capabilities=("self_reflection", "drift_guard")),
            TribeAgentDescriptor("social_brain_agent", "social_brain", capabilities=("bond", "style_adapter")),
            TribeAgentDescriptor("endocrine_agent", "endocrine", capabilities=("hormone_baseline", "gate_state")),
            TribeAgentDescriptor("immune_agent", "immune", capabilities=("risk_scan", "prompt_guard", "input_gate")),
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
            if "emotion" in normalized_target or "motive" in normalized_target or "inner_world" in normalized_target:
                return "limbic"
            if "silence" in normalized_target or "body_sense" in normalized_target:
                return "limbic"
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
            if normalized_target in {"code_agent", "translate_agent", "intent_agent", "vision_agent", "repair_agent"}:
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
