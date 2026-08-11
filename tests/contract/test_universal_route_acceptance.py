from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    freeze_control_route_execution_policy,
)
from host_brain.persistence.universal_route_acceptance_reader import (
    UniversalRouteAcceptanceReader,
)
from host_brain.release.universal_route_acceptance import (
    PROGRAM_INCOMPLETE,
    ROUTE_FAIL,
    ROUTE_NOT_RUN,
    ROUTE_PASS,
    UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
    UNIVERSAL_ROUTE_ACCEPTANCE_MIGRATION_MISMATCH,
    UniversalRouteAcceptanceError,
    build_universal_route_acceptance_receipt,
    collect_universal_route_acceptance,
    validate_universal_route_acceptance_receipt,
)


COMMIT = "a" * 40
MIGRATION_HEAD = "0135_task_review_denial_settlement.sql"
NOW = 1_800_000_000_000
LIMITS = {
    "concurrency": 1,
    "model_cost_micros": 500_000,
    "retry_count": 1,
    "time_ms": 120_000,
    "token_count": 16_384,
    "tool_call_count": 8,
}
DELTA = {
    "concurrency": 0,
    "model_cost_micros": 25,
    "retry_count": 0,
    "time_ms": 20,
    "token_count": 12,
    "tool_call_count": 1,
}
def _source(*, migration_head: str = MIGRATION_HEAD) -> dict[str, object]:
    return {
        "commit_sha": COMMIT,
        "migration_head": migration_head,
        "repository_clean": True,
    }


