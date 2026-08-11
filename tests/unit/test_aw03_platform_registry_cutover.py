from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.channels.vts_mapping_store import VTSMappingStore
from host_brain.mcp.tool_registry import MCPToolRegistry, ToolMeta
from host_brain.persistence.tool_registry_repository import (
    TOOL_REGISTRY_CONFLICT,
    ToolRegistryRepositoryError,
)
from host_brain.persistence.vts_mapping_repository import (
    VTS_MAPPING_CONFLICT,
    VTSMappingRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "relative_path",
    [
        "host_brain/channels/vts_mapping_store.py",
        "host_brain/mcp/tool_registry.py",
    ],
)
def test_business_boundary_has_no_connection_schema_or_sql_writer(
    relative_path: str,
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    lowered = source.lower()
    assert "sqlite3" not in lowered
    assert "self._conn" not in source
    assert "create table" not in lowered
    assert "insert into" not in lowered
    assert "begin immediate" not in lowered
    assert ".execute(" not in source
    assert ".executescript(" not in source


def test_vts_store_uses_exact_revision_replay_and_explicit_conflict() -> None:
    store = VTSMappingStore()
    try:
        first = store.save_mapping(
            {"CUSTOM": "hotkey_custom"}, version="v2", changed_by="unit"
        )
        replay = store.save_mapping(
            {"custom": "hotkey_custom"}, version="v2", changed_by="unit"
        )
        assert replay == first
        assert len(store.list_versions()) == 2

        with pytest.raises(VTSMappingRepositoryError) as raised:
            store.save_mapping(
                {"CUSTOM": "different"}, version="v2", changed_by="unit"
            )
        assert raised.value.reason_code == VTS_MAPPING_CONFLICT
    finally:
        store.close()


def test_mcp_registry_facade_preserves_contract_and_exact_replay() -> None:
    registry = MCPToolRegistry(":memory:")
    meta = ToolMeta(
        provider_id="provider",
        tool_id="tool",
        name="Tool",
        description="",
        access_level="PUBLIC",
        risk_level="LOW",
        reversible=True,
        version="1.0.0",
    )
    try:
        first = registry.register(meta)
        assert registry.register(meta) == first
        assert registry.get(provider_id="provider", tool_id="tool") == meta
        assert registry.list_by_provider("provider") == [meta]
        assert len(registry.sync_log()) == 1

        drifted = ToolMeta.from_dict({**meta.as_dict(), "name": "Drifted"})
        with pytest.raises(ToolRegistryRepositoryError) as raised:
            registry.register(drifted)
        assert raised.value.reason_code == TOOL_REGISTRY_CONFLICT
    finally:
        registry.close()
