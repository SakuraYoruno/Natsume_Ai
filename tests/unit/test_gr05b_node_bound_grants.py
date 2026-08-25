from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.contracts.agent_runtime.budget_policy import BudgetPolicy
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    durable_governor_binding,
    governor_grant_ref,
)
from host_brain.orchestration.node_grants import (
    NODE_GRANT_EXPIRED,
    NODE_GRANT_PARENT_INVALID,
    NODE_GRANT_REPLAY_DRIFT,
    NODE_GRANT_VERIFICATION_FAILED,
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
    NodeGrantDerivationError,
    NodeGrantVerificationError,
)
from host_brain.orchestration.node_grant_errors import (
    NODE_GRANT_BINDING_MISSING,
    NODE_GRANT_ROUTE_DRIFT,
)


NOW_MS = 2_000_000
REQUEST_ID = "request-gr05b"
SESSION_ID = "session-gr05b"
PRINCIPAL_ID = "principal-gr05b"


class _CountingTokenService(TokenService):
    def __init__(self) -> None:
        super().__init__(approval_ttl_ms=120_000)
        self.child_issue_calls = 0
        self.child_subjects: list[str] = []

    def issue_approval_token(self, **kwargs: Any) -> str:
        if str(kwargs.get("intent_id") or "").startswith("node-grant-intent::"):
            self.child_issue_calls += 1
            self.child_subjects.append(str(kwargs.get("skill_name") or ""))
        return super().issue_approval_token(**kwargs)


class _BarrierChildTokenService(_CountingTokenService):
    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__()
        self._barrier = barrier
        self._first_child = True

    def issue_approval_token(self, **kwargs: Any) -> str:
        if (
            self._first_child
            and str(kwargs.get("intent_id") or "").startswith("node-grant-intent::")
        ):
            self._first_child = False
            self._barrier.wait(timeout=10)
        return super().issue_approval_token(**kwargs)


def _parent(ledger: SQLiteLedger, tokens: TokenService) -> str:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-gr05b-parent",
        "ts_ms": NOW_MS - 100,
        "session_id": SESSION_ID,
        "seq": 7,
        "person_id": PRINCIPAL_ID,
        "principal_id": PRINCIPAL_ID,
        "type": "query",
        "action": "exec_intent",
        "target": "host",
        "args": {"approved_plan_ref": "plan://gr05b"},
        "nonce": "nonce-gr05b-parent",
    }
    token_ref = tokens.issue_approval_token(
        intent_id=intent["id"],
        skill_name="exec_intent",
        session_id=SESSION_ID,
        seq=7,
        now_ts_ms=NOW_MS - 100,
    )
    binding = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref=token_ref,
        risk_level=2,
        issued_at_ms=NOW_MS - 100,
        expires_at_ms=NOW_MS + 100_000,
    )
    decision = {
        "request_id": REQUEST_ID,
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVE",
        "rule_trace": [],
        "tokens": {"approval_token_ref": token_ref},
        "approved_action_binding": binding,
        "snapshot_refs": {"plan": "plan://gr05b"},
        "stage": "S_INTENT_OK",
        "ts_ms": NOW_MS - 100,
    }
    ledger.log_governor(decision)
    return governor_grant_ref(REQUEST_ID, decision)


