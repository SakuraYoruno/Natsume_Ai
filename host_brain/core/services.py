"""Service registry and health reporting contract for cold-start/degrade flows."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from natsume_contracts.edge.service_catalog_v1 import (
    EdgeServiceDescriptor,
    edge_required_services,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ACCEPTANCE_PENDING_EXTERNAL,
    INFRA_NODE_MISSING_SERVICE,
    INFRA_NODE_PROBE_TIMEOUT,
    INFRA_NODE_ROLE_CONFLICT,
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
STATUS_NOT_TESTED = "NOT_TESTED"

HEALTH_UP = "UP"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_ACCEPTANCE_PENDING_EXTERNAL = ACCEPTANCE_PENDING_EXTERNAL
BOOT_READY = "READY"
BOOT_DEGRADED = "DEGRADED"
BOOT_ACCEPTANCE_PENDING_EXTERNAL = ACCEPTANCE_PENDING_EXTERNAL

S_SERVICE_HEALTH = "S_SERVICE_HEALTH"
S_SERVICE_BOOTSTRAP = "S_SERVICE_BOOTSTRAP"
S_INFRA_NODE_CATALOG = "S_INFRA_NODE_CATALOG"
S_INFRA_NODE_PROBE = "S_INFRA_NODE_PROBE"
S_ENV_READY = "S_ENV_READY"

KIND_COLD_START = "cold_start"
KIND_DEPENDENCY = "dependency"
KIND_EDGE_RPC = "edge_rpc"
KIND_TIMEOUT = "timeout"
KIND_EXTERNAL_PENDING = "external_pending"

REASON_BY_KIND = {
    KIND_COLD_START: SERVICE_UNAVAILABLE_COLD_START,
    KIND_DEPENDENCY: SERVICE_UNAVAILABLE_DEPENDENCY,
    KIND_EDGE_RPC: SERVICE_UNAVAILABLE_EDGE_RPC,
    KIND_EXTERNAL_PENDING: ACCEPTANCE_PENDING_EXTERNAL,
}

ALLOWED_NODE_ROLES = frozenset(
    {
        "orchestrator",
        "audit",
        "llm",
        "retrieval",
        "vision",
        "edge_gateway",
        "edge_voice",
        "edge_realtime",
        "standby",
    }
)


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


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    region: str
    role: str
    services: tuple[str, ...]
    health_endpoint: str
    key_profile_id: str

    def validate(self) -> None:
        if not self.node_id or not isinstance(self.node_id, str):
            raise ValueError("node_id must be non-empty string")
        if not self.region or not isinstance(self.region, str):
            raise ValueError("region must be non-empty string")
        if self.role not in ALLOWED_NODE_ROLES:
            raise ValueError(f"role must be one of {sorted(ALLOWED_NODE_ROLES)}")
        if not self.services or not isinstance(self.services, tuple):
            raise ValueError("services must be non-empty tuple[str]")
        if any(not isinstance(service, str) or not service for service in self.services):
            raise ValueError("services must only contain non-empty string service ids")
        if not self.health_endpoint or not isinstance(self.health_endpoint, str):
            raise ValueError("health_endpoint must be non-empty string")
        if not self.key_profile_id or not isinstance(self.key_profile_id, str):
            raise ValueError("key_profile_id must be non-empty string")


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
        required_not_tested = 0
        optional_not_tested = 0

        for spec in self.registry.all():
            required_total += 1 if spec.required else 0
            runtime = _normalize_runtime_status(statuses.get(spec.service_id))
            available = runtime["available"]
            not_tested = bool(runtime["not_tested"])
            unavailable_kind = runtime["unavailable_kind"]
            detail = runtime["detail"]

            if not_tested:
                status = STATUS_NOT_TESTED
                reason_code = (
                    str(runtime.get("reason_code") or "").strip()
                    or ACCEPTANCE_PENDING_EXTERNAL
                )
                line_detail = detail or "external service not tested"
                if spec.required:
                    required_not_tested += 1
                else:
                    optional_not_tested += 1
            elif available:
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
        elif required_not_tested > 0 or optional_not_tested > 0:
            overall = HEALTH_ACCEPTANCE_PENDING_EXTERNAL

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
                "required_not_tested": required_not_tested,
                "optional_not_tested": optional_not_tested,
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
        elif (
            int(health_report["summary"].get("required_not_tested") or 0) > 0
            or int(health_report["summary"].get("optional_not_tested") or 0) > 0
        ):
            boot_status = BOOT_ACCEPTANCE_PENDING_EXTERNAL

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
                "probe_unavailable": sum(
                    1
                    for row in probe_rows
                    if not row["available"] and not bool(row.get("not_tested"))
                ),
                "probe_not_tested": sum(
                    1 for row in probe_rows if bool(row.get("not_tested"))
                ),
                "required_total": int(health_report["summary"]["required_total"]),
                "required_unavailable": int(health_report["summary"]["required_unavailable"]),
                "optional_unavailable": int(health_report["summary"]["optional_unavailable"]),
                "required_not_tested": int(
                    health_report["summary"].get("required_not_tested") or 0
                ),
                "optional_not_tested": int(
                    health_report["summary"].get("optional_not_tested") or 0
                ),
            },
            "startup_issues": normalized_issues,
            "dependency_probes": probe_rows,
            "services": health_report["services"],
            "service_health": health_report,
            "ts_ms": now_value,
        }


def build_default_service_registry(
    *,
    edge_service_catalog: Sequence[EdgeServiceDescriptor] | None = None,
) -> ServiceRegistry:
    """Build the Host registry from Host declarations and a public Edge catalog."""

    registry = ServiceRegistry()
    host_required = (
        ServiceSpec(service_id="host.audit.sqlite_ledger", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.governor.decision", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.integrations.llm_gateway", owner=OWNER_HOST, required=True),
        ServiceSpec(service_id="host.retrieval.recall_fusion", owner=OWNER_HOST, required=True),
    )
    for spec in host_required:
        registry.register(spec)

    edge_specs = (
        tuple(edge_service_catalog)
        if edge_service_catalog is not None
        else edge_required_services()
    )
    for edge_spec in edge_specs:
        registry.register(
            ServiceSpec(
                service_id=edge_spec.service_id,
                owner=edge_spec.owner,
                required=edge_spec.required,
            )
        )
    return registry


def normalize_node_inventory(
    nodes: Sequence[NodeSpec | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in nodes:
        spec = _coerce_node_spec(raw)
        normalized.append(
            {
                "node_id": spec.node_id,
                "region": spec.region,
                "role": spec.role,
                "services": list(spec.services),
                "health_endpoint": spec.health_endpoint,
                "key_profile_id": spec.key_profile_id,
            }
        )
    normalized.sort(key=lambda row: str(row["node_id"]))
    return normalized


def build_node_service_mapping(
    *,
    nodes: Sequence[NodeSpec | Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    normalized = normalize_node_inventory(nodes)
    grouped: dict[str, list[str]] = {}
    for row in normalized:
        node_id = str(row["node_id"])
        for service_id in row["services"]:
            grouped.setdefault(str(service_id), []).append(node_id)

    mapping: dict[str, dict[str, Any]] = {}
    for service_id in sorted(grouped.keys()):
        carriers = sorted(grouped[service_id])
        mapping[service_id] = {
            "primary": carriers[0] if carriers else None,
            "secondary": carriers[1:] if len(carriers) > 1 else [],
        }
    return mapping


def validate_node_inventory(
    *,
    nodes: Sequence[NodeSpec | Mapping[str, Any]],
    required_services: Sequence[str] | None = None,
    expected_node_count: int | None = None,
) -> dict[str, Any]:
    normalized = normalize_node_inventory(nodes)
    issues: list[dict[str, Any]] = []

    node_ids = [str(row["node_id"]) for row in normalized]
    role_values = [str(row["role"]) for row in normalized]
    endpoint_values = [str(row["health_endpoint"]) for row in normalized]

    for node_id, count in Counter(node_ids).items():
        if count <= 1:
            continue
        issues.append(
            _node_issue(
                reason_code=INFRA_NODE_ROLE_CONFLICT,
                failed_artifact=node_id,
                detail=f"duplicate node_id detected: {node_id}",
            )
        )
    for role, count in Counter(role_values).items():
        if count <= 1:
            continue
        issues.append(
            _node_issue(
                reason_code=INFRA_NODE_ROLE_CONFLICT,
                failed_artifact=role,
                detail=f"role conflict: role must be unique, got count={count}",
            )
        )
    for endpoint, count in Counter(endpoint_values).items():
        if count <= 1:
            continue
        issues.append(
            _node_issue(
                reason_code=INFRA_NODE_ROLE_CONFLICT,
                failed_artifact=endpoint,
                detail=f"health endpoint conflict: endpoint used by count={count} nodes",
            )
        )

    if expected_node_count is not None and len(normalized) != int(expected_node_count):
        issues.append(
            _node_issue(
                reason_code=INFRA_NODE_ROLE_CONFLICT,
                failed_artifact="node_count",
                detail=f"expected {int(expected_node_count)} nodes, got {len(normalized)}",
            )
        )

    required = sorted({str(service_id) for service_id in (required_services or [])})
    available_services: set[str] = set()
    for row in normalized:
        for service_id in row["services"]:
            available_services.add(str(service_id))
    for service_id in required:
        if service_id in available_services:
            continue
        issues.append(
            _node_issue(
                reason_code=INFRA_NODE_MISSING_SERVICE,
                failed_artifact=service_id,
                detail="required service not assigned to any node",
            )
        )

    issues.sort(
        key=lambda row: (
            str(row.get("failed_artifact") or ""),
            str(row.get("reason_code") or ""),
            str(row.get("detail") or ""),
        )
    )
    return {
        "stage": S_INFRA_NODE_CATALOG,
        "status": ("FAILED" if issues else "READY"),
        "reason_code": (issues[0]["reason_code"] if issues else None),
        "summary": {
            "node_count": len(normalized),
            "role_count": len(set(role_values)),
            "service_count": len(available_services),
            "issue_count": len(issues),
        },
        "issues": issues,
        "nodes": normalized,
        "service_mapping": build_node_service_mapping(nodes=normalized),
    }


def build_node_probe_report(
    *,
    nodes: Sequence[NodeSpec | Mapping[str, Any]],
    runtime_statuses: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    normalized = normalize_node_inventory(nodes)
    statuses = dict(runtime_statuses or {})
    now_value = int(ts_ms if ts_ms is not None else now_ms())

    rows: list[dict[str, Any]] = []
    for row in normalized:
        node_id = str(row["node_id"])
        if node_id in statuses:
            runtime = _normalize_runtime_status(statuses.get(node_id))
        else:
            runtime = {
                "available": False,
                "not_tested": True,
                "status": STATUS_NOT_TESTED,
                "unavailable_kind": KIND_EXTERNAL_PENDING,
                "reason_code": ACCEPTANCE_PENDING_EXTERNAL,
                "detail": "node endpoint was not probed in this acceptance run",
            }
        available = bool(runtime["available"])
        not_tested = bool(runtime.get("not_tested"))
        if not_tested:
            reason_code = (
                str(runtime.get("reason_code") or "").strip()
                or ACCEPTANCE_PENDING_EXTERNAL
            )
            node_status = STATUS_NOT_TESTED
            detail = str(runtime["detail"] or "node not tested")
        elif available:
            reason_code = None
            node_status = STATUS_UP
            detail = str(runtime["detail"] or "node healthy")
        else:
            unavailable_kind = str(runtime["unavailable_kind"] or "").strip().lower()
            if unavailable_kind == KIND_TIMEOUT:
                reason_code = INFRA_NODE_PROBE_TIMEOUT
            else:
                reason_code = map_unavailable_reason(unavailable_kind)
            node_status = STATUS_DOWN
            detail = str(runtime["detail"] or "node unavailable")

        rows.append(
            {
                "node_id": node_id,
                "region": str(row["region"]),
                "role": str(row["role"]),
                "node_status": node_status,
                "reason_code": reason_code,
                "stage": S_INFRA_NODE_PROBE,
                "detail": detail,
            }
        )

    down_count = sum(1 for row in rows if row["node_status"] == STATUS_DOWN)
    not_tested_count = sum(
        1 for row in rows if row["node_status"] == STATUS_NOT_TESTED
    )
    up_count = sum(1 for row in rows if row["node_status"] == STATUS_UP)
    report_status = HEALTH_UP
    if down_count > 0:
        report_status = HEALTH_DEGRADED
    elif not_tested_count > 0:
        report_status = HEALTH_ACCEPTANCE_PENDING_EXTERNAL
    return {
        "request_id": request_id,
        "stage": S_INFRA_NODE_PROBE,
        "status": report_status,
        "reason_code": _pick_node_report_reason(rows),
        "summary": {
            "node_total": len(rows),
            "node_down": down_count,
            "node_not_tested": not_tested_count,
            "node_up": up_count,
        },
        "nodes": rows,
        "ts_ms": now_value,
    }


def build_node_deployment_report(
    *,
    nodes: Sequence[NodeSpec | Mapping[str, Any]],
    required_services: Sequence[str] | None = None,
    expected_node_count: int | None = None,
    runtime_statuses: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    validation = validate_node_inventory(
        nodes=nodes,
        required_services=required_services,
        expected_node_count=expected_node_count,
    )
    probes = build_node_probe_report(
        nodes=nodes,
        runtime_statuses=runtime_statuses,
        request_id=request_id,
        ts_ms=now_value,
    )

    status = validation["status"]
    if status == "READY" and probes["status"] in {
        HEALTH_DEGRADED,
        HEALTH_ACCEPTANCE_PENDING_EXTERNAL,
    }:
        status = str(probes["status"])

    return {
        "request_id": request_id,
        "stage": S_INFRA_NODE_CATALOG,
        "status": status,
        "reason_code": str(validation.get("reason_code") or probes.get("reason_code") or "") or None,
        "summary": {
            "node_count": int(validation["summary"]["node_count"]),
            "service_count": int(validation["summary"]["service_count"]),
            "issue_count": int(validation["summary"]["issue_count"]),
            "node_down": int(probes["summary"]["node_down"]),
            "node_not_tested": int(
                probes["summary"].get("node_not_tested") or 0
            ),
        },
        "node_catalog": validation["nodes"],
        "service_mapping": validation["service_mapping"],
        "validation": validation,
        "node_probe": probes,
        "ts_ms": now_value,
    }


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
        not_tested = bool(runtime["not_tested"])
        unavailable_kind = runtime["unavailable_kind"]
        detail = runtime["detail"] or ("probe healthy" if available else "probe unavailable")
        reason_code = str(runtime.get("reason_code") or "").strip() or None
        if reason_code is None and not available:
            reason_code = map_unavailable_reason(unavailable_kind)
        row = {
            "service_id": service_id,
            "available": available,
            "status": str(runtime.get("status") or ("UP" if available else "DOWN")),
            "not_tested": not_tested,
            "unavailable_kind": unavailable_kind,
            "reason_code": reason_code,
            "detail": detail,
        }
        rows.append(row)
        statuses[service_id] = {
            "available": available,
            "status": row["status"],
            "not_tested": not_tested,
            "unavailable_kind": unavailable_kind,
            "reason_code": reason_code,
            "detail": detail,
        }
    return statuses, rows


def summarize_dependency_rows(rows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    if not rows:
        return {
            "total": 0,
            "unavailable": 0,
            "not_tested": 0,
            "reason_counts": {},
            "unavailable_ids": [],
            "not_tested_ids": [],
        }

    reason_counts: dict[str, int] = {}
    unavailable_ids: list[str] = []
    not_tested_ids: list[str] = []
    unavailable_count = 0
    not_tested_count = 0
    total = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        total += 1
        available = bool(row.get("available", True))
        not_tested = bool(row.get("not_tested")) or str(
            row.get("status") or ""
        ).strip().upper() == STATUS_NOT_TESTED
        component_id = str(row.get("service_id") or row.get("component_id") or "").strip()
        reason_code = str(row.get("reason_code") or "").strip()
        if available:
            continue
        if not_tested:
            not_tested_count += 1
            if component_id:
                not_tested_ids.append(component_id)
            if reason_code:
                reason_counts[reason_code] = int(reason_counts.get(reason_code, 0)) + 1
            continue
        unavailable_count += 1
        if component_id:
            unavailable_ids.append(component_id)
        if reason_code:
            reason_counts[reason_code] = int(reason_counts.get(reason_code, 0)) + 1

    unavailable_ids = sorted(set(unavailable_ids))
    not_tested_ids = sorted(set(not_tested_ids))
    reason_counts = {key: reason_counts[key] for key in sorted(reason_counts.keys())}
    return {
        "total": total,
        "unavailable": unavailable_count,
        "not_tested": not_tested_count,
        "reason_counts": reason_counts,
        "unavailable_ids": unavailable_ids,
        "not_tested_ids": not_tested_ids,
    }


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
            "status": STATUS_UP,
            "not_tested": False,
            "unavailable_kind": None,
            "reason_code": None,
            "detail": "",
        }
    if isinstance(raw, bool):
        return {
            "available": bool(raw),
            "status": STATUS_UP if raw else STATUS_DOWN,
            "not_tested": False,
            "unavailable_kind": None if raw else "generic",
            "reason_code": None,
            "detail": "",
        }
    if isinstance(raw, dict):
        status = str(raw.get("status") or "").strip().upper()
        not_tested = bool(raw.get("not_tested")) or status in {
            "NOT_TESTED",
            "SKIPPED_EXTERNAL",
        }
        available = False if not_tested else bool(raw.get("available", True))
        kind = raw.get("unavailable_kind") or raw.get("kind")
        return {
            "available": available,
            "status": status or (STATUS_UP if available else STATUS_DOWN),
            "not_tested": not_tested,
            "unavailable_kind": str(kind).strip().lower() if kind is not None else None,
            "reason_code": str(raw.get("reason_code") or "").strip() or None,
            "detail": str(raw.get("detail") or "").strip(),
        }
    return {
        "available": True,
        "status": STATUS_UP,
        "not_tested": False,
        "unavailable_kind": None,
        "reason_code": None,
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


def _pick_node_report_reason(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        if str(row.get("node_status") or "").strip().upper() == STATUS_NOT_TESTED:
            continue
        reason = str(row.get("reason_code") or "").strip()
        if reason:
            return reason
    for row in rows:
        reason = str(row.get("reason_code") or "").strip()
        if reason:
            return reason
    return None


def _coerce_node_spec(raw: NodeSpec | Mapping[str, Any]) -> NodeSpec:
    if isinstance(raw, NodeSpec):
        raw.validate()
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError(f"node spec must be Mapping or NodeSpec, got {type(raw).__name__}")
    services = raw.get("services")
    if not isinstance(services, Sequence) or isinstance(services, (str, bytes)):
        raise ValueError("node services must be a list[str]")
    spec = NodeSpec(
        node_id=str(raw.get("node_id") or ""),
        region=str(raw.get("region") or ""),
        role=str(raw.get("role") or ""),
        services=tuple(sorted({str(service) for service in services if str(service).strip()})),
        health_endpoint=str(raw.get("health_endpoint") or ""),
        key_profile_id=str(raw.get("key_profile_id") or ""),
    )
    spec.validate()
    return spec


def _node_issue(*, reason_code: str, failed_artifact: str, detail: str) -> dict[str, Any]:
    return {
        "check_id": S_INFRA_NODE_CATALOG,
        "reason_code": str(reason_code),
        "failed_artifact": str(failed_artifact),
        "detail": str(detail),
    }
