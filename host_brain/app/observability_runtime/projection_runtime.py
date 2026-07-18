"""Production composition and domain adapters for the AW07 projection spine."""

from __future__ import annotations

import time
from typing import Any

from host_brain.agent_runtime.operator_read_model import OperatorReadModelService

from host_brain.observability.projection_store import QueryProjectionStore, digest_json
from host_brain.observability.projection_worker import ProjectionSource, QueryProjectionWorker


TASK_EXECUTION_KIND = "task_execution"
TASK_EXECUTION_INDEX_KEY = "index"
TASK_REVIEW_KIND = "task_review"
TRACE_KIND = "trace"
TRACE_STREAM_KIND = "trace_stream_events"
TRACE_FEED_KIND = "trace_feed"
TRACE_FEED_INDEX_KEY = "index"
TRIBE_EVENTS_KIND = "tribe_events"
TRIBE_EVENTS_INDEX_KEY = "index"
SESSION_KIND = "session"
SESSION_INDEX_KEY = "index"
def get_query_projection_worker() -> QueryProjectionWorker:
    from host_brain.composition.process_services import get_query_projection_store

    return QueryProjectionWorker(get_query_projection_store())


def projection_meta(
    kind: str,
    key: str,
    document: Any | None,
    *,
    max_age_ms: int,
) -> dict[str, Any]:
    now = int(time.time() * 1000)
    if document is None:
        return {
            "schema_version": "Natsume.operator.query_projection.v1",
            "kind": kind,
            "key": key,
            "available": False,
            "degraded_state": "REBUILDING",
            "reason_code": "OPERATOR_PROJECTION_NOT_READY",
            "freshness_ts_ms": 0,
        }
    age = max(0, now - int(document.freshness_ts_ms))
    stale = age > max(0, int(max_age_ms))
    durable_state = str(
        getattr(document.degraded_state, "value", document.degraded_state)
        or "HEALTHY"
    ).upper()
    effective_state = "STALE" if stale else durable_state
    available = not stale and durable_state == "HEALTHY"
    reason_code = None
    if stale:
        reason_code = "OPERATOR_PROJECTION_STALE"
    elif durable_state == "SOURCE_UNAVAILABLE":
        reason_code = "OPERATOR_PROJECTION_SOURCE_UNAVAILABLE"
    elif durable_state == "REBUILDING":
        reason_code = "OPERATOR_PROJECTION_NOT_READY"
    elif durable_state == "DIGEST_MISMATCH":
        reason_code = "OPERATOR_PROJECTION_DIGEST_MISMATCH"
    return {
        "schema_version": document.schema_version,
        "kind": kind,
        "key": key,
        "available": available,
        "projection_version": document.projection_version,
        "projection_cursor": document.projection_cursor,
        "source_cursor": document.source_cursor,
        "source_digest": document.source_digest,
        "freshness_ts_ms": document.freshness_ts_ms,
        "age_ms": age,
        "degraded_state": effective_state,
        "degraded_detail": document.degraded_detail,
        "reason_code": reason_code,
    }


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    raise TypeError("projection payload must be a mapping or model")


def _execution_cursor(payload: Mapping[str, Any]) -> int:
    values: list[int] = [0]
    for execution in list(payload.get("executions") or []):
        if isinstance(execution, Mapping):
            values.append(int(execution.get("updated_ts_ms") or 0))
    execution = payload.get("execution")
    if isinstance(execution, Mapping):
        values.append(int(execution.get("updated_ts_ms") or 0))
    page = payload.get("cursor_page")
    if isinstance(page, Mapping):
        values.append(int(page.get("next_cursor") or 0))
    return max(values, default=0)