def _graph_and_order(
    parent_ref: str,
    *,
    routes: tuple[tuple[str, str], ...] = (("skill", "code-check"), ("skill", "report-build")),
    bindings: list[dict[str, Any]] | None = None,
    budget_time_ms: int = 60_000,
) -> tuple[TaskGraph, WorkOrderV3]:
    nodes = tuple(
        TaskNode(
            node_id=f"node-{index}",
            dependencies=((f"node-{index - 1}",) if index > 1 else ()),
            route=f"{route}:{target}",
            input_refs=(f"artifact://gr05b/input/{index}",),
            output_contract={"type": "evidence"},
            retry_policy={"max_attempts": 1},
            risk_level=index,
            acceptance_criteria=("durable evidence exists",),
            status=(TaskNodeStatus.BLOCKED if index > 1 else TaskNodeStatus.READY),
        )
        for index, (route, target) in enumerate(routes, start=1)
    )
    graph = TaskGraph(
        graph_id="task-graph://gr05b",
        root_work_order_id="work-order://gr05b",
        revision=3,
        planner_ref="planner://gr05b",
        status=TaskGraphStatus.CREATED,
        created_at=NOW_MS - 200,
        nodes=nodes,
    )
    actual_bindings = bindings
    if actual_bindings is None:
        actual_bindings = [
            {
                "candidate_id": f"candidate-{index}",
                "node_id": node.node_id,
                "objective": f"execute {node.node_id}",
                "worker_route": route,
                "target_id": target,
            }
            for index, (node, (route, target)) in enumerate(zip(nodes, routes, strict=True), start=1)
        ]
    budget = BudgetPolicy.from_mapping({
        "schema_version": "Natsume.agent.budget_policy.v1",
        "scope": "gr05b",
        "limits": {
            "token_count": 2_000,
            "tool_call_count": 4,
            "time_ms": budget_time_ms,
            "concurrency": 1,
            "retry_count": 1,
            "model_cost_micros": 100_000,
        },
        "on_exceeded": "BLOCK",
        "metadata": {"policy_version": "gr05b.v1"},
    })
    order = WorkOrderV3(
        work_order_id=graph.root_work_order_id,
        domain="code",
        run_mode="worker",
        objective="execute canonical graph",
        metadata={
            "planning_lineage": {
                "graph_id": graph.graph_id,
                "root_work_order_id": graph.root_work_order_id,
                "revision": graph.revision,
                "approval_ref": parent_ref,
                "review_ref": parent_ref,
                "node_bindings": deepcopy(actual_bindings),
            }
        },
        root_work_order_id=graph.root_work_order_id,
        task_graph_id=graph.graph_id,
        task_node_id=graph.nodes[0].node_id,
        graph_revision=graph.revision,
        capability_requirements=tuple(target for _route, target in routes),
        risk_level=max(node.risk_level for node in nodes),
        budget_policy=budget,
        business_idempotency_scope="gr05b",
        operation_fingerprint="sha256:" + "a" * 64,
        approval_ref=parent_ref,
        review_ref=parent_ref,
    )
    graph.validate()
    order.validate()
    return graph, order


def _service(ledger: SQLiteLedger, tokens: TokenService) -> NodeBoundGrantService:
    return NodeBoundGrantService(
        ledger=ledger,
        token_service=tokens,
        clock=lambda: NOW_MS,
        child_ttl_ms=60_000,
    )


def _derive(service: NodeBoundGrantService, parent_ref: str, graph: TaskGraph, order: WorkOrderV3):
    return service.derive(
        parent_grant_ref=parent_ref,
        task_graph=graph,
        work_order=order,
        request_id=REQUEST_ID,
        session_id=SESSION_ID,
        principal_id=PRINCIPAL_ID,
    )


def test_parent_binding_preflight_rejects_principal_drift_without_child_grants() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        service = _service(ledger, tokens)

        with pytest.raises(NodeGrantDerivationError) as rejected:
            service.validate_parent_binding(
                parent_grant_ref=parent_ref,
                request_id=REQUEST_ID,
                session_id=SESSION_ID,
                principal_id="principal-gr05b-drift",
            )

        assert rejected.value.reason_code == NODE_GRANT_PARENT_INVALID
        assert [
            row
            for row in ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ] == []
        assert tokens.child_issue_calls == 0
    finally:
        ledger.close()


def test_parent_binding_preflight_classifies_expiry_without_hiding_identity_drift() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        service = NodeBoundGrantService(
            ledger=ledger,
            token_service=tokens,
            clock=lambda: NOW_MS + 100_001,
            child_ttl_ms=60_000,
        )

        with pytest.raises(NodeGrantDerivationError) as expired:
            service.validate_parent_binding(
                parent_grant_ref=parent_ref,
                request_id=REQUEST_ID,
                session_id=SESSION_ID,
                principal_id=PRINCIPAL_ID,
            )

        assert expired.value.reason_code == NODE_GRANT_EXPIRED
        assert "expired" in expired.value.detail
        assert tokens.child_issue_calls == 0
    finally:
        ledger.close()


