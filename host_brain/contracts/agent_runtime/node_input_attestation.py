"""Digest-bound attestation for accepted TaskNode input artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from .node_artifact import artifact_digest, is_task_result_artifact_ref


NODE_INPUT_ARTIFACT_ATTESTATION_SCHEMA = (
    "Natsume.task_runtime.node_input_artifact_attestation.v1"
)


class NodeInputArtifactAttestationError(ValueError):
    """Resolved TaskNode artifacts do not form one canonical input bundle."""


def build_node_input_artifact_attestation(
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if isinstance(artifacts, (str, bytes, bytearray)) or not isinstance(
        artifacts,
        Sequence,
    ):
        raise NodeInputArtifactAttestationError(
            "node input artifacts must be a sequence",
        )
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in artifacts:
        if not isinstance(raw, Mapping):
            raise NodeInputArtifactAttestationError(
                "node input artifact must be an object",
            )
        artifact_ref = str(raw.get("artifact_ref") or "").strip()
        receipt_ref = str(raw.get("receipt_ref") or "").strip()
        payload = raw.get("payload")
        payload_sha256 = str(raw.get("payload_sha256") or "").strip().lower()
        if (
            not is_task_result_artifact_ref(artifact_ref)
            or artifact_ref in seen
            or not receipt_ref
            or not isinstance(payload, Mapping)
            or payload_sha256 != artifact_digest(payload)
            or str(payload.get("receipt_ref") or "").strip() != receipt_ref
        ):
            raise NodeInputArtifactAttestationError(
                "node input artifact identity or payload digest is invalid",
            )
        seen.add(artifact_ref)
        entries.append(
            {
                "artifact_ref": artifact_ref,
                "receipt_ref": receipt_ref,
                "payload_sha256": payload_sha256,
            }
        )
    subject = {
        "schema_version": NODE_INPUT_ARTIFACT_ATTESTATION_SCHEMA,
        "artifacts": entries,
    }
    return {
        **subject,
        "bundle_digest": "sha256:" + hashlib.sha256(
            _canonical_json(subject).encode("utf-8")
        ).hexdigest(),
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise NodeInputArtifactAttestationError(
            "node input artifact attestation must be finite JSON",
        ) from exc


__all__ = [
    "NODE_INPUT_ARTIFACT_ATTESTATION_SCHEMA",
    "NodeInputArtifactAttestationError",
    "build_node_input_artifact_attestation",
]
