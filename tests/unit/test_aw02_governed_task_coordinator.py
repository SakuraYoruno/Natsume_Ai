from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration import GovernedCommandApplication
from host_brain.task_runtime import (
    AdmittedExecution,
    GovernedRuntimeError,
    GovernedTaskCoordinator,
)
from host_brain.task_runtime.review_runtime import TaskNodeReviewGate
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


class _NotFound(RuntimeError):
    reason_code = "TASK_STORE_NOT_FOUND"


class _OutboxFactory:
    @staticmethod
    def create(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


class _Publisher:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def publish_pending(self, *, limit: int = 100) -> dict[str, Any]:
        self.calls.append(f"publish:{limit}")
        return {"status": "OK", "published_count": 1}


@dataclass
class _ReservationResult:
    acquired: bool
    reservation: dict[str, Any]


@dataclass
class _BudgetResult:
    accepted: bool
    reservation: dict[str, Any]
    exceeded_dimensions: tuple[str, ...] = ()
    replayed: bool = False


class _FakeTaskStore:
    def __init__(
        self,
        graph: TaskGraph,
        calls: list[str],
        *,
        reservation_acquired: bool = True,
        initial_time_consumed_ms: int = 0,
    ) -> None:
        node = graph.nodes[0]
        self.calls = calls
        self.graph = {
            "graph_id": graph.graph_id,
            "revision": graph.revision,
            "root_work_order_id": graph.root_work_order_id,
            "status": graph.status.value,
            "status_version": 0,
            "graph": graph.to_dict(),
        }
        self.node = {
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "node_id": node.node_id,
            "status": node.status.value,
            "status_version": node.status_version,
            "retry_not_before_ms": None,
            "node": node.to_dict(),
        }
        self.attempts: dict[str, dict[str, Any]] = {}
        self.acceptance_decisions: dict[int, dict[str, Any]] = {}
        self.reservation: dict[str, Any] | None = None
        self.budget: dict[str, Any] | None = None
        self.budget_usage: dict[str, _BudgetResult] = {}
        self.node_budgets: dict[str, dict[str, Any]] = {}
        self.verification_decision: dict[str, Any] | None = None
        self.reservation_acquired = bool(reservation_acquired)
        self.initial_time_consumed_ms = int(initial_time_consumed_ms)

    def assert_budget_usage_ready(self) -> None:
        return None

    def assert_node_budget_ready(self) -> None:
        return None

    def assert_node_verification_ready(self) -> None:
        return None

    def assert_dag_scheduling_ready(self) -> None:
        return None

    def assert_parallel_settlement_decision_ready(self) -> None:
        return None

    def assert_graph_cancellation_ready(self) -> None:
        return None

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> None:
        del graph_id, revision
        return None

    def get_graph(self, graph_id: str, revision: int) -> dict[str, Any]:
        return dict(self.graph)

    def get_node(self, graph_id: str, revision: int, node_id: str) -> dict[str, Any]:
        return {**self.node, "node": dict(self.node["node"])}

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        return dict(self.attempts[attempt_id])

    def get_operation_reservation(self, reservation_id: str) -> dict[str, Any]:
        if self.reservation is None or self.reservation["reservation_id"] != reservation_id:
            raise _NotFound(reservation_id)
        return dict(self.reservation)

    def reconcile_operation_reservation_state(self, reservation_id: str) -> dict[str, Any]:
        return self.get_operation_reservation(reservation_id)

    def get_committed_operation_checkpoint(self, reservation_id: str) -> dict[str, Any]:
        reservation = self.get_operation_reservation(reservation_id)
        attempt = dict(self.attempts[reservation["attempt_id"]])
        receipt_ref = str(reservation["receipt_ref"])
        digest_ref = "sha256://execution/" + receipt_ref.rsplit("/", 1)[-1]
        return {
            "reservation": reservation,
            "attempt": attempt,
            "lease": {
                "lease_id": reservation["lease_id"],
                "attempt_id": reservation["attempt_id"],
                "owner_id": reservation["owner_id"],
                "fencing_token": reservation["fencing_token"],
            },
            "outbox": {
                "aggregate_type": "OPERATION_RESERVATION",
                "aggregate_id": reservation_id,
                "aggregate_version": reservation["status_version"],
                "event_type": "OPERATION_COMMITTED",
                "attempt_id": reservation["attempt_id"],
                "evidence_refs": [receipt_ref, digest_ref],
            },
            "checkpoint_digest_ref": digest_ref,
        }

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict[str, Any]:
        return {
            "observed_ts_ms": 1,
            "graph_record": dict(self.graph),
            "attempts": [dict(item) for item in self.attempts.values()],
            "active_leases": [],
            "budgets": [dict(self.budget)] if self.budget else [],
        }

    def create_budget_reservation(self, reservation: Any, **kwargs: Any) -> dict[str, Any]:
        consumed = reservation.consumed.to_dict()
        consumed["time_ms"] = self.initial_time_consumed_ms
        self.budget = {
            **reservation.to_dict(),
            "consumed": consumed,
            "graph_id": kwargs["graph_id"],
            "graph_revision": kwargs["graph_revision"],
            "node_id": None,
            "attempt_id": None,
            "attempted_consumed": dict(consumed),
            "exceeded_dimensions": [],
        }
        return dict(self.budget)

    def transition_budget(self, budget_id: str, *, expected_version: int, target_status: str, outbox: Any) -> dict[str, Any]:
        assert self.budget is not None and expected_version == self.budget["status_version"]
        self.budget["status"] = target_status
        self.budget["status_version"] += 1
        return dict(self.budget)

    def reconcile_budget(self, budget_id: str) -> dict[str, Any]:
        if self.budget is None:
            raise _NotFound(budget_id)
        remaining = {
            key: self.budget["reserved"][key] - self.budget["consumed"][key]
            for key in self.budget["reserved"]
        }
        return {**self.budget, "remaining": remaining, "balanced": True}

    def get_node_budget_reservation(
        self,
        *,
        parent_budget_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        record = dict(self.node_budgets[attempt_id])
        attempt = self.attempts[attempt_id]
        record["attempt_status"] = attempt["status"]
        record["status"] = (
            "CONSUMED" if attempt["status"] == "SUCCEEDED" else "ACTIVE"
        )
        record["remaining"] = {
            key: record["reserved"][key] - record["consumed"][key]
            for key in record["reserved"]
        }
        return record

    def _bind_node_budget(self, attempt: dict[str, Any]) -> None:
        assert self.budget is not None
        remaining = {
            key: self.budget["reserved"][key] - self.budget["consumed"][key]
            for key in self.budget["reserved"]
        }
        remaining["concurrency"] = 1
        self.node_budgets[attempt["attempt_id"]] = {
            "schema_version": "Natsume.task_runtime.node_budget_reservation.v1",
            "node_budget_id": f"node-budget::{attempt['attempt_id']}",
            "parent_budget_id": self.budget["budget_id"],
            "graph_id": attempt["graph_id"],
            "graph_revision": attempt["graph_revision"],
            "node_id": attempt["node_id"],
            "attempt_id": attempt["attempt_id"],
            "scope": self.budget["scope"],
            "limits": dict(self.budget["limits"]),
            "reserved": remaining,
            "consumed": {key: 0 for key in remaining},
            "attempted_consumed": {key: 0 for key in remaining},
            "usage_version": 0,
            "parent_version_at_claim": self.budget["status_version"],
            "claim_origin": "ATOMIC_READY_CLAIM",
            "attempt_status": attempt["status"],
            "status": "ACTIVE",
        }

    def consume_budget_once(self, budget_id: str, **kwargs: Any) -> _BudgetResult:
        usage_id = kwargs["usage_id"]
        if usage_id in self.budget_usage:
            prior = self.budget_usage[usage_id]
            return _BudgetResult(prior.accepted, dict(self.budget or {}), replayed=True)
        assert self.budget is not None
        delta = kwargs["delta"].to_dict()
        attempted = {
            key: self.budget["consumed"][key] + delta[key]
            for key in self.budget["consumed"]
        }
        dimensions = tuple(
            key for key, value in attempted.items() if value > self.budget["reserved"][key]
        )
        accepted = not dimensions
        if accepted:
            self.budget["consumed"] = attempted
        else:
            self.budget["status"] = "EXCEEDED"
            self.budget["exceeded_dimensions"] = list(dimensions)
        self.budget["status_version"] += 1
        child = self.node_budgets[kwargs["attempt_id"]]
        if accepted:
            child["consumed"] = {
                key: child["consumed"][key] + delta[key]
                for key in child["consumed"]
            }
        child["attempted_consumed"] = {
            key: child["attempted_consumed"][key] + delta[key]
            for key in child["attempted_consumed"]
        }
        child["usage_version"] += 1
        result = _BudgetResult(accepted, dict(self.budget), dimensions)
        self.budget_usage[usage_id] = result
        return result

    def transition_graph(self, graph_id: str, revision: int, *, expected_version: int, target_status: str, outbox: Any) -> dict[str, Any]:
        assert expected_version == self.graph["status_version"]
        self.calls.append(f"graph:{target_status}")
        self.graph["status"] = target_status
        self.graph["status_version"] += 1
        return dict(self.graph)

    def transition_node(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        *,
        expected_version: int,
        target_status: str,
        outbox: Any,
        verification_result: Any = None,
        acceptance_judgement: Any = None,
    ) -> dict[str, Any]:
        assert expected_version == self.node["status_version"]
        self.calls.append(f"node:{target_status}")
        self.node["status"] = target_status
        self.node["status_version"] += 1
        self.node["node"]["status"] = target_status
        self.node["node"]["status_version"] = self.node["status_version"]
        return self.get_node(graph_id, revision, node_id)

    def commit_node_acceptance(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: Any,
        acceptance_judgement: Any,
        attempt_id: str,
        receipt_ref: str,
        worker_output: Any,
        effect_intents: Any = (),
    ) -> dict[str, Any]:
        del outbox, acceptance_judgement, worker_output
        node = self.transition_node(
            graph_id,
            revision,
            node_id,
            expected_version=expected_version,
            target_status="ACCEPTED",
            outbox=None,
        )
        return {
            "node": node,
            "artifact": {
                "graph_id": graph_id,
                "graph_revision": revision,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "receipt_ref": receipt_ref,
            },
            "effect_intents": tuple(effect_intents),
            "decision": {
                "acceptance_ref": f"task-acceptance://fake/{attempt_id}",
                "target_status": "ACCEPTED",
                "attempt_id": attempt_id,
                "node_version_before": expected_version,
                "node_version_after": expected_version + 1,
                "review_id": None,
                "graph_outbox_id": None,
            },
            "replayed": False,
        }

    def commit_node_acceptance_review(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        *,
        attempt_id: str,
        expected_node_version: int,
        acceptance_judgement: Any,
        node_outbox: Any,
        review_item: Any,
        work_order_id: str,
        expected_graph_version: int | None = None,
        graph_outbox: Any = None,
    ) -> dict[str, Any]:
        del acceptance_judgement, node_outbox, work_order_id
        node = self.transition_node(
            graph_id,
            revision,
            node_id,
            expected_version=expected_node_version,
            target_status="WAITING_ACCEPTANCE_REVIEW",
            outbox=None,
        )
        if expected_graph_version is not None:
            graph = self.transition_graph(
                graph_id,
                revision,
                expected_version=expected_graph_version,
                target_status="BLOCKED",
                outbox=graph_outbox,
            )
        else:
            graph = dict(self.graph)
        decision = {
            "acceptance_ref": f"task-acceptance://fake/{attempt_id}/review",
            "target_status": "WAITING_ACCEPTANCE_REVIEW",
            "attempt_id": attempt_id,
            "node_version_before": expected_node_version,
            "node_version_after": expected_node_version + 1,
            "review_id": review_item.review_id,
            "graph_outbox_id": (
                "fake-graph-outbox"
                if expected_graph_version is not None
                else None
            ),
            "evidence_refs": tuple(
                getattr(review_item, "evidence_refs", ())
            ),
        }
        self.acceptance_decisions[expected_node_version + 1] = decision
        review = {
            "review": {
                "review_id": review_item.review_id,
                "review_type": "POST_EXECUTION_ACCEPTANCE",
                "resume_phase": "POST_EXECUTION_ACCEPTANCE",
                "subject_ref": review_item.subject_ref,
                "subject_version": review_item.subject_version,
                "checkpoint_id": review_item.checkpoint_id,
                "status": "PENDING",
            }
        }
        return {
            "node": node,
            "graph": graph,
            "review": review,
            "decision": decision,
            "replayed": False,
        }

    def get_node_acceptance_decision(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        *,
        node_version_after: int | None = None,
    ) -> dict[str, Any]:
        del graph_id, revision, node_id
        version = (
            int(node_version_after)
            if node_version_after is not None
            else max(self.acceptance_decisions)
        )
        return dict(self.acceptance_decisions[version])


    def create_attempt(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("attempt:CREATED")
        attempt = {
            "attempt_id": kwargs["attempt_id"],
            "graph_id": kwargs["graph_id"],
            "graph_revision": kwargs["graph_revision"],
            "node_id": kwargs["node_id"],
            "attempt_no": kwargs["attempt_no"],
            "status": "CREATED",
            "status_version": 0,
        }
        self.attempts[attempt["attempt_id"]] = attempt
        self._bind_node_budget(attempt)
        return dict(attempt)

    def claim_ready_node(self, **kwargs: Any) -> dict[str, Any]:
        assert self.budget is not None
        assert kwargs["budget_id"] == self.budget["budget_id"]
        assert kwargs["expected_attempt_count"] == len(self.attempts)
        self.calls.append("attempt:CREATED")
        attempt = {
            "attempt_id": kwargs["attempt_id"],
            "graph_id": kwargs["graph_id"],
            "graph_revision": kwargs["graph_revision"],
            "node_id": kwargs["node_id"],
            "attempt_no": len(self.attempts) + 1,
            "status": "LEASED",
            "status_version": 1,
        }
        self.attempts[attempt["attempt_id"]] = attempt
        self._bind_node_budget(attempt)
        self.calls.append("attempt:LEASED")
        self.node["status"] = "LEASED"
        self.node["status_version"] += 1
        self.node["retry_not_before_ms"] = None
        self.node["node"]["status"] = "LEASED"
        self.node["node"]["status_version"] = self.node["status_version"]
        return {
            "attempt": dict(attempt),
            "lease": {
                "lease_id": "lease::one",
                "attempt_id": attempt["attempt_id"],
                "fencing_token": 1,
                "status_version": 0,
            },
            "node": self.get_node(
                kwargs["graph_id"],
                kwargs["graph_revision"],
                kwargs["node_id"],
            ),
        }

    def promote_retry_due(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        assert self.node["status"] == "RETRY_PENDING"
        self.node["status"] = "READY"
        self.node["status_version"] += 1
        self.node["retry_not_before_ms"] = None
        self.node["node"]["status"] = "READY"
        self.node["node"]["status_version"] = self.node["status_version"]
        return self.get_node(args[0], args[1], args[2])

    def acquire_lease(self, attempt_id: str, *, owner_id: str, ttl_ms: int, expected_attempt_version: int, outbox: Any) -> dict[str, Any]:
        attempt = self.attempts[attempt_id]
        assert expected_attempt_version == attempt["status_version"]
        self.calls.append("attempt:LEASED")
        attempt["status"] = "LEASED"
        attempt["status_version"] += 1
        self.node["status"] = "LEASED"
        self.node["status_version"] += 1
        self.node["node"]["status"] = "LEASED"
        self.node["node"]["status_version"] = self.node["status_version"]
        return {"lease_id": "lease::one", "fencing_token": 1, "status_version": 0}

    def reserve_operation(self, **kwargs: Any) -> _ReservationResult:
        self.calls.append("operation:RESERVED")
        self.reservation = {
            "reservation_id": kwargs["reservation_id"],
            "business_scope": kwargs["business_scope"],
            "operation_fingerprint": kwargs["operation_fingerprint"],
            "attempt_id": kwargs["attempt_id"],
            "lease_id": kwargs["lease_id"],
            "owner_id": kwargs["owner_id"],
            "fencing_token": kwargs["fencing_token"],
            "status": "RESERVED" if self.reservation_acquired else "RECOVERY_REQUIRED",
            "status_version": 0,
            "receipt_ref": None,
            "recovery_reason_code": None,
        }
        return _ReservationResult(self.reservation_acquired, dict(self.reservation))

    def mark_worker_interruption_recovery_required(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        reason_code: str,
    ) -> dict[str, Any]:
        assert self.reservation is not None
        assert reservation_id == self.reservation["reservation_id"]
        assert attempt_id == self.reservation["attempt_id"]
        assert lease_id == self.reservation["lease_id"]
        assert owner_id == self.reservation["owner_id"]
        assert fencing_token == self.reservation["fencing_token"]
        if self.reservation["status"] == "RECOVERY_REQUIRED":
            assert self.reservation["status_version"] == expected_version + 1
            assert self.reservation["recovery_reason_code"] == reason_code
            return dict(self.reservation)
        assert self.reservation["status"] == "RESERVED"
        assert self.reservation["status_version"] == expected_version
        self.calls.append("operation:RECOVERY_REQUIRED")
        self.reservation["status"] = "RECOVERY_REQUIRED"
        self.reservation["status_version"] += 1
        self.reservation["recovery_reason_code"] = reason_code
        return dict(self.reservation)

    def record_operation_recovery_hold(self, **kwargs: Any) -> dict[str, Any]:
        assert self.reservation is not None
        assert kwargs["reservation_id"] == self.reservation["reservation_id"]
        assert kwargs["attempt_id"] == self.reservation["attempt_id"]
        assert self.reservation["status"] == "RECOVERY_REQUIRED"
        digest = "a" * 64
        return {
            "case_ref": f"operation-recovery-hold://sha256/{digest}",
            "subject_sha256": f"sha256:{digest}",
            "case_kind": "UNKNOWN_SIDE_EFFECT",
            "current": True,
            "exact_replay": True,
        }

    def get_current_operation_recovery_hold_for_reservation(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        reason_code: str,
    ) -> dict[str, Any]:
        assert self.reservation is not None
        assert reservation_id == self.reservation["reservation_id"]
        assert expected_version == self.reservation["status_version"]
        assert reason_code == self.reservation["recovery_reason_code"]
        return self.record_operation_recovery_hold(
            reservation_id=reservation_id,
            attempt_id=self.reservation["attempt_id"],
        )

    def transition_attempt(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        target_status: str,
        outbox: Any,
        error_ref: str | None = None,
    ) -> dict[str, Any]:
        attempt = self.attempts[attempt_id]
        assert expected_version == attempt["status_version"]
        self.calls.append(f"attempt:{target_status}")
        attempt["status"] = target_status
        attempt["status_version"] += 1
        return dict(attempt)

    def commit_node_verification(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        target = str(kwargs["target_status"])
        node = self.transition_node(
            graph_id,
            revision,
            node_id,
            expected_version=kwargs["expected_node_version"],
            target_status=target,
            outbox=kwargs["node_outbox"],
            verification_result=kwargs["verification_result"],
        )
        self.verification_decision = {
            "verification_ref": "task-verification://fake/current",
            "graph_id": graph_id,
            "graph_revision": revision,
            "node_id": node_id,
            "attempt_id": kwargs["attempt_id"],
            "node_version_after": node["status_version"],
            "target_status": target,
            "verification_result": dict(kwargs["verification_result"]),
        }
        return {
            "node": node,
            "decision": dict(self.verification_decision),
            "replayed": False,
        }

    def get_node_verification_decision(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del graph_id, revision, node_id, kwargs
        assert self.verification_decision is not None
        return dict(self.verification_decision)

    def finalize_node_verification_failure(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        committed = self.commit_node_verification(
            graph_id,
            revision,
            node_id,
            attempt_id=kwargs["attempt_id"],
            reservation_id=kwargs["reservation_id"],
            expected_node_version=kwargs["expected_node_version"],
            target_status="VERIFICATION_FAILED",
            verification_result=kwargs["verification_result"],
            node_outbox=kwargs["node_outbox"],
        )
        assert self.budget is not None
        assert kwargs["expected_budget_version"] == self.budget["status_version"]
        self.budget["status"] = "RELEASED"
        self.budget["status_version"] += 1
        assert kwargs["expected_graph_version"] == self.graph["status_version"]
        self.calls.append("graph:FAILED")
        self.graph["status"] = "FAILED"
        self.graph["status_version"] += 1
        return {
            **committed,
            "graph": dict(self.graph),
            "budget": dict(self.budget),
        }

    def fail_running_attempt_bundle(self, attempt_id: str, **kwargs: Any) -> dict[str, Any]:
        attempt = self.attempts[attempt_id]
        assert kwargs["expected_attempt_version"] == attempt["status_version"]
        assert kwargs["expected_node_version"] == self.node["status_version"]
        assert kwargs["expected_graph_version"] == self.graph["status_version"]
        self.calls.append("attempt:FAILED")
        attempt["status"] = "FAILED"
        attempt["status_version"] += 1
        self.calls.append("node:FAILED")
        self.node["status"] = "FAILED"
        self.node["status_version"] += 1
        self.node["node"]["status"] = "FAILED"
        self.node["node"]["status_version"] = self.node["status_version"]
        target_status = str(kwargs["graph_target_status"])
        self.calls.append(f"graph:{target_status}")
        self.graph["status"] = target_status
        self.graph["status_version"] += 1
        result = {
            "attempt": dict(attempt),
            "node": self.get_node(
                self.graph["graph_id"],
                self.graph["revision"],
                self.node["node_id"],
            ),
            "graph": dict(self.graph),
            "lease": {
                "lease_id": "lease::one",
                "status": "RELEASED",
                "status_version": 1,
            },
        }
        if "budget_id" in kwargs:
            assert self.budget is not None
            assert kwargs["budget_id"] == self.budget["budget_id"]
            assert kwargs["expected_budget_version"] == self.budget["status_version"]
            self.budget["status"] = "RELEASED"
            self.budget["status_version"] += 1
            result["budget"] = dict(self.budget)
        return result

    def commit_operation(self, reservation_id: str, **kwargs: Any) -> dict[str, Any]:
        assert self.reservation is not None
        assert kwargs["expected_version"] == self.reservation["status_version"]
        self.calls.append("operation:COMMITTED")
        self.reservation["status"] = "COMMITTED"
        self.reservation["status_version"] += 1
        self.reservation["receipt_ref"] = kwargs["receipt_ref"]
        return dict(self.reservation)


class _FakeReviewRuntime:
    @staticmethod
    def pre_execution_required_nodes(
        work_order: Any,
        *,
        node_ids: Any,
    ) -> frozenset[str]:
        del work_order, node_ids
        return frozenset()

    @staticmethod
    def prepare_post_execution_acceptance_review(
        *,
        context: Any,
    ) -> Any:
        return type(
            "_PreparedReview",
            (),
            {
                "review_id": "task_review_fake_acceptance",
                "subject_ref": (
                    f"task-node://{context.graph_id}/"
                    f"{context.graph_revision}/{context.node_id}"
                    f"?attempt={context.attempt_id}"
                ),
                "subject_version": context.node_status_version,
                "checkpoint_id": (
                    f"task-checkpoint://{context.graph_id}/"
                    f"{context.graph_revision}/{context.node_id}/"
                    "post_execution_acceptance"
                ),
                "evidence_refs": context.evidence_refs,
            },
        )()

    @staticmethod
    def gate_from_record(record: Any) -> TaskNodeReviewGate:
        review = dict(record["review"])
        return TaskNodeReviewGate(
            disposition="WAITING_REVIEW",
            allowed=False,
            review_id=review["review_id"],
            review_status=review["status"],
            review_type=review["review_type"],
            resume_phase=review["resume_phase"],
            subject_ref=review["subject_ref"],
            subject_version=int(review["subject_version"]),
            checkpoint_id=review["checkpoint_id"],
        )

    def post_execution_acceptance_gate(
        self,
        *,
        context: Any,
    ) -> TaskNodeReviewGate:
        prepared = self.prepare_post_execution_acceptance_review(
            context=context,
        )
        return self.gate_from_record(
            {
                "review": {
                    "review_id": prepared.review_id,
                    "status": "PENDING",
                    "review_type": "POST_EXECUTION_ACCEPTANCE",
                    "resume_phase": "POST_EXECUTION_ACCEPTANCE",
                    "subject_ref": prepared.subject_ref,
                    "subject_version": prepared.subject_version,
                    "checkpoint_id": prepared.checkpoint_id,
                }
            }
        )


class _Repository:
    def __init__(self, *, replayed: bool = False) -> None:
        self.replayed = replayed

    def admit(self, command: Any, work_order: Any, assignment: Any, graph: Any) -> dict[str, Any]:
        return {
            "command": command,
            "work_order": work_order,
            "assignment": assignment,
            "graph": graph,
            "created": not self.replayed,
            "replayed": self.replayed,
        }


class _TracingRepository(_Repository):
    def __init__(self, calls: list[str], *, replayed: bool = False) -> None:
        super().__init__(replayed=replayed)
        self.calls = calls

    def admit(self, command: Any, work_order: Any, assignment: Any, graph: Any) -> dict[str, Any]:
        self.calls.append("admit")
        return super().admit(command, work_order, assignment, graph)


class _IntentAdmissionVerifier:
    def __init__(
        self,
        calls: list[str],
        *,
        error: BaseException | None = None,
        prepared_set: bool = False,
    ) -> None:
        self.calls = calls
        self.error = error
        self.prepared_set = prepared_set

    def has_prepared_intent_receipt_set(self, **kwargs: Any) -> bool:
        del kwargs
        self.calls.append("intent-prepared-proof")
        return self.prepared_set

    def verify_intent_admission(self, **kwargs: Any) -> tuple[dict[str, str], ...]:
        self.calls.append("intent")
        if self.error is not None:
            raise self.error
        graph = kwargs["graph"]
        return tuple({"task_node_id": node.node_id} for node in graph.nodes)


class _Worker:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def execute_stream(self, **kwargs: Any):
        self.calls.append(f"worker:{self.name}")
        yield {"phase": "RENDER", "chunk": self.name}
        yield {"phase": "COMMIT", "done": True, "decision": "APPROVE", "narrative": self.name}


class _Resolver:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.workers = {
            "canonical": _Worker("canonical", calls),
            "legacy": _Worker("legacy", calls),
        }

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        self.calls.append(f"resolve:{executor}:{worker_route}")
        return self.workers[executor]


class _Verifier:
    def __init__(self, status: str) -> None:
        self.status = status

    def verify(self, *, execution_context: dict[str, Any], evidence_refs: list[str] | tuple[str, ...], **kwargs: Any) -> dict[str, Any]:
        passed = self.status == "PASS"
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": "verify-governed",
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "required": True,
            "status": self.status,
            "reason_code": None if passed else "WORKER_EVIDENCE_REJECTED",
            "evidence_refs": list(evidence_refs),
            "checks": [],
            "started_at_ms": 1,
            "finished_at_ms": 2,
        }


class _Acceptance:
    def __init__(self, decision: str = "ACCEPT") -> None:
        self.decision = decision

    def judge(self, *, execution_context: dict[str, Any], evidence_refs: list[str] | tuple[str, ...], **kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": "judge-governed",
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": self.decision,
            "reason_code": (
                None
                if self.decision == "ACCEPT"
                else "ACCEPTANCE_REVIEW_REQUIRED"
            ),
            "evidence_refs": list(evidence_refs),
            "criteria": [],
            "critic_candidate": None,
            "started_at_ms": 1,
            "finished_at_ms": 2,
        }


def _contracts(*, executor: TaskExecutor) -> tuple[Any, ...]:
    work_order = WorkOrderV3(
        work_order_id="wo-governed-1",
        domain="research",
        run_mode="research",
        objective="execute one governed command",
        workspace={"strategy": "temp_workspace"},
        root_work_order_id="wo-governed-1",
        task_graph_id="graph-governed-1",
        task_node_id="node-one",
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="work-order:wo-governed-1",
            limits=BudgetAmount(
                token_count=10_000,
                tool_call_count=10,
                time_ms=60_000,
                concurrency=1,
                retry_count=3,
                model_cost_micros=1_000_000,
            ),
        ),
    )
    graph = TaskGraph(
        graph_id="graph-governed-1",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://governed/one-node",
        status=TaskGraphStatus.CREATED,
        created_at=1,
        result_node_id="node-one",
        nodes=(
            TaskNode(
                node_id="node-one",
                dependencies=(),
                route="executive_runtime",
                input_refs=(),
                output_contract={"type": "object"},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-governed-1",
        session_id="session-governed-1",
        principal_id="principal-governed-1",
        input_ref="command-input://request-governed-1",
        input_sha256="1" * 64,
        root_work_order_id=work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="chat",
        target_id="executive_runtime",
        business_scope="host-chat-command",
        operation_fingerprint="2" * 64,
        created_at_ms=1,
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=executor,
        assignment_id=f"runtime-assignment://{work_order.work_order_id}/{executor.value}",
        assigned_at_ms=1,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    return command, work_order, assignment, graph


def _application(
    *,
    graph: TaskGraph,
    calls: list[str],
    verification_status: str,
    acceptance_decision: str = "ACCEPT",
    reservation_acquired: bool = True,
) -> GovernedCommandApplication:
    store = _FakeTaskStore(
        graph,
        calls,
        reservation_acquired=reservation_acquired,
    )
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier(verification_status),
        acceptance=_Acceptance(acceptance_decision),
        review_runtime=(
            _FakeReviewRuntime()
            if acceptance_decision == "REVIEW"
            else None
        ),
        owner_id="worker-one",
    )
    return GovernedCommandApplication(command_repository=_Repository(), coordinator=coordinator)


def test_admitted_execution_preserves_result_and_node_risk_authority() -> None:
    command, _, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)

    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )

    assert admitted.result_node_id == "node-one"
    assert admitted.for_node("node-one").result_node_id == "node-one"
    assert admitted.active_node.risk_level == 1


@pytest.mark.asyncio
async def test_intent_admission_runs_after_durable_admit_and_before_worker() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_TracingRepository(calls),
        coordinator=coordinator,
        intent_admission_verifier=_IntentAdmissionVerifier(calls),
    )

    _ = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert calls[:2] == ["admit", "intent"]
    assert calls.index("intent") < next(
        index for index, value in enumerate(calls) if value.startswith("worker:")
    )


@pytest.mark.asyncio
async def test_intent_block_fails_closed_before_worker_or_budget_mutation() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )

    class _Blocked(RuntimeError):
        reason_code = "INTENT_ADMISSION_BLOCKED"
        detail = "canonical node is blocked"

    app = GovernedCommandApplication(
        command_repository=_TracingRepository(calls),
        coordinator=coordinator,
        intent_admission_verifier=_IntentAdmissionVerifier(
            calls,
            error=_Blocked(),
        ),
    )

    with pytest.raises(GovernedRuntimeError) as blocked:
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert blocked.value.reason_code == "INTENT_ADMISSION_BLOCKED"
    assert calls == ["admit", "intent"]
    assert store.budget is None


@pytest.mark.asyncio
async def test_direct_intent_block_is_durably_cancelled_and_replay_is_terminal() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []

    class _Blocked(RuntimeError):
        reason_code = "INTENT_ADMISSION_BLOCKED"
        detail = "canonical node is directly blocked"

    class _DirectBlockVerifier(_IntentAdmissionVerifier):
        def resolve_terminal_intent_block(self, **kwargs: Any) -> dict[str, Any]:
            calls.append("intent-block-proof")
            assert kwargs["command"] is command
            assert kwargs["work_order"] is work_order
            assert kwargs["graph"] is graph
            return {
                "command_id": command.command_id,
                "task_graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "blocked_node_ids": [graph.nodes[0].node_id],
                "request_id": "intent-block-terminal-request",
                "request_identity": "intent-block://sha256/" + "a" * 64,
                "reason_code": "INTENT_ADMISSION_BLOCKED",
            }

    class _TerminalCoordinator:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        async def request_graph_cancellation(
            self,
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.requests.append(dict(kwargs))
            calls.append("intent-block-cancelled")
            return {
                "done": True,
                "decision": "CANCELLED",
                "reason_code": "INTENT_ADMISSION_BLOCKED",
                "execution_receipt": {
                    "graph_id": graph.graph_id,
                    "graph_revision": graph.revision,
                    "accepted": False,
                    "status": "CANCELLED",
                    "reason_code": "INTENT_ADMISSION_BLOCKED",
                },
            }

    coordinator = _TerminalCoordinator()
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,  # type: ignore[arg-type]
        intent_admission_verifier=_DirectBlockVerifier(
            calls,
            error=_Blocked(),
        ),
    )
    first_admission = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
        replayed=False,
    )

    with pytest.raises(GovernedRuntimeError) as blocked:
        await app._verify_intent_admission(  # noqa: SLF001
            admitted=first_admission,
            command=command,
            work_order=work_order,
            graph=graph,
        )

    assert blocked.value.reason_code == "INTENT_ADMISSION_BLOCKED"
    assert coordinator.requests[0]["source"] == "SYSTEM"
    assert coordinator.requests[0]["reason_code"] == "INTENT_ADMISSION_BLOCKED"

    replay_admission = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
        replayed=True,
    )
    terminal = await app._verify_intent_admission(  # noqa: SLF001
        admitted=replay_admission,
        command=command,
        work_order=work_order,
        graph=graph,
    )

    assert terminal is not None
    assert terminal["decision"] == "CANCELLED"
    assert len(coordinator.requests) == 2


@pytest.mark.asyncio
async def test_exact_intent_receipt_replay_skips_expired_parent_preflight() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    grant_ref = "governor://request-governed-1/decision/frozen"
    command = replace(command, governor_grant_ref=grant_ref)
    work_order = replace(work_order, approval_ref=grant_ref)
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )

    class _ExactReplayRepository(_TracingRepository):
        def get(self, command_id: str) -> dict[str, Any] | None:
            assert command_id == command.command_id
            return {
                "command": command,
                "work_order": work_order,
                "assignment": assignment,
                "graph": graph,
            }

    class _ExpiredGrantService:
        def validate_parent_binding(self, **kwargs: Any) -> None:
            del kwargs
            calls.append("parent-preflight")
            raise RuntimeError("expired parent grant")

    class _ReceiptBlocked(RuntimeError):
        reason_code = "INTENT_ADMISSION_BLOCKED"
        detail = "replayed durable receipt remains blocked"

    app = GovernedCommandApplication(
        command_repository=_ExactReplayRepository(calls, replayed=True),
        coordinator=coordinator,
        node_grant_service=_ExpiredGrantService(),
        intent_admission_verifier=_IntentAdmissionVerifier(
            calls,
            error=_ReceiptBlocked(),
            prepared_set=True,
        ),
    )

    with pytest.raises(GovernedRuntimeError) as blocked:
        await app.execute(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )

    assert blocked.value.reason_code == "INTENT_ADMISSION_BLOCKED"
    assert calls == ["intent-prepared-proof", "admit", "intent"]


