from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

import pytest

from natsume_contracts import (
    EXECUTOR_ASSIGNMENT_SCHEMA_VERSION,
    HOST_COMMAND_SCHEMA_VERSION,
    ExecutorAssignment,
    HostCommand,
    HostCommandSurface,
    TaskContractError,
    TaskContractReasonCode,
    TaskExecutor,
    build_host_command,
)
from host_brain.agent_runtime.runtime_router import (
    RUNTIME_ASSIGNMENT_SCHEMA_VERSION,
    RuntimeAssignment,
    RuntimeExecutor,
)


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _command(**overrides) -> HostCommand:
    values = {
        "surface": HostCommandSurface.HOST_INPUT,
        "request_id": "req::aw02::1",
        "session_id": "session::aw02::1",
        "principal_id": "service.edge.gateway",
        "ingress_claim_ref": "ingress-claim://aw02/1",
        "ingress_audit_ref": "audit://secure-ingress/aw02/1",
        "governor_grant_ref": "governor-grant://aw02/1",
        "input_ref": "input://aw02/1",
        "input_sha256": _DIGEST_A,
        "root_work_order_id": "wo::aw02::1",
        "graph_id": "graph::aw02::1",
        "graph_revision": 1,
        "route_type": "skill",
        "target_id": "session_summary",
        "business_scope": "host:session_summary",
        "operation_fingerprint": _DIGEST_B,
        "created_at_ms": 1_750_000_000_000,
    }
    values.update(overrides)
    return build_host_command(**values)


def test_host_command_builder_is_deterministic_bounded_and_round_trips() -> None:
    first = _command(created_at_ms=100)
    repeated = _command(created_at_ms=200)

    assert first.schema_version == HOST_COMMAND_SCHEMA_VERSION
    assert first.command_id == repeated.command_id
    assert first.command_id.startswith("host-command://host_input/")
    assert first.input_sha256 == f"sha256:{_DIGEST_A}"
    assert first.operation_fingerprint == f"sha256:{_DIGEST_B}"

    payload = json.loads(json.dumps(first.to_dict(), allow_nan=False))
    assert HostCommand.from_mapping(payload) == first
    assert payload["ingress_claim_ref"] == "ingress-claim://aw02/1"
    assert payload["governor_grant_ref"] == "governor-grant://aw02/1"


@pytest.mark.parametrize("surface", list(HostCommandSurface))
def test_all_host_command_surfaces_have_distinct_deterministic_ids(
    surface: HostCommandSurface,
) -> None:
    command = _command(
        surface=surface,
        ingress_claim_ref=None,
        ingress_audit_ref=None,
        governor_grant_ref=None,
    )

    assert command.surface is surface
    assert command.command_id.startswith(f"host-command://{surface.value}/")
    assert command.command_id != _command().command_id or surface is HostCommandSurface.HOST_INPUT


def test_command_id_is_bound_to_surface_and_request_and_rejects_tamper() -> None:
    command = _command()
    changed_request = _command(request_id="req::aw02::2")
    payload = command.to_dict()
    payload["command_id"] = changed_request.command_id

    assert command.command_id != changed_request.command_id
    with pytest.raises(TaskContractError) as rejected:
        HostCommand.from_mapping(payload)
    assert rejected.value.reason_code == TaskContractReasonCode.FIELD_INVALID.value
    assert rejected.value.field_path == "host_command.command_id"


def test_host_command_rejects_raw_secret_fields_and_token_material() -> None:
    payload = _command().to_dict()
    payload["raw_token"] = "never-persist-this"

    with pytest.raises(TaskContractError) as forbidden_field:
        HostCommand.from_mapping(payload)
    assert forbidden_field.value.reason_code == TaskContractReasonCode.SECRET_MATERIAL_FORBIDDEN.value

    payload = _command().to_dict()
    payload["governor_grant_ref"] = "token://opaque-runtime-value"
    with pytest.raises(TaskContractError) as forbidden_scheme:
        HostCommand.from_mapping(payload)
    assert forbidden_scheme.value.reason_code == TaskContractReasonCode.SECRET_MATERIAL_FORBIDDEN.value


def test_host_command_is_strict_finite_and_bounded() -> None:
    payload = _command().to_dict()
    payload["unexpected"] = "value"
    with pytest.raises(TaskContractError) as unexpected:
        HostCommand.from_mapping(payload)
    assert unexpected.value.reason_code == TaskContractReasonCode.FIELD_UNEXPECTED.value

    payload = _command().to_dict()
    payload["created_at_ms"] = float("nan")
    with pytest.raises(TaskContractError) as non_finite:
        HostCommand.from_mapping(payload)
    assert non_finite.value.reason_code == TaskContractReasonCode.PAYLOAD_INVALID.value

    with pytest.raises(TaskContractError) as oversized:
        _command(request_id="r" * 256)
    assert oversized.value.reason_code == TaskContractReasonCode.FIELD_INVALID.value


def test_host_command_requires_paired_ingress_evidence_and_canonical_digests() -> None:
    with pytest.raises(TaskContractError) as unpaired:
        _command(ingress_audit_ref=None)
    assert unpaired.value.field_path == "host_command.ingress_claim_ref"

    payload = _command().to_dict()
    payload["input_sha256"] = _DIGEST_A
    with pytest.raises(TaskContractError) as noncanonical:
        HostCommand.from_mapping(payload)
    assert noncanonical.value.field_path == "host_command.input_sha256"


def test_executor_assignment_preserves_canonical_runtime_assignment_v2_contract() -> None:
    payload = {
        "schema_version": "Natsume.agent.runtime_assignment.v2",
        "work_order_id": "wo::assignment::1",
        "executor": "canonical",
        "assignment_id": "runtime-assignment://wo::assignment::1/" + _DIGEST_A,
        "assigned_at_ms": 123,
        "is_new_task": True,
        "reason_code": "RUNTIME_NEW_TASK_CANONICAL",
    }

    assignment = ExecutorAssignment.from_mapping(payload)

    assert EXECUTOR_ASSIGNMENT_SCHEMA_VERSION == RUNTIME_ASSIGNMENT_SCHEMA_VERSION
    assert RuntimeAssignment is ExecutorAssignment
    assert RuntimeExecutor is TaskExecutor
    assert assignment.executor is TaskExecutor.CANONICAL
    assert assignment.to_dict() == payload
    assert RuntimeAssignment.from_mapping(payload) == assignment


def test_executor_assignment_is_single_strict_fail_closed_model() -> None:
    payload = {
        "schema_version": EXECUTOR_ASSIGNMENT_SCHEMA_VERSION,
        "work_order_id": "wo::assignment::2",
        "executor": "legacy",
        "assignment_id": "runtime-assignment://wo::assignment::2/" + _DIGEST_B,
        "assigned_at_ms": 124,
        "is_new_task": True,
        "reason_code": "RUNTIME_NEW_TASK_LEGACY",
    }

    with pytest.raises(TaskContractError):
        ExecutorAssignment.from_mapping(payload)

    payload["executor"] = "canonical"
    payload["raw_secret"] = "forbidden"
    with pytest.raises(TaskContractError) as forbidden:
        RuntimeAssignment.from_mapping(payload)
    assert forbidden.value.reason_code == TaskContractReasonCode.SECRET_MATERIAL_FORBIDDEN.value


def test_task_contract_module_is_stdlib_only() -> None:
    source_path = Path(__file__).parents[2] / "natsume_contracts" / "task" / "v1.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots <= set(sys.stdlib_module_names) | {"__future__"}
