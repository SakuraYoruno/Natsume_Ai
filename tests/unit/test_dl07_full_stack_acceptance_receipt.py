"""DL-07 receipt rules must not upgrade local or synthetic evidence."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from natsume_contracts.life.full_stack_acceptance import (
    FULL_STACK_ACCEPTANCE_FAIL,
    FULL_STACK_ACCEPTANCE_PASS,
    FULL_STACK_ACCEPTANCE_PENDING_EXTERNAL,
    REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA,
    DockerServiceEvidenceV1,
    FullStackAcceptanceReceiptV1,
    FullStackComponentV1,
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _component(component_id: str, *, status: str = "PASS") -> FullStackComponentV1:
    listener = {
        "docker": "",
        "neo4j": "loopback://127.0.0.1:17688",
        "chroma": "loopback://127.0.0.1:18001",
        "redis": "loopback://127.0.0.1:16380",
        "host": "loopback://127.0.0.1:18090",
        "obs": "loopback://127.0.0.1:18092",
        "monitor": "loopback://127.0.0.1:18190",
        "gateway": "",
        "edge": "",
        "model_provider": "",
        "asr": "",
        "tts": "",
        "avatar": "",
    }[component_id]
    return FullStackComponentV1(
        component_id=component_id,
        status=status,
        evidence_kind=(
            "REAL_HTTP" if component_id in {"host", "obs", "monitor"}
            else "REAL_DOCKER_IO" if component_id in {"docker", "neo4j", "chroma", "redis"}
            else "REAL_EXTERNAL_RECEIPT" if status == "PASS" else "REMOTE_NOT_STARTED"
        ),
        evidence_digest=_digest({"component": component_id, "status": status}),
        listener=listener,
        process_id=123 if component_id in {"host", "obs", "monitor"} else 0,
        reason_codes=() if status == "PASS" else ("REMOTE_EDGE_NOT_STARTED",),
    )


def _receipt(*, external_status: str = "NOT_TESTED", local_status: str = "PASS") -> FullStackAcceptanceReceiptV1:
    local = tuple(
        _component(component_id, status=local_status)
        for component_id in ("docker", "neo4j", "chroma", "redis", "host", "obs", "monitor")
    )
    external = tuple(
        _component(component_id, status=external_status)
        for component_id in ("gateway", "edge", "model_provider", "asr", "tts", "avatar")
    )
    expected = (
        FULL_STACK_ACCEPTANCE_PASS
        if external_status == "PASS" and local_status == "PASS"
        else FULL_STACK_ACCEPTANCE_FAIL
        if local_status != "PASS"
        else FULL_STACK_ACCEPTANCE_PENDING_EXTERNAL
    )
    docker_services = tuple(
        DockerServiceEvidenceV1(
            service_id=service_id,
            status=local_status,
            container_id=("a" if service_id == "neo4j" else "b" if service_id == "chroma" else "c") * 64,
            image_id=("d" if service_id == "neo4j" else "e" if service_id == "chroma" else "f") * 64,
            image_ref=f"example/{service_id}@sha256:" + ("1" if service_id == "neo4j" else "2" if service_id == "chroma" else "3") * 64,
            host_port={"neo4j": 17688, "chroma": 18001, "redis": 16380}[service_id],
            started_at="2026-07-29T10:00:00Z",
            evidence_digest=_digest({"service": service_id, "status": local_status}),
        )
        for service_id in ("neo4j", "chroma", "redis")
    )
    return FullStackAcceptanceReceiptV1(
        commit_sha="a" * 40,
        run_id="dl07.acceptance:20260729",
        started_at_ms=100,
        finished_at_ms=200,
        configuration_digest=_digest({"scope": "isolated"}),
        docker_evidence_digest=_digest({"docker": "receipt"}),
        docker_services=docker_services,
        local_components=local,
        external_components=external,
        remote_ci_status=REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA,
        outcome=expected,
    )


def test_all_real_components_are_required_for_full_pass() -> None:
    receipt = _receipt(external_status="PASS")

    assert receipt.outcome == FULL_STACK_ACCEPTANCE_PASS
    assert receipt.as_dict()["privacy"]["contains_secrets"] is False
    assert FullStackAcceptanceReceiptV1.from_mapping(receipt.as_dict()) == receipt


def test_missing_remote_edge_or_real_model_stays_pending_external() -> None:
    receipt = _receipt(external_status="NOT_TESTED")

    assert receipt.outcome == FULL_STACK_ACCEPTANCE_PENDING_EXTERNAL
    assert receipt.remote_ci_status == REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA


def test_local_failure_cannot_be_hidden_by_missing_external_environment() -> None:
    receipt = _receipt(external_status="NOT_TESTED", local_status="FAIL")

    assert receipt.outcome == FULL_STACK_ACCEPTANCE_FAIL


def test_tampering_or_private_fields_are_rejected() -> None:
    receipt = _receipt(external_status="NOT_TESTED")
    tampered = copy.deepcopy(receipt.as_dict())
    tampered["local_components"][0]["status"] = "FAIL"
    with pytest.raises(ValueError, match="outcome differs|receipt_digest"):
        FullStackAcceptanceReceiptV1.from_mapping(tampered)

    private = copy.deepcopy(receipt.as_dict())
    private["secret_material"] = "no"
    with pytest.raises(ValueError, match="forbidden field|field set"):
        FullStackAcceptanceReceiptV1.from_mapping(private)


def test_synthetic_evidence_never_proves_a_passing_component() -> None:
    with pytest.raises(ValueError, match="synthetic evidence"):
        FullStackComponentV1(
            component_id="host",
            status="PASS",
            evidence_kind="MOCK_HTTP",
            evidence_digest=_digest({"fake": True}),
            listener="loopback://127.0.0.1:18090",
        )
