"""Service reporter utilities (extracted from services.py)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from host_brain.core.errors import (
    INFRA_NODE_PROBE_TIMEOUT,
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    SERVICE_UNAVAILABLE_EDGE_RPC,
    SERVICE_UNAVAILABLE_GENERIC,
)
from host_brain.core.ids import now_ms

from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from host_brain.core.services import NodeSpec, ServiceRegistry, StartupIssue

HEALTH_DEGRADED = "DEGRADED"
HEALTH_UP = "UP"
KIND_TIMEOUT = "timeout"
KIND_COLD_START = "cold_start"
KIND_DEPENDENCY = "dependency"
KIND_EDGE_RPC = "edge_rpc"

REASON_BY_KIND = {
    KIND_COLD_START: SERVICE_UNAVAILABLE_COLD_START,
    KIND_DEPENDENCY: SERVICE_UNAVAILABLE_DEPENDENCY,
    KIND_EDGE_RPC: SERVICE_UNAVAILABLE_EDGE_RPC,
}

S_INFRA_NODE_CATALOG = "S_INFRA_NODE_CATALOG"
S_INFRA_NODE_PROBE = "S_INFRA_NODE_PROBE"
S_SERVICE_BOOTSTRAP = "S_SERVICE_BOOTSTRAP"
STATUS_DOWN = "DOWN"
STATUS_UP = "UP"


def map_unavailable_reason(unavailable_kind: str | None) -> str:
    key = str(unavailable_kind or "").strip().lower()
    if not key:
        return SERVICE_UNAVAILABLE_GENERIC
    return REASON_BY_KIND.get(key, SERVICE_UNAVAILABLE_GENERIC)


def _normalize_runtime_status(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"available": True, "unavailable_kind": None, "detail": ""}
    if isinstance(raw, bool):
        return {"available": bool(raw), "unavailable_kind": None if raw else "generic", "detail": ""}
    if isinstance(raw, dict):
        available = bool(raw.get("available", True))
        kind = raw.get("unavailable_kind") or raw.get("kind")
        return {
            "available": available,
            "unavailable_kind": str(kind).strip().lower() if kind is not None else None,
            "detail": str(raw.get("detail") or "").strip(),
        }
    return {"available": True, "unavailable_kind": None, "detail": ""}


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
        reason = str(row.get("reason_code") or "").strip()
        if reason:
            return reason
    return None

def build_node_probe_report(
    *,
    nodes: Sequence["NodeSpec | Mapping[str, Any]"],
    runtime_statuses: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    from host_brain.core.services import normalize_node_inventory  # noqa: PLC0415
    normalized = normalize_node_inventory(nodes)
    statuses = dict(runtime_statuses or {})
    now_value = int(ts_ms if ts_ms is not None else now_ms())

    rows: list[dict[str, Any]] = []
    for row in normalized:
        node_id = str(row["node_id"])
        runtime = _normalize_runtime_status(statuses.get(node_id))
        available = bool(runtime["available"])
        if available:
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
    return {
        "request_id": request_id,
        "stage": S_INFRA_NODE_PROBE,
        "status": (HEALTH_DEGRADED if down_count > 0 else HEALTH_UP),
        "reason_code": _pick_node_report_reason(rows),
        "summary": {
            "node_total": len(rows),
            "node_down": down_count,
            "node_up": len(rows) - down_count,
        },
        "nodes": rows,
        "ts_ms": now_value,
    }


def build_node_deployment_report(
    *,
    nodes: Sequence["NodeSpec | Mapping[str, Any]"],
    required_services: Sequence[str] | None = None,
    expected_node_count: int | None = None,
    runtime_statuses: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    from host_brain.core.services import validate_node_inventory  # noqa: PLC0415
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
    if status == "READY" and probes["status"] == HEALTH_DEGRADED:
        status = HEALTH_DEGRADED

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
    registry: "ServiceRegistry",
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


def summarize_dependency_rows(rows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    if not rows:
        return {
            "total": 0,
            "unavailable": 0,
            "reason_counts": {},
            "unavailable_ids": [],
        }

    reason_counts: dict[str, int] = {}
    unavailable_ids: list[str] = []
    unavailable_count = 0
    total = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        total += 1
        available = bool(row.get("available", True))
        component_id = str(row.get("service_id") or row.get("component_id") or "").strip()
        reason_code = str(row.get("reason_code") or "").strip()
        if available:
            continue
        unavailable_count += 1
        if component_id:
            unavailable_ids.append(component_id)
        if reason_code:
            reason_counts[reason_code] = int(reason_counts.get(reason_code, 0)) + 1

    unavailable_ids = sorted(set(unavailable_ids))
    reason_counts = {key: reason_counts[key] for key in sorted(reason_counts.keys())}
    return {
        "total": total,
        "unavailable": unavailable_count,
        "reason_counts": reason_counts,
        "unavailable_ids": unavailable_ids,
    }


def normalize_startup_issues(
    issues: "Sequence[StartupIssue | Mapping[str, Any]] | None",
) -> list[dict[str, Any]]:
    from host_brain.core.services import StartupIssue  # noqa: PLC0415
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


def _pick_node_report_reason(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        reason = str(row.get("reason_code") or "").strip()
        if reason:
            return reason
    return None


def _coerce_node_spec(raw: "NodeSpec | Mapping[str, Any]") -> "NodeSpec":
    from host_brain.core.services import NodeSpec  # noqa: PLC0415
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
