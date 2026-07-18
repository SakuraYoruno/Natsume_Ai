from __future__ import annotations

import asyncio
import base64
import json

import pytest
from fastapi import Request
from fastapi import HTTPException

from host_brain.app.obs_routes_task_runtime import (
    get_task_terminal_reconciliation_challenge,
    list_task_terminal_reconciliation_candidates,
    list_task_terminal_reconciliation_cases,
)
from host_brain.app.settings.core import get_core_settings
from host_brain.observability.operator_query_gateway import (
    DigestOperatorQueryAuthenticator,
    OperatorQueryAuthStatus,
    OperatorQueryAuthority,
    OperatorQueryContext,
    OperatorQueryGateway,
)


def _aggregate() -> dict:
    return {
        "case": {
            "schema_version": "Natsume.task_runtime.terminal_reconciliation.case.v1",
            "case_id": "terminal-reconciliation-case://sha256/" + "a" * 64,
            "case_kind": "BLOCKED_FAILURE_NODE",
            "classification": "SIGNED_REPAIR_REQUIRED",
            "reason_code": "HISTORICAL_BLOCKED_FAILURE",
            "graph_id": "graph-terminal-one",
            "graph_revision": 3,
            "graph_status": "BLOCKED",
            "graph_status_version": 4,
            "budget_id": "budget-terminal-one",
            "budget_status": "ACTIVE",
            "budget_status_version": 2,
            "node_id": "node-terminal-one",
            "node_status": "FAILED",
            "node_status_version": 3,
            "attempt_id": "attempt-terminal-one",
            "attempt_status": "FAILED",
            "attempt_status_version": 4,
            "recommended_action": "FAIL_GRAPH_AND_RELEASE_BUDGET",
            "snapshot_sha256": "sha256:" + "b" * 64,
            "created_ts_ms": 1,
            "snapshot": {"private": "CASE_SNAPSHOT_MUST_NOT_LEAK"},
        },
        "subject": {
            "snapshot": {"private": "SUBJECT_SNAPSHOT_MUST_NOT_LEAK"},
            "snapshot_json": "SUBJECT_JSON_MUST_NOT_LEAK",
            "snapshot_sha256": "sha256:" + "c" * 64,
        },
        "proof": {
            "proof_id": "terminal-reconciliation-proof://sha256/" + "d" * 64,
            "case_id": "terminal-reconciliation-case://sha256/" + "a" * 64,
            "case_snapshot_sha256": "sha256:" + "b" * 64,
            "authority_kind": "SIGNED_OPERATOR",
            "action": "FAIL_GRAPH_AND_RELEASE_BUDGET",
            "target_graph_status": "FAILED",
            "target_budget_status": "RELEASED",
            "subject_snapshot_sha256": "sha256:" + "c" * 64,
            "proof_digest": "sha256:" + "e" * 64,
            "issued_ts_ms": 5,
            "attestation_signature": "SIGNATURE_MUST_NOT_LEAK",
            "proof_binding": {"private": "BINDING_MUST_NOT_LEAK"},
            "reconciler_actor_id": "PRIVATE_ACTOR_MUST_NOT_LEAK",
        },
    }


class _Store:
    def __init__(self) -> None:
        self.aggregate = _aggregate()

    def list_terminal_reconciliation_candidates(
        self,
        graph_id,
        graph_revision,
        *,
        limit,
    ):  # noqa: ANN001
        assert graph_id == "graph-terminal-one"
        assert graph_revision == 3
        assert limit == 7
        return (self.aggregate,)

    def list_terminal_reconciliation_cases(
        self,
        *,
        classification,
        limit,
    ):  # noqa: ANN001
        assert classification == "SIGNED_REPAIR_REQUIRED"
        assert limit == 5
        return (self.aggregate["case"],)

    def read_terminal_reconciliation_case(self, case_id):  # noqa: ANN001
        assert case_id == self.aggregate["case"]["case_id"]
        return self.aggregate


def _operator_request() -> Request:
    context = OperatorQueryContext(
        authority=OperatorQueryAuthority(
            principal_id="ops.terminal-reconciler",
            scopes=frozenset({"obs:task-runtime:read"}),
            auth_status=OperatorQueryAuthStatus.AUTHENTICATED,
            key_id="terminal-reconciliation-v1",
        )
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/obs/task-runtime/terminal-reconciliation/challenge",
            "headers": [],
            "state": {"operator_query_context": context},
        }
    )


