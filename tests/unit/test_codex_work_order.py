from __future__ import annotations

import pytest

from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3


def test_codex_work_order_from_mapping_defaults_and_validates() -> None:
    order = AgentWorkOrder.from_mapping(
        {
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect the routing code",
            "workspace": {"strategy": "temp_workspace"},
        }
    )

    order.validate()
    assert order.work_order_id.startswith("wo_")
    assert order.requires_write is False
    assert order.review_required is False


def test_codex_work_order_rejects_unknown_run_mode() -> None:
    order = AgentWorkOrder.from_mapping(
        {
            "domain": "code",
            "run_mode": "legacy",
            "objective": "no",
            "workspace": {"strategy": "temp_workspace"},
        }
    )

    with pytest.raises(ValueError):
        order.validate()


def test_codex_work_order_marks_write_modes_review_required() -> None:
    order = AgentWorkOrder.from_mapping(
        {
            "domain": "code",
            "run_mode": "worker",
            "objective": "change files",
            "workspace": {"strategy": "runtime_repo"},
            "allowed_actions": ["read_files", "write_files"],
        }
    )

    assert order.requires_write is True
    assert order.review_required is True


def test_direct_write_tool_alias_is_write_authority_in_v2_and_v3() -> None:
    payload = {
        "work_order_id": "wo-direct-write-alias",
        "domain": "translation",
        "run_mode": "translate",
        "objective": "write translated output",
        "allowed_actions": ["AGENT_WRITE_FILE"],
    }

    assert AgentWorkOrder.from_mapping(payload).requires_write is True
    assert WorkOrderV3.from_mapping(payload).requires_write is True
