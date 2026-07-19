from __future__ import annotations

from dataclasses import fields

from host_brain.composition import process_environment, process_runtime_services
from host_brain.composition import process_services
from host_brain.composition.process_service_container import ProcessServiceContainer


RETIRED_PROCESS_LOCATORS = {
    "activity_monitor",
    "consolidation_trigger",
    "context_compressor",
    "episodic_memory_graph",
    "retrieval_pipeline",
    "retrieval_orchestrator",
    "resource_manager",
    "skill_composer",
    "tool_broker",
}

RETIRED_PROCESS_ACCESSORS = {
    "get_consolidation_trigger",
    "get_context_compressor",
    "get_episodic_memory_graph",
    "get_skill_composer",
    "get_tool_broker",
}

RETIRED_PROCESS_SETTERS = {
    "set_activity_monitor",
    "set_resource_manager",
}

RETIRED_PROCESS_REEXPORTS = {
    "BondDynamicsWriter",
    "BondStageEngine",
    "BondUpdateQueue",
    "IntentDecisionEngine",
    "MilestoneTracker",
    "SharedMemoryIndex",
    "get_capability_forge_token_service",
}

PROCESS_LOCKS = {
    "capability_runtime_lock",
    "hard_state_lock",
    "model_runtime_lock",
    "tribe_runtime_lock",
}


def test_ne01_dead_process_locators_are_removed_from_the_container() -> None:
    container_fields = {field.name for field in fields(ProcessServiceContainer)}

    assert container_fields.isdisjoint(RETIRED_PROCESS_LOCATORS)
    assert len(container_fields) == 54
    assert len(container_fields - PROCESS_LOCKS - {"application_ports"}) == 49


def test_ne01_dead_process_accessors_are_not_reexported() -> None:
    for accessor in RETIRED_PROCESS_ACCESSORS:
        assert not hasattr(process_services, accessor)
        assert not hasattr(process_environment, accessor)
        assert not hasattr(process_runtime_services, accessor)
    for setter in RETIRED_PROCESS_SETTERS:
        assert not hasattr(process_services, setter)
        assert not hasattr(process_environment, setter)


def test_ne01_dead_process_symbols_are_not_reexported_from_the_broad_facade() -> None:
    for symbol in RETIRED_PROCESS_REEXPORTS:
        assert not hasattr(process_services, symbol)
