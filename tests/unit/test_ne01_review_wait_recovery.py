from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from host_brain.app.observability_runtime.command_composition import (
    ObsCommandComposition,
)
from host_brain.app.observability_runtime.review_wait_recovery import (
    CanonicalReviewWaitRecoveryResolver,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.observability.command_outcome_journal import (
    ObsCommandOutcomeJournal,
    ObsCommandOutcomeKey,
    ObsCommandOutcomeState,
    SideEffectCertainty,
)
from host_brain.secure_ingress import SecureIngressMode
from natsume_contracts.task import HostCommandSurface, build_host_command


BODY_SHA256 = "a" * 64
GRAPH_ID = "graph-review-recovery"
GRAPH_REVISION = 1
NODE_ID = "node-review-recovery"
WORK_ORDER_ID = "work-order-review-recovery"
CLAIM_REF = "secure-ingress-claim://review-recovery"
AUDIT_REF = "secure-ingress-audit://review-recovery"


class _CommandRepository:
    def __init__(self, record: Mapping[str, Any] | None) -> None:
        self.record = record

    def get_by_ingress_claim(self, claim_ref: str) -> Mapping[str, Any] | None:
        assert claim_ref == CLAIM_REF
        return self.record


class _TaskStore:
    def __init__(self, node: Mapping[str, Any]) -> None:
        self.node = dict(node)

    def get_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> Mapping[str, Any]:
        assert (graph_id, graph_revision, node_id) == (
            GRAPH_ID,
            GRAPH_REVISION,
            NODE_ID,
        )
        return dict(self.node)


class _ReviewStore:
    def __init__(self, records: list[Mapping[str, Any]]) -> None:
        self.records = [dict(record) for record in records]

    def list_reviews(self, **kwargs: Any) -> list[Mapping[str, Any]]:
        assert kwargs == {
            "task_graph_id": GRAPH_ID,
            "graph_revision": GRAPH_REVISION,
            "work_order_id": WORK_ORDER_ID,
            "limit": 10,
        }
        return list(self.records)


class _ContinuationStore:
    def __init__(self, record: Mapping[str, Any] | None) -> None:
        self.record = record
        self.reads: list[str] = []

    def get_review_continuation(
        self,
        review_id: str,
    ) -> Mapping[str, Any] | None:
        self.reads.append(review_id)
        return self.record


class _SecureIngressAuthority:
    def resolve_committed_authority_by_audit(
        self,
        *,
        audit_ref: str,
    ) -> Mapping[str, Any]:
        assert audit_ref == AUDIT_REF
        return {
            "claim_ref": CLAIM_REF,
            "audit_ref": AUDIT_REF,
            "surface": "obs.command",
            "action": "obs:command:sys_mode.switch",
            "required_scopes": ["obs:sys-mode:write", "obs:write"],
            "body_sha256": BODY_SHA256,
            "claim_status": "COMMITTED",
            "decision": "ALLOW",
            "side_effects_executed": False,
            "claim_result_ref": None,
        }


class _Ingress:
    def __init__(self) -> None:
        self.open_count = 0

    def open_admission_after_recovery(self) -> None:
        self.open_count += 1


def _command_record() -> Mapping[str, Any]:
    command = build_host_command(
        surface=HostCommandSurface.OPERATOR,
        request_id="request-review-recovery",
        session_id="session-review-recovery",
        principal_id="principal-review-recovery",
        ingress_claim_ref=CLAIM_REF,
        ingress_audit_ref=AUDIT_REF,
        governor_grant_ref="governor-decision://review-recovery",
        input_ref="obs-command://review-recovery/input",
        input_sha256=f"sha256:{BODY_SHA256}",
        root_work_order_id=WORK_ORDER_ID,
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        route_type="operator",
        target_id="sys_mode.switch",
        business_scope="operator-runtime",
        operation_fingerprint="sha256:" + "b" * 64,
        created_at_ms=1_000,
    )
    work_order = WorkOrderV3(
        work_order_id=WORK_ORDER_ID,
        domain="operator",
        run_mode="write",
        objective="recover one exact Operator Review",
        metadata={"operator_command_id": "sys_mode.switch"},
        root_work_order_id=WORK_ORDER_ID,
        task_graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
    )
    return {"command": command, "work_order": work_order}


def _node(*, status: str, status_version: int) -> dict[str, Any]:
    return {
        "graph_id": GRAPH_ID,
        "graph_revision": GRAPH_REVISION,
        "node_id": NODE_ID,
        "status": status,
        "status_version": status_version,
        "created_ts_ms": 1_000,
        "updated_ts_ms": 1_000,
        "node": {"node_id": NODE_ID},
    }


def _review_record(
    status: str,
    *,
    node: Mapping[str, Any],
    review_id: str = "review-recovery",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    status_version = 0 if status == "PENDING" else 1
    if status in {"CONSUMED", "REJECTED", "EXPIRED", "CANCELLED"}:
        status_version = 2
    review = {
        "review_id": review_id,
        "status": status,
        "subject_ref": (
            f"task-node://{GRAPH_ID}/{GRAPH_REVISION}/{NODE_ID}"
            f"?root={WORK_ORDER_ID}&attempt=not-started"
        ),
        "subject_version": 3,
        "checkpoint_id": "task-checkpoint://review-recovery/pre-execution",
        "resume_phase": "PRE_EXECUTION",
    }
    durable = {
        "review": review,
        "status_version": status_version,
        "work_order_id": WORK_ORDER_ID,
        "task_graph_id": GRAPH_ID,
        "task_node_id": NODE_ID,
    }
    if status not in {"CONSUMED", "REJECTED", "EXPIRED", "CANCELLED"}:
        return durable, None
    continuation = {
        "review_id": review_id,
        "graph_id": GRAPH_ID,
        "graph_revision": GRAPH_REVISION,
        "node_id": NODE_ID,
        "subject_version": 3,
        "resume_phase": "PRE_EXECUTION",
        "review_status_version": status_version,
        "review": dict(review),
        "node": {
            key: node[key]
            for key in (
                "graph_id",
                "graph_revision",
                "node_id",
                "status",
                "status_version",
            )
        },
        "checkpoint_crossed": status == "CANCELLED",
    }
    return durable, continuation


def _outcome_journal(path: Path) -> ObsCommandOutcomeJournal:
    journal = ObsCommandOutcomeJournal(path, clock_ms=lambda: 1_000)
    key = ObsCommandOutcomeKey.from_values(
        principal_id="principal-review-recovery",
        tenant_id="tenant-review-recovery",
        idempotency_key="idempotency-review-recovery",
        command_id="sys_mode.switch",
        action="obs:command:sys_mode.switch",
        required_scopes=("obs:write", "obs:sys-mode:write"),
        body_sha256=BODY_SHA256,
    )
    journal.begin(key, claim_ref=CLAIM_REF, audit_ref=AUDIT_REF)
    return ObsCommandOutcomeJournal(path, clock_ms=lambda: 2_000)


def _recover(
    path: Path,
    *,
    reviews: list[Mapping[str, Any]],
    node: Mapping[str, Any],
    continuation: Mapping[str, Any] | None,
) -> tuple[tuple[Any, ...], _Ingress, _ContinuationStore, ObsCommandOutcomeJournal]:
    journal = _outcome_journal(path)
    ingress = _Ingress()
    authority = _SecureIngressAuthority()
    continuations = _ContinuationStore(continuation)
    composition = ObsCommandComposition(
        enabled=True,
        execution_enabled=True,
        effective_mode=SecureIngressMode.ENFORCE,
        global_mode=SecureIngressMode.ENFORCE,
        reason_code="OBS_COMMAND_COMPOSITION_ENFORCE",
        ingress=ingress,
        registry=SimpleNamespace(),
        outcome_journal=journal,
        store=authority,  # type: ignore[arg-type]
        _review_recovery_deferred=True,
    )
    recovered = composition.recover_deferred_review_admissions(
        command_repository=_CommandRepository(_command_record()),
        task_store=_TaskStore(node),
        review_store=_ReviewStore(reviews),
        review_continuation_store=continuations,
    )
    return recovered, ingress, continuations, journal


@pytest.mark.parametrize(
    "review_status",
    ["PENDING", "APPROVED", "CONSUMED", "REJECTED", "EXPIRED", "CANCELLED"],
)
def test_restart_recovers_one_exact_review_without_pseudo_terminal_success(
    tmp_path: Path,
    review_status: str,
) -> None:
    node = _node(
        status="CANCELLED" if review_status == "CANCELLED" else "WAITING_APPROVAL",
        status_version=4 if review_status == "CANCELLED" else 3,
    )
    durable, continuation = _review_record(review_status, node=node)

    recovered, ingress, continuations, journal = _recover(
        tmp_path / f"{review_status.lower()}.jsonl",
        reviews=[durable],
        node=node,
        continuation=continuation,
    )

    assert len(recovered) == 1
    assert recovered[0].state is ObsCommandOutcomeState.WAITING_REVIEW
    assert recovered[0].side_effect_certainty is SideEffectCertainty.NOT_EXECUTED
    assert recovered[0].terminal_result_ref is None
    assert ingress.open_count == 1
    assert continuations.reads == (
        ["review-recovery"]
        if review_status in {"CONSUMED", "REJECTED", "EXPIRED", "CANCELLED"}
        else []
    )
    assert journal.recover_incomplete() == ()


def test_restart_with_no_review_records_uncertain_and_opens_admission(
    tmp_path: Path,
) -> None:
    node = _node(status="READY", status_version=0)
    recovered, ingress, continuations, journal = _recover(
        tmp_path / "no-review.jsonl",
        reviews=[],
        node=node,
        continuation=None,
    )

    assert len(recovered) == 1
    assert recovered[0].state is ObsCommandOutcomeState.UNCERTAIN
    assert recovered[0].side_effect_certainty is SideEffectCertainty.MAY_HAVE_EXECUTED
    assert ingress.open_count == 1
    assert continuations.reads == []
    assert journal.lookup_by_claim_ref(CLAIM_REF).state is ObsCommandOutcomeState.UNCERTAIN


def test_restart_with_multiple_review_identities_fails_closed(
    tmp_path: Path,
) -> None:
    node = _node(status="WAITING_APPROVAL", status_version=3)
    first, _ = _review_record("PENDING", node=node, review_id="review-a")
    second, _ = _review_record("PENDING", node=node, review_id="review-b")

    with pytest.raises(
        RuntimeError,
        match="OBS_COMMAND_REVIEW_RECOVERY_AUTHORITY_NOT_UNIQUE",
    ):
        _recover(
            tmp_path / "multiple.jsonl",
            reviews=[first, second],
            node=node,
            continuation=None,
        )


@pytest.mark.parametrize("drift", ["subject", "continuation", "missing-continuation"])
def test_restart_binding_drift_fails_closed(
    tmp_path: Path,
    drift: str,
) -> None:
    node = _node(status="WAITING_APPROVAL", status_version=3)
    durable, continuation = _review_record("CONSUMED", node=node)
    if drift == "subject":
        durable["review"] = {
            **durable["review"],
            "subject_ref": (
                f"task-node://{GRAPH_ID}/{GRAPH_REVISION}/different-node"
                f"?root={WORK_ORDER_ID}&attempt=not-started"
            ),
        }
    elif drift == "continuation":
        assert continuation is not None
        continuation = {**continuation, "graph_revision": GRAPH_REVISION + 1}
    else:
        continuation = None

    with pytest.raises(RuntimeError, match="OBS_COMMAND_REVIEW_RECOVERY_.*DRIFT"):
        _recover(
            tmp_path / f"drift-{drift}.jsonl",
            reviews=[durable],
            node=node,
            continuation=continuation,
        )