def _verification_kwargs(parent_ref: str, graph: TaskGraph, grant: Any) -> dict[str, Any]:
    return {
        "grant_ref": grant.grant_ref,
        "request_id": REQUEST_ID,
        "session_id": SESSION_ID,
        "principal_id": PRINCIPAL_ID,
        "graph_id": graph.graph_id,
        "root_work_order_id": graph.root_work_order_id,
        "graph_revision": graph.revision,
        "node_id": grant.node_id,
        "route": grant.route,
        "target_id": grant.target_id,
        "parent_grant_ref": parent_ref,
    }


def test_gr05b_skill_nodes_receive_unique_durable_child_grants_and_replay_stably() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(parent_ref)
        order_before = order.to_dict()
        service = _service(ledger, tokens)

        first = _derive(service, parent_ref, graph, order)
        replay = _derive(service, parent_ref, graph, order)

        assert first.replayed is False
        assert replay.replayed is True
        assert replay.grant_refs == first.grant_refs
        assert len(first.grants) == len(graph.nodes) == 2
        assert len(set(first.grant_refs.values())) == 2
        assert parent_ref not in first.grant_refs.values()
        assert tokens.child_issue_calls == 2
        assert order.to_dict() == order_before
        assert "approval_token_ref" not in json.dumps(first.to_dict(), sort_keys=True)

        child_rows = [
            row for row in ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ]
        assert len(child_rows) == 2
        by_node = {
            row["snapshot_refs"]["node_grant"]["node_id"]: row
            for row in child_rows
        }
        for grant in first.grants:
            row = by_node[grant.node_id]
            binding = durable_governor_binding(row)
            authority = binding["node_authority"]
            assert authority == row["snapshot_refs"]["node_grant"]
            assert authority["graph_id"] == graph.graph_id
            assert authority["graph_revision"] == graph.revision
            assert authority["node_id"] == grant.node_id
            assert authority["route"] == grant.route
            assert authority["target_id"] == grant.target_id
            assert authority["parent_governor_grant_ref"] == parent_ref
            assert authority["expires_at_ms"] == grant.expires_at_ms
            assert authority["nonce"] == grant.nonce
            DurableGovernorGrantResolver(ledger).require(
                request_id=REQUEST_ID,
                expected_ref=grant.grant_ref,
            )
    finally:
        ledger.close()


def test_gr05b_expired_batch_reopens_identity_but_cannot_authorize_execution() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(parent_ref)
        first = _derive(_service(ledger, tokens), parent_ref, graph, order)
        expired_now = max(grant.expires_at_ms for grant in first.grants) + 1
        replay = _derive(
            NodeBoundGrantService(
                ledger=ledger,
                token_service=tokens,
                clock=lambda: expired_now,
                child_ttl_ms=60_000,
            ),
            parent_ref,
            graph,
            order,
        )

        assert replay.replayed is True
        assert replay.grant_refs == first.grant_refs
        assert tokens.child_issue_calls == len(first.grants)
        with pytest.raises(NodeGrantVerificationError) as expired:
            DurableNodeGrantVerifier(
                ledger=ledger,
                clock=lambda: expired_now,
            ).verify(
                **_verification_kwargs(
                    parent_ref,
                    graph,
                    replay.grants[0],
                )
            )
        assert expired.value.reason_code == NODE_GRANT_EXPIRED
    finally:
        ledger.close()


