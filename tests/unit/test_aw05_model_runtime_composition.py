from __future__ import annotations

from host_brain.app import dependencies
from host_brain.model_runtime import (
    InMemoryAttemptAuditRepository,
    PromptRegistry,
    ProviderAdapterRegistry,
    RejectingRoutePolicy,
    build_model_runtime_composition,
)


def test_composition_builds_one_consistent_service_graph() -> None:
    registry = PromptRegistry()
    route = RejectingRoutePolicy()
    adapters = ProviderAdapterRegistry()
    audit = InMemoryAttemptAuditRepository()

    composition = build_model_runtime_composition(
        prompt_registry=registry,
        route_policy=route,
        provider_adapters=adapters,
        audit_repository=audit,
    )

    assert composition.attempt_service.prompt_registry is registry
    assert composition.attempt_service.route_policy is route
    assert composition.attempt_service.provider_adapters is adapters
    assert composition.attempt_service.audit_repository is audit


def test_dependencies_exposes_and_resets_only_the_whole_composition() -> None:
    replacement = build_model_runtime_composition()
    dependencies.reset_model_runtime_composition(replacement)
    try:
        assert dependencies.get_model_runtime_composition() is replacement
        assert dependencies.get_model_attempt_service() is replacement.attempt_service
        assert dependencies.get_prompt_registry() is replacement.prompt_registry
    finally:
        dependencies.reset_model_runtime_composition()
