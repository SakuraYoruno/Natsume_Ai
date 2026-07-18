"""Independent semantic validation for migration-0099 durable payloads.

SQLite constraints are a useful first fence, but readers must not trust that a
same-named table or trigger still enforces the original contract.  These pure
helpers therefore recheck the case/proof matrices and the exact JSON shapes
before any persisted reconciliation authority is accepted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


TERMINAL_RECONCILIATION_SUBJECT_SCHEMA = (
    "Natsume.task_runtime.terminal_reconciliation.subject.v1"
)

_CASE_SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "case_kind",
        "classification",
        "reason_code",
        "recommended_action",
        "graph",
        "selected_budget",
        "selected_node",
        "selected_attempt",
        "failure_bundle",
        "observation",
    }
)
_SUBJECT_KEYS = frozenset({"schema_version", "case_id", "observation"})
_OBSERVATION_KEYS = frozenset(
    {
        "graph",
        "budgets",
        "nodes",
        "attempts",
        "leases",
        "operations",
        "reviews",
        "effects",
        "parallel_decisions",
        "success_decisions",
        "cancellations",
    }
)
_FAILURE_BUNDLE_KEYS = frozenset(
    {"node", "attempt", "lease", "outboxes", "evidence_refs", "bundle_digest"}
)


def terminal_case_snapshot_valid(
    snapshot: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    case_schema: str,
) -> bool:
    """Validate exact case JSON shape and observation membership bindings."""

    if frozenset(snapshot) != _CASE_SNAPSHOT_KEYS:
        return False
    if snapshot.get("schema_version") != case_schema:
        return False
    observation = snapshot.get("observation")
    graph = snapshot.get("graph")
    if not _observation_shape_valid(observation) or not isinstance(graph, Mapping):
        return False
    if dict(graph) != dict(observation["graph"]):
        return False
    selected_bindings = (
        ("selected_budget", "budgets"),
        ("selected_node", "nodes"),
        ("selected_attempt", "attempts"),
    )
    for selected_key, collection_key in selected_bindings:
        selected = snapshot.get(selected_key)
        if selected is not None and not _mapping_member(
            selected,
            observation[collection_key],
        ):
            return False
    failure_bundle = snapshot.get("failure_bundle")
    if failure_bundle is not None and not _failure_bundle_valid(
        failure_bundle,
        snapshot=snapshot,
        observation=observation,
    ):
        return False
    return all(
        snapshot.get(key) == record.get(key)
        for key in (
            "case_kind",
            "classification",
            "reason_code",
            "recommended_action",
        )
    )


def terminal_proof_subject_valid(
    subject: Mapping[str, Any],
    *,
    case: Mapping[str, Any],
) -> bool:
    """Validate the signed subject independently of binding/digest checks."""

    if frozenset(subject) != _SUBJECT_KEYS:
        return False
    if subject.get("schema_version") != TERMINAL_RECONCILIATION_SUBJECT_SCHEMA:
        return False
    if subject.get("case_id") != case.get("case_id"):
        return False
    observation = subject.get("observation")
    if not _observation_shape_valid(observation):
        return False
    case_snapshot = case.get("snapshot")
    return bool(
        isinstance(case_snapshot, Mapping)
        and dict(observation) == dict(case_snapshot.get("observation") or {})
    )


def terminal_case_matrix_valid(record: Mapping[str, Any]) -> bool:
    """Recheck the SQL case-kind/classification/action matrix in Python."""

    kind = str(record.get("case_kind") or "")
    classification = str(record.get("classification") or "")
    graph_status = str(record.get("graph_status") or "")
    budget_id = str(record.get("budget_id") or "") or None
    budget_status = str(record.get("budget_status") or "") or None
    node_id = str(record.get("node_id") or "") or None
    node_status = str(record.get("node_status") or "") or None
    attempt_id = str(record.get("attempt_id") or "") or None
    action = str(record.get("recommended_action") or "")
    if kind == "FAILED_UNSETTLED_BUDGET":
        return bool(
            classification == "EXACT_REPAIRABLE"
            and graph_status == "FAILED"
            and budget_id is not None
            and budget_status in {"ACTIVE", "EXCEEDED"}
            and action == "RELEASE_BUDGET"
        )
    if kind == "BLOCKED_FAILURE_NODE":
        budget_matrix_valid = (
            action == "FAIL_GRAPH"
            and (budget_id is None or budget_status in {"RELEASED", "CANCELLED"})
        ) or (
            action == "FAIL_GRAPH_AND_RELEASE_BUDGET"
            and budget_id is not None
            and budget_status in {"ACTIVE", "EXCEEDED"}
        )
        return bool(
            classification == "SIGNED_REPAIR_REQUIRED"
            and graph_status == "BLOCKED"
            and node_id is not None
            and node_status in {"FAILED", "VERIFICATION_FAILED", "CANCELLED"}
            and budget_matrix_valid
        )
    if kind == "CANCELLED_UNSETTLED_BUDGET":
        return bool(
            classification == "SIGNED_REPAIR_REQUIRED"
            and graph_status == "CANCELLED"
            and budget_id is not None
            and budget_status in {"ACTIVE", "EXCEEDED"}
            and action == "RELEASE_BUDGET"
        )
    if kind == "COMPLETED_INCONSISTENT":
        return bool(
            classification == "QUARANTINED_STRUCTURAL"
            and graph_status == "COMPLETED"
            and action == "NONE"
        )
    if kind == "TERMINAL_STRUCTURAL_ANOMALY":
        return classification == "QUARANTINED_STRUCTURAL" and action == "NONE"
    if kind == "TERMINAL_HEALTHY_CLOSURE":
        return bool(
            classification == "SIGNED_REPAIR_REQUIRED"
            and budget_id is not None
            and node_id is None
            and attempt_id is None
            and action == "NONE"
        )
    return False


def terminal_proof_matrix_valid(
    proof: Mapping[str, Any],
    *,
    case: Mapping[str, Any],
) -> bool:
    """Recheck authority, action, target and Outbox-presence semantics."""

    action = str(proof.get("action") or "")
    authority = str(proof.get("authority_kind") or "")
    classification = str(case.get("classification") or "")
    target_graph = str(proof.get("target_graph_status") or "") or None
    target_budget = str(proof.get("target_budget_status") or "") or None
    graph_outbox = str(proof.get("graph_outbox_id") or "") or None
    budget_outbox = str(proof.get("budget_outbox_id") or "") or None
    if action != str(case.get("recommended_action") or ""):
        return False
    if classification == "EXACT_REPAIRABLE":
        authority_valid = authority in {
            "SYSTEM_DURABLE_EVIDENCE",
            "SIGNED_OPERATOR",
        }
    elif classification == "SIGNED_REPAIR_REQUIRED":
        authority_valid = authority == "SIGNED_OPERATOR"
    else:
        # Quarantine is evidence to preserve, never a directly provable case.
        return False
    expected = {
        "RELEASE_BUDGET": (
            str(case.get("graph_status") or ""),
            "RELEASED",
            None,
            True,
        ),
        "FAIL_GRAPH": ("FAILED", None, True, None),
        "FAIL_GRAPH_AND_RELEASE_BUDGET": ("FAILED", "RELEASED", True, True),
        "NONE": (None, None, None, None),
    }
    if action not in expected:
        return False
    expected_graph, expected_budget, needs_graph_outbox, needs_budget_outbox = expected[
        action
    ]
    return bool(
        authority_valid
        and (target_graph, target_budget) == (expected_graph, expected_budget)
        and bool(graph_outbox) is bool(needs_graph_outbox)
        and bool(budget_outbox) is bool(needs_budget_outbox)
        and (
            action != "NONE"
            or str(case.get("case_kind") or "") == "TERMINAL_HEALTHY_CLOSURE"
        )
    )


def selected_binding_matches(
    selected: Any,
    record: Mapping[str, Any],
    *,
    identity_key: str,
    status_key: str,
    version_key: str,
) -> bool:
    expected_identity = record.get(identity_key)
    if expected_identity is None:
        return (
            selected is None
            and record.get(status_key) is None
            and record.get(version_key) is None
        )
    try:
        return bool(
            isinstance(selected, Mapping)
            and str(selected.get(identity_key) or "") == str(expected_identity)
            and str(selected.get("status") or "") == str(record.get(status_key) or "")
            and int(selected.get("status_version", -1))
            == int(
                record.get(version_key)
                if record.get(version_key) is not None
                else -2
            )
        )
    except (TypeError, ValueError):
        return False


def _observation_shape_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    keys = frozenset(value)
    if keys not in {_OBSERVATION_KEYS, _OBSERVATION_KEYS | {"structural_error"}}:
        return False
    if not isinstance(value.get("graph"), Mapping):
        return False
    sequence_keys = _OBSERVATION_KEYS - {"graph"}
    if any(not _mapping_sequence(value.get(key)) for key in sequence_keys):
        return False
    return "structural_error" not in value or bool(
        str(value.get("structural_error") or "")
    )


def _mapping_sequence(value: Any) -> bool:
    return bool(
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and all(isinstance(item, Mapping) for item in value)
    )


def _mapping_member(selected: Any, collection: Any) -> bool:
    return bool(
        isinstance(selected, Mapping)
        and _mapping_sequence(collection)
        and any(dict(selected) == dict(item) for item in collection)
    )


def _failure_bundle_valid(
    value: Any,
    *,
    snapshot: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> bool:
    if not isinstance(value, Mapping) or frozenset(value) != _FAILURE_BUNDLE_KEYS:
        return False
    return bool(
        value.get("node") == snapshot.get("selected_node")
        and value.get("attempt") == snapshot.get("selected_attempt")
        and _mapping_member(value.get("lease"), observation.get("leases"))
        and isinstance(value.get("outboxes"), Mapping)
        and isinstance(value.get("evidence_refs"), list)
        and isinstance(value.get("bundle_digest"), str)
    )


__all__ = [
    "TERMINAL_RECONCILIATION_SUBJECT_SCHEMA",
    "selected_binding_matches",
    "terminal_case_matrix_valid",
    "terminal_case_snapshot_valid",
    "terminal_proof_matrix_valid",
    "terminal_proof_subject_valid",
]
