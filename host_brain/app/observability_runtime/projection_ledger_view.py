"""Bounded, privacy-preserving ledger statistics projection.

The source job is the only code in this module allowed to open the canonical
ledger.  HTTP handlers construct :class:`ProjectionLedgerView` exclusively
from the already initialized ``QueryProjectionStore`` and therefore cannot
trigger ledger startup, migrations, or an on-demand refresh.

This is deliberately a *statistics* view, not a ledger mirror: every source
table and field is allowlisted, rows are retained for at most 30 days, and a
table can materialize at most 5,000 rows.  Private bodies, raw inputs and
secrets are never selected into the projection payload.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Mapping
from functools import wraps
from typing import Any

from fastapi import HTTPException, status
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.observability.projection_store import (
    QueryProjectionStore,
    digest_json,
    public_projection_payload,
)


LEDGER_STATS_KIND = "ledger_stats_table"
LEDGER_STATS_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
LEDGER_STATS_ROW_CAP = 5_000
LEDGER_STATS_MAX_AGE_MS = 60_000
LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR = "0:source-unavailable"


class ProjectionLedgerViewUnavailable(HTTPException):
    """Stable direct-route failure for an unusable statistics projection."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "PROJECTION_SOURCE_UNAVAILABLE")
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "reason_code": self.reason_code,
                "detail": "required statistics projection is unavailable",
            },
        )

_IDENTITY_FIELDS = frozenset(
    {
        "ts_ms", "created_at_ms", "created_ts_ms", "updated_ts_ms",
        "committed_at_ms",
        "request_id", "session_id", "trace_id", "event_id", "id", "seq",
        "task_id", "cycle_id", "proposal_id", "checkpoint_id", "manifest_id",
    }
)
_OUTCOME_FIELDS = frozenset(
    {
        "stage", "phase", "status", "result_status", "decision", "reason_code",
        "reason_codes", "error_code", "audit_tag", "audit_event", "event",
        "event_type", "op_kind", "operation", "action", "source", "source_kind",
        "source_status", "direction", "channel", "side", "blocked", "allowed",
        "success", "failed", "is_fallback", "fallback_reason", "risk_level",
        "impact_level", "violation_code", "root_cause_code", "parse_stage",
        "lint_result", "reversible", "is_reversible", "has_summary",
    }
)
_DIMENSION_FIELDS = frozenset(
    {
        "node_id", "node_type", "parent_node_id", "owner_node_id", "graph_id",
        "subgraph_id", "model", "model_id", "selected_model", "provider",
        "model_target", "skill_name", "tool_name", "task_type", "name",
        "display_name", "proposer_class", "artifact_type", "update_kind",
        "candidate_key", "fact_key", "topic_id", "source_id", "source_label",
        "bridge_backend", "expression", "emotion_tag", "from_wxid", "wxid",
        "group_id", "user_id", "ingress_classification",
    }
)
_MEASURE_FIELDS = frozenset(
    {
        "latency_ms", "duration_ms", "total_latency_ms", "count", "total",
        "request_count", "run_count", "error_count", "success_count",
        "failed_count", "fail_count", "detected_count", "executed_count",
        "occurrence_count", "node_count", "slots_filled", "input_chars",
        "output_chars", "input_turns", "output_turns", "confidence",
        "confidence_pct", "score", "priority_score", "health_score",
        "success_rate", "failure_rate", "window_failure_rate",
        "window_p95_latency_ms", "adaptive_tune_count", "topics_processed",
        "semantic_memories_created", "proposals_submitted", "proposals_skipped",
        "first_detected_ts_ms", "last_detected_ts_ms", "last_execution_ts_ms",
        "last_run_ts", "resume_deadline_at", "gc_at_ms", "undone_at_ms",
    }
)


def _fields(*extra: str) -> frozenset[str]:
    return frozenset(_IDENTITY_FIELDS | _OUTCOME_FIELDS | _DIMENSION_FIELDS | _MEASURE_FIELDS | set(extra))


