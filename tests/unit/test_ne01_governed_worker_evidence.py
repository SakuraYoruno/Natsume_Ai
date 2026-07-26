from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from host_brain.app.governed_host_contracts import _ACCEPTANCE_CRITERION
from host_brain.app.governed_worker_evidence import (
    DurableWorkerCommitAcceptance,
    DurableWorkerCommitVerifier,
    GOVERNED_ACCEPTANCE_CRITERION_UNPROVEN,
    GOVERNED_WORKER_AUTHORITY_DRIFT,
    GOVERNED_WORKER_COMMIT_DRIFT,
    GOVERNED_WORKER_OUTPUT_CONTRACT_INVALID,
    GOVERNED_WORKER_PROJECTION_INVALID,
    GOVERNED_WORKER_PROJECTION_NOT_FOUND,
    GOVERNED_WORKER_VERIFICATION_RESULT_INVALID,
)


class _Reader:
    def __init__(self, projection: dict[str, object] | None) -> None:
        self.projection = projection
        self.loads: list[str] = []

    def load(self, receipt_ref: str):  # type: ignore[no-untyped-def]
        self.loads.append(receipt_ref)
        return deepcopy(self.projection)


def _fixture(
    *,
    criteria: tuple[str, ...] = (_ACCEPTANCE_CRITERION,),
    output_contract: dict[str, object] | None = None,
):
    command = SimpleNamespace(
        command_id="host-command://internal/request-worker-evidence",
        request_id="request-worker-evidence",
        root_work_order_id="work-order-worker-evidence",
        graph_id="graph-worker-evidence",
        graph_revision=3,
        target_id="pentest_agent",
        created_at_ms=1_700_000_000_000,
    )
    work_order = SimpleNamespace(
        work_order_id="work-order-worker-evidence",
        root_work_order_id="work-order-worker-evidence",
        task_graph_id="graph-worker-evidence",
        graph_revision=3,
        verification_plan={
            "required": True,
            "policy": "durable_worker_projection",
        },
    )
    context = {
        "work_order_id": "work-order-worker-evidence",
        "graph_id": "graph-worker-evidence",
        "graph_revision": 3,
        "node_id": "node-delegate",
        "attempt_id": "attempt-delegate-1",
        "worker_route": "delegate:pentest_agent",
        "target_id": "pentest_agent",
        "node_grant_ref": "grant://node/delegate-1",
        "node_output_contract": output_contract
        or {
            "type": "delegate_result",
            "required": ["receipt_ref", "narrative"],
        },
        "node_acceptance_criteria": list(criteria),
    }
    body = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "narrative": "已生成受治理的 Pentest 交接结果",
        "route_type": "delegate",
        "target_id": "pentest_agent",
        "graph_id": context["graph_id"],
        "graph_revision": context["graph_revision"],
        "node_id": context["node_id"],
        "attempt_id": context["attempt_id"],
        "node_grant_ref": context["node_grant_ref"],
        "audit_refs": {
            "node_grant_ref": context["node_grant_ref"],
            "delivery_authorized": False,
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    receipt_ref = (
        "worker-projection://graph-worker-evidence/3/"
        f"node-delegate/attempt-delegate-1/{digest}"
    )
    projection = {
        **body,
        "projection_id": receipt_ref,
        "payload_digest": f"sha256:{digest}",
        "schema_version": "Natsume.task_runtime.worker_projection.v1",
    }
    commit = {
        **body,
        "receipt_ref": receipt_ref,
        "audit_refs": {
            **body["audit_refs"],
            "path": "governed_task_runtime:frozen_node",
            "worker_projection_ref": receipt_ref,
        },
    }
    evidence_refs = (
        receipt_ref,
        f"sha256://execution/{digest}",
    )
    return command, work_order, context, projection, commit, evidence_refs


def test_verification_and_structural_acceptance_reopen_independently() -> None:
    command, order, context, projection, commit, refs = _fixture()
    reader = _Reader(projection)
    verifier = DurableWorkerCommitVerifier(projection_reader=reader)
    acceptance = DurableWorkerCommitAcceptance(projection_reader=reader)

    verification = verifier.verify(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=refs,
    )
    judgement = acceptance.judge(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        verification_result=verification,
        evidence_refs=verification["evidence_refs"],
    )

    assert verification["status"] == "PASS"
    assert [item["status"] for item in verification["checks"]] == [
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        "PASS",
    ]
    assert judgement["decision"] == "ACCEPT"
    assert judgement["criteria"][0]["verdict"] == "PASS"
    assert reader.loads == [commit["receipt_ref"], commit["receipt_ref"]]


def test_natural_language_criterion_requires_durable_review() -> None:
    command, order, context, projection, commit, refs = _fixture(
        criteria=("结果必须精确覆盖用户未结构化目标",),
    )
    reader = _Reader(projection)
    verification = DurableWorkerCommitVerifier(
        projection_reader=reader
    ).verify(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=refs,
    )

    judgement = DurableWorkerCommitAcceptance(
        projection_reader=reader
    ).judge(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        verification_result=verification,
        evidence_refs=verification["evidence_refs"],
    )

    assert verification["status"] == "PASS"
    assert judgement["decision"] == "REVIEW"
    assert (
        judgement["reason_code"]
        == GOVERNED_ACCEPTANCE_CRITERION_UNPROVEN
    )
    assert judgement["criteria"][0]["verdict"] == "UNKNOWN"
    assert judgement["criteria"][0]["deterministic"] is False


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    (
        (
            lambda projection, commit, context: projection.update(
                {"payload_digest": "sha256:" + "0" * 64}
            ),
            GOVERNED_WORKER_PROJECTION_INVALID,
        ),
        (
            lambda projection, commit, context: commit.update(
                {"narrative": "与持久投影不同"}
            ),
            GOVERNED_WORKER_COMMIT_DRIFT,
        ),
        (
            lambda projection, commit, context: context.update(
                {"node_grant_ref": "grant://node/drifted"}
            ),
            GOVERNED_WORKER_AUTHORITY_DRIFT,
        ),
    ),
)
def test_verifier_rejects_projection_commit_or_authority_drift(
    mutate,  # type: ignore[no-untyped-def]
    reason_code: str,
) -> None:
    command, order, context, projection, commit, refs = _fixture()
    mutate(projection, commit, context)

    result = DurableWorkerCommitVerifier(
        projection_reader=_Reader(projection)
    ).verify(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=refs,
    )

    assert result["status"] == "FAIL"
    assert result["reason_code"] == reason_code