@pytest.mark.asyncio
async def test_matching_command_without_prepared_intent_set_cannot_skip_preflight() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    grant_ref = "governor://request-governed-1/decision/frozen"
    command = replace(command, governor_grant_ref=grant_ref)
    work_order = replace(work_order, approval_ref=grant_ref)
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )

    class _ExactReplayRepository(_TracingRepository):
        def get(self, command_id: str) -> dict[str, Any] | None:
            assert command_id == command.command_id
            return {
                "command": command,
                "work_order": work_order,
                "assignment": assignment,
                "graph": graph,
            }

    class _GrantService:
        def validate_parent_binding(self, **kwargs: Any) -> None:
            del kwargs
            calls.append("parent-preflight")

    class _ReceiptBlocked(RuntimeError):
        reason_code = "INTENT_ADMISSION_BLOCKED"
        detail = "prepared set is intentionally absent"

    app = GovernedCommandApplication(
        command_repository=_ExactReplayRepository(calls, replayed=True),
        coordinator=coordinator,
        node_grant_service=_GrantService(),
        intent_admission_verifier=_IntentAdmissionVerifier(
            calls,
            error=_ReceiptBlocked(),
            prepared_set=False,
        ),
    )

    with pytest.raises(GovernedRuntimeError):
        await app.execute(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )

    assert calls == [
        "intent-prepared-proof",
        "parent-preflight",
        "admit",
        "intent",
    ]


