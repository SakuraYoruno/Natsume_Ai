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
import json
import time
from collections.abc import Mapping
from typing import Any

from .projection_daemon import register_projection_job
from host_brain.observability.projection_store import QueryProjectionStore, digest_json


LEDGER_STATS_KIND = "ledger_stats_table"
LEDGER_STATS_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
LEDGER_STATS_ROW_CAP = 5_000
LEDGER_STATS_MAX_AGE_MS = 60_000

_IDENTITY_FIELDS = frozenset(
    {
        "ts_ms", "created_at_ms", "updated_ts_ms", "committed_at_ms",
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
    "audit_log": _fields(),
    "bond_update_log": _fields(),
    "channel_io_log": _fields(),
    "context_compress_log": _fields(),
    "context_compressor_log": _fields(),
    "distill_log": _fields(),
    "distillation_cycle_log": _fields(),
    "episodic_store_log": _fields(),
    "error_log": _fields(),
    "failure_log": _fields(),
    "governor_log": _fields(),
    "graph_run_trace": _fields(),
    "intent_log": _fields(),
    "llm_call_log": _fields(),
    "llm_node_call_log": _fields(),
    "llm_route_log": _fields(),
    "memory_guard_log": _fields(),
    "memory_ops_log": _fields(),
    "permission_log": _fields(),
    "prompt_builder_log": _fields(),
    "qa_violation": _fields(),
    "qa_violation_log": _fields(),
    "rate_limit_store_log": _fields(),
    "retrieval_event_log": _fields(),
    "sandbox_exec_log": _fields(),
    "scheduler_log": _fields("last_gate_state", "last_mood_state", "last_reason_code"),
    "skill_exec_log": _fields(),
    "state_arbiter_log": _fields(),
    "talk_gate_log": _fields(),
    "task_log": _fields("last_gate_state", "last_mood_state", "last_reason_code"),
    "ui_assertion_log": _fields(),
    "vault_manifest_index": _fields(),
    "vision_event_log": _fields(),
    "vision_log": _fields(),
    "voice_event_log": _fields(),
    "vts_event_log": _fields(),
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
    for field in ("ts_ms", "created_at_ms", "updated_ts_ms", "committed_at_ms"):
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
        return [], False, "0:empty"
    try:
        try:
            source_rows = ledger.fetch_by_time_range(
                resolved,
                start_ms=start_ms,
                end_ms=int(now_ms),
                limit=LEDGER_STATS_ROW_CAP + 1,
                offset=0,
            )
        except TypeError:
            source_rows = ledger.fetch_by_time_range(
                resolved,
                start_ms=start_ms,
                end_ms=int(now_ms),
                limit=LEDGER_STATS_ROW_CAP + 1,
            )
    except Exception:
        return [], False, "0:source-unavailable"
    rows = [
        sanitize_stats_row(table, row)
        for row in source_rows
        if isinstance(row, Mapping) and start_ms <= _row_ts(row) <= int(now_ms)
    ]
    rows = [row for row in rows if row]
    rows.sort(key=_stable_row_key)
    truncated = len(rows) > LEDGER_STATS_ROW_CAP
    rows = rows[-LEDGER_STATS_ROW_CAP:]
    last_ts, last_digest = _stable_row_key(rows[-1]) if rows else (0, "empty")
    return rows, truncated, f"{last_ts}:{last_digest}"


def refresh_ledger_stats_projection(
    *,
    ledger: Any | None = None,
    store: QueryProjectionStore | None = None,
) -> dict[str, int]:
    """Background source job; never call from an HTTP or WebSocket handler."""

    if ledger is None or store is None:
        from host_brain.composition.process_services import (  # noqa: PLC0415
            get_ledger,
            get_query_projection_store,
        )

        ledger = ledger or get_ledger()
        store = store or get_query_projection_store()
    now_ms = int(time.time() * 1000)
    counts: dict[str, int] = {}
    for table in sorted(LEDGER_STATS_TABLE_FIELDS):
        rows, truncated, stable_cursor = _load_table_rows(ledger, table, now_ms=now_ms)
        current = store.get(LEDGER_STATS_KIND, table)
        poll_cursor = max(now_ms, int(current.source_cursor) + 1 if current else now_ms)
        payload = {
            "table": table,
            "rows": rows,
            "retention_ms": LEDGER_STATS_RETENTION_MS,
            "row_cap": LEDGER_STATS_ROW_CAP,
            "truncated": truncated,
            "stable_cursor": stable_cursor,
        }
        store.upsert(
            LEDGER_STATS_KIND,
            table,
            payload,
            source_cursor=poll_cursor,
            source_digest=digest_json(payload),
            source_ref=f"ledger-stats://{table}",
            freshness_ts_ms=now_ms,
        )
        counts[table] = len(rows)
    return counts


class ProjectionLedgerView:
    """Read-only ledger-shaped facade over already materialized table rows."""

    def __init__(self, store: QueryProjectionStore | None, *, clock: Any | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: int(time.time() * 1000))
        self.last_degraded_reason: str | None = None

    def resolve_fetch_table(self, *candidates: str) -> str | None:
        for candidate in candidates:
            table = str(candidate or "").strip()
            if table in LEDGER_STATS_TABLE_FIELDS:
                return table
        return None

    def _rows(self, table: str) -> list[dict[str, Any]]:
        resolved = self.resolve_fetch_table(table)
        if resolved is None:
            self.last_degraded_reason = "PROJECTION_TABLE_NOT_ALLOWLISTED"
            return []
        if self._store is None:
            self.last_degraded_reason = "PROJECTION_STORE_NOT_INITIALIZED"
            return []
        document = self._store.get(LEDGER_STATS_KIND, resolved)
        if document is None:
            self.last_degraded_reason = "PROJECTION_REBUILDING"
            return []
        freshness = int(getattr(document, "freshness_ts_ms", 0) or 0)
        if freshness and int(self._clock()) - freshness > LEDGER_STATS_MAX_AGE_MS:
            self.last_degraded_reason = "PROJECTION_STALE"
            return []
        state = getattr(document, "degraded_state", None)
        state_value = str(getattr(state, "value", state) or "HEALTHY").upper()
        if state_value != "HEALTHY":
            self.last_degraded_reason = f"PROJECTION_{state_value}"
            return []
        payload = dict(document.payload)
        if int(payload.get("row_cap") or 0) != LEDGER_STATS_ROW_CAP:
            self.last_degraded_reason = "PROJECTION_POLICY_MISMATCH"
            return []
        rows = payload.get("rows")
        if not isinstance(rows, list) or len(rows) > LEDGER_STATS_ROW_CAP:
            self.last_degraded_reason = "PROJECTION_ROW_CAP_INVALID"
            return []
        self.last_degraded_reason = None
        return [dict(row) for row in rows if isinstance(row, Mapping)]

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
            self.last_degraded_reason = "PROJECTION_WINDOW_OUT_OF_RANGE"
            return []
        rows = [row for row in self._rows(table) if start <= _row_ts(row) <= end]
        rows.sort(key=_stable_row_key)
        begin = min(LEDGER_STATS_ROW_CAP, max(0, int(offset)))
        cap = min(LEDGER_STATS_ROW_CAP, max(0, int(limit)))
        return rows[begin : begin + cap]

    def fetch_by_request_id(self, table: str, request_id: str) -> list[dict[str, Any]]:
        target = str(request_id or "").strip()
        if not target:
            return []
        rows = [row for row in self._rows(table) if str(row.get("request_id") or "") == target]
        rows.sort(key=_stable_row_key)
        return rows[-LEDGER_STATS_ROW_CAP:]

    def __getattr__(self, name: str) -> Any:
        if name in LEDGER_STATS_TABLE_FIELDS:
            return self._rows(name)
        raise AttributeError(name)


def get_projection_ledger_view() -> ProjectionLedgerView:
    """GET-safe factory: peek at the store without creating any dependency."""

    from host_brain.composition.process_services import peek_query_projection_store

    return ProjectionLedgerView(peek_query_projection_store())


register_projection_job("ledger-stats", refresh_ledger_stats_projection)


__all__ = [
    "LEDGER_STATS_KIND",
    "LEDGER_STATS_MAX_AGE_MS",
    "LEDGER_STATS_RETENTION_MS",
    "LEDGER_STATS_ROW_CAP",
    "LEDGER_STATS_TABLE_FIELDS",
    "ProjectionLedgerView",
    "get_projection_ledger_view",
    "refresh_ledger_stats_projection",
    "sanitize_stats_row",
]