def test_verifier_rejects_missing_projection() -> None:
    command, order, context, _, commit, refs = _fixture()

    result = DurableWorkerCommitVerifier(
        projection_reader=_Reader(None)
    ).verify(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=refs,
    )

    assert result["status"] == "FAIL"
    assert result["reason_code"] == GOVERNED_WORKER_PROJECTION_NOT_FOUND


def test_verifier_checks_task_node_output_contract_before_acceptance() -> None:
    command, order, context, projection, commit, refs = _fixture(
        output_contract={
            "type": "delegate_result",
            "required": ["receipt_ref", "nonexistent_result_field"],
        },
    )

    result = DurableWorkerCommitVerifier(
        projection_reader=_Reader(projection)
    ).verify(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=refs,
    )

    assert result["status"] == "FAIL"
    assert result["reason_code"] == GOVERNED_WORKER_OUTPUT_CONTRACT_INVALID


def test_acceptance_rejects_identity_drifted_verification_result() -> None:
    command, order, context, projection, commit, refs = _fixture()
    reader = _Reader(projection)
    verification = DurableWorkerCommitVerifier(
        projection_reader=reader
    ).verify(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=refs,
    )
    verification["attempt_id"] = "attempt-drifted"

    judgement = DurableWorkerCommitAcceptance(
        projection_reader=reader
    ).judge(
        command=command,
        work_order=order,
        execution_context=context,
        worker_commit=commit,
        verification_result=verification,
        evidence_refs=refs,
    )

    assert judgement["decision"] == "REVIEW"
    assert (
        judgement["reason_code"]
        == GOVERNED_WORKER_VERIFICATION_RESULT_INVALID
    )
    assert reader.loads == [commit["receipt_ref"]]
