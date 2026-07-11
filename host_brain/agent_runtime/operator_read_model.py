"""Versioned read models for the Monitor execution/review console.

The adapter deliberately accepts already-governed TaskStore, ReviewStore and
RuntimeEventStore records and emits a small public allowlist.  It owns no
mutation API and never publishes raw executor payloads or resume-token secrets.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable

from host_brain.core.ids import now_ms
from host_brain.core.trace_redaction import public_decision_summary


EXECUTION_INDEX_SCHEMA_VERSION = "Natsume.operator.execution_index.v1"
EXECUTION_REVIEW_SCHEMA_VERSION = "Natsume.operator.execution_review.v1"
REVIEW_LOCATOR_SCHEMA_VERSION = "Natsume.operator.review_locator.v1"

_TERMINAL_GRAPH_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
_TERMINAL_NODE_STATUSES = frozenset({"ACCEPTED", "FAILED", "CANCELLED"})
_TERMINAL_ATTEMPT_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
_REVIEW_LIVE_STATUSES = frozenset({"PENDING", "APPROVED"})
_BUDGET_FIELDS = (
    "token_count",
    "tool_call_count",
    "time_ms",
    "concurrency",
    "retry_count",
    "model_cost_micros",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _refs(value: Any) -> list[str]:
    return list(
        dict.fromkeys(
            text
            for text in (_text(item) for item in _sequence(value))
            if text
        )
    )


def _amount(value: Any) -> dict[str, int]:
    data = _mapping(value)
    return {field: max(0, _int(data.get(field))) for field in _BUDGET_FIELDS}


def _graph_parts(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = _mapping(record)
    graph = _mapping(data.get("graph"))
    raw_nodes = [_mapping(item) for item in _sequence(data.get("nodes"))]
    return graph, raw_nodes


def build_execution_index_read_model(
    graph_records: Iterable[Mapping[str, Any]],
    *,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    """Return a versioned, read-only graph index for operator selection."""

    executions: list[dict[str, Any]] = []
    for raw_record in graph_records:
        record = _mapping(raw_record)
        graph, node_records = _graph_parts(record)
        if not graph:
            continue
        statuses = Counter(
            _text(_mapping(node.get("node")).get("status") or node.get("status")) or "UNKNOWN"
            for node in node_records
        )
        graph_status = _text(record.get("status") or graph.get("status")) or "UNKNOWN"
        executions.append(
            {
                "graph_id": _text(graph.get("graph_id") or record.get("graph_id")),
                "revision": max(1, _int(graph.get("revision") or record.get("revision"), default=1)),
                "root_work_order_id": _text(graph.get("root_work_order_id")),
                "planner_ref": _text(graph.get("planner_ref")),
                "status": graph_status,
                "status_version": max(0, _int(record.get("status_version"))),
                "node_count": len(node_records),
                "node_status_counts": dict(sorted(statuses.items())),
                "terminal": graph_status in _TERMINAL_GRAPH_STATUSES,
                "source_kind": _text(record.get("source_kind")) or "CANONICAL",
                "created_ts_ms": max(0, _int(record.get("created_ts_ms") or graph.get("created_at"))),
                "updated_ts_ms": max(0, _int(record.get("updated_ts_ms"))),
            }
        )
    executions.sort(
        key=lambda item: (
            -int(item["updated_ts_ms"]),
            str(item["graph_id"]),
            -int(item["revision"]),
        )
    )
    return {
        "schema_version": EXECUTION_INDEX_SCHEMA_VERSION,
        "generated_at_ms": max(0, _int(generated_at_ms, default=now_ms())),
        "read_only": True,
        "source_contracts": ["TaskGraph", "TaskNode"],
        "executions": executions,
        "execution_count": len(executions),
    }


def _attempt_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    attempt = _mapping(raw)
    status = _text(attempt.get("status")) or "UNKNOWN"
    return {
        "attempt_id": _text(attempt.get("attempt_id")),
        "node_id": _text(attempt.get("node_id")),
        "attempt_no": max(1, _int(attempt.get("attempt_no"), default=1)),
        "status": status,
        "status_version": max(0, _int(attempt.get("status_version"))),
        "owner_id": _text(attempt.get("owner_id")) or None,
        "created_ts_ms": max(0, _int(attempt.get("created_ts_ms"))),
        "acquired_ts_ms": _optional_int(attempt.get("acquired_ts_ms")),
        "started_ts_ms": _optional_int(attempt.get("started_ts_ms")),
        "completed_ts_ms": _optional_int(attempt.get("completed_ts_ms")),
        "updated_ts_ms": max(0, _int(attempt.get("updated_ts_ms"))),
        "error_ref": _text(attempt.get("error_ref")) or None,
        "terminal": status in _TERMINAL_ATTEMPT_STATUSES,
    }


def _budget_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    budget = _mapping(raw)
    limits = _amount(budget.get("limits"))
    reserved = _amount(budget.get("reserved"))
    consumed = _amount(budget.get("consumed"))
    attempted = _amount(budget.get("attempted_consumed"))
    utilization: dict[str, float | None] = {}
    for field in _BUDGET_FIELDS:
        ceiling = limits[field]
        utilization[field] = round(consumed[field] / ceiling, 4) if ceiling > 0 else None
    return {
        "budget_id": _text(budget.get("budget_id")),
        "scope": _text(budget.get("scope")),
        "node_id": _text(budget.get("node_id")) or None,
        "attempt_id": _text(budget.get("attempt_id")) or None,
        "status": _text(budget.get("status")) or "UNKNOWN",
        "status_version": max(0, _int(budget.get("status_version"))),
        "limits": limits,
        "reserved": reserved,
        "consumed": consumed,
        "attempted_consumed": attempted,
        "utilization": utilization,
        "exceeded_dimensions": _refs(budget.get("exceeded_dimensions")),
        "created_ts_ms": max(0, _int(budget.get("created_ts_ms"))),
        "updated_ts_ms": max(0, _int(budget.get("updated_ts_ms"))),
    }


def _review_view(raw: Mapping[str, Any], *, generated_at_ms: int) -> dict[str, Any]:
    record = _mapping(raw)
    item = _mapping(record.get("review"))
    persisted_status = _text(item.get("status")) or "UNKNOWN"
    expires_at = max(0, _int(item.get("expires_at")))
    effective_status = (
        "EXPIRED"
        if persisted_status in _REVIEW_LIVE_STATUSES and expires_at <= generated_at_ms
        else persisted_status
    )
    is_pending = effective_status == "PENDING"
    is_approved = effective_status == "APPROVED"
    return {
        "review_id": _text(item.get("review_id")),
        "review_type": _text(item.get("review_type")),
        "subject_ref": _text(item.get("subject_ref")),
        "subject_version": max(0, _int(item.get("subject_version"))),
        "requester": _text(item.get("requester")),
        "reason": public_decision_summary(
            item.get("reason"),
            fallback="Review reason is available by evidence reference.",
        ),
        "risk_level": max(1, min(5, _int(item.get("risk_level"), default=1))),
        "status": effective_status,
        "persisted_status": persisted_status,
        "status_version": max(0, _int(record.get("status_version"))),
        "expires_at": expires_at,
        "expired": effective_status == "EXPIRED",
        "resume_phase": _text(item.get("resume_phase")),
        "checkpoint_id": _text(item.get("checkpoint_id")),
        "diff_ref": _text(item.get("diff_ref")) or None,
        "decision_actor": _text(item.get("decision_actor")) or None,
        "token_ref_public": _text(item.get("token_ref")) or None,
        "resume_count": max(0, _int(item.get("resume_count"))),
        "resume_result_ref": _text(item.get("resume_result_ref")) or None,
        "work_order_id": _text(record.get("work_order_id")) or None,
        "task_graph_id": _text(record.get("task_graph_id")) or None,
        "task_node_id": _text(record.get("task_node_id")) or None,
        "side_effects": [
            public_decision_summary(value, fallback="Side effect recorded by reference.", max_chars=160)
            for value in _sequence(record.get("side_effects"))
        ],
        "evidence_refs": _refs(record.get("evidence_refs")),
        "created_ts_ms": max(0, _int(record.get("created_ts_ms"))),
        "updated_ts_ms": max(0, _int(record.get("updated_ts_ms"))),
        "actions": {
            "approve": is_pending,
            "reject": is_pending,
            "resume": is_approved,
            "resume_requires_token": True,
            "resume_binding_fields": [
                "resume_token_ref",
                "subject_ref",
                "subject_version",
                "resume_phase",
                "checkpoint_id",
            ],
        },
    }


def _event_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    event = _mapping(raw)
    event_type = _text(event.get("event_type")) or "UNKNOWN"
    aggregate_type = _text(event.get("aggregate_type")) or "UNKNOWN"
    aggregate_id = _text(event.get("aggregate_id"))
    summary = public_decision_summary(
        f"{event_type} · {aggregate_type} {aggregate_id}",
        fallback="Runtime transition recorded.",
        max_chars=220,
    )
    return {
        "cursor": max(0, _int(event.get("cursor"))),
        "event_id": _text(event.get("event_id")),
        "event_type": event_type,
        "source_kind": _text(event.get("source_kind")),
        "source_ref": _text(event.get("source_ref")),
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": max(0, _int(event.get("aggregate_version"))),
        "payload_ref": _text(event.get("payload_ref")),
        "evidence_refs": _refs(event.get("evidence_refs")),
        "dedupe_key": _text(event.get("dedupe_key")),
        "redaction_level": _text(event.get("redaction_level")) or "INTERNAL_REDACTED",
        "ts_ms": max(0, _int(event.get("ts_ms"))),
        "summary": summary,
    }


def _verification_view(
    *,
    node_id: str,
    node_status: str,
    matching_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    relevant = [
        _mapping(event)
        for event in matching_events
        if "VERIF" in _text(_mapping(event).get("event_type")).upper()
    ]
    evidence_refs = list(
        dict.fromkeys(
            ref
            for event in relevant
            for ref in _refs(event.get("evidence_refs"))
        )
    )
    if node_status == "VERIFICATION_FAILED":
        status = "FAIL"
    elif node_status in {
        "VERIFIED",
        "WAITING_ACCEPTANCE_REVIEW",
        "ACCEPTED",
    }:
        status = "PASS"
    elif node_status == "VERIFYING":
        status = "RUNNING"
    else:
        status = "NOT_RUN"
    return {
        "node_id": node_id,
        "status": status,
        "evidence_refs": evidence_refs,
        "source": "runtime_event" if relevant else "node_status_projection",
        "last_event_cursor": max((_int(event.get("cursor")) for event in relevant), default=None),
    }


def build_execution_review_read_model(
    task_snapshot: Mapping[str, Any],
    *,
    reviews: Iterable[Mapping[str, Any]] = (),
    runtime_events: Iterable[Mapping[str, Any]] = (),
    generated_at_ms: int | None = None,
    after_cursor: int = 0,
    page_limit: int = 100,
) -> dict[str, Any]:
    """Build one replayable execution/review projection without side effects."""

    generated = max(0, _int(generated_at_ms, default=now_ms()))
    snapshot = _mapping(task_snapshot)
    graph_record = _mapping(snapshot.get("graph_record"))
    graph, node_records = _graph_parts(graph_record)
    graph_id = _text(graph.get("graph_id") or graph_record.get("graph_id"))
    revision = max(1, _int(graph.get("revision") or graph_record.get("revision"), default=1))

    attempts = [_attempt_view(_mapping(item)) for item in _sequence(snapshot.get("attempts"))]
    attempts.sort(key=lambda item: (str(item["node_id"]), int(item["attempt_no"]), str(item["attempt_id"])))
    attempts_by_node: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        attempts_by_node.setdefault(str(attempt["node_id"]), []).append(attempt)

    event_candidates = [_event_view(_mapping(item)) for item in runtime_events]
    event_candidates = [event for event in event_candidates if event["cursor"] > max(0, int(after_cursor))]
    event_candidates.sort(key=lambda item: (int(item["cursor"]), str(item["event_id"])))
    deduped_events: list[dict[str, Any]] = []
    seen_dedupe: set[str] = set()
    for event in event_candidates:
        dedupe = _text(event.get("dedupe_key")) or f"cursor:{event['cursor']}"
        if dedupe in seen_dedupe:
            continue
        seen_dedupe.add(dedupe)
        deduped_events.append(event)
    bounded_limit = max(1, min(1000, int(page_limit)))
    has_more = len(deduped_events) > bounded_limit
    timeline = deduped_events[:bounded_limit]

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    verification: list[dict[str, Any]] = []
    for raw_record in node_records:
        record = _mapping(raw_record)
        node = _mapping(record.get("node"))
        node_id = _text(node.get("node_id") or record.get("node_id"))
        status = _text(record.get("status") or node.get("status")) or "UNKNOWN"
        dependencies = _refs(node.get("dependencies"))
        aggregate_id = f"{graph_id}@{revision}/{node_id}"
        node_events = [event for event in timeline if event.get("aggregate_id") == aggregate_id]
        node_verification = _verification_view(
            node_id=node_id,
            node_status=status,
            matching_events=node_events,
        )
        verification.append(node_verification)
        nodes.append(
            {
                "node_id": node_id,
                "dependencies": dependencies,
                "route": _text(node.get("route")),
                "risk_level": max(1, min(5, _int(node.get("risk_level"), default=1))),
                "acceptance_criteria": [
                    public_decision_summary(value, fallback="Acceptance criterion recorded.", max_chars=200)
                    for value in _sequence(node.get("acceptance_criteria"))
                ],
                "status": status,
                "status_version": max(0, _int(record.get("status_version") or node.get("status_version"))),
                "terminal": status in _TERMINAL_NODE_STATUSES,
                "attempts": attempts_by_node.get(node_id, []),
                "verification": node_verification,
                "created_ts_ms": max(0, _int(record.get("created_ts_ms"))),
                "updated_ts_ms": max(0, _int(record.get("updated_ts_ms"))),
            }
        )
        edges.extend(
            {"from": dependency, "to": node_id}
            for dependency in dependencies
        )

    nodes.sort(key=lambda item: str(item["node_id"]))
    edges.sort(key=lambda item: (str(item["from"]), str(item["to"])))
    budgets = [_budget_view(_mapping(item)) for item in _sequence(snapshot.get("budgets"))]
    budgets.sort(key=lambda item: (str(item.get("node_id") or ""), str(item["budget_id"])))
    review_views = [_review_view(_mapping(item), generated_at_ms=generated) for item in reviews]
    review_views.sort(key=lambda item: (int(item["created_ts_ms"]), str(item["review_id"])))

    graph_status = _text(graph_record.get("status") or graph.get("status")) or "UNKNOWN"
    node_status_counts = Counter(str(node["status"]) for node in nodes)
    attempt_status_counts = Counter(str(attempt["status"]) for attempt in attempts)
    review_status_counts = Counter(str(review["status"]) for review in review_views)
    next_cursor = int(timeline[-1]["cursor"]) if timeline else max(0, int(after_cursor))

    return {
        "schema_version": EXECUTION_REVIEW_SCHEMA_VERSION,
        "generated_at_ms": generated,
        "read_only": True,
        "source_contracts": [
            "TaskGraph",
            "TaskNode",
            "TaskAttempt",
            "BudgetReservation",
            "VerificationResult",
            "ReviewItem",
            "RuntimeEvent",
        ],
        "mutation_contract": {
            "direct_database_write": False,
            "review_commands": ["approve", "reject", "resume"],
            "resume_requires_bound_token": True,
            "automatic_resume": False,
        },
        "execution": {
            "graph_id": graph_id,
            "revision": revision,
            "root_work_order_id": _text(graph.get("root_work_order_id")),
            "planner_ref": _text(graph.get("planner_ref")),
            "status": graph_status,
            "status_version": max(0, _int(graph_record.get("status_version"))),
            "terminal": graph_status in _TERMINAL_GRAPH_STATUSES,
            "source_kind": _text(graph_record.get("source_kind")) or "CANONICAL",
            "created_ts_ms": max(0, _int(graph_record.get("created_ts_ms") or graph.get("created_at"))),
            "updated_ts_ms": max(0, _int(graph_record.get("updated_ts_ms"))),
        },
        "dag": {
            "nodes": nodes,
            "edges": edges,
            "node_status_counts": dict(sorted(node_status_counts.items())),
        },
        "attempts": attempts,
        "attempt_status_counts": dict(sorted(attempt_status_counts.items())),
        "budgets": budgets,
        "verification": verification,
        "reviews": review_views,
        "review_status_counts": dict(sorted(review_status_counts.items())),
        "timeline": timeline,
        "cursor_page": {
            "after": max(0, int(after_cursor)),
            "next_cursor": next_cursor,
            "event_count": len(timeline),
            "has_more": has_more,
            "dedupe_field": "dedupe_key",
        },
    }


class OperatorReadModelService:
    """Read-only orchestration over governed durable stores."""

    def __init__(
        self,
        *,
        task_store: Any,
        review_store: Any,
        event_store: Any,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._task_store = task_store
        self._review_store = review_store
        self._event_store = event_store
        self._clock = clock

    def list_executions(self, *, limit: int = 100) -> dict[str, Any]:
        records = self._task_store.list_graphs(limit=limit)
        return build_execution_index_read_model(records, generated_at_ms=self._clock())

    def get_review(self, review_id: str) -> dict[str, Any]:
        """Locate one durable review without exposing its stored private payload."""

        generated_at_ms = self._clock()
        review = _review_view(
            self._review_store.get_review(review_id),
            generated_at_ms=generated_at_ms,
        )
        return {
            "schema_version": REVIEW_LOCATOR_SCHEMA_VERSION,
            "generated_at_ms": generated_at_ms,
            "read_only": True,
            "found": True,
            "review_id": review["review_id"],
            "task_graph_id": review["task_graph_id"],
            "review": review,
        }

    def get_execution(
        self,
        graph_id: str,
        revision: int,
        *,
        after_cursor: int = 0,
        event_limit: int = 100,
    ) -> dict[str, Any]:
        snapshot = self._task_store.read_scheduler_snapshot(graph_id, revision)
        graph_record = _mapping(snapshot.get("graph_record"))
        _, node_records = _graph_parts(graph_record)
        attempts = [_mapping(item) for item in _sequence(snapshot.get("attempts"))]
        budgets = [_mapping(item) for item in _sequence(snapshot.get("budgets"))]
        reviews = self._review_store.list_reviews(
            task_graph_id=str(graph_id),
            limit=500,
        )
        aggregate_refs: list[tuple[str, str]] = [("TASK_GRAPH", str(graph_id))]
        aggregate_refs.extend(
            (
                "TASK_NODE",
                f"{graph_id}@{int(revision)}/{_text(_mapping(record.get('node')).get('node_id') or record.get('node_id'))}",
            )
            for record in node_records
        )
        aggregate_refs.extend(
            ("TASK_ATTEMPT", _text(attempt.get("attempt_id")))
            for attempt in attempts
            if _text(attempt.get("attempt_id"))
        )
        aggregate_refs.extend(
            ("BUDGET_RESERVATION", _text(budget.get("budget_id")))
            for budget in budgets
            if _text(budget.get("budget_id"))
        )
        aggregate_refs.extend(
            ("REVIEW", _text(_mapping(review.get("review")).get("review_id")))
            for review in reviews
            if _text(_mapping(review.get("review")).get("review_id"))
        )
        requested = max(1, min(1000, int(event_limit)))
        events = self._event_store.list_events_for_aggregates(
            aggregate_refs=aggregate_refs,
            after_cursor=after_cursor,
            limit=min(1000, requested + 1),
        )
        return build_execution_review_read_model(
            snapshot,
            reviews=reviews,
            runtime_events=events,
            generated_at_ms=self._clock(),
            after_cursor=after_cursor,
            page_limit=requested,
        )


__all__ = [
    "EXECUTION_INDEX_SCHEMA_VERSION",
    "EXECUTION_REVIEW_SCHEMA_VERSION",
    "REVIEW_LOCATOR_SCHEMA_VERSION",
    "OperatorReadModelService",
    "build_execution_index_read_model",
    "build_execution_review_read_model",
]