@pytest.mark.asyncio
async def test_coordinator_durable_identity_rejects_result_authority_drift() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )
    store.graph = {**store.graph, "graph": dict(store.graph["graph"])}
    store.graph["graph"]["result_node_id"] = None

    with pytest.raises(GovernedRuntimeError) as error:
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert error.value.reason_code == "GOVERNED_DURABLE_STATE_CONFLICT"
    assert calls == []


@pytest.mark.asyncio
async def test_dag_schema_is_checked_before_budget_mutation() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    def _reject_stale_schema() -> None:
        calls.append("schema:rejected")
        raise RuntimeError("migration 0089 missing")

    store.assert_dag_scheduling_ready = _reject_stale_schema  # type: ignore[method-assign]
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="migration 0089 missing"):
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert calls == ["schema:rejected"]
    assert store.budget is None


@pytest.mark.asyncio
async def test_parallel_decision_schema_is_checked_before_budget_or_worker() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    def _reject_stale_schema() -> None:
        calls.append("parallel-schema:rejected")
        raise RuntimeError("migration 0090 missing")

    store.assert_parallel_settlement_decision_ready = _reject_stale_schema  # type: ignore[method-assign]
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="migration 0090 missing"):
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert calls == ["parallel-schema:rejected"]
    assert store.budget is None


