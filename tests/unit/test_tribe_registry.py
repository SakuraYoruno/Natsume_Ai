from __future__ import annotations

from host_brain.tribe.registry import HostTribeRegistry


def test_host_tribe_registry_bootstrap_defaults() -> None:
    registry = HostTribeRegistry()

    families = {item["family_id"] for item in registry.list_families()}
    assert {
        "cortex",
        "ans",
        "hippocampus",
        "limbic",
        "prefrontal",
        "social_brain",
        "endocrine",
        "immune",
    }.issubset(families)

    agents = {item["agent_id"]: item for item in registry.list_agents()}
    assert agents["top_agent"]["family"] == "cortex"
    assert agents["dispatch_agent"]["family"] == "cortex"
    assert agents["ans_agent"]["family"] == "ans"
    assert agents["endocrine_agent"]["family"] == "endocrine"
    assert agents["immune_agent"]["family"] == "immune"


def test_host_tribe_registry_resolve_target_family() -> None:
    registry = HostTribeRegistry()

    assert registry.resolve_target_family(route="plan", target_id=None) == "prefrontal"
    assert registry.resolve_target_family(route="skill", target_id="memory_recall") == "hippocampus"
    assert registry.resolve_target_family(route="delegate", target_id="emotion_reflection_agent") == "limbic"
    assert registry.resolve_target_family(route="delegate", target_id="bond_style_adapter") == "social_brain"
    assert registry.resolve_target_family(route="delegate", target_id="cortisol_gate_resolver") == "endocrine"
    assert registry.resolve_target_family(route="delegate", target_id="immune_prompt_guard") == "immune"
    assert registry.resolve_target_family(route="delegate", target_id=None) == "cortex"


def test_host_tribe_registry_resolve_agent_family_prefers_registered_then_heuristic() -> None:
    registry = HostTribeRegistry()

    assert registry.resolve_agent_family("limbic_agent") == "limbic"
    assert registry.resolve_agent_family("memory_router_agent") == "hippocampus"
    assert registry.resolve_agent_family("") == "cortex"