def _snapshot(
    route: str = "chat",
    *,
    request_id: str = "private-request-value",
) -> tuple[dict[str, object], dict[str, str]]:
    graph_id = f"graph-{route}"
    node_id = f"node-{route}"
    attempt_id = f"attempt-{route}"
    budget_id = f"budget-{route}"
    node_budget_id = f"node-budget-{route}"
    operation_id = f"operation-{route}"
    operation_receipt = f"worker-projection://{route}/receipt"
    usage_outbox = f"outbox-{route}-usage"
    claim_outbox = f"outbox-{route}-claim"
    verification_outbox = f"outbox-{route}-verification"
    acceptance_outbox = f"outbox-{route}-acceptance"
    surface = route if route in {"scheduler", "internal", "operator"} else "chat"
    route_type = "delegate" if route in {"scheduler", "internal", "operator"} else route
    control_route_policies: list[dict[str, object]] = []
    if route in {"scheduler", "internal", "operator"}:
        control_route_policies.append(
            {
                **freeze_control_route_execution_policy(
                    surface=route,
                    control_command_id=f"acceptance.{route}.command",
                    target_id=f"{route}_runtime",
                    review_required=False,
                ),
                "admission_digests_valid": True,
                "binding_valid": True,
            }
        )
    raw = {
        "request_id": request_id,
        "commands": [
            {
                "command_id": f"command-{route}",
                "request_id": request_id,
                "surface": surface,
                "route_type": route_type,
                "graph_id": graph_id,
                "graph_revision": 1,
                "created_at_ms": NOW,
            }
        ],
        "control_route_policies": control_route_policies,
        "graphs": [
            {
                "graph_id": graph_id,
                "revision": 1,
                "status": "COMPLETED",
                "status_version": 2,
            }
        ],
        "nodes": [
            {
                "node_id": node_id,
                "status": "ACCEPTED",
                "status_version": 6,
            }
        ],
        "attempts": [
            {
                "attempt_id": attempt_id,
                "node_id": node_id,
                "attempt_no": 1,
                "status": "SUCCEEDED",
                "status_version": 3,
            }
        ],
        "leases": [
            {
                "lease_id": f"lease-{route}",
                "attempt_id": attempt_id,
                "status": "RELEASED",
                "status_version": 1,
            }
        ],
        "operations": [
            {
                "reservation_id": operation_id,
                "attempt_id": attempt_id,
                "status": "COMMITTED",
                "status_version": 1,
                "receipt_ref": operation_receipt,
            }
        ],
        "graph_budgets": [
            {
                "budget_id": budget_id,
                "status": "CONSUMED",
                "status_version": 2,
                "limits": dict(LIMITS),
                "reserved": dict(LIMITS),
                "consumed": dict(DELTA),
                "attempted_consumed": dict(DELTA),
                "exceeded_dimensions": [],
            }
        ],
        "budget_usage": [
            {
                "budget_id": budget_id,
                "usage_id": f"usage-{route}",
                "attempt_id": attempt_id,
                "delta": dict(DELTA),
                "accepted": 1,
                "exceeded_dimensions": [],
                "reservation_version_before": 1,
                "reservation_version_after": 2,
                "outbox_id": usage_outbox,
            }
        ],
        "node_budgets": [
            {
                "node_budget_id": node_budget_id,
                "parent_budget_id": budget_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "limits": dict(LIMITS),
                "reserved": dict(LIMITS),
                "consumed": dict(DELTA),
                "attempted_consumed": dict(DELTA),
                "exceeded_dimensions": [],
                "usage_version": 1,
                "claim_outbox_id": claim_outbox,
                "claim_origin": "ATOMIC_READY_CLAIM",
            }
        ],
        "node_budget_usage": [
            {
                "node_budget_id": node_budget_id,
                "parent_budget_id": budget_id,
                "usage_id": f"usage-{route}",
                "attempt_id": attempt_id,
                "accepted": 1,
                "child_version_before": 0,
                "child_version_after": 1,
                "parent_outbox_id": usage_outbox,
            }
        ],
        "verifications": [
            {
                "verification_ref": f"verification-{route}",
                "node_id": node_id,
                "attempt_id": attempt_id,
                "target_status": "VERIFIED",
                "result_sha256": "b" * 64,
                "node_outbox_id": verification_outbox,
            }
        ],
        "acceptances": [
            {
                "acceptance_ref": f"acceptance-{route}",
                "node_id": node_id,
                "attempt_id": attempt_id,
                "target_status": "ACCEPTED",
                "result_sha256": "c" * 64,
                "node_outbox_id": acceptance_outbox,
            }
        ],
        "artifacts": [
            {
                "artifact_ref": f"artifact://{route}/result",
                "node_id": node_id,
                "attempt_id": attempt_id,
                "receipt_ref": operation_receipt,
                "output_contract_sha256": "sha256:" + "d" * 64,
                "payload_sha256": "sha256:" + "e" * 64,
            }
        ],
        "worker_projections": [
            {
                "projection_id": f"projection-{route}",
                "command_id": f"command-{route}",
                "request_id": request_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "route_type": route_type,
                "decision": "APPROVE",
                "payload_digest": "sha256:" + "f" * 64,
            }
        ],
        "effects": [
            {
                "effect_id": f"effect-{route}",
                "node_id": node_id,
                "attempt_id": attempt_id,
                "effect_type": "acceptance.test_effect",
                "status": "COMMITTED",
                "status_version": 2,
                "receipt_ref": f"effect-receipt://{route}/result",
            }
        ],
        "outboxes": [
            {
                "outbox_id": outbox_id,
                "aggregate_type": "TEST",
                "aggregate_id": route,
                "aggregate_version": index,
                "event_type": "TEST_PUBLISHED",
                "attempt_id": attempt_id,
                "status": "PUBLISHED",
                "status_version": 1,
                "graph_revision": None,
            }
            for index, outbox_id in enumerate(
                (
                    usage_outbox,
                    claim_outbox,
                    verification_outbox,
                    acceptance_outbox,
                ),
                start=1,
            )
        ],
        "current_recovery_holds": [],
        "effect_reconciliation_cases": [],
        "cancellation_intents": [],
    }
    return (
        {
            "database_kind": "FILE_BACKED_CANONICAL_SQLITE",
            "read_mode": "SQLITE_MODE_RO_QUERY_ONLY_SINGLE_TRANSACTION",
            "migration": {
                "migration_id": 135,
                "name": "task_review_denial_settlement",
            },
            "routes": {route: raw},
        },
        {route: request_id},
    )


