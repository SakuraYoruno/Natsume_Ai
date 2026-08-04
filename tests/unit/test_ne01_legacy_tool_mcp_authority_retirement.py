"""NE-01：旧 Tool/MCP 执行权限必须保持清零。"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from host_brain.composition import process_services
from host_brain.composition.host_governance_runtime import (
    HostGovernanceRuntimeOwner,
    load_host_governance_runtime_settings,
)
from host_brain.app import governed_skill_execution
from host_brain.app import governed_frozen_skill_runtime
from host_brain.mcp.tool_broker import MCPToolBroker
from host_brain.skills import mcp_skill_bridge
from host_brain.tools.mcp_adapter import MCPAdapter
from host_brain.tools.broker import ToolBroker, ToolRuntimeBoundary
from host_brain.persistence.task_repository import DurableTaskStore


ROOT = Path(__file__).resolve().parents[2]


def test_asgi_does_not_construct_or_bind_the_legacy_mcp_broker() -> None:
    source = (ROOT / "host_brain" / "app" / "asgi.py").read_text(encoding="utf-8")
    container_path = (
        ROOT / "host_brain" / "composition" / "process_service_container.py"
    )

    assert "get_mcp_tool_broker" not in source
    assert "mcp_degradation_broker" not in source
    assert not container_path.exists()
    assert not (
        ROOT
        / "host_brain"
        / "composition"
        / "process_runtime_services.py"
    ).exists()


def test_process_owned_tool_broker_locator_is_retired() -> None:
    container_path = (
        ROOT / "host_brain" / "composition" / "process_service_container.py"
    )
    runtime_path = (
        ROOT / "host_brain" / "composition" / "process_runtime_services.py"
    )

    assert not runtime_path.exists()
    assert not container_path.exists()
    assert not hasattr(process_services, "get_tool_broker")


def test_gate11_inventory_tracks_only_the_app_local_boundary_broker() -> None:
    from tools.run_core_release import _call_inventory

    _call_inventory.cache_clear()
    inventory = _call_inventory(ROOT)

    assert inventory.get("ToolBroker") == ["host_brain/tools/broker.py"]
    assert inventory.get("build_tool_runtime_boundary") == [
        "host_brain/app/tool_runtime_composition.py"
    ]


def test_process_mcp_skill_bridge_is_default_off_and_purges_stale_entries() -> None:
    source = inspect.getsource(
        HostGovernanceRuntimeOwner._sync_mcp_skills
    )
    settings_source = inspect.getsource(
        load_host_governance_runtime_settings
    )

    assert "HOST_SKILL_MCP_BRIDGE_ENABLED" in settings_source
    assert "default=False" in settings_source
    assert 'startswith("mcp::")' in source
    assert "unregister(skill_name)" in source


def test_mcp_metadata_bridges_retain_no_provider_callback() -> None:
    skill_bridge_source = inspect.getsource(
        mcp_skill_bridge.register_mcp_tools_into_skill_registry
    )
    adapter_source = inspect.getsource(MCPAdapter.sync_provider)
    broker_source = inspect.getsource(MCPToolBroker)

    assert ".call(" not in skill_bridge_source
    assert "register_handler(" not in adapter_source
    assert "self._handlers" not in broker_source
    assert "self._broker" not in broker_source


def test_raw_tool_broker_cannot_cross_the_app_local_execution_port() -> None:
    public_bound = inspect.getsource(ToolBroker.execute_bound)
    broker_source = inspect.getsource(ToolBroker)
    boundary_source = inspect.getsource(ToolRuntimeBoundary)
    live_authority_source = inspect.getsource(
        DurableTaskStore.assert_live_node_execution_authority
    )
    tool_executor_source = (
        ROOT / "host_brain" / "app" / "governed_tool_runtime.py"
    ).read_text(encoding="utf-8")

    assert "_execute_registered(" not in public_bound
    assert "bind_execution_port" not in broker_source
    assert "_execute_bound_internal" not in broker_source
    assert "bind_execution_port" in boundary_source
    assert "self._execution_port.execute_bound(" in tool_executor_source
    assert "self._broker" not in tool_executor_source
    assert "assert_live_node_execution_authority(" in tool_executor_source
    for required in (
        "graph.status AS graph_status",
        "node.status AS node_status",
        "attempt.status AS attempt_status",
        "lease.expires_ts_ms AS lease_expires_ts_ms",
        "reservation.status AS reservation_status",
        "reservation.expires_ts_ms AS reservation_expires_ts_ms",
        "self._assert_graph_not_cancelling(",
    ):
        assert required in live_authority_source

    boundary_build_callers: list[str] = []
    boundary_port_owners: list[str] = []
    for path in (ROOT / "host_brain").rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        relative = path.relative_to(ROOT).as_posix()
        if (
            relative != "host_brain/tools/broker.py"
            and "build_tool_runtime_boundary(" in source
        ):
            boundary_build_callers.append(relative)
        if ".bind_execution_port" in source:
            boundary_port_owners.append(relative)
    assert boundary_build_callers == ["host_brain/app/tool_runtime_composition.py"]
    assert boundary_port_owners == ["host_brain/app/tool_runtime_composition.py"]


def test_stale_mcp_skill_is_rejected_before_registry_lookup() -> None:
    source = inspect.getsource(
        governed_skill_execution.execute_governed_skill
    )
    guard = 'startswith("mcp::")'

    assert guard in source
    assert source.index(guard) < source.index(
        "registry.require(skill_name)"
    )
    assert "MCP_TASK_AUTHORITY_REQUIRED" in source

    frozen_input_source = inspect.getsource(
        governed_frozen_skill_runtime._prepare_frozen_skill_input
    )
    worker_guard = 'target.startswith("mcp::")'
    assert worker_guard in frozen_input_source

    preparation_source = inspect.getsource(
        governed_frozen_skill_runtime._FrozenSkillWorker.prepare_execution
    )
    assert preparation_source.index("_prepare_frozen_skill_input(") < (
        preparation_source.index("_require_frozen_skill_dependencies(")
    )

    worker_source = inspect.getsource(
        governed_frozen_skill_runtime._FrozenSkillWorker.execute_stream
    )
    assert worker_source.index("_prepare_frozen_skill_input(") < worker_source.index(
        "_preview_effectful_skill("
    )


def test_stale_mcp_skill_runtime_returns_exact_rejection_without_registry_access() -> None:
    class _FailIfReadPorts:
        @property
        def skill_registry(self) -> None:
            raise AssertionError(
                "stale MCP target must be rejected before registry lookup"
            )

    with pytest.raises(
        PermissionError,
        match="MCP_TASK_AUTHORITY_REQUIRED",
    ):
        governed_skill_execution.execute_governed_skill(
            ports=_FailIfReadPorts(),
            durable_authority_ledger=object(),
            request_id="request-stale-mcp",
            session_id="session-stale-mcp",
            person_id="person-stale-mcp",
            intent_id="intent-stale-mcp",
            skill_id="mcp::cursor::open_path",
            task_text="open path",
            user_message="open path",
            governor_result={
                "tokens": {"approval_token_ref": "legacy-mcp-token"}
            },
            intent_draft={},
            emotion_context={},
            render_target_lang="zh",
            governed_execution_context={},
            execution_args={},
            verified_node_grant={},
        )