# Explicit table authority.  A new ledger table is invisible until reviewed
# and added here; no route-controlled name can expand the source query surface.
LEDGER_STATS_TABLE_FIELDS: dict[str, frozenset[str]] = {
    "action_plan_log": _fields("action_count"),
    "agent_loop_log": _fields("round"),
    "airlock_log": _fields(),
    "avatar_action_log": _fields("trigger", "expression_id"),
    "audit_log": _fields(),
    "bond_update_log": _fields(),
    "capability_execution_receipt": _fields(
        "idempotency_key",
        "attempt_id",
        "capability_id",
        "kind",
    ),
    "channel_io_log": _fields(),
    "error_log": _fields(),
    "failure_log": _fields(),
    "governor_log": _fields(),
    "intent_log": _fields(),
    "llm_pool_call_log": _fields(
        "scene_type",
        "cognitive_tier",
        "attempt_count",
        "final_model",
        "final_provider",
        "pool_exhausted",
        "cached",
        "degraded_to_rule",
        "total_latency_ms",
    ),
    "llm_route_log": _fields(),
    "llm_token_usage_log": _fields(
        "scene_type",
        "cognitive_tier",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "billable_prompt_tokens",
        "billable_completion_tokens",
        "billable_total_tokens",
        "estimated",
        "cached",
        "pool_exhausted",
        "attempt_count",
        "billable_attempt_count",
    ),
    "life_memory_admission_receipt": _fields("worth_score"),
    "model_attempt_log": _fields(
        "attempt_id",
        "route_decision_id",
        "attempt_index",
        "adapter_id",
        "retry_reason",
        "prompt_id",
        "prompt_version",
        "started_at_ms",
        "ended_at_ms",
        "model_node_id",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ),
    "memory_ops_log": _fields(),
    "permission_log": _fields(),
    "prompt_builder_log": _fields(),
    "qa_violation": _fields(),
    "qa_violation_log": _fields(),
    "rate_limit_store_log": _fields(),
    "retrieval_event_log": _fields(),
    "scheduler_log": _fields("last_gate_state", "last_mood_state", "last_reason_code"),
    "scheduler_event_log": _fields("trigger_ts"),
    "skill_exec_log": _fields(),
    "state_arbiter_log": _fields(),
    "task_log": _fields("last_gate_state", "last_mood_state", "last_reason_code"),
    "ui_assertion_log": _fields(),
    "vault_manifest_index": _fields(),
    "vision_event_log": _fields(),
    "vision_log": _fields(),
    "voice_event_log": _fields(),
}

_PRIVATE_OR_SECRET_FIELDS = frozenset(
    {
        "acceptance_criteria", "authorization", "chunk", "cookie", "credential",
        "detail", "evidence", "graph_trace_flat_json", "input_data", "input_text",
        "input_preview", "narrative", "normalized_intent", "normalized_json",
        "objective", "output_data", "output_text", "password", "payload",
        "private_reasoning", "prompt", "proposed_intent_draft_json", "raw_text",
        "response", "rule_trace", "secret", "signature", "task_description", "token",
    }
)

# Voice input payloads contain private bodies, but these four enum/numeric
# dimensions are required to build gateway health without retaining or
# querying the raw payload.  They are flattened by the background projector
# and later re-wrapped into a minimal public payload for the gateway read
# model.
_PUBLIC_VOICE_PAYLOAD_FIELDS = frozenset(
    {"platform", "source", "phase", "latency_ms"}
)
_PUBLIC_IMMUNE_GATE_FIELDS = frozenset(
    {"decision", "risk_level", "score", "reason_codes", "matched_rules"}
)


def _row_ts(row: Mapping[str, Any]) -> int:
    for field in (
        "ts_ms",
        "created_at_ms",
        "created_ts_ms",
        "updated_ts_ms",
        "committed_at_ms",
    ):
        try:
            value = int(row.get(field) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def _public_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, (list, tuple)):
        return [_public_scalar(item) for item in list(value)[:16] if isinstance(item, (type(None), bool, int, float, str))]
    if isinstance(value, Mapping):
        # Only shallow, scalar lint/status metadata is useful to statistics.
        return {
            str(key)[:64]: _public_scalar(item)
            for key, item in list(value.items())[:16]
            if str(key) not in _PRIVATE_OR_SECRET_FIELDS
            and isinstance(item, (type(None), bool, int, float, str))
        }
    return str(value)[:128]