def _receipt(
    snapshot: dict[str, object] | None = None,
    request_ids: dict[str, str] | None = None,
) -> dict[str, object]:
    if snapshot is None or request_ids is None:
        snapshot, request_ids = _snapshot()
    return build_universal_route_acceptance_receipt(
        source_binding=_source(),
        snapshot=snapshot,
        request_ids=request_ids,
        generated_at_ms=NOW,
    )


def test_valid_route_is_pass_but_unrun_routes_keep_program_incomplete() -> None:
    receipt = _receipt()

    assert receipt["route_acceptance"]["chat"]["status"] == ROUTE_PASS
    assert receipt["route_acceptance"]["skill"]["status"] == ROUTE_NOT_RUN
    assert receipt["overall_status"] == PROGRAM_INCOMPLETE
    assert validate_universal_route_acceptance_receipt(receipt) == receipt
    raw = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "private-request-value" not in raw
    assert "request://sha256/" in raw


def test_chat_model_budget_lineage_reopens_provider_and_child_ledgers() -> None:
    snapshot, request_ids = _snapshot()
    route = snapshot["routes"]["chat"]
    usage_id = "worker-budget://chat-contract"
    measured = {
        "concurrency": 0,
        "model_cost_micros": 25,
        "retry_count": 0,
        "time_ms": 20,
        "token_count": 12,
        "tool_call_count": 0,
    }
    for key in (
        "consumed",
        "attempted_consumed",
    ):
        route["graph_budgets"][0][key] = dict(measured)
        route["node_budgets"][0][key] = dict(measured)
    route["budget_usage"][0].update(
        usage_id=usage_id,
        delta=dict(measured),
    )
    route["node_budget_usage"][0]["usage_id"] = usage_id
    route["worker_model_budgets"] = [
        {
            "projection_id": "projection-chat",
            "request_id": "private-request-value",
            "node_id": "node-chat",
            "task_attempt_id": "attempt-chat",
            "binding_valid": True,
            "success": True,
            "cached": False,
            "route_decision_id": "route-chat",
            "final_adapter_id": "adapter-chat",
            "final_provider": "provider-chat",
            "final_model": "model-chat",
            "model_attempt_ids": ["model-attempt-chat"],
            "measurement_ref": "model-budget://chat-contract",
            "measurement": {
                "attempt_count": 1,
                "retry_count": 0,
                "token_count": 12,
                "model_cost_micros": 25,
            },
            "usage_id": usage_id,
            "usage_evidence_refs": ["model-budget://chat-contract"],
            "usage_measurements": {
                "retry_count": 0,
                "token_count": 12,
                "model_cost_micros": 25,
            },
        }
    ]
    common_model = {
        "model_attempt_id": "model-attempt-chat",
        "request_id": "private-request-value",
        "route_decision_id": "route-chat",
        "attempt_index": 1,
        "adapter_id": "adapter-chat",
        "provider": "provider-chat",
        "model_id": "model-chat",
        "prompt_id": "surface.main_chain.live.text",
        "prompt_version": "v1.0.0",
        "prompt_digest": "1" * 64,
        "started_at_ms": NOW,
        "latency_ms": 1,
        "error_code": None,
        "model_node_id": "node-chat",
        "scene_type": "CHAT_SURFACE",
        "cognitive_tier": "A",
    }
    route["model_attempts"] = [
        {
            **common_model,
            "status": "started",
            "ended_at_ms": None,
            "output_digest": None,
            "provider_budget_measurements": {},
        },
        {
            **common_model,
            "status": "succeeded",
            "ended_at_ms": NOW + 1,
            "output_digest": "2" * 64,
            "provider_budget_measurements": {
                "prompt_tokens": 8,
                "completion_tokens": 4,
                "token_count": 12,
                "model_cost_micros": 25,
            },
        },
    ]

    receipt = _receipt(snapshot, request_ids)
    assert receipt["route_acceptance"]["chat"]["status"] == ROUTE_PASS

    route["worker_model_budgets"][0]["usage_measurements"][
        "token_count"
    ] = 11
    tampered = _receipt(snapshot, request_ids)
    assert tampered["route_acceptance"]["chat"]["status"] == ROUTE_FAIL
    assert "MODEL_BUDGET_MEASUREMENT_INVALID" in tampered[
        "route_acceptance"
    ]["chat"]["reason_codes"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda route: route["graphs"][0].update(status="ACTIVE"),
            "GRAPH_NOT_COMPLETED",
        ),
        (
            lambda route: route["verifications"].clear(),
            "VERIFICATION_DECISION_INVALID",
        ),
        (
            lambda route: route["acceptances"][0].update(
                target_status="WAITING_ACCEPTANCE_REVIEW"
            ),
            "ACCEPTANCE_DECISION_INVALID",
        ),
        (
            lambda route: route["graph_budgets"][0]["consumed"].update(
                token_count=999
            ),
            "GRAPH_BUDGET_PROJECTION_DRIFT",
        ),
        (
            lambda route: route["leases"][0].update(status="ACTIVE"),
            "LEASE_NONTERMINAL",
        ),
        (
            lambda route: route["effects"][0].update(status="PENDING"),
            "EFFECT_NOT_COMMITTED",
        ),
        (
            lambda route: route["outboxes"][0].update(status="PENDING"),
            "OUTBOX_NOT_PUBLISHED",
        ),
        (
            lambda route: route["current_recovery_holds"].append(
                {
                    "case_ref": "hold",
                    "reservation_id": "operation-chat",
                    "operation_status": "COMMITTED",
                    "operation_status_version": 1,
                    "cancellation_intent_ref": None,
                }
            ),
            "RECOVERY_HOLD_CURRENT",
        ),
    ],
)
def test_partial_or_nonterminal_runtime_facts_fail_closed(
    mutate,
    reason: str,
) -> None:
    snapshot, request_ids = _snapshot()
    route = snapshot["routes"]["chat"]
    mutate(route)

    receipt = _receipt(snapshot, request_ids)

    result = receipt["route_acceptance"]["chat"]
    assert result["status"] == ROUTE_FAIL
    assert reason in result["reason_codes"]


