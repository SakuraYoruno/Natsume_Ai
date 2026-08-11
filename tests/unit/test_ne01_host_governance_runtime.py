from __future__ import annotations

import ast
from collections.abc import Callable
import json
from pathlib import Path

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.host_governance_runtime import (
    HostGovernanceRuntimeCloseError,
    HostGovernanceRuntimeOwner,
    HostGovernanceRuntimeSettings,
    load_host_governance_runtime_settings,
)
from host_brain.mcp.tool_registry import ToolMeta
from host_brain.skills.provider_catalog import BUILTIN_SKILL_PROVIDER_SPECS


ROOT = Path(__file__).resolve().parents[2]


def _settings(*, bridge_enabled: bool = False) -> HostGovernanceRuntimeSettings:
    return HostGovernanceRuntimeSettings(
        mcp_registry_db_path=":memory:",
        mcp_bridge_enabled=bridge_enabled,
        skill_provider_flags=tuple(
            (spec.env_flag, False)
            for spec in BUILTIN_SKILL_PROVIDER_SPECS
        ),
    )


def _owner(
    *,
    settings_provider: Callable[
        [], HostGovernanceRuntimeSettings
    ] | None = None,
    mcp_registry_factory=None,
) -> HostGovernanceRuntimeOwner:
    kwargs = {}
    if mcp_registry_factory is not None:
        kwargs["mcp_registry_factory"] = mcp_registry_factory
    return HostGovernanceRuntimeOwner(
        ledger_provider=lambda: SQLiteLedger(":memory:"),
        persistence_db_path_provider=lambda: ":memory:",
        settings_provider=settings_provider or _settings,
        **kwargs,
    )


def test_owner_requires_active_epoch_and_reuses_exact_dependency_graph() -> None:
    owner = _owner()

    with pytest.raises(
        RuntimeError,
        match="HOST_GOVERNANCE_RUNTIME_NOT_ACTIVE",
    ):
        owner.require_intent_engine()

    owner.begin()
    token_service = owner.require_token_service()
    skill_registry = owner.require_skill_registry()
    tool_catalog = owner.require_tool_catalog()
    mcp_registry = owner.require_mcp_tool_registry()
    scorecards = owner.require_capability_scorecard_provider()
    engine = owner.require_intent_engine()

    assert owner.require_token_service() is token_service
    assert owner.require_skill_registry() is skill_registry
    assert owner.require_tool_catalog() is tool_catalog
    assert owner.require_mcp_tool_registry() is mcp_registry
    assert owner.require_capability_scorecard_provider() is scorecards
    assert owner.require_intent_engine() is engine
    assert engine.token_service is token_service
    assert engine._registry is skill_registry
    assert scorecards._skill_registry is skill_registry
    assert scorecards._tool_catalog is tool_catalog
    assert scorecards._mcp_registry is mcp_registry
    assert (
        engine._tier_policy._capability_policy.scorecard_provider
        is scorecards
    )
    owner.close()


def test_two_host_roots_never_share_governance_runtime_instances() -> None:
    first = _owner()
    second = _owner()
    first.begin()
    second.begin()
    try:
        assert first.require_token_service() is not second.require_token_service()
        assert first.require_skill_registry() is not second.require_skill_registry()
        assert first.require_tool_catalog() is not second.require_tool_catalog()
        assert (
            first.require_mcp_tool_registry()
            is not second.require_mcp_tool_registry()
        )
        assert (
            first.require_capability_scorecard_provider()
            is not second.require_capability_scorecard_provider()
        )
        assert first.require_intent_engine() is not second.require_intent_engine()
    finally:
        first.close()
        second.close()


def test_mcp_bridge_uses_same_owner_registry_and_is_fail_closed() -> None:
    owner = _owner(settings_provider=lambda: _settings(bridge_enabled=True))
    owner.begin()
    try:
        mcp_registry = owner.require_mcp_tool_registry()
        mcp_registry.register(
            ToolMeta(
                provider_id="cursor",
                tool_id="open_path",
                name="Open Path",
                description="Open a project path",
                access_level="PUBLIC",
                risk_level="LOW",
                reversible=True,
            )
        )
        skill_registry = owner.require_skill_registry()
        skill_name = "mcp::cursor::open_path"
        skill = skill_registry.require(skill_name)
        result = skill.handler(
            {"path": "D:/repo"},
            {
                "request_id": "req-owner-mcp",
                "intent_id": "intent-owner-mcp",
                "session_id": "session-owner-mcp",
                "seq": 1,
            },
        )

        assert result["status"] == "REJECTED"
        assert result["output"] is None
        assert (
            owner.require_capability_scorecard_provider()
            .get(skill_name)
            .capability_id
            == skill_name
        )
    finally:
        owner.close()


