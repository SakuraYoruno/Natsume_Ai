"""Service registry and health reporting contract for cold-start/degrade flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from edge_client.rpc import edge_required_services
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    SERVICE_UNAVAILABLE_EDGE_RPC,
    SERVICE_UNAVAILABLE_GENERIC,
)
from host_brain.core.ids import now_ms


OWNER_HOST = "host"
OWNER_EDGE = "edge"
ALLOWED_OWNERS = {OWNER_HOST, OWNER_EDGE}

STATUS_UP = "UP"
STATUS_DOWN = "DOWN"
STATUS_DEGRADED = "DEGRADED"

HEALTH_UP = "UP"
HEALTH_DEGRADED = "DEGRADED"
BOOT_READY = "READY"
BOOT_DEGRADED = "DEGRADED"

S_SERVICE_HEALTH = "S_SERVICE_HEALTH"
S_SERVICE_BOOTSTRAP = "S_SERVICE_BOOTSTRAP"

KIND_COLD_START = "cold_start"
KIND_DEPENDENCY = "dependency"
KIND_EDGE_RPC = "edge_rpc"

REASON_BY_KIND = {
    KIND_COLD_START: SERVICE_UNAVAILABLE_COLD_START,
    KIND_DEPENDENCY: SERVICE_UNAVAILABLE_DEPENDENCY,
    KIND_EDGE_RPC: SERVICE_UNAVAILABLE_EDGE_RPC,
}


@dataclass(frozen=True)
class ServiceSpec:
    service_id: str
    owner: str
    required: bool = True

    def validate(self) -> None:
        if not self.service_id or not isinstance(self.service_id, str):
            raise ValueError("service_id must be non-empty string")
        if self.owner not in ALLOWED_OWNERS:
            raise ValueError(f"owner must be one of {sorted(ALLOWED_OWNERS)}")
        if not isinstance(self.required, bool):
            raise ValueError("required must be bool")


class ServiceRegistry:
    """Single source of truth for required host/edge service declarations."""

    def __init__(self) -> None:
        self._services: dict[str, ServiceSpec] = {}

    def register(self, spec: ServiceSpec) -> None:
        spec.validate()
        if spec.service_id in self._services:
            raise ValueError(f"service already registered: {spec.service_id}")
        self._services[spec.service_id] = spec

    def all(self) -> list[ServiceSpec]:
        return list(self._services.values())

    def get(self, service_id: str) -> ServiceSpec | None:
        return self._services.get(service_id)


@dataclass(frozen=True)
class StartupIssue:
    scope: str
    stage: str
    reason_code: str
    detail: str
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "field": self.field,
        }


class ServiceHealthReporter:
    """Build deterministic service health report and persist audit rows."""

    def __init__(self, *, registry: ServiceRegistry, ledger: SQLiteLedger | None = None) -> None:
        self.registry = registry
        self.ledger = ledger

    def build_report(
        self,
        *,
        request_id: str,
        runtime_statuses: dict[str, Any] | None = None,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        statuses = runtime_statuses or {}

        services_out: list[dict[str, Any]] = []
        required_total = 0
        required_unavailable = 0
        optional_unavailable = 0

        for spec in self.registry.all():
            required_total += 1 if spec.required else 0
            runtime = _normalize_runtime_status(statuses.get(spec.service_id))
            available = runtime["available"]
            unavailable_kind = runtime["unavailable_kind"]
            detail = runtime["detail"]

            if available:
                status = STATUS_UP
                reason_code = None
                line_detail = detail or "service healthy"
            else:
                reason_code = map_unavailable_reason(unavailable_kind)
                status = STATUS_DOWN if spec.required else STATUS_DEGRADED
                line_detail = detail or "service unavailable"
                if spec.required:
                    required_unavailable += 1
                else:
                    optional_unavailable += 1

            row = {
                "service_id": spec.service_id,
                "owner": spec.owner,
                "required": spec.required,
                "status": status,
                "reason_code": reason_code,
                "stage": S_SERVICE_HEALTH,
                "detail": line_detail,
            }
            services_out.append(row)
            self._log_row(
                request_id=request_id,
                row=row,
                ts_ms=now_value,
            )

        overall = HEALTH_UP
        if required_unavailable > 0 or optional_unavailable > 0:
            overall = HEALTH_DEGRADED

        report_reason = _pick_report_reason(services_out)
        return {
            "request_id": request_id,
            "stage": S_SERVICE_HEALTH,
            "reason_code": report_reason,
            "summary": {
                "overall_status": overall,
                "required_total": required_total,
                "required_unavailable": required_unavailable,
                "optional_unavailable": optional_unavailable,
            },
            "services": services_out,
            "ts_ms": now_value,
        }

    def _log_row(self, *, request_id: str, row: dict[str, Any], ts_ms: int) -> None:
        if self.ledger is None:
            return
        self.ledger.log_service_health(
            {
                "request_id": request_id,
                "service_id": row["service_id"],
                "owner": row["owner"],
                "required": int(bool(row["required"])),
                "status": row["status"],
                "reason_code": row["reason_code"],
                "stage": row["stage"],
                "detail": row["detail"],
                "ts_ms": ts_ms,
            }
        )


class ServiceStartupOrchestrator:
    """Boot-time orchestration that merges config/runtime/probe statuses."""

    def __init__(self, *, registry: ServiceRegistry, ledger: SQLiteLedger | None = None) -> None:
        self.registry = registry
        self.ledger = ledger
        self.health_reporter = ServiceHealthReporter(registry=registry, ledger=ledger)

    def build_startup_report(
        self,
        *,
        request_id: str,
        runtime_statuses: Mapping[str, Any] | None = None,
        dependency_probes: Mapping[str, Any] | None = None,
        startup_issues: Sequence[StartupIssue | Mapping[str, Any]] | None = None,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        probe_statuses, probe_rows = normalize_dependency_probes(dependency_probes)

        merged_statuses: dict[str, Any] = {}
        if runtime_statuses:
            merged_statuses.update(dict(runtime_statuses))
        merged_statuses.update(probe_statuses)

        health_report = self.health_reporter.build_report(
            request_id=request_id,
            runtime_statuses=merged_statuses,
            ts_ms=now_value,
        )
        normalized_issues = normalize_startup_issues(startup_issues)
        boot_status = BOOT_READY
        if normalized_issues or int(health_report["summary"]["required_unavailable"]) > 0:
            boot_status = BOOT_DEGRADED

        return {
            "request_id": request_id,
            "stage": S_SERVICE_BOOTSTRAP,
            "reason_code": pick_startup_reason(
                issues=normalized_issues,
                service_reason=health_report.get("reason_code"),
            ),
            "summary": {
                "boot_status": boot_status,
                "issue_count": len(normalized_issues),
                "probe_total": len(probe_rows),
                "probe_unavailable": sum(1 for row in probe_rows if not row["available"]),
                "required_total": int(health_report["summary"]["required_total"]),
                "required_unavailable": int(health_report["summary"]["required_unavailable"]),
                "optional_unavailable": int(health_report["summary"]["optional_unavailable"]),
            },
            "startup_issues": normalized_issues,
            "dependency_probes": probe_rows,
            "services": health_report["services"],
            "service_health": health_report,
            "ts_ms": now_value,
        }


def build_default_service_registry() -> ServiceRegistry:
    registry = ServiceRegistry()
    host_required = (
        ServiceSpec(service_id="host.audit.sqlite_ledger", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.governor.decision", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.skills.loader", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.integrations.llm_gateway", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.retrieval.embedding", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.retrieval.reranker", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.vision.screen_ocr", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.vision.camera_recognizer", owner=OWNER_HOST, required=True),
    )
    for spec in host_required:
        registry.register(spec)

    for edge_spec in edge_required_services():
        registry.register(
            ServiceSpec(
                service_id=edge_spec.service_id,
                owner=edge_spec.owner,
                required=edge_spec.required,
            )
        )
    return registry


def map_unavailable_reason(unavailable_kind: str | None) -> str:
    key = str(unavailable_kind or "").strip().lower()
    if not key:
        return SERVICE_UNAVAILABLE_GENERIC
    return REASON_BY_KIND.get(key, SERVICE_UNAVAILABLE_GENERIC)


def build_owner_unavailable_statuses(
    *,
    registry: ServiceRegistry,
    owner: str,
    unavailable_kind: str,
    detail: str,
) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for spec in registry.all():
        if spec.owner != owner:
            continue
        statuses[spec.service_id] = {
            "available": False,
            "unavailable_kind": unavailable_kind,
            "detail": str(detail),
        }
    return statuses


def normalize_dependency_probes(
    probes: Mapping[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(probes, Mapping):
        return {}, []

    statuses: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for service_id in sorted(str(key) for key in probes.keys()):
        runtime = _normalize_runtime_status(probes.get(service_id))
        available = bool(runtime["available"])
        unavailable_kind = runtime["unavailable_kind"]
        detail = runtime["detail"] or ("probe healthy" if available else "probe unavailable")
        row = {
            "service_id": service_id,
            "available": available,
            "unavailable_kind": unavailable_kind,
            "reason_code": None if available else map_unavailable_reason(unavailable_kind),
            "detail": detail,
        }
        rows.append(row)
        statuses[service_id] = {
            "available": available,
            "unavailable_kind": unavailable_kind,
            "detail": detail,
        }
    return statuses, rows


def normalize_startup_issues(
    issues: Sequence[StartupIssue | Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not issues:
        return []

    normalized: list[dict[str, Any]] = []
    for raw in issues:
        if isinstance(raw, StartupIssue):
            normalized.append(raw.as_dict())
            continue
        if not isinstance(raw, Mapping):
            continue
        reason_code = str(raw.get("reason_code") or "").strip()
        if not reason_code:
            continue
        normalized.append(
            {
                "scope": str(raw.get("scope") or "startup"),
                "stage": str(raw.get("stage") or S_SERVICE_BOOTSTRAP),
                "reason_code": reason_code,
                "detail": str(raw.get("detail") or "startup issue"),
                "field": (str(raw.get("field")) if raw.get("field") is not None else None),
            }
        )
    return normalized


def pick_startup_reason(*, issues: Sequence[Mapping[str, Any]], service_reason: str | None) -> str | None:
    for issue in issues:
        reason = str(issue.get("reason_code") or "").strip()
        if reason:
            return reason
    return str(service_reason) if service_reason else None


def _normalize_runtime_status(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {
            "available": True,
            "unavailable_kind": None,
            "detail": "",
        }
    if isinstance(raw, bool):
        return {
            "available": bool(raw),
            "unavailable_kind": None if raw else "generic",
            "detail": "",
        }
    if isinstance(raw, dict):
        available = bool(raw.get("available", True))
        kind = raw.get("unavailable_kind") or raw.get("kind")
        return {
            "available": available,
            "unavailable_kind": str(kind).strip().lower() if kind is not None else None,
            "detail": str(raw.get("detail") or "").strip(),
        }
    return {
        "available": True,
        "unavailable_kind": None,
        "detail": "",
    }


def _pick_report_reason(services: list[dict[str, Any]]) -> str | None:
    for row in services:
        if row["required"] and row["reason_code"]:
            return str(row["reason_code"])
    for row in services:
        if row["reason_code"]:
            return str(row["reason_code"])
    return None
