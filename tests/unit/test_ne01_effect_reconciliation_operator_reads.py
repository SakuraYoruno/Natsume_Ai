from __future__ import annotations

import asyncio
import base64
import json

import pytest

from host_brain.app.obs_routes_task_runtime import (
    get_task_effect_reconciliation_challenge,
    list_task_effect_reconciliation_candidates,
    list_task_effect_reconciliation_cases,
)
from host_brain.app.settings.core import get_core_settings


def _aggregate() -> dict:
    return {
        "case": {
            "schema_version": "case.v1",
            "case_id": "effect-case://one",
            "intent_ref": "cancel-intent://one",
            "effect_id": "effect://one",
            "graph_id": "graph-one",
            "graph_revision": 3,
            "node_id": "node-one",
            "attempt_id": "attempt-one",
            "purpose": "PENDING_CANCELLATION",
            "classification": "REPAIRABLE_REQUESTED",
            "case_kind": "RECONCILIATION_REQUIRED",
            "reason_code": "EFFECT_OUTCOME_UNCERTAIN",
            "legacy_decision_ref": None,
            "legacy_decision_payload_sha256": None,
            "snapshot_sha256": "sha256:" + "a" * 64,
            "created_ts_ms": 1,
            "snapshot_json": "PRIVATE_SNAPSHOT_MUST_NOT_LEAK",
            "snapshot": {"private": "MUST_NOT_LEAK"},
        },
        "intent": {
            "intent_ref": "cancel-intent://one",
            "request_id": "request-one",
            "payload_sha256": "sha256:" + "b" * 64,
            "status": "REQUESTED",
            "status_version": 0,
            "graph_id": "graph-one",
            "graph_revision": 3,
        },
        "effect": {
            "effect_id": "effect://one",
            "graph_id": "graph-one",
            "graph_revision": 3,
            "node_id": "node-one",
            "attempt_id": "attempt-one",
            "commit_scope": "GRAPH",
            "effect_type": "surface.reply_delivery",
            "payload_ref": "payload://one",
            "payload_sha256": "sha256:" + "c" * 64,
            "idempotency_key": "idempotency-one",
            "status": "FAILED",
            "status_version": 2,
            "attempt_count": 1,
            "fencing_token": 1,
            "available_ts_ms": 1,
            "committed_ts_ms": None,
            "created_ts_ms": 1,
            "failed_ts_ms": 2,
            "lease_owner": "dispatcher-one",
            "lease_expires_ts_ms": None,
            "receipt_ref": None,
            "reason_code": "HANDLER_FAILED",
            "last_error_sha256": "sha256:" + "d" * 64,
            "updated_ts_ms": 2,
            "last_error": "RAW_ERROR_MUST_NOT_LEAK",
        },
        "fence": {
            "effect_id": "effect://one",
            "intent_ref": "cancel-intent://one",
            "observed_status": "FAILED",
            "observed_status_version": 2,
            "resolution_status": "RECONCILIATION_REQUIRED",
        },
        "attempt": {
            "attempt_id": "attempt-one",
            "graph_id": "graph-one",
            "graph_revision": 3,
            "node_id": "node-one",
            "status": "SUCCEEDED",
            "status_version": 1,
        },
        "subject_snapshot_sha256": "sha256:" + "e" * 64,
        "proof": {
            "schema_version": "proof.v1",
            "proof_id": "effect-proof://one",
            "purpose": "PENDING_CANCELLATION",
            "case_id": "effect-case://one",
            "intent_ref": "cancel-intent://one",
            "effect_id": "effect://one",
            "outcome": "NO_SIDE_EFFECT",
            "target_resolution_status": "CANCELLED",
            "receipt_ref": None,
            "executor_quiescence_ref": None,
            "attestation_ref": "attestation://one",
            "attestation_key_id": "key-one",
            "case_snapshot_sha256": "sha256:" + "a" * 64,
            "subject_snapshot_sha256": "sha256:" + "e" * 64,
            "proof_digest": "sha256:" + "f" * 64,
            "outbox_id": "outbox-one",
            "issued_ts_ms": 3,
            "created_ts_ms": 4,
            "attestation_signature": "SIGNATURE_MUST_NOT_LEAK",
            "proof_binding_json": "BINDING_MUST_NOT_LEAK",
            "reconciler_actor_id": "PRIVATE_ACTOR_MUST_NOT_LEAK",
        },
    }


class _Store:
    def __init__(self) -> None:
        self.aggregate = _aggregate()

    def list_effect_reconciliation_candidates(self, intent_ref, *, limit):  # noqa: ANN001
        assert intent_ref == "cancel-intent://one"
        assert limit == 7
        return ({**self.aggregate, "proof_exists": True},)

    def list_effect_reconciliation_cases(self, *, classification, limit):  # noqa: ANN001
        assert classification == "QUARANTINED_FINALIZED"
        assert limit == 5
        return (self.aggregate["case"],)

    def read_graph_cancellation_effect_reconciliation(self, intent_ref, effect_id):  # noqa: ANN001
        assert (intent_ref, effect_id) == ("cancel-intent://one", "effect://one")
        return self.aggregate


def test_operator_reads_expose_exact_digests_without_raw_proof_or_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    store = _Store()

    candidates = asyncio.run(
        list_task_effect_reconciliation_candidates(
            intent_ref="cancel-intent://one",
            limit=7,
            store=store,
        )
    )
    cases = asyncio.run(
        list_task_effect_reconciliation_cases(
            classification="QUARANTINED_FINALIZED",
            limit=5,
            store=store,
        )
    )
    challenge = asyncio.run(
        get_task_effect_reconciliation_challenge(
            intent_ref="cancel-intent://one",
            effect_id="effect://one",
            store=store,
        )
    )

    assert candidates["count"] == 1
    assert candidates["candidates"][0]["settled"] is True
    assert cases["cases"][0]["classification"] == "REPAIRABLE_REQUESTED"
    assert set(cases["cases"][0]) == {
        "case_id",
        "cancellation_ref",
        "effect_id",
        "purpose",
        "classification",
        "case_kind",
        "reason_code",
        "snapshot_sha256",
    }
    assert challenge["subject_snapshot_sha256"] == "sha256:" + "e" * 64
    assert set(challenge["settlement"]) == {
        "record_ref",
        "case_id",
        "outcome",
        "target_resolution_status",
        "case_snapshot_sha256",
        "subject_snapshot_sha256",
        "binding_sha256",
        "issued_ts_ms",
    }
    for field_name in (
        "payload_ref",
        "idempotency_key",
        "fencing_token",
        "lease_owner",
        "receipt_ref",
    ):
        assert field_name not in challenge["effect"]
    signing_subject = json.loads(
        base64.urlsafe_b64decode(
            challenge["signing_material"]["payload_b64"].encode("ascii")
        )
    )
    assert signing_subject["case"]["intent_ref"] == "cancel-intent://one"
    assert signing_subject["effect"]["payload_ref"] == "payload://one"
    assert signing_subject["effect"]["idempotency_key"] == "idempotency-one"
    assert signing_subject["effect"]["fencing_token"] == 1
    serialized = repr((candidates, cases, challenge))
    for secret in (
        "PRIVATE_SNAPSHOT_MUST_NOT_LEAK",
        "RAW_ERROR_MUST_NOT_LEAK",
        "SIGNATURE_MUST_NOT_LEAK",
        "BINDING_MUST_NOT_LEAK",
        "PRIVATE_ACTOR_MUST_NOT_LEAK",
    ):
        assert secret not in serialized
    get_core_settings.cache_clear()
