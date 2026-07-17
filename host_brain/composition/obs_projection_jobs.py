"""Application-local projection callbacks for the standalone Obs process.

Every callback in this module closes over its source and destination ports.
It deliberately has no fallback to Host process globals.  Sources which are
owned by the writer-capable Host runtime are represented by durable, typed
``SOURCE_UNAVAILABLE`` documents until a read-only transport is injected.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.observability_runtime.capability_stats_projection import (
    CAPABILITY_FORGE_OPERATOR_KEY,
    CAPABILITY_FORGE_OPERATOR_KIND,
)
from host_brain.app.observability_runtime.domain_state_projection import (
    SECURITY_IMMUNE_KEY,
    SECURITY_IMMUNE_KIND,
    SKILL_PACK_CATALOG_KEY,
    SKILL_PACK_CATALOG_KIND,
    _domain_pack_catalog,
    load_skill_pack_catalog_projection_source,
)
from host_brain.app.observability_runtime.health_device_projection import (
    DEVICE_STATUS_KIND,
    LLM_NODE_HEALTH_KEY,
    LLM_NODE_HEALTH_KIND,
    ORGAN_TRIBE_KEY,
    ORGAN_TRIBE_KIND,
    SERVICE_HEALTH_KEY,
    SERVICE_HEALTH_KIND,
    load_device_status_projection_sources,
    load_health_projection_sources,
)
from host_brain.app.observability_runtime.operator_live_sources import (
    AppOwnedGatewayHealthReader,
    AppOwnedGatewaySourceReader,
    OperatorLiveSourcePorts,
    OperatorSourceUnavailable,
    build_operator_live_state_callback,
)
from host_brain.app.observability_runtime.process_state_projection import (
    BIO_OPS_KIND,
    BIO_OPS_LATEST_KEY,
    DIGITAL_LIFE_OVERVIEW_KIND,
    INSIGHTS_SUMMARY_KIND,
    LIFE_BODY_KIND,
    RESOURCE_MANAGER_KEY,
    RESOURCE_MANAGER_KIND,
    RUNTIME_SNAPSHOT_KEY,
    RUNTIME_SNAPSHOT_KIND,
    load_bio_ops_projection_sources,
    load_resource_manager_projection_source,
    window_key,
)
from host_brain.app.observability_runtime.projection_runtime import (
    SESSION_INDEX_KEY,
    SESSION_KIND,
    TRIBE_EVENTS_INDEX_KEY,
    TRIBE_EVENTS_KIND,
    refresh_task_projections,
)
from host_brain.app.observability_runtime.projection_ledger_view import (
    ProjectionLedgerView,
)
from host_brain.app.observability_runtime.remaining_query_projection import (
    AGENT_LOOP_INDEX_KEY,
    AGENT_LOOP_KIND,
    CANON_SYNC_KEY,
    CANON_SYNC_KIND,
    CHECKPOINT_INDEX_KEY,
    CHECKPOINT_INDEX_KIND,
    CHECKPOINT_KIND,
    MEMORY_CONTEXT_KIND,
    MEMORY_EVENTS_KIND,
    MEMORY_HEALTH_KIND,
    RESIDENT_CONTEXT_INDEX_KEY,
    RESIDENT_CONTEXT_KIND,
    STARTUP_SNAPSHOT_KEY,
    STARTUP_SNAPSHOT_KIND,
    WORLD_SNAPSHOT_KIND,
    _agent_loop_payload,
    _canon_sync_payload,
    _checkpoint_documents,
    _memory_payloads,
    _resident_context_payload,
)
from host_brain.app.observability_runtime.session_query import SessionQueryService
from host_brain.app.observability_runtime.security_immune_snapshot_reader import (
    DEFAULT_SECURITY_IMMUNE_EVIDENCE_MAX_AGE_MS,
    SecurityImmuneSnapshotReader,
    SecurityImmuneSnapshotSourceError,
)
from host_brain.app.observability_runtime.runtime_snapshot_source import (
    RuntimeSnapshotSourceReader,
    RuntimeSnapshotSourceUnavailable,
)
from host_brain.app.obs_routes_trace import refresh_trace_query_projections
from host_brain.app.obs_routes_summary import (
    DIGITAL_LIFE_OVERVIEW_PROJECTION_WINDOW_MINUTES,
    INSIGHTS_SUMMARY_PROJECTION_WINDOW_MINUTES,
    AggregateProjectionSourceUnavailable,
    _build_digital_life_overview_snapshot_async,
    _build_insights_summary_snapshot_sync,
)
from host_brain.agent_runtime.operator_read_model import OperatorReadModelService
from host_brain.canon.application import CanonApplicationService
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.observability.projection_store import QueryProjectionStore, digest_json
from host_brain.observability.projection_worker import QueryProjectionWorker
from host_brain.persistence.canon_repository import CanonRepository
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.channels.edge_transport import resolve_gateway_base_urls


TYPED_UNAVAILABLE = "TYPED_UNAVAILABLE"
DURABLE_READ = "DURABLE_READ"
STATIC_READ = "STATIC_READ"
DURABLE_READ_PARTIAL = "DURABLE_READ_PARTIAL"

HOST_RUNTIME_SOURCE_NOT_INJECTED = "OBS_HOST_RUNTIME_SOURCE_NOT_INJECTED"
AGGREGATE_PROJECTION_SOURCE_SCOPE = "app-local-aggregate-read-model"


@dataclass(frozen=True, slots=True)
class ExplicitObsProjectionCallback:
    """One callback plus truthful source-availability metadata."""

    name: str
    callback: Callable[[], object]
    source_mode: str
    min_interval_ms: int
    unavailable_reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectionTarget:
    kind: str
    key: str


def _now_ms() -> int:
    return int(time.time() * 1000)


def _positive_environment_ms(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name) or default).strip())
    except (TypeError, ValueError, OverflowError):
        return max(1, int(default))
    return value if value > 0 else max(1, int(default))


def _aggregate_projection_source_scope(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized.startswith("ledger:"):
        return "canonical-ledger-projection"
    if normalized.startswith("query:"):
        return "app-local-query-projection"
    return AGGREGATE_PROJECTION_SOURCE_SCOPE


def _next_cursor(
    store: QueryProjectionStore,
    kind: str,
    key: str,
    observed: int = 0,
) -> int:
    current = store.get(kind, key)
    return max(
        _now_ms(),
        max(0, int(observed or 0)),
        int(current.source_cursor) + 1 if current is not None else 0,
    )


def _upsert(
    store: QueryProjectionStore,
    kind: str,
    key: str,
    payload: Mapping[str, Any],
    *,
    observed: int = 0,
    source_cursor: int | None = None,
    source_ref: str,
    degraded_state: ProjectionDegradedState = ProjectionDegradedState.HEALTHY,
    degraded_detail: str | None = None,
    freshness_ts_ms: int | None = None,
) -> None:
    store.upsert(
        kind,
        key,
        dict(payload),
        source_cursor=(
            _next_cursor(store, kind, key, observed)
            if source_cursor is None
            else max(0, int(source_cursor))
        ),
        source_digest=digest_json(payload),
        source_ref=source_ref,
        degraded_state=degraded_state,
        degraded_detail=degraded_detail,
        freshness_ts_ms=freshness_ts_ms,
    )


def _upsert_unavailable(
    store: QueryProjectionStore,
    *,
    job_name: str,
    target: ProjectionTarget,
    reason_code: str,
    detail: str,
    source_scope: str = "host-runtime",
) -> bool:
    scope = str(source_scope or "").strip()
    if not scope:
        raise ValueError("source_scope is required")
    current = store.get(target.kind, target.key)
    if current is not None:
        state = str(
            getattr(current.degraded_state, "value", current.degraded_state) or ""
        ).upper()
        current_payload = dict(current.payload)
        if (
            state == ProjectionDegradedState.SOURCE_UNAVAILABLE.value
            and current_payload.get("available") is False
            and str(current_payload.get("job_name") or "") == job_name
            and str(current_payload.get("reason_code") or "") == reason_code
            and str(current_payload.get("source_scope") or "") == scope
        ):
            # The source fact did not advance, so cursor/version/journal remain
            # stable.  Refresh only the observation freshness: otherwise an
            # actively observed SOURCE_UNAVAILABLE document eventually drifts
            # into the misleading STALE state at read time.
            store.upsert(
                target.kind,
                target.key,
                current_payload,
                source_cursor=int(current.source_cursor),
                source_ref=f"obs-app://{job_name}/source-unavailable",
                degraded_state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
                degraded_detail=detail,
                freshness_ts_ms=_now_ms(),
            )
            return False
    now = _now_ms()
    _upsert(
        store,
        target.kind,
        target.key,
        {
            "schema_version": "Natsume.operator.projection_source_unavailable.v1",
            "available": False,
            "job_name": job_name,
            "projection_kind": target.kind,
            "projection_key": target.key,
            "reason_code": reason_code,
            "source_scope": scope,
            "timestamp": now,
        },
        observed=now,
        source_ref=f"obs-app://{job_name}/source-unavailable",
        degraded_state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
        degraded_detail=detail,
        freshness_ts_ms=now,
    )
    return True


def _unavailable_callback(
    store: QueryProjectionStore,
    *,
    job_name: str,
    targets: tuple[ProjectionTarget, ...],
    reason_code: str = HOST_RUNTIME_SOURCE_NOT_INJECTED,
    detail: str,
) -> Callable[[], dict[str, int]]:
    def refresh() -> dict[str, int]:
        changed = 0
        for target in targets:
            changed += int(_upsert_unavailable(
                store,
                job_name=job_name,
                target=target,
                reason_code=reason_code,
                detail=detail,
            ))
        return {
            "unavailable_document_count": len(targets),
            "changed_document_count": changed,
        }

    return refresh


def _health_callback(ledger: Any, store: QueryProjectionStore) -> Callable[[], object]:
    def refresh() -> dict[str, int]:
        service_payload, node_payload, observed = load_health_projection_sources(
            ledger,
            include_process_cache=False,
        )
        _upsert(
            store,
            SERVICE_HEALTH_KIND,
            SERVICE_HEALTH_KEY,
            service_payload,
            observed=observed,
            source_ref="obs-app://health-passive/services",
        )
        _upsert(
            store,
            LLM_NODE_HEALTH_KIND,
            LLM_NODE_HEALTH_KEY,
            node_payload,
            observed=observed,
            source_ref="obs-app://health-passive/llm-nodes",
        )
        return {
            "service_count": len(service_payload["services"]),
            "node_count": len(node_payload["nodes"]),
        }

    return refresh


def _device_callback(ledger: Any, store: QueryProjectionStore) -> Callable[[], object]:
    def refresh() -> dict[str, int]:
        sources = load_device_status_projection_sources(
            ledger,
            include_process_cache=False,
        )
        if not sources:
            _upsert_unavailable(
                store,
                job_name="device-status",
                target=ProjectionTarget(DEVICE_STATUS_KIND, "avatar"),
                reason_code="OBS_DEVICE_RECEIPT_NOT_OBSERVED",
                detail="no durable embodiment receipt has been observed",
            )
            return {"device_count": 0}
        for key, (payload, observed) in sources.items():
            _upsert(
                store,
                DEVICE_STATUS_KIND,
                key,
                payload,
                observed=observed,
                source_ref="obs-app://device-status/embodiment-receipt",
            )
        return {"device_count": len(sources)}

    return refresh


def _sessions_callback(ledger: Any, store: QueryProjectionStore) -> Callable[[], object]:
    source = SessionQueryService(ledger, source_only=True)

    def refresh() -> dict[str, int]:
        now = _now_ms()
        snapshot = source._query_source(
            page=1,
            page_size=10_000,
            status=None,
            channel=None,
            keyword=None,
            start_time=0,
            end_time=now,
        )
        rows = [dict(item) for item in list(snapshot.get("items") or [])]
        observed = max((int(item.get("ts_ms") or 0) for item in rows), default=0)
        _upsert(
            store,
            SESSION_KIND,
            SESSION_INDEX_KEY,
            {"items": rows, "timestamp": observed},
            observed=observed,
            source_ref="obs-app://sessions/canonical-ledger",
        )
        return {"session_count": len(rows)}

    return refresh


def _bio_ops_callback(store: QueryProjectionStore, root: Path) -> Callable[[], object]:
    def refresh() -> dict[str, int]:
        documents = load_bio_ops_projection_sources(root)
        if not documents:
            _upsert_unavailable(
                store,
                job_name="bio-ops-metadata",
                target=ProjectionTarget(BIO_OPS_KIND, BIO_OPS_LATEST_KEY),
                reason_code="OBS_BIO_OPS_ARTIFACT_NOT_FOUND",
                detail="no BioOps owner artifact is available",
            )
            return {"document_count": 0}
        for run_id, payload, observed in documents:
            _upsert(
                store,
                BIO_OPS_KIND,
                run_id,
                payload,
                observed=observed,
                source_ref=f"obs-app://bio-ops/{run_id}",
            )
        latest_run_id, latest_payload, latest_observed = documents[-1]
        _upsert(
            store,
            BIO_OPS_KIND,
            BIO_OPS_LATEST_KEY,
            {**latest_payload, "latest_run_id": latest_run_id},
            observed=latest_observed,
            source_ref="obs-app://bio-ops/latest",
        )
        return {"document_count": len(documents) + 1}

    return refresh


def _resource_manager_callback(
    store: QueryProjectionStore,
    status_path: Path,
) -> Callable[[], object]:
    def refresh() -> dict[str, int]:
        payload, observed, source = load_resource_manager_projection_source(
            manager_provider=lambda: None,
            status_path=status_path,
        )
        unavailable = str(payload.get("status") or "").upper() == "UNAVAILABLE"
        _upsert(
            store,
            RESOURCE_MANAGER_KIND,
            RESOURCE_MANAGER_KEY,
            payload,
            observed=observed,
            source_ref=f"obs-app://resource-manager/{source}",
            degraded_state=(
                ProjectionDegradedState.SOURCE_UNAVAILABLE
                if unavailable
                else ProjectionDegradedState.HEALTHY
            ),
            degraded_detail=(
                "resource manager read-only status source is unavailable"
                if unavailable
                else None
            ),
        )
        return {"document_count": 1}

    return refresh


def _skill_catalog_callback(store: QueryProjectionStore) -> Callable[[], object]:
    registry = build_builtin_skill_registry()
    domain_packs = _domain_pack_catalog()

    def refresh() -> dict[str, int]:
        payload, observed = load_skill_pack_catalog_projection_source(
            registry_provider=lambda: registry,
            domain_pack_reader=lambda: list(domain_packs),
        )
        _upsert(
            store,
            SKILL_PACK_CATALOG_KIND,
            SKILL_PACK_CATALOG_KEY,
            payload,
            observed=observed,
            source_ref="obs-app://skill-pack-catalog/static",
        )
        return {"document_count": 1}

    return refresh


def _security_immune_callback(
    store: QueryProjectionStore,
    reader: SecurityImmuneSnapshotReader,
) -> Callable[[], object]:
    target = ProjectionTarget(SECURITY_IMMUNE_KIND, SECURITY_IMMUNE_KEY)

    def refresh() -> dict[str, int]:
        try:
            source = reader.read()
        except SecurityImmuneSnapshotSourceError as exc:
            changed = _upsert_unavailable(
                store,
                job_name="security-immune",
                target=target,
                reason_code=exc.reason_code,
                detail=exc.public_detail,
                source_scope=exc.source_scope,
            )
            return {
                "document_count": 1,
                "unavailable_document_count": 1,
                "changed_document_count": int(changed),
            }
        payload = source.payload
        cursor = max(0, int(source.source_cursor))
        current = store.get(target.kind, target.key)
        if current is not None:
            current_payload = dict(current.payload)
            if current_payload == dict(payload):
                cursor = max(cursor, int(current.source_cursor))
            elif cursor <= int(current.source_cursor):
                # Availability transitions need a new monotonic Store cursor,
                # but never use polling wall time as source evidence.
                cursor = int(current.source_cursor) + 1
        _upsert(
            store,
            SECURITY_IMMUNE_KIND,
            SECURITY_IMMUNE_KEY,
            payload,
            source_cursor=cursor,
            source_ref="obs-app://security-immune/evidence-store",
            freshness_ts_ms=source.freshness_ts_ms,
        )
        return {
            "document_count": 1,
            "unavailable_document_count": 0,
            "changed_document_count": 1,
        }

    return refresh


def _operator_gateway_activity_reader(
    store: QueryProjectionStore,
) -> Callable[[int, int], Mapping[str, Any]]:
    view = ProjectionLedgerView(store)

    def read(start_ms: int, end_ms: int) -> Mapping[str, Any]:
        channel_rows = view.fetch_by_time_range(
            "channel_io_log",
            start_ms=start_ms,
            end_ms=end_ms,
            limit=5_000,
        )
        channel_reason = view.last_degraded_reason
        voice_rows = view.fetch_by_time_range(
            "voice_event_log",
            start_ms=start_ms,
            end_ms=end_ms,
            limit=5_000,
        )
        voice_reason = view.last_degraded_reason
        if channel_reason or voice_reason:
            raise OperatorSourceUnavailable(
                "OBS_GATEWAY_ACTIVITY_PROJECTION_UNAVAILABLE",
                "Canonical gateway activity projection is unavailable",
                source_scope="canonical-ledger-projection",
            )
        return {
            "channel_rows": channel_rows,
            "voice_rows": voice_rows,
        }

    return read


def _operator_runtime_config_reader() -> Mapping[str, Any]:
    try:
        typing_speed = int(
            str(os.environ.get("EDGE_HUMANIZE_TYPING_SPEED_CPS") or "12")
        )
    except ValueError:
        typing_speed = 12
    return {
        "wechat_bridge_backend": str(
            os.environ.get("EDGE_WECHAT_BRIDGE_BACKEND") or "uia"
        ),
        "humanize_enabled": str(
            os.environ.get("EDGE_HUMANIZE_ENABLED") or "false"
        ).lower()
        == "true",
        "humanize_typing_speed_cps": max(1, min(100, typing_speed)),
    }


def _aggregate_summary_callback(
    store: QueryProjectionStore,
    *,
    job_name: str,
    target: ProjectionTarget,
    window_minutes: int,
    async_builder: bool,
) -> Callable[[], object]:
    view = ProjectionLedgerView(store)

    def refresh() -> dict[str, int]:
        try:
            if async_builder:
                payload = asyncio.run(
                    _build_digital_life_overview_snapshot_async(
                        window_minutes,
                        projection_view=view,
                        projection_store=store,
                    )
                )
            else:
                payload = _build_insights_summary_snapshot_sync(
                    window_minutes,
                    projection_view=view,
                    projection_store=store,
                )
        except AggregateProjectionSourceUnavailable as exc:
            changed = _upsert_unavailable(
                store,
                job_name=job_name,
                target=target,
                reason_code=exc.reason_code,
                detail="Required aggregate projection source is unavailable",
                source_scope=_aggregate_projection_source_scope(exc.source),
            )
            return {
                "document_count": 1,
                "unavailable_document_count": 1,
                "changed_document_count": int(changed),
            }
        _upsert(
            store,
            target.kind,
            target.key,
            payload,
            observed=int(payload.get("timestamp") or 0),
            source_ref=f"obs-app://{job_name}/durable-aggregate",
        )
        return {
            "document_count": 1,
            "unavailable_document_count": 0,
            "changed_document_count": 1,
        }

    return refresh


def _runtime_snapshot_callback(
    store: QueryProjectionStore,
    reader: RuntimeSnapshotSourceReader,
) -> Callable[[], object]:
    target = ProjectionTarget(RUNTIME_SNAPSHOT_KIND, RUNTIME_SNAPSHOT_KEY)

    def refresh() -> dict[str, int]:
        try:
            payload, cursor = reader()
        except RuntimeSnapshotSourceUnavailable as exc:
            changed = _upsert_unavailable(
                store,
                job_name="runtime-snapshot",
                target=target,
                reason_code=exc.reason_code,
                detail="Canonical runtime snapshot root is unavailable",
                source_scope=exc.source_scope,
            )
            return {
                "document_count": 1,
                "unavailable_document_count": 1,
                "changed_document_count": int(changed),
            }
        _upsert(
            store,
            target.kind,
            target.key,
            payload,
            observed=cursor,
            source_ref="obs-app://runtime-snapshot/durable-read-models",
            # The root source is available.  Optional slot degradation lives
            # inside the typed runtime payload; marking the whole projection
            # STALE would make the GET adapter discard that usable partial
            # snapshot.
            degraded_state=ProjectionDegradedState.HEALTHY,
            degraded_detail=None,
        )
        return {
            "document_count": 1,
            "unavailable_document_count": 0,
            "partial_document_count": int(bool(payload.get("degraded"))),
            "changed_document_count": 1,
        }

    return refresh


def _remaining_queries_callback(
    ledger: Any,
    store: QueryProjectionStore,
    canon_service: CanonApplicationService,
) -> Callable[[], object]:
    def refresh() -> dict[str, int]:
        agent_payload, agent_cursor = _agent_loop_payload(ledger)
        _upsert(
            store,
            AGENT_LOOP_KIND,
            AGENT_LOOP_INDEX_KEY,
            agent_payload,
            observed=agent_cursor,
            source_ref="obs-app://remaining/agent-loop",
        )
        resident_payload, resident_cursor = _resident_context_payload(ledger)
        _upsert(
            store,
            RESIDENT_CONTEXT_KIND,
            RESIDENT_CONTEXT_INDEX_KEY,
            resident_payload,
            observed=resident_cursor,
            source_ref="obs-app://remaining/resident-context",
        )
        checkpoints, checkpoint_cursor = _checkpoint_documents(ledger)
        _upsert(
            store,
            CHECKPOINT_INDEX_KIND,
            CHECKPOINT_INDEX_KEY,
            {"items": checkpoints, "timestamp": checkpoint_cursor},
            observed=checkpoint_cursor,
            source_ref="obs-app://remaining/checkpoint-index",
        )
        for checkpoint in checkpoints:
            _upsert(
                store,
                CHECKPOINT_KIND,
                str(checkpoint["checkpoint_id"]),
                checkpoint,
                observed=int(checkpoint.get("created_at_ms") or 0),
                source_ref="obs-app://remaining/checkpoint",
            )

        memory_event_count = 0
        try:
            context, health, events, memory_cursor = _memory_payloads(ledger)
            for kind, payload in (
                (MEMORY_CONTEXT_KIND, context),
                (MEMORY_HEALTH_KIND, health),
                (MEMORY_EVENTS_KIND, events),
            ):
                _upsert(
                    store,
                    kind,
                    "default",
                    payload,
                    observed=memory_cursor,
                    source_ref="obs-app://remaining/memory",
                )
            memory_event_count = len(list(events.get("items") or []))
        except Exception:
            for kind in (MEMORY_CONTEXT_KIND, MEMORY_HEALTH_KIND, MEMORY_EVENTS_KIND):
                _upsert_unavailable(
                    store,
                    job_name="remaining-operator-queries",
                    target=ProjectionTarget(kind, "default"),
                    reason_code="OBS_MEMORY_READ_MODEL_UNAVAILABLE",
                    detail="canonical memory read model is unavailable",
                )

        canon_payload, canon_cursor = _canon_sync_payload(canon_service)
        _upsert(
            store,
            CANON_SYNC_KIND,
            CANON_SYNC_KEY,
            canon_payload,
            observed=canon_cursor,
            source_ref="obs-app://remaining/canon-sync",
            degraded_state=(
                ProjectionDegradedState.SOURCE_UNAVAILABLE
                if str(canon_payload.get("reason_code") or "") == "CANON_SYNC_FAILED"
                else ProjectionDegradedState.HEALTHY
            ),
            degraded_detail=(
                "canon sync read source is unavailable"
                if str(canon_payload.get("reason_code") or "") == "CANON_SYNC_FAILED"
                else None
            ),
        )

        # These two views require Host-owned process state.  They are
        # materialized as unavailable instead of silently borrowing Host
        # services or pretending that an empty value is current.
        _upsert_unavailable(
            store,
            job_name="remaining-operator-queries",
            target=ProjectionTarget(WORLD_SNAPSHOT_KIND, "default"),
            reason_code="OBS_WORLD_STATE_READ_PORT_NOT_INJECTED",
            detail="world snapshot requires an explicit Host read port",
        )
        _upsert_unavailable(
            store,
            job_name="remaining-operator-queries",
            target=ProjectionTarget(STARTUP_SNAPSHOT_KIND, STARTUP_SNAPSHOT_KEY),
            reason_code="OBS_HOST_STARTUP_SNAPSHOT_PORT_NOT_INJECTED",
            detail="Host startup snapshot requires an explicit read port",
        )
        return {
            "agent_loop_count": len(agent_payload["loops"]),
            "resident_document_count": len(resident_payload["world_canon_documents"])
            + len(resident_payload["persona_resident_documents"]),
            "checkpoint_count": len(checkpoints),
            "memory_event_count": memory_event_count,
        }

    return refresh


def _task_runtime_callback(
    store: QueryProjectionStore,
    canonical_db_path: Path,
) -> Callable[[], object]:
    source = OperatorReadModelService(
        task_store=DurableTaskStore(canonical_db_path),
        review_store=ReviewStore(canonical_db_path),
        event_store=RuntimeEventStore(canonical_db_path),
    )
    worker = QueryProjectionWorker(store)

    def refresh() -> dict[str, int]:
        return refresh_task_projections(source, worker)

    return refresh


def _trace_callback(ledger: Any, store: QueryProjectionStore) -> Callable[[], object]:
    def host_tribe_source_unavailable() -> list[dict[str, Any]]:
        raise RuntimeError("OBS_HOST_TRIBE_EVENT_PORT_NOT_INJECTED")

    def refresh() -> dict[str, int]:
        result = refresh_trace_query_projections(
            ledger=ledger,
            store=store,
            tribe_event_reader=host_tribe_source_unavailable,
        )
        _upsert_unavailable(
            store,
            job_name="trace",
            target=ProjectionTarget(TRIBE_EVENTS_KIND, TRIBE_EVENTS_INDEX_KEY),
            reason_code="OBS_HOST_TRIBE_EVENT_PORT_NOT_INJECTED",
            detail="Tribe events require an explicit Host read port",
        )
        return result

    return refresh


def build_explicit_obs_projection_callbacks(
    *,
    ledger: Any,
    store: QueryProjectionStore,
    canonical_db_path: str | Path,
    life_snapshot_reader: Callable[[str], Mapping[str, Any]],
) -> tuple[ExplicitObsProjectionCallback, ...]:
    """Compose all seventeen non-core jobs from app-local ports."""

    database = Path(canonical_db_path).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[2]
    host_root = Path(__file__).resolve().parents[1]
    world_root = Path(
        str(os.environ.get("Natsume_WORLD_CANON_ROOT") or host_root / "canon" / "world")
    ).expanduser().resolve()
    canon_service = CanonApplicationService(
        repository=CanonRepository(database),
        world_root=world_root,
    )
    if not callable(life_snapshot_reader):
        raise TypeError("life_snapshot_reader must be callable")
    raw_security_state_dir = Path(
        str(
            os.environ.get("SECURITY_IMMUNE_STATE_DIR")
            or repo_root / "runtime" / "security_immune"
        )
    ).expanduser()
    security_state_dir = (
        raw_security_state_dir
        if raw_security_state_dir.is_absolute()
        else repo_root / raw_security_state_dir
    ).resolve()
    security_evidence_max_age_ms = _positive_environment_ms(
        "OBS_SECURITY_IMMUNE_EVIDENCE_MAX_AGE_MS",
        DEFAULT_SECURITY_IMMUNE_EVIDENCE_MAX_AGE_MS,
    )

    unavailable: dict[str, tuple[tuple[ProjectionTarget, ...], str]] = {
        "capability-forge-operator": (
            (ProjectionTarget(CAPABILITY_FORGE_OPERATOR_KIND, CAPABILITY_FORGE_OPERATOR_KEY),),
            "Capability Forge is a writer-capable Host runtime service",
        ),
        "life-body": (
            (ProjectionTarget(LIFE_BODY_KIND, window_key(360)),),
            "life-body aggregation requires explicit Host read ports",
        ),
        "organ-tribe": (
            (ProjectionTarget(ORGAN_TRIBE_KIND, ORGAN_TRIBE_KEY),),
            "Organ-Tribe state is Host process-owned and no read port is injected",
        ),
    }

    callbacks: dict[str, ExplicitObsProjectionCallback] = {
        "bio-ops-metadata": ExplicitObsProjectionCallback(
            "bio-ops-metadata",
            _bio_ops_callback(store, repo_root / "artifacts" / "observable_effects"),
            STATIC_READ,
            30_000,
        ),
        "device-status": ExplicitObsProjectionCallback(
            "device-status",
            _device_callback(ledger, store),
            DURABLE_READ_PARTIAL,
            5_000,
        ),
        "digital-life-overview": ExplicitObsProjectionCallback(
            "digital-life-overview",
            _aggregate_summary_callback(
                store,
                job_name="digital-life-overview",
                target=ProjectionTarget(
                    DIGITAL_LIFE_OVERVIEW_KIND,
                    window_key(DIGITAL_LIFE_OVERVIEW_PROJECTION_WINDOW_MINUTES),
                ),
                window_minutes=DIGITAL_LIFE_OVERVIEW_PROJECTION_WINDOW_MINUTES,
                async_builder=True,
            ),
            DURABLE_READ_PARTIAL,
            60_000,
        ),
        "health-passive": ExplicitObsProjectionCallback(
            "health-passive",
            _health_callback(ledger, store),
            DURABLE_READ,
            30_000,
        ),
        "insights-summary": ExplicitObsProjectionCallback(
            "insights-summary",
            _aggregate_summary_callback(
                store,
                job_name="insights-summary",
                target=ProjectionTarget(
                    INSIGHTS_SUMMARY_KIND,
                    window_key(INSIGHTS_SUMMARY_PROJECTION_WINDOW_MINUTES),
                ),
                window_minutes=INSIGHTS_SUMMARY_PROJECTION_WINDOW_MINUTES,
                async_builder=False,
            ),
            DURABLE_READ_PARTIAL,
            30_000,
        ),
        "operator-live-state": ExplicitObsProjectionCallback(
            "operator-live-state",
            build_operator_live_state_callback(
                store=store,
                ports=OperatorLiveSourcePorts(
                    gateway_source_reader=AppOwnedGatewaySourceReader(
                        health_reader=AppOwnedGatewayHealthReader(
                            tuple(resolve_gateway_base_urls()),
                        ),
                        activity_reader=_operator_gateway_activity_reader(store),
                        runtime_config_reader=_operator_runtime_config_reader,
                    ),
                    life_snapshot_reader=life_snapshot_reader,
                    # Host-owned mode authority is recovered in the next
                    # authenticated transport slice.  These two targets remain
                    # independently typed unavailable until then.
                    sys_mode_status_reader=None,
                    sys_mode_history_reader=None,
                ),
            ),
            DURABLE_READ_PARTIAL,
            10_000,
        ),
        "remaining-operator-queries": ExplicitObsProjectionCallback(
            "remaining-operator-queries",
            _remaining_queries_callback(ledger, store, canon_service),
            DURABLE_READ_PARTIAL,
            15_000,
        ),
        "resource-manager": ExplicitObsProjectionCallback(
            "resource-manager",
            _resource_manager_callback(store, host_root / "data" / "runtime_resource_manager.json"),
            STATIC_READ,
            30_000,
        ),
        "runtime-snapshot": ExplicitObsProjectionCallback(
            "runtime-snapshot",
            _runtime_snapshot_callback(
                store,
                RuntimeSnapshotSourceReader(
                    store=store,
                    life_snapshot_reader=life_snapshot_reader,
                ),
            ),
            DURABLE_READ_PARTIAL,
            15_000,
        ),
        "sessions": ExplicitObsProjectionCallback(
            "sessions",
            _sessions_callback(ledger, store),
            DURABLE_READ,
            30_000,
        ),
        "security-immune": ExplicitObsProjectionCallback(
            "security-immune",
            _security_immune_callback(
                store,
                SecurityImmuneSnapshotReader(
                    security_state_dir,
                    max_age_ms=security_evidence_max_age_ms,
                ),
            ),
            DURABLE_READ,
            30_000,
        ),
        "skill-pack-catalog": ExplicitObsProjectionCallback(
            "skill-pack-catalog",
            _skill_catalog_callback(store),
            STATIC_READ,
            60_000,
        ),
        "task-runtime": ExplicitObsProjectionCallback(
            "task-runtime",
            _task_runtime_callback(store, database),
            DURABLE_READ,
            15_000,
        ),
        "trace": ExplicitObsProjectionCallback(
            "trace",
            _trace_callback(ledger, store),
            DURABLE_READ_PARTIAL,
            15_000,
        ),
    }
    for name, (targets, detail) in unavailable.items():
        callbacks[name] = ExplicitObsProjectionCallback(
            name,
            _unavailable_callback(
                store,
                job_name=name,
                targets=targets,
                detail=detail,
            ),
            TYPED_UNAVAILABLE,
            {
                "capability-forge-operator": 15_000,
                "life-body": 30_000,
                "organ-tribe": 10_000,
            }[name],
            HOST_RUNTIME_SOURCE_NOT_INJECTED,
        )

    required_order = (
        "bio-ops-metadata",
        "capability-forge-operator",
        "device-status",
        "digital-life-overview",
        "health-passive",
        "insights-summary",
        "life-body",
        "operator-live-state",
        "organ-tribe",
        "remaining-operator-queries",
        "resource-manager",
        "runtime-snapshot",
        "security-immune",
        "sessions",
        "skill-pack-catalog",
        "task-runtime",
        "trace",
    )
    if set(callbacks) != set(required_order):
        raise RuntimeError("OBS_EXPLICIT_PROJECTION_COVERAGE_INVALID")
    return tuple(callbacks[name] for name in required_order)


__all__ = [
    "DURABLE_READ",
    "DURABLE_READ_PARTIAL",
    "ExplicitObsProjectionCallback",
    "HOST_RUNTIME_SOURCE_NOT_INJECTED",
    "ProjectionTarget",
    "STATIC_READ",
    "TYPED_UNAVAILABLE",
    "build_explicit_obs_projection_callbacks",
]