def test_gr05b_node_grant_keeps_one_recovery_margin_after_task_budget() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(
            parent_ref,
            routes=(("skill", "long-running-agent"),),
            budget_time_ms=360_000,
        )
        grant = _derive(_service(ledger, tokens), parent_ref, graph, order).grants[0]

        assert grant.expires_at_ms == NOW_MS + 420_000
        assert (
            grant.expires_at_ms
            == NOW_MS + order.budget_policy.limits.time_ms + 60_000
        )
        assert grant.expires_at_ms > NOW_MS + 100_000
        child_row = next(
            row
            for row in ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        )
        child_token_ref = child_row["tokens"]["approval_token_ref"]
        assert (
            tokens.export_snapshot()[child_token_ref]["expiry_ts_ms"]
            == grant.expires_at_ms
        )

        verified_after_parent_expiry = DurableNodeGrantVerifier(
            ledger=ledger,
            clock=lambda: NOW_MS + 100_001,
        ).verify(**_verification_kwargs(parent_ref, graph, grant))
        assert verified_after_parent_expiry.grant_ref == grant.grant_ref

        with pytest.raises(NodeGrantVerificationError) as expired:
            DurableNodeGrantVerifier(
                ledger=ledger,
                clock=lambda: grant.expires_at_ms,
            ).verify(**_verification_kwargs(parent_ref, graph, grant))
        assert expired.value.reason_code == NODE_GRANT_EXPIRED
    finally:
        ledger.close()


def test_gr05b_missing_binding_fails_whole_graph_before_token_or_ledger_side_effect() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        bindings = [{
            "node_id": "node-1",
            "worker_route": "skill",
            "target_id": "code-check",
        }]
        graph, order = _graph_and_order(parent_ref, bindings=bindings)

        with pytest.raises(NodeGrantDerivationError) as error:
            _derive(_service(ledger, tokens), parent_ref, graph, order)

        assert error.value.reason_code == NODE_GRANT_BINDING_MISSING
        assert tokens.child_issue_calls == 0
        assert not [
            row for row in ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ]
    finally:
        ledger.close()


def test_gr05b_route_drift_fails_whole_graph_before_token_or_ledger_side_effect() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(parent_ref)
        order.metadata["planning_lineage"]["node_bindings"][1]["target_id"] = "substituted"

        with pytest.raises(NodeGrantDerivationError) as error:
            _derive(_service(ledger, tokens), parent_ref, graph, order)

        assert error.value.reason_code == NODE_GRANT_ROUTE_DRIFT
        assert tokens.child_issue_calls == 0
        assert len(ledger.trace_request(REQUEST_ID)["governor_log"]) == 1
    finally:
        ledger.close()


def test_gr05b_sqlite_batch_failure_rolls_back_every_child_grant_and_retries_cleanly() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(parent_ref)
        service = _service(ledger, tokens)
        ledger._conn.execute(  # noqa: SLF001 - deliberate SQLite atomicity fault injection
            """
            CREATE TRIGGER abort_second_node_grant
            BEFORE INSERT ON governor_log
            WHEN NEW.stage = 'S_TASK_GRAPH_NODE_GRANT'
             AND (
                 SELECT COUNT(*) FROM governor_log
                 WHERE stage = 'S_TASK_GRAPH_NODE_GRANT'
             ) = 1
            BEGIN
                SELECT RAISE(ABORT, 'injected second node grant failure');
            END
            """
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected second node grant failure",
        ):
            _derive(service, parent_ref, graph, order)

        assert [
            row
            for row in ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ] == []

        ledger._conn.execute("DROP TRIGGER abort_second_node_grant")  # noqa: SLF001
        retried = _derive(service, parent_ref, graph, order)

        assert retried.replayed is False
        assert len(retried.grants) == 2
        assert tokens.child_issue_calls == 4
        child_rows = [
            row
            for row in ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ]
        assert len(child_rows) == 2
        assert {row["intent_id"] for row in child_rows} == {
            grant.intent_id for grant in retried.grants
        }
    finally:
        ledger.close()