def sanitize_stats_row(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    allowed = LEDGER_STATS_TABLE_FIELDS.get(str(table))
    if allowed is None:
        return {}
    projected = {
        field: _public_scalar(row.get(field))
        for field in sorted(allowed)
        if field in row and field not in _PRIVATE_OR_SECRET_FIELDS
    }
    if str(table) == "voice_event_log":
        raw_payload = row.get("payload")
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                raw_payload = None
        if isinstance(raw_payload, Mapping):
            for field in _PUBLIC_VOICE_PAYLOAD_FIELDS:
                value = raw_payload.get(field)
                if isinstance(value, (type(None), bool, int, float, str)):
                    projected[field] = _public_scalar(value)
            event_type = str(row.get("event_type") or "").strip().upper()
            gate_payload: Any = None
            if event_type == "IMMUNE_CHAT_GATE":
                gate_payload = raw_payload
            elif event_type.startswith("INPUT_"):
                gate_payload = raw_payload.get("immune_gate")
            if isinstance(gate_payload, Mapping):
                for field in _PUBLIC_IMMUNE_GATE_FIELDS:
                    value = gate_payload.get(field)
                    if isinstance(value, (type(None), bool, int, float, str, list, tuple)):
                        projected[field] = _public_scalar(value)
    elif str(table) == "model_attempt_log":
        raw_metadata = row.get("metadata")
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = json.loads(raw_metadata)
            except json.JSONDecodeError:
                raw_metadata = None
        if isinstance(raw_metadata, Mapping):
            model_node_id = raw_metadata.get("model_node_id")
            if isinstance(model_node_id, str) and model_node_id.strip():
                projected["model_node_id"] = _public_scalar(model_node_id.strip())
            measurements = raw_metadata.get("provider_budget_measurements")
            if isinstance(measurements, Mapping):
                for source_field, projected_field in (
                    ("prompt_tokens", "prompt_tokens"),
                    ("completion_tokens", "completion_tokens"),
                    ("token_count", "total_tokens"),
                ):
                    value = measurements.get(source_field)
                    if (
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and value >= 0
                    ):
                        projected[projected_field] = value
    return projected


def _stable_row_key(row: Mapping[str, Any]) -> tuple[int, str]:
    identity = {
        key: row.get(key)
        for key in sorted(_IDENTITY_FIELDS)
        if row.get(key) is not None
    }
    digest = hashlib.sha256(
        json.dumps(identity or dict(row), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return _row_ts(row), digest


def _load_table_rows(ledger: Any, table: str, *, now_ms: int) -> tuple[list[dict[str, Any]], bool, str]:
    start_ms = max(0, int(now_ms) - LEDGER_STATS_RETENTION_MS)
    resolver = getattr(ledger, "resolve_fetch_table", None)
    resolved = resolver(table) if callable(resolver) else table
    if not resolved:
        # An allowlisted logical table whose canonical source cannot be
        # resolved is unavailable, not an observed healthy empty table.
        return [], False, LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR
    try:
        source_rows = ledger.fetch_by_time_range(
            resolved,
            start_ms=start_ms,
            end_ms=int(now_ms),
            limit=LEDGER_STATS_ROW_CAP + 1,
            offset=0,
            descending=True,
        )
    except Exception:
        return [], False, LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR
    # The canonical ledger returns newest-first with a stable timestamp + row
    # identity order.  Select the bounded latest set *before* reordering the
    # public rows so a same-timestamp overflow cannot evict a newer row.
    truncated = len(source_rows) > LEDGER_STATS_ROW_CAP
    source_rows = source_rows[:LEDGER_STATS_ROW_CAP]
    rows = [
        sanitize_stats_row(table, row)
        for row in source_rows
        if isinstance(row, Mapping) and start_ms <= _row_ts(row) <= int(now_ms)
    ]
    rows = [row for row in rows if row]
    rows.sort(key=_stable_row_key)
    last_ts, last_digest = _stable_row_key(rows[-1]) if rows else (0, "empty")
    return rows, truncated, f"{last_ts}:{last_digest}"


def refresh_ledger_stats_projection(
    *,
    ledger: Any,
    store: QueryProjectionStore,
) -> dict[str, int]:
    """Background source job; never call from an HTTP or WebSocket handler."""

    now_ms = int(time.time() * 1000)
    counts: dict[str, int] = {}
    for table in sorted(LEDGER_STATS_TABLE_FIELDS):
        rows, truncated, stable_cursor = _load_table_rows(ledger, table, now_ms=now_ms)
        current = store.get(LEDGER_STATS_KIND, table)
        payload = {
            "table": table,
            "rows": rows,
            "retention_ms": LEDGER_STATS_RETENTION_MS,
            "row_cap": LEDGER_STATS_ROW_CAP,
            "truncated": truncated,
            "stable_cursor": stable_cursor,
        }
        payload_digest = digest_json(public_projection_payload(payload))
        if current is not None and current.source_digest == payload_digest:
            # A poll is not a new source fact.  Reusing the source identity
            # lets QueryProjectionStore refresh freshness/state in place while
            # its immutable journal INSERT remains an idempotent no-op.
            source_cursor = int(current.source_cursor)
            source_digest = str(current.source_digest)
        else:
            source_cursor = max(
                now_ms,
                int(current.source_cursor) + 1 if current is not None else now_ms,
            )
            source_digest = payload_digest
        source_unavailable = stable_cursor == LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR
        store.upsert(
            LEDGER_STATS_KIND,
            table,
            payload,
            source_cursor=source_cursor,
            source_digest=source_digest,
            source_ref=f"ledger-stats://{table}",
            freshness_ts_ms=now_ms,
            degraded_state=(
                ProjectionDegradedState.SOURCE_UNAVAILABLE
                if source_unavailable
                else ProjectionDegradedState.HEALTHY
            ),
        )
        counts[table] = len(rows)
    return counts


class ProjectionLedgerView:
    """Read-only ledger-shaped facade over already materialized table rows."""

    def __init__(
        self,
        store: QueryProjectionStore | None,
        *,
        clock: Any | None = None,
        fail_closed: bool = False,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: int(time.time() * 1000))
        self.fail_closed = bool(fail_closed)
        self.last_degraded_reason: str | None = None
        self._degradation_count = 0
        self._last_degradation_event_reason: str | None = None
        self._coverage_by_table: dict[str, tuple[bool, int | None]] = {}

    @property
    def degradation_count(self) -> int:
        return self._degradation_count

    def degradation_reason_since(self, count: int) -> str | None:
        if self._degradation_count <= max(0, int(count)):
            return None
        return self._last_degradation_event_reason or "PROJECTION_SOURCE_UNAVAILABLE"

    def _degraded(self, reason_code: str) -> list[dict[str, Any]]:
        reason = str(reason_code or "PROJECTION_SOURCE_UNAVAILABLE")
        self.last_degraded_reason = reason
        self._last_degradation_event_reason = reason
        self._degradation_count += 1
        return []

    def resolve_fetch_table(self, *candidates: str) -> str | None:
        for candidate in candidates:
            table = str(candidate or "").strip()
            if table in LEDGER_STATS_TABLE_FIELDS:
                return table
        self._degraded("PROJECTION_TABLE_NOT_ALLOWLISTED")
        return None

    def _rows(
        self,
        table: str,
        *,
        allow_truncated: bool = False,
    ) -> list[dict[str, Any]]:
        resolved = self.resolve_fetch_table(table)
        if resolved is None:
            return []
        self._coverage_by_table.pop(resolved, None)
        if self._store is None:
            return self._degraded("PROJECTION_STORE_NOT_INITIALIZED")
        try:
            document = self._store.get(LEDGER_STATS_KIND, resolved)
        except Exception:
            return self._degraded("PROJECTION_SOURCE_UNAVAILABLE")
        if document is None:
            return self._degraded("PROJECTION_REBUILDING")
        freshness = int(getattr(document, "freshness_ts_ms", 0) or 0)
        if freshness and int(self._clock()) - freshness > LEDGER_STATS_MAX_AGE_MS:
            return self._degraded("PROJECTION_STALE")
        state = getattr(document, "degraded_state", None)
        state_value = str(getattr(state, "value", state) or "HEALTHY").upper()
        if state_value != "HEALTHY":
            return self._degraded(f"PROJECTION_{state_value}")
        payload = dict(document.payload)
        stable_cursor = str(payload.get("stable_cursor") or "").strip().lower()
        if stable_cursor.endswith(":source-unavailable"):
            return self._degraded("PROJECTION_SOURCE_UNAVAILABLE")
        if int(payload.get("retention_ms") or 0) != LEDGER_STATS_RETENTION_MS:
            return self._degraded("PROJECTION_POLICY_MISMATCH")
        if int(payload.get("row_cap") or 0) != LEDGER_STATS_ROW_CAP:
            return self._degraded("PROJECTION_POLICY_MISMATCH")
        rows = payload.get("rows")
        if not isinstance(rows, list) or len(rows) > LEDGER_STATS_ROW_CAP:
            return self._degraded("PROJECTION_ROW_CAP_INVALID")
        public_rows = [dict(row) for row in rows if isinstance(row, Mapping)]
        truncated = bool(payload.get("truncated", False))
        coverage_start = min((_row_ts(row) for row in public_rows), default=None)
        self._coverage_by_table[resolved] = (truncated, coverage_start)
        if truncated and not allow_truncated:
            return self._degraded("PROJECTION_SOURCE_TRUNCATED")
        self.last_degraded_reason = None
        return public_rows

    def fetch_by_time_range(
        self,
        table: str,
        start_ms: int = 0,
        end_ms: int | None = None,
        *,
        limit: int = LEDGER_STATS_ROW_CAP,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        now_ms = int(self._clock())
        start = max(0, int(start_ms or 0))
        end = now_ms if end_ms is None else max(0, int(end_ms))
        if start < now_ms - LEDGER_STATS_RETENTION_MS or end < start:
            return self._degraded("PROJECTION_WINDOW_OUT_OF_RANGE")
        resolved = str(table or "").strip()
        source_rows = self._rows(resolved, allow_truncated=True)
        truncated, coverage_start = self._coverage_by_table.get(
            resolved,
            (False, None),
        )
        # A latest-N materialization can answer only windows strictly newer
        # than its oldest retained timestamp.  Equality is also ambiguous:
        # older rows sharing that millisecond may have been trimmed by the
        # durable id/rowid tie-break.  Fail closed instead of returning an
        # exact-looking partial total/rate.
        if truncated and (coverage_start is None or start <= coverage_start):
            return self._degraded("PROJECTION_SOURCE_TRUNCATED")
        rows = [row for row in source_rows if start <= _row_ts(row) <= end]
        rows.sort(key=_stable_row_key)
        begin = min(LEDGER_STATS_ROW_CAP, max(0, int(offset)))
        cap = min(LEDGER_STATS_ROW_CAP, max(0, int(limit)))
        return rows[begin : begin + cap]

    def fetch_by_request_id(self, table: str, request_id: str) -> list[dict[str, Any]]:
        target = str(request_id or "").strip()
        if not target:
            return []
        resolved = str(table or "").strip()
        source_rows = self._rows(resolved, allow_truncated=True)
        rows = [
            row
            for row in source_rows
            if str(row.get("request_id") or "") == target
        ]
        truncated, _coverage_start = self._coverage_by_table.get(
            resolved,
            (False, None),
        )
        if truncated and not rows:
            return self._degraded("PROJECTION_SOURCE_TRUNCATED")
        rows.sort(key=_stable_row_key)
        return rows[-LEDGER_STATS_ROW_CAP:]

    def __getattr__(self, name: str) -> Any:
        if name in LEDGER_STATS_TABLE_FIELDS:
            return self._rows(name)
        raise AttributeError(name)


def fail_closed_projection_route(handler: Any) -> Any:
    """Convert any degraded view read during a direct route into typed 503.

    Several legacy statistics builders catch broad ``Exception`` and replace
    failed reads with empty lists.  The route boundary therefore checks a
    monotonic degradation counter after the handler returns.
    """

    signature = inspect.signature(handler)

    def resolve_view(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        bound = signature.bind_partial(*args, **kwargs)
        return bound.arguments.get("projection_view") or bound.arguments.get(
            "ledger"
        )

    def guard(view: Any, baseline: int, result: Any) -> Any:
        if bool(getattr(view, "fail_closed", False)):
            reason = view.degradation_reason_since(baseline)
            if reason:
                raise ProjectionLedgerViewUnavailable(reason)
        return result

    if inspect.iscoroutinefunction(handler):

        @wraps(handler)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            view = resolve_view(args, kwargs)
            baseline = int(getattr(view, "degradation_count", 0))
            result = await handler(*args, **kwargs)
            return guard(view, baseline, result)

        return async_wrapper

    @wraps(handler)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        view = resolve_view(args, kwargs)
        baseline = int(getattr(view, "degradation_count", 0))
        result = handler(*args, **kwargs)
        return guard(view, baseline, result)

    return wrapper


__all__ = [
    "LEDGER_STATS_KIND",
    "LEDGER_STATS_MAX_AGE_MS",
    "LEDGER_STATS_RETENTION_MS",
    "LEDGER_STATS_ROW_CAP",
    "LEDGER_STATS_SOURCE_UNAVAILABLE_CURSOR",
    "LEDGER_STATS_TABLE_FIELDS",
    "ProjectionLedgerView",
    "ProjectionLedgerViewUnavailable",
    "fail_closed_projection_route",
    "refresh_ledger_stats_projection",
    "sanitize_stats_row",
]