@pytest.mark.parametrize("route", ["scheduler", "internal", "operator"])
def test_surface_owned_routes_override_worker_route_type(route: str) -> None:
    snapshot, request_ids = _snapshot(route)
    receipt = _receipt(snapshot, request_ids)

    assert receipt["route_acceptance"][route]["status"] == ROUTE_PASS
    assert receipt["route_acceptance"][route]["evidence_counts"][
        "control_route_policies"
    ] == 1


@pytest.mark.parametrize("route", ["scheduler", "internal", "operator"])
def test_control_route_requires_reopened_frozen_execution_policy(
    route: str,
) -> None:
    snapshot, request_ids = _snapshot(route)
    snapshot["routes"][route]["control_route_policies"][0][
        "binding_valid"
    ] = False

    receipt = _receipt(snapshot, request_ids)

    result = receipt["route_acceptance"][route]
    assert result["status"] == ROUTE_FAIL
    assert "CONTROL_ROUTE_POLICY_BINDING_INVALID" in result["reason_codes"]


def test_non_control_route_rejects_smuggled_control_policy() -> None:
    snapshot, request_ids = _snapshot("chat")
    snapshot["routes"]["chat"]["control_route_policies"] = [
        {
            **freeze_control_route_execution_policy(
                surface="scheduler",
                control_command_id="acceptance.scheduler.smuggled",
                target_id="scheduler_runtime",
                review_required=False,
            ),
            "admission_digests_valid": True,
            "binding_valid": True,
        }
    ]

    receipt = _receipt(snapshot, request_ids)

    result = receipt["route_acceptance"]["chat"]
    assert result["status"] == ROUTE_FAIL
    assert "CONTROL_ROUTE_POLICY_UNEXPECTED" in result["reason_codes"]


