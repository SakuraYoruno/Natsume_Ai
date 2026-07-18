"""Strict typed codecs and immutable-lineage validation for Host commands.

This module is deliberately free of connection ownership and write methods.
It validates values used by :mod:`host_command_repository` before or inside
the repository's existing transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import sqlite3
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3

from .host_command_repository_types import (
    HOST_COMMAND_DURABLE_DATA_INVALID,
    HOST_COMMAND_INVALID,
    HostCommandRepositoryError,
)


def assert_current_graph_identity(
    *,
    record: Mapping[str, Any],
    frozen_graph: Mapping[str, Any],
    current_graph: Mapping[str, Any],
    current_nodes: list[sqlite3.Row],
) -> None:
    """Verify mutable runtime state still belongs to the frozen admission."""

    graph_fields = (
        "schema_version",
        "graph_id",
        "root_work_order_id",
        "revision",
        "planner_ref",
    )
    for field_name in graph_fields:
        if current_graph.get(field_name) != frozen_graph.get(field_name):
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                f"current TaskGraph immutable binding drift: {field_name}",
            )
    if (
        str(current_graph.get("graph_id") or "") != str(record["graph_id"])
        or int(current_graph.get("revision") or 0) != int(record["graph_revision"])
        or str(current_graph.get("root_work_order_id") or "")
        != str(record["root_work_order_id"])
    ):
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            "current TaskGraph no longer belongs to the admitted command",
        )

    frozen_nodes = frozen_graph.get("nodes")
    if not isinstance(frozen_nodes, list):
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            "frozen TaskGraph nodes are invalid",
        )
    frozen_by_id = {
        str(item.get("node_id") or ""): dict(item)
        for item in frozen_nodes
        if isinstance(item, Mapping) and str(item.get("node_id") or "")
    }
    current_by_id: dict[str, dict[str, Any]] = {}
    for row in current_nodes:
        node_id = str(row["node_id"] or "")
        current_by_id[node_id] = load_object(
            row["node_json"],
            field_name=f"task_runtime_node[{node_id}].node_json",
        )
    if set(current_by_id) != set(frozen_by_id):
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            "current TaskGraph node identities differ from frozen admission",
        )
    mutable_fields = {"status", "status_version"}
    for node_id, frozen_node in frozen_by_id.items():
        current_node = current_by_id[node_id]
        frozen_immutable = {
            key: value for key, value in frozen_node.items() if key not in mutable_fields
        }
        current_immutable = {
            key: value for key, value in current_node.items() if key not in mutable_fields
        }
        if canonical_json(frozen_immutable) != canonical_json(current_immutable):
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                f"current TaskNode immutable binding drift: {node_id}",
            )


def work_order_roundtrip(
    value: Any,
    *,
    require_instance: bool = True,
) -> tuple[dict[str, Any], WorkOrderV3]:
    """Version-dispatch a v2/v3 WorkOrder and return canonical durable v3."""

    if require_instance and not isinstance(value, (AgentWorkOrder, WorkOrderV3)):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "work_order must be AgentWorkOrder or WorkOrderV3",
        )
    try:
        payload = (
            mapping_from_typed(value, field_name="work_order")
            if require_instance
            else dict(value)
        )
        typed = WorkOrderV3.from_mapping(deepcopy(payload))
        typed.validate()
        return mapping_from_typed(typed, field_name="work_order"), typed
    except HostCommandRepositoryError:
        raise
    except Exception as exc:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID if require_instance else HOST_COMMAND_DURABLE_DATA_INVALID,
            f"work_order violates its versioned contract: {exc}",
        ) from exc


def typed_roundtrip(
    value: Any,
    expected_type: type[Any],
    *,
    field_name: str,
    require_instance: bool = True,
) -> tuple[dict[str, Any], Any]:
    if require_instance and not isinstance(value, expected_type):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} must be {expected_type.__name__}",
        )
    try:
        payload = (
            mapping_from_typed(value, field_name=field_name)
            if require_instance
            else dict(value)
        )
        typed = expected_type.from_mapping(deepcopy(payload))
        validator = getattr(typed, "validate", None)
        if callable(validator):
            validator()
        normalized = mapping_from_typed(typed, field_name=field_name)
        if canonical_json(payload) != canonical_json(normalized):
            payload = normalized
        return payload, typed
    except HostCommandRepositoryError:
        raise
    except Exception as exc:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID if require_instance else HOST_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} violates its typed contract: {exc}",
        ) from exc


def mapping_from_typed(value: Any, *, field_name: str) -> dict[str, Any]:
    serializer = getattr(value, "to_dict", None)
    if not callable(serializer):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} lacks to_dict()",
        )
    payload = serializer()
    if not isinstance(payload, Mapping):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name}.to_dict() must return a mapping",
        )
    return dict(payload)


def required_text(value: Any, *, field_name: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > int(max_length):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} must contain 1..{int(max_length)} characters",
        )
    return text


def matches_review_decision_result(
    conn: sqlite3.Connection,
    *,
    result_ref: str,
    command: Mapping[str, Any],
) -> bool:
    """Resolve an ingress terminal ref to one exact durable Review decision."""

    try:
        parsed = urlsplit(str(result_ref or "").strip())
        if (
            parsed.scheme != "review-decision"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            return False
        review_id = unquote(parsed.netloc)
        if not review_id or quote(review_id, safe="") != parsed.netloc:
            return False
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2 or parts[0] not in {"rejected", "expired"}:
            return False
        status = parts[0].upper()
        if not parts[1].isdigit() or str(int(parts[1])) != parts[1]:
            return False
        review_status_version = int(parts[1])
        if review_status_version < 1:
            return False
        row = conn.execute(
            """
            SELECT review_id, status, status_version, subject_ref,
                   task_graph_id, task_node_id
            FROM agent_runtime_review
            WHERE review_id = ?
            """,
            (review_id,),
        ).fetchone()
        if (
            row is None
            or str(row["status"]) != status
            or int(row["status_version"]) != review_status_version
        ):
            return False
        from host_brain.persistence.review_continuation_store import (  # noqa: PLC0415
            parse_task_review_subject,
        )

        binding = parse_task_review_subject(str(row["subject_ref"] or ""))
        return bool(
            binding is not None
            and binding.graph_id == str(command.get("graph_id") or "")
            and binding.graph_revision == int(command.get("graph_revision") or 0)
            and str(row["task_graph_id"] or "") == binding.graph_id
            and str(row["task_node_id"] or "") == binding.node_id
        )
    except (ImportError, sqlite3.DatabaseError, TypeError, ValueError):
        return False


def optional_text(value: Any, *, field_name: str, max_length: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > int(max_length):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} exceeds {int(max_length)} characters",
        )
    return text


def required_int(value: Any, *, field_name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < int(minimum):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} must be an integer >= {int(minimum)}",
        )
    return int(value)


def required_sha256(value: Any, *, field_name: str) -> str:
    text = str(value or "")
    if (
        len(text) != 71
        or not text.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} must use canonical sha256:<64 lower-case hex> form",
        )
    return text


def normalize_external_sha256(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) == 64 and all(character in "0123456789abcdef" for character in text):
        return f"sha256:{text}"
    return required_sha256(text, field_name=field_name)


def reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_object(value: Any, *, field_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(
            str(value),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_pairs,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} is not strict JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} must contain a JSON object",
        )
    return parsed


def normalize_failure_evidence_refs(value: Any) -> tuple[str, str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "failure_evidence_refs must be a sequence",
        )
    refs: list[str] = []
    for raw in value:
        ref = str(raw or "").strip()
        try:
            parsed = urlsplit(ref)
        except ValueError as exc:
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "failure evidence contains an invalid durable reference",
            ) from exc
        if (
            not ref
            or len(ref) > 1024
            or not parsed.scheme
            or not parsed.netloc
            or ref in refs
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "failure evidence must contain 1..64 unique durable references",
            )
        refs.append(ref)
    if not 1 <= len(refs) <= 64:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "failure evidence must contain 1..64 unique durable references",
        )
    serialized = json.dumps(
        refs,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return serialized, digest(serialized)


def supersession_id(binding: Mapping[str, Any]) -> str:
    serialized = canonical_json(binding)
    return (
        "graph-revision-supersession://sha256/"
        + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    )


def canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"typed payload is not canonical JSON: {exc}",
        ) from exc


def digest(serialized: str) -> str:
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def secure_ingress_surface(command_surface: str) -> str:
    return {
        "host_input": "host.input",
        "tool": "host.tool",
        "gateway": "gateway.webhook",
        "internal": "internal.agent",
        "scheduler": "scheduler.admin",
        "operator": "obs.command",
    }.get(str(command_surface), str(command_surface))


__all__ = [
    "assert_current_graph_identity",
    "canonical_json",
    "digest",
    "load_object",
    "mapping_from_typed",
    "matches_review_decision_result",
    "normalize_external_sha256",
    "normalize_failure_evidence_refs",
    "optional_text",
    "required_int",
    "required_sha256",
    "required_text",
    "secure_ingress_surface",
    "supersession_id",
    "typed_roundtrip",
    "work_order_roundtrip",
]
