"""Read-only Task Runtime projections for the Monitor operator console."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, HTTPException, Query, Request

from host_brain.agent_runtime.operator_read_model import OperatorReadModelService
from host_brain.persistence.review_repository import ReviewStore, ReviewStoreError
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    TaskStoreError,
)
from host_brain.app.obs_routes_core import router
from host_brain.app.settings.core import get_core_settings
from host_brain.integrations.runtime_events import RuntimeEventError, RuntimeEventStore
from host_brain.app.observability_runtime.projection_runtime import (
    ProjectionOperatorReadModelService,
    get_query_projection_worker,
    refresh_task_projections,
)
from host_brain.app.observability_runtime.projection_daemon import register_projection_job
from host_brain.observability.projection_store import QueryProjectionError
from host_brain.observability.operator_query_gateway import (
    get_operator_query_context,
)
from host_brain.task_runtime.terminal_state_reconciliation import (
    TerminalStateReconciliationError,
    TerminalStateReconciliationService,
)


_service_provider: Callable[[], OperatorReadModelService] | None = None
_projection_service: ProjectionOperatorReadModelService | None = None

_EFFECT_CASE_FIELDS = (
    "schema_version",
    "case_id",
    "intent_ref",
    "effect_id",
    "graph_id",
    "graph_revision",
    "node_id",
    "attempt_id",
    "purpose",
    "classification",
    "case_kind",
    "reason_code",
    "legacy_decision_ref",
    "legacy_decision_payload_sha256",
    "snapshot_sha256",
)
_EFFECT_CASE_LIST_FIELDS = (
    "case_id",
    "intent_ref",
    "effect_id",
    "purpose",
    "classification",
    "case_kind",
    "reason_code",
    "snapshot_sha256",
)
_EFFECT_INTENT_PUBLIC_FIELDS = (
    "payload_sha256",
    "status",
    "status_version",
    "graph_id",
    "graph_revision",
)
_EFFECT_INTENT_FIELDS = (
    "intent_ref",
    "request_id",
    "payload_sha256",
    "status",
    "status_version",
    "graph_id",
    "graph_revision",
)
_EFFECT_FIELDS = (
    "effect_id",
    "graph_id",
    "graph_revision",
    "node_id",
    "attempt_id",
    "commit_scope",
    "effect_type",
    "payload_ref",
    "payload_sha256",
    "idempotency_key",
    "status",
    "status_version",
    "attempt_count",
    "fencing_token",
    "available_ts_ms",
    "committed_ts_ms",
    "created_ts_ms",
    "failed_ts_ms",
    "lease_owner",
    "lease_expires_ts_ms",
    "receipt_ref",
    "reason_code",
    "last_error_sha256",
    "updated_ts_ms",
)
_EFFECT_PUBLIC_FIELDS = (
    "effect_id",
    "graph_id",
    "graph_revision",
    "node_id",
    "attempt_id",
    "commit_scope",
    "effect_type",
    "payload_sha256",
    "status",
    "status_version",
    "attempt_count",
    "available_ts_ms",
    "committed_ts_ms",
    "created_ts_ms",
    "failed_ts_ms",
    "lease_expires_ts_ms",
    "reason_code",
    "last_error_sha256",
    "updated_ts_ms",
)
_EFFECT_FENCE_FIELDS = (
    "effect_id",
    "intent_ref",
    "observed_status",
    "observed_status_version",
    "resolution_status",
)
_EFFECT_ATTEMPT_FIELDS = (
    "attempt_id",
    "graph_id",
    "graph_revision",
    "node_id",
    "status",
    "status_version",
)
_EFFECT_PROOF_PUBLIC_FIELDS = (
    "schema_version",
    "proof_id",
    "case_id",
    "outcome",
    "target_resolution_status",
    "case_snapshot_sha256",
    "subject_snapshot_sha256",
    "proof_digest",
    "issued_ts_ms",
)
_TERMINAL_CASE_LIST_FIELDS = (
    "case_id",
    "case_kind",
    "classification",
    "reason_code",
    "graph_id",
    "graph_revision",
    "graph_status",
    "budget_status",
    "node_status",
    "attempt_status",
    "recommended_action",
    "snapshot_sha256",
)
_TERMINAL_PROOF_PUBLIC_FIELDS = (
    "proof_id",
    "case_id",
    "case_snapshot_sha256",
    "authority_kind",
    "action",
    "target_graph_status",
    "target_budget_status",
    "subject_snapshot_sha256",
    "proof_digest",
    "issued_ts_ms",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_db_path() -> Path:
    from host_brain.app.settings.persistence import get_persistence_settings

    return Path(get_persistence_settings().canonical_db_path).expanduser()


def set_operator_read_model_service_provider(
    provider: Callable[[], OperatorReadModelService] | None,
) -> None:
    """Install a test/composition provider without changing the read contract."""

    global _projection_service, _service_provider
    _service_provider = provider
    _projection_service = None


def _get_operator_read_model_service() -> OperatorReadModelService:
    global _projection_service
    _require_operator_views_enabled()
    if _service_provider is not None:
        return _service_provider()
    if _projection_service is None:
        from host_brain.composition.process_services import get_query_projection_store

        _projection_service = ProjectionOperatorReadModelService(
            store=get_query_projection_store(),
        )
    return _projection_service  # type: ignore[return-value]


def _get_effect_reconciliation_store(request: Request) -> Any:
    """Resolve the canonical read authority from this FastAPI app instance."""

    _require_operator_views_enabled()
    store = getattr(
        request.app.state,
        "task_effect_reconciliation_read_authority",
        None,
    )
    if store is not None:
        return _validated_effect_reconciliation_reader(store)
    provider = getattr(request.app.state, "task_runtime_container_provider", None)
    container = provider() if callable(provider) else None
    if container is None:
        container = getattr(request.app.state, "task_runtime_container", None)
    store = getattr(container, "task_store", None)

    return _validated_effect_reconciliation_reader(store)


def _validated_effect_reconciliation_reader(store: Any) -> Any:
    """Accept only the narrow read contract required by these routes."""

    required = (
        "list_effect_reconciliation_candidates",
        "list_effect_reconciliation_cases",
        "read_graph_cancellation_effect_reconciliation",
    )
    if store is None or any(
        not callable(getattr(store, method_name, None)) for method_name in required
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "TASK_EFFECT_RECONCILIATION_READ_UNAVAILABLE",
                "detail": "canonical Task Runtime reconciliation reader is unavailable",
            },
        )
    return store


def _get_terminal_reconciliation_store(request: Request) -> Any:
    """Resolve the query-only 0099 authority from this FastAPI instance."""

    _require_operator_views_enabled()
    store = getattr(
        request.app.state,
        "task_terminal_reconciliation_read_authority",
        None,
    )
    return _validated_terminal_reconciliation_reader(store)


def _validated_terminal_reconciliation_reader(store: Any) -> Any:
    required = (
        "list_terminal_reconciliation_candidates",
        "list_terminal_reconciliation_cases",
        "read_terminal_reconciliation_case",
    )
    if store is None or any(
        not callable(getattr(store, method_name, None)) for method_name in required
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "TASK_TERMINAL_RECONCILIATION_READ_UNAVAILABLE",
                "detail": "canonical terminal reconciliation reader is unavailable",
            },
        )
    return store


def _selected(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else dict(value or {})
    return {field: source.get(field) for field in fields}


def _case_summary(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else dict(value or {})
    selected = _selected(source, _EFFECT_CASE_LIST_FIELDS)
    selected["cancellation_ref"] = selected.pop("intent_ref")
    return selected


def _intent_summary(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else dict(value or {})
    return {
        "cancellation_ref": source.get("intent_ref"),
        "request_ref": source.get("request_id"),
        **_selected(source, _EFFECT_INTENT_PUBLIC_FIELDS),
    }


def _fence_summary(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else dict(value or {})
    return {
        "effect_id": source.get("effect_id"),
        "cancellation_ref": source.get("intent_ref"),
        "observed_status": source.get("observed_status"),
        "observed_status_version": source.get("observed_status_version"),
        "resolution_status": source.get("resolution_status"),
    }


def _settlement_summary(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    source = dict(value) if isinstance(value, dict) else dict(value or {})
    selected = _selected(source, _EFFECT_PROOF_PUBLIC_FIELDS)
    return {
        "record_ref": selected["proof_id"],
        "case_id": selected["case_id"],
        "outcome": selected["outcome"],
        "target_resolution_status": selected["target_resolution_status"],
        "case_snapshot_sha256": selected["case_snapshot_sha256"],
        "subject_snapshot_sha256": selected["subject_snapshot_sha256"],
        "binding_sha256": selected["proof_digest"],
        "issued_ts_ms": selected["issued_ts_ms"],
    }


def _exact_signing_material(source: dict[str, Any]) -> dict[str, str]:
    subject = {
        "case": _selected(source.get("case"), _EFFECT_CASE_FIELDS),
        "intent": _selected(source.get("intent"), _EFFECT_INTENT_FIELDS),
        "effect": _selected(source.get("effect"), _EFFECT_FIELDS),
        "fence": _selected(source.get("fence"), _EFFECT_FENCE_FIELDS),
        "attempt": _selected(source.get("attempt"), _EFFECT_ATTEMPT_FIELDS),
        "subject_snapshot_sha256": _text(source.get("subject_snapshot_sha256")),
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "encoding": "base64url-canonical-json",
        "payload_b64": base64.urlsafe_b64encode(encoded).decode("ascii"),
        "payload_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }


def _effect_reconciliation_challenge(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else dict(value or {})
    return {
        "case": _case_summary(source.get("case")),
        "intent": _intent_summary(source.get("intent")),
        "effect": _selected(source.get("effect"), _EFFECT_PUBLIC_FIELDS),
        "fence": _fence_summary(source.get("fence")),
        "attempt": _selected(source.get("attempt"), _EFFECT_ATTEMPT_FIELDS),
        "subject_snapshot_sha256": _text(source.get("subject_snapshot_sha256")),
        "signing_material": _exact_signing_material(source),
        "settlement": _settlement_summary(source.get("proof")),
    }


def _terminal_case_summary(value: Any) -> dict[str, Any]:
    return _selected(value, _TERMINAL_CASE_LIST_FIELDS)


def _terminal_resolution(case: dict[str, Any]) -> dict[str, Any]:
    action = _text(case.get("recommended_action"))
    graph_status = _text(case.get("graph_status")) or None
    targets = {
        "RELEASE_BUDGET": (graph_status, "RELEASED"),
        "FAIL_GRAPH": ("FAILED", None),
        "FAIL_GRAPH_AND_RELEASE_BUDGET": ("FAILED", "RELEASED"),
        "NONE": (None, None),
    }
    target_graph_status, target_budget_status = targets.get(
        action,
        (None, None),
    )
    return {
        "action": action,
        "target_graph_status": target_graph_status,
        "target_budget_status": target_budget_status,
    }


def _terminal_settlement_summary(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    selected = _selected(value, _TERMINAL_PROOF_PUBLIC_FIELDS)
    return {
        "record_ref": selected["proof_id"],
        "case_id": selected["case_id"],
        "authority_kind": selected["authority_kind"],
        "action": selected["action"],
        "target_graph_status": selected["target_graph_status"],
        "target_budget_status": selected["target_budget_status"],
        "case_snapshot_sha256": selected["case_snapshot_sha256"],
        "subject_snapshot_sha256": selected["subject_snapshot_sha256"],
        "binding_sha256": selected["proof_digest"],
        "issued_ts_ms": selected["issued_ts_ms"],
    }


def _terminal_reconciliation_challenge(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else dict(value or {})
    case = dict(source.get("case") or {})
    subject = dict(source.get("subject") or {})
    return {
        "case": _terminal_case_summary(case),
        "subject_snapshot_sha256": _text(subject.get("snapshot_sha256")),
        "recommended_resolution": _terminal_resolution(case),
        "settlement": _terminal_settlement_summary(source.get("proof")),
    }


def refresh_task_runtime_query_projections() -> dict[str, int]:
    """Background-only Task/Review projection entry."""

    canonical_db_path = _canonical_db_path()
    source = OperatorReadModelService(
        task_store=DurableTaskStore(canonical_db_path),
        review_store=ReviewStore(canonical_db_path),
        event_store=RuntimeEventStore(canonical_db_path),
    )
    return refresh_task_projections(source, get_query_projection_worker())


register_projection_job("task-runtime", refresh_task_runtime_query_projections)


def _require_operator_views_enabled() -> None:
    if not get_core_settings().nle_operator_views_enabled:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "NLE_OPERATOR_VIEWS_DISABLED",
                "detail": "set HOST_NLE_OPERATOR_VIEWS_ENABLED=true to expose operator projections",
            },
        )


def _raise_read_model_error(exc: Exception) -> None:
    reason = _text(getattr(exc, "reason_code", "")) or "OPERATOR_READ_MODEL_UNAVAILABLE"
    detail = _text(getattr(exc, "detail", "")) or str(exc)
    if reason in {"TASK_STORE_NOT_FOUND", "REVIEW_STORE_NOT_FOUND"}:
        status = 404
    elif reason.endswith("SCHEMA_NOT_READY"):
        status = 503
    else:
        status = 409
    raise HTTPException(
        status_code=status,
        detail={"reason_code": reason, "detail": detail},
    ) from exc


@router.get("/task-runtime/executions")
async def list_task_runtime_executions(
    limit: int = Query(100, ge=1, le=500),
    service: OperatorReadModelService = Depends(_get_operator_read_model_service),
) -> dict[str, Any]:
    _require_operator_views_enabled()
    try:
        return service.list_executions(limit=limit)
    except (TaskStoreError, ReviewStoreError, RuntimeEventError, QueryProjectionError) as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/review-locator")
async def get_task_runtime_review_locator(
    review_id: str = Query(..., min_length=1, max_length=512),
    service: OperatorReadModelService = Depends(_get_operator_read_model_service),
) -> dict[str, Any]:
    _require_operator_views_enabled()
    try:
        return service.get_review(review_id)
    except (TaskStoreError, ReviewStoreError, RuntimeEventError, QueryProjectionError) as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/executions/{graph_id}")
async def get_task_runtime_execution(
    graph_id: str,
    revision: int = Query(..., ge=1),
    after_cursor: int = Query(0, ge=0),
    event_limit: int = Query(100, ge=1, le=1000),
    service: OperatorReadModelService = Depends(_get_operator_read_model_service),
) -> dict[str, Any]:
    _require_operator_views_enabled()
    try:
        return service.get_execution(
            graph_id,
            revision,
            after_cursor=after_cursor,
            event_limit=event_limit,
        )
    except (TaskStoreError, ReviewStoreError, RuntimeEventError, QueryProjectionError) as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/effect-reconciliation/candidates")
async def list_task_effect_reconciliation_candidates(
    intent_ref: str | None = Query(None, min_length=1, max_length=512),
    limit: int = Query(100, ge=1, le=500),
    store: Any = Depends(_get_effect_reconciliation_store),
) -> dict[str, Any]:
    """Expose exact, private-body-free signing challenges for REQUESTED intents."""

    _require_operator_views_enabled()
    try:
        candidates = store.list_effect_reconciliation_candidates(
            intent_ref,
            limit=limit,
        )
        return {
            "schema_version": "Natsume.operator.effect_reconciliation_candidates.v1",
            "read_only": True,
            "count": len(candidates),
            "candidates": [
                {
                    **_effect_reconciliation_challenge(item),
                    "settled": bool(item.get("proof_exists", False)),
                }
                for item in candidates
            ],
        }
    except TaskStoreError as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/effect-reconciliation/cases")
async def list_task_effect_reconciliation_cases(
    classification: str | None = Query(None, min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    store: Any = Depends(_get_effect_reconciliation_store),
) -> dict[str, Any]:
    """List immutable legacy repair/quarantine inventory without raw snapshots."""

    _require_operator_views_enabled()
    try:
        cases = store.list_effect_reconciliation_cases(
            classification=classification,
            limit=limit,
        )
        return {
            "schema_version": "Natsume.operator.effect_reconciliation_cases.v1",
            "read_only": True,
            "count": len(cases),
            "cases": [_case_summary(item) for item in cases],
        }
    except TaskStoreError as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/effect-reconciliation/challenge")
async def get_task_effect_reconciliation_challenge(
    intent_ref: str = Query(..., min_length=1, max_length=512),
    effect_id: str = Query(..., min_length=1, max_length=512),
    store: Any = Depends(_get_effect_reconciliation_store),
) -> dict[str, Any]:
    """Read one exact current subject while withholding signatures/raw errors."""

    _require_operator_views_enabled()
    try:
        aggregate = store.read_graph_cancellation_effect_reconciliation(
            intent_ref,
            effect_id,
        )
        return {
            "schema_version": "Natsume.operator.effect_reconciliation_challenge.v1",
            "read_only": True,
            **_effect_reconciliation_challenge(aggregate),
        }
    except TaskStoreError as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/terminal-reconciliation/candidates")
async def list_task_terminal_reconciliation_candidates(
    graph_id: str | None = Query(None, min_length=1, max_length=512),
    graph_revision: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=500),
    store: Any = Depends(_get_terminal_reconciliation_store),
) -> dict[str, Any]:
    """List persisted, unsettled 0099 repair subjects without raw snapshots."""

    _require_operator_views_enabled()
    try:
        candidates = store.list_terminal_reconciliation_candidates(
            graph_id,
            graph_revision,
            limit=limit,
        )
        return {
            "schema_version": (
                "Natsume.operator.terminal_reconciliation_candidates.v1"
            ),
            "read_only": True,
            "count": len(candidates),
            "candidates": [
                {
                    **_terminal_reconciliation_challenge(item),
                    "settled": bool(item.get("proof")),
                }
                for item in candidates
            ],
        }
    except TaskStoreError as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/terminal-reconciliation/cases")
async def list_task_terminal_reconciliation_cases(
    classification: str | None = Query(None, min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    store: Any = Depends(_get_terminal_reconciliation_store),
) -> dict[str, Any]:
    """List immutable 0099 inventory with a bounded public summary."""

    _require_operator_views_enabled()
    try:
        cases = store.list_terminal_reconciliation_cases(
            classification=classification,
            limit=limit,
        )
        return {
            "schema_version": "Natsume.operator.terminal_reconciliation_cases.v1",
            "read_only": True,
            "count": len(cases),
            "cases": [_terminal_case_summary(item) for item in cases],
        }
    except TaskStoreError as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/terminal-reconciliation/challenge")
async def get_task_terminal_reconciliation_challenge(
    request: Request,
    case_id: str = Query(..., min_length=1, max_length=512),
    attestation_ref: str = Query(..., min_length=1, max_length=512),
    attestation_key_id: str = Query(..., min_length=1, max_length=255),
    issued_ts_ms: int = Query(..., ge=0),
    store: Any = Depends(_get_terminal_reconciliation_store),
) -> dict[str, Any]:
    """Return the exact Store-verifiable Ed25519 bytes without writing."""

    _require_operator_views_enabled()
    try:
        context = get_operator_query_context(request)
        actor_id = _text(context.authority.principal_id)
        bound_key_id = _text(context.authority.key_id)
        if bound_key_id and bound_key_id != attestation_key_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason_code": "TASK_TERMINAL_RECONCILIATION_KEY_MISMATCH",
                    "detail": "requested signing key differs from authenticated authority",
                },
            )
        aggregate = store.read_terminal_reconciliation_case(case_id)
        challenge = TerminalStateReconciliationService(
            task_store=store,
            outbox_factory=OutboxMessage,
            clock_ms=lambda: issued_ts_ms,
        ).build_operator_challenge(
            case_id=case_id,
            reconciler_actor_id=actor_id,
            issued_ts_ms=issued_ts_ms,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
        )
        return {
            "schema_version": (
                "Natsume.operator.terminal_reconciliation_challenge.v1"
            ),
            "read_only": True,
            **_terminal_reconciliation_challenge(aggregate),
            "record_ref": challenge["proof_id"],
            "graph_outbox_id": challenge["graph_outbox_id"],
            "budget_outbox_id": challenge["budget_outbox_id"],
            "signing_material": challenge["signing_material"],
            "command_request": {
                "case_id": challenge["case_id"],
                "expected_case_snapshot_sha256": challenge[
                    "case_snapshot_sha256"
                ],
                "expected_subject_snapshot_sha256": challenge[
                    "subject_snapshot_sha256"
                ],
                "action": challenge["action"],
                "target_graph_status": challenge["target_graph_status"],
                "target_budget_status": challenge["target_budget_status"],
                "issued_ts_ms": issued_ts_ms,
                "attestation_ref": attestation_ref,
                "attestation_key_id": attestation_key_id,
            },
        }
    except TerminalStateReconciliationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": exc.reason_code, "detail": exc.detail},
        ) from exc
    except TaskStoreError as exc:
        _raise_read_model_error(exc)


__all__ = [
    "get_task_runtime_review_locator",
    "get_task_runtime_execution",
    "get_task_effect_reconciliation_challenge",
    "get_task_terminal_reconciliation_challenge",
    "list_task_effect_reconciliation_candidates",
    "list_task_effect_reconciliation_cases",
    "list_task_terminal_reconciliation_candidates",
    "list_task_terminal_reconciliation_cases",
    "list_task_runtime_executions",
    "set_operator_read_model_service_provider",
]