def test_receipt_digest_and_privacy_declaration_are_tamper_evident() -> None:
    receipt = _receipt()
    tampered = deepcopy(receipt)
    tampered["route_acceptance"]["chat"]["evidence_counts"]["effects"] = 99

    with pytest.raises(
        UniversalRouteAcceptanceError,
        match="digest mismatch",
    ):
        validate_universal_route_acceptance_receipt(tampered)

    forbidden = deepcopy(receipt)
    forbidden["privacy"]["content"] = "leaked user text"
    with pytest.raises(UniversalRouteAcceptanceError) as raised:
        validate_universal_route_acceptance_receipt(forbidden)
    assert raised.value.reason_code == UNIVERSAL_ROUTE_ACCEPTANCE_INVALID


def test_synthetic_legacy_receipt_cannot_be_production_acceptance() -> None:
    legacy = {
        "schema_version": "natsume.universal-runtime-route-acceptance.v2",
        "fixture_kind": "SYNTHETIC_CONTRACT_ONLY_NOT_PRODUCTION_ACCEPTANCE",
        "commit_sha": COMMIT,
        "migration_head": MIGRATION_HEAD,
        "route_acceptance": {"chat": {"status": "PASS"}},
    }

    with pytest.raises(
        UniversalRouteAcceptanceError,
        match="fields are not canonical",
    ):
        validate_universal_route_acceptance_receipt(legacy)


def test_database_and_repository_migration_heads_must_match() -> None:
    snapshot, request_ids = _snapshot()

    with pytest.raises(UniversalRouteAcceptanceError) as raised:
        build_universal_route_acceptance_receipt(
            source_binding=_source(
                migration_head="0134_task_node_acceptance_decision.sql"
            ),
            snapshot=snapshot,
            request_ids=request_ids,
            generated_at_ms=NOW,
        )
    assert raised.value.reason_code == (
        UNIVERSAL_ROUTE_ACCEPTANCE_MIGRATION_MISMATCH
    )


def test_same_request_cannot_claim_two_routes() -> None:
    snapshot, _request_ids = _snapshot()

    with pytest.raises(UniversalRouteAcceptanceError, match="more than one route"):
        build_universal_route_acceptance_receipt(
            source_binding=_source(),
            snapshot=snapshot,
            request_ids={
                "chat": "same-request",
                "skill": "same-request",
            },
            generated_at_ms=NOW,
        )


def test_real_reader_is_query_only_and_missing_command_is_fail_closed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "canonical.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="universal-route-acceptance-reader",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    reader = UniversalRouteAcceptanceReader(db_path)
    with reader._read_operation() as connection:  # noqa: SLF001
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM host_command_journal")

    snapshot = reader.capture({"chat": "missing-request"})
    applied = snapshot["migration"]
    receipt = build_universal_route_acceptance_receipt(
        source_binding={
            "commit_sha": COMMIT,
            "migration_head": (
                f"{int(applied['migration_id']):04d}_{applied['name']}.sql"
            ),
            "repository_clean": True,
        },
        snapshot=snapshot,
        request_ids={"chat": "missing-request"},
        generated_at_ms=NOW,
    )
    result = receipt["route_acceptance"]["chat"]
    assert result["status"] == ROUTE_FAIL
    assert result["reason_codes"] == ["COMMAND_MISSING"]
    assert result["request_ref"].startswith("request://sha256/")
    assert set(result["evidence_counts"].values()) == {0}


def test_collect_rejects_a_dirty_tracked_worktree_before_reading_database(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    migration_root = repository / "host_brain" / "audit" / "migrations"
    migration_root.mkdir(parents=True)
    (migration_root / MIGRATION_HEAD).write_text("-- migration\n", encoding="utf-8")
    tracked = repository / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    import subprocess

    for command in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "acceptance@example.invalid"),
        ("git", "config", "user.name", "Acceptance Contract"),
        ("git", "add", "."),
        ("git", "commit", "-qm", "initial"),
    ):
        subprocess.run(
            command,
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    tracked.write_text("dirty\n", encoding="utf-8")

    with pytest.raises(UniversalRouteAcceptanceError, match="tracked worktree"):
        collect_universal_route_acceptance(
            repository_root=repository,
            canonical_db_path=repository / "missing.sqlite3",
            request_ids={"chat": "request"},
            generated_at_ms=NOW,
        )
