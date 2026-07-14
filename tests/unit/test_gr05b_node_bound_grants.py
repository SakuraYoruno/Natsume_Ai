from __future__ import annotations

from copy import deepcopy
import json
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
    NODE_GRANT_BINDING_MISSING,
    NODE_GRANT_EXPIRED,
    NODE_GRANT_REPLAY_DRIFT,
    NODE_GRANT_ROUTE_DRIFT,
    NODE_GRANT_VERIFICATION_FAILED,
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
    NodeGrantDerivationError,
    NodeGrantVerificationError,
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
            "time_ms": 60_000,
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
