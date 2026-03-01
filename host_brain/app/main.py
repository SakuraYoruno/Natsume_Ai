"""Host app entry helpers for service health reporting."""

from __future__ import annotations

import json
from typing import Any

from edge_client.config import build_edge_runtime_statuses, load_edge_config
from host_brain.app.config import build_host_runtime_statuses, load_host_config
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id
from host_brain.core.services import (
    KIND_DEPENDENCY,
    OWNER_EDGE,
    OWNER_HOST,
    ServiceHealthReporter,
    ServiceStartupOrchestrator,
    build_default_service_registry,
    build_owner_unavailable_statuses,
)


def build_service_health_report(
    *,
    runtime_statuses: dict[str, Any] | None = None,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Build deterministic health output using the default service catalog."""

    registry = build_default_service_registry()
    reporter = ServiceHealthReporter(registry=registry, ledger=ledger)
    req_id = request_id or new_request_id()
    return reporter.build_report(
        request_id=req_id,
        runtime_statuses=runtime_statuses,
        ts_ms=ts_ms,
    )


def build_startup_report(
    *,
    host_env: dict[str, Any] | None = None,
    edge_env: dict[str, Any] | None = None,
    host_local_overrides: dict[str, Any] | None = None,
    edge_local_overrides: dict[str, Any] | None = None,
    runtime_statuses: dict[str, Any] | None = None,
    dependency_probes: dict[str, Any] | None = None,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Build startup orchestration report with config checks and dependency probes."""

    registry = build_default_service_registry()
    req_id = request_id or new_request_id()
    statuses: dict[str, Any] = {}
    startup_issues: list[dict[str, Any]] = []

    host_result = load_host_config(env=host_env, local_overrides=host_local_overrides)
    if host_result.success and host_result.config is not None:
        statuses.update(build_host_runtime_statuses(host_result.config))
    else:
        host_detail = _pick_config_issue_detail(host_result.issues, fallback="host config invalid")
        startup_issues.append(
            {
                "scope": "host_config",
                "stage": host_result.stage,
                "reason_code": host_result.reason_code,
                "detail": host_detail,
                "field": host_result.issues[0].field if host_result.issues else None,
            }
        )
        statuses.update(
            build_owner_unavailable_statuses(
                registry=registry,
                owner=OWNER_HOST,
                unavailable_kind=KIND_DEPENDENCY,
                detail=f"host config unavailable: {host_result.reason_code}",
            )
        )

    edge_result = load_edge_config(env=edge_env, local_overrides=edge_local_overrides)
    if edge_result.success and edge_result.config is not None:
        statuses.update(build_edge_runtime_statuses(edge_result.config))
    else:
        edge_detail = _pick_config_issue_detail(edge_result.issues, fallback="edge config invalid")
        startup_issues.append(
            {
                "scope": "edge_config",
                "stage": edge_result.stage,
                "reason_code": edge_result.reason_code,
                "detail": edge_detail,
                "field": edge_result.issues[0].field if edge_result.issues else None,
            }
        )
        statuses.update(
            build_owner_unavailable_statuses(
                registry=registry,
                owner=OWNER_EDGE,
                unavailable_kind=KIND_DEPENDENCY,
                detail=f"edge config unavailable: {edge_result.reason_code}",
            )
        )

    if runtime_statuses:
        statuses.update(runtime_statuses)

    orchestrator = ServiceStartupOrchestrator(registry=registry, ledger=ledger)
    startup_report = orchestrator.build_startup_report(
        request_id=req_id,
        runtime_statuses=statuses,
        dependency_probes=dependency_probes,
        startup_issues=startup_issues,
        ts_ms=ts_ms,
    )
    startup_report["config"] = {
        "host": host_result.as_dict(),
        "edge": edge_result.as_dict(),
    }
    return startup_report


def _pick_config_issue_detail(issues: tuple[Any, ...], *, fallback: str) -> str:
    if not issues:
        return fallback
    first = issues[0]
    detail = str(getattr(first, "detail", "")).strip()
    return detail or fallback


if __name__ == "__main__":
    report = build_startup_report(runtime_statuses={}, dependency_probes={})
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