@pytest.mark.asyncio
async def test_resolver_cancellation_is_not_converted_to_terminal_failure() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    class _CancelledResolver:
        @staticmethod
        def resolve(*, executor: str, worker_route: str) -> Any:
            del executor, worker_route
            raise asyncio.CancelledError

    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_CancelledResolver(),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    with pytest.raises(asyncio.CancelledError):
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert "attempt:FAILED" not in calls
    assert "node:FAILED" not in calls
    assert "graph:FAILED" not in calls
    assert "operation:RESERVED" not in calls


@pytest.mark.asyncio
async def test_worker_self_cancellation_enters_durable_recovery_without_graph_intent() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    class _SelfCancellingWorker:
        @staticmethod
        async def execute_stream(**kwargs: Any):
            del kwargs
            raise asyncio.CancelledError
            if False:
                yield {}

    class _SelfCancellingResolver:
        @staticmethod
        def resolve(*, executor: str, worker_route: str) -> Any:
            del executor, worker_route
            return _SelfCancellingWorker()

    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_SelfCancellingResolver(),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert (
        receipt["projection"]["worker_failure_reason_code"]
        == "GOVERNED_WORKER_SELF_CANCELLED"
    )
    assert store.reservation is not None
    assert store.reservation["status"] == "RECOVERY_REQUIRED"
    assert (
        store.reservation["recovery_reason_code"]
        == "GOVERNED_WORKER_SELF_CANCELLED"
    )
    assert list(store.attempts.values())[-1]["status"] == "RUNNING"
    assert store.get_graph_cancellation_for_graph(graph.graph_id, graph.revision) is None
    assert "attempt:FAILED" not in calls
    assert "node:FAILED" not in calls
    assert "graph:FAILED" not in calls


