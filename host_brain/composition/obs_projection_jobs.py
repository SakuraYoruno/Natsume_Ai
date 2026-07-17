"""Application-local projection callbacks for the standalone Obs process.

Every callback in this module closes over its source and destination ports.
It deliberately has no fallback to Host process globals.  Sources which are
owned by the writer-capable Host runtime are represented by durable, typed
``SOURCE_UNAVAILABLE`` documents until a read-only transport is injected.
"""

from __future__ import annotations

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
from host_brain.app.observability_runtime.live_source_projection import (
    GATEWAY_STATS_SOURCE_KEY,
    GATEWAY_STATS_SOURCE_KIND,
    PROACTIVE_AUTONOMY_DEFAULT_KEY,
    PROACTIVE_AUTONOMY_KIND,
    SYS_MODE_HISTORY_KEY,
    SYS_MODE_HISTORY_KIND,
    SYS_MODE_STATUS_KEY,
    SYS_MODE_STATUS_KIND,
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
from host_brain.app.obs_routes_trace import refresh_trace_query_projections
from host_brain.agent_runtime.operator_read_model import OperatorReadModelService
from host_brain.canon.application import CanonApplicationService
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.observability.projection_store import QueryProjectionStore, digest_json
from host_brain.observability.projection_worker import QueryProjectionWorker
from host_brain.persistence.canon_repository import CanonRepository
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.skills.builtin_registry import build_builtin_skill_registry


TYPED_UNAVAILABLE = "TYPED_UNAVAILABLE"
DURABLE_READ = "DURABLE_READ"
STATIC_READ = "STATIC_READ"
DURABLE_READ_PARTIAL = "DURABLE_READ_PARTIAL"

HOST_RUNTIME_SOURCE_NOT_INJECTED = "OBS_HOST_RUNTIME_SOURCE_NOT_INJECTED"


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
    source_ref: str,
    degraded_state: ProjectionDegradedState = ProjectionDegradedState.HEALTHY,
    degraded_detail: str | None = None,
) -> None:
    store.upsert(
        kind,
        key,
        dict(payload),
        source_cursor=_next_cursor(store, kind, key, observed),
        source_digest=digest_json(payload),
        source_ref=source_ref,
        degraded_state=degraded_state,
        degraded_detail=degraded_detail,
    )


def _upsert_unavailable(
    store: QueryProjectionStore,
    *,
    job_name: str,
    target: ProjectionTarget,
    reason_code: str,
    detail: str,
) -> bool:
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
            and str(current_payload.get("source_scope") or "") == "host-runtime"
        ):
            # Polling the same unavailable source is an observation no-op.  A
            # retry after a committed response loss must not create journal
            # churn or pretend that the source advanced.
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
            "source_scope": "host-runtime",
            "timestamp": now,
        },
        observed=now,
        source_ref=f"obs-app://{job_name}/source-unavailable",
        degraded_state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
        degraded_detail=detail,
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

    unavailable: dict[str, tuple[tuple[ProjectionTarget, ...], str]] = {
        "capability-forge-operator": (
            (ProjectionTarget(CAPABILITY_FORGE_OPERATOR_KIND, CAPABILITY_FORGE_OPERATOR_KEY),),
            "Capability Forge is a writer-capable Host runtime service",
        ),
        "digital-life-overview": (
            (ProjectionTarget(DIGITAL_LIFE_OVERVIEW_KIND, window_key(360)),),
            "digital-life overview requires explicit Host aggregate read ports",
        ),
        "insights-summary": (
            (ProjectionTarget(INSIGHTS_SUMMARY_KIND, window_key(60)),),
            "insights summary requires explicit Host aggregate read ports",
        ),
        "life-body": (
            (ProjectionTarget(LIFE_BODY_KIND, window_key(360)),),
            "life-body aggregation requires explicit Host read ports",
        ),
        "operator-live-state": (
            (
                ProjectionTarget(GATEWAY_STATS_SOURCE_KIND, GATEWAY_STATS_SOURCE_KEY),
                ProjectionTarget(SYS_MODE_STATUS_KIND, SYS_MODE_STATUS_KEY),
                ProjectionTarget(SYS_MODE_HISTORY_KIND, SYS_MODE_HISTORY_KEY),
                ProjectionTarget(PROACTIVE_AUTONOMY_KIND, PROACTIVE_AUTONOMY_DEFAULT_KEY),
            ),
            "operator live state requires explicit Host and Edge read ports",
        ),
        "organ-tribe": (
            (ProjectionTarget(ORGAN_TRIBE_KIND, ORGAN_TRIBE_KEY),),
            "Organ-Tribe state is Host process-owned and no read port is injected",
        ),
        "runtime-snapshot": (
            (ProjectionTarget(RUNTIME_SNAPSHOT_KIND, RUNTIME_SNAPSHOT_KEY),),
            "runtime snapshot requires explicit Host lifecycle read ports",
        ),
        "security-immune": (
            (ProjectionTarget(SECURITY_IMMUNE_KIND, SECURITY_IMMUNE_KEY),),
            "Security Immune runtime state is Host process-owned",
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
        "health-passive": ExplicitObsProjectionCallback(
            "health-passive",
            _health_callback(ledger, store),
            DURABLE_READ,
            30_000,
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
        "sessions": ExplicitObsProjectionCallback(
            "sessions",
            _sessions_callback(ledger, store),
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
                "digital-life-overview": 60_000,
                "insights-summary": 30_000,
                "life-body": 30_000,
                "operator-live-state": 10_000,
                "organ-tribe": 10_000,
                "runtime-snapshot": 15_000,
                "security-immune": 30_000,
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
