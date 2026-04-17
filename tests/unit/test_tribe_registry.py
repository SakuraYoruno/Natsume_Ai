from __future__ import annotations

from host_brain.tribe.registry import HostTribeRegistry


def test_host_tribe_registry_bootstrap_defaults() -> None:
    registry = HostTribeRegistry()

    families = {item["family_id"] for item in registry.list_families()}
    assert {
        "cortex",
        "thalamus",
        "ans",
        "hippocampus",
        "limbic",
        "prefrontal",
        "social_brain",
        "endocrine",
        "immune",
        "pain_reward",
        "language",
        "somatic",
        "peripheral",
        "circadian",
        "proprioception",
        "initiative_governor",
        "default_mode",
        "mirror_neuron",
        "interoception",
        "motor_cortex",
        "cerebellum",
        "glymphatic",
        "vestibular",
    }.issubset(families)

    agents = {item["agent_id"]: item for item in registry.list_agents()}
    assert agents["cortex_orchestrator"]["family"] == "cortex"
    assert agents["executive_router"]["family"] == "cortex"
    assert agents["thalamus_agent"]["family"] == "thalamus"
    assert agents["ans_agent"]["family"] == "ans"
    assert agents["endocrine_agent"]["family"] == "endocrine"
    assert agents["immune_agent"]["family"] == "immune"
    assert agents["pain_reward_agent"]["family"] == "pain_reward"
    assert agents["language_system_agent"]["family"] == "language"
    assert agents["somatic_agent"]["family"] == "somatic"
    assert agents["peripheral_agent"]["family"] == "peripheral"
    assert agents["circadian_agent"]["family"] == "circadian"
    assert agents["proprioception_agent"]["family"] == "proprioception"
    assert agents["initiative_governor_agent"]["family"] == "initiative_governor"
    assert agents["default_mode_agent"]["family"] == "default_mode"
    assert agents["mirror_neuron_agent"]["family"] == "mirror_neuron"
    assert agents["interoception_agent"]["family"] == "interoception"
    assert agents["motor_cortex_agent"]["family"] == "motor_cortex"
    assert agents["cerebellum_agent"]["family"] == "cerebellum"
    assert agents["glymphatic_agent"]["family"] == "glymphatic"
    assert agents["vestibular_agent"]["family"] == "vestibular"


def test_host_tribe_registry_resolve_target_family() -> None:
    registry = HostTribeRegistry()

    assert registry.resolve_target_family(route="plan", target_id=None) == "prefrontal"
    assert registry.resolve_target_family(route="skill", target_id="memory_recall") == "hippocampus"
    assert registry.resolve_target_family(route="delegate", target_id="thalamus_intent_compiler") == "thalamus"
    assert registry.resolve_target_family(route="delegate", target_id="emotion_reflection_agent") == "limbic"
    assert registry.resolve_target_family(route="delegate", target_id="bond_style_adapter") == "social_brain"
    assert registry.resolve_target_family(route="delegate", target_id="cortisol_gate_resolver") == "endocrine"
    assert registry.resolve_target_family(route="delegate", target_id="immune_prompt_guard") == "immune"
    assert registry.resolve_target_family(route="delegate", target_id="language_style_adapter") == "language"
    assert registry.resolve_target_family(route="delegate", target_id="pain_reward_router") == "pain_reward"
    assert registry.resolve_target_family(route="delegate", target_id="somatic_feedback_agent") == "somatic"
    assert registry.resolve_target_family(route="delegate", target_id="peripheral_platform_gate") == "peripheral"
    assert registry.resolve_target_family(route="delegate", target_id="circadian_sleep_pressure") == "circadian"
    assert registry.resolve_target_family(route="delegate", target_id="proprioception_load_meter") == "proprioception"
    assert registry.resolve_target_family(route="delegate", target_id="initiative_readiness_guard") == "initiative_governor"
    assert registry.resolve_target_family(route="delegate", target_id="default_mode_inner_activity") == "default_mode"
    assert registry.resolve_target_family(route="delegate", target_id="mirror_resonance_adjuster") == "mirror_neuron"
    assert registry.resolve_target_family(route="delegate", target_id="interoceptive_budget_router") == "interoception"
    assert registry.resolve_target_family(route="delegate", target_id="motor_cortex_action_plan") == "motor_cortex"
    assert registry.resolve_target_family(route="delegate", target_id="cerebellum_error_correction") == "cerebellum"
    assert registry.resolve_target_family(route="delegate", target_id="glymphatic_recovery_cycle") == "glymphatic"
    assert registry.resolve_target_family(route="delegate", target_id="vestibular_route_balance") == "vestibular"
    assert registry.resolve_target_family(route="delegate", target_id=None) == "cortex"


def test_host_tribe_registry_resolve_agent_family_prefers_registered_then_heuristic() -> None:
    registry = HostTribeRegistry()

    assert registry.resolve_agent_family("limbic_agent") == "limbic"
    assert registry.resolve_agent_family("memory_router_agent") == "hippocampus"
    assert registry.resolve_agent_family("") == "cortex"