class ProjectionOperatorReadModelService:
    """Production GET adapter: durable projection first, governed sources in worker."""

    def __init__(
        self,
        *,
        store: QueryProjectionStore,
        max_age_ms: int = 15000,
    ) -> None:
        self._store = store
        self._max_age_ms = max(0, int(max_age_ms))

    def list_executions(self, *, limit: int = 100) -> dict[str, Any]:
        document = self._store.get(TASK_EXECUTION_KIND, TASK_EXECUTION_INDEX_KEY)
        meta = projection_meta(
            TASK_EXECUTION_KIND,
            TASK_EXECUTION_INDEX_KEY,
            document,
            max_age_ms=self._max_age_ms,
        )
        if document is None or not meta["available"]:
            return {
                "schema_version": "Natsume.operator.execution_index.v1",
                "generated_at_ms": int(time.time() * 1000),
                "read_only": True,
                "source_contracts": ["TaskGraph", "TaskNode"],
                "executions": [],
                "execution_count": 0,
                "projection": meta,
            }
        payload = dict(document.payload)
        executions = list(payload.get("executions") or [])[: min(500, max(1, int(limit)))]
        payload["executions"] = executions
        payload["execution_count"] = len(executions)
        payload["projection"] = meta
        return payload

    def get_review(self, review_id: str) -> dict[str, Any]:
        key = str(review_id).strip()

        document = self._store.get(TASK_REVIEW_KIND, key)
        meta = projection_meta(TASK_REVIEW_KIND, key, document, max_age_ms=self._max_age_ms)
        if document is None or not meta["available"]:
            return {
                "schema_version": "Natsume.operator.review_locator.v1",
                "generated_at_ms": int(time.time() * 1000),
                "read_only": True,
                "found": False,
                "review_id": key,
                "task_graph_id": None,
                "review": None,
                "projection": meta,
            }
        payload = dict(document.payload)
        payload["projection"] = meta
        return payload

    def get_execution(
        self,
        graph_id: str,
        revision: int,
        *,
        after_cursor: int = 0,
        event_limit: int = 100,
    ) -> dict[str, Any]:
        key = f"{str(graph_id).strip()}@{int(revision)}"

        document = self._store.get(TASK_EXECUTION_KIND, key)
        meta = projection_meta(TASK_EXECUTION_KIND, key, document, max_age_ms=self._max_age_ms)
        if document is None or not meta["available"]:
            return {
                "schema_version": "Natsume.operator.execution_review.v1",
                "generated_at_ms": int(time.time() * 1000),
                "read_only": True,
                "execution": {"graph_id": str(graph_id), "revision": int(revision)},
                "timeline": [],
                "cursor_page": {
                    "after": max(0, int(after_cursor)),
                    "next_cursor": max(0, int(after_cursor)),
                    "event_count": 0,
                    "has_more": False,
                    "dedupe_field": "dedupe_key",
                },
                "projection": meta,
            }
        payload = dict(document.payload)
        after = max(0, int(after_cursor))
        limit = min(1000, max(1, int(event_limit)))
        candidates = [
            dict(item)
            for item in list(payload.get("timeline") or [])
            if isinstance(item, Mapping) and int(item.get("cursor") or 0) > after
        ]
        has_more = len(candidates) > limit
        timeline = candidates[:limit]
        payload["timeline"] = timeline
        payload["cursor_page"] = {
            "after": after,
            "next_cursor": int(timeline[-1].get("cursor") or after) if timeline else after,
            "event_count": len(timeline),
            "has_more": has_more,
            "dedupe_field": "dedupe_key",
        }
        payload["projection"] = meta
        return payload


def refresh_task_projections(
    source: OperatorReadModelService,
    worker: QueryProjectionWorker,
) -> dict[str, int]:
    """Independent worker entry; never invoked by a query service."""

    index_payload = _payload(source.list_executions(limit=500))
    index_cursor = _execution_cursor(index_payload)
    index_payload["generated_at_ms"] = index_cursor
    worker.store.upsert(
        TASK_EXECUTION_KIND,
        TASK_EXECUTION_INDEX_KEY,
        index_payload,
        source_cursor=index_cursor,
        source_digest=digest_json(index_payload),
        source_ref="task-runtime-background-worker",
    )
    execution_count = 0
    review_count = 0
    for execution in list(index_payload.get("executions") or []):
        if not isinstance(execution, Mapping):
            continue
        graph_id = str(execution.get("graph_id") or "").strip()
        revision = int(execution.get("revision") or 1)
        if not graph_id:
            continue
        value = _payload(source.get_execution(graph_id, revision, after_cursor=0, event_limit=1000))
        execution_cursor = _execution_cursor(value)
        value["generated_at_ms"] = execution_cursor
        worker.store.upsert(
            TASK_EXECUTION_KIND,
            f"{graph_id}@{revision}",
            value,
            source_cursor=execution_cursor,
            source_digest=digest_json(value),
            source_ref="task-runtime-background-worker",
        )
        execution_count += 1
        for review in list(value.get("reviews") or []):
            if not isinstance(review, Mapping):
                continue
            review_id = str(review.get("review_id") or "").strip()
            if not review_id:
                continue
            review_payload = _payload(source.get_review(review_id))
            review = review_payload.get("review")
            review_cursor = int(review.get("updated_ts_ms") or 0) if isinstance(review, Mapping) else 0
            review_payload["generated_at_ms"] = review_cursor
            worker.store.upsert(
                TASK_REVIEW_KIND,
                review_id,
                review_payload,
                source_cursor=review_cursor,
                source_digest=digest_json(review_payload),
                source_ref="task-review-background-worker",
            )
            review_count += 1
    return {"index_count": 1, "execution_count": execution_count, "review_count": review_count}


def trace_source(request_id: str, value: Any) -> ProjectionSource:
    payload = _payload(value)
    cursor = max(
        [int(payload.get("ts_ms") or 0), int(payload.get("timestamp") or 0)]
        + [
            int(item.get("ts_ms") or 0)
            for item in list(payload.get("nodes") or [])
            if isinstance(item, Mapping)
        ],
        default=0,
    )
    return ProjectionSource(
        key=str(request_id).strip(),
        payload=payload,
        cursor=cursor,
        digest=digest_json(payload),
    )


__all__ = [
    "ProjectionOperatorReadModelService",
    "SESSION_INDEX_KEY",
    "SESSION_KIND",
    "TASK_EXECUTION_INDEX_KEY",
    "TASK_EXECUTION_KIND",
    "TASK_REVIEW_KIND",
    "TRACE_KIND",
    "TRACE_STREAM_KIND",
    "TRACE_FEED_KIND",
    "TRACE_FEED_INDEX_KEY",
    "TRIBE_EVENTS_KIND",
    "TRIBE_EVENTS_INDEX_KEY",
    "get_query_projection_worker",
    "trace_source",
    "projection_meta",
    "refresh_task_projections",
]