def test_gr05b_two_processes_converge_one_atomic_node_grant_batch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "concurrent-node-grants.sqlite3"
    first_ledger = SQLiteLedger(db_path, check_same_thread=False)
    second_ledger = SQLiteLedger(db_path, check_same_thread=False)
    try:
        parent_ref = _parent(first_ledger, TokenService())
        graph, order = _graph_and_order(parent_ref)
        barrier = threading.Barrier(2)
        first_service = NodeBoundGrantService(
            ledger=first_ledger,
            token_service=_BarrierChildTokenService(barrier),
            clock=lambda: NOW_MS,
            child_ttl_ms=60_000,
        )
        second_service = NodeBoundGrantService(
            ledger=second_ledger,
            token_service=_BarrierChildTokenService(barrier),
            clock=lambda: NOW_MS,
            child_ttl_ms=60_000,
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(
                _derive,
                first_service,
                parent_ref,
                graph,
                deepcopy(order),
            )
            second_future = pool.submit(
                _derive,
                second_service,
                parent_ref,
                graph,
                deepcopy(order),
            )
            first = first_future.result(timeout=15)
            second = second_future.result(timeout=15)

        assert sorted((first.replayed, second.replayed)) == [False, True]
        assert first.grant_refs == second.grant_refs
        child_rows = [
            row
            for row in first_ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ]
        assert len(child_rows) == len(graph.nodes)
        node_ids = [
            row["snapshot_refs"]["node_grant"]["node_id"]
            for row in child_rows
        ]
        assert sorted(node_ids) == sorted(node.node_id for node in graph.nodes)
        assert all(node_ids.count(node.node_id) == 1 for node in graph.nodes)
    finally:
        first_ledger.close()
        second_ledger.close()


@pytest.mark.parametrize("route", ["skill", "delegate", "chat"])
def test_gr05b_all_three_routes_issue_and_verify_exact_secret_free_authority(
    route: str,
) -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(
            parent_ref,
            routes=((route, "shared-target"),),
        )
        grant = _derive(_service(ledger, tokens), parent_ref, graph, order).grants[0]
        verified = DurableNodeGrantVerifier(ledger=ledger, clock=lambda: NOW_MS).verify(
            grant_ref=grant.grant_ref,
            request_id=REQUEST_ID,
            session_id=SESSION_ID,
            principal_id=PRINCIPAL_ID,
            graph_id=graph.graph_id,
            root_work_order_id=graph.root_work_order_id,
            graph_revision=graph.revision,
            node_id=grant.node_id,
            route=route,
            target_id="shared-target",
            parent_grant_ref=parent_ref,
        )

        expected_subject = "shared-target" if route == "skill" else f"{route}:shared-target"
        assert verified.route == route
        assert verified.target_id == "shared-target"
        assert verified.action_kind == expected_subject
        assert verified.token_subject == expected_subject
        assert tokens.child_subjects == [expected_subject]
        assert DurableNodeGrantVerifier(ledger=ledger, clock=lambda: NOW_MS).verify(
            **_verification_kwargs(parent_ref, graph, grant)
        ) == verified
        serialized = json.dumps(verified.to_dict(), sort_keys=True)
        assert "approval_token_ref" not in serialized
        assert "approval::" not in serialized
    finally:
        ledger.close()


@pytest.mark.parametrize(
    ("field", "drifted"),
    [
        ("route", "delegate"),
        ("target_id", "other-target"),
        ("graph_id", "task-graph://other"),
        ("graph_revision", 4),
        ("parent_grant_ref", "governor-decision://sha256/" + "f" * 64),
    ],
)
def test_gr05b_verifier_rejects_cross_route_target_graph_revision_and_parent_replay(
    field: str,
    drifted: Any,
) -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(parent_ref, routes=(("skill", "shared-target"),))
        grant = _derive(_service(ledger, tokens), parent_ref, graph, order).grants[0]
        kwargs = _verification_kwargs(parent_ref, graph, grant)
        kwargs[field] = drifted

        with pytest.raises(NodeGrantVerificationError) as error:
            DurableNodeGrantVerifier(ledger=ledger, clock=lambda: NOW_MS).verify(**kwargs)

        assert error.value.reason_code == NODE_GRANT_VERIFICATION_FAILED
    finally:
        ledger.close()


def test_gr05b_verifier_rejects_expired_and_forged_child_grants() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(parent_ref, routes=(("chat", "shared-target"),))
        grant = _derive(_service(ledger, tokens), parent_ref, graph, order).grants[0]
        kwargs = _verification_kwargs(parent_ref, graph, grant)

        with pytest.raises(NodeGrantVerificationError) as expired:
            DurableNodeGrantVerifier(
                ledger=ledger,
                clock=lambda: grant.expires_at_ms + 1,
            ).verify(**kwargs)
        assert expired.value.reason_code == NODE_GRANT_EXPIRED

        kwargs["grant_ref"] = "governor-decision://sha256/" + "0" * 64
        with pytest.raises(NodeGrantVerificationError) as forged:
            DurableNodeGrantVerifier(ledger=ledger, clock=lambda: NOW_MS).verify(**kwargs)
        assert forged.value.reason_code == NODE_GRANT_VERIFICATION_FAILED
    finally:
        ledger.close()


def test_gr05b_partial_durable_replay_fails_closed_without_reissuing() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(parent_ref)
        service = _service(ledger, tokens)
        first = _derive(service, parent_ref, graph, order)
        assert tokens.child_issue_calls == 2

        with ledger._conn:
            ledger._conn.execute(
                "DELETE FROM governor_log WHERE intent_id = ?",
                (first.grants[-1].intent_id,),
            )
        with pytest.raises(NodeGrantDerivationError) as error:
            _derive(service, parent_ref, graph, order)

        assert error.value.reason_code == NODE_GRANT_REPLAY_DRIFT
        assert tokens.child_issue_calls == 2
        assert len([
            row for row in ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ]) == 1
    finally:
        ledger.close()


class _ReviewContinuationStore:
    def __init__(self, authority: dict[str, Any]) -> None:
        self.authority = deepcopy(authority)
        self.live = True

    def validate_review_continuation_claim(
        self,
        claim: dict[str, Any],
    ) -> dict[str, Any]:
        raise AssertionError("secret-bearing claim validator must not cross runtime boundary")

    def validate_review_continuation_authority(
        self,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.live or dict(authority) != self.authority:
            raise RuntimeError("Review continuation authority moved")
        return deepcopy(self.authority)

    def validate_review_continuation_execution_authority(
        self,
        authority: dict[str, Any],
        **_execution: Any,
    ) -> dict[str, Any]:
        return self.validate_review_continuation_authority(authority)


class _HeartbeatedReviewContinuationStore:
    def __init__(
        self,
        authority: dict[str, Any],
        *,
        durable_claim_expires_ts_ms: int,
    ) -> None:
        self.authority = deepcopy(authority)
        self.durable_claim_expires_ts_ms = int(durable_claim_expires_ts_ms)
        self.canonical_owner = str(authority["claim_owner"])
        self.canonical_fence = int(authority["claim_fencing_token"])
        self.canonical_token_digest = str(authority["claim_token_sha256"])

    def validate_review_continuation_authority(
        self,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        candidate = dict(authority)
        if (
            candidate != self.authority
            or str(candidate.get("claim_owner")) != self.canonical_owner
            or int(candidate.get("claim_fencing_token") or 0)
            != self.canonical_fence
            or str(candidate.get("claim_token_sha256"))
            != self.canonical_token_digest
            or int(candidate.get("claim_expires_ts_ms") or 0)
            > self.durable_claim_expires_ts_ms
        ):
            raise RuntimeError("Review continuation authority moved")
        # The secret-free projection intentionally retains its original,
        # shorter expiry.  The canonical store has already proved that the
        # same owner/token/fence remains live under the heartbeat extension.
        return deepcopy(candidate)


def test_review_renewal_accepts_heartbeat_validated_projection_after_initial_expiry() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    resumed_at = NOW_MS + 10_000
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(
            parent_ref,
            routes=(("skill", "code-check"),),
        )
        initial_authority = {
            "schema_version": "Natsume.task.review_continuation_authority.v1",
            "review_id": "task-review-heartbeated-projection",
            "review_status": "CONSUMED",
            "review_status_version": 1,
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "node_id": graph.nodes[0].node_id,
            "subject_version": 1,
            "resume_phase": "PRE_EXECUTION",
            "claim_owner": "review-worker-heartbeat",
            "claim_token_sha256": "sha256:" + "c" * 64,
            "claim_fencing_token": 11,
            "claim_expires_ts_ms": resumed_at - 1,
            "status_version": 1,
        }
        reviews = _HeartbeatedReviewContinuationStore(
            initial_authority,
            durable_claim_expires_ts_ms=resumed_at + 30_000,
        )
        assert (
            initial_authority["claim_expires_ts_ms"]
            < resumed_at
            < reviews.durable_claim_expires_ts_ms
        )
        service = NodeBoundGrantService(
            ledger=ledger,
            token_service=tokens,
            review_store=reviews,
            clock=lambda: resumed_at,
            child_ttl_ms=60_000,
        )

        batch = service.derive_review_continuation(
            parent_grant_ref=parent_ref,
            task_graph=graph,
            work_order=order,
            request_id=REQUEST_ID,
            session_id=SESSION_ID,
            principal_id=PRINCIPAL_ID,
            continuation_claim=initial_authority,
        )

        assert batch.replayed is False
        assert len(batch.grants) == 1
        assert (
            batch.grants[0].expires_at_ms
            == resumed_at + order.budget_policy.limits.time_ms + 60_000
        )

        reviews.canonical_fence += 1
        with pytest.raises(NodeGrantDerivationError) as taken_over:
            service.derive_review_continuation(
                parent_grant_ref=parent_ref,
                task_graph=graph,
                work_order=order,
                request_id=REQUEST_ID,
                session_id=SESSION_ID,
                principal_id=PRINCIPAL_ID,
                continuation_claim=initial_authority,
            )
        assert taken_over.value.reason_code == NODE_GRANT_PARENT_INVALID
    finally:
        ledger.close()


def test_gr05b_consumed_review_renews_expired_parent_only_for_live_claim() -> None:
    ledger = SQLiteLedger(":memory:")
    tokens = _CountingTokenService()
    resumed_at = NOW_MS + 100_001
    try:
        parent_ref = _parent(ledger, tokens)
        graph, order = _graph_and_order(
            parent_ref,
            routes=(("delegate", "internal-agent"),),
        )
        authority = {
            "schema_version": "Natsume.task.review_continuation_authority.v1",
            "review_id": "task-review-gr05b",
            "review_status": "CONSUMED",
            "review_status_version": 1,
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "node_id": graph.nodes[0].node_id,
            "subject_version": 1,
            "resume_phase": "PRE_EXECUTION",
            "claim_owner": "review-worker-gr05b",
            "claim_token_sha256": "sha256:" + "b" * 64,
            "claim_fencing_token": 3,
            "claim_expires_ts_ms": resumed_at + 30_000,
        }
        reviews = _ReviewContinuationStore(authority)
        service = NodeBoundGrantService(
            ledger=ledger,
            token_service=tokens,
            review_store=reviews,
            clock=lambda: resumed_at,
            child_ttl_ms=60_000,
        )
        batch = service.derive_review_continuation(
            parent_grant_ref=parent_ref,
            task_graph=graph,
            work_order=order,
            request_id=REQUEST_ID,
            session_id=SESSION_ID,
            principal_id=PRINCIPAL_ID,
            continuation_claim=authority,
        )
        grant = batch.grants[0]

        assert grant.expires_at_ms == resumed_at + 120_000
        assert batch.replayed is False
        verified = DurableNodeGrantVerifier(
            ledger=ledger,
            review_store=reviews,
            clock=lambda: resumed_at,
        ).verify(
            **_verification_kwargs(parent_ref, graph, grant),
            attempt_id="attempt-review-gr05b",
            lease_id="lease-review-gr05b",
            lease_owner_id="owner-review-gr05b",
            fencing_token=1,
        )
        assert verified.grant_ref == grant.grant_ref
        renewal_rows = [
            row
            for row in ledger.trace_request(REQUEST_ID)["governor_log"]
            if row.get("reason_code")
            == "TASK_GRAPH_NODE_REVIEW_CONTINUATION_APPROVE"
        ]
        assert len(renewal_rows) == 1
        assert (
            renewal_rows[0]["snapshot_refs"]["review_continuation_authority"]
            == authority
        )

        reviews.live = False
        with pytest.raises(NodeGrantVerificationError):
            DurableNodeGrantVerifier(
                ledger=ledger,
                review_store=reviews,
                clock=lambda: resumed_at,
            ).verify(
                **_verification_kwargs(parent_ref, graph, grant),
                attempt_id="attempt-review-gr05b",
                lease_id="lease-review-gr05b",
                lease_owner_id="owner-review-gr05b",
                fencing_token=1,
            )
    finally:
        ledger.close()