@pytest.mark.asyncio
async def test_caller_cancellation_marks_operation_before_propagating() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    worker_started = asyncio.Event()

    class _BlockingWorker:
        @staticmethod
        async def execute_stream(**kwargs: Any):
            del kwargs
            worker_started.set()
            await asyncio.Event().wait()
            yield {"phase": "COMMIT", "decision": "APPROVE"}

    class _BlockingResolver:
        @staticmethod
        def resolve(*, executor: str, worker_route: str) -> Any:
            del executor, worker_route
            return _BlockingWorker()

    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_BlockingResolver(),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    async def consume() -> list[dict[str, Any]]:
        return [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    execution = asyncio.create_task(consume())
    await asyncio.wait_for(worker_started.wait(), timeout=1.0)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert store.reservation is not None
    assert store.reservation["status"] == "RECOVERY_REQUIRED"
    assert store.reservation["recovery_reason_code"] == "GOVERNED_CALLER_CANCELLED"
    assert "attempt:FAILED" not in calls
    assert "node:FAILED" not in calls
    assert "graph:FAILED" not in calls


@pytest.mark.asyncio
async def test_worker_private_reason_is_not_persisted_or_projected() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    class _PrivateWorkerError(RuntimeError):
        reason_code = "provider-secret: do-not-store"

    class _PrivateReasonWorker:
        @staticmethod
        async def execute_stream(**kwargs: Any):
            del kwargs
            raise _PrivateWorkerError
            if False:
                yield {}

    class _PrivateReasonResolver:
        @staticmethod
        def resolve(*, executor: str, worker_route: str) -> Any:
            del executor, worker_route
            return _PrivateReasonWorker()

    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_PrivateReasonResolver(),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert (
        receipt["projection"]["worker_failure_reason_code"]
        == "GOVERNED_WORKER_FAILED"
    )
    assert store.reservation is not None
    assert (
        store.reservation["recovery_reason_code"]
        == "GOVERNED_WORKER_FAILED"
    )
    assert "provider-secret" not in repr(receipt)
    assert "provider-secret" not in repr(store.reservation)


@pytest.mark.asyncio
async def test_zero_durable_time_budget_blocks_before_operation_or_worker() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    limits = replace(work_order.budget_policy.limits, time_ms=1)
    work_order = replace(
        work_order,
        budget_policy=replace(work_order.budget_policy, limits=limits),
    )
    calls: list[str] = []
    store = _FakeTaskStore(
        graph,
        calls,
        initial_time_consumed_ms=1,
    )
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "BUDGET_BLOCKED"
    assert receipt["reason_code"] == "GOVERNED_BUDGET_EXCEEDED"
    assert store.reservation is None
    assert store.budget is not None
    assert store.budget["status"] == "RELEASED"
    assert store.budget["exceeded_dimensions"] == ["time_ms"]
    assert "worker:canonical" not in calls
    assert not any(value.startswith("operation:") for value in calls)


@pytest.mark.asyncio
async def test_worker_timeout_error_is_not_misclassified_as_time_budget_timeout() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    class _WorkerTimeout:
        async def execute_stream(self, **kwargs: Any):
            del kwargs
            raise TimeoutError("worker-owned timeout")
            if False:
                yield {}

    class _WorkerTimeoutResolver:
        @staticmethod
        def resolve(*, executor: str, worker_route: str) -> Any:
            del executor, worker_route
            return _WorkerTimeout()

    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_WorkerTimeoutResolver(),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["projection"]["worker_failure_reason_code"] != (
        "GOVERNED_TIME_BUDGET_EXCEEDED"
    )


@pytest.mark.asyncio
async def test_worker_time_budget_timeout_preserves_operation_for_reconciliation() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    limits = replace(work_order.budget_policy.limits, time_ms=1)
    work_order = replace(
        work_order,
        budget_policy=replace(work_order.budget_policy, limits=limits),
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    class _SlowWorker:
        async def execute_stream(self, **kwargs: Any):
            del kwargs
            calls.append("worker:slow")
            await asyncio.Event().wait()
            yield {"phase": "COMMIT", "done": True, "decision": "APPROVE"}

    class _SlowResolver:
        @staticmethod
        def resolve(*, executor: str, worker_route: str) -> Any:
            del executor, worker_route
            return _SlowWorker()

    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_SlowResolver(),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert receipt["projection"]["worker_failure_reason_code"] == "GOVERNED_TIME_BUDGET_EXCEEDED"
    assert receipt["projection"]["time_budget_ms"] == 1
    assert store.reservation is not None
    assert store.reservation["status"] == "RECOVERY_REQUIRED"
    assert (
        store.reservation["recovery_reason_code"]
        == "GOVERNED_TIME_BUDGET_EXCEEDED"
    )
    assert store.budget is not None
    assert store.budget["status"] == "EXCEEDED"
    assert store.budget["exceeded_dimensions"] == ["time_ms"]
    assert "attempt:FAILED" not in calls
    assert "node:FAILED" not in calls
    assert "graph:FAILED" not in calls


@pytest.mark.asyncio
async def test_operation_collision_preserves_authority_for_reconciliation() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    app = _application(
        graph=graph,
        calls=calls,
        verification_status="PASS",
        reservation_acquired=False,
    )

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert [event["phase"] for event in events] == ["TERMINAL"]
    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert receipt["accepted"] is False
    assert "worker:canonical" not in calls
    assert "attempt:FAILED" not in calls
    assert "node:FAILED" not in calls
    assert "graph:BLOCKED" not in calls
    assert "budget:RELEASED" not in calls
    assert calls[-1] == "publish:200"


@pytest.mark.asyncio
async def test_canonical_assignment_drives_full_durable_happy_path_and_single_commit() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    app = _application(graph=graph, calls=calls, verification_status="PASS")

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert [event["phase"] for event in events] == ["RENDER", "COMMIT"]
    receipt = events[-1]["execution_receipt"]
    assert receipt["accepted"] is True
    assert receipt["executor"] == "canonical"
    assert calls.index("operation:COMMITTED") < calls.index("attempt:SUCCEEDED")
    assert calls.index("attempt:SUCCEEDED") < calls.index("node:EXECUTED")
    assert calls[-2:] == ["graph:COMPLETED", "publish:200"]
    assert "resolve:canonical:executive_runtime" in calls
    assert "worker:canonical" in calls


@pytest.mark.asyncio
async def test_missing_budget_policy_fails_before_worker_or_task_mutation() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    app = _application(graph=graph, calls=calls, verification_status="PASS")

    with pytest.raises(GovernedRuntimeError) as error:
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=replace(work_order, budget_policy=None),
                assignment=assignment,
                graph=graph,
            )
        ]

    assert error.value.reason_code == "GOVERNED_BUDGET_POLICY_REQUIRED"
    assert calls == []


