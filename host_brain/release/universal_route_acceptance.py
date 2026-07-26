"""Commit-bound acceptance receipts for all governed Host execution routes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from host_brain.persistence.universal_route_acceptance_reader import (
    UniversalRouteAcceptanceReader,
)


UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_VERSION = (
    "natsume.universal-runtime-route-acceptance.v3"
)
UNIVERSAL_ROUTE_ACCEPTANCE_INVALID = "UNIVERSAL_ROUTE_ACCEPTANCE_INVALID"
UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID = (
    "UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID"
)
UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_DIRTY = (
    "UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_DIRTY"
)
UNIVERSAL_ROUTE_ACCEPTANCE_MIGRATION_MISMATCH = (
    "UNIVERSAL_ROUTE_ACCEPTANCE_MIGRATION_MISMATCH"
)

ROUTE_PASS = "PASS"
ROUTE_FAIL = "FAIL"
ROUTE_NOT_RUN = "NOT_RUN"
PROGRAM_PASS = "PASS"
PROGRAM_INCOMPLETE = "INCOMPLETE"

REQUIRED_ROUTES = (
    "chat",
    "skill",
    "plan",
    "delegate",
    "scheduler",
    "internal",
    "operator",
)
_SURFACE_ROUTES = frozenset({"scheduler", "internal", "operator"})
_BUDGET_DIMENSIONS = frozenset(
    {
        "token_count",
        "tool_call_count",
        "time_ms",
        "concurrency",
        "retry_count",
        "model_cost_micros",
    }
)
_TERMINAL_ATTEMPT_STATUSES = frozenset(
    {"SUCCEEDED", "FAILED", "CANCELLED"}
)
_TERMINAL_LEASE_STATUSES = frozenset(
    {"RELEASED", "EXPIRED", "CANCELLED"}
)
_FORBIDDEN_RECEIPT_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "command_json",
        "content",
        "effect_payload",
        "graph_json",
        "message",
        "model_output",
        "password",
        "prompt",
        "raw_body",
        "raw_input",
        "secret",
        "signature",
        "token",
        "work_order_json",
    }
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MIGRATION_FILE_RE = re.compile(r"^(?P<id>\d{4})_(?P<name>.+)\.sql$")


class UniversalRouteAcceptanceError(RuntimeError):
    """Structured fail-closed acceptance error."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(
            reason_code or UNIVERSAL_ROUTE_ACCEPTANCE_INVALID
        )
        self.detail = str(detail or "universal route acceptance failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def collect_universal_route_acceptance(
    *,
    repository_root: str | Path,
    canonical_db_path: str | Path,
    request_ids: Mapping[str, str],
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    """Read one real SQLite snapshot and bind it to a clean Git HEAD."""

    root = Path(repository_root).resolve()
    source = _repository_source_binding(root)
    reader = UniversalRouteAcceptanceReader(canonical_db_path)
    snapshot = reader.capture(_selected_request_ids(request_ids))
    _require_snapshot_migration(
        snapshot,
        migration_head=source["migration_head"],
    )
    return build_universal_route_acceptance_receipt(
        source_binding=source,
        snapshot=snapshot,
        request_ids=request_ids,
        generated_at_ms=(
            int(time.time() * 1000)
            if generated_at_ms is None
            else _non_negative_int(generated_at_ms, "generated_at_ms")
        ),
    )


def build_universal_route_acceptance_receipt(
    *,
    source_binding: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    request_ids: Mapping[str, str],
    generated_at_ms: int,
) -> dict[str, Any]:
    """Evaluate a privacy-bounded reader snapshot and seal its digest."""

    commit_sha, migration_head = _validated_source_binding(source_binding)
    selected = _selected_request_ids(request_ids)
    route_snapshots = snapshot.get("routes")
    if not isinstance(route_snapshots, Mapping):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "snapshot.routes must be an object",
        )
    if set(route_snapshots) != set(selected):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "snapshot routes differ from selected request routes",
        )
    _require_snapshot_migration(snapshot, migration_head=migration_head)

    route_acceptance: dict[str, dict[str, Any]] = {}
    for route in REQUIRED_ROUTES:
        if route not in selected:
            route_acceptance[route] = _not_run_route()
            continue
        raw = route_snapshots.get(route)
        if not isinstance(raw, Mapping):
            raise UniversalRouteAcceptanceError(
                UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
                f"snapshot for route {route} must be an object",
            )
        route_acceptance[route] = _evaluate_route(
            route=route,
            request_id=selected[route],
            raw=raw,
        )

    database_snapshot_digest = _sha256_ref(
        {
            route: route_acceptance[route]["evidence_digest"]
            for route in REQUIRED_ROUTES
        }
    )
    overall_status = (
        PROGRAM_PASS
        if all(
            route_acceptance[route]["status"] == ROUTE_PASS
            for route in REQUIRED_ROUTES
        )
        else PROGRAM_INCOMPLETE
    )
    run_material = {
        "commit_sha": commit_sha,
        "migration_head": migration_head,
        "database_snapshot_digest": database_snapshot_digest,
        "generated_at_ms": _non_negative_int(
            generated_at_ms,
            "generated_at_ms",
        ),
    }
    receipt: dict[str, Any] = {
        "schema_version": UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_VERSION,
        "evidence_kind": "REAL_CANONICAL_SQLITE_READ_ONLY_SNAPSHOT",
        "run_id": (
            "universal-runtime-acceptance://sha256/"
            + _sha256_hex(run_material)
        ),
        "commit_sha": commit_sha,
        "migration_head": migration_head,
        "generated_at_ms": int(generated_at_ms),
        "database_evidence": {
            "database_kind": str(snapshot.get("database_kind") or ""),
            "read_mode": str(snapshot.get("read_mode") or ""),
            "snapshot_digest": database_snapshot_digest,
        },
        "route_acceptance": route_acceptance,
        "privacy": {
            "contains_user_text": False,
            "contains_model_output": False,
            "contains_secrets": False,
            "identifiers_are_digested": True,
        },
        "overall_status": overall_status,
    }
    _require_database_evidence(receipt["database_evidence"])
    receipt["receipt_digest"] = _sha256_ref(receipt)
    validate_universal_route_acceptance_receipt(receipt)
    return receipt


