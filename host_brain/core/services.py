"""Service registry and health reporting contract for cold-start/degrade flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

S_SERVICE_HEALTH = "S_SERVICE_HEALTH"

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
                "stage": S_SERVICE_HEALTH,
                "detail": row["detail"],
                "ts_ms": ts_ms,
            }
        )


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