def test_configuration_is_frozen_per_epoch_and_reloaded_after_close() -> None:
    settings = [_settings(bridge_enabled=False), _settings(bridge_enabled=True)]
    calls: list[int] = []

    def provide_settings() -> HostGovernanceRuntimeSettings:
        calls.append(len(calls))
        return settings[len(calls) - 1]

    owner = _owner(settings_provider=provide_settings)
    owner.begin()
    first_token_service = owner.require_token_service()
    assert calls == [0]
    with pytest.raises(
        RuntimeError,
        match="HOST_GOVERNANCE_RUNTIME_ALREADY_ACTIVE",
    ):
        owner.begin()
    assert calls == [0]

    owner.close()
    owner.begin()
    try:
        assert calls == [0, 1]
        assert owner.epoch == 2
        assert owner.require_token_service() is not first_token_service
    finally:
        owner.close()


def test_close_failure_retains_exact_registry_and_fences_new_epoch() -> None:
    remaining_failures = [1]

    class FailingRegistry:
        def __init__(self, **_: object) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            if remaining_failures[0] > 0:
                remaining_failures[0] -= 1
                raise RuntimeError("injected close failure")

    owner = _owner(mcp_registry_factory=FailingRegistry)
    owner.begin()
    registry = owner.require_mcp_tool_registry()

    with pytest.raises(
        HostGovernanceRuntimeCloseError,
        match="MCP_TOOL_REGISTRY_CLOSE_FAILED",
    ):
        owner.close()
    with pytest.raises(
        RuntimeError,
        match="HOST_GOVERNANCE_RUNTIME_CLOSE_RETRY_REQUIRED",
    ):
        owner.begin()
    with pytest.raises(
        RuntimeError,
        match="HOST_GOVERNANCE_RUNTIME_CLOSE_RETRY_REQUIRED",
    ):
        owner.require_mcp_tool_registry()

    owner.close()
    assert registry.close_calls == 2
    owner.begin()
    try:
        assert owner.require_mcp_tool_registry() is not registry
    finally:
        owner.close()


def test_settings_loader_freezes_provider_flags_and_requires_db_path() -> None:
    loaded = load_host_governance_runtime_settings(
        persistence_db_path_provider=lambda: "D:/runtime/audit.sqlite3",
        env={
            "HOST_SKILL_MCP_BRIDGE_ENABLED": "1",
            "HOST_SKILL_FILE_ENABLED": "0",
        },
    )

    assert loaded.mcp_registry_db_path == "D:/runtime/audit.sqlite3"
    assert loaded.mcp_bridge_enabled is True
    assert loaded.skill_provider_enabled(
        "HOST_SKILL_FILE_ENABLED",
        default=True,
    ) is False

    with pytest.raises(
        RuntimeError,
        match="MCP_REGISTRY_CANONICAL_DB_PATH_REQUIRED",
    ):
        load_host_governance_runtime_settings(
            persistence_db_path_provider=lambda: "",
            env={},
        )


def test_architecture_policy_tracks_the_app_local_intent_constructor() -> None:
    relative_path = "host_brain/composition/host_governance_runtime.py"
    policy = json.loads(
        (ROOT / "configs/release/core_architecture_policy.json").read_text(
            encoding="utf-8-sig"
        )
    )
    assert policy["service_constructors"]["IntentDecisionEngine"] == {
        "count": 1,
        "allowed_paths": [relative_path],
    }

    tree = ast.parse(
        (ROOT / relative_path).read_text(encoding="utf-8-sig"),
        filename=relative_path,
    )
    constructors = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "IntentDecisionEngine"
    ]
    assert len(constructors) == 1