def validate_universal_route_acceptance_receipt(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Reopen a receipt and reject structural or digest tampering."""

    if not isinstance(payload, Mapping):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "acceptance receipt must be an object",
        )
    expected_fields = {
        "schema_version",
        "evidence_kind",
        "run_id",
        "commit_sha",
        "migration_head",
        "generated_at_ms",
        "database_evidence",
        "route_acceptance",
        "privacy",
        "overall_status",
        "receipt_digest",
    }
    if set(payload) != expected_fields:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "acceptance receipt fields are not canonical",
        )
    if payload.get("schema_version") != (
        UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_VERSION
    ):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "acceptance receipt schema is unsupported",
        )
    if payload.get("evidence_kind") != (
        "REAL_CANONICAL_SQLITE_READ_ONLY_SNAPSHOT"
    ):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "synthetic or non-SQLite evidence cannot prove route acceptance",
        )
    commit_sha = str(payload.get("commit_sha") or "")
    if _COMMIT_RE.fullmatch(commit_sha) is None:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "commit_sha must be a full lowercase Git SHA",
        )
    migration_head = str(payload.get("migration_head") or "")
    if _MIGRATION_FILE_RE.fullmatch(migration_head) is None:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "migration_head must be a numbered SQL migration filename",
        )
    generated_at_ms = _non_negative_int(
        payload.get("generated_at_ms"),
        "generated_at_ms",
    )
    database_evidence = payload.get("database_evidence")
    if not isinstance(database_evidence, Mapping):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "database_evidence must be an object",
        )
    _require_database_evidence(database_evidence)
    routes = payload.get("route_acceptance")
    if not isinstance(routes, Mapping) or set(routes) != set(REQUIRED_ROUTES):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "route_acceptance must contain exactly all seven routes",
        )
    for route in REQUIRED_ROUTES:
        _validate_route_record(route, routes[route])
    derived = (
        PROGRAM_PASS
        if all(routes[route]["status"] == ROUTE_PASS for route in REQUIRED_ROUTES)
        else PROGRAM_INCOMPLETE
    )
    if str(payload.get("overall_status") or "") != derived:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "overall_status differs from route results",
        )
    expected_run_id = (
        "universal-runtime-acceptance://sha256/"
        + _sha256_hex(
            {
                "commit_sha": commit_sha,
                "migration_head": migration_head,
                "database_snapshot_digest": database_evidence[
                    "snapshot_digest"
                ],
                "generated_at_ms": generated_at_ms,
            }
        )
    )
    if str(payload.get("run_id") or "") != expected_run_id:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "run_id differs from immutable run fields",
        )
    privacy = payload.get("privacy")
    if privacy != {
        "contains_user_text": False,
        "contains_model_output": False,
        "contains_secrets": False,
        "identifiers_are_digested": True,
    }:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "privacy declaration is incomplete",
        )
    _reject_forbidden_fields(payload)
    expected_digest = _sha256_ref(
        {key: value for key, value in payload.items() if key != "receipt_digest"}
    )
    if str(payload.get("receipt_digest") or "") != expected_digest:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "acceptance receipt digest mismatch",
        )
    return dict(payload)


def _evaluate_route(
    *,
    route: str,
    request_id: str,
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: set[str] = set()
    commands = _records(raw, "commands")
    if len(commands) != 1:
        reasons.add(
            "COMMAND_MISSING" if not commands else "COMMAND_DUPLICATE"
        )
        return _route_record(
            status=ROUTE_FAIL,
            reasons=reasons,
            request_id=request_id,
            raw=raw,
        )
    command = commands[0]
    if _logical_route(command) != route:
        reasons.add("ROUTE_IDENTITY_MISMATCH")
    if str(command.get("request_id") or "") != request_id:
        reasons.add("REQUEST_IDENTITY_MISMATCH")

    graphs = _records(raw, "graphs")
    if len(graphs) != 1:
        reasons.add("GRAPH_MISSING_OR_DUPLICATE")
    elif str(graphs[0].get("status") or "") != "COMPLETED":
        reasons.add("GRAPH_NOT_COMPLETED")

    nodes = _records(raw, "nodes")
    if not nodes:
        reasons.add("NODE_MISSING")
    if any(str(node.get("status") or "") != "ACCEPTED" for node in nodes):
        reasons.add("NODE_NOT_ACCEPTED")

    attempts = _records(raw, "attempts")
    success_by_node: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        node_attempts = [
            item
            for item in attempts
            if str(item.get("node_id") or "") == node_id
        ]
        if not node_attempts:
            reasons.add("ATTEMPT_MISSING")
            continue
        if any(
            str(item.get("status") or "") not in _TERMINAL_ATTEMPT_STATUSES
            for item in node_attempts
        ):
            reasons.add("ATTEMPT_NONTERMINAL")
        succeeded = [
            item
            for item in node_attempts
            if str(item.get("status") or "") == "SUCCEEDED"
        ]
        if len(succeeded) != 1:
            reasons.add("SUCCESS_ATTEMPT_MISSING_OR_DUPLICATE")
            continue
        terminal = succeeded[0]
        if int(terminal.get("attempt_no") or 0) != max(
            int(item.get("attempt_no") or 0) for item in node_attempts
        ):
            reasons.add("SUCCESS_ATTEMPT_NOT_LATEST")
        success_by_node[node_id] = terminal
    if len(attempts) < len(nodes):
        reasons.add("ATTEMPT_MISSING")

    leases = _records(raw, "leases")
    if any(
        str(item.get("status") or "") not in _TERMINAL_LEASE_STATUSES
        for item in leases
    ):
        reasons.add("LEASE_NONTERMINAL")
    for attempt in success_by_node.values():
        attempt_leases = [
            item
            for item in leases
            if str(item.get("attempt_id") or "")
            == str(attempt.get("attempt_id") or "")
        ]
        if not attempt_leases or not any(
            str(item.get("status") or "") == "RELEASED"
            for item in attempt_leases
        ):
            reasons.add("SUCCESS_LEASE_NOT_RELEASED")

    operations = _records(raw, "operations")
    for attempt in success_by_node.values():
        attempt_operations = [
            item
            for item in operations
            if str(item.get("attempt_id") or "")
            == str(attempt.get("attempt_id") or "")
        ]
        if len(attempt_operations) != 1:
            reasons.add("OPERATION_MISSING_OR_DUPLICATE")
            continue
        operation = attempt_operations[0]
        if (
            str(operation.get("status") or "") != "COMMITTED"
            or not str(operation.get("receipt_ref") or "").strip()
        ):
            reasons.add("OPERATION_RECEIPT_NOT_COMMITTED")

    _evaluate_budget_lineage(
        raw=raw,
        attempts=attempts,
        reasons=reasons,
    )
    _evaluate_node_decisions(
        raw=raw,
        command=command,
        nodes=nodes,
        success_by_node=success_by_node,
        operations=operations,
        reasons=reasons,
    )

    effects = _records(raw, "effects")
    if not effects:
        reasons.add("EFFECT_MISSING")
    if any(
        str(item.get("status") or "") != "COMMITTED"
        or not str(item.get("receipt_ref") or "").strip()
        for item in effects
    ):
        reasons.add("EFFECT_NOT_COMMITTED")

    outboxes = _records(raw, "outboxes")
    if not outboxes:
        reasons.add("OUTBOX_MISSING")
    if any(str(item.get("status") or "") != "PUBLISHED" for item in outboxes):
        reasons.add("OUTBOX_NOT_PUBLISHED")
    _require_referenced_outboxes(raw=raw, outboxes=outboxes, reasons=reasons)

    if _records(raw, "current_recovery_holds"):
        reasons.add("RECOVERY_HOLD_CURRENT")
    if _records(raw, "effect_reconciliation_cases"):
        reasons.add("EFFECT_RECONCILIATION_HISTORY_PRESENT")
    if _records(raw, "cancellation_intents"):
        reasons.add("CANCELLATION_HISTORY_PRESENT")
    return _route_record(
        status=ROUTE_FAIL if reasons else ROUTE_PASS,
        reasons=reasons,
        request_id=request_id,
        raw=raw,
    )


def _evaluate_budget_lineage(
    *,
    raw: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    reasons: set[str],
) -> None:
    graph_budgets = _records(raw, "graph_budgets")
    if len(graph_budgets) != 1:
        reasons.add("GRAPH_BUDGET_MISSING_OR_DUPLICATE")
        return
    graph_budget = graph_budgets[0]
    if (
        str(graph_budget.get("status") or "") != "CONSUMED"
        or graph_budget.get("exceeded_dimensions") != []
    ):
        reasons.add("GRAPH_BUDGET_NOT_CONSUMED")
    if not _valid_budget_limits(graph_budget.get("limits")):
        reasons.add("GRAPH_BUDGET_LIMITS_INVALID")

    usages = _records(raw, "budget_usage")
    if any(
        int(item.get("accepted") or 0) != 1
        or item.get("exceeded_dimensions") != []
        for item in usages
    ):
        reasons.add("BUDGET_USAGE_REJECTED")
    if not _projection_matches(graph_budget, usages):
        reasons.add("GRAPH_BUDGET_PROJECTION_DRIFT")

    node_budgets = _records(raw, "node_budgets")
    child_usage = _records(raw, "node_budget_usage")
    by_attempt: dict[str, list[Mapping[str, Any]]] = {}
    for item in node_budgets:
        by_attempt.setdefault(str(item.get("attempt_id") or ""), []).append(item)
    if len(node_budgets) != len(attempts):
        reasons.add("NODE_BUDGET_CARDINALITY_DRIFT")
    parent_budget_id = str(graph_budget.get("budget_id") or "")
    parent_usage_by_id = {
        str(item.get("usage_id") or ""): item for item in usages
    }
    for attempt in attempts:
        attempt_id = str(attempt.get("attempt_id") or "")
        children = by_attempt.get(attempt_id, [])
        if len(children) != 1:
            reasons.add("NODE_BUDGET_MISSING_OR_DUPLICATE")
            continue
        child = children[0]
        if (
            str(child.get("parent_budget_id") or "") != parent_budget_id
            or child.get("exceeded_dimensions") != []
            or not _valid_budget_limits(child.get("limits"))
            or not str(child.get("claim_outbox_id") or "").strip()
        ):
            reasons.add("NODE_BUDGET_BINDING_INVALID")
        child_rows = [
            item
            for item in child_usage
            if str(item.get("node_budget_id") or "")
            == str(child.get("node_budget_id") or "")
        ]
        if int(child.get("usage_version") or 0) != len(child_rows):
            reasons.add("NODE_BUDGET_VERSION_DRIFT")
        expected_before = 0
        for item in child_rows:
            usage_id = str(item.get("usage_id") or "")
            parent = parent_usage_by_id.get(usage_id)
            if (
                parent is None
                or str(item.get("attempt_id") or "") != attempt_id
                or str(item.get("parent_budget_id") or "") != parent_budget_id
                or int(item.get("accepted") or 0)
                != int(parent.get("accepted") or 0)
                or str(item.get("parent_outbox_id") or "")
                != str(parent.get("outbox_id") or "")
                or int(item.get("child_version_before") or 0)
                != expected_before
                or int(item.get("child_version_after") or 0)
                != expected_before + 1
            ):
                reasons.add("NODE_BUDGET_USAGE_LINEAGE_INVALID")
            expected_before += 1
        attempt_usages = [
            item
            for item in usages
            if str(item.get("attempt_id") or "") == attempt_id
        ]
        if not _projection_matches(child, attempt_usages):
            reasons.add("NODE_BUDGET_PROJECTION_DRIFT")


def _evaluate_node_decisions(
    *,
    raw: Mapping[str, Any],
    command: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    success_by_node: Mapping[str, Mapping[str, Any]],
    operations: Sequence[Mapping[str, Any]],
    reasons: set[str],
) -> None:
    verifications = _records(raw, "verifications")
    acceptances = _records(raw, "acceptances")
    artifacts = _records(raw, "artifacts")
    projections = _records(raw, "worker_projections")
    operation_receipts = {
        str(item.get("attempt_id") or ""): str(item.get("receipt_ref") or "")
        for item in operations
        if str(item.get("status") or "") == "COMMITTED"
    }
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        attempt = success_by_node.get(node_id)
        if attempt is None:
            continue
        attempt_id = str(attempt.get("attempt_id") or "")
        verified = [
            item
            for item in verifications
            if str(item.get("node_id") or "") == node_id
            and str(item.get("attempt_id") or "") == attempt_id
            and str(item.get("target_status") or "") == "VERIFIED"
        ]
        if len(verified) != 1 or not _raw_sha256(
            verified[0].get("result_sha256") if verified else ""
        ):
            reasons.add("VERIFICATION_DECISION_INVALID")
        accepted = [
            item
            for item in acceptances
            if str(item.get("node_id") or "") == node_id
            and str(item.get("attempt_id") or "") == attempt_id
            and str(item.get("target_status") or "") == "ACCEPTED"
        ]
        if len(accepted) != 1 or not _raw_sha256(
            accepted[0].get("result_sha256") if accepted else ""
        ):
            reasons.add("ACCEPTANCE_DECISION_INVALID")
        node_artifacts = [
            item
            for item in artifacts
            if str(item.get("node_id") or "") == node_id
            and str(item.get("attempt_id") or "") == attempt_id
        ]
        if len(node_artifacts) != 1:
            reasons.add("ARTIFACT_MISSING_OR_DUPLICATE")
        else:
            artifact = node_artifacts[0]
            if (
                not _sha256_ref_value(artifact.get("output_contract_sha256"))
                or not _sha256_ref_value(artifact.get("payload_sha256"))
                or str(artifact.get("receipt_ref") or "")
                != operation_receipts.get(attempt_id, "")
            ):
                reasons.add("ARTIFACT_RECEIPT_BINDING_INVALID")
        node_projections = [
            item
            for item in projections
            if str(item.get("node_id") or "") == node_id
            and str(item.get("attempt_id") or "") == attempt_id
        ]
        if len(node_projections) != 1:
            reasons.add("WORKER_PROJECTION_MISSING_OR_DUPLICATE")
        else:
            projection = node_projections[0]
            if (
                str(projection.get("command_id") or "")
                != str(command.get("command_id") or "")
                or str(projection.get("request_id") or "")
                != str(command.get("request_id") or "")
                or str(projection.get("decision") or "") != "APPROVE"
                or not _sha256_ref_value(projection.get("payload_digest"))
            ):
                reasons.add("WORKER_PROJECTION_BINDING_INVALID")


def _require_referenced_outboxes(
    *,
    raw: Mapping[str, Any],
    outboxes: Sequence[Mapping[str, Any]],
    reasons: set[str],
) -> None:
    published = {
        str(item.get("outbox_id") or "")
        for item in outboxes
        if str(item.get("status") or "") == "PUBLISHED"
    }
    required: set[str] = set()
    for key, field in (
        ("budget_usage", "outbox_id"),
        ("node_budgets", "claim_outbox_id"),
        ("node_budget_usage", "parent_outbox_id"),
        ("verifications", "node_outbox_id"),
        ("acceptances", "node_outbox_id"),
    ):
        required.update(
            str(item.get(field) or "")
            for item in _records(raw, key)
            if str(item.get(field) or "")
        )
    if not required.issubset(published):
        reasons.add("REFERENCED_OUTBOX_NOT_PUBLISHED")


def _route_record(
    *,
    status: str,
    reasons: set[str],
    request_id: str,
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    counts = {
        key: len(_records(raw, key))
        for key in (
            "commands",
            "graphs",
            "nodes",
            "attempts",
            "leases",
            "operations",
            "graph_budgets",
            "budget_usage",
            "node_budgets",
            "node_budget_usage",
            "verifications",
            "acceptances",
            "artifacts",
            "worker_projections",
            "effects",
            "outboxes",
            "current_recovery_holds",
            "effect_reconciliation_cases",
            "cancellation_intents",
        )
    }
    evidence_material = _privacy_bounded_evidence(raw)
    return {
        "status": status,
        "reason_codes": sorted(reasons),
        "request_ref": "request://sha256/" + _sha256_hex(request_id),
        "evidence_counts": counts,
        "evidence_digest": _sha256_ref(evidence_material),
    }


def _not_run_route() -> dict[str, Any]:
    material = {"status": ROUTE_NOT_RUN, "reason": "ROUTE_NOT_SELECTED"}
    return {
        "status": ROUTE_NOT_RUN,
        "reason_codes": ["ROUTE_NOT_SELECTED"],
        "request_ref": None,
        "evidence_counts": {},
        "evidence_digest": _sha256_ref(material),
    }


def _privacy_bounded_evidence(raw: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "commands": (
            "command_id",
            "request_id",
            "surface",
            "route_type",
            "graph_id",
            "graph_revision",
            "created_at_ms",
        ),
        "graphs": ("graph_id", "revision", "status", "status_version"),
        "nodes": ("node_id", "status", "status_version"),
        "attempts": (
            "attempt_id",
            "node_id",
            "attempt_no",
            "status",
            "status_version",
        ),
        "leases": ("lease_id", "attempt_id", "status", "status_version"),
        "operations": (
            "reservation_id",
            "attempt_id",
            "status",
            "status_version",
            "receipt_ref",
        ),
        "graph_budgets": (
            "budget_id",
            "status",
            "status_version",
            "limits",
            "reserved",
            "consumed",
            "attempted_consumed",
            "exceeded_dimensions",
        ),
        "budget_usage": (
            "budget_id",
            "usage_id",
            "attempt_id",
            "delta",
            "accepted",
            "exceeded_dimensions",
            "reservation_version_before",
            "reservation_version_after",
            "outbox_id",
        ),
        "node_budgets": (
            "node_budget_id",
            "parent_budget_id",
            "node_id",
            "attempt_id",
            "limits",
            "reserved",
            "consumed",
            "attempted_consumed",
            "exceeded_dimensions",
            "usage_version",
            "claim_outbox_id",
            "claim_origin",
        ),
        "node_budget_usage": (
            "node_budget_id",
            "parent_budget_id",
            "usage_id",
            "attempt_id",
            "accepted",
            "child_version_before",
            "child_version_after",
            "parent_outbox_id",
        ),
        "verifications": (
            "verification_ref",
            "node_id",
            "attempt_id",
            "target_status",
            "result_sha256",
            "node_outbox_id",
        ),
        "acceptances": (
            "acceptance_ref",
            "node_id",
            "attempt_id",
            "target_status",
            "result_sha256",
            "node_outbox_id",
        ),
        "artifacts": (
            "artifact_ref",
            "node_id",
            "attempt_id",
            "receipt_ref",
            "output_contract_sha256",
            "payload_sha256",
        ),
        "worker_projections": (
            "projection_id",
            "command_id",
            "request_id",
            "node_id",
            "attempt_id",
            "route_type",
            "decision",
            "payload_digest",
        ),
        "effects": (
            "effect_id",
            "node_id",
            "attempt_id",
            "effect_type",
            "status",
            "status_version",
            "receipt_ref",
        ),
        "outboxes": (
            "outbox_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "event_type",
            "attempt_id",
            "status",
            "status_version",
            "graph_revision",
        ),
        "current_recovery_holds": (
            "case_ref",
            "reservation_id",
            "operation_status",
            "operation_status_version",
            "cancellation_intent_ref",
        ),
        "effect_reconciliation_cases": (
            "case_id",
            "effect_id",
            "intent_ref",
            "classification",
            "proof_count",
        ),
        "cancellation_intents": (
            "intent_ref",
            "status",
            "status_version",
        ),
    }
    return {
        key: [
            {field: item.get(field) for field in fields}
            for item in _records(raw, key)
        ]
        for key, fields in allowed.items()
    }


def _projection_matches(
    reservation: Mapping[str, Any],
    usages: Sequence[Mapping[str, Any]],
) -> bool:
    consumed = {dimension: 0 for dimension in _BUDGET_DIMENSIONS}
    attempted = {dimension: 0 for dimension in _BUDGET_DIMENSIONS}
    for usage in usages:
        delta = usage.get("delta")
        if not isinstance(delta, Mapping) or set(delta) != _BUDGET_DIMENSIONS:
            return False
        for dimension in _BUDGET_DIMENSIONS:
            value = delta.get(dimension)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return False
            attempted[dimension] += value
            if int(usage.get("accepted") or 0) == 1:
                consumed[dimension] += value
    return (
        reservation.get("consumed") == consumed
        and reservation.get("attempted_consumed") == attempted
    )


def _valid_budget_limits(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != _BUDGET_DIMENSIONS:
        return False
    if not all(
        not isinstance(item, bool) and isinstance(item, int) and item >= 0
        for item in value.values()
    ):
        return False
    return (
        int(value["time_ms"]) > 0
        and int(value["concurrency"]) > 0
        and (
            int(value["token_count"]) > 0
            or int(value["tool_call_count"]) > 0
        )
    )


def _validated_source_binding(
    value: Mapping[str, Any],
) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "commit_sha",
        "migration_head",
        "repository_clean",
    }:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID,
            "source binding fields are not canonical",
        )
    commit_sha = str(value.get("commit_sha") or "")
    migration_head = str(value.get("migration_head") or "")
    if (
        _COMMIT_RE.fullmatch(commit_sha) is None
        or _MIGRATION_FILE_RE.fullmatch(migration_head) is None
        or value.get("repository_clean") is not True
    ):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID,
            "source binding is not a clean commit and migration head",
        )
    return commit_sha, migration_head


def _repository_source_binding(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists() and not _git_ok(root, "rev-parse", "--git-dir"):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID,
            "repository_root is not a Git worktree",
        )
    commit_sha = _git(root, "rev-parse", "HEAD").lower()
    if _COMMIT_RE.fullmatch(commit_sha) is None:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID,
            "Git HEAD is not a full lowercase commit SHA",
        )
    dirty = _git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=no",
    )
    if dirty:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_DIRTY,
            "tracked worktree changes forbid commit-bound acceptance",
        )
    return {
        "commit_sha": commit_sha,
        "migration_head": _current_migration_head(root),
        "repository_clean": True,
    }


def _current_migration_head(root: Path) -> str:
    migration_root = root / "host_brain" / "audit" / "migrations"
    candidates: list[tuple[int, str]] = []
    for path in migration_root.glob("*.sql"):
        matched = _MIGRATION_FILE_RE.fullmatch(path.name)
        if matched is not None:
            candidates.append((int(matched.group("id")), path.name))
    if not candidates:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID,
            "migration catalog is empty",
        )
    number = max(item[0] for item in candidates)
    heads = sorted(name for item, name in candidates if item == number)
    if len(heads) != 1:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID,
            "migration catalog has a duplicate numeric head",
        )
    return heads[0]


def _require_snapshot_migration(
    snapshot: Mapping[str, Any],
    *,
    migration_head: str,
) -> None:
    migration = snapshot.get("migration")
    matched = _MIGRATION_FILE_RE.fullmatch(str(migration_head or ""))
    if not isinstance(migration, Mapping) or matched is None:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_MIGRATION_MISMATCH,
            "database migration evidence is unavailable",
        )
    if (
        int(migration.get("migration_id") or -1) != int(matched.group("id"))
        or str(migration.get("name") or "") != matched.group("name")
    ):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_MIGRATION_MISMATCH,
            "database migration head differs from repository migration head",
        )


def _selected_request_ids(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "at least one route request is required",
        )
    selected: dict[str, str] = {}
    for raw_route, raw_request_id in value.items():
        route = str(raw_route or "").strip().lower()
        request_id = str(raw_request_id or "").strip()
        if route not in REQUIRED_ROUTES or not request_id or len(request_id) > 240:
            raise UniversalRouteAcceptanceError(
                UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
                "route request mapping is invalid",
            )
        selected[route] = request_id
    if len(set(selected.values())) != len(selected):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "one request cannot prove more than one route",
        )
    return selected


def _validate_route_record(route: str, value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "status",
        "reason_codes",
        "request_ref",
        "evidence_counts",
        "evidence_digest",
    }:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            f"route {route} result fields are not canonical",
        )
    status = str(value.get("status") or "")
    reasons = value.get("reason_codes")
    counts = value.get("evidence_counts")
    if status not in {ROUTE_PASS, ROUTE_FAIL, ROUTE_NOT_RUN}:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            f"route {route} status is invalid",
        )
    if (
        not isinstance(reasons, list)
        or reasons != sorted(set(str(item) for item in reasons))
        or not isinstance(counts, Mapping)
        or not _sha256_ref_value(value.get("evidence_digest"))
    ):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            f"route {route} evidence is invalid",
        )
    request_ref = value.get("request_ref")
    if status == ROUTE_PASS:
        if reasons or not str(request_ref or "").startswith(
            "request://sha256/"
        ):
            raise UniversalRouteAcceptanceError(
                UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
                f"route {route} PASS lacks exact request evidence",
            )
    elif status == ROUTE_FAIL:
        if not reasons or not str(request_ref or "").startswith(
            "request://sha256/"
        ):
            raise UniversalRouteAcceptanceError(
                UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
                f"route {route} FAIL lacks reason evidence",
            )
    elif (
        reasons != ["ROUTE_NOT_SELECTED"]
        or request_ref is not None
        or dict(counts)
    ):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            f"route {route} NOT_RUN is not canonical",
        )


def _require_database_evidence(value: Mapping[str, Any]) -> None:
    if set(value) != {"database_kind", "read_mode", "snapshot_digest"} or (
        value.get("database_kind") != "FILE_BACKED_CANONICAL_SQLITE"
        or value.get("read_mode")
        != "SQLITE_MODE_RO_QUERY_ONLY_SINGLE_TRANSACTION"
        or not _sha256_ref_value(value.get("snapshot_digest"))
    ):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            "database evidence is not a real query-only canonical SQLite snapshot",
        )


def _reject_forbidden_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            if key in _FORBIDDEN_RECEIPT_FIELDS:
                raise UniversalRouteAcceptanceError(
                    UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
                    f"receipt contains forbidden field {key}",
                )
            _reject_forbidden_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_fields(item)


def _records(value: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    selected = value.get(key, [])
    if not isinstance(selected, list) or not all(
        isinstance(item, Mapping) for item in selected
    ):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            f"snapshot field {key} must be a list of objects",
        )
    return list(selected)


def _logical_route(command: Mapping[str, Any]) -> str:
    surface = str(command.get("surface") or "").strip().lower()
    if surface in _SURFACE_ROUTES:
        return surface
    return str(command.get("route_type") or "").strip().lower()


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            f"{field} must be a non-negative integer",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            f"{field} must be a non-negative integer",
        ) from exc
    if normalized < 0:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_INVALID,
            f"{field} must be a non-negative integer",
        )
    return normalized


def _sha256_ref(value: Any) -> str:
    return "sha256:" + _sha256_hex(value)


def _sha256_hex(value: Any) -> str:
    raw = (
        value
        if isinstance(value, str)
        else json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return hashlib.sha256(str(raw).encode("utf-8")).hexdigest()


def _sha256_ref_value(value: Any) -> bool:
    return _SHA256_REF_RE.fullmatch(str(value or "")) is not None


def _raw_sha256(value: Any) -> bool:
    return _RAW_SHA256_RE.fullmatch(str(value or "")) is not None


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID,
            "Git source binding is unavailable",
        ) from exc
    if completed.returncode != 0:
        raise UniversalRouteAcceptanceError(
            UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID,
            "Git source binding command failed",
        )
    return completed.stdout.strip()


def _git_ok(root: Path, *args: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


__all__ = [
    "PROGRAM_INCOMPLETE",
    "PROGRAM_PASS",
    "REQUIRED_ROUTES",
    "ROUTE_FAIL",
    "ROUTE_NOT_RUN",
    "ROUTE_PASS",
    "UNIVERSAL_ROUTE_ACCEPTANCE_INVALID",
    "UNIVERSAL_ROUTE_ACCEPTANCE_MIGRATION_MISMATCH",
    "UNIVERSAL_ROUTE_ACCEPTANCE_SCHEMA_VERSION",
    "UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_DIRTY",
    "UNIVERSAL_ROUTE_ACCEPTANCE_SOURCE_INVALID",
    "UniversalRouteAcceptanceError",
    "build_universal_route_acceptance_receipt",
    "collect_universal_route_acceptance",
    "validate_universal_route_acceptance_receipt",
]
