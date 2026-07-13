from __future__ import annotations

from host_brain.app.health_routes import _apply_host_health_status


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
