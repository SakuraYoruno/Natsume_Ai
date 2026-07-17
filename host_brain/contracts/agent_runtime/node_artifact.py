"""Canonical data contract for immutable TaskNode result artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote


NODE_ARTIFACT_SCHEMA_VERSION = "Natsume.task_runtime.node_artifact.v1"
NODE_ARTIFACT_REF_PREFIX = "artifact://task-result/"
_MAX_ARTIFACT_PAYLOAD_BYTES = 1_048_576
_JSON_TYPES = {
    "array": list,
    "boolean": bool,
    "integer": int,
    "null": type(None),
    "number": (int, float),
    "object": dict,
    "string": str,
}


class NodeArtifactContractError(ValueError):
    """A TaskNode output or artifact binding is not canonical."""


def build_task_result_artifact_ref(
    graph_id: str,
    graph_revision: int,
    node_id: str,
) -> str:
    graph_ref = _required_text(graph_id, "graph_id")
    node_ref = _required_text(node_id, "node_id")
    if type(graph_revision) is not int or graph_revision < 1:
        raise NodeArtifactContractError("graph_revision must be a positive integer")
    return (
        NODE_ARTIFACT_REF_PREFIX
        + f"{quote(graph_ref, safe='')}/revisions/{graph_revision}/nodes/"
        f"{quote(node_ref, safe='')}"
    )


def is_task_result_artifact_ref(value: Any) -> bool:
    return str(value or "").strip().startswith(NODE_ARTIFACT_REF_PREFIX)


def canonical_artifact_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise NodeArtifactContractError("node artifact must be finite JSON") from exc


def artifact_digest(value: Mapping[str, Any]) -> str:
    encoded = canonical_artifact_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def normalize_output_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise NodeArtifactContractError("TaskNode.output_contract must be a non-empty object")
    contract = _json_object(value, "output_contract")
    contract_type = contract.get("type")
    if contract_type is not None and (
        not isinstance(contract_type, str) or not contract_type.strip()
    ):
        raise NodeArtifactContractError("output_contract.type must be non-empty text")
    required = contract.get("required", [])
    if not isinstance(required, Sequence) or isinstance(
        required, (str, bytes, bytearray)
    ):
        raise NodeArtifactContractError("output_contract.required must be an array")
    required_fields = tuple(str(item or "").strip() for item in required)
    if any(not item for item in required_fields) or len(set(required_fields)) != len(
        required_fields
    ):
        raise NodeArtifactContractError(
            "output_contract.required must contain unique non-empty fields"
        )
    properties = contract.get("properties")
    if properties is not None and not isinstance(properties, Mapping):
        raise NodeArtifactContractError("output_contract.properties must be an object")
    return contract


def build_node_artifact_payload(
    worker_output: Mapping[str, Any],
    *,
    receipt_ref: str,
    output_contract: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _json_object(worker_output, "worker_output")
    receipt = _required_text(receipt_ref, "receipt_ref")
    existing_receipt = str(payload.get("receipt_ref") or "").strip()
    if existing_receipt and existing_receipt != receipt:
        raise NodeArtifactContractError(
            "worker output receipt_ref differs from the committed receipt"
        )
    payload["receipt_ref"] = receipt
    contract = normalize_output_contract(output_contract)
    _validate_payload_against_contract(payload, contract)
    if len(canonical_artifact_json(payload).encode("utf-8")) > _MAX_ARTIFACT_PAYLOAD_BYTES:
        raise NodeArtifactContractError("node artifact payload exceeds one MiB")
    return payload


def validate_node_artifact_payload(
    payload: Mapping[str, Any],
    *,
    receipt_ref: str,
    output_contract: Mapping[str, Any],
) -> dict[str, Any]:
    canonical = _json_object(payload, "artifact payload")
    if str(canonical.get("receipt_ref") or "").strip() != _required_text(
        receipt_ref,
        "receipt_ref",
    ):
        raise NodeArtifactContractError(
            "node artifact payload is not bound to its receipt_ref"
        )
    _validate_payload_against_contract(
        canonical,
        normalize_output_contract(output_contract),
    )
    if len(canonical_artifact_json(canonical).encode("utf-8")) > _MAX_ARTIFACT_PAYLOAD_BYTES:
        raise NodeArtifactContractError("node artifact payload exceeds one MiB")
    return canonical


def _validate_payload_against_contract(
    payload: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    required = tuple(str(item) for item in contract.get("required", []))
    missing = tuple(field for field in required if field not in payload)
    if missing:
        raise NodeArtifactContractError(
            "node output is missing required fields: " + ",".join(missing)
        )
    properties = contract.get("properties")
    if not isinstance(properties, Mapping):
        return
    for field_name, raw_spec in properties.items():
        field = str(field_name or "").strip()
        if not field or field not in payload or not isinstance(raw_spec, Mapping):
            continue
        expected_type = raw_spec.get("type")
        if not isinstance(expected_type, str) or expected_type not in _JSON_TYPES:
            continue
        value = payload[field]
        expected = _JSON_TYPES[expected_type]
        valid = isinstance(value, expected)
        if expected_type in {"integer", "number"} and isinstance(value, bool):
            valid = False
        if not valid:
            raise NodeArtifactContractError(
                f"node output field {field} does not satisfy type {expected_type}"
            )


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NodeArtifactContractError(f"{field_name} must be an object")
    try:
        loaded = json.loads(canonical_artifact_json(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise NodeArtifactContractError(f"{field_name} must be finite JSON") from exc
    if not isinstance(loaded, dict):
        raise NodeArtifactContractError(f"{field_name} must be an object")
    return loaded


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NodeArtifactContractError(f"{field_name} is required")
    return text


__all__ = [
    "NODE_ARTIFACT_REF_PREFIX",
    "NODE_ARTIFACT_SCHEMA_VERSION",
    "NodeArtifactContractError",
    "artifact_digest",
    "build_node_artifact_payload",
    "build_task_result_artifact_ref",
    "canonical_artifact_json",
    "is_task_result_artifact_ref",
    "normalize_output_contract",
    "validate_node_artifact_payload",
]
