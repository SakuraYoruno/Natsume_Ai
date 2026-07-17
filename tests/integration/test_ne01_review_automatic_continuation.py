from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.orchestration import GovernedCommandApplication
from host_brain.orchestration.governor_grant import governor_grant_ref
from host_brain.orchestration.node_grants import (
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
    NodeGrantDerivationError,
    NodeGrantVerificationError,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.review_continuation_store import (
    REVIEW_CONTINUATION_CLAIM_LOST,
    ReviewContinuationStore,
    ReviewContinuationStoreError,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.task_repository import DurableTaskStore, TaskStoreError
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.task_runtime.review_continuation_worker import (
    ReviewContinuationWorker,
)
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from tests.integration.test_gr03a_governed_durable_dag import (
    NOW,
    _consume_review,
    _contracts,
    _coordinator,
    _migrated_db,
    _runtime_outbox,
    _Worker,
)


@dataclass(slots=True)
class _MutableClock:
    value: int = NOW

    def __call__(self) -> int:
        return self.value


class _ApplicationContinuationRuntime:
    """Thin surface boundary around the real governed application re-entry."""

    def __init__(
        self,
        application: GovernedCommandApplication,
        contracts: tuple[Any, Any, Any, Any],
    ) -> None:
        self._application = application
        self._contracts = contracts
        self.claims: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []

    async def continue_review_continuation(
        self,
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        normalized = dict(claim)
        self.claims.append(normalized)
        result = await self._application.execute_review_continuation(
            command=self._contracts[0],
            work_order=self._contracts[1],
            assignment=self._contracts[2],
            graph=self._contracts[3],
            continuation_claim=normalized,
        )
        self.results.append(dict(result))
        return result


class _NodeGrantService:
    """Issue exact test refs while retaining the production application boundary."""

    def __init__(self) -> None:
        self.continuation_claims: list[dict[str, Any]] = []

    def validate_parent_binding(self, **_kwargs: Any) -> bool:
        return True

    def derive(
        self,
        *,
        parent_grant_ref: str,
        task_graph: Any,
        **_: Any,
    ) -> SimpleNamespace:
        return self._batch(parent_grant_ref=parent_grant_ref, graph=task_graph)

    def derive_review_continuation(
        self,
        *,
        parent_grant_ref: str,
        task_graph: Any,
        continuation_claim: Mapping[str, Any],
        **_: Any,
    ) -> SimpleNamespace:
        self.continuation_claims.append(dict(continuation_claim))
        return self._batch(parent_grant_ref=parent_grant_ref, graph=task_graph)

    @staticmethod
    def _batch(*, parent_grant_ref: str, graph: Any) -> SimpleNamespace:
        return SimpleNamespace(
            parent_grant_ref=parent_grant_ref,
            task_graph_id=graph.graph_id,
            graph_revision=graph.revision,
            grant_refs={
                node.node_id: f"node-grant://review-continuation/{node.node_id}"
                for node in graph.nodes
            },
        )


class _GovernedResolver:
    def __init__(self, calls: list[str]) -> None:
        self._worker = _Worker(calls, set())

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        assert executor == "canonical"
        assert worker_route.startswith("skill:")
        return self._worker


class _GrantVerifyingWorker(_Worker):
    def __init__(
        self,
        calls: list[str],
        verifier: DurableNodeGrantVerifier,
    ) -> None:
        super().__init__(calls, set())
        self._verifier = verifier
        self.verified_grants: list[Any] = []

    async def execute_stream(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: dict[str, Any],
        **kwargs: Any,
    ):
        route, separator, target_id = str(
            execution_context["worker_route"]
        ).partition(":")
        assert separator == ":"
        self.verified_grants.append(
            self._verifier.verify(
                grant_ref=execution_context["node_grant_ref"],
                request_id=command.request_id,
                session_id=command.session_id,
                principal_id=command.principal_id,
                graph_id=execution_context["graph_id"],
                root_work_order_id=work_order.root_work_order_id,
                graph_revision=execution_context["graph_revision"],
                node_id=execution_context["node_id"],
                route=route,
                target_id=target_id,
                parent_grant_ref=execution_context["parent_grant_ref"],
                attempt_id=execution_context["attempt_id"],
                lease_id=execution_context["lease_id"],
                lease_owner_id=execution_context["lease_owner_id"],
                fencing_token=execution_context["fencing_token"],
            )
        )
        async for event in super().execute_stream(
            execution_context=execution_context,
            **kwargs,
        ):
            yield event


class _SlowGrantVerifyingWorker(_GrantVerifyingWorker):
    def __init__(
        self,
        calls: list[str],
        verifier: DurableNodeGrantVerifier,
        *,
        clock: _MutableClock | Any,
        delay_seconds: float,
    ) -> None:
        super().__init__(calls, verifier)
        self._clock = clock
        self._delay_seconds = float(delay_seconds)
        self.verification_times: list[int] = []

    async def execute_stream(self, **kwargs: Any):
        await asyncio.sleep(self._delay_seconds)
        self.verification_times.append(int(self._clock()))
        async for event in super().execute_stream(**kwargs):
            yield event


class _ExactResolver:
    def __init__(self, worker: _Worker) -> None:
        self._worker = worker

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        assert executor == "canonical"
        assert worker_route.startswith("skill:")
        return self._worker


def _with_governor_grant(
    contracts: tuple[Any, Any, Any, Any],
) -> tuple[Any, Any, Any, Any]:
    graph = replace(
        contracts[3],
        nodes=tuple(
            replace(node, route=f"skill:{node.node_id}")
            for node in contracts[3].nodes
        ),
    )
    return (
        replace(
            contracts[0],
            governor_grant_ref="governor-grant://review-continuation/root",
        ),
        contracts[1],
        contracts[2],
        graph,
    )


def _install_parent_grant(
    *,
    ledger: SQLiteLedger,
    token_service: TokenService,
    command: Any,
    expires_at_ms: int,
) -> str:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-review-parent::{command.request_id}",
        "ts_ms": NOW - 100,
        "session_id": command.session_id,
        "seq": 7,
        "person_id": command.principal_id,
        "principal_id": command.principal_id,
        "type": "query",
        "action": "exec_intent",
        "target": "host",
        "args": {"approved_plan_ref": "plan://review-continuation"},
        "nonce": f"nonce-review-parent::{command.request_id}",
    }
    token_ref = token_service.issue_approval_token(
        intent_id=intent["id"],
        skill_name="exec_intent",
        session_id=command.session_id,
        seq=7,
        now_ts_ms=NOW - 100,
    )
    binding = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref=token_ref,
        risk_level=2,
        issued_at_ms=NOW - 100,
        expires_at_ms=expires_at_ms,
    )
    decision = {
        "request_id": command.request_id,
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVE",
        "rule_trace": [],
        "tokens": {"approval_token_ref": token_ref},
        "approved_action_binding": binding,
        "snapshot_refs": {"plan": "plan://review-continuation"},
        "stage": "S_INTENT_OK",
        "ts_ms": NOW - 100,
    }
    ledger.log_governor(decision)
    return governor_grant_ref(command.request_id, decision)


def _with_real_governor_authority(
    contracts: tuple[Any, Any, Any, Any],
    *,
    parent_grant_ref: str,
) -> tuple[Any, Any, Any, Any]:
    graph = replace(
        contracts[3],
        nodes=tuple(
            replace(node, route=f"skill:{node.node_id}")
            for node in contracts[3].nodes
        ),
    )
    work_order = contracts[1]
    metadata = {
        **dict(work_order.metadata),
        "planning_lineage": {
            "graph_id": graph.graph_id,
            "root_work_order_id": graph.root_work_order_id,
            "revision": graph.revision,
            "approval_ref": parent_grant_ref,
            "review_ref": parent_grant_ref,
            "node_bindings": [
                {
                    "candidate_id": f"candidate-{node.node_id}",
                    "node_id": node.node_id,
                    "objective": f"execute {node.node_id}",
                    "worker_route": "skill",
                    "target_id": node.node_id,
                }
                for node in graph.nodes
            ],
        },
    }
    return (
        replace(contracts[0], governor_grant_ref=parent_grant_ref),
        replace(
            work_order,
            metadata=metadata,
            approval_ref=parent_grant_ref,
            review_ref=parent_grant_ref,
        ),
        contracts[2],
        graph,
    )


async def _initial_receipt(
    application: GovernedCommandApplication,
    contracts: tuple[Any, Any, Any, Any],
) -> dict[str, Any]:
    events = [
        event
        async for event in application.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    return dict(events[-1]["execution_receipt"])


def _worker(
    *,
    store: ReviewContinuationStore,
    runtime: _ApplicationContinuationRuntime,
    worker_id: str,
) -> ReviewContinuationWorker:
    return ReviewContinuationWorker(
        store=store,
        runtime=runtime,
        worker_id=worker_id,
        claim_lease_ms=30_000,
        poll_interval_ms=60_000,
        retry_delay_ms=0,
        batch_limit=1,
    )


@pytest.mark.asyncio
async def test_pre_review_worker_automatically_resumes_without_fake_attempt(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    try:
        base_contracts = _contracts((("root", ()),), review_required=True)
        tokens = TokenService(approval_ttl_ms=600_000)
        parent_expires_at = NOW + 10
        parent_ref = _install_parent_grant(
            ledger=ledger,
            token_service=tokens,
            command=base_contracts[0],
            expires_at_ms=parent_expires_at,
        )
        contracts = _with_real_governor_authority(
            base_contracts,
            parent_grant_ref=parent_ref,
        )
        calls: list[str] = []
        reviews = ReviewStore(db_path, clock=lambda: NOW)
        continuation_store = ReviewContinuationStore(db_path, clock=lambda: NOW)
        grant_clock = _MutableClock()
        node_grants = NodeBoundGrantService(
            ledger=ledger,
            token_service=tokens,
            review_store=continuation_store,
            clock=grant_clock,
            child_ttl_ms=60_000,
        )
        grant_verifier = DurableNodeGrantVerifier(
            ledger=ledger,
            review_store=continuation_store,
            clock=grant_clock,
        )
        governed_worker = _GrantVerifyingWorker(calls, grant_verifier)
        application = GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=_coordinator(
                db_path,
                calls,
                review_runtime=TaskNodeReviewRuntime(reviews, clock=lambda: NOW),
                worker_resolver=_ExactResolver(governed_worker),
                node_grant_verifier=grant_verifier,
            ),
            node_grant_service=node_grants,
        )

        waiting = await _initial_receipt(application, contracts)

        assert waiting["status"] == "WAITING_APPROVAL"
        assert waiting["attempt_id"] is None
        assert calls == []
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM task_runtime_attempt"
            ).fetchone() == (0,)

        review = waiting["projection"]["review"]
        _consume_review(reviews, review)
        grant_clock.value = parent_expires_at + 1
        runtime = _ApplicationContinuationRuntime(application, contracts)

        delivered = await _worker(
            store=continuation_store,
            runtime=runtime,
            worker_id="review-continuation-pre",
        ).run_once()

        assert delivered == 1
        assert runtime.results[-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert calls == ["root"]
        assert "claim_token" not in runtime.claims[-1]
        assert runtime.claims[-1]["claim_token_sha256"].startswith("sha256:")
        assert len(governed_worker.verified_grants) == 1
        renewed_grant = governed_worker.verified_grants[0]
        assert renewed_grant.expires_at_ms == (
            grant_clock.value
            + contracts[1].budget_policy.limits.time_ms
            + 60_000
        )
        assert renewed_grant.expires_at_ms > parent_expires_at
        renewal_rows = [
            row
            for row in ledger.trace_request(contracts[0].request_id)["governor_log"]
            if row.get("reason_code")
            == "TASK_GRAPH_NODE_REVIEW_CONTINUATION_APPROVE"
        ]
        assert len(renewal_rows) == 1
        assert (
            renewal_rows[0]["snapshot_refs"]["review_continuation_authority"]
            == runtime.claims[-1]
        )
        assert "claim_token\"" not in json.dumps(
            renewal_rows[0],
            sort_keys=True,
        )
        assert continuation_store.get_review_continuation(review["review_id"]) is None
        with sqlite3.connect(str(db_path)) as conn:
            attempts = conn.execute(
                "SELECT attempt_no, status FROM task_runtime_attempt"
            ).fetchall()
        assert attempts == [(1, "SUCCEEDED")]
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_post_review_worker_resumes_acceptance_without_worker_reexecution(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _with_governor_grant(_contracts((("root", ()),)))
    calls: list[str] = []
    reviews = ReviewStore(db_path, clock=lambda: NOW)
    node_grants = _NodeGrantService()
    application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            review_runtime=TaskNodeReviewRuntime(reviews, clock=lambda: NOW),
            acceptance_decision="REVIEW",
            worker_resolver=_GovernedResolver(calls),
        ),
        node_grant_service=node_grants,
    )

    waiting = await _initial_receipt(application, contracts)

    assert waiting["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert calls == ["root"]
    review = waiting["projection"]["review"]
    with sqlite3.connect(str(db_path)) as conn:
        attempt_before = conn.execute(
            "SELECT attempt_id, attempt_no, status FROM task_runtime_attempt"
        ).fetchall()
    assert len(attempt_before) == 1
    assert attempt_before[0][1:] == (1, "SUCCEEDED")

    _consume_review(reviews, review)
    continuation_store = ReviewContinuationStore(db_path, clock=lambda: NOW)
    runtime = _ApplicationContinuationRuntime(application, contracts)

    delivered = await _worker(
        store=continuation_store,
        runtime=runtime,
        worker_id="review-continuation-post",
    ).run_once()

    assert delivered == 1
    assert runtime.results[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert runtime.results[-1]["execution_receipt"]["replayed"] is True
    assert calls == ["root"]
    assert node_grants.continuation_claims == runtime.claims
    assert continuation_store.get_review_continuation(review["review_id"]) is None
    with sqlite3.connect(str(db_path)) as conn:
        attempt_after = conn.execute(
            "SELECT attempt_id, attempt_no, status FROM task_runtime_attempt"
        ).fetchall()
    assert attempt_after == attempt_before


@pytest.mark.asyncio
async def test_expired_claim_is_fenced_before_atomic_ready_claim_after_takeover(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    try:
        base_contracts = _contracts((("root", ()),), review_required=True)
        tokens = TokenService(approval_ttl_ms=600_000)
        parent_ref = _install_parent_grant(
            ledger=ledger,
            token_service=tokens,
            command=base_contracts[0],
            expires_at_ms=NOW + 100_000,
        )
        contracts = _with_real_governor_authority(
            base_contracts,
            parent_grant_ref=parent_ref,
        )
        calls: list[str] = []
        reviews = ReviewStore(db_path, clock=lambda: NOW)
        clock = _MutableClock()
        first_process = ReviewContinuationStore(db_path, clock=clock)
        node_grants = NodeBoundGrantService(
            ledger=ledger,
            token_service=tokens,
            review_store=first_process,
            clock=clock,
            child_ttl_ms=60_000,
        )
        application = GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=_coordinator(
                db_path,
                calls,
                review_runtime=TaskNodeReviewRuntime(reviews, clock=lambda: NOW),
                worker_resolver=_GovernedResolver(calls),
            ),
            node_grant_service=node_grants,
        )
        waiting = await _initial_receipt(application, contracts)
        review = waiting["projection"]["review"]
        _consume_review(reviews, review)

        first_claim = first_process.claim_pending_review_continuations(
            worker_id="review-process-a",
            lease_ms=100,
            limit=1,
        )[0]
        first_authority = first_process.validate_review_continuation_claim(
            first_claim
        )
        renewed_batch = node_grants.derive_review_continuation(
            parent_grant_ref=parent_ref,
            task_graph=contracts[3],
            work_order=contracts[1],
            request_id=contracts[0].request_id,
            session_id=contracts[0].session_id,
            principal_id=contracts[0].principal_id,
            continuation_claim=first_authority,
        )
        renewed_grant = renewed_batch.grants[0]
        verifier = DurableNodeGrantVerifier(
            ledger=ledger,
            review_store=first_process,
            clock=clock,
        )
        verified = verifier.verify_for_claim(
            grant_ref=renewed_grant.grant_ref,
            request_id=contracts[0].request_id,
            session_id=contracts[0].session_id,
            principal_id=contracts[0].principal_id,
            graph_id=contracts[3].graph_id,
            root_work_order_id=contracts[3].root_work_order_id,
            graph_revision=contracts[3].revision,
            node_id=contracts[3].nodes[0].node_id,
            route="skill",
            target_id="root",
            parent_grant_ref=parent_ref,
        )
        assert verified.grant_ref == renewed_grant.grant_ref

        clock.value += 101
        second_process = ReviewContinuationStore(db_path, clock=clock)
        second_claim = second_process.claim_pending_review_continuations(
            worker_id="review-process-b",
            lease_ms=100,
            limit=1,
        )[0]
        second_authority = second_process.validate_review_continuation_claim(
            second_claim
        )

        assert second_claim["claim_fencing_token"] == (
            first_claim["claim_fencing_token"] + 1
        )
        assert second_claim["claim_token"] != first_claim["claim_token"]
        with pytest.raises(ReviewContinuationStoreError) as stale_authority:
            first_process.validate_review_continuation_authority(first_authority)
        assert stale_authority.value.reason_code == REVIEW_CONTINUATION_CLAIM_LOST

        # The READY-node claim performs the Review fence check after acquiring
        # BEGIN IMMEDIATE and before creating an Attempt or Lease.  A former
        # owner therefore cannot leave even a partial execution authority row.
        task_store = DurableTaskStore(db_path, clock=clock)
        waiting_node = task_store.get_node(
            contracts[3].graph_id,
            contracts[3].revision,
            contracts[3].nodes[0].node_id,
        )
        ready_node = task_store.transition_node(
            contracts[3].graph_id,
            contracts[3].revision,
            contracts[3].nodes[0].node_id,
            expected_version=waiting_node["status_version"],
            target_status="READY",
            outbox=_runtime_outbox(
                "TASK_NODE",
                (
                    f"{contracts[3].graph_id}@{contracts[3].revision}/"
                    f"{contracts[3].nodes[0].node_id}"
                ),
                waiting_node["status_version"] + 1,
                "TASK_NODE_READY",
            ),
        )
        graph_record = task_store.get_graph(
            contracts[3].graph_id,
            contracts[3].revision,
        )
        if graph_record["status"] == "BLOCKED":
            task_store.transition_graph(
                contracts[3].graph_id,
                contracts[3].revision,
                expected_version=graph_record["status_version"],
                target_status="ACTIVE",
                outbox=_runtime_outbox(
                    "TASK_GRAPH",
                    contracts[3].graph_id,
                    graph_record["status_version"] + 1,
                    "TASK_GRAPH_ACTIVE",
                ),
            )
        budget = task_store.read_scheduler_snapshot(
            contracts[3].graph_id,
            contracts[3].revision,
        )["budgets"][0]
        stale_attempt_id = "attempt::review-stale-owner-must-not-exist"
        with pytest.raises(TaskStoreError) as stale_ready_claim:
            task_store.claim_ready_node(
                attempt_id=stale_attempt_id,
                graph_id=contracts[3].graph_id,
                graph_revision=contracts[3].revision,
                node_id=contracts[3].nodes[0].node_id,
                expected_node_version=ready_node["status_version"],
                expected_attempt_count=0,
                owner_id="stale-review-process-a",
                lease_ttl_ms=10_000,
                budget_id=budget["budget_id"],
                operation_scope=contracts[0].business_scope,
                operation_fingerprint=contracts[0].operation_fingerprint,
                attempt_created_outbox=_runtime_outbox(
                    "TASK_ATTEMPT",
                    stale_attempt_id,
                    0,
                    "TASK_ATTEMPT_CREATED",
                    attempt_id=stale_attempt_id,
                ),
                attempt_leased_outbox=_runtime_outbox(
                    "TASK_ATTEMPT",
                    stale_attempt_id,
                    1,
                    "TASK_ATTEMPT_LEASED",
                    attempt_id=stale_attempt_id,
                ),
                review_continuation_authority=first_authority,
            )
        assert stale_ready_claim.value.reason_code == REVIEW_CONTINUATION_CLAIM_LOST
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM task_runtime_attempt"
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT COUNT(*) FROM task_runtime_lease"
            ).fetchone() == (0,)

        # Create one legitimate execution authority under the takeover owner,
        # then replay the same Attempt identity with the former authority.  A
        # response-loss replay must validate Review ownership before it may
        # return the already-durable Attempt/Lease.
        replay_attempt_id = "attempt::review-current-owner-replay"
        replay_claim_args = {
            "attempt_id": replay_attempt_id,
            "graph_id": contracts[3].graph_id,
            "graph_revision": contracts[3].revision,
            "node_id": contracts[3].nodes[0].node_id,
            "expected_node_version": ready_node["status_version"],
            "expected_attempt_count": 0,
            "owner_id": "review-process-b-executor",
            "lease_ttl_ms": 10_000,
            "budget_id": budget["budget_id"],
            "operation_scope": contracts[0].business_scope,
            "operation_fingerprint": contracts[0].operation_fingerprint,
            "attempt_created_outbox": _runtime_outbox(
                "TASK_ATTEMPT",
                replay_attempt_id,
                0,
                "TASK_ATTEMPT_CREATED",
                attempt_id=replay_attempt_id,
            ),
            "attempt_leased_outbox": _runtime_outbox(
                "TASK_ATTEMPT",
                replay_attempt_id,
                1,
                "TASK_ATTEMPT_LEASED",
                attempt_id=replay_attempt_id,
            ),
        }
        legitimate_claim = task_store.claim_ready_node(
            **replay_claim_args,
            review_continuation_authority=second_authority,
        )
        assert legitimate_claim["replayed"] is False

        with pytest.raises(TaskStoreError) as stale_existing_attempt_replay:
            task_store.claim_ready_node(
                **replay_claim_args,
                review_continuation_authority=first_authority,
            )
        assert (
            stale_existing_attempt_replay.value.reason_code
            == REVIEW_CONTINUATION_CLAIM_LOST
        )
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM task_runtime_attempt"
            ).fetchone() == (1,)
            assert conn.execute(
                "SELECT COUNT(*) FROM task_runtime_lease"
            ).fetchone() == (1,)

        with pytest.raises(ReviewContinuationStoreError) as stale_completion:
            first_process.complete_review_continuation(
                review["review_id"],
                worker_id="review-process-a",
                claim_token=first_claim["claim_token"],
                fencing_token=first_claim["claim_fencing_token"],
                expected_version=first_claim["status_version"],
            )
        assert stale_completion.value.reason_code == REVIEW_CONTINUATION_CLAIM_LOST
        with pytest.raises(NodeGrantDerivationError):
            node_grants.derive_review_continuation(
                parent_grant_ref=parent_ref,
                task_graph=contracts[3],
                work_order=contracts[1],
                request_id=contracts[0].request_id,
                session_id=contracts[0].session_id,
                principal_id=contracts[0].principal_id,
                continuation_claim=first_authority,
            )
        with pytest.raises(NodeGrantVerificationError):
            verifier.verify_for_claim(
                grant_ref=renewed_grant.grant_ref,
                request_id=contracts[0].request_id,
                session_id=contracts[0].session_id,
                principal_id=contracts[0].principal_id,
                graph_id=contracts[3].graph_id,
                root_work_order_id=contracts[3].root_work_order_id,
                graph_revision=contracts[3].revision,
                node_id=contracts[3].nodes[0].node_id,
                route="skill",
                target_id="root",
                parent_grant_ref=parent_ref,
            )
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_two_node_review_continuation_survives_original_claim_expiry_via_heartbeat(
    tmp_path: Path,
) -> None:
    started = time.monotonic()

    def clock() -> int:
        return NOW + int((time.monotonic() - started) * 1_000)

    db_path = _migrated_db(tmp_path)
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    try:
        base_contracts = _contracts(
            (("root", ()), ("second", ("root",))),
            review_required=False,
        )
        tokens = TokenService(approval_ttl_ms=600_000)
        parent_ref = _install_parent_grant(
            ledger=ledger,
            token_service=tokens,
            command=base_contracts[0],
            expires_at_ms=NOW + 300_000,
        )
        contracts = _with_real_governor_authority(
            base_contracts,
            parent_grant_ref=parent_ref,
        )
        repository = HostCommandRepository(db_path, clock=clock)
        repository.admit(*contracts)

        # Install one real PRE checkpoint without making Review mandatory for
        # every downstream node.  The resumed authority must remain valid for
        # the whole immutable two-node graph, not only the reviewed root.
        task_store = DurableTaskStore(db_path, clock=clock)
        root = task_store.get_node(
            contracts[3].graph_id,
            contracts[3].revision,
            "root",
        )
        waiting = task_store.transition_node(
            contracts[3].graph_id,
            contracts[3].revision,
            "root",
            expected_version=root["status_version"],
            target_status="WAITING_APPROVAL",
            outbox=_runtime_outbox(
                "TASK_NODE",
                f"{contracts[3].graph_id}@{contracts[3].revision}/root",
                root["status_version"] + 1,
                "TASK_NODE_WAITING_APPROVAL",
            ),
        )
        reviews = ReviewStore(db_path, clock=clock)
        subject_ref = (
            f"task-node://{contracts[3].graph_id}/{contracts[3].revision}/root"
            f"?root={contracts[3].root_work_order_id}&attempt=not-started"
        )
        checkpoint_id = (
            f"task-checkpoint://{contracts[3].graph_id}/{contracts[3].revision}/root/"
            "pre_execution"
            f"?root={contracts[3].root_work_order_id}&attempt=not-started"
        )
        review = ReviewItem(
            review_id="review-two-node-heartbeat",
            review_type=ReviewType.PRE_EXECUTION_APPROVAL,
            subject_ref=subject_ref,
            subject_version=waiting["status_version"],
            requester="review-heartbeat-test",
            reason="prove graph-long Review claim heartbeat",
            risk_level=3,
            side_effects=("execute immutable two-node graph",),
            evidence_refs=("evidence://review/two-node-heartbeat",),
            status=ReviewStatus.PENDING,
            expires_at=clock() + 60_000,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id=checkpoint_id,
        )
        reviews.create_review(
            review,
            work_order_id=contracts[1].work_order_id,
            task_graph_id=contracts[3].graph_id,
            task_node_id="root",
        )
        _consume_review(reviews, review.to_dict())

        continuation_store = ReviewContinuationStore(db_path, clock=clock)
        node_grants = NodeBoundGrantService(
            ledger=ledger,
            token_service=tokens,
            review_store=continuation_store,
            clock=clock,
            child_ttl_ms=60_000,
        )
        grant_verifier = DurableNodeGrantVerifier(
            ledger=ledger,
            review_store=continuation_store,
            clock=clock,
        )
        calls: list[str] = []
        governed_worker = _SlowGrantVerifyingWorker(
            calls,
            grant_verifier,
            clock=clock,
            delay_seconds=0.9,
        )
        application = GovernedCommandApplication(
            command_repository=repository,
            coordinator=_coordinator(
                db_path,
                calls,
                review_runtime=TaskNodeReviewRuntime(reviews, clock=clock),
                worker_resolver=_ExactResolver(governed_worker),
                node_grant_verifier=grant_verifier,
                clock=clock,
            ),
            node_grant_service=node_grants,
        )
        runtime = _ApplicationContinuationRuntime(application, contracts)
        review_worker = ReviewContinuationWorker(
            store=continuation_store,
            runtime=runtime,
            worker_id="review-two-node-heartbeat-worker",
            claim_lease_ms=1_500,
            poll_interval_ms=60_000,
            retry_delay_ms=0,
            batch_limit=1,
        )

        delivered = await review_worker.run_once()

        assert delivered == 1
        with sqlite3.connect(str(db_path)) as debug_conn:
            debug_state = {
                "continuation": debug_conn.execute(
                    """
                    SELECT claim_owner, claim_expires_ts_ms, status_version, last_error
                    FROM task_runtime_review_continuation WHERE review_id = ?
                    """,
                    (review.review_id,),
                ).fetchall(),
                "attempts": debug_conn.execute(
                    "SELECT node_id, status FROM task_runtime_attempt ORDER BY node_id"
                ).fetchall(),
                "nodes": debug_conn.execute(
                    "SELECT node_id, status FROM task_runtime_node ORDER BY node_id"
                ).fetchall(),
            }
        assert runtime.results, {
            "claims": runtime.claims,
            "calls": calls,
            "durable": debug_state,
            "verification_times": governed_worker.verification_times,
        }
        assert runtime.results[-1]["execution_receipt"]["status"] == "ACCEPTED"
        assert calls == ["root", "second"]
        assert len(governed_worker.verified_grants) == 2
        original_claim_expiry = runtime.claims[0]["claim_expires_ts_ms"]
        assert governed_worker.verification_times[-1] > original_claim_expiry
        assert continuation_store.get_review_continuation(review.review_id) is None
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute(
                "SELECT node_id, status FROM task_runtime_attempt ORDER BY attempt_no, node_id"
            ).fetchall() == [("root", "SUCCEEDED"), ("second", "SUCCEEDED")]
    finally:
        ledger.close()