@pytest.mark.asyncio
async def test_failed_verification_never_emits_commit() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    app = _application(graph=graph, calls=calls, verification_status="FAIL")

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert [event["phase"] for event in events] == ["RENDER", "TERMINAL"]
    assert events[-1]["decision"] == "REJECT"
    assert events[-1]["execution_receipt"]["status"] == "VERIFICATION_FAILED"
    assert "resolve:canonical:executive_runtime" in calls
    assert "worker:canonical" in calls
    assert calls.index("operation:COMMITTED") < calls.index("node:VERIFICATION_FAILED")
    assert calls[-2:] == ["graph:FAILED", "publish:200"]


@pytest.mark.asyncio
async def test_acceptance_review_reopens_durable_gate_without_reexecution() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    app = _application(
        graph=graph,
        calls=calls,
        verification_status="PASS",
        acceptance_decision="REVIEW",
    )

    first = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]
    replay = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    first_receipt = first[-1]["execution_receipt"]
    replay_receipt = replay[-1]["execution_receipt"]
    assert [event["phase"] for event in replay] == ["TERMINAL"]
    assert first[-1]["decision"] == "REJECT"
    assert first_receipt["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert replay[-1]["decision"] == "REJECT"
    assert replay[-1]["replayed"] is False
    assert replay_receipt["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert replay_receipt["verification_status"] == "PASS"
    assert replay_receipt["accepted"] is False
    assert replay_receipt["receipt_ref"] == first_receipt["receipt_ref"]
    assert calls.count("resolve:canonical:executive_runtime") == 1
    assert calls.count("worker:canonical") == 1
    assert calls.count("operation:RESERVED") == 1
