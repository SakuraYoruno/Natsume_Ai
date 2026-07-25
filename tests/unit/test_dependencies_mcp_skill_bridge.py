from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.composition.host_governance_runtime import (
    HostGovernanceRuntimeOwner,
    HostGovernanceRuntimeSettings,
    load_host_governance_runtime_settings,
)
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.mcp.tool_registry import ToolMeta
from host_brain.skills.provider_catalog import BUILTIN_SKILL_PROVIDER_SPECS


def _owner(
    db_path: Path,
    *,
    bridge_enabled: bool,
) -> HostGovernanceRuntimeOwner:
    settings = HostGovernanceRuntimeSettings(
        mcp_registry_db_path=str(db_path),
        mcp_bridge_enabled=bridge_enabled,
        skill_provider_flags=tuple(
            (spec.env_flag, False)
            for spec in BUILTIN_SKILL_PROVIDER_SPECS
        ),
    )
    owner = HostGovernanceRuntimeOwner(
        ledger_provider=lambda: SQLiteLedger(db_path),
        persistence_db_path_provider=lambda: str(db_path),
        settings_provider=lambda: settings,
    )
    owner.begin()
    return owner


def _migrated_db(tmp_path: Path, request_id: str) -> Path:
    db_path = tmp_path / "audit.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=request_id,
    )
    assert migrated.success is True, migrated.as_dict()
    return db_path


def _register_cursor_tool(owner: HostGovernanceRuntimeOwner) -> None:
    owner.require_mcp_tool_registry().register(
        ToolMeta(
            provider_id="cursor",
            tool_id="open_path",
            name="Open Path",
            description="Open project path in Cursor",
            access_level="PUBLIC",
            risk_level="LOW",
            reversible=True,
        )
    )


def test_enabled_legacy_bridge_registers_only_fail_closed_skill_projection(
    tmp_path: Path,
) -> None:
    owner = _owner(
        _migrated_db(tmp_path, "mcp-skill-bridge-enabled"),
        bridge_enabled=True,
    )
    try:
        skill_registry = owner.require_skill_registry()
        skill_name = "mcp::cursor::open_path"
        assert skill_registry.get(skill_name) is None

        _register_cursor_tool(owner)
        assert owner.require_skill_registry() is skill_registry
        skill = skill_registry.require(skill_name)
        result = skill.handler(
            {"path": "D:/repo"},
            {
                "request_id": "req-1",
                "intent_id": "intent-1",
                "session_id": "sess-1",
                "seq": 3,
            },
        )

        assert result["status"] == "REJECTED"
        assert result["reason_code"] == MCP_TASK_AUTHORITY_REQUIRED
        assert result["output"] is None
    finally:
        owner.close()


def test_skill_registry_skips_bridge_but_scorecard_reads_mcp_metadata(
    tmp_path: Path,
) -> None:
    owner = _owner(
        _migrated_db(tmp_path, "mcp-skill-bridge-disabled"),
        bridge_enabled=False,
    )
    try:
        _register_cursor_tool(owner)
        skill_registry = owner.require_skill_registry()
        skill_name = "mcp::cursor::open_path"
        assert skill_registry.get(skill_name) is None
        scorecard = owner.require_capability_scorecard_provider().get(
            skill_name
        )
        assert scorecard is not None
        assert scorecard.capability_id == skill_name
    finally:
        owner.close()


def test_bridge_flag_changes_only_at_a_new_host_epoch(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path, "mcp-skill-bridge-epoch")
    enabled_owner = _owner(db_path, bridge_enabled=True)
    try:
        _register_cursor_tool(enabled_owner)
        enabled_registry = enabled_owner.require_skill_registry()
        assert enabled_registry.get("mcp::cursor::open_path") is not None
        assert enabled_owner.require_skill_registry() is enabled_registry
    finally:
        enabled_owner.close()

    disabled_owner = _owner(db_path, bridge_enabled=False)
    try:
        disabled_registry = disabled_owner.require_skill_registry()
        assert disabled_registry is not enabled_registry
        assert disabled_registry.get("mcp::cursor::open_path") is None
        assert (
            disabled_owner.require_mcp_tool_registry().get(
                provider_id="cursor",
                tool_id="open_path",
            )
            is not None
        )
    finally:
        disabled_owner.close()


def test_mcp_registry_uses_the_typed_canonical_db_path(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path, "mcp-canonical-ledger")
    owner = _owner(db_path, bridge_enabled=False)
    try:
        registry = owner.require_mcp_tool_registry()
        assert Path(registry.db_path).resolve() == db_path.resolve()
        registry.register(
            ToolMeta(
                provider_id="canonical",
                tool_id="probe",
                name="Canonical probe",
                description="",
                access_level="PUBLIC",
                risk_level="LOW",
                reversible=True,
            )
        )
        assert (
            registry.get(provider_id="canonical", tool_id="probe")
            is not None
        )
    finally:
        owner.close()


def test_mcp_registry_fails_closed_without_canonical_persistence_path() -> None:
    with pytest.raises(
        RuntimeError,
        match="MCP_REGISTRY_CANONICAL_DB_PATH_REQUIRED",
    ):
        load_host_governance_runtime_settings(
            persistence_db_path_provider=lambda: "",
            env={},
        )
