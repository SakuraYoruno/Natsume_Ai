from __future__ import annotations

import pytest

from host_brain.model_runtime.prompt_specs import list_prompt_specs
from host_brain.model_runtime import (
    PromptRef,
    PromptSchemaError,
    PromptVariableError,
    ProductionPromptCatalogAdapter,
    UnknownPromptError,
    build_production_prompt_registry,
    production_prompt_catalog_digest,
    production_prompt_catalog_inventory,
)


def test_production_catalog_is_complete_deterministic_and_definition_bound() -> None:
    first = production_prompt_catalog_inventory()
    second = production_prompt_catalog_inventory()
    registry = build_production_prompt_registry()

    assert first == second
    assert len(first) == len(list_prompt_specs()) == len(registry.list_definitions())
    assert [item.prompt_id for item in first] == sorted(item.prompt_id for item in first)
    assert production_prompt_catalog_digest() == production_prompt_catalog_digest()
    assert all(len(item.definition_digest) == 64 for item in first)
    for item in first:
        info = registry.definition(item.prompt_id)
        assert info.version == item.version
        assert info.metadata["definition_digest"] == item.definition_digest


def test_static_production_prompt_rejects_missing_and_renamed_variables() -> None:
    registry = build_production_prompt_registry()
    ref = PromptRef(
        "thalamus_agent.intent_classification_prompts",
        {"text": "hello", "tribe_summary": "ready"},
        version="v1.0.0",
    )
    rendered = registry.render(ref)

    assert rendered.prompt_id == ref.prompt_id
    assert len(rendered.digest) == 64
    assert rendered.metadata["definition_digest"]
    with pytest.raises(PromptVariableError, match="missing.*tribe_summary"):
        registry.render(PromptRef(ref.prompt_id, {"text": "hello"}))
    with pytest.raises(PromptVariableError, match="unexpected.*tribe_state"):
        registry.render(
            PromptRef(
                ref.prompt_id,
                {"text": "hello", "tribe_summary": "ready", "tribe_state": "renamed"},
            )
        )


def test_operational_adapter_is_path_scoped_and_fails_closed() -> None:
    adapter = ProductionPromptCatalogAdapter()
    messages = adapter.render(
        "failure_matrix.user_responses",
        entry="MODEL_FALLBACK",
        variables={},
    )
    assert messages[0]["role"] == "user"
    assert "备用通道" in messages[0]["content"]

    with pytest.raises(UnknownPromptError):
        adapter.render("missing.prompt", entry="x", variables={})
    with pytest.raises(PromptSchemaError, match="unknown operational prompt entry"):
        adapter.render("failure_matrix.user_responses", entry="MISSING", variables={})
    with pytest.raises(PromptVariableError, match="unexpected.*unused"):
        adapter.render(
            "failure_matrix.user_responses",
            entry="MODEL_FALLBACK",
            variables={"unused": "drift"},
        )


def test_operational_catalog_entry_is_available_through_registry_renderer() -> None:
    registry = build_production_prompt_registry()
    rendered = registry.render(
        PromptRef(
            "failure_matrix.user_responses",
            {"entry": "MODEL_FALLBACK", "variables": {}},
        )
    )
    assert rendered.messages[0]["content"]
    with pytest.raises(PromptVariableError, match="unexpected.*extra"):
        registry.render(
            PromptRef(
                "failure_matrix.user_responses",
                {"entry": "MODEL_FALLBACK", "variables": {}, "extra": True},
            )
        )