def test_terminal_operator_reads_are_minimal_and_signature_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    store = _Store()
    case_id = store.aggregate["case"]["case_id"]
    try:
        candidates = asyncio.run(
            list_task_terminal_reconciliation_candidates(
                graph_id="graph-terminal-one",
                graph_revision=3,
                limit=7,
                store=store,
            )
        )
        cases = asyncio.run(
            list_task_terminal_reconciliation_cases(
                classification="SIGNED_REPAIR_REQUIRED",
                limit=5,
                store=store,
            )
        )
        store.aggregate["proof"] = None
        challenge = asyncio.run(
            get_task_terminal_reconciliation_challenge(
                request=_operator_request(),
                case_id=case_id,
                attestation_ref="attestation://terminal-reconciliation/operator-read",
                attestation_key_id="terminal-reconciliation-v1",
                issued_ts_ms=1_900_000_000_123,
                store=store,
            )
        )
    finally:
        get_core_settings.cache_clear()

    assert candidates["read_only"] is True
    assert candidates["count"] == 1
    assert candidates["candidates"][0]["settled"] is True
    assert cases["read_only"] is True
    assert set(cases["cases"][0]) == {
        "case_id",
        "case_kind",
        "classification",
        "reason_code",
        "graph_id",
        "graph_revision",
        "graph_status",
        "budget_status",
        "node_status",
        "attempt_status",
        "recommended_action",
        "snapshot_sha256",
    }
    assert challenge["subject_snapshot_sha256"] == "sha256:" + "c" * 64
    assert challenge["recommended_resolution"] == {
        "action": "FAIL_GRAPH_AND_RELEASE_BUDGET",
        "target_graph_status": "FAILED",
        "target_budget_status": "RELEASED",
    }
    signing_subject = json.loads(
        base64.urlsafe_b64decode(
            challenge["signing_material"]["payload_b64"].encode("ascii")
        )
    )
    assert signing_subject["case"]["case_id"] == case_id
    assert signing_subject["case"]["graph_status_version"] == 4
    assert signing_subject["subject_snapshot_sha256"] == "sha256:" + "c" * 64
    assert signing_subject["resolution"] == challenge["recommended_resolution"]
    assert signing_subject["authority"] == {
        "authority_kind": "SIGNED_OPERATOR",
        "reconciler_actor_id": "ops.terminal-reconciler",
        "attestation_ref": "attestation://terminal-reconciliation/operator-read",
        "attestation_key_id": "terminal-reconciliation-v1",
        "issued_ts_ms": 1_900_000_000_123,
    }
    assert signing_subject["outboxes"]["graph"]["attempt_id"] is None
    assert signing_subject["outboxes"]["budget"]["attempt_id"] is None
    assert challenge["settlement"] is None
    assert set(candidates["candidates"][0]["settlement"]) == {
        "record_ref",
        "case_id",
        "authority_kind",
        "action",
        "target_graph_status",
        "target_budget_status",
        "case_snapshot_sha256",
        "subject_snapshot_sha256",
        "binding_sha256",
        "issued_ts_ms",
    }
    assert challenge["command_request"] == {
        "case_id": case_id,
        "expected_case_snapshot_sha256": "sha256:" + "b" * 64,
        "expected_subject_snapshot_sha256": "sha256:" + "c" * 64,
        "action": "FAIL_GRAPH_AND_RELEASE_BUDGET",
        "target_graph_status": "FAILED",
        "target_budget_status": "RELEASED",
        "issued_ts_ms": 1_900_000_000_123,
        "attestation_ref": "attestation://terminal-reconciliation/operator-read",
        "attestation_key_id": "terminal-reconciliation-v1",
    }
    serialized = repr((candidates, cases, challenge))
    for secret in (
        "CASE_SNAPSHOT_MUST_NOT_LEAK",
        "SUBJECT_SNAPSHOT_MUST_NOT_LEAK",
        "SUBJECT_JSON_MUST_NOT_LEAK",
        "SIGNATURE_MUST_NOT_LEAK",
        "BINDING_MUST_NOT_LEAK",
        "PRIVATE_ACTOR_MUST_NOT_LEAK",
    ):
        assert secret not in serialized
    transport = OperatorQueryGateway(
        DigestOperatorQueryAuthenticator()
    ).serialize_public(challenge)
    assert transport.redacted_fields == 0


def test_terminal_challenge_rejects_key_different_from_authenticated_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    store = _Store()
    store.aggregate["proof"] = None
    try:
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(
                get_task_terminal_reconciliation_challenge(
                    request=_operator_request(),
                    case_id=store.aggregate["case"]["case_id"],
                    attestation_ref=(
                        "attestation://terminal-reconciliation/key-mismatch"
                    ),
                    attestation_key_id="different-key",
                    issued_ts_ms=1_900_000_000_123,
                    store=store,
                )
            )
    finally:
        get_core_settings.cache_clear()

    assert rejected.value.status_code == 409
    assert rejected.value.detail["reason_code"] == (
        "TASK_TERMINAL_RECONCILIATION_KEY_MISMATCH"
    )
