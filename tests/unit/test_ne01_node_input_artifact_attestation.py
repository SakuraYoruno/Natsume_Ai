from __future__ import annotations

from copy import deepcopy

import pytest

from host_brain.contracts.agent_runtime.node_artifact import artifact_digest
from host_brain.contracts.agent_runtime.node_input_attestation import (
    NODE_INPUT_ARTIFACT_ATTESTATION_SCHEMA,
    NodeInputArtifactAttestationError,
    build_node_input_artifact_attestation,
)


def _artifact() -> dict[str, object]:
    receipt_ref = "worker-projection://accepted/" + "a" * 64
    payload = {
        "receipt_ref": receipt_ref,
        "narrative": "accepted predecessor",
    }
    return {
        "artifact_ref": (
            "artifact://task-result/graph-a/revisions/2/nodes/node-a"
        ),
        "receipt_ref": receipt_ref,
        "payload": payload,
        "payload_sha256": artifact_digest(payload),
    }


def test_attestation_binds_artifact_receipt_and_payload_digest() -> None:
    artifact = _artifact()

    attestation = build_node_input_artifact_attestation([artifact])

    assert (
        attestation["schema_version"]
        == NODE_INPUT_ARTIFACT_ATTESTATION_SCHEMA
    )
    assert attestation["artifacts"] == [
        {
            "artifact_ref": artifact["artifact_ref"],
            "receipt_ref": artifact["receipt_ref"],
            "payload_sha256": artifact["payload_sha256"],
        }
    ]
    assert str(attestation["bundle_digest"]).startswith("sha256:")


@pytest.mark.parametrize("field", ("receipt_ref", "payload_sha256"))
def test_attestation_rejects_artifact_identity_or_digest_drift(
    field: str,
) -> None:
    artifact = deepcopy(_artifact())
    artifact[field] = (
        "worker-projection://drifted/" + "b" * 64
        if field == "receipt_ref"
        else "sha256:" + "0" * 64
    )

    with pytest.raises(NodeInputArtifactAttestationError):
        build_node_input_artifact_attestation([artifact])


def test_attestation_rejects_duplicate_artifact_reference() -> None:
    artifact = _artifact()

    with pytest.raises(NodeInputArtifactAttestationError):
        build_node_input_artifact_attestation([artifact, artifact])
