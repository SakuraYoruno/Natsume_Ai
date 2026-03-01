"""Host app entry helpers for service health reporting."""

from __future__ import annotations

import json
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id
from host_brain.core.services import ServiceHealthReporter, build_default_service_registry


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


if __name__ == "__main__":
    report = build_service_health_report(runtime_statuses={})
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
