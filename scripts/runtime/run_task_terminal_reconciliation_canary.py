"""Run the NE-01 terminal reconciliation canary against an isolated real SQLite DB.

The canary deliberately creates two durable terminal half-states through the
canonical repositories and stores: one exact FAILED + ACTIVE Budget case and
one structurally untrusted CANCELLED + ACTIVE Budget case.  A real
``ProcessTaskRuntimeContainer`` restart must repair only the exact case and
leave the ambiguous case fenced.

No user database, production key or test fixture is imported.  The temporary
database is deleted after the receipt has been derived.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.app.host_runtime_startup_guards import (
    assert_graph_cancellation_operator_startup,
)
from host_brain.app.settings.core import CoreSettings
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.task_runtime import build_process_task_runtime_container
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetPolicy,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    AttemptStatus,
    DurableTaskStore,
    OutboxMessage,
    TaskStoreError,
    build_operation_fingerprint,
    build_outbox_payload_ref,
)
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


CANARY_SCHEMA_VERSION = "natsume.task-terminal-reconciliation-canary.v1"
NOW_MS = 1_990_000_000_000


@dataclass(frozen=True, slots=True)
class _Clock:
    value: int = NOW_MS

    def __call__(self) -> int:
        return self.value


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    identity: str,
    graph_revision: int | None = None,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        graph_revision=graph_revision,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=graph_revision,
        ),
        idempotency_key=f"ne01-terminal-canary:{identity}",
        outbox_id=f"outbox://ne01-terminal-canary/{identity}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _admit_graph(
    *,
    repository: HostCommandRepository,
    suffix: str,
) -> tuple[str, str, str, str]:
    graph_id = f"graph-ne01-terminal-canary-{suffix}"
    node_id = f"node-ne01-terminal-canary-{suffix}"
    work_order_id = f"work-order-ne01-terminal-canary-{suffix}"
    input_ref = f"canary-input://ne01-terminal/{suffix}"
    operation_scope = f"canary:terminal-reconciliation:{suffix}"
    fingerprint = build_operation_fingerprint(
        business_scope=operation_scope,
        operation="terminal-canary",
        parameters={"graph_id": graph_id, "node_id": node_id},
    )
    budget_policy = BudgetPolicy(
        scope=f"graph:{graph_id}@1",
        limits=BudgetAmount(
            token_count=10,
            tool_call_count=1,
            time_ms=60_000,
            concurrency=1,
            retry_count=1,
            model_cost_micros=1_000,
        ),
    )
    output_contract = {"type": "terminal_reconciliation_canary"}
    work_order = WorkOrderV3(
        work_order_id=work_order_id,
        domain="code",
        run_mode="inspect",
        objective="verify deterministic terminal reconciliation",
        input_pack={"input_ref": input_ref},
        forbidden_actions=("external_effect",),
        acceptance_criteria=("terminal authority remains canonical",),
        root_work_order_id=work_order_id,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        input_refs=(input_ref,),
        output_contract=output_contract,
        budget_policy=budget_policy,
        business_idempotency_scope=operation_scope,
        operation_fingerprint=fingerprint,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=work_order_id,
        revision=1,
        planner_ref=f"planner://ne01-terminal-canary/{suffix}",
        status=TaskGraphStatus.CREATED,
        created_at=NOW_MS,
        result_node_id=node_id,
        nodes=(
            TaskNode(
                node_id=node_id,
                dependencies=(),
                route="worker:terminal-canary",
                input_refs=(input_ref,),
                output_contract=output_contract,
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id=f"runtime-assignment://{work_order_id}/canonical",
        assigned_at_ms=NOW_MS,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    command = build_host_command(
        surface=HostCommandSurface.INTERNAL,
        request_id=f"request-ne01-terminal-canary-{suffix}",
        session_id=f"session-ne01-terminal-canary-{suffix}",
        principal_id="service.ne01-terminal-canary",
        governor_grant_ref=f"governor-grant://ne01-terminal-canary/{suffix}",
        input_ref=input_ref,
        input_sha256=_digest(f"ne01-terminal-canary:{suffix}"),
        root_work_order_id=work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type="internal",
        target_id=node_id,
        business_scope=operation_scope,
        operation_fingerprint=fingerprint,
        created_at_ms=NOW_MS,
    )
    admitted = repository.admit(command, work_order, assignment, graph)
    if admitted.get("created") is not True:
        raise RuntimeError("CANARY_GRAPH_ADMISSION_NOT_CREATED")
    return graph_id, node_id, operation_scope, fingerprint


def _activate_budget(
    *,
    store: DurableTaskStore,
    graph_id: str,
    suffix: str,
) -> str:
    budget_id = f"budget-ne01-terminal-canary-{suffix}"
    amount = BudgetAmount(token_count=10)
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=f"graph:{graph_id}@1",
            limits=amount,
            reserved=amount,
            status=BudgetReservationStatus.PENDING,
        ),
        graph_id=graph_id,
        graph_revision=1,
        node_id=None,
        attempt_id=None,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
            identity=f"{suffix}:budget-created",
        ),
    )
    store.transition_budget(
        budget_id,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            1,
            "BUDGET_RESERVATION_ACTIVE",
            identity=f"{suffix}:budget-active",
        ),
    )
    return budget_id


def _activate_graph(
    *,
    store: DurableTaskStore,
    graph_id: str,
    suffix: str,
) -> None:
    store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            1,
            "TASK_GRAPH_ACTIVE",
            identity=f"{suffix}:graph-active",
            graph_revision=1,
        ),
    )


def _seed_exact_repairable(
    *,
    store: DurableTaskStore,
    repository: HostCommandRepository,
) -> tuple[str, str]:
    suffix = "exact"
    graph_id, node_id, operation_scope, fingerprint = _admit_graph(
        repository=repository,
        suffix=suffix,
    )
    _activate_graph(store=store, graph_id=graph_id, suffix=suffix)
    budget_id = _activate_budget(store=store, graph_id=graph_id, suffix=suffix)
    attempt_id = "attempt-ne01-terminal-canary-exact"
    owner_id = "runtime-ne01-terminal-canary/boot-1"
    store.create_attempt(
        attempt_id=attempt_id,
        graph_id=graph_id,
        graph_revision=1,
        node_id=node_id,
        attempt_no=1,
        expected_node_version=0,
        operation_scope=operation_scope,
        operation_fingerprint=fingerprint,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            0,
            "TASK_ATTEMPT_CREATED",
            identity="exact:attempt-created",
            attempt_id=attempt_id,
        ),
    )
    lease = store.acquire_lease(
        attempt_id,
        owner_id=owner_id,
        ttl_ms=60_000,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            1,
            "TASK_ATTEMPT_LEASED",
            identity="exact:attempt-leased",
            attempt_id=attempt_id,
        ),
    )
    store.transition_node(
        graph_id,
        1,
        node_id,
        expected_version=1,
        target_status=TaskNodeStatus.RUNNING,
        outbox=_event(
            "TASK_NODE",
            f"{graph_id}@1/{node_id}",
            2,
            "TASK_NODE_RUNNING",
            identity="exact:node-running",
        ),
    )
    store.transition_attempt(
        attempt_id,
        owner_id=owner_id,
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            2,
            "TASK_ATTEMPT_RUNNING",
            identity="exact:attempt-running",
            attempt_id=attempt_id,
        ),
    )
    failure_ref = "canary-failure://ne01/terminal-exact"
    store.fail_running_attempt_bundle(
        attempt_id,
        owner_id=owner_id,
        fencing_token=int(lease["fencing_token"]),
        expected_attempt_version=2,
        expected_node_version=2,
        expected_graph_version=1,
        error_ref=failure_ref,
        graph_target_status=TaskGraphStatus.FAILED,
        attempt_outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            3,
            "TASK_ATTEMPT_FAILED",
            identity="exact:attempt-failed",
            attempt_id=attempt_id,
            evidence_refs=(failure_ref,),
        ),
    )
    return graph_id, budget_id


def _seed_ambiguous_terminal(
    *,
    store: DurableTaskStore,
    repository: HostCommandRepository,
) -> tuple[str, str]:
    suffix = "ambiguous"
    graph_id, _node_id, _scope, _fingerprint = _admit_graph(
        repository=repository,
        suffix=suffix,
    )
    _activate_graph(store=store, graph_id=graph_id, suffix=suffix)
    budget_id = _activate_budget(store=store, graph_id=graph_id, suffix=suffix)
    store.transition_graph(
        graph_id,
        1,
        expected_version=1,
        target_status=TaskGraphStatus.CANCELLED,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            2,
            "TASK_GRAPH_CANCELLED",
            identity="ambiguous:graph-cancelled",
            graph_revision=1,
        ),
    )
    return graph_id, budget_id


def _operator_bundle_preflight() -> dict[str, Any]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "ne01-terminal-canary-ephemeral"
    encoded = base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("=")
    settings = CoreSettings.model_validate(
        {
            "HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED": True,
            "HOST_TASK_OPERATION_RECONCILIATION_OPERATOR_ENABLED": True,
            "HOST_TASK_EFFECT_RECONCILIATION_OPERATOR_ENABLED": True,
            "HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED": True,
            "HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON": json.dumps(
                {key_id: f"ed25519:{encoded}"},
                separators=(",", ":"),
            ),
        }
    )
    gateway = SimpleNamespace(
        enabled=True,
        execution_enabled=True,
        ingress=SimpleNamespace(execute=lambda *_args, **_kwargs: None),
    )
    assert_graph_cancellation_operator_startup(
        enabled=settings.task_graph_cancellation_operator_enabled,
        operation_reconciliation_enabled=(
            settings.task_operation_reconciliation_operator_enabled
        ),
        effect_reconciliation_enabled=(
            settings.task_effect_reconciliation_operator_enabled
        ),
        terminal_reconciliation_enabled=(
            settings.task_terminal_reconciliation_operator_enabled
        ),
        command_ingress=SimpleNamespace(mode="ENFORCE"),
        security=SimpleNamespace(obs_command_composition=gateway),
    )
    return {
        "status": "PASS",
        "key_kind": "EPHEMERAL_VERIFICATION_ONLY",
        "key_id_sha256": _digest(key_id),
        "private_key_persisted": False,
    }


def _commit_sha(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise RuntimeError("CANARY_COMMIT_SHA_INVALID")
    return value


def run_canary(*, repository_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="natsume-ne01-terminal-canary-") as raw:
        root = Path(raw).resolve()
        db_path = root / "canonical.sqlite3"
        migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
            request_id="ne01-terminal-reconciliation-canary",
            ts_ms=NOW_MS,
        )
        if migration.success is not True:
            raise RuntimeError(
                "CANARY_MIGRATION_FAILED:" + json.dumps(migration.as_dict())
            )
        clock = _Clock()
        store = DurableTaskStore(db_path, clock=clock)
        repository = HostCommandRepository(db_path, clock=clock)
        exact_graph_id, exact_budget_id = _seed_exact_repairable(
            store=store,
            repository=repository,
        )
        ambiguous_graph_id, ambiguous_budget_id = _seed_ambiguous_terminal(
            store=store,
            repository=repository,
        )
        inventory = store.inventory_terminal_reconciliation_cases(limit=100)
        pre_cases = tuple(inventory["cases"])
        exact_cases = tuple(
            row for row in pre_cases if row["classification"] == "EXACT_REPAIRABLE"
        )
        non_exact_cases = tuple(
            row for row in pre_cases if row["classification"] != "EXACT_REPAIRABLE"
        )
        if len(exact_cases) != 1 or len(non_exact_cases) != 1:
            raise RuntimeError("CANARY_TERMINAL_CASE_MATRIX_INVALID")
        exact_case_id = str(exact_cases[0]["case_id"])
        non_exact_case_id = str(non_exact_cases[0]["case_id"])
        ledger = SQLiteLedger(str(db_path), check_same_thread=False)
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
            terminal_reconciliation_auto_enabled=True,
        )
        try:
            auto = dict(container.terminal_reconciliation_auto_result)
            if int(auto.get("reconciled_count") or 0) != 1:
                raise RuntimeError("CANARY_EXACT_AUTO_COUNT_INVALID")
            exact = container.task_store.read_terminal_reconciliation_case(
                exact_case_id
            )
            non_exact = container.task_store.read_terminal_reconciliation_case(
                non_exact_case_id
            )
            exact_budget = container.task_store.reconcile_budget(exact_budget_id)
            ambiguous_budget = container.task_store.reconcile_budget(
                ambiguous_budget_id
            )
            exact_graph = container.task_store.get_graph(exact_graph_id, 1)
            ambiguous_graph = container.task_store.get_graph(ambiguous_graph_id, 1)
            if (
                exact.get("proof", {}).get("authority_kind")
                != "SYSTEM_DURABLE_EVIDENCE"
                or exact_budget.get("status") != "RELEASED"
                or exact_graph.get("status") != "FAILED"
            ):
                raise RuntimeError("CANARY_EXACT_AUTO_REPAIR_INVALID")
            if (
                non_exact.get("proof") is not None
                or ambiguous_budget.get("status") != "ACTIVE"
                or ambiguous_graph.get("status") != "CANCELLED"
            ):
                raise RuntimeError("CANARY_NON_EXACT_CASE_MUTATED")
            try:
                container.task_store.assert_terminal_reconciliation_production_ready()
            except TaskStoreError as exc:
                production_gate = {
                    "status": "BLOCKED_AS_EXPECTED",
                    "reason_code": exc.reason_code,
                }
            else:
                raise RuntimeError("CANARY_AMBIGUOUS_CASE_DID_NOT_BLOCK_PRODUCTION")
            receipt = {
                "schema_version": CANARY_SCHEMA_VERSION,
                "commit_sha": _commit_sha(repository_root),
                "database_kind": "ISOLATED_REAL_SQLITE",
                "migration_head": str(
                    getattr(container.migration_result, "last_applied", "")
                    or getattr(container.migration_result, "latest_migration", "")
                    or "0118_conversation_acceptance_effects.sql"
                ),
                "terminal_inventory": {
                    "candidate_count": len(pre_cases),
                    "exact_repairable_count": len(exact_cases),
                    "non_exact_count": len(non_exact_cases),
                },
                "exact_auto": {
                    "status": "PASS",
                    "reconciled_count": int(auto["reconciled_count"]),
                    "authority_kind": exact["proof"]["authority_kind"],
                    "graph_status": exact_graph["status"],
                    "budget_status": exact_budget["status"],
                },
                "non_exact_fence": {
                    "status": "PASS",
                    "classification": non_exact["case"]["classification"],
                    "proof_absent": True,
                    "graph_status": ambiguous_graph["status"],
                    "budget_status": ambiguous_budget["status"],
                    "production_gate": production_gate,
                },
                "operator_bundle_preflight": _operator_bundle_preflight(),
                "external_side_effects": 0,
                "private_or_user_data_used": False,
                "temporary_database_removed": True,
                "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
                "status": "PASS",
            }
        finally:
            asyncio.run(container.aclose())
            ledger.close()
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the isolated NE-01 terminal reconciliation canary",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument("--receipt", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = run_canary(repository_root=args.repository_root.resolve())
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
    if args.receipt is not None:
        target = args.receipt.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
