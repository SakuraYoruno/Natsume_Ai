from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any, Mapping

import pytest

from host_brain.composition.runtime_group import (
    GovernedRuntimeGroup,
    _continuation_result_ref,
)
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.persistence.host_command_repository import (
    HOST_COMMAND_INGRESS_CONFLICT,
    HostCommandRepository,
    HostCommandRepositoryError,
)
from host_brain.persistence.review_continuation_store import (
    ReviewContinuationStore,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
    SecureIngressStoreError,
)
from natsume_contracts.task import HostCommandSurface
from tests.contract.test_aw03_host_command_repository import (
    NOW,
    _admission,
    _migrated_db,
)


@dataclass
class _Clock:
    value: int = NOW

    def __call__(self) -> int:
        return self.value


class _DeniedRuntime:
    def __init__(self) -> None:
        self.claims: list[dict[str, Any]] = []

    async def activate(self) -> None:
        return None

    async def close_admission(self) -> None:
        return None

    async def drain(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def continue_review_admission(
        self,
        record: Mapping[str, Any],
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        assert record["command"].graph_id == claim["graph_id"]
        assert record["command"].graph_revision == claim["graph_revision"]
        self.claims.append(dict(claim))
        return {
            "execution_receipt": {
                "status": "REVIEW_DENIED",
                "accepted": False,
                # A POST checkpoint can retain the pre-approval worker
                # receipt.  Review denial must nevertheless complete secure
                # ingress with the durable Review decision, never this stale
                # execution receipt.
                "receipt_ref": "agent-result://pre-approval-worker-receipt",
            }
        }


class _NoopContinuationWorker:
    def __init__(self, **_: Any) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True

    def wake(self) -> None:
        return None

    async def stop(self) -> None:
        self.started = False

    async def join(self) -> None:
        return None


def _insert_secure_authority(
    db_path: Path,
    *,
    surface: HostCommandSurface,
    request_id: str,
    principal_id: str,
    body_sha256: str,
    claim_ref: str,
    audit_ref: str,
) -> None:
    ingress_surface = {
        HostCommandSurface.INTERNAL: "internal.agent",
        HostCommandSurface.SCHEDULER: "scheduler.admin",
    }[surface]
    action = {
        HostCommandSurface.INTERNAL: "internal:agent:execute",
        HostCommandSurface.SCHEDULER: "scheduler:admin",
    }[surface]
    body_digest = body_sha256.removeprefix("sha256:")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_idempotency_claim(
                principal_id, surface, idempotency_digest, action,
                required_scopes_json, request_id, body_sha256, claim_ref,
                result_ref, claim_status, provisional_expires_at_ms,
                claimed_at_ms, updated_at_ms, expires_at_ms
            ) VALUES (?, ?, ?, ?, '[]', ?, ?, ?, NULL,
                      'COMMITTED', NULL, ?, ?, ?)
            """,
            (
                principal_id,
                ingress_surface,
                "sha256:" + "c" * 64,
                action,
                request_id,
                body_digest,
                claim_ref,
                NOW,
                NOW,
                NOW + 120_000,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_audit(
                audit_ref, request_id, correlation_id, surface, action, method,
                normalized_path, principal_id, tenant_id, key_id,
                credential_kind, credential_ref_digest, requested_scopes_json,
                required_scopes_json, body_sha256, idempotency_digest,
                nonce_digest, transport_key_digest, decision, reason_code,
                stage, claim_ref, rate_snapshot_json, side_effects_executed, ts_ms
            ) VALUES (?, ?, NULL, ?, ?, 'POST', '/test/review-continuation',
                      ?, 'tenant-ne01', 'key-ne01', 'HMAC_SHA256', ?,
                      '[]', '[]', ?, ?, ?, ?, 'ALLOW',
                      'SECURE_INGRESS_ALLOWED', 'COMPLETE', ?, '{}', 0, ?)
            """,
            (
                audit_ref,
                request_id,
                ingress_surface,
                action,
                principal_id,
                "sha256:" + "d" * 64,
                body_digest,
                "sha256:" + "c" * 64,
                "sha256:" + "e" * 64,
                "sha256:" + "f" * 64,
                claim_ref,
                NOW,
            ),
        )


def _waiting_review(
    db_path: Path,
    *,
    review_id: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    root_work_order_id: str,
    expires_at: int,
    phase: ReviewResumePhase,
) -> ReviewItem:
    post = phase is ReviewResumePhase.POST_EXECUTION_ACCEPTANCE
    attempt_id = f"attempt-{review_id}" if post else "not-started"
    waiting_status = "WAITING_ACCEPTANCE_REVIEW" if post else "WAITING_APPROVAL"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = ?, status_version = 1,
                updated_ts_ms = ?
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            """,
            (waiting_status, NOW, graph_id, graph_revision, node_id),
        )
        if post:
            conn.execute(
                """
                INSERT INTO task_runtime_attempt(
                    attempt_id, graph_id, graph_revision, node_id, attempt_no,
                    status, status_version, owner_id, operation_scope,
                    operation_fingerprint, created_ts_ms, acquired_ts_ms,
                    started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
                ) VALUES (?, ?, ?, ?, 1, 'SUCCEEDED', 3, 'worker-review-test',
                          'scope-review-test', 'fingerprint-review-test',
                          ?, ?, ?, ?, ?, NULL)
                """,
                (
                    attempt_id,
                    graph_id,
                    graph_revision,
                    node_id,
                    NOW,
                    NOW,
                    NOW,
                    NOW,
                    NOW,
                ),
            )
    return ReviewItem(
        review_id=review_id,
        review_type=(
            ReviewType.POST_EXECUTION_ACCEPTANCE
            if post
            else ReviewType.PRE_EXECUTION_APPROVAL
        ),
        subject_ref=(
            f"task-node://{graph_id}/{graph_revision}/{node_id}"
            f"?root={root_work_order_id}&attempt={attempt_id}"
        ),
        subject_version=1,
        requester="review-denied-ingress-test",
        reason="verify durable denied ingress replay",
        risk_level=3,
        side_effects=("execute",),
        evidence_refs=("evidence://ne01/review-denied-ingress",),
        status=ReviewStatus.PENDING,
        expires_at=expires_at,
        resume_phase=phase,
        checkpoint_id=(
            f"task-checkpoint://{graph_id}/{graph_revision}/{node_id}/"
            f"{phase.value.lower()}?root={root_work_order_id}&attempt={attempt_id}"
        ),
    )


async def _complete_denied_review(
    tmp_path: Path,
    *,
    surface: HostCommandSurface,
    decision: str,
    phase: ReviewResumePhase = ReviewResumePhase.PRE_EXECUTION,
) -> dict[str, Any]:
    db_path = _migrated_db(tmp_path)
    suffix = f"{surface.value}-{decision.lower()}"
    claim_ref = f"secure-ingress-claim://ne01/{suffix}"
    audit_ref = f"secure-ingress-audit://ne01/{suffix}"
    command, work_order, assignment, graph = _admission(
        surface=surface,
        request_id=f"request-ne01-{suffix}",
        ingress_claim_ref=claim_ref,
        ingress_audit_ref=audit_ref,
    )
    _insert_secure_authority(
        db_path,
        surface=surface,
        request_id=command.request_id,
        principal_id=command.principal_id,
        body_sha256=command.input_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    commands = HostCommandRepository(db_path, clock=lambda: NOW)
    admitted = commands.admit(command, work_order, assignment, graph)
    assert admitted["created"] is True

    clock = _Clock()
    reviews = ReviewStore(db_path, clock=clock)
    review = _waiting_review(
        db_path,
        review_id=f"review-ne01-{suffix}",
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        node_id=graph.nodes[0].node_id,
        root_work_order_id=graph.root_work_order_id,
        expires_at=NOW + 10,
        phase=phase,
    )
    reviews.create_review(
        review,
        work_order_id=work_order.work_order_id,
        task_graph_id=graph.graph_id,
        task_node_id=graph.nodes[0].node_id,
    )
    if decision == "REJECTED":
        terminal = reviews.reject_review(review.review_id, actor_id="reviewer-ne01")
    else:
        clock.value = NOW + 10
        expired = reviews.expire_due()
        assert expired["expired_review_ids"] == (review.review_id,)
        terminal = reviews.get_review(review.review_id)
    assert terminal["review"]["status"] == decision

    prior_result_ref = "agent-result://pre-approval-worker-receipt"
    if phase is ReviewResumePhase.POST_EXECUTION_ACCEPTANCE:
        # Simulate a database upgraded from a pre-0096 release that completed
        # secure ingress with the worker receipt before POST Review decided.
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                UPDATE secure_ingress_idempotency_claim
                SET result_ref = ?
                WHERE claim_ref = ?
                """,
                (prior_result_ref, claim_ref),
            )

    continuations = ReviewContinuationStore(db_path, clock=clock)
    raw_claim = continuations.claim_pending_review_continuations(
        worker_id=f"review-worker-{suffix}",
        lease_ms=60_000,
        limit=1,
    )[0]
    authority = continuations.validate_review_continuation_claim(raw_claim)
    runtime = _DeniedRuntime()
    ingress_results = SQLiteSecureIngressStore(db_path)
    runtime_name = {
        HostCommandSurface.INTERNAL: "internal_agent",
        HostCommandSurface.SCHEDULER: "scheduler",
    }[surface]
    group = GovernedRuntimeGroup(
        {runtime_name: runtime},
        review_store=continuations,
        review_decision_store=reviews,
        command_repository=commands,
        claim_result_store=ingress_results,
        review_continuation_worker_factory=_NoopContinuationWorker,
        review_continuation_worker_id=f"review-group-{suffix}",
    )
    try:
        await group.activate()
        result = await group.continue_review_continuation(authority)
    finally:
        await group.aclose()
        ingress_results.close()

    assert result["execution_receipt"] == {
        "status": "REVIEW_DENIED",
        "accepted": False,
        "receipt_ref": "agent-result://pre-approval-worker-receipt",
    }
    assert runtime.claims == [dict(authority)]
    expected_ref = (
        f"review-decision://{review.review_id}/{decision.lower()}/"
        f"{authority['review_status_version']}"
    )
    with sqlite3.connect(str(db_path)) as conn:
        result_ref = conn.execute(
            """
            SELECT result_ref FROM secure_ingress_idempotency_claim
            WHERE claim_ref = ?
            """,
            (claim_ref,),
        ).fetchone()[0]
    assert result_ref == expected_ref
    return {
        "db_path": db_path,
        "commands": commands,
        "contracts": (command, work_order, assignment, graph),
        "review_id": review.review_id,
        "review_status": decision,
        "review_status_version": authority["review_status_version"],
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": graph.nodes[0].node_id,
        "prior_result_ref": prior_result_ref,
        "claim_ref": claim_ref,
        "result_ref": expected_ref,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "decision", "phase"),
    [
        (surface, decision, phase)
        for surface in (
            HostCommandSurface.INTERNAL,
            HostCommandSurface.SCHEDULER,
        )
        for decision in ("REJECTED", "EXPIRED")
        for phase in (
            ReviewResumePhase.PRE_EXECUTION,
            ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        )
    ],
)
async def test_review_denial_completes_durable_ingress_and_exact_replay(
    tmp_path: Path,
    surface: HostCommandSurface,
    decision: str,
    phase: ReviewResumePhase,
) -> None:
    state = await _complete_denied_review(
        tmp_path,
        surface=surface,
        decision=decision,
        phase=phase,
    )

    replay = state["commands"].admit(*state["contracts"])

    assert replay["created"] is False
    assert replay["replayed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "surface",
    [HostCommandSurface.INTERNAL, HostCommandSurface.SCHEDULER],
)
@pytest.mark.parametrize("tamper", ["status", "version", "graph", "ref"])
async def test_review_denial_replay_rejects_any_decision_binding_tamper(
    tmp_path: Path,
    surface: HostCommandSurface,
    tamper: str,
) -> None:
    state = await _complete_denied_review(
        tmp_path,
        surface=surface,
        decision="REJECTED",
    )
    with sqlite3.connect(str(state["db_path"])) as conn:
        if tamper == "status":
            conn.execute(
                "UPDATE agent_runtime_review SET status = 'EXPIRED' WHERE review_id = ?",
                (state["review_id"],),
            )
        elif tamper == "version":
            conn.execute(
                """
                UPDATE agent_runtime_review
                SET status_version = status_version + 1
                WHERE review_id = ?
                """,
                (state["review_id"],),
            )
        elif tamper == "graph":
            conn.execute(
                """
                UPDATE agent_runtime_review
                SET task_graph_id = 'forged-review-graph'
                WHERE review_id = ?
                """,
                (state["review_id"],),
            )
        else:
            conn.execute(
                """
                UPDATE secure_ingress_idempotency_claim
                SET result_ref = 'review-decision://forged/rejected/1'
                WHERE claim_ref = ?
                """,
                (state["claim_ref"],),
            )

    with pytest.raises(HostCommandRepositoryError) as conflict:
        state["commands"].admit(*state["contracts"])

    assert conflict.value.reason_code == HOST_COMMAND_INGRESS_CONFLICT


