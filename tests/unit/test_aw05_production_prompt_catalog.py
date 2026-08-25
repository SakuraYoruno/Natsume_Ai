from __future__ import annotations

from host_brain.model_runtime.prompt_specs import list_prompt_specs
from host_brain.model_runtime import (
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
