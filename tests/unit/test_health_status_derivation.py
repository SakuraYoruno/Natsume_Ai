from __future__ import annotations

from host_brain.app.health_routes import _apply_host_health_status
from host_brain.core.errors import (
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
)


def _report(*, required: int = 0, optional: int = 0) -> dict:
    return {
        "summary": {
            "overall_status": "UP",
            "required_unavailable": required,
            "optional_unavailable": optional,
        }
    }


def test_required_service_failure_is_not_reported_ok() -> None:
    report = _report(required=1)

    _apply_host_health_status(report)

    assert report["status"] == "FAILED"
    assert report["summary"]["overall_status"] == "DOWN"


def test_optional_service_failure_is_degraded() -> None:
    report = _report(optional=1)

    _apply_host_health_status(report)

    assert report["status"] == "DEGRADED"
    assert report["summary"]["overall_status"] == "DEGRADED"


def test_allowed_cold_start_is_degraded_without_hiding_required_service() -> None:
    report = _report(required=1)
    report["services"] = [
        {
            "service_id": "host.integrations.llm_gateway",
            "required": True,
            "status": "DOWN",
            "reason_code": SERVICE_UNAVAILABLE_COLD_START,
        }
    ]

    _apply_host_health_status(
        report,
        allow_cold_start_degradation=True,
    )

    assert report["status"] == "DEGRADED"
    assert report["summary"]["overall_status"] == "DEGRADED"
    assert report["summary"]["required_unavailable"] == 1
    assert report["services"][0]["status"] == "DOWN"


def test_cold_start_without_explicit_allowance_remains_failed() -> None:
    report = _report(required=1)
    report["services"] = [
        {
            "service_id": "host.integrations.llm_gateway",
            "required": True,
            "status": "DOWN",
            "reason_code": SERVICE_UNAVAILABLE_COLD_START,
        }
    ]

    _apply_host_health_status(report)

    assert report["status"] == "FAILED"
    assert report["summary"]["overall_status"] == "DOWN"


def test_allowed_cold_start_does_not_mask_another_required_failure() -> None:
    report = _report(required=2)
    report["services"] = [
        {
            "service_id": "host.integrations.llm_gateway",
            "required": True,
            "status": "DOWN",
            "reason_code": SERVICE_UNAVAILABLE_COLD_START,
        },
        {
            "service_id": "host.audit.sqlite_ledger",
            "required": True,
            "status": "DOWN",
            "reason_code": SERVICE_UNAVAILABLE_DEPENDENCY,
        },
    ]

    _apply_host_health_status(
        report,
        allow_cold_start_degradation=True,
    )

    assert report["status"] == "FAILED"
    assert report["summary"]["overall_status"] == "DOWN"


def test_required_durability_failure_is_fail_closed() -> None:
    report = _report()
    report["degradation_state"] = {
        "durability_required": True,
        "durability_status": "FAILED",
        "last_durable_error": "RUNTIME_EVENT_SCHEMA_NOT_READY",
    }

    _apply_host_health_status(report)

    assert report["status"] == "FAILED"
    assert report["summary"]["overall_status"] == "DOWN"
    assert report["reason_code"] == "RUNTIME_EVENT_SCHEMA_NOT_READY"


def test_optional_durability_unavailable_is_degraded() -> None:
    report = _report()
    report["degradation_state"] = {
        "durability_required": True,
        "durability_status": "UNAVAILABLE",
    }

    _apply_host_health_status(report)

    assert report["status"] == "DEGRADED"
    assert report["summary"]["overall_status"] == "DEGRADED"
