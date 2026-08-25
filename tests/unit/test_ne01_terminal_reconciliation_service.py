from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.contracts.agent_runtime.effect_reconciliation import (
    build_graph_cancellation_effect_reconciliation_binding,
)
from host_brain.contracts.agent_runtime.terminal_reconciliation import (
    build_terminal_reconciliation_attestation_binding,
    build_terminal_reconciliation_binding,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.task_runtime.terminal_state_reconciliation import (
    TERMINAL_RECONCILIATION_REJECTED,
    TerminalStateReconciliationError,
    TerminalStateReconciliationService,
)


CASE_ID = "terminal-reconciliation-case://sha256/" + "a" * 64
CASE_DIGEST = "sha256:" + "b" * 64
SUBJECT_DIGEST = "sha256:" + "c" * 64
ATTESTATION_REF = "attestation://terminal-reconciliation/1"
SIGNATURE = "ed25519:" + "A" * 86 + "=="


def _aggregate(
    *,
    classification: str = "SIGNED_REPAIR_REQUIRED",
    action: str = "FAIL_GRAPH_AND_RELEASE_BUDGET",
    graph_status: str = "BLOCKED",
) -> dict[str, Any]:
    return {
        "case": {
            "case_id": CASE_ID,
            "schema_version": "Natsume.task_runtime.terminal_reconciliation.case.v1",
            "case_kind": "BLOCKED_FAILURE_NODE",
            "classification": classification,
            "reason_code": "HISTORICAL_BLOCKED_FAILURE",
            "graph_id": "graph-terminal-reconciliation",
            "graph_revision": 1,
            "graph_status": graph_status,
            "graph_status_version": 4,
            "budget_id": "budget-terminal-reconciliation",
            "budget_status": "ACTIVE",
            "budget_status_version": 2,
            "node_id": "worker",
            "attempt_id": "attempt-terminal-reconciliation",
            "recommended_action": action,
            "snapshot_sha256": CASE_DIGEST,
        },
        "subject": {"snapshot_sha256": SUBJECT_DIGEST},
        "proof": None,
        "graph": {"status": graph_status, "status_version": 4},
        "budget": {"status": "ACTIVE", "status_version": 2},
    }


class _Store:
    def __init__(
        self,
        aggregate: dict[str, Any],
        *,
        lose_response: bool = False,
        drift_on_second_read: bool = False,
    ) -> None:
        self.aggregate = aggregate
        self.lose_response = lose_response
        self.drift_on_second_read = drift_on_second_read
        self.read_calls = 0
        self.record_calls: list[dict[str, Any]] = []
        self.candidate_classifications: list[str | None] = []

    def read_terminal_reconciliation_case(self, case_id: str) -> dict[str, Any]:
        assert case_id == CASE_ID
        self.read_calls += 1
        if self.drift_on_second_read and self.read_calls == 2:
            drifted = deepcopy(self.aggregate)
            drifted["subject"]["snapshot_sha256"] = "sha256:" + "d" * 64
            return drifted
        return self.aggregate

    def list_terminal_reconciliation_candidates(
        self,
        graph_id: str | None = None,
        graph_revision: int | None = None,
        *,
        classification: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        del graph_id, graph_revision, limit
        self.candidate_classifications.append(classification)
        if (
            classification is not None
            and self.aggregate["case"].get("classification") != classification
        ):
            return ()
        return (self.aggregate,) if self.aggregate.get("proof") is None else ()

    def record_terminal_reconciliation_proof(
        self,
        case_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert case_id == CASE_ID
        self.record_calls.append(dict(kwargs))
        graph_outbox = kwargs["graph_outbox"]
        budget_outbox = kwargs["budget_outbox"]
        self.aggregate["proof"] = {
            "proof_id": kwargs["proof_id"],
            "case_id": case_id,
            "case_snapshot_sha256": kwargs["expected_case_snapshot_sha256"],
            "subject_snapshot_sha256": kwargs[
                "expected_subject_snapshot_sha256"
            ],
            "action": kwargs["action"],
            "target_graph_status": kwargs["target_graph_status"],
            "target_budget_status": kwargs["target_budget_status"],
            "authority_kind": kwargs["authority_kind"],
            "reconciler_actor_id": kwargs["reconciler_actor_id"],
            "issued_ts_ms": kwargs["issued_ts_ms"],
            "attestation_ref": kwargs["attestation_ref"],
            "attestation_key_id": kwargs["attestation_key_id"],
            "attestation_signature": kwargs["attestation_signature"],
            "graph_outbox_id": (
                graph_outbox.outbox_id if graph_outbox is not None else None
            ),
            "budget_outbox_id": (
                budget_outbox.outbox_id if budget_outbox is not None else None
            ),
        }
        if kwargs["action"] != "NONE":
            self.aggregate["graph"] = {"status": "FAILED", "status_version": 5}
            self.aggregate["budget"] = {"status": "RELEASED", "status_version": 3}
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("simulated committed response loss")
        return self.aggregate


def _service(store: _Store) -> TerminalStateReconciliationService:
    return TerminalStateReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        clock_ms=lambda: 2_000_000_000_000,
    )


def _signed_request() -> dict[str, Any]:
    return {
        "case_id": CASE_ID,
        "expected_case_snapshot_sha256": CASE_DIGEST,
        "expected_subject_snapshot_sha256": SUBJECT_DIGEST,
        "action": "FAIL_GRAPH_AND_RELEASE_BUDGET",
        "target_graph_status": "FAILED",
        "target_budget_status": "RELEASED",
        "reconciler_actor_id": "operator-terminal-reconciliation",
        "issued_ts_ms": 2_000_000_000_000,
        "attestation_ref": ATTESTATION_REF,
        "attestation_key_id": "terminal-key-1",
        "attestation_signature": SIGNATURE,
    }


def _challenge_request() -> dict[str, Any]:
    return {
        "case_id": CASE_ID,
        "reconciler_actor_id": "operator-terminal-reconciliation",
        "issued_ts_ms": 2_000_000_000_000,
        "attestation_ref": ATTESTATION_REF,
        "attestation_key_id": "terminal-key-1",
    }


def test_operator_challenge_is_exact_signable_and_store_identical() -> None:
    store = _Store(_aggregate())
    service = _service(store)

    challenge = service.build_operator_challenge(**_challenge_request())

    assert store.read_calls == 2
    assert store.record_calls == []
    assert challenge["case_snapshot_sha256"] == CASE_DIGEST
    assert challenge["subject_snapshot_sha256"] == SUBJECT_DIGEST
    assert challenge["action"] == "FAIL_GRAPH_AND_RELEASE_BUDGET"
    assert challenge["target_graph_status"] == "FAILED"
    assert challenge["target_budget_status"] == "RELEASED"
    assert "attestation_signature" not in challenge
    assert "attestation_signature" not in challenge["binding"]["authority"]
    binding_bytes = base64.urlsafe_b64decode(
        challenge["signing_material"]["payload_b64"].encode("ascii")
    )
    assert json.loads(binding_bytes) == challenge["binding"]
    assert challenge["signing_material"]["payload_sha256"] == (
        "sha256:" + hashlib.sha256(binding_bytes).hexdigest()
    )
    assert build_terminal_reconciliation_binding(challenge["binding"]) == binding_bytes
    graph_binding = challenge["binding"]["outboxes"]["graph"]
    budget_binding = challenge["binding"]["outboxes"]["budget"]
    neutral_mapping, neutral_bytes = build_terminal_reconciliation_attestation_binding(
        case=challenge["binding"]["case"],
        subject_snapshot_sha256=SUBJECT_DIGEST,
        action=challenge["action"],
        target_graph_status=challenge["target_graph_status"],
        target_budget_status=challenge["target_budget_status"],
        authority_kind="SIGNED_OPERATOR",
        reconciler_actor_id="operator-terminal-reconciliation",
        attestation_ref=ATTESTATION_REF,
        attestation_key_id="terminal-key-1",
        issued_ts_ms=2_000_000_000_000,
        graph_outbox=graph_binding,
        budget_outbox=budget_binding,
    )
    assert neutral_mapping == challenge["binding"]
    assert neutral_bytes == binding_bytes

    private_key = Ed25519PrivateKey.generate()
    signature = private_key.sign(binding_bytes)
    private_key.public_key().verify(signature, binding_bytes)
    encoded_signature = "ed25519:" + base64.urlsafe_b64encode(signature).decode(
        "ascii"
    )
    assert len(encoded_signature) == 96
    reconciled = service.reconcile_terminal_state(
        case_id=challenge["case_id"],
        expected_case_snapshot_sha256=challenge["case_snapshot_sha256"],
        expected_subject_snapshot_sha256=challenge["subject_snapshot_sha256"],
        action=challenge["action"],
        target_graph_status=challenge["target_graph_status"],
        target_budget_status=challenge["target_budget_status"],
        reconciler_actor_id=challenge["authority"]["reconciler_actor_id"],
        issued_ts_ms=challenge["authority"]["issued_ts_ms"],
        attestation_ref=challenge["authority"]["attestation_ref"],
        attestation_key_id=challenge["authority"]["attestation_key_id"],
        attestation_signature=encoded_signature,
    )
    assert reconciled["status"] == "RECONCILED"
    assert store.record_calls[0]["proof_id"] == challenge["proof_id"]
    assert store.record_calls[0]["graph_outbox"].outbox_id == challenge[
        "graph_outbox_id"
    ]
    assert store.record_calls[0]["budget_outbox"].outbox_id == challenge[
        "budget_outbox_id"
    ]


def test_operator_challenge_derives_deterministic_proof_and_outbox_bindings() -> None:
    store = _Store(_aggregate())
    service = _service(store)

    first = service.build_operator_challenge(**_challenge_request())
    second = service.build_operator_challenge(**_challenge_request())

    assert first == second
    assert store.record_calls == []
    assert first["proof_id"] in first["binding"]["outboxes"]["graph"][
        "evidence_refs"
    ]
    assert first["graph_outbox_id"] == first["binding"]["outboxes"]["graph"][
        "outbox_id"
    ]
    assert first["budget_outbox_id"] == first["binding"]["outboxes"]["budget"][
        "outbox_id"
    ]
    assert first["binding"]["outboxes"]["graph"]["attempt_id"] is None


def test_operator_challenge_rejects_subject_drift_without_writing() -> None:
    store = _Store(_aggregate(), drift_on_second_read=True)

    with pytest.raises(TerminalStateReconciliationError) as rejected:
        _service(store).build_operator_challenge(**_challenge_request())

    assert rejected.value.reason_code == TERMINAL_RECONCILIATION_REJECTED
    assert rejected.value.side_effects_may_have_run is False
    assert store.read_calls == 2
    assert store.record_calls == []


def test_operator_challenge_rejects_proved_case_without_writing() -> None:
    aggregate = _aggregate()
    aggregate["proof"] = {"proof_id": "proof://already-recorded"}
    store = _Store(aggregate)

    with pytest.raises(TerminalStateReconciliationError):
        _service(store).build_operator_challenge(**_challenge_request())

    assert store.record_calls == []


def test_terminal_binding_rejects_extra_fields_and_cross_domain_substitution() -> None:
    challenge = _service(_Store(_aggregate())).build_operator_challenge(
        **_challenge_request()
    )
    assert set(challenge["binding"]) == {
        "schema_version",
        "domain",
        "subject_snapshot_sha256",
        "case",
        "resolution",
        "authority",
        "outboxes",
    }
    assert set(challenge["binding"]["authority"]) == {
        "authority_kind",
        "reconciler_actor_id",
        "attestation_ref",
        "attestation_key_id",
        "issued_ts_ms",
    }
    assert set(challenge["binding"]["outboxes"]) == {"graph", "budget"}
    assert set(challenge["binding"]["outboxes"]["graph"]) == {
        "aggregate_type",
        "aggregate_id",
        "aggregate_version",
        "graph_revision",
        "event_type",
        "payload_ref",
        "attempt_id",
        "evidence_refs",
        "idempotency_key",
        "outbox_id",
    }
    binding = deepcopy(challenge["binding"])
    binding["case"]["unexpected"] = True
    with pytest.raises(ValueError):
        build_terminal_reconciliation_binding(binding)

    outbox_drift = deepcopy(challenge["binding"])
    outbox_drift["outboxes"]["graph"]["unexpected"] = True
    with pytest.raises(ValueError):
        build_terminal_reconciliation_binding(outbox_drift)

    wrong_domain = deepcopy(challenge["binding"])
    wrong_domain["domain"] = "Natsume.task_runtime.acceptance_effect_reconciliation"
    with pytest.raises(ValueError):
        build_terminal_reconciliation_binding(wrong_domain)
    with pytest.raises(ValueError):
        build_graph_cancellation_effect_reconciliation_binding(
            challenge["binding"]
        )


def test_signed_repair_builds_exact_graph_and_budget_outboxes() -> None:
    store = _Store(_aggregate())

    result = _service(store).reconcile_terminal_state(**_signed_request())

    assert result["status"] == "RECONCILED"
    assert result["replayed"] is False
    assert result["proof"]["action"] == "FAIL_GRAPH_AND_RELEASE_BUDGET"
    assert "attestation_signature" not in result["proof"]
    assert "proof_binding" not in result["proof"]
    assert "snapshot" not in result["case"]
    assert len(store.record_calls) == 1
    call = store.record_calls[0]
    assert call["authority_kind"] == "SIGNED_OPERATOR"
    assert call["graph_outbox"].event_type == "TASK_GRAPH_FAILED"
    assert call["graph_outbox"].graph_revision == 1
    assert call["graph_outbox"].attempt_id is None
    assert call["budget_outbox"].event_type == "BUDGET_RESERVATION_RELEASED"
    assert call["budget_outbox"].graph_revision is None
    assert call["proof_id"] in call["graph_outbox"].evidence_refs
    assert CASE_ID in call["budget_outbox"].evidence_refs


def test_signed_healthy_closure_uses_none_targets_and_writes_no_outbox() -> None:
    aggregate = _aggregate(action="NONE", graph_status="FAILED")
    aggregate["case"].update(
        {
            "case_kind": "TERMINAL_HEALTHY_CLOSURE",
            "reason_code": "CURRENT_TERMINAL_STATE_HEALTHY_REQUIRES_SIGNED_CLOSURE",
            "budget_status": "RELEASED",
            "node_id": None,
            "attempt_id": None,
        }
    )
    aggregate["graph"] = {"status": "FAILED", "status_version": 4}
    aggregate["budget"] = {"status": "RELEASED", "status_version": 2}
    store = _Store(aggregate)
    service = _service(store)

    challenge = service.build_operator_challenge(**_challenge_request())

    assert challenge["action"] == "NONE"
    assert challenge["target_graph_status"] is None
    assert challenge["target_budget_status"] is None
    assert challenge["graph_outbox_id"] is None
    assert challenge["budget_outbox_id"] is None
    result = service.reconcile_terminal_state(
        case_id=CASE_ID,
        expected_case_snapshot_sha256=CASE_DIGEST,
        expected_subject_snapshot_sha256=SUBJECT_DIGEST,
        action="NONE",
        target_graph_status=None,
        target_budget_status=None,
        reconciler_actor_id="operator-terminal-reconciliation",
        issued_ts_ms=2_000_000_000_000,
        attestation_ref=ATTESTATION_REF,
        attestation_key_id="terminal-key-1",
        attestation_signature=SIGNATURE,
    )

    assert result["proof"]["action"] == "NONE"
    assert result["graph"]["status_version"] == 4
    assert result["budget"]["status_version"] == 2
    assert store.record_calls[0]["graph_outbox"] is None
    assert store.record_calls[0]["budget_outbox"] is None


def test_committed_response_loss_returns_only_exact_proof_replay() -> None:
    store = _Store(_aggregate(), lose_response=True)

    result = _service(store).reconcile_terminal_state(**_signed_request())

    assert result["replayed"] is True
    assert len(store.record_calls) == 1


def test_auto_repair_consumes_only_exact_repairable_cases() -> None:
    aggregate = _aggregate(
        classification="EXACT_REPAIRABLE",
        action="RELEASE_BUDGET",
        graph_status="FAILED",
    )
    aggregate["case"]["case_kind"] = "FAILED_UNSETTLED_BUDGET"
    store = _Store(aggregate)

    result = _service(store).reconcile_exact_cases()

    assert result["reconciled_count"] == 1
    assert store.candidate_classifications == [
        "EXACT_REPAIRABLE",
        "EXACT_REPAIRABLE",
    ]
    call = store.record_calls[0]
    assert call["authority_kind"] == "SYSTEM_DURABLE_EVIDENCE"
    assert call["attestation_ref"] is None
    assert call["graph_outbox"] is None
    assert call["budget_outbox"] is not None


def test_auto_batch_reports_prior_commits_when_a_later_query_fails() -> None:
    aggregate = _aggregate(
        classification="EXACT_REPAIRABLE",
        action="RELEASE_BUDGET",
        graph_status="FAILED",
    )
    aggregate["case"]["case_kind"] = "FAILED_UNSETTLED_BUDGET"

    class FailingSecondQueryStore(_Store):
        def list_terminal_reconciliation_candidates(
            self,
            graph_id: str | None = None,
            graph_revision: int | None = None,
            *,
            classification: str | None = None,
            limit: int = 100,
        ) -> tuple[dict[str, Any], ...]:
            if len(self.candidate_classifications) == 1:
                raise RuntimeError("later candidate query unavailable")
            return super().list_terminal_reconciliation_candidates(
                graph_id,
                graph_revision,
                classification=classification,
                limit=limit,
            )

    store = FailingSecondQueryStore(aggregate)

    with pytest.raises(TerminalStateReconciliationError) as failed:
        _service(store).reconcile_exact_cases()

    assert failed.value.side_effects_may_have_run is True
    assert "1 committed case" in failed.value.detail
    assert len(store.record_calls) == 1


def test_quarantined_case_cannot_be_repaired_even_with_signed_shape() -> None:
    aggregate = _aggregate(classification="QUARANTINED_STRUCTURAL")
    store = _Store(aggregate)

    with pytest.raises(TerminalStateReconciliationError) as rejected:
        _service(store).reconcile_terminal_state(**_signed_request())

    assert rejected.value.reason_code == TERMINAL_RECONCILIATION_REJECTED
    assert rejected.value.side_effects_may_have_run is False
    assert store.record_calls == []


def test_target_matrix_drift_is_rejected_before_store_mutation() -> None:
    request = _signed_request()
    request["target_budget_status"] = None
    store = _Store(_aggregate())

    with pytest.raises(TerminalStateReconciliationError) as rejected:
        _service(store).reconcile_terminal_state(**request)

    assert rejected.value.side_effects_may_have_run is False
    assert store.record_calls == []