@pytest.mark.parametrize(
    ("surface", "phase"),
    [
        (surface, phase)
        for surface in (
            HostCommandSurface.INTERNAL,
            HostCommandSurface.SCHEDULER,
        )
        for phase in (
            ReviewResumePhase.PRE_EXECUTION,
            ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        )
    ],
)
def test_later_review_denial_cannot_be_bound_to_consumed_continuation_claim(
    surface: HostCommandSurface,
    phase: ReviewResumePhase,
) -> None:
    command = _admission(surface=surface)[0]
    result = {
        "execution_receipt": {
            "status": "REVIEW_DENIED",
            "accepted": False,
            "projection": {
                "review": {
                    "review_id": "review-B-later-denial",
                    "status": "REJECTED",
                }
            },
        }
    }
    consumed_claim = {
        "review_id": "review-A-consumed",
        "review_status": "CONSUMED",
        "review_status_version": 2,
        "resume_phase": phase.value,
        "review": {
            "review": {
                "review_id": "review-A-consumed",
                "status": "CONSUMED",
                "resume_phase": phase.value,
            }
        },
    }

    assert (
        _continuation_result_ref(
            result,
            claim=consumed_claim,
            command=command,
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "decision"),
    [
        (surface, decision)
        for surface in (
            HostCommandSurface.INTERNAL,
            HostCommandSurface.SCHEDULER,
        )
        for decision in ("REJECTED", "EXPIRED")
    ],
)
async def test_post_review_upgrade_replaces_only_exact_historical_receipt(
    tmp_path: Path,
    surface: HostCommandSurface,
    decision: str,
) -> None:
    state = await _complete_denied_review(
        tmp_path,
        surface=surface,
        decision=decision,
        phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
    )
    with sqlite3.connect(str(state["db_path"])) as conn:
        conn.execute(
            """
            UPDATE secure_ingress_idempotency_claim
            SET result_ref = ?
            WHERE claim_ref = ?
            """,
            (state["prior_result_ref"], state["claim_ref"]),
        )

    exact = {
        "claim_ref": state["claim_ref"],
        "result_ref": state["result_ref"],
        "prior_result_ref": state["prior_result_ref"],
        "review_id": state["review_id"],
        "review_status": state["review_status"],
        "review_status_version": state["review_status_version"],
        "graph_id": state["graph_id"],
        "graph_revision": state["graph_revision"],
        "node_id": state["node_id"],
        "at_ms": NOW + 100,
    }
    tampered: list[tuple[str, Any]] = [
        ("result_ref", "review-decision://forged/rejected/1"),
        ("prior_result_ref", "agent-result://different-prior-receipt"),
        ("review_id", "review-forged"),
        (
            "review_status",
            "EXPIRED" if state["review_status"] == "REJECTED" else "REJECTED",
        ),
        ("review_status_version", int(state["review_status_version"]) + 1),
        ("graph_id", str(state["graph_id"]) + "-forged"),
        ("graph_revision", int(state["graph_revision"]) + 1),
        ("node_id", str(state["node_id"]) + "-forged"),
    ]
    store = SQLiteSecureIngressStore(state["db_path"])
    try:
        for field, forged in tampered:
            candidate = {**exact, field: forged}
            with pytest.raises(SecureIngressStoreError):
                store.complete_review_decision_claim(**candidate)
            with sqlite3.connect(str(state["db_path"])) as conn:
                current = conn.execute(
                    """
                    SELECT result_ref FROM secure_ingress_idempotency_claim
                    WHERE claim_ref = ?
                    """,
                    (state["claim_ref"],),
                ).fetchone()[0]
            assert current == state["prior_result_ref"]

        store.complete_review_decision_claim(**exact)
    finally:
        store.close()

    with sqlite3.connect(str(state["db_path"])) as conn:
        upgraded = conn.execute(
            """
            SELECT result_ref FROM secure_ingress_idempotency_claim
            WHERE claim_ref = ?
            """,
            (state["claim_ref"],),
        ).fetchone()[0]
    assert upgraded == state["result_ref"]
