"""Pure codecs and durable-record validators for the canonical TaskStore.

The functions in this module perform deterministic JSON normalization and
SQLite row-to-contract conversion only.  They never open a connection, execute
SQL, own a clock, or begin/commit a transaction.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)
from host_brain.contracts.agent_runtime.parallel_settlement import (
    MAX_PARALLEL_SETTLEMENT_PAYLOAD_BYTES,
    PARALLEL_SETTLEMENT_SCHEMA_VERSION,
    ParallelSettlementContractError,
    normalize_parallel_settlement_decision,
)
from host_brain.contracts.agent_runtime.parallel_success import (
    MAX_PARALLEL_SUCCESS_PAYLOAD_BYTES,
    PARALLEL_SUCCESS_SCHEMA_VERSION,
    ParallelSuccessContractError,
    normalize_parallel_success_decision,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraph, TaskNode
from host_brain.persistence.task_store_types import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectStatus,
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_BUDGET_ALREADY_EXCEEDED,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)


_SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACCEPTANCE_EFFECT_TYPE_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]{0,62}(?:\.[a-z][a-z0-9_]{0,62})+$"
)
_MAX_ACCEPTANCE_EFFECT_PAYLOAD_BYTES = 262_144

_ATTEMPT_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    AttemptStatus.CREATED: frozenset({AttemptStatus.LEASED, AttemptStatus.CANCEL_REQUESTED}),
    AttemptStatus.LEASED: frozenset(
        {AttemptStatus.RUNNING, AttemptStatus.RECOVERABLE, AttemptStatus.CANCEL_REQUESTED}
    ),
    AttemptStatus.RUNNING: frozenset(
        {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.RECOVERABLE,
            AttemptStatus.CANCEL_REQUESTED,
        }
    ),
    AttemptStatus.RECOVERABLE: frozenset(
        {AttemptStatus.LEASED, AttemptStatus.CANCEL_REQUESTED}
    ),
    AttemptStatus.SUCCEEDED: frozenset(),
    AttemptStatus.FAILED: frozenset(),
    AttemptStatus.CANCEL_REQUESTED: frozenset({AttemptStatus.CANCELLED}),
    AttemptStatus.CANCELLED: frozenset(),
}

_BUDGET_AMOUNT_FIELDS = tuple(BudgetAmount().to_dict())
_NODE_ARRAY_FIELDS = ("dependencies", "input_refs", "acceptance_criteria")
_NODE_OBJECT_FIELDS = ("output_contract", "retry_policy")
_NODE_IMMUTABLE_FIELDS = (
    "schema_version",
    "node_id",
    "dependencies",
    "route",
    "input_refs",
    "output_contract",
    "retry_policy",
    "risk_level",
    "acceptance_criteria",
)


def _validate_attempt_transition(
    current: AttemptStatus | str,
    target: AttemptStatus | str,
) -> AttemptStatus:
    try:
        current_status = AttemptStatus(str(current))
        target_status = AttemptStatus(str(target))
    except ValueError as exc:
        raise TaskStoreError(TASK_STORE_INVALID, "unknown attempt status") from exc
    if target_status not in _ATTEMPT_TRANSITIONS[current_status]:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"illegal Attempt transition: {current_status.value}->{target_status.value}",
        )
    return target_status


def _dumps(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"value is not canonical finite JSON: {exc}",
        ) from exc


def build_acceptance_effect_payload_digest(payload: Mapping[str, Any]) -> str:
    """Return the digest TaskStore expects for a canonical JSON effect payload."""

    if not isinstance(payload, Mapping):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "acceptance effect payload must be a JSON object",
        )
    canonical = _dumps(dict(payload))
    payload_size = len(canonical.encode("utf-8"))
    if payload_size < 2 or payload_size > _MAX_ACCEPTANCE_EFFECT_PAYLOAD_BYTES:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "acceptance effect payload exceeds the canonical size limit",
        )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _reject_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key is forbidden: {key}")
        result[key] = value
    return result


def _assert_finite_json(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number is forbidden")
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite_json(key)
            _assert_finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _assert_finite_json(item)


def _loads_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must contain a non-empty JSON object",
        )
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_non_finite_json_constant,
            object_pairs_hook=_reject_duplicate_json_object,
        )
        _assert_finite_json(parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} contains invalid JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must decode to an object",
        )
    return dict(parsed)


def _loads_list(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, str) or not value.strip():
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must contain a non-empty JSON array",
        )
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_non_finite_json_constant,
            object_pairs_hook=_reject_duplicate_json_object,
        )
        _assert_finite_json(parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} contains invalid JSON",
        ) from exc
    if not isinstance(parsed, list):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must decode to an array",
        )
    return list(parsed)


def _validated_budget_amount(payload: dict[str, Any], *, field_name: str) -> BudgetAmount:
    if set(payload) != set(_BUDGET_AMOUNT_FIELDS):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must contain exactly {sorted(_BUDGET_AMOUNT_FIELDS)}",
        )
    if any(
        isinstance(payload[field], bool) or not isinstance(payload[field], int)
        for field in _BUDGET_AMOUNT_FIELDS
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} values must be JSON integers",
        )
    try:
        return BudgetAmount.from_mapping(payload)
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} violates BudgetAmount contract: {exc}",
        ) from exc


def _amount_from_json(value: Any, *, field_name: str) -> BudgetAmount:
    return _validated_budget_amount(
        _loads_object(value, field_name=field_name),
        field_name=field_name,
    )


def _add_amounts(left: BudgetAmount, right: BudgetAmount) -> BudgetAmount:
    left_values = left.to_dict()
    right_values = right.to_dict()
    return BudgetAmount.from_mapping(
        {key: left_values[key] + right_values[key] for key in left_values}
    )


def _exceeded_dimensions(
    attempted: BudgetAmount,
    reserved: BudgetAmount,
) -> tuple[str, ...]:
    return tuple(
        key
        for key, value in attempted.to_dict().items()
        if value > reserved.to_dict()[key]
    )


def _validated_node_mapping(payload: dict[str, Any], *, field_name: str) -> TaskNode:
    for key in _NODE_ARRAY_FIELDS:
        if not isinstance(payload.get(key), list):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"{field_name}.{key} must be an array",
            )
    for key in _NODE_OBJECT_FIELDS:
        if not isinstance(payload.get(key), dict):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"{field_name}.{key} must be an object",
            )
    if (
        isinstance(payload.get("risk_level"), bool)
        or not isinstance(payload.get("risk_level"), int)
        or isinstance(payload.get("status_version"), bool)
        or not isinstance(payload.get("status_version"), int)
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} risk_level/status_version must be integers",
        )
    try:
        return TaskNode.from_mapping(payload)
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} violates TaskNode contract: {exc}",
        ) from exc


def _validated_node_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = _loads_object(row["node_json"], field_name="task_runtime_node.node_json")
    node = _validated_node_mapping(payload, field_name="task_runtime_node.node_json")
    expected = {
        "node_id": str(row["node_id"]),
        "status": str(row["status"]),
        "status_version": int(row["status_version"]),
    }
    actual = {
        "node_id": node.node_id,
        "status": node.status.value,
        "status_version": node.status_version,
    }
    if actual != expected:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"task_runtime_node SQL/JSON identity mismatch: expected {expected}, got {actual}",
        )
    return payload


def _validated_graph_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = _loads_object(row["graph_json"], field_name="task_runtime_graph.graph_json")
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list) or any(not isinstance(item, dict) for item in raw_nodes):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "task_runtime_graph.graph_json.nodes must be an array of objects",
        )
    for index, node_payload in enumerate(raw_nodes):
        _validated_node_mapping(
            node_payload,
            field_name=f"task_runtime_graph.graph_json.nodes[{index}]",
        )
    if (
        isinstance(payload.get("revision"), bool)
        or not isinstance(payload.get("revision"), int)
        or isinstance(payload.get("created_at"), bool)
        or not isinstance(payload.get("created_at"), int)
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "task_runtime_graph revision/created_at must be integers",
        )
    try:
        graph = TaskGraph.from_mapping(payload)
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"task_runtime_graph.graph_json violates TaskGraph contract: {exc}",
        ) from exc
    expected = {
        "graph_id": str(row["graph_id"]),
        "revision": int(row["revision"]),
        "root_work_order_id": str(row["root_work_order_id"]),
        "planner_ref": str(row["planner_ref"]),
        "status": str(row["status"]),
    }
    actual = {
        "graph_id": graph.graph_id,
        "revision": graph.revision,
        "root_work_order_id": graph.root_work_order_id,
        "planner_ref": graph.planner_ref,
        "status": graph.status.value,
    }
    if actual != expected:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"task_runtime_graph SQL/JSON identity mismatch: expected {expected}, got {actual}",
        )
    return payload


def _node_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["node"] = _validated_node_payload(row)
    record.pop("node_json")
    return record


def _graph_record(graph_row: sqlite3.Row, node_rows: list[sqlite3.Row]) -> dict[str, Any]:
    record = dict(graph_row)
    graph_payload = _validated_graph_payload(graph_row)
    node_records = [_node_record(row) for row in node_rows]
    embedded_nodes = {
        str(node["node_id"]): node
        for node in graph_payload["nodes"]
    }
    durable_nodes = {
        str(node_record["node_id"]): node_record["node"]
        for node_record in node_records
    }
    if set(embedded_nodes) != set(durable_nodes):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "TaskGraph embedded node identities do not match task_runtime_node rows",
        )
    for node_id, embedded in embedded_nodes.items():
        durable = durable_nodes[node_id]
        if any(embedded.get(key) != durable.get(key) for key in _NODE_IMMUTABLE_FIELDS):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"TaskNode immutable contract drift for {node_id}",
            )
    source_kind = str(graph_row["source_kind"])
    raw_legacy_snapshot = graph_row["legacy_snapshot_json"]
    if source_kind != "CANONICAL":
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"unsupported durable graph source_kind: {source_kind}",
        )
    if raw_legacy_snapshot is not None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "canonical TaskGraph cannot contain a retired legacy snapshot",
        )
    record.pop("graph_json")
    record.pop("legacy_snapshot_json")
    record["graph"] = graph_payload
    record["nodes"] = node_records
    return record


def _validated_budget_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    amounts = {
        "limits": _amount_from_json(record.pop("limits_json"), field_name="limits_json"),
        "reserved": _amount_from_json(record.pop("reserved_json"), field_name="reserved_json"),
        "consumed": _amount_from_json(record.pop("consumed_json"), field_name="consumed_json"),
        "attempted_consumed": _amount_from_json(
            record.pop("attempted_consumed_json"),
            field_name="attempted_consumed_json",
        ),
    }
    raw_dimensions = _loads_list(
        record.pop("exceeded_dimensions_json"),
        field_name="exceeded_dimensions_json",
    )
    if (
        any(not isinstance(item, str) or not item for item in raw_dimensions)
        or len(set(raw_dimensions)) != len(raw_dimensions)
        or any(item not in _BUDGET_AMOUNT_FIELDS for item in raw_dimensions)
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "exceeded_dimensions_json contains invalid or duplicate dimensions",
        )
    try:
        status = BudgetReservationStatus(str(record["status"]))
        reservation = BudgetReservation(
            budget_id=str(record["budget_id"]),
            scope=str(record["scope"]),
            limits=amounts["limits"],
            reserved=amounts["reserved"],
            consumed=amounts["consumed"],
            status=status,
            status_version=int(record["status_version"]),
        )
        reservation.validate()
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable BudgetReservation violates contract: {exc}",
        ) from exc
    consumed_values = amounts["consumed"].to_dict()
    attempted_values = amounts["attempted_consumed"].to_dict()
    if any(attempted_values[key] < consumed_values[key] for key in _BUDGET_AMOUNT_FIELDS):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "attempted budget consumption cannot be lower than committed consumption",
        )
    if status is BudgetReservationStatus.EXCEEDED and not raw_dimensions:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "EXCEEDED budget reservation requires exceeded dimensions",
        )
    record.update({key: value.to_dict() for key, value in amounts.items()})
    record["exceeded_dimensions"] = list(raw_dimensions)
    return record


def _budget_usage_record(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    record = dict(row)
    delta_json = str(record.pop("delta_json"))
    delta = _amount_from_json(delta_json, field_name="budget_usage.delta_json")
    expected_digest = "sha256:" + hashlib.sha256(delta_json.encode("utf-8")).hexdigest()
    dimensions = _loads_list(
        record.pop("exceeded_dimensions_json"),
        field_name="budget_usage.exceeded_dimensions_json",
    )
    try:
        accepted_raw = int(record.get("accepted"))
        version_before = int(record.get("reservation_version_before"))
        version_after = int(record.get("reservation_version_after"))
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable BudgetUsage outcome/version is invalid",
        ) from exc
    reason_code = str(record.get("reason_code") or "").strip() or None
    if (
        not _DURABLE_REFERENCE_PATTERN.fullmatch(str(record.get("usage_id") or ""))
        or str(record.get("delta_digest") or "") != expected_digest
        or any(item not in _BUDGET_AMOUNT_FIELDS for item in dimensions)
        or len(set(dimensions)) != len(dimensions)
        or accepted_raw not in {0, 1}
        or version_before < 0
        or version_after < 1
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable BudgetUsage binding is invalid",
        )
    accepted = bool(accepted_raw)
    normal_accepted = (
        accepted
        and reason_code is None
        and not dimensions
        and version_after == version_before + 1
    )
    terminal_winner = (
        not accepted
        and reason_code == TASK_STORE_BUDGET_EXCEEDED
        and bool(dimensions)
        and version_after == version_before + 1
    )
    terminal_sibling = (
        not accepted
        and reason_code == TASK_STORE_BUDGET_ALREADY_EXCEEDED
        and bool(dimensions)
        and version_after == version_before
    )
    if not (normal_accepted or terminal_winner or terminal_sibling):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable BudgetUsage outcome/reason/version matrix is invalid",
        )
    record["accepted"] = accepted
    record["delta"] = delta.to_dict()
    record["exceeded_dimensions"] = [str(item) for item in dimensions]
    record["replayed"] = False
    return record


def _acceptance_effect_text(value: Any, *, field_name: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > int(maximum):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a non-empty string of at most {maximum} characters",
        )
    return normalized


def _acceptance_effect_fence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "fencing_token must be a positive integer",
        )
    return int(value)


def _acceptance_effect_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    record = dict(row)
    try:
        scope = AcceptanceEffectCommitScope(str(record["commit_scope"]))
        status = AcceptanceEffectStatus(str(record["status"]))
        revision = int(record["graph_revision"])
        fence = int(record["fencing_token"])
        attempt_count = int(record["attempt_count"])
        status_version = int(record["status_version"])
        available_ts_ms = int(record["available_ts_ms"])
        created_ts_ms = int(record["created_ts_ms"])
        updated_ts_ms = int(record["updated_ts_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable AcceptanceEffectIntent has invalid scalar fields: {exc}",
        ) from exc

    effect_id = str(record.get("effect_id") or "")
    effect_type = str(record.get("effect_type") or "")
    payload_ref = str(record.get("payload_ref") or "")
    payload_json = str(record.get("payload_json") or "")
    payload_sha256 = str(record.get("payload_sha256") or "")
    try:
        payload = _loads_object(
            payload_json,
            field_name="acceptance_effect.payload_json",
        )
        canonical_payload_json = _dumps(payload)
    except TaskStoreError as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable AcceptanceEffectIntent payload is invalid: {exc.detail}",
        ) from exc
    computed_payload_sha256 = "sha256:" + hashlib.sha256(
        canonical_payload_json.encode("utf-8")
    ).hexdigest()
    if (
        not _DURABLE_REFERENCE_PATTERN.fullmatch(effect_id)
        or len(effect_type) > 128
        or not _ACCEPTANCE_EFFECT_TYPE_PATTERN.fullmatch(effect_type)
        or not _DURABLE_REFERENCE_PATTERN.fullmatch(payload_ref)
        or not _SHA256_DIGEST_PATTERN.fullmatch(payload_sha256)
        or payload_json != canonical_payload_json
        or len(payload_json.encode("utf-8")) > _MAX_ACCEPTANCE_EFFECT_PAYLOAD_BYTES
        or payload_sha256 != computed_payload_sha256
        or not str(record.get("idempotency_key") or "").strip()
        or not str(record.get("graph_id") or "").strip()
        or not str(record.get("node_id") or "").strip()
        or not str(record.get("attempt_id") or "").strip()
        or revision < 1
        or fence < 0
        or attempt_count < 0
        or status_version < 0
        or min(available_ts_ms, created_ts_ms, updated_ts_ms) < 0
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable AcceptanceEffectIntent binding is invalid",
        )

    owner = str(record.get("lease_owner") or "").strip() or None
    expires = record.get("lease_expires_ts_ms")
    receipt = str(record.get("receipt_ref") or "").strip() or None
    committed_ts = record.get("committed_ts_ms")
    failed_ts = record.get("failed_ts_ms")
    reason = str(record.get("reason_code") or "").strip() or None
    last_error = str(record.get("last_error") or "").strip() or None
    if expires is not None and int(expires) < 0:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable AcceptanceEffectIntent lease expiry is invalid",
        )
    if committed_ts is not None and int(committed_ts) < 0:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable AcceptanceEffectIntent commit timestamp is invalid",
        )
    if failed_ts is not None and int(failed_ts) < 0:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable AcceptanceEffectIntent failure timestamp is invalid",
        )

    valid_state = False
    if status is AcceptanceEffectStatus.PENDING:
        initial_pending = (
            owner is None
            and expires is None
            and fence == 0
            and attempt_count == 0
            and status_version == 0
            and receipt is None
            and reason is None
            and last_error is None
            and committed_ts is None
            and failed_ts is None
        )
        retry_pending = (
            owner is None
            and expires is None
            and fence >= 1
            and attempt_count >= 1
            and status_version >= 2
            and receipt is None
            and reason is not None
            and last_error is not None
            and committed_ts is None
            and failed_ts is None
        )
        valid_state = initial_pending or retry_pending
    elif status is AcceptanceEffectStatus.CLAIMED:
        valid_state = (
            owner is not None
            and expires is not None
            and fence >= 1
            and attempt_count >= 1
            and status_version >= 1
            and receipt is None
            and reason is None
            and last_error is None
            and committed_ts is None
            and failed_ts is None
        )
    elif status is AcceptanceEffectStatus.COMMITTED:
        valid_state = (
            owner is not None
            and expires is None
            and fence >= 1
            and attempt_count >= 1
            and status_version >= 2
            and receipt is not None
            and _DURABLE_REFERENCE_PATTERN.fullmatch(receipt) is not None
            and reason is None
            and last_error is None
            and committed_ts is not None
            and failed_ts is None
        )
    elif status is AcceptanceEffectStatus.FAILED:
        valid_state = (
            owner is not None
            and expires is None
            and fence >= 1
            and attempt_count >= 1
            and status_version >= 2
            and receipt is None
            and committed_ts is None
            and failed_ts is not None
            and reason is not None
            and last_error is not None
        )
    if not valid_state:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable AcceptanceEffectIntent state is incoherent: {status.value}",
        )
    record["commit_scope"] = scope.value
    record["status"] = status.value
    record["payload"] = payload
    record["replayed"] = False
    return record


def _graph_cancellation_intent_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    record = dict(row)
    try:
        payload = _loads_object(
            str(record.get("payload_json") or ""),
            field_name="task_runtime_graph_cancellation_intent.payload_json",
        )
        intent = normalize_graph_cancellation_intent(payload)
    except (TaskStoreError, GraphCancellationContractError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable graph cancellation intent is invalid: {exc}",
        ) from exc
    budget_version = record.get("budget_version_at_request")
    expected = {
        "intent_ref": intent.intent_ref,
        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
        "request_id": intent.payload["request_id"],
        "request_identity": intent.payload["request_identity"],
        "source": intent.payload["source"],
        "reason_code": intent.payload["reason_code"],
        "graph_id": intent.payload["graph_id"],
        "graph_revision": intent.payload["graph_revision"],
        "graph_version_before": intent.payload["graph_status_version_before"],
        "graph_version_after": intent.payload["graph_status_version_before"] + 1,
        "budget_id": intent.payload["budget_id"],
        "budget_version_at_request": intent.payload["budget_status_version_before"],
        "payload_sha256": intent.payload_sha256,
        "graph_outbox_id": intent.payload["graph_outbox_id"],
    }
    actual = {
        "intent_ref": str(record.get("intent_ref") or ""),
        "schema_version": str(record.get("schema_version") or ""),
        "request_id": str(record.get("request_id") or ""),
        "request_identity": str(record.get("request_identity") or ""),
        "source": str(record.get("source") or ""),
        "reason_code": str(record.get("reason_code") or ""),
        "graph_id": str(record.get("graph_id") or ""),
        "graph_revision": int(record.get("graph_revision") or 0),
        "graph_version_before": int(record.get("graph_version_before") or 0),
        "graph_version_after": int(record.get("graph_version_after") or 0),
        "budget_id": str(record.get("budget_id") or "").strip() or None,
        "budget_version_at_request": (
            int(budget_version) if budget_version is not None else None
        ),
        "payload_sha256": str(record.get("payload_sha256") or ""),
        "graph_outbox_id": str(record.get("graph_outbox_id") or ""),
    }
    status = str(record.get("status") or "")
    status_version = int(record.get("status_version") or 0)
    if (
        str(record.get("payload_json") or "") != intent.payload_json
        or actual != expected
        or (status, status_version) not in {("REQUESTED", 0), ("FINALIZED", 1)}
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable graph cancellation intent binding drifted",
        )
    record["payload"] = intent.payload
    record["payload_json"] = intent.payload_json
    record["payload_sha256"] = intent.payload_sha256
    record["budget_id"] = actual["budget_id"]
    record["budget_version_at_request"] = actual["budget_version_at_request"]
    return record


def _graph_cancellation_continuation_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    """Validate coordination metadata without inventing cancellation status.

    The returned record deliberately contains no business ``status``.  Callers
    must read the canonical 0092 intent/decision for that truth.
    """

    record = dict(row)
    required = {
        "intent_ref",
        "graph_id",
        "graph_revision",
        "claim_owner",
        "claim_token",
        "claim_expires_ts_ms",
        "claim_fencing_token",
        "attempt_count",
        "available_ts_ms",
        "last_error",
        "status_version",
        "created_ts_ms",
        "updated_ts_ms",
    }
    actual = set(record)
    if actual != required:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "graph cancellation continuation columns differ from the frozen shape",
        )

    intent_ref = record.get("intent_ref")
    graph_id = record.get("graph_id")
    if (
        not isinstance(intent_ref, str)
        or not intent_ref.strip()
        or not isinstance(graph_id, str)
        or not graph_id.strip()
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "graph cancellation continuation identity is invalid",
        )

    integer_fields = (
        "graph_revision",
        "claim_fencing_token",
        "attempt_count",
        "available_ts_ms",
        "status_version",
        "created_ts_ms",
        "updated_ts_ms",
    )
    if any(type(record.get(field)) is not int for field in integer_fields):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "graph cancellation continuation counters require integer storage",
        )
    graph_revision = int(record["graph_revision"])
    fence = int(record["claim_fencing_token"])
    attempts = int(record["attempt_count"])
    available = int(record["available_ts_ms"])
    status_version = int(record["status_version"])
    created = int(record["created_ts_ms"])
    updated = int(record["updated_ts_ms"])
    if (
        graph_revision < 1
        or fence < 0
        or attempts < 0
        or attempts != fence
        or available < 0
        or status_version < 0
        or created < 0
        or updated < created
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "graph cancellation continuation counters are incoherent",
        )

    owner = record.get("claim_owner")
    token = record.get("claim_token")
    expires = record.get("claim_expires_ts_ms")
    empty_claim = owner is None and token is None and expires is None
    live_claim = (
        isinstance(owner, str)
        and bool(owner.strip())
        and len(owner) <= 255
        and isinstance(token, str)
        and bool(token.strip())
        and len(token) <= 512
        and type(expires) is int
        and int(expires) >= 0
        and fence > 0
    )
    if not empty_claim and not live_claim:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "graph cancellation continuation claim is partial or invalid",
        )

    last_error = record.get("last_error")
    if last_error is not None and (
        not isinstance(last_error, str)
        or not last_error.strip()
        or len(last_error) > 2_000
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "graph cancellation continuation last_error is invalid",
        )
    return record


def _graph_cancellation_decision_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    record = dict(row)
    try:
        payload = _loads_object(
            str(record.get("payload_json") or ""),
            field_name="task_runtime_graph_cancellation_decision.payload_json",
        )
        decision = normalize_graph_cancellation_decision(payload)
    except (TaskStoreError, GraphCancellationContractError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable graph cancellation decision is invalid: {exc}",
        ) from exc
    budget_before = decision.payload["budget_status_version_before"]
    budget_changed = (
        decision.payload["budget_status_before"]
        != decision.payload["budget_target_status"]
    )
    expected = {
        "decision_ref": decision.decision_ref,
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": decision.payload["intent_ref"],
        "graph_id": decision.payload["graph_id"],
        "graph_revision": decision.payload["graph_revision"],
        "graph_version_before": decision.payload["graph_status_version_before"],
        "graph_version_after": decision.payload["graph_status_version_before"] + 1,
        "budget_id": decision.payload["budget_id"],
        "budget_version_before": budget_before,
        "budget_version_after": (
            None if budget_before is None else budget_before + int(budget_changed)
        ),
        "payload_sha256": decision.payload_sha256,
        "graph_outbox_id": decision.payload["graph_outbox_id"],
        "budget_outbox_id": decision.payload["budget_outbox_id"],
    }
    actual = {
        "decision_ref": str(record.get("decision_ref") or ""),
        "schema_version": str(record.get("schema_version") or ""),
        "intent_ref": str(record.get("intent_ref") or ""),
        "graph_id": str(record.get("graph_id") or ""),
        "graph_revision": int(record.get("graph_revision") or 0),
        "graph_version_before": int(record.get("graph_version_before") or 0),
        "graph_version_after": int(record.get("graph_version_after") or 0),
        "budget_id": str(record.get("budget_id") or "").strip() or None,
        "budget_version_before": (
            int(record["budget_version_before"])
            if record.get("budget_version_before") is not None
            else None
        ),
        "budget_version_after": (
            int(record["budget_version_after"])
            if record.get("budget_version_after") is not None
            else None
        ),
        "payload_sha256": str(record.get("payload_sha256") or ""),
        "graph_outbox_id": str(record.get("graph_outbox_id") or ""),
        "budget_outbox_id": str(record.get("budget_outbox_id") or "").strip() or None,
    }
    if str(record.get("payload_json") or "") != decision.payload_json or actual != expected:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable graph cancellation decision binding drifted",
        )
    record["payload"] = decision.payload
    record["payload_json"] = decision.payload_json
    record["payload_sha256"] = decision.payload_sha256
    record["budget_id"] = actual["budget_id"]
    record["budget_version_before"] = actual["budget_version_before"]
    record["budget_version_after"] = actual["budget_version_after"]
    record["budget_outbox_id"] = actual["budget_outbox_id"]
    return record


def _parallel_success_decision_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    record = dict(row)
    payload_json = str(record.get("payload_json") or "")
    if len(payload_json.encode("utf-8")) > MAX_PARALLEL_SUCCESS_PAYLOAD_BYTES:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable parallel success payload is too large",
        )
    try:
        payload = _loads_object(
            payload_json,
            field_name="task_runtime_parallel_success_decision.payload_json",
        )
        decision = normalize_parallel_success_decision(payload)
    except (TaskStoreError, ParallelSuccessContractError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable parallel success decision is invalid: {exc}",
        ) from exc
    expected = {
        "decision_ref": decision.decision_ref,
        "schema_version": PARALLEL_SUCCESS_SCHEMA_VERSION,
        "graph_id": decision.payload["graph_id"],
        "graph_revision": decision.payload["graph_revision"],
        "graph_version_before": decision.payload["graph_status_version_before"],
        "graph_version_after": decision.payload["graph_status_version_before"] + 1,
        "budget_id": decision.payload["budget_id"],
        "budget_version_before": decision.payload["budget_status_version_before"],
        "budget_version_after": decision.payload["budget_status_version_before"] + 1,
        "result_node_id": decision.payload["result_node_id"],
        "result_attempt_id": decision.payload["result_attempt_id"],
        "result_receipt_ref": decision.payload["result_receipt_ref"],
        "payload_sha256": decision.payload_sha256,
        "graph_outbox_id": decision.payload["graph_outbox_id"],
        "budget_outbox_id": decision.payload["budget_outbox_id"],
    }
    actual = {
        "decision_ref": str(record.get("decision_ref") or ""),
        "schema_version": str(record.get("schema_version") or ""),
        "graph_id": str(record.get("graph_id") or ""),
        "graph_revision": int(record.get("graph_revision") or 0),
        "graph_version_before": int(record.get("graph_version_before") or 0),
        "graph_version_after": int(record.get("graph_version_after") or 0),
        "budget_id": str(record.get("budget_id") or ""),
        "budget_version_before": int(record.get("budget_version_before") or 0),
        "budget_version_after": int(record.get("budget_version_after") or 0),
        "result_node_id": str(record.get("result_node_id") or ""),
        "result_attempt_id": str(record.get("result_attempt_id") or ""),
        "result_receipt_ref": str(record.get("result_receipt_ref") or ""),
        "payload_sha256": str(record.get("payload_sha256") or ""),
        "graph_outbox_id": str(record.get("graph_outbox_id") or ""),
        "budget_outbox_id": str(record.get("budget_outbox_id") or ""),
    }
    if payload_json != decision.payload_json or actual != expected:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable parallel success decision binding drifted",
        )
    record["payload"] = decision.payload
    record["payload_json"] = decision.payload_json
    record["payload_sha256"] = decision.payload_sha256
    return record


def _parallel_settlement_decision_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    record = dict(row)
    payload_json = str(record.get("payload_json") or "")
    if len(payload_json.encode("utf-8")) > MAX_PARALLEL_SETTLEMENT_PAYLOAD_BYTES:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable parallel settlement payload is too large",
        )
    try:
        payload = _loads_object(
            payload_json,
            field_name="task_runtime_parallel_settlement_decision.payload_json",
        )
        decision = normalize_parallel_settlement_decision(payload)
    except (TaskStoreError, ParallelSettlementContractError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable parallel settlement decision is invalid: {exc}",
        ) from exc
    budget_released = decision.payload["budget_target_status"] == "RELEASED"
    expected = {
        "decision_ref": decision.decision_ref,
        "schema_version": PARALLEL_SETTLEMENT_SCHEMA_VERSION,
        "graph_id": decision.payload["graph_id"],
        "graph_revision": decision.payload["graph_revision"],
        "graph_version_before": decision.payload["graph_status_version_before"],
        "graph_version_after": decision.payload["graph_status_version_before"] + 1,
        "budget_id": decision.payload["budget_id"],
        "budget_version_before": decision.payload["budget_status_version_before"],
        "budget_version_after": (
            decision.payload["budget_status_version_before"]
            + (1 if budget_released else 0)
        ),
        "decision_kind": decision.payload["decision_kind"],
        "selected_node_id": decision.payload["selected_node_id"],
        "graph_target_status": decision.payload["graph_target_status"],
        "budget_target_status": decision.payload["budget_target_status"],
        "payload_sha256": decision.payload_sha256,
        "graph_outbox_id": decision.payload["graph_outbox_id"],
        "budget_outbox_id": decision.payload["budget_outbox_id"],
    }
    actual = {
        "decision_ref": str(record.get("decision_ref") or ""),
        "schema_version": str(record.get("schema_version") or ""),
        "graph_id": str(record.get("graph_id") or ""),
        "graph_revision": int(record.get("graph_revision") or 0),
        "graph_version_before": int(record.get("graph_version_before") or 0),
        "graph_version_after": int(record.get("graph_version_after") or 0),
        "budget_id": str(record.get("budget_id") or ""),
        "budget_version_before": int(record.get("budget_version_before") or 0),
        "budget_version_after": int(record.get("budget_version_after") or 0),
        "decision_kind": str(record.get("decision_kind") or ""),
        "selected_node_id": str(record.get("selected_node_id") or ""),
        "graph_target_status": str(record.get("graph_target_status") or ""),
        "budget_target_status": (
            str(record.get("budget_target_status") or "").strip() or None
        ),
        "payload_sha256": str(record.get("payload_sha256") or ""),
        "graph_outbox_id": str(record.get("graph_outbox_id") or ""),
        "budget_outbox_id": (
            str(record.get("budget_outbox_id") or "").strip() or None
        ),
    }
    if payload_json != decision.payload_json or actual != expected:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "durable parallel settlement decision binding drifted",
        )
    record["payload"] = decision.payload
    record["payload_json"] = decision.payload_json
    record["payload_sha256"] = decision.payload_sha256
    return record


def _outbox_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    has_graph_revision = "graph_revision" in record
    evidence_refs = _loads_list(
        record.pop("evidence_refs_json"),
        field_name="task_runtime_outbox.evidence_refs_json",
    )
    if any(not isinstance(reference, str) or not reference.strip() for reference in evidence_refs):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "task_runtime_outbox evidence refs must be non-empty strings",
        )
    try:
        OutboxMessage.create(
            outbox_id=str(record["outbox_id"]),
            aggregate_type=str(record["aggregate_type"]),
            aggregate_id=str(record["aggregate_id"]),
            aggregate_version=int(record["aggregate_version"]),
            event_type=str(record["event_type"]),
            payload_ref=str(record["payload_ref"]),
            idempotency_key=str(record["idempotency_key"]),
            graph_revision=record.get("graph_revision"),
            attempt_id=record["attempt_id"],
            evidence_refs=tuple(evidence_refs),
            available_ts_ms=int(record["available_ts_ms"]),
        ).validate(
            allow_legacy_graph_revision=not has_graph_revision,
            allow_legacy_graph_payload_ref=True,
        )
    except (TaskStoreError, TypeError, ValueError) as exc:
        if isinstance(exc, TaskStoreError) and exc.reason_code == TASK_STORE_DURABLE_DATA_INVALID:
            raise
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable OutboxMessage violates contract: {exc}",
        ) from exc
    claim_columns = {
        "claim_owner",
        "claim_token",
        "claim_expires_ts_ms",
        "claim_fencing_token",
        "attempt_count",
        "last_error",
    }
    present_claim_columns = claim_columns.intersection(record)
    if present_claim_columns and present_claim_columns != claim_columns:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "task_runtime_outbox durable claim columns are incomplete",
        )
    if present_claim_columns:
        owner = record["claim_owner"]
        token = record["claim_token"]
        expires = record["claim_expires_ts_ms"]
        claim_values = (owner, token, expires)
        claim_is_empty = all(value is None for value in claim_values)
        claim_is_complete = all(value is not None for value in claim_values)
        if not claim_is_empty and not claim_is_complete:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task_runtime_outbox durable claim is partial",
            )
        raw_fence = record["claim_fencing_token"]
        raw_attempts = record["attempt_count"]
        if (
            type(raw_fence) is not int
            or type(raw_attempts) is not int
            or (expires is not None and type(expires) is not int)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task_runtime_outbox durable claim counters require integer storage",
            )
        fence = raw_fence
        attempts = raw_attempts
        expiry = expires
        if (
            fence < 0
            or attempts < 0
            or fence != attempts
            or (expiry is not None and expiry < 0)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task_runtime_outbox durable claim counters are invalid",
            )
        if claim_is_complete:
            if (
                str(record["status"]) != "PENDING"
                or not isinstance(owner, str)
                or not owner.strip()
                or not isinstance(token, str)
                or not token.strip()
                or fence < 1
                or attempts < 1
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "task_runtime_outbox live claim binding is invalid",
                )
        elif str(record["status"]) != "PENDING" and any(
            value is not None for value in claim_values
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal task_runtime_outbox row retains a delivery claim",
            )
        last_error = record["last_error"]
        if last_error is not None and (
            not isinstance(last_error, str)
            or not last_error.strip()
            or len(last_error) > 2_000
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task_runtime_outbox last_error is invalid",
            )
    record["evidence_refs"] = evidence_refs
    return record


def _legacy_receipt_digest(value: Any) -> str:
    if value is None:
        payload = b"null:"
    elif isinstance(value, (bytes, bytearray, memoryview)):
        payload = b"blob:" + bytes(value)
    elif isinstance(value, str):
        payload = b"text:" + value.encode("utf-8", errors="surrogatepass")
    else:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"unsupported migrated receipt storage type: {type(value).__name__}",
        )
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
