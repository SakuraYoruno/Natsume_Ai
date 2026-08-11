from __future__ import annotations

import asyncio

from host_brain.composition import process_services as dependencies
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.model_runtime import (
    AttemptStatus,
    InMemoryAttemptAuditRepository,
    LedgerAttemptAuditRepository,
    ModelAttemptRecord,
    PromptRegistry,
    ProviderAdapterRegistry,
    RejectingRoutePolicy,
    build_model_runtime_composition,
)
from host_brain.model_runtime.production_composition import (
    build_app_local_production_model_runtime,
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


def test_app_local_production_composition_never_shares_mutable_model_roots() -> None:
    class Ledger:
        def log_model_attempt(self, _row) -> None:
            return None

    def build():
        return build_app_local_production_model_runtime(
            ledger=Ledger(),
            credential_resolver=lambda _provider: None,
            base_url_resolver=lambda _provider: None,
            transport=lambda **_kwargs: {},
        )

    first = build()
    second = build()

    assert first is not second
    assert first.prompt_registry is not second.prompt_registry
    assert first.route_catalog is not second.route_catalog
    assert first.provider_adapters is not second.provider_adapters
    assert first.attempt_service is not second.attempt_service


def test_dependencies_exposes_and_resets_only_the_whole_composition() -> None:
    replacement = build_model_runtime_composition()
    dependencies.reset_model_runtime_composition(replacement)
    try:
        assert dependencies.get_model_runtime_composition() is replacement
        assert dependencies.get_model_attempt_service() is replacement.attempt_service
        assert dependencies.get_prompt_registry() is replacement.prompt_registry
    finally:
        dependencies.reset_model_runtime_composition()


def test_ledger_attempt_repository_preserves_route_retry_and_prompt_provenance() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    repository = LedgerAttemptAuditRepository(ledger)
    record = ModelAttemptRecord(
        attempt_id="attempt-1",
        request_id="request-1",
        route_decision_id="route-1",
        attempt_index=2,
        status=AttemptStatus.REJECTED,
        retry_reason="output_schema_repair",
        adapter_id="adapter-1",
        provider="provider-1",
        model_id="model-1",
        prompt_id="prompt-1",
        prompt_version="v3",
        prompt_digest="a" * 64,
        started_at_ms=100,
        ended_at_ms=125,
        latency_ms=25,
        output_digest="b" * 64,
        error_code="OUTPUT_SCHEMA_VIOLATION",
        error_message="safe validation summary",
        metadata={"semantic_retry": True, "api_key": "sk-private-material"},
    )

    asyncio.run(repository.record_attempt(record))

    rows = ledger.fetch_by_request_id("model_attempt_log", "request-1")
    assert len(rows) == 1
    assert rows[0]["route_decision_id"] == "route-1"
    assert rows[0]["attempt_index"] == 2
    assert rows[0]["retry_reason"] == "output_schema_repair"
    assert rows[0]["prompt_id"] == "prompt-1"
    assert rows[0]["prompt_version"] == "v3"
    assert rows[0]["metadata"]["semantic_retry"] is True
    assert "api_key" not in rows[0]["metadata"]
    assert "sk-private-material" not in str(rows[0]["metadata"])


def test_ledger_attempt_repository_requires_dedicated_writer() -> None:
    class LegacyLedger:
        def log_llm_node_call(self, row):
            raise AssertionError(row)

    try:
        LedgerAttemptAuditRepository(LegacyLedger())
    except TypeError as exc:
        assert "log_model_attempt" in str(exc)
    else:
        raise AssertionError("legacy llm node projection must not be accepted")
